from __future__ import annotations

import json
import math
import re
import uuid
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.engine.models import EnginePackage
from lion_rebar.export.bundle import replace_export_bundle
from lion_rebar.export.excel_export import (
    ExcelExportError,
    ExportFrameConfig,
    ExportPosition,
    _assign_beam_label,
    _detect_beam_labels,
    _entity_insert,
    _resolve_active_shape_code,
    _segments_for_link,
    generate_excel_export,
)
from lion_rebar.export.frame_detection import (
    DrawingFrame,
    apply_operator_frame_decisions,
    assign_point_to_frame,
    detect_drawing_frames,
)
from lion_rebar.export.engine_shape_parameters import (
    EngineShapeParameterError,
    parameters_for_shape,
)
from lion_rebar.export.errors import F006AsymmetricHookError
from lion_rebar.export.auto_gate import (
    AUTO_EXPORT_BATCH_STATUSES,
    AUTO_EXPORT_MATCHER_DISPOSITIONS,
    auto_batch_status_passes,
    auto_matcher_disposition_passes,
    is_deterministic_production_source,
    validate_production_segment_evidence,
)
from lion_rebar.project.models import ProjectModel
from lion_rebar.project.data_identity import current_identity
from lion_rebar.project.approval_origin import is_manual_approval
from lion_rebar.utils.files import safe_filename


class BatchAutoOutputError(RuntimeError):
    pass


@dataclass(frozen=True)
class BatchAutoOutputBlocker:
    annotation_handle: str
    annotation_text: str
    reason_codes: tuple[str, ...]
    reason_text: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "annotation_handle": self.annotation_handle,
            "annotation_text": self.annotation_text,
            "reason_codes": list(self.reason_codes),
            "reason_text": self.reason_text,
        }


@dataclass(frozen=True)
class BatchExportContext:
    frames: tuple[DrawingFrame, ...]
    positions: tuple[ExportPosition, ...]
    frame_configs: tuple[ExportFrameConfig, ...]
    blockers: tuple[BatchAutoOutputBlocker, ...]
    actionable_count: int

    @property
    def complete(self) -> bool:
        return self.actionable_count > 0 and not self.blockers


@dataclass(frozen=True)
class BatchAutoOutputResult:
    workbook_path: Path
    traceability_path: Path
    complete: bool
    actionable_count: int
    included_position_count: int
    blocked_position_count: int
    grouped_row_count: int
    total_group_count: int
    blocker_counts: dict[str, int]


_PROXY_ANNOTATION_PREFIX = "PXANN_"
_OWN_PROXY_GEOMETRY_RE = re.compile(r"^PXG_([^_]+)_\d+_\d+$")

#: Точност на производствения размер: два знака в сантиметри, тоест 0,1 mm.
PRODUCTION_SEGMENT_DECIMALS = 2


def production_segment_precision(values: Sequence[float]) -> tuple[float, ...]:
    """Реже шума от обхождането, без да пипа истинските размери.

    ЗАЩО. Собственик, 23.09.2026, при одита на „Тест-003“: „тук трябва да ги
    оправим да са точен размер, както е записано в енджина“.

    В описа влизаха четиринайсет позиции с числа като ``374.999942 + 17``
    (`109N14x395`), ``99.999942 + 13`` (`34N8x115`) и ``440.000022 + 13``
    (`28N12x455`). Това е шум с порядък 0,0006 mm от обхождането на DXF
    полилинията, а не мярка — арматуристът получаваше режещ лист с шест знака.

    0,1 mm е далеч под всякакъв допуск за арматура: там, където проектантът е
    начертал кръгло, числото става кръгло, а където НЕ е — остава.
    ``22N12x390`` с начертани ``374.931063`` си остава 374,93, защото тези
    0,7 mm са в чертежа, не в сметката.

    Реже се тук, защото оттук минават и двата пътя — доказаните сегменти и
    изведените от геометрията — преди ``ExportPosition`` да стигне до
    одобрената връзка, ledger-а и Excel.
    """

    изход: list[float] = []
    for value in values or ():
        try:
            число = float(value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(число):
            continue
        изход.append(round(число, PRODUCTION_SEGMENT_DECIMALS))
    return tuple(изход)


def _own_proxy_geometry_handles(row: Mapping[str, Any]) -> tuple[str, ...]:
    """Геометрията на СОБСТВЕНОТО proxy на надписа, когато тя може да замени
    липсващ кандидат от matcher-а.

    Диалектът arm_koloni/arm_shaibi доказва формата от декларацията на
    проектанта и НЯМА нужда от геометрично съвпадение, затова
    ``candidate_handles`` е празен по замисъл. Това не значи, че геометрия
    липсва: ``PXG_<родител>_<n>_0`` е разбитото proxy на самия надпис
    ``PXANN_<родител>`` -- собствеността стои в самия handle и няма какво да
    се разграничава.

    Тясно по замисъл, за да не разхлабва нищо друго: изисква детерминистичен
    proxy източник И всеки handle да сочи същия родител. Смесен списък не
    доказва собственост и се отхвърля.
    """

    handle = str(row.get("annotation_handle") or "")
    if not handle.startswith(_PROXY_ANNOTATION_PREFIX):
        return ()
    parent = handle[len(_PROXY_ANNOTATION_PREFIX) :]
    if not parent:
        return ()
    if not is_deterministic_production_source(row):
        return ()
    candidates = [
        str(value)
        for value in (row.get("reference_geometry_handles") or [])
        if str(value)
    ]
    if not candidates:
        return ()
    for value in candidates:
        match = _OWN_PROXY_GEOMETRY_RE.match(value)
        if match is None or match.group(1) != parent:
            return ()
    return tuple(candidates)


def build_batch_export_context(
    report_path: Path,
    *,
    entities: Sequence[DxfEntityRecord],
    engine: EnginePackage,
    approved_links: Mapping[str, dict] | None = None,
    frame_decisions: Mapping[str, str] | None = None,
) -> BatchExportContext:
    report_path = Path(report_path)
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BatchAutoOutputError(f"Пакетният JSON отчет не може да бъде прочетен: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("rows"), list):
        raise BatchAutoOutputError("Пакетният JSON отчет няма очакваната структура.")
    if bool(payload.get("cancelled")):
        raise BatchAutoOutputError("Прекратен пакетен анализ не може да създаде изходен Excel.")

    frames = apply_operator_frame_decisions(
        detect_drawing_frames(entities), dict(frame_decisions or {}),
    )
    if not frames:
        raise BatchAutoOutputError("Не са намерени чертожни рамки за автоматичния Excel.")
    # Неопределената рамка е отказ за ПОЗИЦИИТЕ В НЕЯ, не за целия чертеж.
    #
    # Собственик, 20.09.2026, след измерването върху новите чертежи. Дотогава
    # една рамка без категория вдигаше грешка за всичко: „KOFR_B“ има 43 листа,
    # от които 6 с разпозната категория, и въпреки това нула от 2974 позиции
    # минаваха; „S-SR+27,82“ намира вярно ГОРНА и ДОЛНА, но една трета рамка без
    # заглавие обезсилваше и двете.
    #
    # Финалният контрол остава строг: той пази пълната спецификация и продължава
    # да спира при неопределена рамка (``integrity`` -> ``FRAME_CATEGORY_UNRESOLVED``).
    # Тоест разхлабва се автоматичното одобрение, а не производственият изход.

    entity_map = {str(record.handle): record for record in entities}
    frame_map = {frame.index: frame for frame in frames}
    beam_labels = _detect_beam_labels(entities, frames)
    all_shape_map = {shape.code.strip().upper(): shape for shape in engine.shapes}
    active_shape_map = {
        shape.code.strip().upper(): shape
        for shape in engine.shapes
        if shape.active
    }

    positions: list[ExportPosition] = []
    blockers: list[BatchAutoOutputBlocker] = []
    approved_links = approved_links or {}
    actionable_count = 0
    for raw_row in payload["rows"]:
        if not isinstance(raw_row, dict) or str(raw_row.get("annotation_class") or "") != "ACTIONABLE":
            continue
        actionable_count += 1
        row = dict(raw_row)
        reasons: list[tuple[str, str]] = []
        annotation_handle = str(row.get("annotation_handle") or "")
        annotation_text = str(row.get("annotation_text") or "")
        approved_link = dict(approved_links.get(annotation_handle) or {})
        approved_in_project = (
            is_manual_approval(approved_link)
            or str(row.get("batch_status") or "") == "ОДОБРЕНА ВРЪЗКА"
            or str(row.get("matcher_disposition") or "") == "APPROVED_IN_PROJECT"
        )
        annotation_text = str(
            approved_link.get("resolved_annotation_text")
            or approved_link.get("annotation_text")
            or annotation_text
        )
        if approved_in_project and not approved_link:
            reasons.append(
                (
                    "MISSING_APPROVED_LINK",
                    "отчетът сочи проектно одобрение, но одобрената връзка липсва",
                )
            )

        count = _positive_int(
            approved_link.get("production_count")
            or approved_link.get("annotation_count")
            or row.get("annotation_count")
        )
        diameter_mm = _positive_float(
            approved_link.get("production_diameter_mm")
            or approved_link.get("diameter_mm")
            or row.get("diameter_mm")
        )
        production_length_cm = _positive_float(
            approved_link.get("production_length_cm")
            or approved_link.get("declared_length_cm")
            or row.get("declared_length_cm")
        )
        if count is None:
            reasons.append(("INVALID_COUNT", "липсва положителен брой"))
        if diameter_mm is None:
            reasons.append(("INVALID_DIAMETER", "липсва валиден диаметър"))
        block_sourced = "::BJ-FI-L" in str(row.get("annotation_handle") or "")
        deterministic_source = is_deterministic_production_source(row)
        if production_length_cm is None:
            reasons.append(("INVALID_LENGTH", "липсва положителна обща дължина"))
        # Прагът "над 1000 броя" пази от ГРЕШНО ПРОЧЕТЕН брой в свободен
        # текст. Когато броят е сметнат от именувани блок-атрибути
        # (BX x BP x BJ), няма разчитане, което да сгреши -- всяка стойност
        # си има име и произходът е записан в count_multiplier.
        # Изчислен брой се доверява САМО когато няма проблемни множители.
        # Нечетим BX/BP означава, че броят може да е занижен -- точно
        # случаят, който вече веднъж даде 78 пъти по-малки количества.
        multiplier_problems = list(row.get("count_multiplier_problems") or [])
        if multiplier_problems:
            reasons.append((
                "MULTIPLIER_UNREADABLE",
                "нечетим множител в блока: " + ", ".join(multiplier_problems),
            ))
        computed_count = (
            (bool(row.get("count_multiplier_tags")) or block_sourced)
            and not multiplier_problems
        )
        if (
            (bool(row.get("quantity_review_required")) and not computed_count)
            or list(row.get("data_quality_flags") or [])
        ):
            reasons.append(("DATA_QUALITY", "количеството или входните данни изискват проверка"))
        if (
            not approved_in_project
            and not auto_batch_status_passes(row)
        ):
            reasons.append(("BATCH_STATUS", "пакетният статус не е автоматично допустим"))
        # ЕКСПЕРИМЕНТ A: позиция от именувани атрибути на блок + изведени
        # по уравнение производствени размери.
        if (
            not approved_in_project
            and not auto_matcher_disposition_passes(row)
        ):
            reasons.append(("MATCHER_REVIEW", "Matcher-ът изисква ръчен преглед"))
        # Коя геометрия ще бъде изнесена, се решава ТУК, защото следващата
        # проверка зависи от отговора. Празният ``MISSING_GEOMETRY`` се
        # докладва по-долу, където е и останалата геометрична валидация.
        engineer_geometry = tuple(
            str(value)
            for value in (approved_link.get("geometry_handles") or [])
            if str(value)
        )
        # Изричният избор на инженер е над всичко. След него идва собствената
        # proxy геометрия на надписа: когато количествата и сегментите идват
        # от декларацията, геометрията на записа трябва да е СЪЩИЯТ прът, а не
        # каквото matcher-ът е закачил -- иначе в маркирания DXF под позицията
        # застава чужд обект.
        own_proxy_geometry = _own_proxy_geometry_handles(row)
        matcher_geometry = tuple(
            str(value) for value in (row.get("candidate_handles") or []) if str(value)
        )
        geometry_handles = engineer_geometry or own_proxy_geometry or matcher_geometry
        exports_own_proxy_geometry = bool(
            not engineer_geometry and own_proxy_geometry
        )
        # ``global_conflict`` означава, че ЕДИН DXF обект е първи кандидат на
        # няколко надписа. Когато позицията изнася собствената си proxy
        # геометрия, а не спорния обект, конфликтът не казва нищо за това,
        # което записваме.
        if bool(row.get("global_conflict")) and not exports_own_proxy_geometry:
            reasons.append(("OWNERSHIP_CONFLICT", "геометрията има нерешен ownership конфликт"))
        if str(row.get("f006_production_status") or "") == "F006_PRODUCTION_REVIEW":
            reasons.append(
                (
                    "F006_PRODUCTION_REVIEW",
                    "формата е сигурно F006, но производствените размери не са "
                    "еднозначни; production export изисква потвърждение",
                )
            )
        elif bool(row.get("production_export_blocked")):
            block_reason = str(
                row.get("production_export_block_reason")
                or "PRODUCTION_SEGMENT_EVIDENCE_INVALID"
            ).strip()
            reasons.append(
                (
                    block_reason,
                    "производственото доказателство за сегментите е "
                    "невалидно или противоречиво; изисква потвърждение",
                )
            )

        raw_shape_code = str(
            approved_link.get("master_shape_code") or row.get("master_shape_code") or ""
        ).strip().upper()
        shape_code = _resolve_active_shape_code(raw_shape_code, all_shape_map=all_shape_map)
        shape = active_shape_map.get(shape_code)
        if shape is None:
            reasons.append(("MISSING_ACTIVE_MASTER", "липсва активна MASTER форма"))
        elif shape_code not in {f"F{index:03d}" for index in range(1, 11)} and not tuple(shape.segments):
            # Later Engine forms are supported only when the pinned Engine
            # carries a real segment definition.  A bare recognition subtype
            # must remain blocked exactly like the legacy renderer gate.
            reasons.append((
                "MISSING_MASTER_RENDERER",
                "Engine формата няма производствена сегментна/renderer дефиниция",
            ))

        annotation = entity_map.get(annotation_handle)
        point = _entity_insert(annotation) if annotation is not None else None
        frame_index = assign_point_to_frame(point[0], point[1], frames) if point is not None else None
        frame = frame_map.get(int(frame_index)) if frame_index is not None else None
        if frame is None:
            reasons.append(("MISSING_FRAME", "надписът не може да бъде свързан с чертожна рамка"))
        elif frame.is_unknown:
            reasons.append(
                (
                    "UNKNOWN_FRAME",
                    "листът на надписа е с неопределена категория и чака "
                    "операторско решение",
                )
            )
        elif frame.category_code == "BEAMS" and shape_code.startswith("F01"):
            reasons.append(
                (
                    "BEAM_STAIR_SHAPE",
                    f"{shape_code} е стълбищна форма в гредов чертеж и не се приема автоматично",
                )
            )

        if not geometry_handles and not bool(approved_link.get("geometry_optional_by_engineer")):
            reasons.append(("MISSING_GEOMETRY", "липсва еднозначна геометрия"))

        segment_lengths_cm: tuple[float, ...] = ()
        turn_angles_deg: tuple[float, ...] = ()
        # ЕКСПЕРИМЕНТ B: когато производствените размери вече са ИЗВЕДЕНИ по
        # уравнение от котировките, те са доказателството. Engine-ът не ги
        # пре-извежда от геометрията -- това би отхвърлило коректния резултат,
        # защото DXF контурът на стремето е от отделни LINE/ARC обекти.
        raw_derived_segments = row.get("production_segments_cm")
        derived_segments: list[float] = []
        if isinstance(raw_derived_segments, (list, tuple)):
            try:
                parsed_derived_segments = [
                    float(value) for value in raw_derived_segments
                ]
            except (TypeError, ValueError):
                parsed_derived_segments = []
            if parsed_derived_segments and all(
                math.isfinite(value) and value > 0
                for value in parsed_derived_segments
            ):
                derived_segments = parsed_derived_segments
        # Доказаните сегменти важат и за отворените форми (F003/F004/F007),
        # не само за стремената.
        #
        # Формата се подава, защото извън заключените F001..F010 размерите се
        # мерят по СОБСТВЕНАТА дефиниция на формата. Без нея родена форма не
        # може да докаже размерите си и пада на геометрията — което вкарваше
        # измереното ``16,9706`` в описа вместо надписаните ``17``.
        production_segment_evidence_valid = (
            validate_production_segment_evidence(row, shape=shape)
        )
        segments_proven = (
            len(derived_segments) >= 2
            and production_segment_evidence_valid
        )
        if (
            not reasons
            and segments_proven
            and len(derived_segments) >= 2
            and shape is not None
            and production_length_cm is not None
        ):
            candidate_segments = tuple(derived_segments)
            candidate_turns = tuple(
                float(value)
                for value in (row.get("canonical_turn_angles_deg") or
                              [90.0] * max(len(derived_segments) - 1, 1))
            )
            try:
                validated_parameters = parameters_for_shape(
                    shape,
                    candidate_segments,
                    production_length_cm=production_length_cm,
                    turn_angles_deg=candidate_turns,
                )
            except F006AsymmetricHookError as exc:
                reasons.append(("F006_PRODUCTION_REVIEW", str(exc)))
            except (EngineShapeParameterError, TypeError, ValueError) as exc:
                reasons.append(("MASTER_LENGTH_CHECK", str(exc)))
            else:
                segment_lengths_cm = (
                    tuple(validated_parameters.expanded_segments_cm)
                    if shape_code == "F002"
                    else candidate_segments
                )
                turn_angles_deg = candidate_turns
        elif not reasons and shape is not None and production_length_cm is not None:
            link = dict(row)
            link.update(approved_link)
            link["geometry_handles"] = list(geometry_handles)
            try:
                segment_lengths_cm, turn_angles_deg = _segments_for_link(
                    link,
                    shape=shape,
                    entity_map=entity_map,
                    production_length_cm=production_length_cm,
                )
                validated_parameters = parameters_for_shape(
                    shape,
                    segment_lengths_cm,
                    production_length_cm=production_length_cm,
                    turn_angles_deg=turn_angles_deg,
                )
                if shape_code == "F002":
                    segment_lengths_cm = tuple(
                        validated_parameters.expanded_segments_cm
                    )
            except F006AsymmetricHookError as exc:
                # The form may remain STRONG in the report, but automatic
                # production export is correctly blocked until the hook
                # dimensions are resolved.
                reasons.append(("F006_PRODUCTION_REVIEW", str(exc)))
            except (ExcelExportError, EngineShapeParameterError, TypeError, ValueError) as exc:
                reasons.append(("MASTER_LENGTH_CHECK", str(exc)))

        if reasons:
            blockers.append(
                BatchAutoOutputBlocker(
                    annotation_handle=annotation_handle,
                    annotation_text=annotation_text,
                    reason_codes=tuple(code for code, _text in reasons),
                    reason_text="; ".join(text for _code, text in reasons),
                )
            )
            continue

        assert count is not None
        assert diameter_mm is not None
        assert production_length_cm is not None
        assert shape is not None
        assert frame is not None
        beam_name = ""
        beam_label_handle = ""
        beam_assignment_method = ""
        beam_assignment_score: float | None = None
        beam_assignment_margin: float | None = None
        if frame.category_code == "BEAMS" and point is not None:
            assignment = _assign_beam_label(point[0], point[1], frame.index, beam_labels)
            if assignment is not None:
                beam_name = assignment.label.title
                beam_label_handle = assignment.label.handle
                beam_assignment_method = assignment.method
                beam_assignment_score = assignment.score
                beam_assignment_margin = assignment.margin
            else:
                blockers.append(
                    BatchAutoOutputBlocker(
                        annotation_handle=annotation_handle,
                        annotation_text=annotation_text,
                        reason_codes=("MISSING_BEAM",),
                        reason_text="позицията е в гредов чертеж, но гредата не е определена",
                    )
                )
                continue

        positions.append(
            ExportPosition(
                annotation_handle=annotation_handle,
                annotation_text=annotation_text,
                count=count,
                diameter_mm=diameter_mm,
                production_length_cm=production_length_cm,
                shape_code=shape_code,
                shape_name=shape.name,
                shape_revision=int(shape.revision),
                geometry_handles=geometry_handles,
                frame_index=frame.index,
                segment_lengths_cm=production_segment_precision(segment_lengths_cm),
                turn_angles_deg=turn_angles_deg,
                # Заглавието на групата колони ("К23, К24 (35/35) - 2бр.").
                # Без него листът с колоните не се подрежда група по група,
                # както са листовете с гредите и вертикалите -- виж
                # ``excel_export._column_group_sections``.
                column_group_label=str(row.get("column_group_label") or ""),
                approval_status=(
                    str(approved_link.get("approval_status") or "APPROVED_IN_PROJECT")
                    if approved_in_project
                    else "AUTO_APPROVED_BATCH_GATE"
                ),
                production_length_source=(
                    str(
                        approved_link.get("production_length_source")
                        or "APPROVED_PROJECT_PLUS_MASTER_EXACT_CHECK"
                    )
                    if approved_in_project
                    else "ANNOTATION_PLUS_MASTER_EXACT_CHECK"
                ),
                beam_name=beam_name,
                beam_label_handle=beam_label_handle,
                beam_assignment_method=beam_assignment_method,
                beam_assignment_score=beam_assignment_score,
                beam_assignment_margin=beam_assignment_margin,
            )
        )

    if actionable_count <= 0:
        raise BatchAutoOutputError("Пакетният анализ няма реални позиции с пълен брой/Ø/L.")
    if not positions:
        # Грешка „нищо не мина“ без причина е безполезна точно когато е най-
        # нужна: при чертеж от нов офис. Затова причините влизат в текста --
        # най-честите първи, с бройки.
        причини: Counter[str] = Counter()
        for blocker in blockers:
            причини.update(blocker.reason_codes)
        обяснение = (
            "; ".join(f"{код}: {брой}" for код, брой in причини.most_common(6))
            or "без регистрирани причини"
        )
        raise BatchAutoOutputError(
            "Нито една позиция не премина автоматичната производствена проверка. "
            "Excel не е създаден, за да не изглежда празен файл като готова заявка. "
            f"Причини ({len(blockers)} позиции): {обяснение}"
        )

    # Лист за неопределена рамка не се прави: в нея няма нито една автоматично
    # одобрена позиция (блокер ``UNKNOWN_FRAME``), а празен лист „неопределен“
    # изглежда като готова заявка, без да е.
    configs = tuple(
        ExportFrameConfig(
            frame_index=frame.index,
            category_code=frame.category_code,
            sheet_name=frame.suggested_sheet_name,
            title_text=frame.combined_title,
            confirmed=frame.confidence == "HIGH",
        )
        for frame in frames
        if not frame.is_unknown
    )
    return BatchExportContext(
        frames=tuple(frames),
        positions=tuple(positions),
        frame_configs=configs,
        blockers=tuple(blockers),
        actionable_count=actionable_count,
    )


def generate_batch_excel_output(
    project: ProjectModel,
    engine: EnginePackage,
    entities: Sequence[DxfEntityRecord],
    report_path: Path,
    *,
    output_dir: Path | None = None,
    approved_links: Mapping[str, dict] | None = None,
) -> BatchAutoOutputResult:
    from lion_rebar.export.frame_decisions import load_frame_decisions

    context = build_batch_export_context(
        report_path,
        entities=entities,
        engine=engine,
        approved_links=approved_links,
        frame_decisions=load_frame_decisions(
            Path(project.root), current_identity(project),
        ),
    )
    target_dir = Path(output_dir) if output_dir is not None else Path(project.root) / "output"
    target_dir.mkdir(parents=True, exist_ok=True)
    project_token = safe_filename(project.name or "LION_REBAR_PROJECT")
    suffix = "REBAR_SPECIFICATION" if context.complete else "REBAR_SPECIFICATION_AUTO_DRAFT"
    final_workbook = target_dir / f"{project_token}_{suffix}.xlsx"
    final_trace = final_workbook.with_name(final_workbook.stem + "_TRACEABILITY.json")
    temporary_workbook = target_dir / f".{final_workbook.stem}.{uuid.uuid4().hex}.tmp.xlsx"

    excel_result = generate_excel_export(
        project,
        engine,
        context.frames,
        context.positions,
        context.frame_configs,
        temporary_workbook,
    )
    temporary_trace = excel_result.traceability_path
    trace_payload = json.loads(temporary_trace.read_text(encoding="utf-8"))
    blocker_counts = Counter(
        code
        for blocker in context.blockers
        for code in blocker.reason_codes
    )
    trace_payload["automatic_batch_output"] = {
        "schema": "lion-rebar-automatic-batch-output/v1",
        "complete": context.complete,
        "actionable_positions": context.actionable_count,
        "included_positions": len(context.positions),
        "blocked_positions": len(context.blockers),
        "blocker_counts": dict(sorted(blocker_counts.items())),
        "blockers": [blocker.to_dict() for blocker in context.blockers],
        "source_report": Path(report_path).name,
    }
    temporary_trace.write_text(
        json.dumps(trace_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    try:
        replace_export_bundle(
            (
                (temporary_workbook, final_workbook),
                (temporary_trace, final_trace),
            )
        )
    finally:
        temporary_workbook.unlink(missing_ok=True)
        temporary_trace.unlink(missing_ok=True)

    return BatchAutoOutputResult(
        workbook_path=final_workbook,
        traceability_path=final_trace,
        complete=context.complete,
        actionable_count=context.actionable_count,
        included_position_count=len(context.positions),
        blocked_position_count=len(context.blockers),
        grouped_row_count=excel_result.grouped_row_count,
        total_group_count=excel_result.total_group_count,
        blocker_counts=dict(sorted(blocker_counts.items())),
    )


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _positive_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None
