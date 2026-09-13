from __future__ import annotations

from lion_rebar.dxf.batch_geometry_report import _approved_row
from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.dxf.recognition_policy import RecognitionPolicy


def test_approved_manual_assignment_keeps_production_length_separate_from_dxf():
    annotation = DxfEntityRecord(
        handle="ANN",
        entity_type="BLOCK_ATTR_TEXT",
        layer="ARM_TXT",
        text="400N10x140",
    )
    approved = {
        "annotation_text": "400N10x140",
        "diameter_mm": 10,
        "declared_length_cm": 140,
        "production_length_cm": 140,
        "production_length_source": "MANUAL_SEGMENTS",
        "production_segment_lengths_cm": [61, 18, 61],
        "geometry_handles": ["G1", "G2", "G3"],
        "geometry_layer": "Arm - N10",
        "segment_count": 3,
        "geometry_length_raw": 150,
        "numeric_difference": 10,
        "geometry_vs_production_difference_cm": 10,
        "master_shape_code": "F005",
        "master_shape_name": "ФИБА",
        "manual_assignment": {
            "canonical_signature": "O|n=3",
            "segment_lengths_cm": [66, 18, 66],
            "turn_angles_deg": [-90, -90],
        },
    }

    row = _approved_row(annotation, approved, RecognitionPolicy())

    assert row["geometry_length_raw"] == 150
    assert row["effective_geometry_length_cm"] == 140
    assert row["production_length_cm"] == 140
    assert row["production_segment_lengths_cm"] == [61, 18, 61]
    assert row["master_shape_code"] == "F005"
