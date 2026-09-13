from __future__ import annotations

from pathlib import Path

from lion_rebar.engine.package_io import inspect_engine_bundle
from lion_rebar.engine.test_runner import run_engine_tests
from lion_rebar.engine.validation import validate_engine


def test_v0311_engine_records_every_narrow_arc_and_group_gate() -> None:
    path = (
        Path(__file__).resolve().parents[1]
        / "engine_updates"
        / "lion-rebar-master_v0.3.11_ARC_LENGTH_STIRRUP_GROUP_PRODUCTION_DRAFT.lionengine"
    )
    engine, manifest, _names = inspect_engine_bundle(path)
    recognition = engine.settings["geometry_recognition"]

    assert engine.version == "0.3.11"
    assert engine.parent_version == "0.3.10"
    assert engine.title == (
        "LION REBAR MASTER v0.3.11 — ARC LENGTH AND STIRRUP GROUP RESOLVER"
    )
    assert engine.production_ready is True
    assert manifest["engine_version"] == "0.3.11"

    assert recognition["c_shape_curve_total_tolerance_cm"] == 2.5
    assert recognition["c_shape_curve_search_distance_raw"] == 120.0
    assert recognition["c_shape_curve_strong_distance_raw"] == 60.0
    assert recognition["stirrup_length_group_rule_enabled"] is True
    assert recognition["stirrup_length_group_layer_aliases"] == [
        "STREMENA",
        "СТРЕМЕНА",
    ]
    assert recognition["stirrup_length_group_master_codes"] == ["F006"]
    assert recognition["stirrup_length_search_distance_raw"] == 205.0
    assert recognition["stirrup_arc_length_tolerance_cm"] == 2.0
    assert recognition["stirrup_group_min_members"] == 2
    assert recognition["stirrup_group_max_member_spacing_raw"] == 80.0
    assert recognition["stirrup_group_max_row_deviation_raw"] == 5.0
    assert recognition["stirrup_group_repeat_length_tolerance_cm"] == 0.5
    assert recognition["stirrup_group_require_count_compatibility"] is True

    errors = [
        issue
        for issue in validate_engine(engine, for_publish=True)
        if issue.severity == "ERROR"
    ]
    assert not errors
    results = run_engine_tests(engine)
    assert len(results) == 20
    assert all(result.passed for result in results)
