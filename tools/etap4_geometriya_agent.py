# -*- coding: utf-8 -*-
"""ЕТАП 4 — защо позиция остава БЕЗ ГЕОМЕТРИЯ.

Само чете. Не пипа проекти, не пипа ``lion_rebar/``, не одобрява нищо.

ВЪПРОСЪТ. По-ранен извод: „позициите без активна MASTER форма нямат и
``canonical_signature``, значи геометрия няма". Липсата на подпис доказва само,
че на надписа не е съпоставен кандидат — не че в чертежа няма обект. Този
скрипт разделя причините по ДОКАЗАТЕЛСТВО, а не по догадка:

  IN_BLOCK            обектът е вътре във вложен BLOCK/INSERT
  PROXY               обектът е ACAD_PROXY_ENTITY / ACAD_ZOMBIE_ENTITY
  FILTERED            обектът е извлечен, но отпада от филтър (слой или тип)
  NO_ANNOTATION_LINK  обектът е там, но извън радиуса на търсене на надписа
  SEGMENTS_NOT_JOINED няколко сегмента, които никога не са свързани в един път
  SCHEMATIC_ONLY      само символ (кръг-сечение, щриховка) без производствен размер
  TRULY_ABSENT        в чертежа наистина няма обект около надписа
  UNKNOWN             причината не е установена — казва се, не се гадае

КАК СЕ МЕРИ. Три независими наблюдения за всеки надпис без кандидат:

1. ПОВТАРЯНЕ. Същият код (``find_geometry_candidates``), същата политика от
   отчета на прогона, същият закачен MASTER engine, същият набор обекти (със
   свален NOISE слой, както го прави прозорецът). Очаква се пак нула кандидата —
   иначе измерването не е за този прогон.

2. ОПИТ С ОБЯВЕНИ СЛОЕВЕ. Същият код, но с ``declared_rebar_geometry_layers``
   = всички слоеве от ТОЗИ чертеж, които носят LINE/LWPOLYLINE/POLYLINE/ARC и
   които речникът в ``_rebar_geometry_layer_role`` НЕ познава. Ако така се роди
   кандидат, причината е филтърът по слой, а доказателството е измерената
   дължина срещу обявената и намерената MASTER форма. Това е опит за
   ИЗМЕРВАНЕ, не предложение за поведение по подразбиране.

3. НЕЗАВИСИМО ЧЕТЕНЕ НА DXF (``ezdxf``). Модел + рекурсивно съдържанието на
   всички вмъквания с преобразуваните координати. Така се вижда какво ФИЗИЧЕСКИ
   стои около надписа, включително обекти, които вносът не превръща в
   примитиви (CIRCLE, HATCH, SOLID, SPLINE, ELLIPSE) и proxy обектите.

Стартиране (от ``release``):

    PYTHONIOENCODING=utf-8 LION_REBAR_DATA_ROOT=C:\\lr_exp\\data \\
      .venv/Scripts/python.exe tools/etap4_geometriya_agent.py

    --projects Тест-002,s-7   само тези проекти
    --out <файл>              къде да запише JSON (по подразбиране
                              C:\\lr_exp\\rezultat\\etap_4\\GEOMETRIYA_AGENT.json)
    --evidence-per-cause N    колко примера на причина да влязат в отчета
"""

from __future__ import annotations

import argparse
import collections
import glob
import io
import json
import math
import os
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterable, Sequence

RELEASE_ROOT = Path(__file__).resolve().parents[1]
if str(RELEASE_ROOT) not in sys.path:
    sys.path.insert(0, str(RELEASE_ROOT))

PROEKTI = Path(r"C:\lr_exp\proekti")
RUNS = Path(r"C:\lr_exp\data\full_path_runs")
DEFAULT_OUT = Path(r"C:\lr_exp\rezultat\etap_4\GEOMETRIYA_AGENT.json")
DEFAULT_PROJECTS = ("Тест-002", "s-7", "s-8", "s-9", "Тест-003", "тест-006")

PRIMITIVE_TYPES = {"LINE", "LWPOLYLINE", "POLYLINE", "ARC"}
#: Типове, които носят геометрия, но ``_primitive_from_record`` не приема.
NON_PRIMITIVE_GEOMETRY = {"CIRCLE", "ELLIPSE", "SPLINE", "HATCH", "SOLID", "POINT", "3DFACE"}

STAGE_LAYER_FILTER = (
    "GeometryCandidateContext.build → _rebar_geometry_layer_role "
    "(lion_rebar/dxf/geometry_candidates.py:3732): обектът никога не става "
    "GeometryPrimitive, затова не влиза в пространствения индекс."
)
STAGE_TYPE_FILTER = (
    "_primitive_from_record (lion_rebar/dxf/geometry_candidates.py:3065): "
    "приема само LINE/LWPOLYLINE/POLYLINE/ARC; всичко друго връща None."
)
STAGE_NOISE_FILTER = (
    "layer_classifier.filter_analysis_entities "
    "(lion_rebar/session/layer_classifier.py:120): слоят е класифициран NOISE "
    "с увереност ≥0.95 и е свален ПРЕДИ анализа."
)
STAGE_DISTANCE = (
    "find_geometry_candidates → RecognitionPolicy.search_distance_limit "
    "(lion_rebar/dxf/recognition_policy.py:344) и отказът "
    "„ПРЕКАЛЕНО ДАЛЕЧ ОТ НАДПИСА“ (geometry_candidates.py:744)."
)
STAGE_CHAIN = (
    "LineChainIndex / _connected_paths с endpoint_tolerance_raw "
    "(geometry_candidates.py:3165): краищата не се допират в допуска, затова "
    "сегментите остават отделни кандидати."
)


# --------------------------------------------------------------------------
# четене на входовете
# --------------------------------------------------------------------------
def _read_json(path: Path) -> Any:
    with io.open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _baseline_run_dir(project: str) -> Path | None:
    """Папката на прогона, дал базовата линия.

    Там стоят ТОЧНО тези ``extracted_entities.json``, ``layer_classification``,
    ``master/`` и ``source/original.dxf``, с които е направен отчетът. Копието в
    ``C:\\lr_exp\\proekti`` може да е от друго поколение.
    """

    _, report = _baseline_batch(project)
    candidate = report.parents[3]
    return candidate if (candidate / "working").is_dir() else None


def _project_file(project: str, relative: str) -> Path:
    run = _baseline_run_dir(project)
    if run is not None and (run / relative).exists():
        return run / relative
    return PROEKTI / project / relative


def _records(project: str):
    from lion_rebar.dxf.models import DxfEntityRecord

    payload = _read_json(_project_file(project, "working/extracted_entities.json"))
    records = []
    for row in payload.get("entities", []):
        records.append(
            DxfEntityRecord(
                handle=str(row.get("handle") or ""),
                entity_type=str(row.get("entity_type") or ""),
                layer=str(row.get("layer") or ""),
                geometry=row.get("geometry") or {},
                text=row.get("text"),
                block_name=row.get("block_name"),
                color_index=row.get("color_index"),
                linetype=row.get("linetype"),
                raw=row.get("raw") or {},
            )
        )
    return records, payload.get("summary") or {}


def _baseline_batch(project: str) -> tuple[dict, Path]:
    """Най-старият запазен пакетен отчет за проекта — базовата линия."""

    matches = sorted(
        RUNS.glob(
            os.path.join(
                project + "_*", "output", "04_REVIEW_REPORTS", "*",
                project + "_geometry_batch.json",
            )
        )
    )
    if not matches:
        raise SystemExit("Няма запазен пакетен отчет за %s" % project)
    return _read_json(matches[0]), matches[0]


def _engine(project: str):
    from lion_rebar.engine.models import EnginePackage

    project_payload = _read_json(_project_file(project, "working/project.json"))
    relative = str(project_payload.get("extensions", {}).get("engine_snapshot") or "")
    snapshot = _project_file(project, relative.replace("\\", "/"))
    return EnginePackage.from_dict(_read_json(snapshot)), snapshot


# --------------------------------------------------------------------------
# независимо четене на DXF през ezdxf
# --------------------------------------------------------------------------
class DxfNeighbourhood:
    """Всичко геометрично в чертежа, с път през блоковете и преобразуване.

    Съдържанието на вмъкванията се чете през ``virtual_entities()`` — там
    координатите вече са преобразувани, точно както ги вижда и вносът.
    """

    MAX_DEPTH = 4
    CELL = 100.0

    def __init__(self, dxf_path: Path) -> None:
        import ezdxf

        self.doc = ezdxf.readfile(dxf_path)
        self.header_insunits = self.doc.header.get("$INSUNITS")
        self.items: list[dict[str, Any]] = []
        self.proxy_count = 0
        self.cells: dict[tuple[int, int], list[int]] = collections.defaultdict(list)
        for entity in self.doc.modelspace():
            self._walk(entity, block_path=(), transform=None, depth=0)
        for index, item in enumerate(self.items):
            for point in item["points"]:
                self.cells[
                    (int(math.floor(point[0] / self.CELL)), int(math.floor(point[1] / self.CELL)))
                ].append(index)

    # -- обхождане --------------------------------------------------------
    def _walk(self, entity, *, block_path: tuple[str, ...], transform, depth: int) -> None:
        dxftype = str(entity.dxftype()).upper()
        if dxftype in {"ACAD_PROXY_ENTITY", "ACAD_ZOMBIE_ENTITY"}:
            self.proxy_count += 1
            self._add(entity, block_path, transform, proxy=True)
            return
        if dxftype == "INSERT":
            own = {
                "block": str(entity.dxf.name),
                "insert_handle": str(getattr(entity.dxf, "handle", "") or ""),
                "insert": [
                    round(float(entity.dxf.insert.x), 4),
                    round(float(entity.dxf.insert.y), 4),
                ],
                "scale": [
                    round(float(entity.dxf.xscale or 1.0), 6),
                    round(float(entity.dxf.yscale or 1.0), 6),
                ],
                "rotation": round(float(entity.dxf.rotation or 0.0), 4),
            }
            if depth >= self.MAX_DEPTH:
                return
            try:
                children = list(entity.virtual_entities())
            except Exception:  # noqa: BLE001 — повреден/неподдържан блок
                children = []
            for child in children:
                self._walk(
                    child,
                    block_path=block_path + (own["block"],),
                    transform=transform or own,
                    depth=depth + 1,
                )
            return
        if dxftype in {"TEXT", "MTEXT", "ATTRIB", "DIMENSION", "OLE2FRAME", "VIEWPORT"}:
            return
        self._add(entity, block_path, transform, proxy=False)

    def _add(self, entity, block_path, transform, *, proxy: bool) -> None:
        points = self._points(entity)
        if not points:
            return
        handle = str(getattr(entity.dxf, "handle", "") or "")
        source_insert = str((transform or {}).get("insert_handle") or "")
        if not handle and source_insert:
            # Виртуалните обекти от блок нямат свой handle — вносът им дава
            # производен ``<handle на вмъкването>::B<индекс>``. Тук се пази поне
            # вмъкването, за да има проследимо име в доказателството.
            handle = "%s::B?" % source_insert
        self.items.append(
            {
                "handle": handle,
                "source_insert_handle": source_insert or None,
                "type": str(entity.dxftype()).upper(),
                "layer": str(getattr(entity.dxf, "layer", "0") or "0"),
                "block_path": list(block_path),
                "transform": transform,
                "proxy": proxy,
                "points": points,
                "length": _chain_length(points),
            }
        )

    @staticmethod
    def _points(entity) -> list[tuple[float, float]]:
        dxftype = str(entity.dxftype()).upper()
        try:
            if dxftype == "LINE":
                return [
                    (float(entity.dxf.start.x), float(entity.dxf.start.y)),
                    (float(entity.dxf.end.x), float(entity.dxf.end.y)),
                ]
            if dxftype == "LWPOLYLINE":
                return [(float(x), float(y)) for x, y, *_ in entity.get_points("xyb")]
            if dxftype == "POLYLINE":
                return [
                    (float(v.dxf.location.x), float(v.dxf.location.y))
                    for v in entity.vertices
                ]
            if dxftype in {"ARC", "CIRCLE", "ELLIPSE", "SPLINE"}:
                return [(float(p.x), float(p.y)) for p in entity.flattening(0.5)]
            if dxftype == "HATCH":
                out: list[tuple[float, float]] = []
                for path in entity.paths:
                    for vertex in getattr(path, "vertices", ()) or ():
                        out.append((float(vertex[0]), float(vertex[1])))
                return out
            if dxftype in {"SOLID", "3DFACE", "TRACE"}:
                out = []
                for name in ("vtx0", "vtx1", "vtx2", "vtx3"):
                    value = getattr(entity.dxf, name, None)
                    if value is not None:
                        out.append((float(value.x), float(value.y)))
                return out
            if dxftype == "POINT":
                return [(float(entity.dxf.location.x), float(entity.dxf.location.y))]
        except Exception:  # noqa: BLE001 — повреден обект не вали прегледа
            return []
        # Непознат тип: пробва се общата рамка.
        try:
            import ezdxf.bbox

            box = ezdxf.bbox.extents([entity])
            return [
                (float(box.extmin.x), float(box.extmin.y)),
                (float(box.extmax.x), float(box.extmax.y)),
            ]
        except Exception:  # noqa: BLE001
            return []

    # -- заявка -----------------------------------------------------------
    def near(self, probes: Sequence[tuple[float, float]], radius: float) -> list[dict[str, Any]]:
        span = max(1, int(math.ceil(radius / self.CELL)))
        indices: set[int] = set()
        for probe in probes:
            gx = int(math.floor(probe[0] / self.CELL))
            gy = int(math.floor(probe[1] / self.CELL))
            for dx in range(-span, span + 1):
                for dy in range(-span, span + 1):
                    indices.update(self.cells.get((gx + dx, gy + dy), ()))
        found = []
        for index in sorted(indices):
            item = self.items[index]
            distance = min(
                _point_to_chain(probe, item["points"]) for probe in probes
            )
            if distance <= radius:
                copy = dict(item)
                copy["distance"] = round(distance, 3)
                found.append(copy)
        found.sort(key=lambda row: row["distance"])
        return found


def _chain_length(points: Sequence[tuple[float, float]]) -> float:
    return round(
        sum(math.dist(points[i], points[i + 1]) for i in range(len(points) - 1)), 3
    )


def _point_to_chain(point, points) -> float:
    if len(points) == 1:
        return math.dist(point, points[0])
    best = math.inf
    for index in range(len(points) - 1):
        best = min(best, _point_to_segment(point, points[index], points[index + 1]))
    return best


def _point_to_segment(point, start, end) -> float:
    dx, dy = end[0] - start[0], end[1] - start[1]
    denominator = dx * dx + dy * dy
    if denominator <= 1e-12:
        return math.dist(point, start)
    t = ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / denominator
    t = max(0.0, min(1.0, t))
    return math.dist(point, (start[0] + t * dx, start[1] + t * dy))


# --------------------------------------------------------------------------
# анализ на един проект
# --------------------------------------------------------------------------
def analyse(project: str, *, verbose: bool = True) -> dict[str, Any]:
    from lion_rebar.dxf import geometry_candidates as gc
    from lion_rebar.dxf.geometry_candidates import (
        build_geometry_candidate_context,
        find_geometry_candidates,
    )
    from lion_rebar.dxf.local_detail_resolver import build_local_detail_index
    from lion_rebar.dxf.recognition_policy import RecognitionPolicy
    from lion_rebar.dxf.text_parser import looks_like_rebar_text
    from lion_rebar.engine.shape_index import EngineShapeIndex
    from lion_rebar.session.layer_classifier import high_confidence_noise_layers

    all_records, summary = _records(project)
    batch, batch_path = _baseline_batch(project)
    engine, snapshot = _engine(project)
    layer_report = _read_json(_project_file(project, "working/layer_classification.json"))
    noise = high_confidence_noise_layers(layer_report)
    records = [record for record in all_records if str(record.layer or "") not in noise]

    policy_run = RecognitionPolicy.from_settings(
        {"geometry_recognition": batch.get("recognition_policy") or {}}
    )
    rows = [
        row
        for row in batch.get("rows", [])
        if row.get("annotation_class") == "ACTIONABLE" and not row.get("candidate_handles")
    ]
    by_handle = {record.handle: record for record in all_records}
    shape_index = EngineShapeIndex.from_shapes(tuple(engine.shapes))

    # --- слоеве: кой носи примитивни типове и каква роля му дава речникът ---
    layer_types: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for record in all_records:
        layer_types[str(record.layer or "")][record.entity_type.upper()] += 1
    roles = {
        layer: gc._rebar_geometry_layer_role(layer, policy_run) for layer in layer_types
    }
    unknown_primitive_layers = tuple(
        sorted(
            layer
            for layer, counts in layer_types.items()
            if roles.get(layer) is None
            and any(kind in PRIMITIVE_TYPES for kind in counts)
            and layer not in noise
        )
    )

    policy_wide = replace(
        policy_run, declared_rebar_geometry_layers=unknown_primitive_layers
    )
    # Вариант с върнатите NOISE слоеве — само за да се провери дали NOISE
    # филтърът е свалил армировъчна геометрия.
    policy_noise = replace(
        policy_wide,
        declared_rebar_geometry_layers=tuple(
            sorted(set(unknown_primitive_layers) | {layer for layer in noise})
        ),
    )

    if verbose:
        print("  %s: %d надписа без кандидат, NOISE слоеве %s"
              % (project, len(rows), sorted(noise) or "няма"))
        print("     слоеве с примитивни типове, непознати за речника: %s"
              % (list(unknown_primitive_layers) or "няма"))

    context_run = build_geometry_candidate_context(records, policy=policy_run)
    context_wide = build_geometry_candidate_context(records, policy=policy_wide)
    context_noise = build_geometry_candidate_context(all_records, policy=policy_noise)

    # Както в ``build_batch_geometry_report``: същият избор на надписи и същият
    # индекс на локалните детайли. Без тях повторението не е за този прогон.
    allowed_annotation_types = {"BLOCK_ATTR_TEXT"}
    if policy_run.include_text_mtext_annotations:
        allowed_annotation_types.update({"TEXT", "MTEXT"})
    annotations = [
        entity
        for entity in records
        if entity.entity_type.upper() in allowed_annotation_types
        and looks_like_rebar_text(entity.text or "")
    ]
    local_detail_index = build_local_detail_index(annotations, records, policy=policy_run)

    def run(context, policy, annotation, entities, *, use_local_detail: bool = True):
        local_detail = (
            local_detail_index.assignment_for(str(annotation.handle))
            if use_local_detail
            else None
        )
        return find_geometry_candidates(
            annotation,
            entities,
            max_candidates=8,
            shape_definitions=tuple(engine.shapes),
            control_tests=tuple(engine.tests),
            policy=policy,
            context=context,
            shape_index=shape_index,
            local_detail_shape_code=(
                local_detail.shape_code if local_detail else None
            ),
            local_detail_rule_code=(
                local_detail.rule_code if local_detail else None
            ),
            local_detail_rule_note=(local_detail.note if local_detail else None),
            allowed_dimension_handles=(
                local_detail.dimension_handles
                if local_detail and local_detail.shape_code == "F006"
                else None
            ),
        )

    dxf_path = _project_file(project, "source/original.dxf")
    neighbourhood = DxfNeighbourhood(dxf_path)
    leaders = [r for r in records if r.entity_type.upper() == "LEADER"]

    per_position: list[dict[str, Any]] = []
    reproduced = 0
    for row in rows:
        handle = str(row.get("annotation_handle") or "")
        annotation = by_handle.get(handle)
        declared = float(row.get("declared_length_cm") or 0.0)
        record: dict[str, Any] = {
            "project": project,
            "annotation_handle": handle,
            "annotation_text": row.get("annotation_text"),
            "annotation_layer": annotation.layer if annotation else None,
            "declared_length_cm": declared,
            "diameter_mm": row.get("diameter_mm"),
        }
        if annotation is None:
            record["cause"] = "UNKNOWN"
            record["why"] = "Надписът липсва в extracted_entities.json."
            per_position.append(record)
            continue

        probes = gc._annotation_probe_points(
            annotation, records, policy_run, leader_entities=leaders
        )
        radius = policy_run.search_distance_limit(declared)
        primary = policy_run.primary_search_distance_limit(declared)
        record["search_radius_raw"] = round(radius, 2)
        record["primary_radius_raw"] = round(primary, 2)
        record["probe_count"] = len(probes)
        if not probes:
            record["cause"] = "UNKNOWN"
            record["why"] = "Надписът няма координата — не може да се мери близост."
            per_position.append(record)
            continue

        # Отчетът на прогона е по-силно доказателство от повторението: той
        # носи и глобалните решения след ранжирането.
        record["report_analysis_status"] = row.get("analysis_status")
        record["report_rejections"] = dict(row.get("rejection_counts") or {})
        record["report_review_reason_codes"] = list(row.get("review_reason_codes") or ())
        record["report_ownership_rejected_candidate"] = row.get(
            "ownership_rejected_candidate"
        )
        record["report_master_shape_code"] = row.get("master_shape_code")
        record["report_canonical_signature"] = row.get("canonical_signature")
        record["report_candidate_confidence"] = row.get("candidate_confidence")
        record["report_candidate_notes"] = [
            str(note) for note in (row.get("candidate_notes") or ())
        ][:3]

        base = run(context_run, policy_run, annotation, records)
        record["baseline_status"] = base.status
        record["baseline_candidates"] = len(base.candidates)
        record["baseline_rejections"] = dict(base.rejection_counts)
        if not base.candidates:
            reproduced += 1
        record["chain_run"] = _chain_evidence(
            context_run, probes, radius, declared, policy_run
        )

        # Контра-опит: същото търсене БЕЗ наложения от местния детайл shape_code.
        # Ако кандидатът се появява само така, загубата е от обвързването с
        # най-близкия детайлен надпис, не от липса на геометрия.
        detail = local_detail_index.assignment_for(handle)
        record["local_detail"] = (
            {
                "shape_code": detail.shape_code,
                "rule_code": detail.rule_code,
                "label_handle": detail.label_handle,
                "label_text": detail.label_text,
                "label_distance_raw": round(float(detail.label_distance_raw or 0.0), 2),
            }
            if detail is not None
            else None
        )
        if detail is not None and not base.candidates:
            free = run(
                context_run, policy_run, annotation, records, use_local_detail=False
            )
            record["no_detail_candidates"] = len(free.candidates)
            if free.candidates:
                best = free.candidates[0]
                record["no_detail_top"] = {
                    "handles": list(best.handles),
                    "layer": best.layer,
                    "geometry_length_raw": round(best.geometry_length_raw, 3),
                    "numeric_difference": round(best.numeric_difference, 3),
                    "distance_raw": round(best.distance_raw, 3),
                    "master_shape_code": best.master_shape_code,
                    "confidence": best.confidence,
                    "basis": best.basis,
                }

        wide = run(context_wide, policy_wide, annotation, records)
        record["wide_status"] = wide.status
        record["wide_candidates"] = len(wide.candidates)
        record["wide_rejections"] = dict(wide.rejection_counts)
        if wide.candidates:
            top = wide.candidates[0]
            record["wide_top"] = {
                "handles": list(top.handles),
                "layer": top.layer,
                "segment_count": top.segment_count,
                "geometry_length_raw": round(top.geometry_length_raw, 3),
                "effective_length_cm": top.effective_geometry_length_cm,
                "numeric_difference": round(top.numeric_difference, 3),
                "distance_raw": round(top.distance_raw, 3),
                "basis": top.basis,
                "confidence": top.confidence,
                "canonical_signature": top.canonical_signature,
                "master_shape_code": top.master_shape_code,
                "master_shape_score": top.master_shape_score,
                "uniform_scale_factor": top.uniform_scale_factor,
                "review_reason_codes": list(top.review_reason_codes),
            }

        noise_gain = None
        if noise:
            with_noise = run(context_noise, policy_noise, annotation, all_records)
            if with_noise.candidates and not wide.candidates:
                noise_gain = {
                    "handles": list(with_noise.candidates[0].handles),
                    "layer": with_noise.candidates[0].layer,
                }
            record["noise_variant_candidates"] = len(with_noise.candidates)
        record["noise_variant_gain"] = noise_gain

        near = neighbourhood.near(probes, radius)
        near_far = neighbourhood.near(probes, radius * 3.0)
        record["dxf_near"] = _census(near)
        record["dxf_near_3x"] = _census(near_far)
        record["dxf_near_top"] = [
            {
                "handle": item["handle"],
                "source_insert_handle": item.get("source_insert_handle"),
                "type": item["type"],
                "layer": item["layer"],
                "block_path": item["block_path"],
                "transform": item["transform"],
                "proxy": item["proxy"],
                "distance": item["distance"],
                "length": item["length"],
            }
            for item in near[:6]
        ]
        record["chain"] = _chain_evidence(
            context_wide, probes, radius, declared, policy_wide
        )
        record["global_match"] = _global_length_match(
            context_wide, probes, declared, policy_run,
            diameter_mm=row.get("diameter_mm"),
        )
        record["scale_match"] = _scale_match(
            near, declared, policy_run, insunits=neighbourhood.header_insunits
        )
        per_position.append(record)

    for record in per_position:
        _classify(record, roles=roles, noise=noise, policy=policy_run)

    return {
        "project": project,
        "batch_report": str(batch_path),
        "inputs_from_run_folder": str(_baseline_run_dir(project) or PROEKTI / project),
        "source_dxf": str(dxf_path),
        "engine_snapshot": str(snapshot),
        "engine_version": engine.version,
        "units_code": summary.get("units_code"),
        "units_name": summary.get("units_name"),
        "insunits_header": neighbourhood.header_insunits,
        "modelspace_proxy_entities": neighbourhood.proxy_count,
        "noise_layers": sorted(noise),
        "unknown_primitive_layers": list(unknown_primitive_layers),
        "actionable": sum(
            1 for row in batch.get("rows", []) if row.get("annotation_class") == "ACTIONABLE"
        ),
        "actionable_without_geometry": len(rows),
        "baseline_reproduced": reproduced,
        "positions": per_position,
    }


def _census(items: Iterable[dict[str, Any]]) -> dict[str, Any]:
    items = list(items)
    by_type = collections.Counter(item["type"] for item in items)
    by_layer = collections.Counter(item["layer"] for item in items)
    return {
        "total": len(items),
        "by_type": dict(by_type),
        "by_layer": dict(by_layer.most_common(8)),
        "in_block": sum(1 for item in items if item["block_path"]),
        "proxy": sum(1 for item in items if item["proxy"]),
        "primitive_type": sum(1 for item in items if item["type"] in PRIMITIVE_TYPES),
        "other_geometry_type": sum(
            1 for item in items if item["type"] in NON_PRIMITIVE_GEOMETRY
        ),
    }


def _chain_evidence(context, probes, radius, declared, policy) -> dict[str, Any]:
    """Дали наблизо има сегменти, чиято СУМА пасва, но не са свързани."""

    nearby = context.nearby_primitives(probes, radius)
    if not nearby:
        return {"nearby_primitives": 0}
    lengths = sorted((round(p.length_raw, 2) for p in nearby), reverse=True)
    components = context.connected_paths(probes, radius, policy)
    best_component = 0.0
    for component in components:
        total = sum(p.length_raw for p in component)
        if abs(total - declared) < abs(best_component - declared):
            best_component = total
    tolerance = policy.hard_length_difference_limit(declared)
    # Минималната празнина между краищата на близките примитиви.
    gaps: list[float] = []
    for index, left in enumerate(nearby):
        for right in nearby[index + 1:]:
            gaps.append(
                min(
                    math.dist(a, b)
                    for a in (left.start, left.end)
                    for b in (right.start, right.end)
                )
            )
    sum_all = sum(p.length_raw for p in nearby)
    return {
        "nearby_primitives": len(nearby),
        "nearby_handles": [
            primitive.handle
            for primitive in sorted(
                nearby, key=lambda item: abs(item.length_raw - declared)
            )[:6]
        ],
        "nearby_lengths": lengths[:8],
        "component_count": len(components),
        "best_component_length": round(best_component, 2),
        "sum_nearby_length": round(sum_all, 2),
        "declared": declared,
        "hard_tolerance": round(tolerance, 2),
        "single_fits": any(abs(value - declared) <= tolerance for value in lengths),
        "component_fits": abs(best_component - declared) <= tolerance,
        "min_gap": round(min(gaps), 3) if gaps else None,
        "endpoint_tolerance": policy.endpoint_tolerance_raw,
        "near_gap_max": policy.near_gap_max_raw,
    }


def _global_length_match(context, probes, declared, policy, *, diameter_mm) -> dict[str, Any]:
    """Най-близкият обект В ЦЕЛИЯ чертеж с дължина, равна на обявената.

    Отговаря на въпроса, който липсата на кандидат сама не отговаря: прътът
    съществува ли изобщо, и ако да — колко далеч от надписа стои. Мери се с
    ТОЧНИЯ допуск на политиката, не с окото.
    """

    from lion_rebar.dxf import geometry_candidates as gc

    tolerance = policy.exact_length_tolerance(declared)
    best_rebar: tuple[float, Any] | None = None
    best_any: tuple[float, Any] | None = None
    best_diameter: tuple[float, Any] | None = None
    declared_diameter = None
    try:
        declared_diameter = float(diameter_mm) if diameter_mm is not None else None
    except (TypeError, ValueError):
        declared_diameter = None
    for primitive in context.primitives:
        if abs(primitive.length_raw - declared) > tolerance:
            continue
        distance = min(
            gc._distance_point_to_segments(probe, primitive.segments) for probe in probes
        )
        if best_any is None or distance < best_any[0]:
            best_any = (distance, primitive)
        role = gc._rebar_geometry_layer_role(primitive.layer, policy)
        if role is not None and (best_rebar is None or distance < best_rebar[0]):
            best_rebar = (distance, primitive)
        layer_diameter = gc._diameter_from_layer(primitive.layer)
        if (
            declared_diameter is not None
            and layer_diameter is not None
            and abs(layer_diameter - declared_diameter) <= 0.01
            and (best_diameter is None or distance < best_diameter[0])
        ):
            best_diameter = (distance, primitive)

    def describe(found) -> dict[str, Any] | None:
        if found is None:
            return None
        distance, primitive = found
        return {
            "handle": primitive.handle,
            "layer": primitive.layer,
            "entity_type": primitive.entity_type,
            "length_raw": round(primitive.length_raw, 3),
            "distance_raw": round(distance, 3),
        }

    return {
        "exact_tolerance": round(tolerance, 3),
        "nearest_rebar_layer": describe(best_rebar),
        "nearest_any_layer": describe(best_any),
        "nearest_matching_diameter_layer": describe(best_diameter),
    }


def _scale_match(
    near_items: Sequence[dict[str, Any]],
    declared: float,
    policy,
    *,
    insunits: Any,
) -> dict[str, Any] | None:
    """Мащаб или единици: пасва ли дължината СЛЕД обосновано преобразуване.

    Умишлено тясно, за да не произведе съвпадение по случайност:

    * само обекти от приетите типове (LINE/LWPOLYLINE/POLYLINE/ARC) и само на
      слой, който речникът признава за армировъчен — обиколка на кръг върху
      слой за линийки не е прът;
    * само обосновани множители: ``1/мащаб`` на вмъкването, което носи обекта
      (``virtual_entities`` връща вече умножените координати), и ``0.1``, ако
      заглавието на файла казва mm ($INSUNITS=4), а сравнението е в cm;
    * попадението се мери с ТОЧНИЯ, не с грубия допуск.
    """

    from lion_rebar.dxf import geometry_candidates as gc

    exact = policy.exact_length_tolerance(declared)
    best: dict[str, Any] | None = None
    for item in near_items:
        if str(item.get("type") or "").upper() not in PRIMITIVE_TYPES:
            continue
        if gc._rebar_geometry_layer_role(str(item.get("layer") or ""), policy) is None:
            continue
        length = float(item.get("length") or 0.0)
        if length <= 0.0 or abs(length - declared) <= exact:
            continue
        factors: list[tuple[str, float]] = []
        try:
            if int(insunits) == 4:
                factors.append(("mm→cm 0.1 ($INSUNITS=4)", 0.1))
        except (TypeError, ValueError):
            pass
        transform = item.get("transform") or {}
        scale = transform.get("scale") or ()
        if scale and float(scale[0] or 0.0) not in (0.0, 1.0):
            sx = abs(float(scale[0]))
            if sx > 0.0:
                factors.append(("1/мащаб на блока %.4g" % sx, 1.0 / sx))
        for name, factor in factors:
            difference = abs(length * factor - declared)
            if difference > exact:
                continue
            if best is None or difference < best["difference"]:
                best = {
                    "handle": item.get("handle"),
                    "layer": item.get("layer"),
                    "type": item.get("type"),
                    "block_path": item.get("block_path"),
                    "transform": transform or None,
                    "raw_length": round(length, 3),
                    "factor": name,
                    "converted_length": round(length * factor, 3),
                    "declared": declared,
                    "difference": round(difference, 3),
                    "exact_tolerance": round(exact, 3),
                }
    return best


def _classify(record: dict[str, Any], *, roles, noise, policy) -> None:
    """Причината — по доказателство, с изричен UNKNOWN когато липсва.

    Редът е от най-силното доказателство към най-слабото:
    решението на прогона → какво е имал програмният индекс → какво е физически
    в DXF → и чак тогава отказ да се назове причина.
    """

    if record.get("cause"):
        return
    top = record.get("wide_top")
    near = record.get("dxf_near") or {}
    near3 = record.get("dxf_near_3x") or {}
    chain_run = record.get("chain_run") or {}
    chain_wide = record.get("chain") or {}
    top_items = record.get("dxf_near_top") or []
    rejections = record.get("report_rejections") or {}
    declared = float(record.get("declared_length_cm") or 0.0)
    hard = policy.hard_length_difference_limit(declared)
    radius = float(record.get("search_radius_raw") or 0.0)
    global_match = record.get("global_match") or {}

    record["dxf_handles"] = record.get("dxf_handles") or []

    def outside_radius_match() -> dict[str, Any] | None:
        """Точна по дължина геометрия, но ИЗВЪН радиуса на търсене.

        Има таван. Обект с еднаква дължина на 700 единици от надписа не е
        „неговият прът, поставен малко далеч“ — това е близост, приета за
        принадлежност, и точно този дефект е забранен. Приема се само до
        ``3 × радиуса``, и то не повече от втория пръстен на политиката.
        """

        ceiling = min(3.0 * radius, float(policy.extended_search_distance_raw))
        for key in ("nearest_matching_diameter_layer", "nearest_rebar_layer"):
            found = global_match.get(key)
            if not found:
                continue
            distance = float(found["distance_raw"])
            if radius < distance <= ceiling:
                found = dict(found)
                found["key"] = key
                found["ceiling"] = round(ceiling, 2)
                return found
        return None

    # ---- 0. Глобалното решение на пакета: геометрията е отишла у друг надпис.
    if "FOREIGN_GEOMETRY_REJECTED" in (record.get("report_review_reason_codes") or ()):
        rejected = record.get("report_ownership_rejected_candidate") or {}
        handles = [str(h) for h in (rejected.get("handles") or ())]
        record["cause"] = "OWNERSHIP_TAKEN"
        record["stage_lost"] = (
            "batch_geometry_report._clear_foreign_geometry_candidate "
            "(lion_rebar/dxf/batch_geometry_report.py:5517): кандидатът е намерен "
            "и е разпознат, но припокриващите се DXF обекти са дадени на друг "
            "надпис с по-точно сурово съвпадение и няма свободен резервен."
        )
        record["why"] = (
            "Геометрията СЪЩЕСТВУВА и е била съпоставена (MASTER %s, оценка „%s“, "
            "canonical %s); отнета е при глобалното решение за собственост, "
            "затова редът излиза с „НЯМА КАНДИДАТ“. Бележка на самия отчет: %s"
            % (
                rejected.get("master_shape_code")
                or record.get("report_master_shape_code")
                or "няма",
                record.get("report_candidate_confidence") or "—",
                rejected.get("canonical_signature")
                or record.get("report_canonical_signature")
                or "—",
                (record.get("report_candidate_notes") or ["—"])[0][:260],
            )
        )
        record["dxf_handles"] = handles[:8] or [
            item["handle"] for item in top_items[:3]
        ]
        record["block_path"] = _block_path_for(top_items, record["dxf_handles"])
        record["transform"] = _transform_for(top_items, record["dxf_handles"])
        return

    # ---- 0б. Наложената от местния детайл форма е убила силен кандидат.
    if record.get("no_detail_top"):
        best = record["no_detail_top"]
        detail = record.get("local_detail") or {}
        record["cause"] = "LOCAL_DETAIL_OVERRIDE"
        record["stage_lost"] = (
            "local_detail_resolver.build_local_detail_index → "
            "find_geometry_candidates(local_detail_shape_code=…) "
            "(lion_rebar/dxf/local_detail_resolver.py): наложената форма %s "
            "изхвърля кандидатите, които не са тази форма."
            % detail.get("shape_code")
        )
        record["why"] = (
            "Геометрията СЪЩЕСТВУВА и е силна: %s на слой %r, L=%.2f срещу "
            "обявена %.2f (разлика %.2f), на %.2f от надписа, MASTER %s, "
            "оценка „%s“. Изчезва само защото местният детайл налага форма %s, "
            "взета от надпис %s %r на %.2f от позицията."
            % (best["handles"], best["layer"], best["geometry_length_raw"],
               declared, best["numeric_difference"], best["distance_raw"],
               best["master_shape_code"], best["confidence"],
               detail.get("shape_code"), detail.get("label_handle"),
               detail.get("label_text"), detail.get("label_distance_raw") or 0.0)
        )
        record["dxf_handles"] = best["handles"][:8]
        record["length_agrees"] = abs(float(best["numeric_difference"])) <= hard
        record["block_path"] = _block_path_for(top_items, best["handles"])
        record["transform"] = _transform_for(top_items, best["handles"])
        return

    if record.get("baseline_candidates"):
        record["cause"] = "UNKNOWN"
        record["stage_lost"] = "неустановен — повторението не възпроизвежда прогона"
        record["why"] = (
            "Повторението със същата политика дава %d кандидата, а отчетът на "
            "прогона казва „НЯМА КАНДИДАТ“ (%s). Разликата е в пакетния слой, "
            "не в търсенето; причината не е установена."
            % (record["baseline_candidates"], record.get("report_analysis_status"))
        )
        return

    run_primitives = int(chain_run.get("nearby_primitives") or 0)
    exact = float((global_match.get("exact_tolerance") or 0.0))

    # ---- 1а. Обявяването на слоя връща ТОЧНАТА дължина.
    # Най-силното доказателство за филтър по слой: същият код, същата политика,
    # добавен е САМО слоят — и се ражда кандидат, чиято измерена дължина съвпада
    # с обявената в точния допуск. Проверява се и че слоят наистина е непознат
    # за речника, за да не се приписва на филтъра нещо друго.
    if top and roles.get(top["layer"]) is None:
        difference = abs(float(top["numeric_difference"]))
        handles = top["handles"]
        in_block = [h for h in handles if "::B" in h]
        record["length_agrees"] = difference <= exact
        record["dxf_handles"] = handles[:8]
        record["block_path"] = _block_path_for(top_items, handles)
        record["transform"] = _transform_for(top_items, handles)
        if difference <= exact:
            record["cause"] = "IN_BLOCK" if in_block else "FILTERED"
            record["stage_lost"] = STAGE_LAYER_FILTER
            record["why"] = (
                "%sСлой %r не е в речника на _rebar_geometry_layer_role (роля "
                "None), затова обектът никога не става GeometryPrimitive. Само с "
                "обявен слой същият код ражда кандидат %s: измерена L=%.2f срещу "
                "обявена %.2f (разлика %.2f при точен допуск %.2f), на %.2f от "
                "надписа, MASTER %s, оценка „%s“."
                % (("Обектът е ВЪТРЕ в блок (производен handle %s). " % in_block[:2])
                   if in_block else "",
                   top["layer"], handles[:3], top["geometry_length_raw"], declared,
                   difference, exact, top["distance_raw"],
                   top["master_shape_code"] or "няма", top["confidence"])
            )
            return

    # ---- 1. Диаметърът в слоя / proxy собственост изхвърля наличната геометрия.
    if "ГРЕШЕН ДИАМЕТЪР В СЛОЯ" in rejections and run_primitives:
        record["cause"] = "FILTERED"
        record["stage_lost"] = (
            "find_geometry_candidates → reject_explicit_diameter_mismatch "
            "(lion_rebar/dxf/geometry_candidates.py:718): слоят кодира друг "
            "диаметър (N…), затова геометрията се отхвърля."
        )
        record["why"] = (
            "Програмата е имала %d примитива в радиуса и е отхвърлила %d от тях с "
            "„ГРЕШЕН ДИАМЕТЪР В СЛОЯ“ (надпис Ø%s). Останалите откази: %s."
            % (run_primitives, rejections.get("ГРЕШЕН ДИАМЕТЪР В СЛОЯ"),
               record.get("diameter_mm"),
               {k: v for k, v in rejections.items() if k != "ГРЕШЕН ДИАМЕТЪР В СЛОЯ"} or "няма")
        )
        record["dxf_handles"] = list(chain_run.get("nearby_handles") or [])[:5]
        return
    if "ЧУЖД ДИАМЕТЪР ПО PROXY СОБСТВЕНОСТ" in rejections and run_primitives:
        record["cause"] = "PROXY"
        record["stage_lost"] = (
            "find_geometry_candidates → _proxy_owner_diameter_mismatch "
            "(lion_rebar/dxf/geometry_candidates.py:730): proxy геометрията е "
            "обявена за чужд диаметър и се отхвърля."
        )
        record["why"] = (
            "Геометрията идва от ACAD_PROXY_ENTITY (в модела %s proxy обекта около "
            "надписа). %d примитива са отхвърлени с „ЧУЖД ДИАМЕТЪР ПО PROXY "
            "СОБСТВЕНОСТ“, плюс %s."
            % (near.get("proxy"), rejections["ЧУЖД ДИАМЕТЪР ПО PROXY СОБСТВЕНОСТ"],
               {k: v for k, v in rejections.items()
                if k != "ЧУЖД ДИАМЕТЪР ПО PROXY СОБСТВЕНОСТ"})
        )
        record["dxf_handles"] = list(chain_run.get("nearby_handles") or [])[:5]
        return

    # ---- 2. Разклонена/несвързана геометрия: сегментите не са станали един път.
    if "РАЗКЛОНЕНА ИЛИ НЕСВЪРЗАНА ГЕОМЕТРИЯ" in rejections and run_primitives:
        record["cause"] = "SEGMENTS_NOT_JOINED"
        record["stage_lost"] = STAGE_CHAIN
        record["why"] = (
            "Програмата е имала %d примитива в радиуса; %d кандидата са отказани "
            "с „РАЗКЛОНЕНА ИЛИ НЕСВЪРЗАНА ГЕОМЕТРИЯ“ и %s с „АБСУРДНА РАЗЛИКА В "
            "ДЪЛЖИНАТА“. Свързани компоненти: %s, най-близък по дължина %.2f "
            "срещу обявена %.2f (допуск %.2f)."
            % (run_primitives, rejections["РАЗКЛОНЕНА ИЛИ НЕСВЪРЗАНА ГЕОМЕТРИЯ"],
               rejections.get("АБСУРДНА РАЗЛИКА В ДЪЛЖИНАТА", 0),
               chain_run.get("component_count"),
               chain_run.get("best_component_length") or 0.0, declared, hard)
        )
        record["dxf_handles"] = list(chain_run.get("nearby_handles") or [])[:5]
        return

    # ---- 3. Нула примитива у програмата: геометрията не е станала примитив.
    if run_primitives == 0:
        if top:
            handles = top["handles"]
            in_block = [h for h in handles if "::B" in h]
            proxy = [h for h in handles if h.startswith("PXG_")]
            fit = abs(float(top["numeric_difference"])) <= hard
            record["length_agrees"] = bool(fit)
            record["dxf_handles"] = handles[:8]
            record["block_path"] = _block_path_for(top_items, handles)
            record["transform"] = _transform_for(top_items, handles)
            if proxy:
                record["cause"] = "PROXY"
                record["stage_lost"] = STAGE_LAYER_FILTER
                record["why"] = (
                    "Кандидатът идва от proxy геометрия %s; измерена L=%.2f срещу "
                    "обявена L=%.2f." % (proxy[:3], top["geometry_length_raw"], declared)
                )
                return
            if in_block:
                record["cause"] = "IN_BLOCK"
                record["stage_lost"] = STAGE_LAYER_FILTER
                record["why"] = (
                    "Обектът е ВЪТРЕ в блок — производен handle %s, слой %r. "
                    "Вносът го разгъва, но речникът на слоя му дава роля None, "
                    "затова не става примитив. С обявен слой се ражда кандидат "
                    "L=%.2f срещу обявена L=%.2f (разлика %.2f, MASTER %s; %s)."
                    % (in_block[:3], top["layer"], top["geometry_length_raw"],
                       declared, top["numeric_difference"],
                       top["master_shape_code"] or "няма",
                       "дължината пасва" if fit else "дължината НЕ пасва")
                )
                return
            record["cause"] = "FILTERED"
            record["stage_lost"] = STAGE_LAYER_FILTER
            record["why"] = (
                "Обектът е в модела, слой %r, тип от приетите. Речникът в "
                "_rebar_geometry_layer_role не познава този слой (роля None), "
                "затова обектът никога не става GeometryPrimitive и в ранжирането "
                "няма нито един отказ. С обявен слой се ражда кандидат %s: "
                "L=%.2f срещу обявена L=%.2f (разлика %.2f, MASTER %s, %s)."
                % (top["layer"], top["handles"][:3], top["geometry_length_raw"],
                   declared, top["numeric_difference"],
                   top["master_shape_code"] or "няма", top["confidence"])
            )
            return

        if record.get("noise_variant_gain"):
            record["cause"] = "FILTERED"
            record["stage_lost"] = STAGE_NOISE_FILTER
            record["why"] = (
                "Кандидат се появява само когато свалените NOISE слоеве се върнат: %s."
                % record["noise_variant_gain"]
            )
            return

        outside = outside_radius_match()
        if outside is not None:
            record["cause"] = "NO_ANNOTATION_LINK"
            record["stage_lost"] = STAGE_DISTANCE
            record["why"] = (
                "Прътът СЪЩЕСТВУВА: обект %s на слой %r с L=%.2f срещу обявена "
                "%.2f (точен допуск %.2f), но стои на %.1f от надписа, а радиусът "
                "на търсене е %.1f. %sВ радиуса стоят само %s."
                % (outside["handle"], outside["layer"], outside["length_raw"],
                   declared, global_match.get("exact_tolerance"),
                   outside["distance_raw"], radius,
                   "Слоят кодира същия диаметър като надписа. "
                   if outside["key"] == "nearest_matching_diameter_layer" else "",
                   near.get("by_type") or "нищо")
            )
            record["dxf_handles"] = [outside["handle"]]
            record["length_agrees"] = True
            return

        if near.get("total", 0) == 0:
            if near3.get("total", 0) == 0:
                record["cause"] = "TRULY_ABSENT"
                record["stage_lost"] = "няма етап — в чертежа няма обект около надписа"
                record["why"] = (
                    "Независимо четене на DXF: нула геометрични обекта в радиус "
                    "%.1f и в тройния радиус %.1f (модел + рекурсивно разгънати "
                    "блокове)." % (record["search_radius_raw"],
                                   record["search_radius_raw"] * 3)
                )
            else:
                record["cause"] = "NO_ANNOTATION_LINK"
                record["stage_lost"] = STAGE_DISTANCE
                record["why"] = (
                    "Нула обекта в радиуса %.1f, но %d обекта в тройния радиус: "
                    "надписът стои по-далеч от прът, отколкото политиката търси."
                    % (record["search_radius_raw"], near3["total"])
                )
            return

        only_circles = (
            near.get("primitive_type", 0) == 0
            and near.get("other_geometry_type", 0) > 0
            and set(near.get("by_type", {})) <= {"CIRCLE", "HATCH", "SOLID", "POINT"}
        )
        if only_circles:
            record["cause"] = "SCHEMATIC_ONLY"
            record["stage_lost"] = STAGE_TYPE_FILTER
            record["why"] = (
                "В радиуса стоят само символи без осева линия: %s (слоеве %s). "
                "Кръгът е сечение на прът, щриховката е запълване — производствен "
                "размер няма откъде да се вземе."
                % (near.get("by_type"), list(near.get("by_layer", {}))[:4])
            )
            record["dxf_handles"] = [item["handle"] for item in top_items[:5]]
            return
        if near.get("primitive_type", 0) == 0 and near.get("other_geometry_type", 0) > 0:
            record["cause"] = "FILTERED"
            record["stage_lost"] = STAGE_TYPE_FILTER
            record["why"] = (
                "В радиуса има само типове, които _primitive_from_record не приема: %s."
                % near.get("by_type")
            )
            record["dxf_handles"] = [item["handle"] for item in top_items[:5]]
            return
        if near.get("proxy"):
            record["cause"] = "PROXY"
            record["stage_lost"] = (
                "proxy_adapter.extract_acad_proxy_rebar "
                "(lion_rebar/dxf/proxy_adapter.py): около надписа стои "
                "ACAD_PROXY_ENTITY, чиято геометрия не е станала примитив."
            )
            record["why"] = (
                "В радиуса %.1f: %d proxy обекта, нула примитива у програмата."
                % (record["search_radius_raw"], near["proxy"])
            )
            record["dxf_handles"] = [
                item["handle"] for item in top_items if item["proxy"]
            ][:5]
            return

    # ---- 4. Имало е примитиви, но всички са паднали по дължина.
    if run_primitives and "АБСУРДНА РАЗЛИКА В ДЪЛЖИНАТА" in rejections:
        sum_fits = (
            abs(float(chain_run.get("sum_nearby_length") or 0.0) - declared) <= hard
        )
        if sum_fits and not chain_run.get("component_fits"):
            record["cause"] = "SEGMENTS_NOT_JOINED"
            record["stage_lost"] = STAGE_CHAIN
            record["why"] = (
                "%d близки примитива: сумата им %.2f пасва на обявената %.2f "
                "(допуск %.2f), но нито един свързан път не пасва; най-малката "
                "празнина между краища е %s при допуск %.2f."
                % (run_primitives, chain_run["sum_nearby_length"], declared, hard,
                   chain_run.get("min_gap"), chain_run.get("endpoint_tolerance"))
            )
            record["dxf_handles"] = [item["handle"] for item in top_items[:5]]
            return
        outside = outside_radius_match()
        if outside is not None:
            record["cause"] = "NO_ANNOTATION_LINK"
            record["stage_lost"] = STAGE_DISTANCE
            record["why"] = (
                "Близката геометрия падна по дължина (най-близки L=%s срещу %.2f), "
                "а обект с ТОЧНАТА обявена дължина съществува: %s на слой %r, "
                "L=%.2f, но на %.1f от надписа при радиус %.1f."
                % ((chain_run.get("nearby_lengths") or [None])[:3], declared,
                   outside["handle"], outside["layer"], outside["length_raw"],
                   outside["distance_raw"], radius)
            )
            record["dxf_handles"] = [outside["handle"]]
            record["length_agrees"] = True
            return

        if record.get("scale_match"):
            scale = record["scale_match"]
            record["cause"] = "SCHEMATIC_ONLY"
            record["stage_lost"] = (
                "find_geometry_candidates → hard_length_difference_limit: "
                "нарисуваният размер не е производственият. "
                "virtual_entities() връща вече умножените по мащаба на "
                "вмъкването координати (importer._exploded_block_records)."
            )
            record["why"] = (
                "Близкият обект %s (%s, слой %r, блок %s) е с L=%.2f; след %s "
                "става %.2f срещу обявена %.2f (разлика %.2f при допуск %.2f). "
                "Производствен размер от рисунката не бива да се вади без "
                "потвърдено проектно правило."
                % (scale["handle"] or "—", scale["type"], scale["layer"],
                   scale["block_path"], scale["raw_length"], scale["factor"],
                   scale["converted_length"], declared, scale["difference"],
                   scale["exact_tolerance"])
            )
            record["block_path"] = scale["block_path"] or None
            record["transform"] = scale["transform"]
            return

        if top and top.get("uniform_scale_factor"):
            record["cause"] = "SCHEMATIC_ONLY"
            record["stage_lost"] = (
                "find_geometry_candidates: единственото съвпадение е при "
                "равномерен схематичен мащаб %s — рисунката не е в производствен "
                "размер (schematic_scale_*)." % top["uniform_scale_factor"]
            )
            record["why"] = (
                "Близката геометрия пасва на MASTER %s само при равномерен мащаб "
                "%s; сурова L=%.2f срещу обявена %.2f."
                % (top.get("master_shape_code"), top["uniform_scale_factor"],
                   top["geometry_length_raw"], declared)
            )
            record["dxf_handles"] = top["handles"][:8]
            return
        record["cause"] = "UNKNOWN"
        record["stage_lost"] = (
            "find_geometry_candidates → hard_length_difference_limit "
            "(geometry_candidates.py:991, „АБСУРДНА РАЗЛИКА В ДЪЛЖИНАТА“)"
        )
        record["why"] = (
            "Програмата е имала %d примитива в радиуса, но всички паднаха по "
            "дължина: най-близка единична L=%s, най-близък компонент %.2f, сума "
            "%.2f срещу обявена %.2f (допуск %.2f). В целия чертеж няма обект с "
            "точната обявена дължина на армировъчен слой (%s). Дали това е "
            "правилният прът, НЕ е установено."
            % (run_primitives, (chain_run.get("nearby_lengths") or [None])[:4],
               chain_run.get("best_component_length") or 0.0,
               chain_run.get("sum_nearby_length") or 0.0, declared, hard,
               global_match.get("nearest_rebar_layer") or "няма")
        )
        record["dxf_handles"] = list(chain_run.get("nearby_handles") or [])[:5]
        return

    outside = outside_radius_match()
    if outside is not None and run_primitives == 0:
        record["cause"] = "NO_ANNOTATION_LINK"
        record["stage_lost"] = STAGE_DISTANCE
        record["why"] = (
            "Обект с точната обявена дължина съществува: %s, слой %r, L=%.2f, но "
            "на %.1f от надписа при радиус %.1f."
            % (outside["handle"], outside["layer"], outside["length_raw"],
               outside["distance_raw"], radius)
        )
        record["dxf_handles"] = [outside["handle"]]
        record["length_agrees"] = True
        return

    # ---- 5. Обявеният слой ражда кандидат, но дължината НЕ съвпада точно.
    # Филтърът по слой пак е причината ранжирането да е празно, но че точно
    # този обект е прътът, НЕ е доказано. Казва се и двете.
    if top and roles.get(top["layer"]) is None:
        difference = abs(float(top["numeric_difference"]))
        handles = top["handles"]
        record["cause"] = "IN_BLOCK" if any("::B" in h for h in handles) else "FILTERED"
        record["stage_lost"] = STAGE_LAYER_FILTER
        record["length_agrees"] = False
        record["why"] = (
            "Слой %r не е в речника (роля None) — затова в ранжирането няма нито "
            "един кандидат. С обявен слой се ражда кандидат %s, но измерената "
            "L=%.2f се различава от обявената %.2f с %.2f, а точният допуск е "
            "%.2f (оценка „%s“). Че този обект е прътът, НЕ е доказано."
            % (top["layer"], handles[:3], top["geometry_length_raw"], declared,
               difference, exact, top["confidence"])
        )
        record["dxf_handles"] = handles[:8]
        record["block_path"] = _block_path_for(top_items, handles)
        record["transform"] = _transform_for(top_items, handles)
        return

    filtered_in_radius = [
        item
        for item in top_items
        if str(item["type"]).upper() in PRIMITIVE_TYPES
        and roles.get(str(item["layer"])) is None
    ]
    record["cause"] = "UNKNOWN"
    record["stage_lost"] = "неустановен"
    record["why"] = (
        "Примитиви у програмата: %d (затова в ранжирането има %s отказ). В радиуса "
        "на DXF: %s, слоеве %s. %sС обявени слоеве кандидат %s, защото дължината "
        "не пасва. Най-близък обект с точната дължина в целия чертеж: %s. "
        "Причината не е установена."
        % (run_primitives, rejections or "нула",
           near.get("by_type"), list(near.get("by_layer", {}))[:4],
           ("В радиуса СТОИ обект от приет тип на непознат за речника слой "
            "(%s %s, слой %r, L=%s, на %s) — филтърът по слой обяснява защо "
            "ранжирането не вижда нищо, но НЕ е доказано, че това е прътът. "
            % (filtered_in_radius[0]["type"], filtered_in_radius[0]["handle"],
               filtered_in_radius[0]["layer"], filtered_in_radius[0]["length"],
               filtered_in_radius[0]["distance"]))
           if filtered_in_radius else "",
           "не се ражда" if not top else "се ражда",
           global_match.get("nearest_any_layer") or "няма")
    )
    if filtered_in_radius:
        record["dxf_handles"] = [item["handle"] for item in filtered_in_radius[:5]]


def _block_path_for(items, handles) -> list[str] | None:
    """Пътят през блоковете САМО за обекта, за когото се говори.

    Без резервен вариант „кой да е блок наблизо“: това би сложило чужд блок и
    чужд мащаб в доказателството.
    """

    wanted = {str(h).split("::")[0] for h in handles}
    for item in items:
        if item["handle"] and item["handle"] in wanted and item["block_path"]:
            return item["block_path"]
    return None


def _transform_for(items, handles) -> dict[str, Any] | None:
    wanted = {str(h).split("::")[0] for h in handles}
    for item in items:
        if item["handle"] and item["handle"] in wanted and item["transform"]:
            return item["transform"]
    return None


# --------------------------------------------------------------------------
# отчет
# --------------------------------------------------------------------------
#: ``OWNERSHIP_TAKEN`` не е в първоначалния списък на задачата. Появи се в
#: измерването: отчетът сам казва „ЧУЖДА ГЕОМЕТРИЯ ОТХВЪРЛЕНА ГЛОБАЛНО“.
#: Геометрията там съществува и е разпозната — отнета е от съседен надпис.
CAUSES = (
    "IN_BLOCK", "PROXY", "FILTERED", "NO_ANNOTATION_LINK",
    "SEGMENTS_NOT_JOINED", "SCHEMATIC_ONLY", "TRULY_ABSENT",
    "OWNERSHIP_TAKEN", "LOCAL_DETAIL_OVERRIDE", "UNKNOWN",
)


def _method_text(results) -> str:
    projects = ", ".join(result["project"] for result in results)
    reproduced = sum(result["baseline_reproduced"] for result in results)
    total = sum(result["actionable_without_geometry"] for result in results)
    return (
        "Знаменател: редове с annotation_class=ACTIONABLE и празно "
        "candidate_handles в НАЙ-СТАРИЯ запазен пакетен отчет на всеки проект "
        "(%s). Входовете се четат от папката на СЪЩИЯ прогон "
        "(working/extracted_entities.json, layer_classification.json, "
        "master/engine snapshot, source/original.dxf), не от друго копие. "
        "Три независими наблюдения за всяка позиция. (1) ПОВТАРЯНЕ: същият "
        "find_geometry_candidates, същата политика от отчета, същият закачен "
        "engine, същият избор на надписи и същият local_detail индекс, върху "
        "обектите без NOISE слоевете — %d от %d позиции пак дават нула "
        "кандидата; останалите са точно случаите, в които пакетният слой е "
        "изчистил намерения кандидат (ЧУЖДА ГЕОМЕТРИЯ ОТХВЪРЛЕНА ГЛОБАЛНО). "
        "(2) ОПИТ С ОБЯВЕНИ СЛОЕВЕ: същото търсене с "
        "declared_rebar_geometry_layers = всички слоеве на чертежа, които носят "
        "LINE/LWPOLYLINE/POLYLINE/ARC и на които речникът дава роля None. "
        "Причината се приписва на филтъра по слой само когато така роденият "
        "кандидат съвпадне с обявената дължина в ТОЧНИЯ допуск на политиката; "
        "иначе изрично се казва, че дължината не пасва. (3) НЕЗАВИСИМО ЧЕТЕНЕ "
        "НА DXF през ezdxf: модел + рекурсивно съдържанието на вмъкванията с "
        "преобразувани координати (дълбочина до 4), заедно с CIRCLE, HATCH, "
        "SOLID, SPLINE, ELLIPSE и proxy обектите, които вносът не превръща в "
        "примитиви. Оттам идват блоковият път и преобразуването. Освен това: "
        "контра-опит БЕЗ наложения от local_detail shape_code; търсене в целия "
        "чертеж за обект с ТОЧНАТА обявена дължина (и отделно на слой, кодиращ "
        "същия диаметър); проверка за мащаб/единици само с обосновани "
        "множители (1/мащаб на вмъкването, 0.1 при $INSUNITS=4) и само в "
        "точния допуск. "
        "КАКВО НЕ Е УСТАНОВЕНО. Не е мерено дали обявената дължина е вярна — "
        "сравнява се само с чертежа. Търсенето в целия чертеж е по ЕДИНИЧНИ "
        "обекти, не по вериги, затова прът от няколко сегмента далеч от надписа "
        "може да е пропуснат. Съответствието надпис↔прът НЕ е потвърдено "
        "визуално от оператор за нито една позиция. „Прътът съществува, но "
        "извън радиуса“ се приема само до 3 × радиуса и не повече от втория "
        "пръстен на политиката — обект с еднаква дължина по-далеч се отчита "
        "като UNKNOWN, не като „неговия“. Причините НЕ са предложения за "
        "промяна в поведението."
        % (projects, reproduced, total)
    )


def _cause_rows(results, cause):
    return [
        row
        for result in results
        for row in result["positions"]
        if row["cause"] == cause
    ]


def _fixable(results, totals) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    layer_rows = [
        row
        for row in _cause_rows(results, "FILTERED") + _cause_rows(results, "IN_BLOCK")
        if (row.get("wide_top") or {}).get("layer")
    ]
    exact = [row for row in layer_rows if row.get("length_agrees")]
    layers = collections.Counter(
        (row["project"], row["wide_top"]["layer"]) for row in exact
    )
    if exact:
        out.append(
            {
                "cause": "FILTERED — речникът на армировъчните слоеве",
                "positions_affected": len(exact),
                "what_a_general_fix_would_be": (
                    "Механизмът вече съществува и е общ: "
                    "RecognitionPolicy.declared_rebar_geometry_layers, пълнен от "
                    "потвърдения паспорт на чертежа "
                    "(project/convention_profile_decisions.policy_with_confirmed_profile). "
                    "Общата поправка НЕ е нова ключова дума в речника — това "
                    "оправя този офис и се къса на следващия, който пише "
                    "„Zhelezo“ или „Ferro“. Общото е пътят, по който операторът "
                    "обявява слоя за ТОЗИ чертеж, след като е видял "
                    "доказателството: колко надписа намират точната си дължина на "
                    "този слой. Измереното тук: %s."
                    % ", ".join(
                        "%s/%s — %d позиции" % (project, layer, count)
                        for (project, layer), count in layers.most_common()
                    )
                ),
                "risk": (
                    "Обявяването на слой прави ВСИЧКИ негови обекти кандидати. На "
                    "Тест-002 освен s-Reinf излиза и 1 кандидат от S-SLAB, тоест "
                    "широкото обявяване може да върже надпис към контур на плоча. "
                    "Затова обявяването остава по чертеж и от оператор, а "
                    "кандидатите пак минават през MASTER формата, дължината и "
                    "ръчното одобрение. Нито една от тези позиции не е гледана в "
                    "прозореца."
                ),
                "would_need_dimension_check": True,
            }
        )
    weak = [row for row in layer_rows if not row.get("length_agrees")]
    if weak:
        out.append(
            {
                "cause": "FILTERED — слой с роля None, но дължината не съвпада точно",
                "positions_affected": len(weak),
                "what_a_general_fix_would_be": (
                    "Същият път (обявен слой), но тук обявяването НЕ решава "
                    "позицията: кандидатът излиза извън точния допуск и остава за "
                    "ръчна проверка. Полезно като видимост за оператора, не като "
                    "автоматично одобрение."
                ),
                "risk": "Слаб кандидат, приет за силен, е точно забранената грешка.",
                "would_need_dimension_check": True,
            }
        )
    diameter = [
        row
        for row in _cause_rows(results, "FILTERED")
        if "ГРЕШЕН ДИАМЕТЪР В СЛОЯ" in (row.get("report_rejections") or {})
    ]
    if diameter:
        out.append(
            {
                "cause": "FILTERED — диаметър, кодиран в името на слоя (закон 2)",
                "positions_affected": len(diameter),
                "what_a_general_fix_would_be": (
                    "Нищо в кода. Отказът е верен: около надписа стоят пръти на "
                    "слой с ДРУГ кодиран диаметър (reject_explicit_diameter_"
                    "mismatch). Ако надписът е верен, прътът му не е нарисуван "
                    "там; ако слоят е верен, надписът е чужд. И двете са "
                    "операторска проверка, не автоматика."
                ),
                "risk": "Разхлабването на закон 2 връща тихо грешни диаметри.",
                "would_need_dimension_check": True,
            }
        )
    ownership = _cause_rows(results, "OWNERSHIP_TAKEN")
    if ownership:
        out.append(
            {
                "cause": "OWNERSHIP_TAKEN — геометрията е дадена на друг надпис",
                "positions_affected": len(ownership),
                "what_a_general_fix_would_be": (
                    "Не е липса на геометрия и не бива да се брои като такава. "
                    "_clear_foreign_geometry_candidate вече пази бележка кой е "
                    "спечелил и какво е отхвърлено. Общата поправка е ОТЧЕТНА: "
                    "отделен статус „отнета собственост“ вместо „НЯМА КАНДИДАТ“, "
                    "за да стигне позицията до оператора с двата надписа един до "
                    "друг. Смяната на самото решение за собственост е инженерна "
                    "промяна и иска отделно измерване."
                ),
                "risk": (
                    "Ако собствеността се раздаде на двата надписа, един прът се "
                    "брои два пъти в експорта."
                ),
                "would_need_dimension_check": False,
            }
        )
    detail = _cause_rows(results, "LOCAL_DETAIL_OVERRIDE")
    if detail:
        out.append(
            {
                "cause": "LOCAL_DETAIL_OVERRIDE — форма, наложена от най-близкия детайлен надпис",
                "positions_affected": len(detail),
                "what_a_general_fix_would_be": (
                    "Наложената форма изхвърля СИЛЕН кандидат с точна дължина. "
                    "Общата поправка: наложеният от local_detail shape_code да не "
                    "изтрива кандидат, който сам по себе си е точен по MASTER и по "
                    "дължина — тогава излиза конфликт за оператора, не празна "
                    "позиция. Обвързването „най-близък детайлен надпис“ също иска "
                    "граница: тук надпис за стреме („стр. 20N8/s=20“) на 63 "
                    "единици наложи F006 на прав Ø25 прът."
                ),
                "risk": (
                    "Отслабването на местния детайл може да върне случаите, за "
                    "които е направен (F006/F007 по отпечатани коти). Иска "
                    "регресионен тест за двете страни."
                ),
                "would_need_dimension_check": True,
            }
        )
    return out


def _not_fixable(results, totals) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    proxy = _cause_rows(results, "PROXY")
    if proxy:
        out.append(
            {
                "cause": "PROXY — геометрия от ACAD_PROXY_ENTITY",
                "positions_affected": len(proxy),
                "why": (
                    "Съдържанието на proxy обекта е ДИСПЛЕЙНО: примитивите са "
                    "това, което чуждото приложение е оставило да се нарисува, не "
                    "осева линия с производствен размер. Тук те падат по закон 2 и "
                    "4 (чужд диаметър по proxy собственост, разклонена "
                    "геометрия). Превръщането на display примитиви в "
                    "производствена геометрия НЕ се предлага — маркират се за "
                    "оператор. Верният изход е чертеж, записан с действителните "
                    "обекти, или потвърдено проектно правило за този диалект."
                ),
            }
        )
    schematic = _cause_rows(results, "SCHEMATIC_ONLY")
    if schematic:
        out.append(
            {
                "cause": "SCHEMATIC_ONLY — символ без производствен размер",
                "positions_affected": len(schematic),
                "why": (
                    "В радиуса стои само символ (кръг — сечение на прът, "
                    "щриховка) или рисунка, която пасва единствено след "
                    "равномерен мащаб. Производствена дължина от такъв символ не "
                    "може да се извади без потвърдено проектно правило; "
                    "измисленият размер е по-лош от липсващия."
                ),
            }
        )
    segments = _cause_rows(results, "SEGMENTS_NOT_JOINED")
    if segments:
        out.append(
            {
                "cause": "SEGMENTS_NOT_JOINED — разклонена/несвързана геометрия",
                "positions_affected": len(segments),
                "why": (
                    "Отказът е по закон 3 и 4: сегменти се свързват само през "
                    "общи крайни точки, а разклонена геометрия не е една форма. "
                    "Всички тези случаи са в един чертеж (s-7) и идват от proxy "
                    "графика, в която пръти, стремена и водачи се пресичат. "
                    "Свързването „по близост“ е точно дефектът, който проектът "
                    "забранява, затова тук няма обща поправка без решение на "
                    "собственика за този диалект."
                ),
            }
        )
    truly = _cause_rows(results, "TRULY_ABSENT")
    out.append(
        {
            "cause": "TRULY_ABSENT — в чертежа наистина няма обект",
            "positions_affected": len(truly),
            "why": (
                "Независимото четене на DXF намира геометрия в радиуса или в "
                "тройния радиус на всяка от %d позиции. Тоест изводът "
                "„геометрия няма“ не се потвърждава за нито една позиция в тези "
                "шест чертежа."
                % sum(result["actionable_without_geometry"] for result in results)
            )
            if not truly
            else "В чертежа няма геометричен обект около надписа.",
        }
    )
    out.append(
        {
            "cause": "IN_BLOCK — скрито във вложен блок",
            "positions_affected": len(_cause_rows(results, "IN_BLOCK")),
            "why": (
                "Няма такъв случай. Вносът разгъва вмъкванията до дълбочина 3 "
                "(importer.MAX_BLOCK_DEPTH), а измерената реална вложеност в "
                "шестте чертежа е най-много 2 и няма нито един XREF; блокова "
                "геометрия наистина стои около надписи (например Тест-003 "
                "„str.skr.gr.“, s-8 „A$Ce1a8c2d1“) и се вижда от програмата с "
                "производни handle-и <insert>::B<индекс>. Затова „скрито в блок“ "
                "не обяснява нито една позиция без геометрия."
            ),
        }
    )
    return out


def _uncertain(results) -> list[str]:
    unknown = _cause_rows(results, "UNKNOWN")
    by_project = collections.Counter(row["project"] for row in unknown)
    zero = [
        row for row in unknown
        if not (row.get("chain_run") or {}).get("nearby_primitives")
    ]
    rejected = [
        row for row in unknown
        if (row.get("chain_run") or {}).get("nearby_primitives")
    ]
    return [
        "UNKNOWN: %d позиции (%s). Причината НЕ е установена и не се гади."
        % (len(unknown), dict(by_project)),
        "%d от тях са с нула примитива в индекса на програмата: в радиуса стои "
        "обект от приет тип на непознат за речника слой, но дължината му не "
        "съвпада с обявената, затова обявяването на слоя НЕ ги решава. Част от "
        "надписите са количествени или мрежови („203N8x165/17- по oc D“, "
        "„1570N10x100 - етап 1“, „2170N10x47“), където един обект с тази дължина "
        "може и да не е начертан. Не е проверено при оператор." % len(zero),
        "%d от тях имат близки примитиви, отхвърлени с „АБСУРДНА РАЗЛИКА В "
        "ДЪЛЖИНАТА“. Дали нарисуваната фигура е схематична, или прътът е "
        "другаде, не е установено. Най-ясният единичен случай е Тест-003 "
        "6B93AF „2x28N8x100“: стремето стои ВЪТРЕ в блок str.skr.gr. с мащаб "
        "2.0 на слой „ARM stremena“, приет е като примитив, но измерената "
        "обиколка 154.68 не съвпада с обявената 100 нито сурова, нито делена на "
        "мащаба (77.34)." % len(rejected),
        "Знаменателят е %d позиции (%s), а не 318 от по-ранния отчет. Мери се по "
        "запазените отчети, не по нов прогон."
        % (
            sum(result["actionable_without_geometry"] for result in results),
            ", ".join(
                "%s %d" % (result["project"], result["actionable_without_geometry"])
                for result in results
            ),
        ),
        "Съответствието между надпис и прът не е потвърдено визуално за нито "
        "една позиция. Всяко число тук е от чертежа и от кода, не от оператор.",
        "Причината се чете от НАЙ-СТАРИЯ запазен отчет на всеки проект. В "
        "работното дърво има непокомитени промени (например "
        "lion_rebar/export/batch_auto_output.py); geometry_candidates.py, "
        "batch_geometry_report.py, importer.py и local_detail_resolver.py НЕ са "
        "променени, затова повторението е валидно, но не е абсолютна гаранция.",
    ]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--projects", default=",".join(DEFAULT_PROJECTS))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--evidence-per-cause", type=int, default=8)
    parser.add_argument("--raw", default="")
    args = parser.parse_args(argv)

    projects = [name for name in args.projects.split(",") if name.strip()]
    results = []
    for project in projects:
        print("• %s" % project)
        results.append(analyse(project))

    if args.raw:
        Path(args.raw).parent.mkdir(parents=True, exist_ok=True)
        with io.open(args.raw, "w", encoding="utf-8") as handle:
            json.dump(results, handle, ensure_ascii=False, indent=1)
        print("  подробните редове: %s" % args.raw)

    per_project = {}
    evidence: list[dict[str, Any]] = []
    for result in results:
        counts = collections.Counter(row["cause"] for row in result["positions"])
        per_project[result["project"]] = {
            "actionable": result["actionable"],
            "actionable_without_geometry": result["actionable_without_geometry"],
            "baseline_reproduced": result["baseline_reproduced"],
            "by_cause": {cause: counts.get(cause, 0) for cause in CAUSES},
        }
        seen: collections.Counter = collections.Counter()
        for row in result["positions"]:
            if seen[row["cause"]] >= args.evidence_per_cause:
                continue
            seen[row["cause"]] += 1
            evidence.append(
                {
                    "project": row["project"],
                    "annotation_handle": row["annotation_handle"],
                    "annotation_text": row["annotation_text"],
                    "annotation_layer": row.get("annotation_layer"),
                    "declared_length_cm": row.get("declared_length_cm"),
                    "cause": row["cause"],
                    "why": row.get("why"),
                    "dxf_handles": row.get("dxf_handles") or [],
                    "block_path": row.get("block_path"),
                    "transform": row.get("transform"),
                    "stage_lost": row.get("stage_lost"),
                    "length_agrees": row.get("length_agrees"),
                    "wide_top": row.get("wide_top"),
                    "dxf_near": row.get("dxf_near"),
                    "chain": row.get("chain"),
                }
            )
    totals = collections.Counter()
    for block in per_project.values():
        for cause, count in block["by_cause"].items():
            totals[cause] += count
    payload = {
        "schema": "lion-rebar-etap4-geometriya/v1",
        "generated_at": __import__("datetime").datetime.now().astimezone().isoformat(timespec="seconds"),
        "method": _method_text(results),
        "totals_by_cause": dict(totals),
        "per_project": per_project,
        "evidence": evidence,
        "fixable": _fixable(results, totals),
        "not_fixable": _not_fixable(results, totals),
        "uncertain": _uncertain(results),
        "projects_meta": [
            {
                key: result[key]
                for key in (
                    "project", "batch_report", "inputs_from_run_folder", "source_dxf",
                    "engine_snapshot", "engine_version",
                    "units_code", "units_name", "insunits_header",
                    "modelspace_proxy_entities", "noise_layers",
                    "unknown_primitive_layers", "actionable",
                    "actionable_without_geometry", "baseline_reproduced",
                )
            }
            for result in results
        ],
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with io.open(out, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=1)
    print("\nЗаписано: %s" % out)
    for project, block in per_project.items():
        print("  %-10s без геометрия %4d от %4d актуални → %s"
              % (project, block["actionable_without_geometry"], block["actionable"],
                 {k: v for k, v in block["by_cause"].items() if v}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
