from __future__ import annotations

import pytest

from lion_rebar.dxf import batch_geometry_report as batch_report
from lion_rebar.engine.models import ShapeDefinition
from lion_rebar.export.engine_shape_parameters import parameters_for_shape
from lion_rebar.export.errors import F006AsymmetricHookError as SharedF006Error
from lion_rebar.export.excel_export import F006AsymmetricHookError as ExcelF006Error


# Exact label/unequal-hook family reported by the independent recovery.6.3 audit.
F006_SEGMENTS = (10.0, 70.0, 50.732235, 70.0, 50.732235, 3.53553)


def _row() -> dict:
    return {
        "annotation_class": "ACTIONABLE",
        "annotation_handle": "T-F006-631",
        "annotation_text": "33N8x255",
        "master_shape_code": "F006",
        "declared_length_cm": sum(F006_SEGMENTS),
        "candidate_handles": ["G1"],
        "batch_status": "СИЛНО СТРЕМЕ F006",
        "candidate_confidence": "СИЛНО СЕМЕЙСТВО F006",
        "numeric_difference": 0.01,
        "review_reason_codes": ["HOOKED_CLOSURE"],
        "matcher_blocker_codes": "HOOKED_CLOSURE",
        "canonical_segment_lengths_cm": list(F006_SEGMENTS),
        "master_ambiguous_shape_codes": [],
        "global_conflict": False,
        "geometry_length_raw": sum(F006_SEGMENTS),
    }


def test_shared_exception_is_the_same_cross_layer_type() -> None:
    assert ExcelF006Error is SharedF006Error


def test_new_engine_parameter_layer_preserves_f006_asymmetric_subtype() -> None:
    shape = ShapeDefinition(code="F006", name="СТРЕМЕ", state="LOCKED", active=True)
    with pytest.raises(SharedF006Error) as captured:
        parameters_for_shape(
            shape,
            F006_SEGMENTS,
            production_length_cm=sum(F006_SEGMENTS),
        )
    assert captured.value.hooks == (10.0, 3.53553)


def test_real_f006_gate_keeps_strong_form_when_new_layer_raises_typed_signal(monkeypatch) -> None:
    row = _row()
    shape = ShapeDefinition(code="F006", name="СТРЕМЕ", state="LOCKED", active=True)

    def new_layer_path(_link, *, shape, entity_map, production_length_cm):
        parameters_for_shape(
            shape,
            F006_SEGMENTS,
            production_length_cm=production_length_cm,
        )
        raise AssertionError("asymmetric F006 must raise before this line")

    monkeypatch.setattr(batch_report, "_segments_for_link", new_layer_path)
    batch_report._apply_f006_printed_production_dimensions(
        [row],
        entities=[],
        shape_definitions=[shape],
    )

    assert row["batch_status"] == "СИЛНО СТРЕМЕ F006"
    assert row["f006_asymmetric_hooks"] is True
    assert row["f006_production_status"] == "F006_PRODUCTION_DERIVED"
    assert not bool(row.get("production_export_blocked"))
    # The exact asymmetric hooks remain evidence; no synthetic 10/10 rewrite.
    assert row["canonical_segment_lengths_cm"][0] == 10.0
    assert row["canonical_segment_lengths_cm"][-1] == 3.53553


def test_synthetic_67_row_replay_has_zero_f006_form_downgrades(monkeypatch) -> None:
    rows = []
    for index in range(67):
        row = _row()
        row["annotation_handle"] = f"T-F006-631-{index:03d}"
        row["candidate_handles"] = [f"G-{index:03d}"]
        rows.append(row)
    shape = ShapeDefinition(code="F006", name="СТРЕМЕ", state="LOCKED", active=True)

    def new_layer_path(_link, *, shape, entity_map, production_length_cm):
        parameters_for_shape(
            shape,
            F006_SEGMENTS,
            production_length_cm=production_length_cm,
        )
        raise AssertionError("asymmetric F006 must raise before this line")

    monkeypatch.setattr(batch_report, "_segments_for_link", new_layer_path)
    batch_report._apply_f006_printed_production_dimensions(
        rows,
        entities=[],
        shape_definitions=[shape],
    )

    assert sum(row["batch_status"] == "СИЛНО СТРЕМЕ F006" for row in rows) == 67
    assert sum(bool(row.get("f006_asymmetric_hooks")) for row in rows) == 67
    assert sum(row.get("f006_production_status") == "F006_PRODUCTION_DERIVED" for row in rows) == 67
    assert sum(bool(row.get("production_export_blocked")) for row in rows) == 0


def test_regression_replay_keeps_all_67_audited_f006_rows_strong(monkeypatch) -> None:
    """Exact size of the independent 6.3 regression: 67 rows must not downgrade.

    This is not a substitute for the locked real DXF holdout.  It is a focused
    replay of the local failure mechanism reported by the independent audit:
    every row reaches the new Engine-parameter layer, receives the typed
    asymmetric-hook signal, and must retain strong form recognition.
    """

    shape = ShapeDefinition(code="F006", name="СТРЕМЕ", state="LOCKED", active=True)
    rows = []
    for index in range(67):
        row = _row()
        row["annotation_handle"] = f"T-F006-631-{index:03d}"
        rows.append(row)

    def new_layer_path(_link, *, shape, entity_map, production_length_cm):
        parameters_for_shape(
            shape,
            F006_SEGMENTS,
            production_length_cm=production_length_cm,
        )
        raise AssertionError("asymmetric F006 must raise before this line")

    monkeypatch.setattr(batch_report, "_segments_for_link", new_layer_path)
    batch_report._apply_f006_printed_production_dimensions(
        rows,
        entities=[],
        shape_definitions=[shape],
    )

    assert sum(row["batch_status"] == "СИЛНО СТРЕМЕ F006" for row in rows) == 67
    assert sum(bool(row.get("f006_asymmetric_hooks")) for row in rows) == 67
    assert sum(row.get("f006_production_status") == "F006_PRODUCTION_DERIVED" for row in rows) == 67
    assert not any(bool(row.get("production_export_blocked")) for row in rows)
