from pathlib import Path

from lion_rebar.dxf.recognition_policy import RecognitionPolicy
from lion_rebar.engine.package_io import inspect_engine_bundle
from lion_rebar.engine.test_runner import run_engine_tests
from lion_rebar.engine.validation import validate_engine


def test_v039_engine_carries_f007_rule_and_five_cm_gate() -> None:
    root = Path(__file__).resolve().parents[1]
    bundle = (
        root
        / "engine_updates"
        / "lion-rebar-master_v0.3.9_F007_EXACT5_PRODUCTION_DRAFT.lionengine"
    )
    engine, manifest, names = inspect_engine_bundle(bundle)

    assert engine.version == "0.3.9"
    assert engine.parent_version == "0.3.8"
    assert engine.production_ready is True
    assert names == ["engine.json", "manifest.json"]
    assert manifest["engine_version"] == "0.3.9"

    policy = RecognitionPolicy.from_settings(engine.settings)
    assert policy.exact_length_abs == 5.0
    assert policy.c_shape_curve_rule_enabled is True
    assert policy.c_shape_curve_turn_deg == 135.0
    assert policy.c_shape_curve_total_tolerance_cm == 2.0
    assert any(rule.code == "R-F007-ESOVE-CURVE" for rule in engine.rules)

    issues = validate_engine(engine, for_publish=True)
    assert not [item for item in issues if item.severity == "ERROR"]
    results = run_engine_tests(engine)
    assert len(results) == 20
    assert all(result.passed for result in results)
