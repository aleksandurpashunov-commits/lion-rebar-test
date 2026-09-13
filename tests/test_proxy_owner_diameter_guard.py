"""Diameter rejection that needs no layer naming convention (rc44).

Owner-requested 2026-09-12: "само непознати неща да излизат за преглед, а не
елементарни грешки".

``geometry_candidates.py`` already rejects a candidate whose LAYER NAME
encodes a different diameter (RecognitionPolicy law 2). The arm_koloni /
arm_shaibi dialect puts every diameter on one generic layer, so that rule
can never fire there -- and a coincidental length match on unrelated Ø16
geometry was free to out-rank the truth for a declared Ø8 bar (real case:
"104N8x94", owner-caught 2026-09-12).

This dialect names its exploded geometry after the proxy that owns it
(``PXG_<parent>_<index>_0``), and that parent's own annotation declares a
diameter. Comparing those two restores the same rule without any layer
naming convention.
"""
from __future__ import annotations

from lion_rebar.dxf.geometry_candidates import (
    GeometryPrimitive,
    _proxy_declared_diameters,
    _proxy_owner_diameter_mismatch,
    _proxy_owner_handle,
)
from lion_rebar.dxf.models import DxfEntityRecord


def _annotation(parent: str, diameter: float | None) -> DxfEntityRecord:
    return DxfEntityRecord(
        handle=f"PXANN_{parent}",
        entity_type="BLOCK_ATTR_TEXT",
        layer="arm_koloni",
        text="x",
        geometry={"insert": [0.0, 0.0, 0.0]},
        raw={
            "source": "ACAD_PROXY_GRAPHIC_REBAR_ANNOTATION",
            "parent_proxy_handle": parent,
            "diameter_mm": diameter,
        },
    )


def _primitive(handle: str) -> GeometryPrimitive:
    return GeometryPrimitive(
        handle=handle,
        layer="ARM_PROXY_arm_koloni",
        entity_type="LINE",
        length_raw=100.0,
        segments=(),
        points=(),
        start=(0.0, 0.0),
        end=(100.0, 0.0),
    )


def test_owner_handle_is_read_from_the_exploded_geometry_handle() -> None:
    assert _proxy_owner_handle("PXG_9B6BFA_2_0") == "9B6BFA"
    assert _proxy_owner_handle("PXG_98E9FA_12_0") == "98E9FA"
    # Ordinary DXF handles carry no ownership claim.
    assert _proxy_owner_handle("9BD1FC") is None
    assert _proxy_owner_handle("") is None


def test_declared_diameters_are_collected_per_proxy_parent() -> None:
    entities = [_annotation("AAA", 8.0), _annotation("BBB", 16.0)]

    assert _proxy_declared_diameters(entities) == {"AAA": 8.0, "BBB": 16.0}


def test_a_parent_with_contradictory_diameters_proves_nothing() -> None:
    """Fail-closed: two annotations on one proxy claiming different
    diameters cannot be used to reject anything."""

    entities = [_annotation("AAA", 8.0), _annotation("AAA", 16.0)]

    assert _proxy_declared_diameters(entities) == {}


def test_unusable_diameter_values_are_ignored() -> None:
    entities = [
        _annotation("AAA", None),
        _annotation("BBB", 0.0),
        _annotation("CCC", -5.0),
        _annotation("DDD", 10.0),
    ]

    assert _proxy_declared_diameters(entities) == {"DDD": 10.0}


def test_geometry_owned_by_a_different_diameter_is_a_mismatch() -> None:
    owners = {"OWNER16": 16.0}
    shape = (_primitive("PXG_OWNER16_0_0"), _primitive("PXG_OWNER16_1_0"))

    assert _proxy_owner_diameter_mismatch(shape, 8.0, owners) is True


def test_geometry_owned_by_the_same_diameter_is_not_a_mismatch() -> None:
    owners = {"OWNER8": 8.0}
    shape = (_primitive("PXG_OWNER8_0_0"),)

    assert _proxy_owner_diameter_mismatch(shape, 8.0, owners) is False


def test_unknown_ownership_never_rejects() -> None:
    """Fail-open: a plain DXF handle, or a proxy whose annotation declares
    nothing, proves nothing about diameter and must not block a candidate."""

    owners = {"OWNER16": 16.0}

    assert _proxy_owner_diameter_mismatch((_primitive("9BD1FC"),), 8.0, owners) is False
    assert (
        _proxy_owner_diameter_mismatch((_primitive("PXG_UNKNOWN_0_0"),), 8.0, owners)
        is False
    )
    assert _proxy_owner_diameter_mismatch((_primitive("PXG_OWNER16_0_0"),), 8.0, {}) is False


def test_a_single_matching_piece_clears_the_whole_candidate() -> None:
    """Mixed ownership is not proof: if any piece could legitimately belong
    to this bar, the candidate survives and the normal scoring decides."""

    owners = {"OWNER16": 16.0, "OWNER8": 8.0}
    shape = (_primitive("PXG_OWNER16_0_0"), _primitive("PXG_OWNER8_0_0"))

    assert _proxy_owner_diameter_mismatch(shape, 8.0, owners) is False


def test_tolerance_matches_the_existing_layer_diameter_rule() -> None:
    owners = {"OWNER": 8.0}
    shape = (_primitive("PXG_OWNER_0_0"),)

    # Within 0.01 mm is the same bar, as in the layer-name rule.
    assert _proxy_owner_diameter_mismatch(shape, 8.005, owners) is False
    assert _proxy_owner_diameter_mismatch(shape, 8.2, owners) is True
