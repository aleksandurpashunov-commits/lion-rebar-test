from __future__ import annotations

from pathlib import Path

from lion_rebar.engine.package_io import inspect_engine_bundle
from lion_rebar.engine.test_runner import run_engine_tests
from lion_rebar.engine.validation import validate_engine


def test_engine_v0310_records_only_narrow_evidence_gates() -> None:
    path = (
        Path(__file__).resolve().parents[1]
        / "engine_updates"
        / "lion-rebar-master_v0.3.10_EVIDENCE_TIEBREAK_PRODUCTION_DRAFT.lionengine"
    )
    engine, manifest, _names = inspect_engine_bundle(path)
    recognition = engine.settings["geometry_recognition"]

    assert engine.version == "0.3.10"
    assert engine.parent_version == "0.3.9"
    assert engine.production_ready is True
    assert manifest["engine_version"] == "0.3.10"
    assert recognition["exact_length_abs"] == 5.0
    assert recognition["c_shape_curve_strong_distance_raw"] == 60.0
    assert recognition["dimension_candidate_tiebreak_enabled"] is True
    assert recognition["dimension_candidate_tiebreak_total_tolerance_cm"] == 0.51
    assert recognition["verified_alternative_recovery_enabled"] is True
    assert recognition["verified_alternative_dimension_tolerance_cm"] == 0.51

    errors = [
        issue
        for issue in validate_engine(engine, for_publish=True)
        if issue.severity == "ERROR"
    ]
    assert not errors
    results = run_engine_tests(engine)
    assert len(results) == 20
    assert all(result.passed for result in results)

