"""Автоматична 'silent wrong' метрика — вградена в анализа, не ръчна.

ЗАЩО СЪЩЕСТВУВА
---------------
Фаза 1, Стъпка 1 (независим одит, 09.09.2026) измери РЪЧНО, чрез еднократен
скрипт, колко от одобрените позиции получават своя брой/диаметър/сегменти в
Excel/DXF export-а от reviewед и заключен Final Ledger запис, вместо от
fallback (ре-парснат суров текст на надписа или жива DXF геометрия в
момента на export-а). Резултатът тогава: 97.7% от бройката идваше от суров
текст, защото AUTO_BATCH_GATE пътят не пазеше ``production_count`` — реален
production риск, открит само защото някой седна и го преброи ръчно.

Тази функция превръща онзи еднократен одит в постоянна, автоматична проверка,
изпълнявана след всеки пакетен анализ (виж ``ui/batch_geometry_worker.py``),
за да не се налага никой да преоткрива същия проблем ръчно отново.

ПРИНЦИП
-------
Чисто observational — извиква същите REAL production функции
(``_proven_auto_f007_canonical_segments``, ``_approved_contextual_segments``,
``_segments_from_nearby_dimension_texts``, ``_segments_from_shape_parameter_texts``)
в същия приоритетен ред като ``_segments_for_link``, вместо да преповтаря
логиката им — за да не може класификацията някога да се разминае с реалния
export път. Никога не хвърля изключение навън: диагностика, не гейт.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lion_rebar.export import excel_export as _excel_export
from lion_rebar.engine.models import EnginePackage

METRICS_FILE_NAME = "silent_wrong_metrics.json"
SCHEMA = "lion-rebar-silent-wrong-metrics/v1"

#: Тиерове, при които export-ната стойност НЕ идва от reviewed Final Ledger
#: запис — виж KNOWN_LIMITATIONS_v0.1.0_BG.md, т. 1.
_RISKY_COUNT_TIERS = frozenset({"parsed_from_raw_text"})
_RISKY_SEGMENT_TIERS = frozenset({"raw_geometry_traversal"})


def _classify_segments(link: dict, shape, entity_map: dict, production_length_cm: float) -> str:
    """Огледало на приоритетния ред в ``excel_export._segments_for_link``.

    Извиква реалните production helper функции за всичко нетривиално, за да
    остане класификацията гарантирано вярна на действителния export път.
    """

    ee = _excel_export
    if str(shape.code).upper() == "F001":
        return "F001_no_breakdown"

    manual = link.get("manual_assignment") if isinstance(link.get("manual_assignment"), dict) else {}
    if manual.get("production_segment_lengths_cm"):
        return "manual_assignment.production_segment_lengths_cm"

    try:
        auto_f007 = ee._proven_auto_f007_canonical_segments(
            link, shape_code=shape.code, production_length_cm=production_length_cm,
        )
    except Exception:  # noqa: BLE001 - diagnostic only, never blocks the caller
        auto_f007 = None
    if auto_f007 is not None:
        return "rc32_f007_patch"

    manual_segments = link.get("production_segment_lengths_cm")
    if isinstance(manual_segments, list) and manual_segments:
        return "link.production_segment_lengths_cm"

    try:
        contextual = ee._approved_contextual_segments(link, shape.code, production_length_cm)
    except Exception:  # noqa: BLE001
        contextual = None
    if contextual is not None:
        return "f004_contextual_regex_text"

    records = [entity_map[h] for h in (link.get("geometry_handles") or []) if h in entity_map]
    summary = None
    if records:
        try:
            summary = ee.summarize_selected_geometry(records)
        except ee.ManualGeometryError:
            summary = None
        except Exception:  # noqa: BLE001
            summary = None

    if summary is not None:
        try:
            dimensioned = ee._segments_from_nearby_dimension_texts(
                link, shape_code=shape.code, summary=summary, entity_map=entity_map,
                production_length_cm=production_length_cm,
            )
        except Exception:  # noqa: BLE001
            dimensioned = None
        if dimensioned is not None:
            return "nearby_dimension_texts"
        try:
            parameter_texts = ee._segments_from_shape_parameter_texts(
                shape_code=shape.code, summary=summary, entity_map=entity_map,
                production_length_cm=production_length_cm,
            )
        except Exception:  # noqa: BLE001
            parameter_texts = None
        if parameter_texts is not None:
            return "shape_parameter_texts"

        canonical = link.get("canonical_segment_lengths_cm")
        if isinstance(canonical, list) and canonical:
            try:
                canonical_values = ee._canonicalize_for_master(
                    shape.code, tuple(float(v) for v in canonical), points=summary.points,
                )
                canonical_turns = tuple(
                    float(v) for v in (link.get("canonical_turn_angles_deg") or [])
                )
                ee.parameters_from_segments(
                    shape.code, canonical_values,
                    production_length_cm=production_length_cm, turn_angles_deg=canonical_turns,
                )
            except ee.MasterDimensionError:
                pass
            except Exception:  # noqa: BLE001
                pass
            else:
                return "candidate_payload.canonical_segment_lengths_cm"

        try:
            raw_values = tuple(float(v) for v in summary.segment_lengths_cm)
            if raw_values:
                canonical_raw = ee._canonicalize_for_master(shape.code, raw_values, points=summary.points)
                if ee.segments_match_total(shape.code, sum(canonical_raw), float(production_length_cm), tolerance=0.11):
                    return "raw_geometry_traversal"
        except Exception:  # noqa: BLE001
            pass

    if manual.get("segment_lengths_cm"):
        try:
            values = tuple(float(v) for v in manual["segment_lengths_cm"])
            canonical_values = ee._canonicalize_for_master(shape.code, values)
            if ee.segments_match_total(shape.code, sum(canonical_values), float(production_length_cm), tolerance=0.11):
                return "manual_assignment.segment_lengths_cm(legacy)"
        except Exception:  # noqa: BLE001
            pass

    try:
        fixed = [s.fixed_length_cm for s in sorted(shape.segments, key=lambda i: i.index)]
        if fixed and all(v is not None for v in fixed):
            return "fixed_shape_segments"
    except Exception:  # noqa: BLE001
        pass

    return "ERROR_no_proof"


def compute_silent_wrong_metrics(
    project_root: Path | str,
    engine: EnginePackage,
) -> dict[str, Any]:
    """Класифицира произхода на count/diameter/segments за всяка одобрена
    позиция в текущия проект. Никога не хвърля изключение — при липсващи
    файлове или каквато и да е грешка връща ``{"available": False, ...}``.
    """

    try:
        return _compute(project_root, engine)
    except Exception as exc:  # noqa: BLE001 - diagnostic only
        return {
            "schema": SCHEMA,
            "available": False,
            "reason": f"{type(exc).__name__}: {exc}",
        }


def _compute(project_root: Path | str, engine: EnginePackage) -> dict[str, Any]:
    """Reads ``approved_geometry_links.json`` directly — NOT the Final
    Control ledger. This function is meant to run right after batch
    analysis + auto-approval (``ui/batch_geometry_worker.py``), long before
    an operator ever runs Final Control, so the ledger CSV does not exist
    yet at that point. The approved-links file already IS the exact set of
    currently-approved positions, manual or automatic."""

    ee = _excel_export
    root = Path(project_root)
    working = root / "working"
    approved_path = working / "approved_geometry_links.json"
    extracted_path = working / "extracted_entities.json"
    if not (approved_path.is_file() and extracted_path.is_file()):
        return {
            "schema": SCHEMA,
            "available": False,
            "reason": "Няма одобрени връзки или извлечени обекти за този проект.",
        }

    all_shape_map = {s.code.strip().upper(): s for s in engine.shapes}
    shape_map = {s.code.strip().upper(): s for s in engine.shapes if s.active}

    extracted_payload = json.loads(extracted_path.read_text(encoding="utf-8-sig"))
    entities = ee._records_from_extracted_payload(extracted_payload)
    entity_map = {r.handle: r for r in entities}

    approved_payload = json.loads(approved_path.read_text(encoding="utf-8-sig"))
    links = [
        dict(link)
        for link in approved_payload.get("links", [])
        if isinstance(link, dict) and str(link.get("annotation_handle") or "")
    ]

    count_tier: dict[str, int] = {}
    diameter_tier: dict[str, int] = {}
    segment_tier: dict[str, int] = {}
    risky_handles: list[str] = []
    total = 0

    for link in links:
        annotation_handle = str(link.get("annotation_handle") or "")
        annotation_text = str(
            link.get("resolved_annotation_text") or link.get("annotation_text") or ""
        )
        parsed = ee.parse_rebar_text(annotation_text)

        is_risky = False

        if ee._positive_int(link.get("production_count")):
            count_key = "link.production_count"
        elif ee._positive_int(link.get("annotation_count")):
            count_key = "link.annotation_count"
        elif parsed.count is not None:
            count_key = "parsed_from_raw_text"
        else:
            count_key = "NONE"
        count_tier[count_key] = count_tier.get(count_key, 0) + 1
        is_risky = is_risky or count_key in _RISKY_COUNT_TIERS

        if ee._positive_float(link.get("production_diameter_mm")):
            diameter_key = "link.production_diameter_mm"
        elif ee._positive_float(link.get("diameter_mm")):
            diameter_key = "link.diameter_mm"
        elif parsed.diameter_mm is not None:
            diameter_key = "parsed_from_raw_text"
        else:
            diameter_key = "NONE"
        diameter_tier[diameter_key] = diameter_tier.get(diameter_key, 0) + 1

        production_length = ee._positive_float(link.get("production_length_cm"))
        if production_length is None:
            production_length = ee._positive_float(link.get("declared_length_cm"))
        shape_code = ee._resolve_active_shape_code(
            ee._resolve_shape_code(link, link), all_shape_map=all_shape_map,
        )
        shape = shape_map.get(shape_code) if shape_code else None
        if shape is None or production_length is None:
            segment_tier["SKIPPED_incomplete_row"] = segment_tier.get("SKIPPED_incomplete_row", 0) + 1
        else:
            try:
                tier = _classify_segments(link, shape, entity_map, production_length)
            except Exception as exc:  # noqa: BLE001
                tier = f"EXCEPTION({type(exc).__name__})"
            segment_tier[tier] = segment_tier.get(tier, 0) + 1
            is_risky = is_risky or tier in _RISKY_SEGMENT_TIERS

        total += 1
        if is_risky:
            risky_handles.append(annotation_handle)

    return {
        "schema": SCHEMA,
        "available": True,
        "total_approved_positions": total,
        "count_source": count_tier,
        "diameter_source": diameter_tier,
        "segment_source": segment_tier,
        # Позиции, чиято export стойност идва от ре-парснат текст или жива
        # геометрия вместо от reviewed Final Ledger запис — виж
        # KNOWN_LIMITATIONS_v0.1.0_BG.md, т. 1.
        "silent_wrong_risk_count": len(risky_handles),
        "silent_wrong_risk_handles": risky_handles,
    }


def write_silent_wrong_metrics(project_root: Path | str, engine: EnginePackage) -> Path:
    """Изчислява и записва метриката в ``working/silent_wrong_metrics.json``.

    Best-effort: грешка тук никога не бива да блокира пакетния анализ.
    """

    metrics = compute_silent_wrong_metrics(project_root, engine)
    path = Path(project_root) / "working" / METRICS_FILE_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    return path


def read_silent_wrong_metrics(project_root: Path | str) -> dict[str, Any] | None:
    """Чете предварително записаната метрика, ако съществува. Никога не хвърля."""

    path = Path(project_root) / "working" / METRICS_FILE_NAME
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
