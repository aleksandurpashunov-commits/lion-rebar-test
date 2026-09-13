from pathlib import Path

import pytest

from lion_rebar.config.constants import ACTIVATION_PHRASE, APPROVAL_PHRASE
from lion_rebar.engine.models import EnginePackage, SegmentDefinition, ShapeDefinition
from lion_rebar.engine.package_io import inspect_engine_bundle
from lion_rebar.engine.repository import EngineRepository, EngineRepositoryError
from lion_rebar.utils.files import (
    atomic_write_json,
    canonical_json_bytes,
    read_json,
    set_writable,
    sha256_bytes,
)


def test_publish_activate_and_rollback(tmp_path: Path):
    repository = EngineRepository(tmp_path / "registry")
    bootstrap = repository.load_current()
    assert bootstrap.version == "0.0.0"

    draft = repository.clone_current_to_draft("0.0.1", "Александър")
    draft.title = "Governance update"
    repository.publish(
        draft,
        approved_by="Александър",
        approval_note="Тестова публикувана версия",
        phrase=APPROVAL_PHRASE,
        activate=True,
    )
    assert repository.load_current().version == "0.0.1"

    repository.activate_version(
        bootstrap.engine_id,
        bootstrap.version,
        by="Александър",
        reason="Rollback test",
        phrase=ACTIVATION_PHRASE,
    )
    assert repository.load_current().version == "0.0.0"


def test_wrong_approval_phrase_is_rejected(tmp_path: Path):
    repository = EngineRepository(tmp_path / "registry")
    draft = repository.clone_current_to_draft("0.0.1", "Owner")
    with pytest.raises(EngineRepositoryError):
        repository.publish(
            draft,
            approved_by="Owner",
            approval_note="test",
            phrase="wrong",
        )


def test_trusted_bundled_engine_installs_only_from_explicit_predecessor(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    old_bundle = (
        root
        / "engine_updates"
        / "lion-rebar-master_v0.3.12_POSITION_KEY_F001_CALIBRATION_PRODUCTION_DRAFT.lionengine"
    )
    new_bundle = (
        root
        / "engine_updates"
        / "lion-rebar-master_v0.3.13_F003_F005_DISTINCT_CONTEXT_PRODUCTION_DRAFT.lionengine"
    )
    repository = EngineRepository(tmp_path / "registry")

    old, _manifest, _names = inspect_engine_bundle(old_bundle)
    repository.save_draft(old)
    repository.publish(
        old,
        approved_by="TEST",
        approval_note="Seed Engine v0.3.12",
        phrase=APPROVAL_PHRASE,
        activate=True,
    )
    assert repository.load_current().version == "0.3.12"

    installed = repository.install_trusted_bundled_engine(
        new_bundle,
        allowed_current_versions={"0.3.12"},
        expected_target_version="0.3.13",
    )

    assert installed is True
    current = repository.load_current()
    f005 = current.find_shape("F005")
    assert current.version == "0.3.13"
    assert f005 is not None and f005.active
    assert "canonical_replacement" not in f005.recognition
    assert repository.install_trusted_bundled_engine(
        new_bundle,
        allowed_current_versions={"0.3.12"},
        expected_target_version="0.3.13",
    ) is False


def test_loads_legacy_engine_checksum_before_model_defaults_are_added(tmp_path: Path):
    repository = EngineRepository(tmp_path / "registry")
    published_path = repository.current_path()
    source_data = read_json(published_path)

    # Simulate a published Engine from an older application version. Fields added
    # later to ShapeDefinition were not part of the original signed JSON bytes.
    shape = ShapeDefinition(
        code="F001",
        name="Legacy straight bar",
        state="LOCKED",
        locked=True,
        segments=[SegmentDefinition(index=1, length_code="L1")],
    ).to_dict()
    shape.pop("family_id", None)
    shape.pop("alias_of", None)
    shape.pop("parameter_relations", None)
    source_data["shapes"] = [shape]
    source_data.pop("content_sha256", None)
    source_data["content_sha256"] = sha256_bytes(canonical_json_bytes(source_data))
    set_writable(published_path)
    atomic_write_json(published_path, source_data)

    loaded = repository.load_current()

    assert isinstance(loaded, EnginePackage)
    assert loaded.version == "0.0.0"
    assert loaded.shapes[0].family_id == ""


def test_still_rejects_actual_legacy_engine_tampering(tmp_path: Path):
    repository = EngineRepository(tmp_path / "registry")
    published_path = repository.current_path()
    source_data = read_json(published_path)
    source_data["title"] = "Tampered after publication"
    set_writable(published_path)
    atomic_write_json(published_path, source_data)

    with pytest.raises(EngineRepositoryError, match="Checksum"):
        repository.load_current()
