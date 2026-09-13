from __future__ import annotations

import pytest

from lion_rebar.manual_dimensions import (
    DIMENSION_SOURCE_ANNOTATION,
    DIMENSION_SOURCE_DXF,
    DIMENSION_SOURCE_MANUAL,
    length_mismatch_is_material,
    parse_manual_segment_lengths,
    resolve_manual_dimensions,
)


def test_annotation_length_remains_production_length_when_dxf_is_schematic():
    result = resolve_manual_dimensions(
        source=DIMENSION_SOURCE_ANNOTATION,
        declared_length_cm=1150,
        measured_segment_lengths_cm=(1120.0, 24.79),
        expected_segment_count=2,
    )

    assert result.production_length_cm == 1150
    assert result.measured_geometry_length_cm == pytest.approx(1144.79)
    assert result.production_segment_lengths_cm == ()
    assert result.geometry_difference_cm == pytest.approx(-5.21)


def test_manual_fiba_dimensions_override_schematic_dxf_lengths():
    result = resolve_manual_dimensions(
        source=DIMENSION_SOURCE_MANUAL,
        declared_length_cm=140,
        measured_segment_lengths_cm=(66.0, 18.0, 66.0),
        manual_segment_text="61 + 18 + 61",
        expected_segment_count=3,
    )

    assert result.production_segment_lengths_cm == (61.0, 18.0, 61.0)
    assert result.production_length_cm == 140
    assert result.measured_geometry_length_cm == 150
    assert result.geometry_difference_cm == 10


def test_dxf_source_keeps_measured_segments_for_f006():
    result = resolve_manual_dimensions(
        source=DIMENSION_SOURCE_DXF,
        declared_length_cm=130,
        measured_segment_lengths_cm=(34, 64, 34, 64, 20, 20),
        expected_segment_count=6,
    )

    assert result.production_length_cm == 236
    assert result.production_segment_lengths_cm == (34, 64, 34, 64, 20, 20)


def test_manual_segment_count_must_match_selected_geometry():
    with pytest.raises(ValueError, match="въведени 3"):
        resolve_manual_dimensions(
            source=DIMENSION_SOURCE_MANUAL,
            declared_length_cm=1150,
            measured_segment_lengths_cm=(1100, 50),
            manual_segment_text="1000 + 100 + 50",
            expected_segment_count=2,
        )


def test_parser_accepts_decimal_comma_and_common_separators():
    assert parse_manual_segment_lengths("61,5 + 18; 61,5") == (61.5, 18.0, 61.5)


def test_material_mismatch_uses_half_percent_or_one_centimetre():
    assert length_mismatch_is_material(1150, 1144.79) is False
    assert length_mismatch_is_material(140, 150) is True
