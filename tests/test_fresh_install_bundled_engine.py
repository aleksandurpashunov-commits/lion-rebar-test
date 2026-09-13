from pathlib import Path

from lion_rebar.engine.repository import EngineRepository


def test_trusted_bundled_engine_can_replace_fresh_bootstrap(tmp_path: Path):
    repo = EngineRepository(tmp_path / "registry")
    assert repo.load_current().engine_id == "lion-rebar-bootstrap"
    assert repo.load_current().version == "0.0.0"

    bundle = (
        Path(__file__).resolve().parents[1]
        / "engine_updates"
        / "lion-rebar-master_v0.3.13_F003_F005_DISTINCT_CONTEXT_PRODUCTION_DRAFT.lionengine"
    )
    changed = repo.install_trusted_bundled_engine(
        bundle,
        allowed_current_versions={"0.0.0", "0.3.12"},
        expected_target_version="0.3.13",
    )
    assert changed is True
    current = repo.load_current()
    assert current.engine_id == "lion-rebar-master"
    assert current.version == "0.3.13"
    assert current.production_ready is True
