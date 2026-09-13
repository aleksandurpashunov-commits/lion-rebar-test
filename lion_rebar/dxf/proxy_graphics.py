from __future__ import annotations

import colorsys
import math
from collections import Counter
from collections.abc import Iterable, Sequence, Set
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from ezdxf import colors as ezcolors
from ezdxf import transform as eztransform
from ezdxf.math import Matrix44, Vec3
from ezdxf.path import Command, make_path

from lion_rebar.dxf.models import DxfEntityRecord


PROXY_DISPLAY_SOURCE = "ACAD_PROXY_GRAPHIC_DISPLAY"
MODELSPACE_INSTANCE_KEY = "MS"
MAX_INSERT_DEPTH = 64
SUPPORTED_PROXY_PRIMITIVES = frozenset(
    {
        "LINE",
        "LWPOLYLINE",
        "POLYLINE",
        "ARC",
        "CIRCLE",
        "TEXT",
        "MTEXT",
        "ELLIPSE",
        "SPLINE",
    }
)

_LIGHT_BACKGROUND = (247, 248, 250)
_DEFAULT_FOREGROUND = (32, 41, 56)


RecordFactory = Callable[[Any], DxfEntityRecord]


@dataclass
class ProxyDisplayResult:
    """Portable, display-only proxy graphics and per-instance diagnostics.

    ``records`` is deliberately separate from the importer's engineering entity
    collection. Callers may merge it into a scene, but must not pass it to the
    rebar candidate, quantity, weight, or export pipelines.
    """

    records: list[DxfEntityRecord] = field(default_factory=list)
    diagnostics: list[dict[str, Any]] = field(default_factory=list)
    proxy_instance_count: int = 0
    discovered_primitive_count: int = 0
    rendered_primitive_count: int = 0
    suppressed_duplicate_count: int = 0
    unsupported_primitive_count: int = 0
    complete_proxy_count: int = 0
    partial_proxy_count: int = 0
    missing_graphic_count: int = 0
    unreadable_proxy_count: int = 0
    type_counts: dict[str, int] = field(default_factory=dict)
    output_type_counts: dict[str, int] = field(default_factory=dict)
    unsupported_type_counts: dict[str, int] = field(default_factory=dict)
    status_counts: dict[str, int] = field(default_factory=dict)
    traversal_diagnostic_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def proxy_display_source_key(
    source_proxy_handle: str,
    virtual_index: int,
    *,
    instance_key: str = MODELSPACE_INSTANCE_KEY,
) -> str:
    """Return the stable key shared by display and recognized proxy records."""

    proxy_token = str(source_proxy_handle or "?").strip() or "?"
    location = str(instance_key or MODELSPACE_INSTANCE_KEY).strip()
    return f"{location}|P:{proxy_token}|V:{int(virtual_index)}"


def extract_proxy_display_graphics(
    document: Any,
    modelspace_entities: Sequence[Any],
    *,
    record_factory: RecordFactory,
    suppressed_source_keys: Set[Any] = frozenset(),
) -> ProxyDisplayResult:
    """Extract all supported ACAD proxy graphics without altering the DXF.

    Recognition rules, layer names, text parsing, scale proof, and annotation
    association are intentionally absent. Nested block references are walked
    explicitly because ezdxf cannot transform ``ACAD_PROXY_ENTITY`` itself.
    Instead, the proxy graphic is decoded in its definition coordinate system
    and the resulting virtual primitives are copied and transformed.
    """

    extractor = _ProxyDisplayExtractor(
        document=document,
        record_factory=record_factory,
        suppressed_source_keys=suppressed_source_keys,
    )
    identity = Matrix44()
    for modelspace_index, entity in enumerate(modelspace_entities):
        kind = _safe_dxftype(entity)
        locator = f"MS:{modelspace_index}"
        if kind == "ACAD_PROXY_ENTITY":
            extractor.extract_proxy(
                entity,
                transform=identity,
                insertion_path=(),
                inherited_layer="0",
                byblock_rgb=_DEFAULT_FOREGROUND,
                source_locator=locator,
            )
        elif kind == "INSERT":
            extractor.walk_insert(
                entity,
                parent_transform=identity,
                inherited_layer="0",
                inherited_byblock_rgb=_DEFAULT_FOREGROUND,
                insertion_path=(),
                block_stack=(),
                depth=0,
                source_locator=locator,
            )
    extractor.sort_counters()
    return extractor.result


class _ProxyDisplayExtractor:
    def __init__(
        self,
        *,
        document: Any,
        record_factory: RecordFactory,
        suppressed_source_keys: Set[Any],
    ) -> None:
        self.document = document
        self.record_factory = record_factory
        self.suppressed_source_keys = suppressed_source_keys
        self.result = ProxyDisplayResult()

    def sort_counters(self) -> None:
        for name in (
            "type_counts",
            "output_type_counts",
            "unsupported_type_counts",
            "status_counts",
        ):
            values = getattr(self.result, name)
            setattr(self.result, name, dict(sorted(values.items())))

    def walk_insert(
        self,
        source_insert: Any,
        *,
        parent_transform: Matrix44,
        inherited_layer: str,
        inherited_byblock_rgb: tuple[int, int, int],
        insertion_path: tuple[dict[str, Any], ...],
        block_stack: tuple[str, ...],
        depth: int,
        source_locator: str,
    ) -> None:
        try:
            occurrences = list(_insert_occurrences(source_insert))
        except Exception as exc:  # noqa: BLE001 - one bad INSERT is isolated
            self._traversal_diagnostic(
                insertion_path,
                source_insert,
                status="unreadable",
                reason=f"MINSERT expansion failed: {type(exc).__name__}: {exc}",
                reason_code="MINSERT_EXPANSION_FAILED",
            )
            return

        source_handle = _source_handle(source_insert, source_locator)
        block_name = str(_dxf_get(source_insert, "name", "") or "")
        for occurrence_index, occurrence in occurrences:
            source_insert_layer = str(
                _dxf_get(occurrence, "layer", "0") or "0"
            )
            insert_layer = _effective_layer(
                source_insert_layer,
                inherited_layer,
                inside_block=bool(insertion_path),
            )
            segment = {
                "insert_handle": source_handle,
                "block_name": block_name,
                "occurrence_index": occurrence_index,
                "source_insert_layer": source_insert_layer,
                "insert_layer": insert_layer,
            }
            path = insertion_path + (segment,)
            if depth >= MAX_INSERT_DEPTH:
                self._traversal_diagnostic(
                    path,
                    source_insert,
                    status="unreadable",
                    reason=f"maximum INSERT depth {MAX_INSERT_DEPTH} reached",
                    reason_code="MAX_INSERT_DEPTH",
                )
                continue

            block_key = block_name.casefold()
            if not block_key:
                self._traversal_diagnostic(
                    path,
                    source_insert,
                    status="unreadable",
                    reason="INSERT has no block name",
                    reason_code="MISSING_BLOCK_NAME",
                )
                continue
            if block_key in block_stack:
                cycle = " -> ".join((*block_stack, block_key))
                self._traversal_diagnostic(
                    path,
                    source_insert,
                    status="unreadable",
                    reason=f"cyclic block reference: {cycle}",
                    reason_code="CYCLIC_BLOCK_REFERENCE",
                )
                continue

            try:
                block = occurrence.block()
                if block is None:
                    block = self.document.blocks.get(block_name)
            except Exception as exc:  # noqa: BLE001
                self._traversal_diagnostic(
                    path,
                    source_insert,
                    status="unreadable",
                    reason=f"block definition is unavailable: {type(exc).__name__}: {exc}",
                    reason_code="BLOCK_DEFINITION_UNAVAILABLE",
                )
                continue
            if block is None:
                self._traversal_diagnostic(
                    path,
                    source_insert,
                    status="unreadable",
                    reason=f"block definition is unavailable: {block_name}",
                    reason_code="BLOCK_DEFINITION_UNAVAILABLE",
                )
                continue

            _, insert_source_rgb, _ = _resolve_entity_rgb(
                occurrence,
                layer=insert_layer,
                document=self.document,
                byblock_rgb=inherited_byblock_rgb,
            )
            try:
                transform = occurrence.matrix44() @ parent_transform
            except Exception as exc:  # noqa: BLE001
                self._traversal_diagnostic(
                    path,
                    source_insert,
                    status="unreadable",
                    reason=f"INSERT transform failed: {type(exc).__name__}: {exc}",
                    reason_code="INSERT_TRANSFORM_FAILED",
                )
                continue

            next_stack = (*block_stack, block_key)
            for block_index, child in enumerate(block):
                kind = _safe_dxftype(child)
                child_locator = f"{source_locator}/{block_name}:{block_index}"
                if kind == "ACAD_PROXY_ENTITY":
                    self.extract_proxy(
                        child,
                        transform=transform,
                        insertion_path=path,
                        inherited_layer=insert_layer,
                        byblock_rgb=insert_source_rgb,
                        source_locator=child_locator,
                    )
                elif kind == "INSERT":
                    self.walk_insert(
                        child,
                        parent_transform=transform,
                        inherited_layer=insert_layer,
                        inherited_byblock_rgb=insert_source_rgb,
                        insertion_path=path,
                        block_stack=next_stack,
                        depth=depth + 1,
                        source_locator=child_locator,
                    )

    def extract_proxy(
        self,
        proxy: Any,
        *,
        transform: Matrix44,
        insertion_path: tuple[dict[str, Any], ...],
        inherited_layer: str,
        byblock_rgb: tuple[int, int, int],
        source_locator: str,
    ) -> None:
        self.result.proxy_instance_count += 1
        proxy_handle = _source_handle(proxy, source_locator)
        source_layer = str(_dxf_get(proxy, "layer", "0") or "0")
        proxy_layer = _effective_layer(
            source_layer,
            inherited_layer,
            inside_block=bool(insertion_path),
        )
        instance_key = _instance_key(insertion_path)
        path_payload = [dict(value) for value in insertion_path]
        base = {
            "kind": "proxy",
            "source_proxy_handle": proxy_handle,
            "layer": proxy_layer,
            "source_proxy_layer": source_layer,
            "insertion_path": path_payload,
            "instance_key": instance_key,
        }

        try:
            has_payload_attribute = hasattr(proxy, "proxy_graphic")
            payload = getattr(proxy, "proxy_graphic", None)
        except Exception as exc:  # noqa: BLE001
            self._finish_proxy(
                base,
                status="unreadable",
                reason=f"proxy graphic payload cannot be read: {type(exc).__name__}: {exc}",
                reason_code="PROXY_PAYLOAD_UNREADABLE",
            )
            return
        if has_payload_attribute and not payload:
            self._finish_proxy(
                base,
                status="missing",
                reason="proxy graphic payload is missing",
                reason_code="PROXY_GRAPHIC_MISSING",
            )
            return

        virtuals: list[Any] = []
        decode_error: Exception | None = None
        try:
            iterator = iter(proxy.virtual_entities())
            while True:
                try:
                    virtuals.append(next(iterator))
                except StopIteration:
                    break
                except Exception as exc:  # noqa: BLE001
                    decode_error = exc
                    break
        except Exception as exc:  # noqa: BLE001
            decode_error = exc

        if not virtuals:
            if decode_error is not None:
                self._finish_proxy(
                    base,
                    status="unreadable",
                    reason=(
                        "proxy graphic decoding failed: "
                        f"{type(decode_error).__name__}: {decode_error}"
                    ),
                    reason_code="PROXY_GRAPHIC_UNREADABLE",
                )
            else:
                self._finish_proxy(
                    base,
                    status="missing",
                    reason="proxy graphic yielded no primitives",
                    reason_code="PROXY_GRAPHIC_EMPTY",
                )
            return

        _, proxy_source_rgb, _ = _resolve_entity_rgb(
            proxy,
            layer=proxy_layer,
            document=self.document,
            byblock_rgb=byblock_rgb,
        )
        rendered = 0
        suppressed = 0
        unsupported = 0
        reasons: list[str] = []
        unsupported_types: Counter[str] = Counter()

        for virtual_index, primitive in enumerate(virtuals):
            kind = _safe_dxftype(primitive) or "UNKNOWN"
            self.result.discovered_primitive_count += 1
            _increment(self.result.type_counts, kind)
            source_key = proxy_display_source_key(
                proxy_handle,
                virtual_index,
                instance_key=instance_key,
            )
            if _source_is_suppressed(
                self.suppressed_source_keys,
                source_key=source_key,
                instance_key=instance_key,
                proxy_handle=proxy_handle,
                virtual_index=virtual_index,
            ):
                suppressed += 1
                self.result.suppressed_duplicate_count += 1
                continue
            if kind not in SUPPORTED_PROXY_PRIMITIVES:
                unsupported += 1
                self.result.unsupported_primitive_count += 1
                unsupported_types[kind] += 1
                _increment(self.result.unsupported_type_counts, kind)
                continue

            try:
                log, transformed = eztransform.copies([primitive], transform)
            except Exception as exc:  # noqa: BLE001
                transformed = []
                log_messages = [f"{type(exc).__name__}: {exc}"]
            else:
                log_messages = list(log.messages())

            if not transformed:
                unsupported += 1
                self.result.unsupported_primitive_count += 1
                unsupported_types[kind] += 1
                _increment(self.result.unsupported_type_counts, kind)
                detail = "; ".join(log_messages) or "no transformed primitive returned"
                reasons.append(f"virtual {virtual_index} {kind}: {detail}")
                continue

            source_failed = bool(log_messages)
            component_success = 0
            for component_index, transformed_primitive in enumerate(transformed):
                output_kind = _safe_dxftype(transformed_primitive) or kind
                if output_kind not in SUPPORTED_PROXY_PRIMITIVES:
                    source_failed = True
                    unsupported_types[output_kind] += 1
                    _increment(self.result.unsupported_type_counts, output_kind)
                    reasons.append(
                        f"virtual {virtual_index} produced unsupported {output_kind}"
                    )
                    continue
                try:
                    record = _record_for_supported_primitive(
                        transformed_primitive,
                        output_kind,
                        self.record_factory,
                    )
                except Exception as exc:  # noqa: BLE001
                    source_failed = True
                    reasons.append(
                        f"virtual {virtual_index} {output_kind} record failed: "
                        f"{type(exc).__name__}: {exc}"
                    )
                    continue

                display_kind = str(record.entity_type or output_kind).upper()
                if display_kind not in SUPPORTED_PROXY_PRIMITIVES:
                    source_failed = True
                    unsupported_types[display_kind] += 1
                    _increment(self.result.unsupported_type_counts, display_kind)
                    reasons.append(
                        f"virtual {virtual_index} serialized as unsupported "
                        f"{display_kind}"
                    )
                    continue

                primitive_source_layer = str(
                    _dxf_get(transformed_primitive, "layer", "0") or "0"
                )
                record_layer = _effective_layer(
                    primitive_source_layer,
                    proxy_layer,
                    inside_block=True,
                )
                display_rgb, source_rgb, color_mode = _resolve_entity_rgb(
                    transformed_primitive,
                    layer=record_layer,
                    document=self.document,
                    byblock_rgb=proxy_source_rgb,
                )
                stable_handle = f"PXVIS::{source_key}|C:{component_index}:{display_kind}"
                origin = getattr(transformed_primitive, "origin_of_copy", None)
                origin_is_proxy = _safe_dxftype(origin) == "ACAD_PROXY_ENTITY"
                raw = dict(record.raw or {})
                raw.update(
                    {
                        "source": PROXY_DISPLAY_SOURCE,
                        "display_only": True,
                        "engineering_eligible": False,
                        "source_key": source_key,
                        "proxy_source_key": source_key,
                        "display_id": stable_handle,
                        "source_proxy_handle": proxy_handle,
                        "parent_proxy_handle": proxy_handle,
                        "source_proxy_layer": source_layer,
                        "effective_proxy_layer": proxy_layer,
                        "proxy_virtual_index": virtual_index,
                        "proxy_component_index": component_index,
                        "source_primitive_type": kind,
                        "transformed_primitive_type": output_kind,
                        "display_primitive_type": display_kind,
                        "instance_key": instance_key,
                        "proxy_instance_id": instance_key,
                        "insertion_path": path_payload,
                        "primitive_source_layer": primitive_source_layer,
                        "display_rgb": _rgb_hex(display_rgb),
                        "source_rgb": _rgb_hex(source_rgb),
                        "proxy_source_rgb": _rgb_hex(proxy_source_rgb),
                        "color_resolution": color_mode,
                        "color_adjusted_for_contrast": display_rgb != source_rgb,
                        "true_color": _true_color_value(transformed_primitive),
                        "origin_proxy_verified": origin_is_proxy,
                        "visibility_layers": _visibility_layers(
                            insertion_path,
                            proxy_layer,
                            record_layer,
                        ),
                        "transformed_extrusion": _point3(
                            _dxf_get(
                                transformed_primitive,
                                "extrusion",
                                (0.0, 0.0, 1.0),
                            )
                        ),
                    }
                )
                record.handle = stable_handle
                record.entity_type = display_kind
                record.layer = record_layer
                record.color_index = _safe_color_index(transformed_primitive)
                record.raw = raw
                record.geometry = dict(record.geometry or {})
                record.geometry.update(
                    {
                        "display_only": True,
                        "source_key": source_key,
                    }
                )
                self.result.records.append(record)
                self.result.rendered_primitive_count += 1
                _increment(self.result.output_type_counts, display_kind)
                rendered += 1
                component_success += 1

            if source_failed:
                unsupported += 1
                self.result.unsupported_primitive_count += 1
                unsupported_types[kind] += 1
                _increment(self.result.unsupported_type_counts, kind)
                if log_messages:
                    reasons.append(
                        f"virtual {virtual_index} {kind}: {'; '.join(log_messages)}"
                    )
            elif component_success == 0:
                unsupported += 1
                self.result.unsupported_primitive_count += 1
                unsupported_types[kind] += 1
                _increment(self.result.unsupported_type_counts, kind)

        if unsupported_types:
            reasons.append(
                "unsupported primitive types: "
                + ", ".join(
                    f"{kind}={count}" for kind, count in sorted(unsupported_types.items())
                )
            )
        if decode_error is not None:
            reasons.append(
                "proxy graphic decoding stopped early: "
                f"{type(decode_error).__name__}: {decode_error}"
            )
            unsupported += 1
            self.result.unsupported_primitive_count += 1

        base.update(
            {
                "discovered_primitive_count": len(virtuals),
                "rendered_primitive_count": rendered,
                "suppressed_duplicate_count": suppressed,
                "unsupported_primitive_count": unsupported,
            }
        )
        if unsupported or decode_error is not None:
            self._finish_proxy(
                base,
                status="partial",
                reason="; ".join(_unique(reasons))
                or "proxy graphic is only partially supported",
                reason_code="PROXY_GRAPHIC_PARTIAL",
                counts_already_set=True,
            )
        else:
            reason = "all supported proxy graphic primitives are available for display"
            if suppressed:
                reason += f"; {suppressed} duplicate source primitive(s) suppressed"
            self._finish_proxy(
                base,
                status="complete",
                reason=reason,
                reason_code="PROXY_GRAPHIC_COMPLETE",
                counts_already_set=True,
            )

    def _finish_proxy(
        self,
        base: dict[str, Any],
        *,
        status: str,
        reason: str,
        reason_code: str,
        counts_already_set: bool = False,
    ) -> None:
        entry = dict(base)
        if not counts_already_set:
            entry.update(
                {
                    "discovered_primitive_count": 0,
                    "rendered_primitive_count": 0,
                    "suppressed_duplicate_count": 0,
                    "unsupported_primitive_count": 0,
                }
            )
        entry.update(
            {
                "status": status,
                "reason": reason,
                "reason_code": reason_code,
            }
        )
        self.result.diagnostics.append(entry)
        _increment(self.result.status_counts, status)
        if status == "complete":
            self.result.complete_proxy_count += 1
        elif status == "partial":
            self.result.partial_proxy_count += 1
        elif status == "missing":
            self.result.missing_graphic_count += 1
        elif status == "unreadable":
            self.result.unreadable_proxy_count += 1

    def _traversal_diagnostic(
        self,
        insertion_path: tuple[dict[str, Any], ...],
        entity: Any,
        *,
        status: str,
        reason: str,
        reason_code: str,
    ) -> None:
        self.result.traversal_diagnostic_count += 1
        _increment(self.result.status_counts, status)
        self.result.diagnostics.append(
            {
                "kind": "block_traversal",
                "source_proxy_handle": "",
                "layer": str(_dxf_get(entity, "layer", "0") or "0"),
                "insertion_path": [dict(value) for value in insertion_path],
                "status": status,
                "reason": reason,
                "reason_code": reason_code,
                "discovered_primitive_count": 0,
                "rendered_primitive_count": 0,
                "suppressed_duplicate_count": 0,
                "unsupported_primitive_count": 0,
            }
        )


def _insert_occurrences(source_insert: Any) -> Iterable[tuple[int, Any]]:
    count = int(getattr(source_insert, "mcount", 1) or 1)
    if count <= 1:
        yield 0, source_insert
        return
    for index, occurrence in enumerate(source_insert.multi_insert()):
        yield index, occurrence


def _instance_key(insertion_path: tuple[dict[str, Any], ...]) -> str:
    if not insertion_path:
        return MODELSPACE_INSTANCE_KEY
    return "/".join(
        "I:{handle}#{index}".format(
            handle=str(segment.get("insert_handle") or "?"),
            index=int(segment.get("occurrence_index") or 0),
        )
        for segment in insertion_path
    )


def _source_handle(entity: Any, fallback: str) -> str:
    handle = str(_dxf_get(entity, "handle", "") or "").strip()
    return handle or f"@{fallback}"


def _source_is_suppressed(
    values: Set[Any],
    *,
    source_key: str,
    instance_key: str,
    proxy_handle: str,
    virtual_index: int,
) -> bool:
    candidates: tuple[Any, ...] = (
        source_key,
        (instance_key, proxy_handle, virtual_index),
        (proxy_handle, virtual_index),
        f"{proxy_handle}:{virtual_index}",
        f"PXG_{proxy_handle}_{virtual_index}_0",
    )
    return any(value in values for value in candidates)


def _record_for_supported_primitive(
    primitive: Any,
    kind: str,
    record_factory: RecordFactory,
) -> DxfEntityRecord:
    try:
        record = record_factory(primitive)
    except Exception:
        if kind not in {"ELLIPSE", "SPLINE"}:
            raise
        record = DxfEntityRecord(
            handle="",
            entity_type=kind,
            layer=str(_dxf_get(primitive, "layer", "0") or "0"),
            color_index=_safe_color_index(primitive),
            linetype=str(_dxf_get(primitive, "linetype", "") or "") or None,
        )

    if kind == "ELLIPSE":
        record.geometry = {
            "center": _point3(_dxf_get(primitive, "center", (0.0, 0.0, 0.0))),
            "major_axis": _point3(
                _dxf_get(primitive, "major_axis", (1.0, 0.0, 0.0))
            ),
            "ratio": float(_dxf_get(primitive, "ratio", 1.0) or 1.0),
            "start_param": float(_dxf_get(primitive, "start_param", 0.0) or 0.0),
            "end_param": float(_dxf_get(primitive, "end_param", 0.0) or 0.0),
            "extrusion": _point3(
                _dxf_get(primitive, "extrusion", (0.0, 0.0, 1.0))
            ),
        }
    elif kind == "SPLINE":
        geometry = dict(record.geometry or {})
        geometry.update({
            "degree": int(_dxf_get(primitive, "degree", 3) or 3),
            "flags": int(_dxf_get(primitive, "flags", 0) or 0),
            "closed": bool(getattr(primitive, "closed", False)),
            "control_points": [_point3(value) for value in primitive.control_points],
            "fit_points": [_point3(value) for value in primitive.fit_points],
            "knots": [float(value) for value in primitive.knots],
            "weights": [float(value) for value in primitive.weights],
            "start_tangent": _optional_point3(
                _dxf_get(primitive, "start_tangent", None)
            ),
            "end_tangent": _optional_point3(
                _dxf_get(primitive, "end_tangent", None)
            ),
        })
        if not geometry.get("render_points"):
            try:
                geometry["render_points"] = [
                    _point3(value) for value in primitive.flattening(0.01, segments=8)
                ]
            except Exception:  # noqa: BLE001 - exact NURBS data remains available
                geometry["render_points"] = []
        record.geometry = geometry
    elif kind == "TEXT":
        record.geometry = dict(record.geometry or {})
        record.geometry.update(
            {
                "width_factor": float(_dxf_get(primitive, "width", 1.0) or 1.0),
                "oblique": float(_dxf_get(primitive, "oblique", 0.0) or 0.0),
                "halign": int(_dxf_get(primitive, "halign", 0) or 0),
                "valign": int(_dxf_get(primitive, "valign", 0) or 0),
                "align_point": _optional_point3(
                    _dxf_get(primitive, "align_point", None)
                ),
            }
        )
    elif kind == "MTEXT":
        record.geometry = dict(record.geometry or {})
        record.geometry.update(
            {
                "width": float(_dxf_get(primitive, "width", 0.0) or 0.0),
                "attachment_point": int(
                    _dxf_get(primitive, "attachment_point", 1) or 1
                ),
                "text_direction": _optional_point3(
                    _dxf_get(primitive, "text_direction", None)
                ),
            }
        )
    return _canonicalize_display_record(primitive, kind, record)


def _canonicalize_display_record(
    primitive: Any,
    kind: str,
    record: DxfEntityRecord,
) -> DxfEntityRecord:
    """Serialize display geometry in WCS, never in implicit DXF OCS.

    A reflected INSERT commonly leaves ARC, TEXT and 2D polylines with an
    extrusion of ``(0, 0, -1)``.  Their stored DXF x/y values are then OCS
    coordinates and cannot be sent directly to the 2D viewer.  This helper is
    intentionally applied only to the display-only copy after the strict
    engineering adapter has finished.
    """

    geometry = dict(record.geometry or {})
    if kind == "LINE":
        geometry.update(
            {
                "start": _point3(_dxf_get(primitive, "start", (0.0, 0.0, 0.0))),
                "end": _point3(_dxf_get(primitive, "end", (0.0, 0.0, 0.0))),
                "coordinate_space": "WCS",
            }
        )
    elif kind == "LWPOLYLINE":
        raw_points = list(primitive.get_points("xyb"))
        world_points = [Vec3(value) for value in primitive.vertices_in_wcs()]
        if len(raw_points) != len(world_points):
            raise ValueError("LWPOLYLINE OCS/WCS vertex count mismatch")
        bulges = [float(value[2] or 0.0) for value in raw_points]
        closed = bool(primitive.closed)
        if any(abs(value) > 1e-12 for value in bulges) and not _ocs_is_xy_plane(
            primitive
        ):
            return _projected_curve_record(
                record,
                primitive,
                source_kind=kind,
                world_points=world_points,
                bulges=bulges,
                closed=closed,
            )
        orientation = _ocs_view_orientation(primitive)
        view_bulges = [value * orientation for value in bulges]
        geometry.update(
            {
                "points": [
                    [point.x, point.y, bulge]
                    for point, bulge in zip(world_points, view_bulges)
                ],
                "wcs_points": [_point3(point) for point in world_points],
                "bulges": view_bulges,
                "source_ocs_bulges": bulges,
                "closed": closed,
                "curve_segment_count": sum(
                    abs(value) > 1e-12 for value in view_bulges
                ),
                "coordinate_space": "WCS",
                "source_extrusion": _entity_extrusion(primitive),
                "view_orientation_reversed": orientation < 0.0,
            }
        )
    elif kind == "POLYLINE":
        vertices = list(primitive.vertices)
        bulges = [
            float(_dxf_get(vertex, "bulge", 0.0) or 0.0) for vertex in vertices
        ]
        is_2d = bool(getattr(primitive, "is_2d_polyline", False))
        if is_2d:
            world_points = [Vec3(value) for value in primitive.points_in_wcs()]
        else:
            world_points = [
                Vec3(_dxf_get(vertex, "location", (0.0, 0.0, 0.0)))
                for vertex in vertices
            ]
        if len(vertices) != len(world_points):
            raise ValueError("POLYLINE OCS/WCS vertex count mismatch")
        closed = bool(primitive.is_closed)
        if (
            is_2d
            and any(abs(value) > 1e-12 for value in bulges)
            and not _ocs_is_xy_plane(primitive)
        ):
            return _projected_curve_record(
                record,
                primitive,
                source_kind=kind,
                world_points=world_points,
                bulges=bulges,
                closed=closed,
            )
        orientation = _ocs_view_orientation(primitive) if is_2d else 1.0
        view_bulges = [value * orientation for value in bulges]
        geometry.update(
            {
                "points": [_point3(point) for point in world_points],
                "wcs_points": [_point3(point) for point in world_points],
                "bulges": view_bulges,
                "source_ocs_bulges": bulges,
                "closed": closed,
                "curve_segment_count": sum(
                    abs(value) > 1e-12 for value in view_bulges
                ),
                "coordinate_space": "WCS",
                "source_extrusion": _entity_extrusion(primitive),
                "view_orientation_reversed": orientation < 0.0,
            }
        )
    elif kind == "CIRCLE":
        ocs = primitive.ocs()
        center = Vec3(ocs.to_wcs(_dxf_get(primitive, "center", (0.0, 0.0, 0.0))))
        radius = float(_dxf_get(primitive, "radius", 0.0) or 0.0)
        if _ocs_is_xy_plane(primitive):
            geometry.update(
                {
                    "center": _point3(center),
                    "radius": radius,
                    "coordinate_space": "WCS",
                    "source_extrusion": _entity_extrusion(primitive),
                }
            )
        else:
            record.entity_type = "ELLIPSE"
            geometry = _ocs_circle_as_ellipse_geometry(
                primitive,
                center=center,
                radius=radius,
                start_angle=0.0,
                end_angle=360.0,
            )
    elif kind == "ARC":
        ocs = primitive.ocs()
        center = Vec3(ocs.to_wcs(_dxf_get(primitive, "center", (0.0, 0.0, 0.0))))
        radius = float(_dxf_get(primitive, "radius", 0.0) or 0.0)
        source_start = float(_dxf_get(primitive, "start_angle", 0.0) or 0.0)
        source_end = float(_dxf_get(primitive, "end_angle", 0.0) or 0.0)
        if _ocs_is_xy_plane(primitive):
            world_start = Vec3(primitive.start_point)
            world_end = Vec3(primitive.end_point)
            start_angle = _view_angle(center, world_start)
            end_angle = _view_angle(center, world_end)
            reversed_for_view = _ocs_view_orientation(primitive) < 0.0
            if reversed_for_view:
                start_angle, end_angle = end_angle, start_angle
            geometry.update(
                {
                    "center": _point3(center),
                    "radius": radius,
                    "start_angle": start_angle,
                    "end_angle": end_angle,
                    "source_start_angle": source_start,
                    "source_end_angle": source_end,
                    "source_world_start": _point3(world_start),
                    "source_world_end": _point3(world_end),
                    "coordinate_space": "WCS",
                    "source_extrusion": _entity_extrusion(primitive),
                    "view_orientation_reversed": reversed_for_view,
                }
            )
        else:
            record.entity_type = "ELLIPSE"
            geometry = _ocs_circle_as_ellipse_geometry(
                primitive,
                center=center,
                radius=radius,
                start_angle=source_start,
                end_angle=source_end,
            )
            geometry["source_entity_type"] = "ARC"
    elif kind == "ELLIPSE":
        center = Vec3(_dxf_get(primitive, "center", (0.0, 0.0, 0.0)))
        major = Vec3(_dxf_get(primitive, "major_axis", (1.0, 0.0, 0.0)))
        try:
            minor = Vec3(primitive.minor_axis)
        except Exception:  # noqa: BLE001
            normal = Vec3(_entity_extrusion(primitive)).normalize()
            minor = normal.cross(major).normalize(
                major.magnitude
                * float(_dxf_get(primitive, "ratio", 1.0) or 1.0)
            )
        geometry.update(
            {
                "center": _point3(center),
                "major_axis": _point3(major),
                "minor_axis": _point3(minor),
                "ratio": float(_dxf_get(primitive, "ratio", 1.0) or 1.0),
                "start_param": float(
                    _dxf_get(primitive, "start_param", 0.0) or 0.0
                ),
                "end_param": float(
                    _dxf_get(primitive, "end_param", math.tau) or math.tau
                ),
                "source_world_start": _point3(primitive.start_point),
                "source_world_end": _point3(primitive.end_point),
                "coordinate_space": "WCS",
                "source_extrusion": _entity_extrusion(primitive),
            }
        )
    elif kind == "TEXT":
        geometry = _canonical_text_geometry(primitive, record)
    elif kind == "MTEXT":
        geometry = _canonical_mtext_geometry(primitive, record)
    elif kind == "SPLINE":
        geometry["coordinate_space"] = "WCS"

    record.geometry = geometry
    return record


def _projected_curve_record(
    record: DxfEntityRecord,
    primitive: Any,
    *,
    source_kind: str,
    world_points: list[Vec3],
    bulges: list[float],
    closed: bool,
) -> DxfEntityRecord:
    """Represent a tilted OCS bulge path as projected cubic curves.

    A circular OCS arc becomes an ellipse in the XY view. Keeping its bulge
    after projection would draw a different circle; straight chords would be
    worse. ezdxf's path conversion supplies cubic Bezier arc segments in WCS,
    which the display renderer consumes as curves.
    """

    path = make_path(primitive)
    current = Vec3(path.start)
    segments: list[dict[str, list[float]]] = []
    for command in path:
        end = Vec3(command.end)
        if command.type is Command.LINE_TO:
            control1 = current.lerp(end, 1.0 / 3.0)
            control2 = current.lerp(end, 2.0 / 3.0)
        elif command.type is Command.CURVE3_TO:
            control = Vec3(command.ctrl)
            control1 = current + (control - current) * (2.0 / 3.0)
            control2 = end + (control - end) * (2.0 / 3.0)
        elif command.type is Command.CURVE4_TO:
            control1 = Vec3(command.ctrl1)
            control2 = Vec3(command.ctrl2)
        else:
            raise ValueError("multi-subpath proxy curve is not supported")
        segments.append(
            {
                "start": _point3(current),
                "control1": _point3(control1),
                "control2": _point3(control2),
                "end": _point3(end),
            }
        )
        current = end
    if not segments:
        raise ValueError("tilted proxy curve yielded no Bezier segments")
    record.entity_type = "SPLINE"
    record.geometry = {
        "start": _point3(path.start),
        "bezier_segments": segments,
        "render_points": [_point3(value) for value in path.flattening(0.01, 8)],
        "closed": closed,
        "source_entity_type": source_kind,
        "source_points_wcs": [_point3(value) for value in world_points],
        "source_ocs_bulges": list(bulges),
        "curve_segment_count": sum(abs(value) > 1e-12 for value in bulges),
        "coordinate_space": "WCS",
        "curve_representation": "projected_cubic_bezier",
        "source_extrusion": _entity_extrusion(primitive),
    }
    return record


def _ocs_circle_as_ellipse_geometry(
    primitive: Any,
    *,
    center: Vec3,
    radius: float,
    start_angle: float,
    end_angle: float,
) -> dict[str, Any]:
    ocs = primitive.ocs()
    major = Vec3(ocs.ux) * radius
    minor = Vec3(ocs.uy) * radius
    sweep = (float(end_angle) - float(start_angle)) % 360.0
    if math.isclose(sweep, 0.0, abs_tol=1e-12):
        sweep = 360.0
    start_param = math.radians(float(start_angle))
    end_param = start_param + math.radians(sweep)
    return {
        "center": _point3(center),
        "major_axis": _point3(major),
        "minor_axis": _point3(minor),
        "ratio": 1.0,
        "start_param": start_param,
        "end_param": end_param,
        "coordinate_space": "WCS",
        "curve_representation": "projected_ocs_ellipse",
        "source_extrusion": _entity_extrusion(primitive),
    }


def _canonical_text_geometry(
    primitive: Any,
    record: DxfEntityRecord,
) -> dict[str, Any]:
    geometry = dict(record.geometry or {})
    ocs = primitive.ocs()
    insert = Vec3(ocs.to_wcs(_dxf_get(primitive, "insert", (0.0, 0.0, 0.0))))
    raw_rotation = float(_dxf_get(primitive, "rotation", 0.0) or 0.0)
    radians = math.radians(raw_rotation)
    baseline = Vec3(ocs.to_wcs((math.cos(radians), math.sin(radians), 0.0)))
    vertical = Vec3(ocs.to_wcs((-math.sin(radians), math.cos(radians), 0.0)))
    baseline_view_length = math.hypot(baseline.x, baseline.y)
    vertical_view_length = math.hypot(vertical.x, vertical.y)
    if baseline_view_length <= 1e-12 or vertical_view_length <= 1e-12:
        raise ValueError("TEXT plane is edge-on to the XY display view")
    rotation = math.degrees(math.atan2(baseline.y, baseline.x))
    height = float(_dxf_get(primitive, "height", 1.0) or 1.0)
    view_height = height * vertical_view_length
    align_value = _dxf_get(primitive, "align_point", None)
    align_point = (
        _point3(ocs.to_wcs(align_value)) if align_value is not None else None
    )
    geometry.update(
        {
            "insert": _point3(insert),
            "align_point": align_point,
            "height": view_height,
            "rotation": rotation,
            "source_rotation": raw_rotation,
            "baseline_direction_wcs": _point3(baseline),
            "vertical_direction_wcs": _point3(vertical),
            "view_width_scale": baseline_view_length,
            "coordinate_space": "WCS",
            "source_extrusion": _entity_extrusion(primitive),
        }
    )
    geometry.update(
        _text_view_extents(
            record.text or "",
            insert,
            view_height,
            rotation,
            width_factor=float(_dxf_get(primitive, "width", 1.0) or 1.0)
            * baseline_view_length,
        )
    )
    return geometry


def _canonical_mtext_geometry(
    primitive: Any,
    record: DxfEntityRecord,
) -> dict[str, Any]:
    geometry = dict(record.geometry or {})
    insert = Vec3(_dxf_get(primitive, "insert", (0.0, 0.0, 0.0)))
    direction_value = _dxf_get(primitive, "text_direction", None)
    if direction_value is None:
        rotation = float(getattr(primitive, "get_rotation", lambda: 0.0)() or 0.0)
        direction = Vec3.from_deg_angle(rotation)
    else:
        direction = Vec3(direction_value)
        rotation = math.degrees(math.atan2(direction.y, direction.x))
    view_direction_length = math.hypot(direction.x, direction.y)
    normal = Vec3(_entity_extrusion(primitive)).normalize()
    vertical = normal.cross(direction.normalize())
    vertical_view_length = math.hypot(vertical.x, vertical.y)
    if view_direction_length <= 1e-12 or vertical_view_length <= 1e-12:
        raise ValueError("MTEXT plane is edge-on to the XY display view")
    height = float(_dxf_get(primitive, "char_height", 1.0) or 1.0)
    view_height = height * vertical_view_length
    geometry.update(
        {
            "insert": _point3(insert),
            "char_height": view_height,
            "rotation": rotation,
            "text_direction": _point3(direction),
            "vertical_direction_wcs": _point3(vertical),
            "coordinate_space": "WCS",
            "source_extrusion": _entity_extrusion(primitive),
        }
    )
    geometry.update(
        _text_view_extents(
            record.text or "",
            insert,
            view_height,
            rotation,
            width_hint=float(_dxf_get(primitive, "width", 0.0) or 0.0),
        )
    )
    return geometry


def _text_view_extents(
    text: str,
    insert: Vec3,
    height: float,
    rotation: float,
    *,
    width_hint: float = 0.0,
    width_factor: float = 1.0,
) -> dict[str, Any]:
    lines = (text or "").replace("\r", "").split("\n") or [""]
    char_height = max(float(height or 1.0), 1e-6)
    estimate = (
        max((len(line) for line in lines), default=1)
        * char_height
        * 0.62
        * abs(float(width_factor or 1.0))
    )
    width = max(float(width_hint or 0.0), estimate, char_height)
    box_height = max(len(lines) * char_height * 1.2, char_height)
    angle = math.radians(rotation)
    ux, uy = math.cos(angle), math.sin(angle)
    vx, vy = -uy, ux
    corners = [
        [insert.x, insert.y, insert.z],
        [insert.x + width * ux, insert.y + width * uy, insert.z],
        [insert.x + box_height * vx, insert.y + box_height * vy, insert.z],
        [
            insert.x + width * ux + box_height * vx,
            insert.y + width * uy + box_height * vy,
            insert.z,
        ],
    ]
    return {
        "visual_anchor": [
            insert.x + 0.5 * width * ux + 0.5 * box_height * vx,
            insert.y + 0.5 * width * uy + 0.5 * box_height * vy,
            insert.z,
        ],
        "visual_bbox": corners,
        "estimated_text_width": width,
        "estimated_text_height": box_height,
    }


def _entity_extrusion(entity: Any) -> list[float]:
    return _point3(_dxf_get(entity, "extrusion", (0.0, 0.0, 1.0)))


def _ocs_is_xy_plane(entity: Any) -> bool:
    normal = Vec3(_entity_extrusion(entity)).normalize()
    return (
        abs(normal.x) <= 1e-9
        and abs(normal.y) <= 1e-9
        and abs(abs(normal.z) - 1.0) <= 1e-9
    )


def _ocs_view_orientation(entity: Any) -> float:
    ocs = entity.ocs()
    ux = Vec3(ocs.ux)
    uy = Vec3(ocs.uy)
    determinant = ux.x * uy.y - ux.y * uy.x
    return -1.0 if determinant < 0.0 else 1.0


def _view_angle(center: Vec3, point: Vec3) -> float:
    return math.degrees(math.atan2(point.y - center.y, point.x - center.x)) % 360.0


def _visibility_layers(
    insertion_path: tuple[dict[str, Any], ...],
    proxy_layer: str,
    record_layer: str,
) -> list[str]:
    result: list[str] = []
    for segment in insertion_path:
        layer = str(segment.get("insert_layer") or "0")
        if layer not in result:
            result.append(layer)
    for layer in (str(proxy_layer or "0"), str(record_layer or "0")):
        if layer not in result:
            result.append(layer)
    return result


def _effective_layer(layer: str, inherited_layer: str, *, inside_block: bool) -> str:
    value = str(layer or "0")
    if value == "0" and inside_block:
        return str(inherited_layer or "0")
    return value


def _resolve_entity_rgb(
    entity: Any,
    *,
    layer: str,
    document: Any,
    byblock_rgb: tuple[int, int, int],
) -> tuple[tuple[int, int, int], tuple[int, int, int], str]:
    true_color = _true_color_value(entity)
    if true_color is not None:
        rgb = tuple(int(value) for value in ezcolors.int2rgb(true_color))
        mode = "TRUECOLOR"
    else:
        aci = _safe_color_index(entity)
        if aci in (None, 256):
            rgb = _layer_rgb(document, layer)
            mode = "BYLAYER"
        elif aci == 0:
            rgb = byblock_rgb
            mode = "BYBLOCK"
        else:
            rgb = _aci_rgb(aci)
            mode = "ACI"
    source_rgb = _clamp_rgb(rgb)
    return _ensure_light_background_contrast(source_rgb), source_rgb, mode


def _layer_rgb(document: Any, layer_name: str) -> tuple[int, int, int]:
    try:
        layer = document.layers.get(layer_name)
    except Exception:  # noqa: BLE001
        return _DEFAULT_FOREGROUND
    try:
        true_rgb = layer.rgb
    except Exception:  # noqa: BLE001
        true_rgb = None
    if true_rgb is not None:
        return _clamp_rgb(true_rgb)
    try:
        return _aci_rgb(int(layer.color))
    except Exception:  # noqa: BLE001
        return _DEFAULT_FOREGROUND


def _aci_rgb(aci: int) -> tuple[int, int, int]:
    # ACI 7 is adaptive black/white. The LION viewer has a light background,
    # therefore black is the faithful visible choice here.
    if abs(int(aci)) == 7:
        return 0, 0, 0
    try:
        return _clamp_rgb(ezcolors.aci2rgb(abs(int(aci))))
    except Exception:  # noqa: BLE001
        return _DEFAULT_FOREGROUND


def _ensure_light_background_contrast(
    rgb: tuple[int, int, int],
) -> tuple[int, int, int]:
    if _contrast_ratio(rgb, _LIGHT_BACKGROUND) >= 3.0:
        return rgb
    red, green, blue = (value / 255.0 for value in rgb)
    hue, lightness, saturation = colorsys.rgb_to_hls(red, green, blue)
    for target_lightness in (0.42, 0.36, 0.30, 0.24, 0.18, 0.12):
        candidate = _clamp_rgb(
            tuple(
                round(value * 255.0)
                for value in colorsys.hls_to_rgb(
                    hue,
                    min(lightness, target_lightness),
                    saturation,
                )
            )
        )
        if _contrast_ratio(candidate, _LIGHT_BACKGROUND) >= 3.0:
            return candidate
    return _DEFAULT_FOREGROUND


def _contrast_ratio(
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


def _true_color_value(entity: Any) -> int | None:
    namespace = getattr(entity, "dxf", None)
    try:
        if namespace is None or not namespace.hasattr("true_color"):
            return None
    except Exception:  # noqa: BLE001
        value = _dxf_get(entity, "true_color", None)
        return _safe_int(value)
    return _safe_int(_dxf_get(entity, "true_color", None))


def _safe_color_index(entity: Any) -> int | None:
    return _safe_int(_dxf_get(entity, "color", 256))


def _safe_dxftype(entity: Any) -> str:
    if entity is None:
        return ""
    try:
        return str(entity.dxftype() or "").upper()
    except Exception:  # noqa: BLE001
        return ""


def _dxf_get(entity: Any, name: str, default: Any) -> Any:
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


def _point3(value: Any) -> list[float]:
    try:
        return [
            float(value[0]),
            float(value[1]),
            float(value[2] if len(value) > 2 else 0.0),
        ]
    except (TypeError, IndexError):
        return [
            float(value.x),
            float(value.y),
            float(getattr(value, "z", 0.0)),
        ]


def _optional_point3(value: Any) -> list[float] | None:
    if value is None:
        return None
    try:
        return _point3(value)
    except (AttributeError, TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _clamp_rgb(values: Iterable[Any]) -> tuple[int, int, int]:
    result = tuple(max(0, min(255, int(value))) for value in values)
    if len(result) != 3:
        return _DEFAULT_FOREGROUND
    return result  # type: ignore[return-value]


def _rgb_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def _increment(mapping: dict[str, int], key: str, count: int = 1) -> None:
    mapping[key] = int(mapping.get(key, 0)) + count


def _unique(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


__all__ = [
    "MAX_INSERT_DEPTH",
    "MODELSPACE_INSTANCE_KEY",
    "PROXY_DISPLAY_SOURCE",
    "SUPPORTED_PROXY_PRIMITIVES",
    "ProxyDisplayResult",
    "extract_proxy_display_graphics",
    "proxy_display_source_key",
]
