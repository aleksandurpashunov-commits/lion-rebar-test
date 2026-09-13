from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from lion_rebar.dxf.batch_geometry_report import (
    _apply_proxy_declared_shape_suggestions,
    _apply_proxy_reference_geometry_fallback,
    build_batch_geometry_report,
)
from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.dxf.proxy_adapter import (
    PROXY_ANNOTATION_SOURCE,
    PROXY_ASSOCIATION_CODE,
    PROXY_DECLARED_LENGTH_CODE,
    PROXY_DECLARED_SHAPE_CODE,
    ProxyAdapterConfig,
    extract_acad_proxy_rebar,
)
from lion_rebar.engine.package_io import inspect_engine_bundle


class _FakeEntity:
    def __init__(
        self,
        kind: str,
        *,
        handle: str = "",
        layer: str = "0",
        text: str = "",
        insert: tuple[float, float, float] = (0.0, 0.0, 0.0),
        points: tuple[tuple[float, float, float], ...] = (),
        virtual: tuple["_FakeEntity", ...] = (),
        color: int = 256,
        true_color: int | None = None,
        halign: int = 0,
        valign: int = 0,
        align_point: tuple[float, float, float] | None = None,
        center: tuple[float, float, float] = (0.0, 0.0, 0.0),
        radius: float = 0.0,
        start_angle: float = 0.0,
        end_angle: float = 0.0,
        closed: bool = False,
    ) -> None:
        self._kind = kind
        self._virtual = virtual
        self.closed = closed
        self.dxf = SimpleNamespace(
            handle=handle,
            layer=layer,
            text=text,
            insert=insert,
            rotation=0.0,
            height=10.0,
            width=1.0,
            oblique=0.0,
            halign=halign,
            valign=valign,
            align_point=align_point,
            color=color,
            true_color=true_color,
            center=center,
            radius=radius,
            start_angle=start_angle,
            end_angle=end_angle,
        )
        self.points = points

    def dxftype(self) -> str:
        return self._kind

    def virtual_entities(self):
        return iter(self._virtual)


def _record_factory(entity: _FakeEntity) -> DxfEntityRecord:
    if entity.dxftype() == "TEXT":
        insert = list(entity.dxf.insert)
        return DxfEntityRecord(
            handle="",
            entity_type="TEXT",
            layer=entity.dxf.layer,
            text=entity.dxf.text,
            geometry={
                "insert": insert,
                "height": 10.0,
                "rotation": 0.0,
                "visual_anchor": insert,
                "visual_bbox": [insert],
                "width_factor": float(entity.dxf.width),
                "oblique": float(entity.dxf.oblique),
                "halign": int(entity.dxf.halign),
                "valign": int(entity.dxf.valign),
                "align_point": (
                    list(entity.dxf.align_point)
                    if entity.dxf.align_point is not None
                    else None
                ),
            },
            color_index=entity.dxf.color,
            raw=(
                {"true_color": entity.dxf.true_color}
                if entity.dxf.true_color is not None
                else {}
            ),
        )
    if entity.dxftype() == "POLYLINE":
        return DxfEntityRecord(
            handle="",
            entity_type="POLYLINE",
            layer=entity.dxf.layer,
            geometry={
                "points": [list(value) for value in entity.points],
                "bulges": [0.0 for _ in entity.points],
                "closed": False,
            },
            color_index=entity.dxf.color,
            raw=(
                {"true_color": entity.dxf.true_color}
                if entity.dxf.true_color is not None
                else {}
            ),
        )
    if entity.dxftype() == "LWPOLYLINE":
        return DxfEntityRecord(
            handle="",
            entity_type="LWPOLYLINE",
            layer=entity.dxf.layer,
            geometry={
                "points": [list(value) for value in entity.points],
                "bulges": [0.0 for _ in entity.points],
                "closed": entity.closed,
            },
            color_index=entity.dxf.color,
            raw=(
                {"true_color": entity.dxf.true_color}
                if entity.dxf.true_color is not None
                else {}
            ),
        )
    if entity.dxftype() == "ARC":
        return DxfEntityRecord(
            handle="",
            entity_type="ARC",
            layer=entity.dxf.layer,
            geometry={
                "center": list(entity.dxf.center),
                "radius": float(entity.dxf.radius),
                "start_angle": float(entity.dxf.start_angle),
                "end_angle": float(entity.dxf.end_angle),
            },
            color_index=entity.dxf.color,
            raw=(
                {"true_color": entity.dxf.true_color}
                if entity.dxf.true_color is not None
                else {}
            ),
        )
    raise ValueError(entity.dxftype())


def _geometry_proxy(handle: str, length: float, *, layer: str = "GA_X") -> _FakeEntity:
    return _FakeEntity(
        "ACAD_PROXY_ENTITY",
        handle=handle,
        layer=layer,
        virtual=(
            _FakeEntity(
                "POLYLINE",
                layer="0",
                points=((0.0, 0.0, 0.0), (length, 0.0, 0.0)),
            ),
        ),
    )


def _annotation_proxy(
    handle: str,
    position: int,
    length: float,
    *,
    layer: str = "GA_X",
) -> _FakeEntity:
    tokens = (
        str(position),
        "3",
        "N",
        "14",
        "x",
        str(int(length)),
        "/",
        "10",
    )
    return _FakeEntity(
        "ACAD_PROXY_ENTITY",
        handle=handle,
        layer=layer,
        virtual=tuple(
            _FakeEntity("TEXT", text=token, insert=(index * 10.0, 10.0, 0.0))
            for index, token in enumerate(tokens)
        ),
    )


def _config(**overrides) -> ProxyAdapterConfig:
    values = {
        "minimum_scale_evidence": 1,
        "minimum_scale_coverage": 1.0,
        "minimum_scale_winner_margin": 1,
    }
    values.update(overrides)
    return ProxyAdapterConfig(**values)


def test_proxy_adapter_recovers_complete_annotations_and_exact_parent_geometry() -> None:
    source = []
    for index, length in enumerate((100.0, 200.0, 300.0), start=1):
        source.extend(
            (
                _geometry_proxy(f"G{index}", length),
                _annotation_proxy(f"A{index}", index, length),
            )
        )

    result = extract_acad_proxy_rebar(
        source,
        record_factory=_record_factory,
        units_code=4,
        config=_config(minimum_scale_evidence=3),
    )

    assert result.parsed_annotation_count == 3
    assert result.resolved_annotation_count == 3
    assert result.unresolved_annotation_count == 0
    assert result.scale_factor == 1.0
    assert result.scale_evidence_by_hypothesis["1"] == 3
    assert [value.text for value in result.annotation_records] == [
        "3N14x100",
        "3N14x200",
        "3N14x300",
    ]
    assert result.annotation_records[0].raw["proxy_geometry_parent_handle"] == "G1"
    assert result.annotation_records[0].raw["association_code"] == PROXY_ASSOCIATION_CODE
    assert any("$INSUNITS е mm" in value for value in result.warnings)


def test_proxy_adapter_calibrates_scale_from_geometry_instead_of_header() -> None:
    result = extract_acad_proxy_rebar(
        (_geometry_proxy("G1", 1000.0), _annotation_proxy("A1", 1, 100.0)),
        record_factory=_record_factory,
        units_code=4,
        config=_config(),
    )

    assert result.resolved_annotation_count == 1
    assert result.scale_factor == 0.1
    assert result.annotation_records[0].raw["exact_total_cm"] == 100.0


def test_proxy_adapter_preserves_virtual_color_layer_and_text_layout_provenance() -> None:
    geometry_proxy = _FakeEntity(
        "ACAD_PROXY_ENTITY",
        handle="G-COLOR",
        layer="GA_X",
        virtual=(
            _FakeEntity(
                "POLYLINE",
                layer="SOURCE_GEOMETRY",
                color=5,
                true_color=0x123456,
                points=((0.0, 0.0, 0.0), (100.0, 0.0, 0.0)),
            ),
        ),
    )
    tokens = ("1", "3", "N", "14", "x", "100", "/", "10")
    annotation_proxy = _FakeEntity(
        "ACAD_PROXY_ENTITY",
        handle="A-COLOR",
        layer="GA_X",
        virtual=tuple(
            _FakeEntity(
                "TEXT",
                text=token,
                insert=(index * 10.0, 10.0, 0.0),
                layer="SOURCE_TEXT",
                color=0,
                true_color=(0x654321 if index == 0 else None),
                halign=(2 if index == 0 else 0),
                valign=(3 if index == 0 else 0),
                align_point=(15.0, 12.0, 0.0) if index == 0 else None,
            )
            for index, token in enumerate(tokens)
        ),
    )

    result = extract_acad_proxy_rebar(
        (geometry_proxy, annotation_proxy),
        record_factory=_record_factory,
        units_code=4,
        config=_config(),
    )

    geometry = result.geometry_records[0]
    assert geometry.layer == "ARM_PROXY_GA_X"
    assert geometry.raw["proxy_virtual_layer"] == "SOURCE_GEOMETRY"
    assert geometry.raw["true_color"] == 0x123456
    fragment = result.annotation_records[0].geometry["render_fragments"][0]
    assert fragment["source_layer"] == "SOURCE_TEXT"
    assert fragment["true_color"] == 0x654321
    assert fragment["halign"] == 2
    assert fragment["valign"] == 3
    assert fragment["align_point"] == [15.0, 12.0, 0.0]


def test_proxy_adapter_accepts_declared_length_when_geometry_subset_is_ambiguous() -> None:
    """Owner-confirmed 2026-09-10: geometric ambiguity no longer discards the
    annotation -- the declared count/diameter/length are trusted directly,
    same policy as everywhere else in the product ("read the specification,
    don't audit the drafting precision"). Geometry stays unattached here
    (two equally-valid candidate lines, no way to pick one) rather than
    guessing which physical line the bar actually is."""

    ambiguous = _FakeEntity(
        "ACAD_PROXY_ENTITY",
        handle="G1",
        layer="GA_X",
        virtual=(
            _FakeEntity(
                "POLYLINE",
                points=((0.0, 0.0, 0.0), (100.0, 0.0, 0.0)),
            ),
            _FakeEntity(
                "POLYLINE",
                points=((0.0, 20.0, 0.0), (100.0, 20.0, 0.0)),
            ),
        ),
    )
    result = extract_acad_proxy_rebar(
        (ambiguous, _annotation_proxy("A1", 1, 100.0)),
        record_factory=_record_factory,
        units_code=4,
        config=_config(),
    )

    assert result.parsed_annotation_count == 1
    assert result.resolved_annotation_count == 1
    assert result.unresolved_annotation_count == 0
    assert result.declared_length_annotation_count == 1
    assert result.scale_factor is None
    record = result.annotation_records[0]
    assert record.text == "3N14x100"
    assert record.raw["association_code"] == PROXY_DECLARED_LENGTH_CODE
    assert record.raw["geometry_linked"] is False
    assert record.raw["proxy_geometry_handles"] == []


def test_proxy_adapter_ignores_unapproved_proxy_layer() -> None:
    result = extract_acad_proxy_rebar(
        (
            _geometry_proxy("G1", 100.0, layer="OTHER"),
            _annotation_proxy("A1", 1, 100.0, layer="OTHER"),
        ),
        record_factory=_record_factory,
        units_code=4,
        config=_config(),
    )

    assert result.proxy_entity_count == 2
    assert result.parsed_annotation_count == 0
    assert result.annotation_records == []
    assert result.geometry_records == []


def test_proxy_adapter_admits_second_designer_gredi_dialect_layers() -> None:
    """2026-09-10: a second designer's beam/column/corbel reinforcement uses
    the exact same complete-token-sequence + exact-geometry-length proof as
    GA_X/GA_Y, just on C_ARM_Gredi/C_arm_stylbi/C_Arm_kobilici layers."""

    for layer in ("C_ARM_Gredi", "C_arm_stylbi", "C_Arm_kobilici"):
        result = extract_acad_proxy_rebar(
            (_geometry_proxy("G1", 100.0, layer=layer), _annotation_proxy("A1", 1, 100.0, layer=layer)),
            record_factory=_record_factory,
            units_code=4,
            config=_config(),
        )
        assert result.resolved_annotation_count == 1, f"layer {layer} was not admitted"
        assert result.annotation_records[0].text == "3N14x100"


def test_proxy_adapter_geometry_member_accepts_arc_bend_segments() -> None:
    """A bar bend/hook drawn as an ARC primitive must count toward the exact
    declared length, not be silently excluded before geometry matching."""

    import math

    half_circle_radius = 100.0 / math.pi  # length = radius * pi = 100.0 exactly
    arc_geometry_proxy = _FakeEntity(
        "ACAD_PROXY_ENTITY",
        handle="G-ARC",
        layer="C_ARM_Gredi",
        virtual=(
            _FakeEntity(
                "ARC",
                center=(0.0, 0.0, 0.0),
                radius=half_circle_radius,
                start_angle=0.0,
                end_angle=180.0,
            ),
        ),
    )
    result = extract_acad_proxy_rebar(
        (arc_geometry_proxy, _annotation_proxy("A1", 1, 100.0, layer="C_ARM_Gredi")),
        record_factory=_record_factory,
        units_code=4,
        config=_config(),
    )
    assert result.resolved_annotation_count == 1
    assert result.annotation_records[0].raw["exact_total_cm"] == 100.0


def test_proxy_adapter_resolves_self_contained_bar_geometry_and_annotation() -> None:
    """2026-09-10 finding: unlike GA_X/GA_Y (separate geometry-then-annotation
    proxies), this dialect draws ONE bar as a SINGLE proxy holding both its
    own text fragments and its own geometry. The association must try the
    annotation's own proxy, not only strictly preceding ones."""

    tokens = ("1", "3", "N", "14", "x", "100", "/", "10")
    self_contained = _FakeEntity(
        "ACAD_PROXY_ENTITY",
        handle="SELF-1",
        layer="C_ARM_Gredi",
        virtual=(
            *(
                _FakeEntity("TEXT", text=token, insert=(index * 10.0, 10.0, 0.0))
                for index, token in enumerate(tokens)
            ),
            _FakeEntity(
                "POLYLINE",
                points=((0.0, 0.0, 0.0), (100.0, 0.0, 0.0)),
            ),
        ),
    )
    result = extract_acad_proxy_rebar(
        (self_contained,),
        record_factory=_record_factory,
        units_code=4,
        config=_config(),
    )
    assert result.parsed_annotation_count == 1
    assert result.resolved_annotation_count == 1
    assert result.annotation_records[0].raw["proxy_geometry_parent_handle"] == "SELF-1"
    assert result.annotation_records[0].raw["exact_total_cm"] == 100.0


def _total_count_tokens(
    position: int, diameter: int, length: int, count: int, *, closer: str = ")",
) -> tuple[str, ...]:
    return (
        str(position), "N", str(diameter), "x", str(length),
        " (общо ", str(count), closer,
    )


def test_proxy_adapter_parses_owner_confirmed_koloni_stirrup_labels() -> None:
    """2026-09-10, owner-confirmed against the real drawing: koloni stirrup
    labels carry NO leading count -- the total trails as ``(общо N)`` --
    and the SAME proxy also carries unrelated cross-section detail numbers
    straight after the closing bracket (real data: '51','26','51','26',
    '8','8' following position 3's "N8x170 (общо 26)"). Values below are
    the owner's own verified numbers: 8+31+46+31+46+8 = 170.
    """

    tokens = _total_count_tokens(3, 8, 170, 26)
    trailing_junk = ("51", "26", "51", "26", "8", "8")
    annotation = _FakeEntity(
        "ACAD_PROXY_ENTITY",
        handle="A-KOLONI",
        layer="ARM_KOLONI",
        virtual=tuple(
            _FakeEntity("TEXT", text=token, insert=(index * 10.0, 10.0, 0.0))
            for index, token in enumerate((*tokens, *trailing_junk))
        ),
    )
    result = extract_acad_proxy_rebar(
        (_geometry_proxy("G-KOLONI", 170.0, layer="ARM_KOLONI"), annotation),
        record_factory=_record_factory,
        units_code=4,
        config=_config(minimum_scale_evidence=1),
    )
    assert result.parsed_annotation_count == 1
    assert result.resolved_annotation_count == 1
    assert result.annotation_records[0].text == "26N8x170"
    assert result.annotation_records[0].raw["exact_total_cm"] == 170.0


def test_proxy_adapter_total_count_annotation_ignores_empty_placeholder_fragments() -> None:
    """Real data: some koloni labels carry empty-text placeholder fragments
    right after the position and right after 'N' (e.g. ['3', '', 'N', '',
    '8', 'x', '170', ' (общо ', '27', ')']) -- they must be skipped, not
    treated as part of the token sequence."""

    tokens = ("3", "", "N", "", "8", "x", "170", " (общо ", "27", ")")
    annotation = _FakeEntity(
        "ACAD_PROXY_ENTITY",
        handle="A-EMPTY",
        layer="ARM_KOLONI",
        virtual=tuple(
            _FakeEntity("TEXT", text=token, insert=(index * 10.0, 10.0, 0.0))
            for index, token in enumerate(tokens)
        ),
    )
    result = extract_acad_proxy_rebar(
        (_geometry_proxy("G-EMPTY", 170.0, layer="ARM_KOLONI"), annotation),
        record_factory=_record_factory,
        units_code=4,
        config=_config(minimum_scale_evidence=1),
    )
    assert result.parsed_annotation_count == 1
    assert result.resolved_annotation_count == 1
    assert result.annotation_records[0].text == "27N8x170"


def test_proxy_adapter_total_count_annotation_accepts_br_closer() -> None:
    """Real data: some labels close with '(общо N бр.)' instead of '(общо N)'."""

    tokens = _total_count_tokens(1, 25, 481, 22, closer="бр.)")
    annotation = _FakeEntity(
        "ACAD_PROXY_ENTITY",
        handle="A-BR",
        layer="ARM_KOLONI",
        virtual=tuple(
            _FakeEntity("TEXT", text=token, insert=(index * 10.0, 10.0, 0.0))
            for index, token in enumerate(tokens)
        ),
    )
    result = extract_acad_proxy_rebar(
        (_geometry_proxy("G-BR", 481.0, layer="ARM_KOLONI"), annotation),
        record_factory=_record_factory,
        units_code=4,
        config=_config(minimum_scale_evidence=1),
    )
    assert result.parsed_annotation_count == 1
    assert result.resolved_annotation_count == 1
    assert result.annotation_records[0].text == "22N25x481"


def test_proxy_adapter_total_count_annotation_falls_back_to_declared_length() -> None:
    """Real-world case (2026-09-10, owner-confirmed): the koloni dialect's
    own drawn geometry often does not sum to exactly the declared length
    (duplicate segments in the proxy graphic -- see KNOWN_LIMITATIONS
    item 3). The label is a SELF-CONTAINED bar (its own text AND its own
    geometry in one proxy, same as the Gredi dialect) -- when the exact
    match fails, the declared length is trusted directly, and the bar's own
    (non-exclusive) geometry is still attached for reference."""

    label_tokens = _total_count_tokens(3, 8, 170, 26)
    self_contained = _FakeEntity(
        "ACAD_PROXY_ENTITY",
        handle="SELF-KOLONI",
        layer="ARM_KOLONI",
        virtual=(
            *(
                _FakeEntity("TEXT", text=token, insert=(index * 10.0, 10.0, 0.0))
                for index, token in enumerate(label_tokens)
            ),
            # Deliberately wrong sum (180cm, not the declared 170cm) --
            # mirrors the real duplicate-geometry artifact where the drawn
            # segments never land on the exact declared total.
            _FakeEntity("POLYLINE", points=((0.0, 0.0, 0.0), (90.0, 0.0, 0.0))),
            _FakeEntity("POLYLINE", points=((0.0, 0.0, 0.0), (90.0, 0.0, 0.0))),
        ),
    )
    result = extract_acad_proxy_rebar(
        (self_contained,),
        record_factory=_record_factory,
        units_code=4,
        config=_config(minimum_scale_evidence=1),
    )
    assert result.parsed_annotation_count == 1
    assert result.resolved_annotation_count == 1
    assert result.declared_length_annotation_count == 1
    record = result.annotation_records[0]
    assert record.text == "26N8x170"
    assert record.raw["association_code"] == PROXY_DECLARED_LENGTH_CODE
    assert record.raw["exact_total_cm"] == 170.0
    assert record.raw["geometry_linked"] is False
    # Reference-only: the bar's own (non-exclusive) geometry is still surfaced.
    assert len(record.raw["proxy_geometry_handles"]) == 2


def _column_group_header(text: str, insert: tuple[float, float, float]) -> _FakeEntity:
    return _FakeEntity("TEXT", text=text, insert=insert)


def test_proxy_adapter_multiplies_koloni_total_by_column_group_header() -> None:
    """Owner-confirmed 2026-09-10: "(общо N)" is declared for ONE column;
    several physical columns sharing an identical cross-section are titled
    with one "K.., K.. (WxH) - Nбр." plain-text header instead of repeating
    the detail. The resolved total must be multiplied by that "N бр.」."""

    tokens = _total_count_tokens(1, 25, 481, 22)
    annotation = _FakeEntity(
        "ACAD_PROXY_ENTITY",
        handle="A-GROUP",
        layer="ARM_KOLONI",
        virtual=tuple(
            _FakeEntity("TEXT", text=token, insert=(index * 10.0, -100.0, 0.0))
            for index, token in enumerate(tokens)
        ),
    )
    header = _column_group_header(
        "%%UK73, K80, K83, K88 (25/55) - 4бр.", (0.0, -80.0, 0.0)
    )
    result = extract_acad_proxy_rebar(
        (header, _geometry_proxy("G-GROUP", 481.0, layer="ARM_KOLONI"), annotation),
        record_factory=_record_factory,
        units_code=4,
        config=_config(minimum_scale_evidence=1),
    )
    assert result.parsed_annotation_count == 1
    assert result.resolved_annotation_count == 1
    assert result.column_group_multiplied_count == 1
    record = result.annotation_records[0]
    assert record.text == "88N25x481"
    assert record.raw["column_group_count"] == 4
    assert record.raw["column_group_source_count"] == 22
    assert record.raw["column_group_label"] == "%%UK73, K80, K83, K88 (25/55) - 4бр."


def test_proxy_adapter_column_group_header_of_one_leaves_count_unchanged() -> None:
    """A "1бр." header (a single, un-shared column) must not alter anything."""

    tokens = _total_count_tokens(1, 25, 481, 22)
    annotation = _FakeEntity(
        "ACAD_PROXY_ENTITY",
        handle="A-SOLO",
        layer="ARM_KOLONI",
        virtual=tuple(
            _FakeEntity("TEXT", text=token, insert=(index * 10.0, -100.0, 0.0))
            for index, token in enumerate(tokens)
        ),
    )
    header = _column_group_header("%%UK40 - 1бр.", (0.0, -80.0, 0.0))
    result = extract_acad_proxy_rebar(
        (header, _geometry_proxy("G-SOLO", 481.0, layer="ARM_KOLONI"), annotation),
        record_factory=_record_factory,
        units_code=4,
        config=_config(minimum_scale_evidence=1),
    )
    assert result.column_group_multiplied_count == 0
    assert result.annotation_records[0].text == "22N25x481"


def test_proxy_adapter_column_group_header_never_applies_outside_koloni_layer() -> None:
    """Double-gate safety: even a nearby "N бр." header text must not affect
    the unrelated GA_X/gredi "leading count" grammar or a non-arm_koloni
    layer, since the "за една колона" convention is koloni-specific."""

    header = _column_group_header("%%UK73, K80 (25/55) - 4бр.", (0.0, -80.0, 0.0))

    # 1) LEAD_COUNT grammar (GA_X) sitting right under the same header.
    lead_result = extract_acad_proxy_rebar(
        (
            header,
            _geometry_proxy("G-LEAD", 100.0, layer="GA_X"),
            _annotation_proxy("A-LEAD", 1, 100.0, layer="GA_X"),
        ),
        record_factory=_record_factory,
        units_code=4,
        config=_config(),
    )
    assert lead_result.column_group_multiplied_count == 0
    assert lead_result.annotation_records[0].text == "3N14x100"

    # 2) TRAILING_TOTAL_COUNT grammar but on a non-ARM_KOLONI layer.
    tokens = _total_count_tokens(1, 25, 481, 22)
    other_layer_annotation = _FakeEntity(
        "ACAD_PROXY_ENTITY",
        handle="A-OTHER-LAYER",
        layer="C_ARM_GREDI",
        virtual=tuple(
            _FakeEntity("TEXT", text=token, insert=(index * 10.0, -100.0, 0.0))
            for index, token in enumerate(tokens)
        ),
    )
    other_layer_result = extract_acad_proxy_rebar(
        (
            header,
            _geometry_proxy("G-OTHER-LAYER", 481.0, layer="C_ARM_GREDI"),
            other_layer_annotation,
        ),
        record_factory=_record_factory,
        units_code=4,
        config=_config(minimum_scale_evidence=1),
    )
    assert other_layer_result.column_group_multiplied_count == 0
    assert other_layer_result.annotation_records[0].text == "22N25x481"


def _column_group_box(
    x0: float, x1: float, y0: float, y1: float, *, layer: str = "ARM_KOLONI"
) -> _FakeEntity:
    return _FakeEntity(
        "LWPOLYLINE",
        layer=layer,
        points=((x0, y0, 0.0), (x1, y0, 0.0), (x1, y1, 0.0), (x0, y1, 0.0)),
        closed=True,
    )


def test_proxy_adapter_column_group_box_wins_over_a_closer_neighbouring_header() -> None:
    """Owner-confirmed 2026-09-12, real case: an annotation drawn INSIDE one
    column-group's own detail box was 14 drawing units (under 2%) closer,
    by raw text distance, to a NEIGHBOURING group's header -- silently
    halving its true quantity (50 instead of 150 pieces). When the
    annotation's anchor falls inside a detected box, that box's own header
    must win outright, even though the neighbouring header is nominally
    nearer to the raw annotation point."""

    # header_far sits right next to the annotation (raw text distance 10);
    # header_near_by_box sits far away (raw text distance 1000), but the
    # box the annotation is actually drawn inside is centered right on top
    # of it (box-center distance 0 vs 1010.05) -- exactly the real-case
    # shape: a near-tie by raw text distance that the box resolves cleanly.
    header_far = _column_group_header("%%UK30, K31 (35/90) - 2бр.", (10.0, 0.0, 0.0))
    header_near_by_box = _column_group_header(
        "%%UK75, K76, K77, K84, K85, K86 (25/50) - 6бр.", (-1000.0, 0.0, 0.0)
    )
    box = _column_group_box(-2000.0, 0.0, -200.0, 200.0)

    tokens = _total_count_tokens(1, 8, 150, 25)
    annotation = _FakeEntity(
        "ACAD_PROXY_ENTITY",
        handle="A-BOX",
        layer="ARM_KOLONI",
        virtual=tuple(
            _FakeEntity("TEXT", text=token, insert=(0.0 + index * 0.01, 0.0, 0.0))
            for index, token in enumerate(tokens)
        ),
    )
    result = extract_acad_proxy_rebar(
        (
            header_far,
            header_near_by_box,
            box,
            _geometry_proxy("G-BOX", 150.0, layer="ARM_KOLONI"),
            annotation,
        ),
        record_factory=_record_factory,
        units_code=4,
        config=_config(minimum_scale_evidence=1),
    )
    assert result.column_group_multiplied_count == 1
    record = result.annotation_records[0]
    assert record.raw["column_group_count"] == 6
    assert record.raw["column_group_label"] == (
        "%%UK75, K76, K77, K84, K85, K86 (25/50) - 6бр."
    )
    assert record.text == "150N8x150"


def test_proxy_adapter_small_closed_shape_is_not_treated_as_a_group_box() -> None:
    """A small closed rectangle (e.g. a stirrup icon's own outline) must
    NOT be treated as a group detail box -- see
    ``_COLUMN_GROUP_BOX_MIN_HEIGHT``/``_COLUMN_GROUP_BOX_MIN_WIDTH``: real
    per-position icons measured under 190 in both dimensions in the real
    file, real group boxes measured 440-502 x ~1075-1080. This is a
    generic "clearly bigger than an icon" gate, not a match against one
    specific size."""

    header_near = _column_group_header("%%UK30, K31 (35/90) - 2бр.", (10.0, 0.0, 0.0))
    header_far = _column_group_header(
        "%%UK75, K76, K77, K84, K85, K86 (25/50) - 6бр.", (-1000.0, 0.0, 0.0)
    )
    # A closed rectangle that geometrically CONTAINS the annotation, but is
    # far below both size gates (80x80, vs the >=100 width / >=350 height
    # required) -- must be ignored entirely, not just "the wrong box".
    tiny_box = _column_group_box(-40.0, 40.0, -40.0, 40.0)

    tokens = _total_count_tokens(1, 8, 150, 25)
    annotation = _FakeEntity(
        "ACAD_PROXY_ENTITY",
        handle="A-TINY-BOX",
        layer="ARM_KOLONI",
        virtual=tuple(
            _FakeEntity("TEXT", text=token, insert=(0.0 + index * 0.01, 0.0, 0.0))
            for index, token in enumerate(tokens)
        ),
    )
    result = extract_acad_proxy_rebar(
        (
            header_near,
            header_far,
            tiny_box,
            _geometry_proxy("G-TINY-BOX", 150.0, layer="ARM_KOLONI"),
            annotation,
        ),
        record_factory=_record_factory,
        units_code=4,
        config=_config(minimum_scale_evidence=1),
    )
    # No usable box -> falls back to plain nearest-header-by-text-distance,
    # which is header_far (closer to the annotation's raw anchor).
    assert result.column_group_multiplied_count == 1
    record = result.annotation_records[0]
    assert record.raw["column_group_count"] == 2
    assert record.raw["column_group_label"] == "%%UK30, K31 (35/90) - 2бр."


def test_proxy_adapter_derives_declared_shape_from_arc_verified_stirrup_segments() -> None:
    """Owner-requested 2026-09-10 ("не видях чужда форма... и са доста добре
    изчертани"): the designer's own bend-segment breakdown (already trusted
    for the koloni "(общо N)" grammar) can safely prove exact PRODUCTION
    segments once shape FAMILY is independently confirmed by real bend-arc
    geometry -- never from the declared text alone (see
    PROXY_DECLARED_SHAPE_CODE). Real values: "N8x170 (общо 26)" with
    trailing segments 51,26,51,26,8,8 -- width=51, height=26, hook=8 closes
    2*(51+26)+2*8=170 exactly, matching the owner-confirmed real drawing."""

    label_tokens = _total_count_tokens(3, 8, 170, 26)
    trailing_segments = ("51", "26", "51", "26", "8", "8")
    text_fragments = tuple(
        _FakeEntity("TEXT", text=token, insert=(index * 10.0, 10.0, 0.0))
        for index, token in enumerate((*label_tokens, *trailing_segments))
    )
    # Five distinct small-radius corner arcs (genuine bends) plus one
    # decorative "bar end" marker drawn as two ARCs tracing the SAME two
    # endpoints in opposite directions (real pattern measured on the actual
    # koloni drawing -- see _count_structural_bend_arcs docstring).
    corner_arcs = tuple(
        _FakeEntity(
            "ARC", center=(index * 50.0, 0.0, 0.0), radius=4.8,
            start_angle=0.0, end_angle=90.0,
        )
        for index in range(5)
    )
    decoration_pair = (
        _FakeEntity(
            "ARC", center=(500.0, 0.0, 0.0), radius=7.11,
            start_angle=90.0, end_angle=-90.0,
        ),
        _FakeEntity(
            "ARC", center=(500.0, 0.0, 0.0), radius=7.11,
            start_angle=-90.0, end_angle=90.0,
        ),
    )
    self_contained = _FakeEntity(
        "ACAD_PROXY_ENTITY",
        handle="A-STIRRUP-SHAPE",
        layer="ARM_KOLONI",
        virtual=(*text_fragments, *corner_arcs, *decoration_pair),
    )
    result = extract_acad_proxy_rebar(
        (self_contained,),
        record_factory=_record_factory,
        units_code=4,
        config=_config(minimum_scale_evidence=1),
    )
    assert result.resolved_annotation_count == 1
    assert result.declared_shape_evidence_count == 1
    record = result.annotation_records[0]
    assert record.raw["declared_shape_code"] == "F006"
    # (width, height, hook) -- vertical_shape_resolver's own native order,
    # matching the pre-existing dimension-hint path
    # (_apply_dimension_derived_production_sizes) and the UI's own reorder
    # step (ui/main_window.py::_shape_dimension_overlay_input). Anything
    # that needs master_engine_v031's (hook, side_a, side_b) order must
    # reorder at its own point of use, not here.
    assert record.raw["declared_shape_segments_cm"] == [51.0, 26.0, 8.0]
    assert record.raw["declared_shape_source"] == PROXY_DECLARED_SHAPE_CODE
    assert record.raw["declared_shape_bend_arc_count"] == 5


def test_proxy_adapter_zero_bend_arcs_never_asserts_the_bent_shape() -> None:
    """Owner-confirmed 2026-09-10: even when the declared segments would
    ALSO solve the F006 equation numerically, zero genuine bend arcs is
    independent proof the bar has NO bend at all -- it must never resolve
    as the numerically-coincidental bent shape. A 6-segment split with
    zero bend arcs never occurs in real data (validated: 165/165 real
    positions across two drawings always paired 5/6 segments with 5 bend
    arcs, never 0) -- this exact combination is deliberately synthetic,
    outside the two recognised zero-arc families (F001: one segment,
    F002: three), so it must fail closed (no shape asserted) rather than
    guess. Declared/dimension text may prove exact segments but must
    never by itself prove a BENT shape family (mirrors the engine's
    existing rule in batch_geometry_report.py: "без форма не може да се
    твърди какво е производството")."""

    label_tokens = _total_count_tokens(3, 8, 170, 26)
    trailing_segments = ("51", "26", "51", "26", "8", "8")
    text_fragments = tuple(
        _FakeEntity("TEXT", text=token, insert=(index * 10.0, 10.0, 0.0))
        for index, token in enumerate((*label_tokens, *trailing_segments))
    )
    # Only the decorative "bar end" marker -- zero genuine bend arcs.
    decoration_pair = (
        _FakeEntity(
            "ARC", center=(500.0, 0.0, 0.0), radius=7.11,
            start_angle=90.0, end_angle=-90.0,
        ),
        _FakeEntity(
            "ARC", center=(500.0, 0.0, 0.0), radius=7.11,
            start_angle=-90.0, end_angle=90.0,
        ),
    )
    self_contained = _FakeEntity(
        "ACAD_PROXY_ENTITY",
        handle="A-NO-BEND-EVIDENCE",
        layer="ARM_KOLONI",
        virtual=(*text_fragments, *decoration_pair),
    )
    result = extract_acad_proxy_rebar(
        (self_contained,),
        record_factory=_record_factory,
        units_code=4,
        config=_config(minimum_scale_evidence=1),
    )
    assert result.resolved_annotation_count == 1
    assert result.declared_shape_evidence_count == 0
    record = result.annotation_records[0]
    assert record.raw.get("declared_shape_code") is None
    assert record.raw["declared_shape_bend_arc_count"] == 0


def _three_part_bar_proxy(
    handle: str,
    *,
    label: tuple[str, ...],
    trailing: tuple[str, ...],
    legs: tuple[tuple[tuple[float, float, float], ...], ...],
) -> _FakeEntity:
    """A three-part bar drawn as polyline legs plus the decorative end-circle
    this dialect always adds -- a full circle split into two mirrored ARCs,
    which ``_count_structural_bend_arcs`` cancels out to zero."""

    text_fragments = tuple(
        _FakeEntity("TEXT", text=token, insert=(index * 10.0, 900.0, 0.0))
        for index, token in enumerate((*label, *trailing))
    )
    leg_entities = tuple(_FakeEntity("POLYLINE", points=points) for points in legs)
    decoration_pair = (
        _FakeEntity(
            "ARC", center=(500.0, 900.0, 0.0), radius=7.11,
            start_angle=90.0, end_angle=-90.0,
        ),
        _FakeEntity(
            "ARC", center=(500.0, 900.0, 0.0), radius=7.11,
            start_angle=-90.0, end_angle=90.0,
        ),
    )
    return _FakeEntity(
        "ACAD_PROXY_ENTITY",
        handle=handle,
        layer="ARM_KOLONI",
        virtual=(*text_fragments, *leg_entities, *decoration_pair),
    )


def test_proxy_adapter_reduction_splice_resolves_as_f002_not_f001() -> None:
    """Owner-confirmed real case 2026-09-10 (live-app resolution:
    "14N20x451" -> F002, segments 310+21+120): a main bar whose declared
    length is split into three parts by a coupler/diameter-reduction
    marker (e.g. "150-21-310") carries zero bend ARCs but is the engine's
    own dedicated F002 "ПРАВ ПРЪТ С РЕДУКЦИЯ", which needs all three
    segments preserved -- NOT F001 with just the total length (an earlier
    version of this fix wrongly collapsed them, losing the reduction
    detail entirely).

    The F002 verdict now rests on the DRAWN geometry as well as the text:
    the engine defines F002 as "два хоризонтални прави участъка и
    НАКЛОНЕНА редукция между тях", and the legs below are measured from
    the real drawing -- 150 / 20.62 / 310, the middle inclined 14 degrees
    off the two parallel outer legs. Zero ARCs does NOT mean straight in
    this dialect; see tests/test_proxy_three_leg_shape_from_geometry.py.
    """

    result = extract_acad_proxy_rebar(
        (
            _three_part_bar_proxy(
                "A-REDUCTION-SPLICE",
                label=_total_count_tokens(1, 25, 481, 22),
                trailing=("150", "21", "310"),
                legs=(
                    ((0.0, 0.0, 0.0), (0.0, 150.0, 0.0)),
                    ((0.0, 150.0, 0.0), (5.0, 170.0, 0.0)),
                    ((5.0, 170.0, 0.0), (5.0, 480.0, 0.0)),
                ),
            ),
        ),
        record_factory=_record_factory,
        units_code=4,
        config=_config(minimum_scale_evidence=1),
    )
    assert result.resolved_annotation_count == 1
    assert result.declared_shape_evidence_count == 1
    record = result.annotation_records[0]
    assert record.raw["declared_shape_bend_arc_count"] == 0
    assert record.raw["declared_shape_code"] == "F002"
    assert record.raw["declared_shape_segments_cm"] == [150.0, 21.0, 310.0]


def test_proxy_adapter_three_part_u_bar_resolves_as_f003_not_f002() -> None:
    """Real shaibi class, owner-reported 2026-09-13: twenty positions in
    ``38_83_shaibi_+18.18 do +21.48.dxf`` declare three parts and carry zero
    bend ARCs, yet their own drawn legs meet at 90 degrees. They are F003
    U-bars ("и двете външни рамена са по-къси от средния сегмент") and were
    being exported as straight spliced bars.

    Legs use the sheet's own 2-units-per-cm scale, as measured."""

    result = extract_acad_proxy_rebar(
        (
            _three_part_bar_proxy(
                "A-U-BAR",
                label=_total_count_tokens(1, 12, 415, 34),
                trailing=("30", "355", "30"),
                legs=(
                    ((0.0, 0.0, 0.0), (0.0, 60.0, 0.0)),
                    ((0.0, 60.0, 0.0), (710.0, 60.0, 0.0)),
                    ((710.0, 60.0, 0.0), (710.0, 0.0, 0.0)),
                ),
            ),
        ),
        record_factory=_record_factory,
        units_code=4,
        config=_config(minimum_scale_evidence=1),
    )
    assert result.declared_shape_evidence_count == 1
    record = result.annotation_records[0]
    assert record.raw["declared_shape_bend_arc_count"] == 0
    assert record.raw["declared_shape_code"] == "F003"
    assert record.raw["declared_shape_segments_cm"] == [30.0, 355.0, 30.0]


def test_proxy_adapter_three_part_declaration_without_geometry_stays_unknown() -> None:
    """Fail-closed. With no drawn legs there is nothing to tell a reduction
    splice apart from a U-bar, so the position reaches the operator rather
    than defaulting to either."""

    result = extract_acad_proxy_rebar(
        (
            _three_part_bar_proxy(
                "A-NO-LEGS",
                label=_total_count_tokens(1, 25, 481, 22),
                trailing=("150", "21", "310"),
                legs=(),
            ),
        ),
        record_factory=_record_factory,
        units_code=4,
        config=_config(minimum_scale_evidence=1),
    )
    assert result.resolved_annotation_count == 1
    assert result.declared_shape_evidence_count == 0
    assert result.annotation_records[0].raw.get("declared_shape_code") is None


def _shaibi_wall_label(text: str, insert: tuple[float, float, float]) -> _FakeEntity:
    return _FakeEntity("TEXT", text=text, insert=insert, layer="C_TXT_shaibi")


def _shaibi_total_count_proxy(
    handle: str, position: int, diameter: int, length: int, count: int,
    *, insert_x: float = 0.0,
) -> _FakeEntity:
    tokens = _total_count_tokens(position, diameter, length, count)
    return _FakeEntity(
        "ACAD_PROXY_ENTITY",
        handle=handle,
        layer="ARM_SHAIBI",
        virtual=tuple(
            _FakeEntity("TEXT", text=token, insert=(insert_x + index * 10.0, -100.0, 0.0))
            for index, token in enumerate(tokens)
        ),
    )


def test_proxy_adapter_multiplies_shaibi_total_by_matching_wall_section_group() -> None:
    """Owner-confirmed 2026-09-10, real case: "Ш 4 - 25/350" and
    "Ш 7 - 25/350" (identical section, close together) share ONE drawn
    detail -- "Ш7 = същата като Ш4, еднакви са". The resolved total must be
    multiplied by the number of walls sharing that section."""

    annotation = _shaibi_total_count_proxy("A-WALL-GROUP", 1, 10, 390, 20, insert_x=0.0)
    labels = (
        _shaibi_wall_label("Ш 4 - 25/350", (5.0, 20.0, 0.0)),
        _shaibi_wall_label("Ш 7 - 25/350", (5.0, 25.0, 0.0)),
    )
    result = extract_acad_proxy_rebar(
        (*labels, annotation),
        record_factory=_record_factory,
        units_code=4,
        config=_config(minimum_scale_evidence=1),
    )
    assert result.parsed_annotation_count == 1
    assert result.resolved_annotation_count == 1
    assert result.shaibi_wall_group_multiplied_count == 1
    record = result.annotation_records[0]
    assert record.text == "40N10x390"
    assert record.raw["count"] == 40


def test_proxy_adapter_shaibi_solo_wall_leaves_count_unchanged() -> None:
    """A single, un-shared wall label must not alter anything."""

    annotation = _shaibi_total_count_proxy("A-WALL-SOLO", 1, 10, 390, 20, insert_x=0.0)
    label = _shaibi_wall_label("Ш 3 - 25/600", (5.0, 20.0, 0.0))
    result = extract_acad_proxy_rebar(
        (label, annotation),
        record_factory=_record_factory,
        units_code=4,
        config=_config(minimum_scale_evidence=1),
    )
    assert result.shaibi_wall_group_multiplied_count == 0
    assert result.annotation_records[0].text == "20N10x390"


def test_proxy_adapter_collapses_shaibi_visual_duplicate_shown_twice() -> None:
    """Owner-confirmed real case: "N16x323 (общо 4)" belongs ONLY to Ш3, but
    the drawing shows it a second time near an unrelated wall group
    (both ends of one bend labelled separately) -- counting both would
    double-count a single physical bar. Two annotations with IDENTICAL
    declared (diameter, length, count) sitting close together must
    collapse to one, keeping the copy closest to a real wall label."""

    real_copy = _shaibi_total_count_proxy("A-N16-REAL", 7, 16, 323, 4, insert_x=1000.0)
    duplicate_copy = _shaibi_total_count_proxy("A-N16-DUP", 7, 16, 323, 4, insert_x=1000.2)
    solo_label = _shaibi_wall_label("Ш 3 - 25/600", (1005.0, -80.0, 0.0))
    result = extract_acad_proxy_rebar(
        (solo_label, real_copy, duplicate_copy),
        record_factory=_record_factory,
        units_code=4,
        config=_config(minimum_scale_evidence=1),
    )
    assert result.resolved_annotation_count == 1
    assert result.annotation_records[0].text == "4N16x323"
    assert result.annotation_records[0].raw["count"] == 4


def test_proxy_adapter_drops_shaibi_entry_far_from_any_wall_label() -> None:
    """Owner-confirmed real pattern: some details are repeated as a pure
    visual reference copy in a floor-plan callout area far from any real
    "Ш<N> - W/H" wall label (measured real gap: legitimate details sit
    100-1400 units from their label, reference-only copies sit 2500-4500
    away). A copy far beyond that gap must be dropped, not miscounted."""

    near_copy = _shaibi_total_count_proxy("A-NEAR", 1, 10, 390, 20, insert_x=100.0)
    far_copy = _shaibi_total_count_proxy("A-FAR", 1, 10, 390, 20, insert_x=5000.0)
    label = _shaibi_wall_label("Ш 3 - 25/600", (105.0, -80.0, 0.0))
    result = extract_acad_proxy_rebar(
        (label, near_copy, far_copy),
        record_factory=_record_factory,
        units_code=4,
        config=_config(minimum_scale_evidence=1),
    )
    assert result.resolved_annotation_count == 1
    assert result.annotation_records[0].text == "20N10x390"


def test_proxy_adapter_drops_shaibi_lead_count_duplicate_of_total_sibling() -> None:
    """Owner-confirmed real pair: "14N12x317/10" (LEAD_COUNT grammar) is a
    redundant per-leg rendering of the SAME physical bar as its sibling
    "14 N12x317 (общо 28)" (28 = 14 x 2 legs). Counting both double-counts
    one detail; the LEAD_COUNT rendering must be dropped entirely for
    arm_shaibi, never just left unmultiplied."""

    lead_count_entry = _FakeEntity(
        "ACAD_PROXY_ENTITY",
        handle="A-LEAD-DUP",
        layer="ARM_SHAIBI",
        virtual=tuple(
            _FakeEntity("TEXT", text=token, insert=(index * 10.0, -100.0, 0.0))
            for index, token in enumerate(("8", "14", "N", "12", "x", "317", "/", "10"))
        ),
    )
    total_entry = _shaibi_total_count_proxy("A-TOTAL", 8, 12, 317, 28, insert_x=0.0)
    label = _shaibi_wall_label("Ш 9 - 35/510", (5.0, -80.0, 0.0))
    result = extract_acad_proxy_rebar(
        (label, lead_count_entry, total_entry),
        record_factory=_record_factory,
        units_code=4,
        config=_config(minimum_scale_evidence=1),
    )
    assert result.resolved_annotation_count == 1
    assert result.annotation_records[0].text == "28N12x317"
    assert result.annotation_records[0].raw["count"] == 28


def test_proxy_adapter_shaibi_wall_group_never_applies_outside_arm_shaibi_layer() -> None:
    """Double-gate safety: a nearby "Ш<N> - W/H" label and a LEAD_COUNT
    entry on an unrelated layer must never be affected by any shaibi-only
    mechanism (multiplier, dedup, or the LEAD_COUNT drop)."""

    label = _shaibi_wall_label("Ш 4 - 25/350", (5.0, 20.0, 0.0))
    other_label = _shaibi_wall_label("Ш 7 - 25/350", (5.0, 25.0, 0.0))
    lead_result = extract_acad_proxy_rebar(
        (
            label,
            other_label,
            _geometry_proxy("G-LEAD", 100.0, layer="GA_X"),
            _annotation_proxy("A-LEAD", 1, 100.0, layer="GA_X"),
        ),
        record_factory=_record_factory,
        units_code=4,
        config=_config(),
    )
    assert lead_result.shaibi_wall_group_multiplied_count == 0
    assert lead_result.annotation_records[0].text == "3N14x100"


def test_proxy_provenance_is_reference_only_and_does_not_create_ownership_conflict(
    tmp_path: Path,
) -> None:
    engine_path = (
        Path(__file__).resolve().parents[1]
        / "engine_updates"
        / "lion-rebar-master_v0.3.12_POSITION_KEY_F001_CALIBRATION_PRODUCTION_DRAFT.lionengine"
    )
    engine, _manifest, _names = inspect_engine_bundle(engine_path)
    geometry = DxfEntityRecord(
        handle="PXG_SHARED_0_0",
        entity_type="POLYLINE",
        layer="ARM_PROXY_GA_X",
        geometry={
            "points": [[0.0, 0.0, 0.0], [100.0, 0.0, 0.0]],
            "bulges": [0.0, 0.0],
            "closed": False,
        },
    )

    def annotation(handle: str, position: int) -> DxfEntityRecord:
        return DxfEntityRecord(
            handle=handle,
            entity_type="BLOCK_ATTR_TEXT",
            layer="GA_X",
            text="2N10x100",
            geometry={"insert": [1000.0, 1000.0, 0.0]},
            raw={
                "source": PROXY_ANNOTATION_SOURCE,
                "association_code": PROXY_ASSOCIATION_CODE,
                "parent_proxy_handle": handle.replace("PXANN_", ""),
                "proxy_geometry_parent_handle": "PXG_SHARED",
                "proxy_geometry_handles": [geometry.handle],
                "position_key": position,
                "spacing_cm": 10.0,
                "scale_factor": 1.0,
                "exact_total_cm": 100.0,
                "predecessor_distance": 1,
                "modelspace_distance": 1,
            },
        )

    report = build_batch_geometry_report(
        [geometry, annotation("PXANN_A1", 1), annotation("PXANN_A2", 2)],
        tmp_path,
        project_name="PROXY_REFERENCE_ONLY",
        generated_at=datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc),
        shape_definitions=engine.shapes,
        control_tests=engine.tests,
        policy=engine.settings,
    )
    payload = json.loads(report.json_path.read_text(encoding="utf-8"))

    assert report.geometry_strong_count == 2
    assert report.proxy_parent_provenance_count == 2
    assert report.ownership_conflict_count == 0
    assert all(value["ownership_reference_only"] for value in payload["rows"])
    assert all(value["master_shape_code"] == "F001" for value in payload["rows"])


def test_batch_report_surfaces_reference_geometry_when_no_candidate_found(
    tmp_path: Path,
) -> None:
    """Owner-requested 2026-09-11/12 ("искам програмата да е ТОП, без
    компромиси"): confirmed live -- an operator previously had to click
    through every individual proxy fragment by hand for a "НЯМА КАНДИДАТ"
    position, because the annotation's own attached reference geometry
    (already known, via PROXY_TEXT_DECLARED_LENGTH) was never surfaced to
    the review row. When NO real geometric candidate is found at all,
    ``reference_geometry_handles`` must carry that already-known geometry
    -- and ``candidate_handles`` must stay empty (this is never promoted
    to a proven match)."""

    engine_path = (
        Path(__file__).resolve().parents[1]
        / "engine_updates"
        / "lion-rebar-master_v0.3.12_POSITION_KEY_F001_CALIBRATION_PRODUCTION_DRAFT.lionengine"
    )
    engine, _manifest, _names = inspect_engine_bundle(engine_path)

    # Deliberately no real LINE/POLYLINE/ARC entity provided for
    # "PXG_PHANTOM_0_0" -- find_geometry_candidates must find nothing.
    annotation = DxfEntityRecord(
        handle="PXANN_B1",
        entity_type="BLOCK_ATTR_TEXT",
        layer="ARM_KOLONI",
        text="26N8x170",
        geometry={"insert": [2000.0, 2000.0, 0.0]},
        raw={
            "source": PROXY_ANNOTATION_SOURCE,
            "association_code": PROXY_DECLARED_LENGTH_CODE,
            "parent_proxy_handle": "B1",
            "proxy_geometry_parent_handle": "B1",
            "proxy_geometry_handles": ["PXG_PHANTOM_0_0", "PXG_PHANTOM_1_0"],
            "position_key": 3,
            "count": 26,
            "diameter_mm": 8.0,
            "length_cm": 170.0,
            "spacing_cm": None,
            "scale_factor": None,
            "exact_total_cm": 170.0,
            "predecessor_distance": 0,
            "modelspace_distance": 0,
            "geometry_linked": False,
            "ownership_mode": "REFERENCE_ONLY",
        },
    )

    report = build_batch_geometry_report(
        [annotation],
        tmp_path,
        project_name="PROXY_REFERENCE_GEOMETRY_FALLBACK",
        generated_at=datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc),
        shape_definitions=engine.shapes,
        control_tests=engine.tests,
        policy=engine.settings,
    )
    payload = json.loads(report.json_path.read_text(encoding="utf-8"))
    rows = payload["rows"]
    assert len(rows) == 1
    row = rows[0]
    assert not row.get("candidate_handles")
    assert row.get("reference_geometry_handles") == ["PXG_PHANTOM_0_0", "PXG_PHANTOM_1_0"]


def test_reference_geometry_fallback_prefers_own_geometry_over_far_candidate() -> None:
    """Owner-confirmed 2026-09-12, verified against BOTH real files: a
    FAR_ASSOCIATION candidate never coincides with the annotation's own
    reference geometry (0 of 33 real cases in koloni+shaibi) -- every one
    points at a completely unrelated proxy elsewhere on the sheet. Real
    label-to-geometry pairs in this dialect are always drawn close
    together. So when a row's ONLY candidate is FAR_ASSOCIATION-flagged,
    the fallback must STILL populate reference_geometry_handles (the
    quick-preview default in review_center.py then prefers it) -- unlike
    a row with a real, non-far candidate, which must NOT get this field."""

    far_row = {
        "annotation_handle": "PXANN_FAR",
        "candidate_handles": ["SOME_FAR_HANDLE"],
        "review_reason_codes": ["FAR_ASSOCIATION"],
    }
    real_candidate_row = {
        "annotation_handle": "PXANN_REAL",
        "candidate_handles": ["SOME_REAL_HANDLE"],
        "review_reason_codes": [],
    }
    rows = [far_row, real_candidate_row]
    annotations_by_handle = {
        "PXANN_FAR": DxfEntityRecord(
            handle="PXANN_FAR",
            entity_type="BLOCK_ATTR_TEXT",
            layer="ARM_KOLONI",
            text="22N25x481",
            geometry={"insert": [0.0, 0.0, 0.0]},
            raw={
                "association_code": PROXY_DECLARED_LENGTH_CODE,
                "proxy_geometry_handles": ["PXG_FAR_0_0", "PXG_FAR_1_0"],
            },
        ),
        "PXANN_REAL": DxfEntityRecord(
            handle="PXANN_REAL",
            entity_type="BLOCK_ATTR_TEXT",
            layer="ARM_KOLONI",
            text="10N25x481",
            geometry={"insert": [0.0, 0.0, 0.0]},
            raw={
                "association_code": PROXY_DECLARED_LENGTH_CODE,
                "proxy_geometry_handles": ["PXG_REAL_0_0"],
            },
        ),
    }

    applied = _apply_proxy_reference_geometry_fallback(
        rows, annotations_by_handle=annotations_by_handle
    )

    assert applied == 1
    assert far_row["reference_geometry_handles"] == ["PXG_FAR_0_0", "PXG_FAR_1_0"]
    # candidate_handles is never touched -- the far candidate stays
    # reachable via "Всички кандидати" even after this fallback also
    # attaches the more-trustworthy reference geometry.
    assert far_row["candidate_handles"] == ["SOME_FAR_HANDLE"]
    assert "reference_geometry_handles" not in real_candidate_row


def test_declared_shape_suggestion_overrides_unreliable_master_shape_code() -> None:
    """Owner-confirmed 2026-09-12, real case: two independent columns
    ("UK46,HK4" and "UK34,HK3") both weakly matched the SAME unrelated
    straight line on the generic "arm_koloni" layer (no per-diameter
    layer name for geometry_candidates.py's hard diameter check to
    reject against) -- both ended up with master_shape_code=F001
    (wrong) and matcher_blocker_codes containing
    GEOMETRY_OWNERSHIP_CONFLICT, silently hiding the correct,
    arc-verified F006 suggestion (8-76-21-76-21-8) the operator needed
    to notice the trap. A row with a genuinely reliable master_shape_code
    (no FAR_ASSOCIATION/GEOMETRY_OWNERSHIP_CONFLICT) must still be left
    alone -- this fix only targets proven-unreliable existing matches."""

    conflicted_row = {
        "annotation_handle": "PXANN_CONFLICT",
        "master_shape_code": "F001",
        "review_reason_codes": ["FAR_ASSOCIATION"],
        "matcher_blocker_codes": ["GEOMETRY_OWNERSHIP_CONFLICT", "FAR_ASSOCIATION"],
    }
    reliable_row = {
        "annotation_handle": "PXANN_RELIABLE",
        "master_shape_code": "F001",
        "review_reason_codes": [],
        "matcher_blocker_codes": [],
    }
    rows = [conflicted_row, reliable_row]
    annotations_by_handle = {
        "PXANN_CONFLICT": DxfEntityRecord(
            handle="PXANN_CONFLICT",
            entity_type="BLOCK_ATTR_TEXT",
            layer="ARM_KOLONI",
            text="50N8x210",
            geometry={"insert": [0.0, 0.0, 0.0]},
            raw={
                "declared_shape_source": PROXY_DECLARED_SHAPE_CODE,
                "declared_shape_code": "F006",
                "declared_shape_segments_cm": [76.0, 21.0, 8.0],
                "declared_shape_confidence": "HIGH",
                "declared_shape_bend_arc_count": 5,
            },
        ),
        "PXANN_RELIABLE": DxfEntityRecord(
            handle="PXANN_RELIABLE",
            entity_type="BLOCK_ATTR_TEXT",
            layer="ARM_KOLONI",
            text="10N8x210",
            geometry={"insert": [0.0, 0.0, 0.0]},
            raw={
                "declared_shape_source": PROXY_DECLARED_SHAPE_CODE,
                "declared_shape_code": "F006",
                "declared_shape_segments_cm": [76.0, 21.0, 8.0],
                "declared_shape_confidence": "HIGH",
                "declared_shape_bend_arc_count": 5,
            },
        ),
    }

    applied = _apply_proxy_declared_shape_suggestions(
        rows, annotations_by_handle=annotations_by_handle
    )

    assert applied == 1
    assert conflicted_row["suggested_shape_code"] == "F006"
    assert conflicted_row["suggested_segments_cm"] == [76.0, 21.0, 8.0]
    # master_shape_code/batch_status/matcher_disposition are never touched --
    # the operator still confirms this position manually either way.
    assert conflicted_row["master_shape_code"] == "F001"

    # A reliable row's master_shape_code is left alone -- no suggestion added.
    assert "suggested_shape_code" not in reliable_row
    assert reliable_row["master_shape_code"] == "F001"
