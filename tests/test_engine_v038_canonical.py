from pathlib import Path

from lion_rebar.engine.package_io import inspect_engine_bundle
from lion_rebar.engine.test_runner import run_engine_tests
from lion_rebar.engine.validation import validate_engine


def test_v038_canonical_engine_is_publishable_and_traceable() -> None:
    root = Path(__file__).resolve().parents[1]
    bundle = (
        root
        / "engine_updates"
        / "lion-rebar-master_v0.3.8_CANONICAL_FORMS_PRODUCTION_DRAFT.lionengine"
    )
    engine, manifest, names = inspect_engine_bundle(bundle)

    assert engine.version == "0.3.8"
    assert engine.production_ready is True
    assert names == ["engine.json", "manifest.json"]
    assert manifest["engine_version"] == "0.3.8"

    active = {shape.code for shape in engine.shapes if shape.active}
    retired = {shape.code for shape in engine.shapes if not shape.active}
    assert {"F003", "F010", "F013", "F014", "F016"} <= active
    assert retired == {"F005", "F009", "F015", "F017", "F018"}

    f003 = engine.find_shape("F003")
    f005 = engine.find_shape("F005")
    f009 = engine.find_shape("F009")
    assert f003 is not None and "Адаптивна" in f003.template_orientation
    assert f005 is not None
    assert f005.recognition["canonical_replacement"] == "F003"
    assert f009 is not None and f009.alias_of == "F010"

    issues = validate_engine(engine, for_publish=True)
    assert not [item for item in issues if item.severity == "ERROR"]
    results = run_engine_tests(engine)
    assert len(results) == 20
    assert all(result.passed for result in results)
