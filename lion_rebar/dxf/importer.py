from __future__ import annotations

import colorsys
import math
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from lion_rebar.dxf.models import DxfEntityRecord, DxfImportResult, DxfImportSummary
from lion_rebar.dxf.proxy_adapter import extract_acad_proxy_rebar
from lion_rebar.dxf.proxy_graphics import (
    extract_proxy_display_graphics,
    proxy_display_source_key,
)
from lion_rebar.dxf.text_parser import looks_like_rebar_text


class DxfImportError(RuntimeError):
    pass


INSUNITS_NAMES = {
    0: "Unitless / незададени",
    1: "in",
    2: "ft",
    3: "mi",
    4: "mm",
    5: "cm",
    6: "m",
    7: "km",
    8: "microinch",
    9: "mil",
    10: "yd",
    11: "angstrom",
    12: "nm",
    13: "micron",
    14: "dm",
    15: "dam",
    16: "hm",
    17: "Gm",
    18: "AU",
    19: "light year",
    20: "parsec",
}


class DxfImporter:
    def __init__(self) -> None:
        try:
            import ezdxf  # type: ignore
        except ImportError as exc:
            raise DxfImportError(
                "Липсва библиотеката ezdxf. Стартирай INSTALL_LION_REBAR.bat."
            ) from exc
        self.ezdxf = ezdxf

    def import_file(self, path: Path) -> DxfImportResult:
        if not path.exists():
            raise DxfImportError(f"DXF файлът не съществува: {path}")
        try:
            document = self.ezdxf.readfile(path)
        except Exception as exc:
            raise DxfImportError(f"DXF файлът не може да бъде прочетен: {exc}") from exc

        modelspace = document.modelspace()
        modelspace_entities = list(modelspace)
        entities: list[DxfEntityRecord] = []
        counts: Counter[str] = Counter()
        warnings: list[str] = []
        potential_rebar_texts = 0
        text_entities = 0
        derived_entity_count = 0
        reconstructed_rebar_annotations = 0
        reconstructed_attribute_values = 0

        for entity in modelspace_entities:
            entity_type = entity.dxftype()
            counts[entity_type] += 1
            try:
                record = self._record_from_entity(entity)
            except Exception as exc:
                warnings.append(
                    f"Пропуснат обект {getattr(entity.dxf, 'handle', '?')} ({entity_type}): {exc}"
                )
                continue
            entities.append(record)
            if record.text is not None:
                text_entities += 1
                if looks_like_rebar_text(record.text):
                    potential_rebar_texts += 1

            if entity_type == "INSERT":
                разгънати = self._exploded_block_records(entity, record)
                for вложен in разгънати:
                    entities.append(вложен)
                    counts[вложен.entity_type] += 1
                    derived_entity_count += 1
                    if вложен.text is not None:
                        text_entities += 1
                        if looks_like_rebar_text(вложен.text):
                            potential_rebar_texts += 1
                if разгънати:
                    # Съдържанието вече се рисува само́: кръстчето 3x3 на
                    # вмъкването (``drawing_view``) би стояло върху детайла и
                    # би подсказвало, че там няма нищо.
                    record.geometry["render_marker"] = False
                derived = _rebar_annotation_from_insert_record(record)
                if derived is not None:
                    record.geometry["render_marker"] = False
                    record.geometry["derived_rebar_annotation_handle"] = derived.handle
                    entities.append(derived)
                    text_entities += 1
                    potential_rebar_texts += 1
                    derived_entity_count += 1
                    reconstructed_rebar_annotations += 1
                    reconstructed_attribute_values += 3
                    duplicate_tags = derived.raw.get("duplicate_attribute_tags", [])
                    if duplicate_tags:
                        warnings.append(
                            f"Блок {record.handle} ({record.block_name or '?'}) има дублирани "
                            f"армировъчни атрибути: {', '.join(str(value) for value in duplicate_tags)}. "
                            "Запазена е досегашната съвместима логика 'последна стойност', "
                            "а всички сурови стойности са записани за проверка."
                        )

        units_code = document.header.get("$INSUNITS")
        try:
            units_code = int(units_code) if units_code is not None else None
        except (TypeError, ValueError):
            units_code = None
        units_name = INSUNITS_NAMES.get(units_code, f"Unknown ({units_code})")
        if units_code in (None, 0):
            warnings.append("DXF $INSUNITS не е зададен. Мерните единици изискват ръчно потвърждение.")

        proxy_result = extract_acad_proxy_rebar(
            modelspace_entities,
            record_factory=self._record_from_entity,
            units_code=units_code,
        )
        if proxy_result.annotation_records or proxy_result.geometry_records:
            entities.extend(proxy_result.geometry_records)
            entities.extend(proxy_result.annotation_records)
            text_entities += len(proxy_result.annotation_records)
            potential_rebar_texts += len(proxy_result.annotation_records)
            derived_entity_count += len(proxy_result.geometry_records) + len(
                proxy_result.annotation_records
            )
            reconstructed_rebar_annotations += len(proxy_result.annotation_records)
        warnings.extend(proxy_result.warnings)

        # The strict adapter above remains the sole path from proxy data into
        # engineering recognition.  A second, generic pass extracts the
        # available graphics only for the drawing view.  Exact virtual source
        # keys already represented by strict adapter records are suppressed so
        # recognized GA_X/GA_Y content is not drawn twice.
        suppressed_proxy_display_keys: set[str] = set()
        modelspace_proxy_by_handle = {
            str(getattr(value.dxf, "handle", "") or ""): value
            for value in modelspace_entities
            if str(value.dxftype()).upper() == "ACAD_PROXY_ENTITY"
        }
        for record in proxy_result.geometry_records:
            parent_handle = str(record.raw.get("parent_proxy_handle") or "")
            try:
                virtual_index = int(record.raw.get("virtual_index"))
            except (TypeError, ValueError):
                continue
            if not parent_handle:
                continue
            parent_proxy = modelspace_proxy_by_handle.get(parent_handle)
            effective_layer = _proxy_effective_display_layer(
                record.raw.get("proxy_virtual_layer"),
                parent_proxy=parent_proxy,
                fallback_layer=str(record.raw.get("parent_proxy_layer") or "0"),
            )
            record.raw.update(
                _proxy_color_metadata(
                    document,
                    color_index=record.color_index,
                    true_color=record.raw.get("true_color"),
                    parent_proxy=parent_proxy,
                    layer=effective_layer,
                )
            )
            record.raw["display_effective_layer"] = effective_layer
            source_key = proxy_display_source_key(parent_handle, virtual_index)
            record.raw["proxy_source_key"] = source_key
            suppressed_proxy_display_keys.add(source_key)
        for record in proxy_result.annotation_records:
            parent_handle = str(record.raw.get("parent_proxy_handle") or "")
            text_source_keys: list[str] = []
            for value in record.raw.get("fragment_virtual_indices", []):
                try:
                    virtual_index = int(value)
                except (TypeError, ValueError):
                    continue
                if not parent_handle:
                    continue
                source_key = proxy_display_source_key(parent_handle, virtual_index)
                text_source_keys.append(source_key)
                suppressed_proxy_display_keys.add(source_key)
            record.raw["proxy_text_source_keys"] = text_source_keys
            render_fragments = record.geometry.get("render_fragments")
            if isinstance(render_fragments, list):
                source_key_by_index = {
                    int(value): proxy_display_source_key(parent_handle, int(value))
                    for value in record.raw.get("fragment_virtual_indices", [])
                    if parent_handle and str(value).lstrip("-").isdigit()
                }
                for fragment in render_fragments:
                    if not isinstance(fragment, dict):
                        continue
                    try:
                        fragment_index = int(fragment.get("virtual_index"))
                    except (TypeError, ValueError):
                        continue
                    fragment["proxy_source_key"] = source_key_by_index.get(fragment_index)
                    parent_proxy = modelspace_proxy_by_handle.get(parent_handle)
                    effective_layer = _proxy_effective_display_layer(
                        fragment.get("source_layer"),
                        parent_proxy=parent_proxy,
                        fallback_layer=record.layer,
                    )
                    fragment.update(
                        _proxy_color_metadata(
                            document,
                            color_index=fragment.get("color_index"),
                            true_color=fragment.get("true_color"),
                            parent_proxy=parent_proxy,
                            layer=effective_layer,
                        )
                    )
                    fragment["display_effective_layer"] = effective_layer

        proxy_display = extract_proxy_display_graphics(
            document,
            modelspace_entities,
            record_factory=self._record_from_entity,
            suppressed_source_keys=suppressed_proxy_display_keys,
        )
        for diagnostic in proxy_display.diagnostics:
            status = str(diagnostic.get("status") or "")
            if status == "complete":
                continue
            handle = str(diagnostic.get("source_proxy_handle") or "INSERT")
            layer = str(diagnostic.get("layer") or "0")
            reason = str(diagnostic.get("reason") or "неизвестна причина")
            warnings.append(
                f"Proxy display {handle} (слой {layer}) е {status}: {reason}"
            )

        layers = sorted(
            {str(layer.dxf.name) for layer in document.layers}
            | {record.layer for record in proxy_display.records if record.layer}
        )

        summary = DxfImportSummary(
            filename=path.name,
            dxf_version=str(document.dxfversion),
            units_code=units_code,
            units_name=units_name,
            layers=layers,
            entity_counts=dict(sorted(counts.items())),
            total_entities=sum(counts.values()),
            text_entities=text_entities,
            potential_rebar_texts=potential_rebar_texts,
            block_count=len(document.blocks),
            derived_entity_count=derived_entity_count,
            reconstructed_rebar_annotations=reconstructed_rebar_annotations,
            reconstructed_attribute_values=reconstructed_attribute_values,
            proxy_entity_count=proxy_result.proxy_entity_count,
            proxy_text_fragment_count=proxy_result.text_fragment_count,
            proxy_polyline_count=proxy_result.polyline_count,
            proxy_arc_count=proxy_result.arc_count,
            proxy_parsed_annotation_count=proxy_result.parsed_annotation_count,
            proxy_resolved_annotation_count=proxy_result.resolved_annotation_count,
            proxy_unresolved_annotation_count=proxy_result.unresolved_annotation_count,
            proxy_geometry_record_count=len(proxy_result.geometry_records),
            proxy_scale_factor=proxy_result.scale_factor,
            proxy_scale_evidence_count=proxy_result.scale_evidence_count,
            proxy_scale_evidence_by_hypothesis=proxy_result.scale_evidence_by_hypothesis,
            proxy_position_occurrences=proxy_result.position_occurrences,
            proxy_missing_position_keys=proxy_result.missing_position_keys,
            proxy_display_instance_count=proxy_display.proxy_instance_count,
            proxy_display_discovered_primitive_count=(
                proxy_display.discovered_primitive_count
            ),
            proxy_display_rendered_primitive_count=(
                proxy_display.rendered_primitive_count
            ),
            proxy_display_suppressed_duplicate_count=(
                proxy_display.suppressed_duplicate_count
            ),
            proxy_display_unsupported_primitive_count=(
                proxy_display.unsupported_primitive_count
            ),
            proxy_display_complete_count=proxy_display.complete_proxy_count,
            proxy_display_partial_count=proxy_display.partial_proxy_count,
            proxy_display_missing_graphic_count=proxy_display.missing_graphic_count,
            proxy_display_unreadable_count=proxy_display.unreadable_proxy_count,
            proxy_display_type_counts=proxy_display.type_counts,
            # Successful objects are represented by the aggregate counters;
            # retain per-object detail only where an operator can act on it.
            proxy_display_diagnostics=[
                dict(value)
                for value in proxy_display.diagnostics
                if str(value.get("status") or "") != "complete"
            ],
            warnings=warnings,
        )
        return DxfImportResult(
            summary=summary,
            entities=entities,
            display_entities=proxy_display.records,
        )

    #: Докъде се разгъват вложени блокове. Три нива стигат за чертежите,
    #: които сме виждали, и пазят от безкрайна рекурсия при повреден файл.
    MAX_BLOCK_DEPTH = 3

    def _exploded_block_records(
        self,
        entity: Any,
        insert_record: DxfEntityRecord,
        *,
        depth: int = 0,
    ) -> list[DxfEntityRecord]:
        """Съдържанието на един ``INSERT`` като отделни обекти.

        ЗАЩО. Собственик, 22.09.2026: „невидимите геометрии“. Дотук ``INSERT``
        се записваше като ЕДИН запис — точка на вмъкване, завъртане, мащаб и
        атрибути — а съдържанието му не влизаше никъде. ``drawing_view`` го
        рисува като кръстче 3x3, затова на екрана стоеше „+“ там, където в
        AutoCAD има цял детайл.

        Измерено на „Тест-003“ (1 530 обекта, 241 вмъквания): обекти, дошли
        отвътре в блок — **нула**, а зад вмъкванията стоят **310 геометрични
        обекта** (160 LWPOLYLINE, 102 LINE, 44 CIRCLE, 4 SPLINE). Само блокът
        ``skr.gr.`` до надписа ``2x28N8x100`` носи 33 обекта, всичките на
        армировъчни слоеве: ``ARM. gredi`` (8 кръга — сеченията на прътите),
        ``ARM stremena`` (стремето), ``text arm. gredi``.

        ``virtual_entities()`` връща съдържанието с ВЕЧЕ преобразувани
        координати (завъртане, мащаб, вмъкване). Обектите нямат свой handle,
        затова получават производен: ``<handle на вмъкването>::B<индекс>`` —
        същият способ, по който вече се именуват изведените обекти. Така
        връзката с чертежа се пази.

        Отказът е тих по нужда: повреден или неподдържан блок не бива да
        проваля целия импорт — тогава просто няма разгъване, както досега.
        """

        if depth >= self.MAX_BLOCK_DEPTH:
            return []
        try:
            virtual = list(entity.virtual_entities())
        except Exception:  # noqa: BLE001 — неподдържан/повреден блок
            return []

        родител = str(insert_record.handle or "")
        записи: list[DxfEntityRecord] = []
        for индекс, вложен in enumerate(virtual, start=1):
            try:
                запис = self._record_from_entity(вложен)
            except Exception:  # noqa: BLE001
                continue
            запис.handle = f"{родител}::B{индекс}"
            # Слой „0“ вътре в блок наследява слоя на вмъкването — така го
            # рисува и AutoCAD.
            if str(запис.layer or "").strip() in ("", "0"):
                запис.layer = insert_record.layer
            запис.raw["source_insert_handle"] = родител
            запис.raw["source_block_name"] = insert_record.block_name
            записи.append(запис)
            if запис.entity_type == "INSERT":
                записи.extend(
                    self._exploded_block_records(вложен, запис, depth=depth + 1)
                )
        return записи

    def _record_from_entity(self, entity: Any) -> DxfEntityRecord:
        entity_type = entity.dxftype()
        handle = str(getattr(entity.dxf, "handle", ""))
        layer = str(getattr(entity.dxf, "layer", "0"))
        color = _safe_int(getattr(entity.dxf, "color", None))
        linetype = str(getattr(entity.dxf, "linetype", "")) or None
        geometry: dict[str, Any] = {}
        text: str | None = None
        block_name: str | None = None
        raw: dict[str, Any] = {}
        true_color = _safe_int(getattr(entity.dxf, "true_color", None))
        if true_color is not None:
            raw["true_color"] = true_color

        if entity_type == "LINE":
            geometry = {
                "start": _point(entity.dxf.start),
                "end": _point(entity.dxf.end),
            }
        elif entity_type == "LWPOLYLINE":
            points = []
            bulges = []
            for item in entity.get_points("xyb"):
                x, y, bulge = item
                parsed_bulge = float(bulge or 0.0)
                # Keep the historical [x, y, bulge] layout for project
                # compatibility and an explicit parallel list for unambiguous
                # curve-aware consumers.
                points.append([float(x), float(y), parsed_bulge])
                bulges.append(parsed_bulge)
            geometry = {
                "points": points,
                "bulges": bulges,
                "closed": bool(entity.closed),
                "curve_segment_count": sum(abs(value) > 1e-12 for value in bulges),
            }
        elif entity_type == "POLYLINE":
            points = [_point(vertex.dxf.location) for vertex in entity.vertices]
            bulges = [
                float(getattr(vertex.dxf, "bulge", 0.0) or 0.0)
                for vertex in entity.vertices
            ]
            geometry = {
                "points": points,
                "closed": bool(entity.is_closed),
                "bulges": bulges,
                "curve_segment_count": sum(abs(value) > 1e-12 for value in bulges),
            }
        elif entity_type == "CIRCLE":
            geometry = {"center": _point(entity.dxf.center), "radius": float(entity.dxf.radius)}
        elif entity_type == "ARC":
            geometry = {
                "center": _point(entity.dxf.center),
                "radius": float(entity.dxf.radius),
                "start_angle": float(entity.dxf.start_angle),
                "end_angle": float(entity.dxf.end_angle),
            }
        elif entity_type == "ELLIPSE":
            geometry = {
                "center": _point(entity.dxf.center),
                "major_axis": _point(entity.dxf.major_axis),
                "ratio": float(entity.dxf.ratio),
                "start_param": float(getattr(entity.dxf, "start_param", 0.0) or 0.0),
                "end_param": float(
                    getattr(entity.dxf, "end_param", math.tau) or math.tau
                ),
            }
        elif entity_type == "SPLINE":
            try:
                render_points = [
                    _point(value) for value in entity.flattening(0.01, segments=8)
                ]
            except Exception:  # malformed spline stays auditable, not fatal
                render_points = []
            geometry = {
                "degree": int(getattr(entity.dxf, "degree", 3) or 3),
                "control_points": [_point(value) for value in entity.control_points],
                "fit_points": [_point(value) for value in entity.fit_points],
                "knots": [float(value) for value in entity.knots],
                "weights": [float(value) for value in entity.weights],
                "closed": bool(getattr(entity, "closed", False)),
                # Never draw the control polygon as if it were the spline.
                # This is a display approximation only; the exact NURBS data
                # above remains available for audit.
                "render_points": render_points,
            }
        elif entity_type == "TEXT":
            text = str(entity.dxf.text)
            insert = _point(entity.dxf.insert)
            height = float(getattr(entity.dxf, "height", 1.0) or 1.0)
            rotation = float(getattr(entity.dxf, "rotation", 0.0) or 0.0)
            geometry = {
                "insert": insert,
                "height": height,
                "rotation": rotation,
                "width_factor": float(getattr(entity.dxf, "width", 1.0) or 1.0),
                "oblique": float(getattr(entity.dxf, "oblique", 0.0) or 0.0),
                "halign": int(getattr(entity.dxf, "halign", 0) or 0),
                "valign": int(getattr(entity.dxf, "valign", 0) or 0),
                "align_point": _optional_point(
                    getattr(entity.dxf, "align_point", None)
                ),
                **_text_visual_geometry(text, insert, height, rotation),
            }
        elif entity_type == "MTEXT":
            try:
                text = entity.plain_text()
            except Exception:
                text = str(entity.text)
            insert = _point(entity.dxf.insert)
            height = float(getattr(entity.dxf, "char_height", 1.0) or 1.0)
            rotation = float(getattr(entity, "get_rotation", lambda: 0.0)() or 0.0)
            width_hint = float(getattr(entity.dxf, "width", 0.0) or 0.0)
            geometry = {
                "insert": insert,
                "char_height": height,
                "rotation": rotation,
                "width": width_hint,
                "attachment_point": int(
                    getattr(entity.dxf, "attachment_point", 1) or 1
                ),
                "text_direction": _optional_point(
                    getattr(entity.dxf, "text_direction", None)
                ),
                **_text_visual_geometry(text, insert, height, rotation, width_hint=width_hint),
            }
        elif entity_type == "POINT":
            geometry = {"location": _point(entity.dxf.location)}
        elif entity_type == "INSERT":
            block_name = str(entity.dxf.name)
            geometry = {
                "insert": _point(entity.dxf.insert),
                "rotation": float(getattr(entity.dxf, "rotation", 0.0) or 0.0),
                "xscale": float(getattr(entity.dxf, "xscale", 1.0) or 1.0),
                "yscale": float(getattr(entity.dxf, "yscale", 1.0) or 1.0),
            }
            attributes = []
            for attrib in getattr(entity, "attribs", []):
                attributes.append(
                    {
                        "handle": str(getattr(attrib.dxf, "handle", "")),
                        "tag": str(getattr(attrib.dxf, "tag", "")),
                        "text": str(getattr(attrib.dxf, "text", "")),
                        "insert": _point(getattr(attrib.dxf, "insert", (0, 0, 0))),
                        "height": float(getattr(attrib.dxf, "height", 1.0) or 1.0),
                        "rotation": float(getattr(attrib.dxf, "rotation", 0.0) or 0.0),
                        "layer": str(getattr(attrib.dxf, "layer", "0") or "0"),
                        **_text_visual_geometry(
                            str(getattr(attrib.dxf, "text", "")),
                            _point(getattr(attrib.dxf, "insert", (0, 0, 0))),
                            float(getattr(attrib.dxf, "height", 1.0) or 1.0),
                            float(getattr(attrib.dxf, "rotation", 0.0) or 0.0),
                        ),
                    }
                )
            geometry["attributes"] = attributes
        elif entity_type == "DIMENSION":
            # Preserve both contracts: measured geometry for recognition and
            # the explicit/resolved AutoCAD display for the operator.
            measurement = None
            for attribute in ("actual_measurement", "measurement"):
                try:
                    measurement_raw = getattr(entity.dxf, attribute, None)
                except Exception:  # noqa: BLE001
                    measurement_raw = None
                if measurement_raw is None:
                    continue
                try:
                    value = float(measurement_raw)
                except (TypeError, ValueError):
                    continue
                if value > 0:
                    measurement = value
                    break
            text = str(getattr(entity.dxf, "text", "") or "") or None
            primitives = _dimension_render_primitives(entity)
            display_text = None
            if text and text.strip() not in {"<>", " "}:
                display_text = text.strip()
            if not display_text:
                display_text = next(
                    (
                        str(item.get("text") or "").strip()
                        for item in primitives
                        if item.get("kind") == "TEXT" and str(item.get("text") or "").strip()
                    ),
                    None,
                )
            geometry = {
                "defpoint": _point(getattr(entity.dxf, "defpoint", (0, 0, 0))),
                "defpoint2": _point(getattr(entity.dxf, "defpoint2", (0, 0, 0))),
                "defpoint3": _point(getattr(entity.dxf, "defpoint3", (0, 0, 0))),
                "text_midpoint": _point(getattr(entity.dxf, "text_midpoint", (0, 0, 0))),
                "measurement": measurement,
                "display_text": display_text,
                "dimension_style": str(getattr(entity.dxf, "dimstyle", "") or ""),
                "render_primitives": primitives,
            }

        elif entity_type == "LEADER":
            geometry = {"vertices": [_point(vertex) for vertex in entity.vertices]}
        elif entity_type == "HATCH":
            geometry = {"boundary_count": len(entity.paths)}
        else:
            geometry = {"unsupported_for_render": True}

        return DxfEntityRecord(
            handle=handle,
            entity_type=entity_type,
            layer=layer,
            geometry=geometry,
            text=text,
            block_name=block_name,
            color_index=color,
            linetype=linetype,
            raw=raw,
        )


def _dimension_render_primitives(entity: Any) -> list[dict[str, Any]]:
    """Flatten ezdxf's resolved dimension block into portable primitives."""

    try:
        virtual_entities = list(entity.virtual_entities())
    except Exception:  # unsupported/proxy dimension: viewer has a safe fallback
        return []
    result: list[dict[str, Any]] = []
    for primitive in virtual_entities:
        kind = str(primitive.dxftype() or "").upper()
        if kind == "LINE":
            result.append(
                {
                    "kind": "LINE",
                    "start": _point(primitive.dxf.start),
                    "end": _point(primitive.dxf.end),
                }
            )
        elif kind == "ARC":
            result.append(
                {
                    "kind": "ARC",
                    "center": _point(primitive.dxf.center),
                    "radius": float(primitive.dxf.radius),
                    "start_angle": float(primitive.dxf.start_angle),
                    "end_angle": float(primitive.dxf.end_angle),
                }
            )
        elif kind in {"TEXT", "MTEXT"}:
            try:
                raw_text = primitive.plain_text() if kind == "MTEXT" else str(primitive.dxf.text)
            except Exception:
                raw_text = str(getattr(primitive.dxf, "text", "") or "")
            # ezdxf can preserve a simple MTEXT alignment prefix in generated
            # dimension blocks.  It is formatting, not part of the number.
            if raw_text.startswith("\\A") and ";" in raw_text:
                raw_text = raw_text.split(";", 1)[1]
            insert = _point(getattr(primitive.dxf, "insert", (0, 0, 0)))
            height = float(
                getattr(primitive.dxf, "char_height", None)
                or getattr(primitive.dxf, "height", None)
                or 1.0
            )
            rotation_getter = getattr(primitive, "get_rotation", None)
            rotation = float(
                rotation_getter() if callable(rotation_getter)
                else getattr(primitive.dxf, "rotation", 0.0) or 0.0
            )
            result.append(
                {
                    "kind": "TEXT",
                    "text": raw_text,
                    "insert": insert,
                    "height": height,
                    "rotation": rotation,
                }
            )
        elif kind == "INSERT":
            result.append(
                {
                    "kind": "ARROW",
                    "name": str(getattr(primitive.dxf, "name", "") or ""),
                    "insert": _point(getattr(primitive.dxf, "insert", (0, 0, 0))),
                    "rotation": float(getattr(primitive.dxf, "rotation", 0.0) or 0.0),
                    "scale": float(getattr(primitive.dxf, "xscale", 1.0) or 1.0),
                }
            )
    return result


def _text_visual_geometry(
    text: str,
    insert: list[float],
    height: float,
    rotation: float,
    *,
    width_hint: float = 0.0,
) -> dict[str, Any]:
    """Return a conservative visible extent for TEXT/MTEXT/ATTRIB.

    DXF insertion points are often outside the visible centre of a label. The
    estimated box is deliberately generous and is used only for proximity
    ranking; it never changes the source drawing.
    """

    lines = (text or "").replace("\r", "").split("\n") or [""]
    char_height = max(float(height or 1.0), 1e-6)
    estimated_width = max((len(line) for line in lines), default=1) * char_height * 0.62
    width = max(float(width_hint or 0.0), estimated_width, char_height)
    box_height = max(len(lines) * char_height * 1.20, char_height)
    angle = math.radians(float(rotation or 0.0))
    ux, uy = math.cos(angle), math.sin(angle)
    vx, vy = -uy, ux
    x, y = float(insert[0]), float(insert[1])
    corners = [
        [x, y, 0.0],
        [x + width * ux, y + width * uy, 0.0],
        [x + box_height * vx, y + box_height * vy, 0.0],
        [x + width * ux + box_height * vx, y + width * uy + box_height * vy, 0.0],
    ]
    center = [
        x + 0.5 * width * ux + 0.5 * box_height * vx,
        y + 0.5 * width * uy + 0.5 * box_height * vy,
        0.0,
    ]
    return {
        "visual_anchor": center,
        "visual_bbox": corners,
        "estimated_text_width": width,
        "estimated_text_height": box_height,
    }


def _point(value: Any) -> list[float]:
    try:
        return [float(value[0]), float(value[1]), float(value[2] if len(value) > 2 else 0.0)]
    except (TypeError, IndexError):
        return [float(value.x), float(value.y), float(getattr(value, "z", 0.0))]


def _optional_point(value: Any) -> list[float] | None:
    if value is None:
        return None
    try:
        return _point(value)
    except (AttributeError, TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


_PROXY_LIGHT_BACKGROUND = (247, 248, 250)
_PROXY_DEFAULT_FOREGROUND = (32, 41, 56)


def _proxy_effective_display_layer(
    virtual_layer: Any,
    *,
    parent_proxy: Any | None,
    fallback_layer: str,
) -> str:
    parent_layer = str(
        _proxy_dxf_value(parent_proxy, "layer", fallback_layer)
        or fallback_layer
        or "0"
    )
    source_layer = str(virtual_layer or "0")
    return parent_layer if source_layer == "0" else source_layer


def _proxy_color_metadata(
    document: Any,
    *,
    color_index: Any,
    true_color: Any,
    parent_proxy: Any | None,
    layer: str,
) -> dict[str, Any]:
    """Resolve a strict proxy primitive before its generic twin is suppressed."""

    from ezdxf import colors as ezcolors  # type: ignore

    parsed_true_color = _safe_int(true_color)
    parsed_color_index = _safe_int(color_index)
    parent_rgb = _proxy_parent_source_rgb(
        document,
        parent_proxy,
        fallback_layer=layer,
        ezcolors=ezcolors,
    )
    if parsed_true_color is not None:
        source_rgb = _proxy_clamp_rgb(ezcolors.int2rgb(parsed_true_color))
        mode = "TRUECOLOR"
    elif parsed_color_index in (None, 256):
        source_rgb = _proxy_layer_rgb(document, layer, ezcolors=ezcolors)
        mode = "BYLAYER"
    elif parsed_color_index == 0:
        source_rgb = parent_rgb
        mode = "BYBLOCK"
    else:
        source_rgb = _proxy_aci_rgb(parsed_color_index, ezcolors=ezcolors)
        mode = "ACI"
    display_rgb = _proxy_contrast_rgb(source_rgb)
    return {
        "display_rgb": _proxy_rgb_hex(display_rgb),
        "source_rgb": _proxy_rgb_hex(source_rgb),
        "proxy_source_rgb": _proxy_rgb_hex(parent_rgb),
        "color_resolution": mode,
        "color_adjusted_for_contrast": display_rgb != source_rgb,
    }


def _proxy_fragment_rgb_hex(
    document: Any,
    fragment: dict[str, Any],
    *,
    parent_proxy: Any | None,
    layer: str,
) -> str:
    """Backward-compatible value helper for focused importer tests/tools."""

    return str(
        _proxy_color_metadata(
            document,
            color_index=fragment.get("color_index"),
            true_color=fragment.get("true_color"),
            parent_proxy=parent_proxy,
            layer=layer,
        )["display_rgb"]
    )


def _proxy_parent_source_rgb(
    document: Any,
    parent_proxy: Any | None,
    *,
    fallback_layer: str,
    ezcolors: Any,
) -> tuple[int, int, int]:
    if parent_proxy is None:
        return _PROXY_DEFAULT_FOREGROUND
    true_color = _safe_int(_proxy_dxf_value(parent_proxy, "true_color", None))
    if true_color is not None:
        return _proxy_clamp_rgb(ezcolors.int2rgb(true_color))
    color_index = _safe_int(_proxy_dxf_value(parent_proxy, "color", 256))
    if color_index in (None, 256):
        layer = str(
            _proxy_dxf_value(parent_proxy, "layer", fallback_layer)
            or fallback_layer
            or "0"
        )
        return _proxy_layer_rgb(document, layer, ezcolors=ezcolors)
    if color_index == 0:
        # A modelspace proxy has no containing INSERT from which BYBLOCK could
        # inherit. Match the generic extractor's visible dark fallback.
        return _PROXY_DEFAULT_FOREGROUND
    return _proxy_aci_rgb(color_index, ezcolors=ezcolors)


def _proxy_layer_rgb(
    document: Any,
    layer: str,
    *,
    ezcolors: Any,
) -> tuple[int, int, int]:
    try:
        layer_record = document.layers.get(str(layer or "0"))
        true_rgb = layer_record.rgb
        if true_rgb is not None:
            return _proxy_clamp_rgb(true_rgb)
        return _proxy_aci_rgb(int(layer_record.color), ezcolors=ezcolors)
    except Exception:  # noqa: BLE001
        return _PROXY_DEFAULT_FOREGROUND


def _proxy_aci_rgb(value: int, *, ezcolors: Any) -> tuple[int, int, int]:
    if abs(int(value)) == 7:
        return 0, 0, 0
    try:
        return _proxy_clamp_rgb(ezcolors.aci2rgb(abs(int(value))))
    except Exception:  # noqa: BLE001
        return _PROXY_DEFAULT_FOREGROUND


def _proxy_contrast_rgb(rgb: tuple[int, int, int]) -> tuple[int, int, int]:
    if _proxy_contrast_ratio(rgb, _PROXY_LIGHT_BACKGROUND) >= 3.0:
        return rgb
    red, green, blue = (value / 255.0 for value in rgb)
    hue, lightness, saturation = colorsys.rgb_to_hls(red, green, blue)
    for target_lightness in (0.42, 0.36, 0.30, 0.24, 0.18, 0.12):
        candidate = _proxy_clamp_rgb(
            round(value * 255.0)
            for value in colorsys.hls_to_rgb(
                hue,
                min(lightness, target_lightness),
                saturation,
            )
        )
        if _proxy_contrast_ratio(candidate, _PROXY_LIGHT_BACKGROUND) >= 3.0:
            return candidate
    return _PROXY_DEFAULT_FOREGROUND


def _proxy_contrast_ratio(
    left: tuple[int, int, int],
    right: tuple[int, int, int],
) -> float:
    def luminance(rgb: tuple[int, int, int]) -> float:
        channels = []
        for value in rgb:
            channel = value / 255.0
            channels.append(
                channel / 12.92
                if channel <= 0.04045
                else ((channel + 0.055) / 1.055) ** 2.4
            )
        return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]

    high, low = sorted((luminance(left), luminance(right)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def _proxy_clamp_rgb(values: Iterable[Any]) -> tuple[int, int, int]:
    result = tuple(max(0, min(255, int(value))) for value in values)
    return (
        result
        if len(result) == 3
        else _PROXY_DEFAULT_FOREGROUND
    )  # type: ignore[return-value]


def _proxy_rgb_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def _proxy_dxf_value(entity: Any | None, name: str, default: Any) -> Any:
    namespace = getattr(entity, "dxf", None)
    if namespace is None:
        return default
    try:
        return namespace.get(name, default)
    except Exception:  # noqa: BLE001
        try:
            return getattr(namespace, name)
        except Exception:  # noqa: BLE001
            return default

_REQUIRED_REBAR_ATTRIBUTE_TAGS = ("BJ", "FI", "L")

#: Множители в арматурните блокове на вертикалните елементи.
#: Семантика (потвърдена от Product Owner, 2026-08-25):
#:   BX -- брой елементи (K1, K2, K3 ...)
#:   BP -- множител на броя еднакви елементи (стремена, есове, пръти)
#:   BJ -- брой елементи от даден вид
#: Производственият брой е ``BX * BP * BJ``.
#:
#: До тази поправка се четеше САМО BJ. Реален случай от чертеж на вертикали:
#: блок с BX=39, BP=2, BJ=28 влизаше в описа с 28 бр. вместо 2184 --
#: 78 пъти по-малко, без предупреждение. Това е тихо грешно количество.
_REBAR_MULTIPLIER_TAGS = ("BX", "BP")


def _multiplier_from_tags(by_tag: dict) -> tuple[int, list[str], list[str]]:
    """Връща (множител, използвани тагове, проблеми).

    FAIL-CLOSED (независим одит на rc21, P0-4): присъстващ, но нечетим,
    нулев или дробен множител е ГРЕШКА, не липсваща стойност. По-ранната
    версия го игнорираше мълчаливо и произвеждаше занижен брой — точно
    класът дефект, който вече веднъж даде 78 пъти по-малки количества.

    Само НАПЪЛНО ЛИПСВАЩ таг означава множител 1.
    """

    multiplier = 1
    used: list[str] = []
    problems: list[str] = []
    for tag in _REBAR_MULTIPLIER_TAGS:
        entry = by_tag.get(tag)
        if not isinstance(entry, dict):
            continue  # липсващ таг -> 1, това е допустимо
        raw = str(entry.get("text", "")).strip()
        if not raw:
            problems.append(f"{tag}:ПРАЗЕН")
            continue
        try:
            number = float(raw.replace(",", "."))
        except (TypeError, ValueError):
            problems.append(f"{tag}:НЕЧЕТИМ:{raw}")
            continue
        if number <= 0:
            problems.append(f"{tag}:НЕПОЛОЖИТЕЛЕН:{raw}")
            continue
        if abs(number - round(number)) > 1e-9:
            problems.append(f"{tag}:ДРОБЕН:{raw}")
            continue
        value = int(round(number))
        if value > 1:
            multiplier *= value
            used.append(tag)
    return multiplier, used, problems


def _rebar_annotation_from_insert_record(
    record: DxfEntityRecord,
) -> DxfEntityRecord | None:
    """Create a deterministic complete rebar label from attached block ATTRIBs.

    The source data remains attached to the INSERT record. This function creates
    only a selectable/renderable derived record. It does not alter the DXF and
    it does not infer position, spacing, or geometry.
    """

    if record.entity_type != "INSERT":
        return None
    raw_attributes = record.geometry.get("attributes")
    if not isinstance(raw_attributes, list):
        return None

    by_tag: dict[str, dict[str, Any]] = {}
    values_by_tag: dict[str, list[dict[str, Any]]] = {}
    for value in raw_attributes:
        if not isinstance(value, dict):
            continue
        tag = str(value.get("tag", "")).strip().upper()
        if tag:
            values_by_tag.setdefault(tag, []).append(value)
            # Compatibility rule: preserve the current behaviour exactly.  The
            # last ATTRIB with a given tag wins, but duplicates are no longer
            # silent and their full provenance is retained below.
            by_tag[tag] = value

    if not all(tag in by_tag for tag in _REQUIRED_REBAR_ATTRIBUTE_TAGS):
        return None

    count = str(by_tag["BJ"].get("text", "")).strip()
    diameter = str(by_tag["FI"].get("text", "")).strip()
    length = str(by_tag["L"].get("text", "")).strip()
    if not count or not diameter or not length:
        return None

    # Производственият брой включва множителите BX и BP, ако ги има.
    multiplier, multiplier_tags, multiplier_problems = _multiplier_from_tags(by_tag)
    production_count = count
    if multiplier > 1:
        try:
            production_count = str(int(float(count.replace(",", "."))) * multiplier)
        except (TypeError, ValueError):
            # Нечетим брой -> НЕ се гадае. Позицията остава с оригиналния
            # текст и ще бъде уловена от съществуващите проверки.
            production_count = count
            multiplier_tags = []
            multiplier_problems = [*multiplier_problems, "BJ:НЕЧЕТИМ"]

    annotation = f"{production_count}N{diameter}x{length}"
    if not looks_like_rebar_text(annotation):
        return None

    first = by_tag["BJ"]
    insert = first.get("insert") or record.geometry.get("insert")
    if not isinstance(insert, (list, tuple)) or len(insert) < 2:
        return None

    heights = []
    for tag in _REQUIRED_REBAR_ATTRIBUTE_TAGS:
        try:
            heights.append(float(by_tag[tag].get("height", 1.0) or 1.0))
        except (TypeError, ValueError):
            pass
    height = max(heights, default=1.0)
    try:
        rotation = float(first.get("rotation", record.geometry.get("rotation", 0.0)) or 0.0)
    except (TypeError, ValueError):
        rotation = 0.0

    attribute_audit = {
        tag: {
            "handle": str(by_tag[tag].get("handle", "")),
            "value": str(by_tag[tag].get("text", "")),
            "insert": by_tag[tag].get("insert"),
        }
        for tag in _REQUIRED_REBAR_ATTRIBUTE_TAGS
    }
    for tag in _REBAR_MULTIPLIER_TAGS:
        if tag in by_tag:
            attribute_audit[tag] = {
                "handle": str(by_tag[tag].get("handle", "")),
                "value": str(by_tag[tag].get("text", "")),
                "insert": by_tag[tag].get("insert"),
            }
    duplicate_tags = sorted(
        tag for tag, values in values_by_tag.items() if len(values) > 1
    )
    # КОНФЛИКТНИ дублирани множители са production blocker.
    # Дублиран таг с ЕДНА И СЪЩА стойност е безобиден (проектантът е
    # копирал атрибут). Дублиран таг с РАЗЛИЧНИ стойности означава, че
    # броят е двусмислен -- „last wins“ би избрал произволно и би дал
    # тихо грешно количество.
    conflicting_multiplier_tags = sorted(
        tag for tag in _REBAR_MULTIPLIER_TAGS + _REQUIRED_REBAR_ATTRIBUTE_TAGS
        if len({
            str(value.get("text", "")).strip()
            for value in values_by_tag.get(tag, [])
        }) > 1
    )
    raw_attribute_values = {
        tag: [
            {
                "handle": str(value.get("handle", "")),
                "value": str(value.get("text", "")),
                "insert": value.get("insert"),
                "layer": str(value.get("layer", "")),
            }
            for value in values
        ]
        for tag, values in sorted(values_by_tag.items())
    }
    attribute_inserts = [
        list(by_tag[tag].get("insert"))
        for tag in _REQUIRED_REBAR_ATTRIBUTE_TAGS
        if isinstance(by_tag[tag].get("insert"), (list, tuple))
        and len(by_tag[tag].get("insert")) >= 2
    ]
    visual_points = []
    for tag in _REQUIRED_REBAR_ATTRIBUTE_TAGS:
        value = by_tag[tag]
        raw_bbox = value.get("visual_bbox")
        if isinstance(raw_bbox, list):
            visual_points.extend(raw_bbox)
    if not visual_points:
        visual_points = attribute_inserts
    if visual_points:
        visual_anchor = [
            sum(float(point[0]) for point in visual_points) / len(visual_points),
            sum(float(point[1]) for point in visual_points) / len(visual_points),
            0.0,
        ]
    else:
        visual_anchor = [float(insert[0]), float(insert[1]), 0.0]

    return DxfEntityRecord(
        handle=f"{record.handle}::BJ-FI-L",
        entity_type="BLOCK_ATTR_TEXT",
        layer=record.layer,
        geometry={
            "insert": [float(insert[0]), float(insert[1]), float(insert[2]) if len(insert) > 2 else 0.0],
            "height": height,
            "rotation": rotation,
            "parent_insert_handle": record.handle,
            "attribute_tags": list(_REQUIRED_REBAR_ATTRIBUTE_TAGS) + multiplier_tags,
            "attribute_inserts": attribute_inserts,
            # Проследимост на производствения брой: кой множител е приложен
            # и какъв е бил оригиналният BJ преди умножението.
            "count_multiplier": multiplier,
            "count_multiplier_tags": list(multiplier_tags),
            "count_before_multiplier": count,
            # Проблемните множители пътуват до отчета и портата; празен
            # списък означава чист брой.
            "count_multiplier_problems": list(multiplier_problems) + [
                f"{tag}:КОНФЛИКТНО ДУБЛИРАНЕ"
                for tag in conflicting_multiplier_tags
            ],
            "conflicting_attribute_tags": list(conflicting_multiplier_tags),
            "visual_anchor": visual_anchor,
            "visual_bbox": visual_points,
            "derived": True,
        },
        text=annotation,
        block_name=record.block_name,
        color_index=record.color_index,
        linetype=record.linetype,
        raw={
            "source": "INSERT_ATTRIBUTES",
            "parent_insert_handle": record.handle,
            "block_name": record.block_name,
            "attributes": attribute_audit,
            "all_attribute_values": raw_attribute_values,
            "attribute_tag_counts": {
                tag: len(values) for tag, values in sorted(values_by_tag.items())
            },
            "duplicate_attribute_tags": duplicate_tags,
            "attribute_selection_policy": "LAST_VALUE_COMPATIBILITY",
            "position_inferred": False,
            "spacing_inferred": False,
            "geometry_linked": False,
        },
    )
