from __future__ import annotations

from pathlib import Path

from lion_rebar.dxf.recognition_policy import RecognitionPolicy
from lion_rebar.engine.package_io import inspect_engine_bundle
from lion_rebar.engine.test_runner import run_engine_tests
from lion_rebar.engine.validation import validate_engine


def test_v0312_engine_records_position_key_priority_and_f001_calibration() -> None:
    path = (
        Path(__file__).resolve().parents[1]
        / "engine_updates"
        / "lion-rebar-master_v0.3.12_POSITION_KEY_F001_CALIBRATION_PRODUCTION_DRAFT.lionengine"
    )
    engine, manifest, _names = inspect_engine_bundle(path)
    recognition = engine.settings["geometry_recognition"]

    assert engine.version == "0.3.12"
    assert engine.parent_version == "0.3.11"
    assert engine.title == (
        "LION REBAR MASTER v0.3.12 — POSITION KEY PRIORITY AND F001 CALIBRATION"
    )
    assert engine.production_ready is True
    assert manifest["engine_version"] == "0.3.12"

    assert recognition["position_key_manual_review_override_enabled"] is True
    assert recognition["straight_bar_exact_length_rule_enabled"] is True
    assert recognition["straight_bar_exact_length_master_codes"] == ["F001"]
    assert recognition["straight_bar_exact_length_ratio"] == 0.007
    assert recognition["straight_bar_exact_length_max_abs_cm"] == 7.0

    policy = RecognitionPolicy.from_settings(engine.settings)
    assert policy.exact_length_tolerance_for_master(750.0, "F001") == 5.25
    assert policy.exact_length_tolerance_for_master(900.0, "F001") == 6.3
    assert policy.exact_length_tolerance_for_master(750.0, "F003") == 5.0

    errors = [
        issue
        for issue in validate_engine(engine, for_publish=True)
        if issue.severity == "ERROR"
    ]
    assert not errors
    results = run_engine_tests(engine)
    assert len(results) == 20
    assert all(result.passed for result in results)
