from __future__ import annotations

from pathlib import Path

from lion_rebar.engine.package_io import inspect_engine_bundle
from lion_rebar.engine.test_runner import run_engine_tests
from lion_rebar.engine.validation import validate_engine


def test_v0313_keeps_f003_and_f005_as_distinct_active_shapes() -> None:
    root = Path(__file__).resolve().parents[1]
    bundle = (
        root
        / "engine_updates"
        / "lion-rebar-master_v0.3.13_F003_F005_DISTINCT_CONTEXT_PRODUCTION_DRAFT.lionengine"
    )
    engine, manifest, names = inspect_engine_bundle(bundle)

    assert engine.version == "0.3.13"
    assert engine.parent_version == "0.3.12"
    assert engine.production_ready is True
    assert names == ["engine.json", "manifest.json"]
    assert manifest["engine_version"] == "0.3.13"

    f003 = engine.find_shape("F003")
    f005 = engine.find_shape("F005")
    assert f003 is not None and f003.active and f003.state == "LOCKED"
    assert f005 is not None and f005.active and f005.state == "LOCKED"
    assert f003.name == "ПРЪТ С ДВА ВИНКЕЛА"
    assert f005.name == "ФИБА"
    assert "canonical_replacement" not in f005.recognition
    assert f005.alias_of is None
    assert f005.recognition["requires_equal_end_arms"] is True

    rule = next(rule for rule in engine.rules if rule.code == "R-NAMED-U-SUBTYPE-F003-F005")
    assert rule.enabled is True
    f006_rule = next(
        rule for rule in engine.rules if rule.code == "R-F006-PRINTED-MASTER-EQUATION"
    )
    assert f006_rule.enabled is True
    assert f006_rule.then[0]["formula"] == "L=2*(width+height+hook)"
    assert f006_rule.then[0]["tolerance_cm"] == 2.1
    assert engine.settings["geometry_recognition"]["named_u_subtype_context_rule_enabled"] is True
    assert not [issue for issue in validate_engine(engine, for_publish=True) if issue.severity == "ERROR"]
    assert all(result.passed for result in run_engine_tests(engine))
