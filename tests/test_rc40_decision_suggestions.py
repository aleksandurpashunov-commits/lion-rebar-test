from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from lion_rebar.project.data_identity import DataIdentity
from lion_rebar.review.decision_suggestions import (
    CONFLICT,
    INELIGIBLE,
    NO_MATCH,
    READY,
    DecisionSuggestionIndex,
    ExplicitSuggestionAcceptanceRequired,
    SuggestionNotAcceptable,
)
from lion_rebar.session.database import SessionDatabase
from lion_rebar.session.decision_contract import (
    DECISION_CONTRACT_SCHEMA,
    append_project_decision_event,
    mark_project_decision_delivered,
)


ENGINE = "0.3.14"


def _identity(
    *, project: str = "P1", source: str = "same-dxf",
    generation: str = "G1",
) -> dict:
    return {
        "project_id": project,
        "project_name": project,
        "source_sha256": source,
        "source_generation_id": generation,
        "analysis_run_id": "R1",
        "drawing_family_id": hashlib.sha256(source.encode()).hexdigest(),
        "engine_version": ENGINE,
    }


def _row(
    handle: str = "TARGET", *, segments=(47,), length=47,
    shape: str = "F001", candidate_basis: str = "DXF_HANDLES",
) -> dict:
    return {
        "annotation_handle": handle,
        "annotation_text": "1N10x47",
        "annotation_count": 1,
        "diameter_mm": 10,
        "declared_length_cm": length,
        "master_shape_code": shape,
        "canonical_segment_lengths_cm": list(segments),
        "canonical_turn_angles_deg": [],
        "candidate_handles": [f"G-{handle}"],
        "candidate_layer": "ARM-N10",
        "candidate_basis": candidate_basis,
        "batch_status": "REVIEW",
        "matcher_disposition": "MATCHER_REVIEW",
        "matcher_evidence_band": "MEDIUM",
        "candidate_confidence": "MEDIUM",
        "review_reason_codes": ["SHAPE_REVIEW"],
        "contextual_rule_code": "",
        "canonical_signature": "candidate-v1",
        "numeric_difference": 0,
        "alternative_candidate_count": 0,
        "frame_category_code": "DETAIL",
        "global_conflict": False,
        "geometry_owner_conflict": False,
    }


def _envelope(
    event_id: str, *, handle: str = "OLD",
    accepted_shape: str = "F008", accepted_segments=(13, 21, 13),
    action: str = "CORRECTION", geometry: bool = True,
    reason: str = "WRONG_SHAPE", identity: dict | None = None,
    note: str = "Проверено по детайла",
    supersedes=(),
    candidate_basis: str = "DXF_HANDLES",
) -> dict:
    source_identity = dict(identity or _identity())
    before = _row(handle, candidate_basis=candidate_basis)
    payload = {
        "annotation_handle": handle,
        "annotation_text": before["annotation_text"],
        "action_class": action,
        "reason_code": reason,
        "reason": "Грешна MASTER форма",
        "note": note,
        "row_context": dict(before),
        "annotation": {
            "handle": handle,
            "text": before["annotation_text"],
            "resolved_text": "1N10x47",
        },
        "original": {
            "count": 1,
            "diameter_mm": 10,
            "declared_length_cm": 47,
            "shape_code": "F001",
            "segment_lengths_cm": [47],
            "geometry_handles": [f"G-{handle}"],
            "candidate_basis": candidate_basis,
            "review_reason_codes": ["SHAPE_REVIEW"],
            "batch_status": "REVIEW",
            "matcher_disposition": "MATCHER_REVIEW",
        },
        "resolved": {
            "shape_code": accepted_shape,
            "shape_name": accepted_shape,
            "shape_revision": 1,
            "count": 1,
            "diameter_mm": 10,
            "production_length_cm": 47,
            "segment_lengths_cm": list(accepted_segments),
            "turn_angles_deg": [90, 90],
        },
        "geometry": {
            "handles": [f"G-{handle}"] if geometry else [],
            "layer": "ARM-N10",
            "basis": candidate_basis,
        },
    }
    return {
        "schema": DECISION_CONTRACT_SCHEMA,
        "event_id": event_id,
        "occurred_at": "2026-09-06T12:00:00+03:00",
        "event_type": "ENGINEER_RESOLVED",
        "operation_kind": "INDIVIDUAL",
        "decision_revision": 1,
        "supersedes_event_ids": list(supersedes),
        "active": True,
        "annotation_handles": [handle],
        "source_identity": source_identity,
        "payload": payload,
    }


def _record(
    db: SessionDatabase, session_id: str, envelope: dict,
    *, finish: bool = True,
) -> None:
    identity = envelope["source_identity"]
    payload = envelope["payload"]
    db.create_session(
        session_id=session_id,
        started_at="2026-09-06T11:00:00+03:00",
        app_version="rc40-test",
    )
    db.record_event(
        session_id=session_id,
        occurred_at=envelope["occurred_at"],
        event_type=envelope["event_type"],
        payload={"human_decision": envelope},
        project_name=identity["project_name"],
        project_root=f"C:/projects/{identity['project_name']}",
        source_sha256=identity["source_sha256"],
        engine_version=ENGINE,
        annotation_handle=payload["annotation_handle"],
        annotation_text=payload["annotation_text"],
        stable_event_id=envelope["event_id"],
    )
    if finish:
        db.finish_session(
            session_id=session_id,
            ended_at="2026-09-06T13:00:00+03:00",
            summary={},
        )


def _ended_db(tmp_path: Path, *envelopes: dict) -> Path:
    path = tmp_path / "sessions.sqlite3"
    db = SessionDatabase(path)
    for index, envelope in enumerate(envelopes, start=1):
        _record(db, f"S{index}", envelope)
    return path


def _current_identity() -> DataIdentity:
    return DataIdentity("CURRENT", "new-dxf", "CURRENT-GEN", "RUN")


def test_ended_session_correction_is_preview_only_until_explicit_accept(
    tmp_path: Path,
) -> None:
    index = DecisionSuggestionIndex.from_sources(
        session_db_path=_ended_db(tmp_path, _envelope("E1"))
    )
    current = _row()
    before = json.loads(json.dumps(current))
    suggestion = index.lookup(
        current, identity=_current_identity(), engine_version=ENGINE
    )

    assert suggestion.status == READY
    assert current == before
    assert suggestion.proposal is not None
    assert suggestion.proposal.shape_code == "F008"
    with pytest.raises(ExplicitSuggestionAcceptanceRequired):
        suggestion.accepted_prefill()

    prefill = suggestion.accepted_prefill(explicitly_accepted=True)
    assert prefill["segment_lengths_cm"] == [13.0, 21.0, 13.0]
    assert "geometry" not in prefill
    assert "geometry_handles" not in prefill
    assert "note" not in prefill
    assert suggestion.acceptance_audit()["source_event_ids"] == ["E1"]


def test_active_db_session_is_not_learning_evidence(tmp_path: Path) -> None:
    path = tmp_path / "sessions.sqlite3"
    db = SessionDatabase(path)
    _record(db, "ACTIVE", _envelope("E-ACTIVE"), finish=False)
    index = DecisionSuggestionIndex.from_sources(session_db_path=path)
    result = index.lookup(
        _row(), identity=_current_identity(), engine_version=ENGINE
    )
    assert result.status == NO_MATCH
    assert index.source_stats.delivered_event_count == 0


def test_portable_project_journal_requires_delivered_marker(
    tmp_path: Path,
) -> None:
    project = tmp_path / "portable-project"
    working = project / "working"
    working.mkdir(parents=True)
    project_payload = {
        "name": "portable-project",
        "engine_id": "lion-rebar-master",
        "engine_version": ENGINE,
        "extensions": {
            "source_sha256": "same-dxf",
            "data_identity": _identity(),
        },
    }
    (working / "project.json").write_text(
        json.dumps(project_payload), encoding="utf-8"
    )
    payload = _envelope("IGNORED")["payload"]
    payload["stable_event_id"] = "PORTABLE-E1"
    envelope = append_project_decision_event(
        project, "ENGINEER_RESOLVED", payload
    )

    pending_index = DecisionSuggestionIndex.from_sources(project_root=project)
    assert pending_index.lookup(
        _row(), identity=_current_identity(), engine_version=ENGINE
    ).status == NO_MATCH

    mark_project_decision_delivered(
        project, envelope["event_id"], session_id="S-PORTABLE"
    )
    delivered_index = DecisionSuggestionIndex.from_sources(project_root=project)
    result = delivered_index.lookup(
        _row(), identity=_current_identity(), engine_version=ENGINE
    )
    assert result.status == READY
    assert result.provenance[0].delivery_sources == (
        "PROJECT_OUTBOX_DELIVERED",
    )


def test_confirmations_and_unexplained_geometryless_corrections_never_suggest(
    tmp_path: Path,
) -> None:
    confirmation = _envelope(
        "CONFIRM", action="CONFIRMATION", reason="CONFIRM_CORRECT",
        accepted_shape="F001", accepted_segments=(47,),
    )
    geometryless = _envelope("NO-GEOM", geometry=False)
    index = DecisionSuggestionIndex.from_sources(
        session_db_path=_ended_db(tmp_path, confirmation, geometryless)
    )
    assert index.lookup(
        _row(), identity=_current_identity(), engine_version=ENGINE
    ).status == NO_MATCH
    assert index.source_stats.eligible_evidence_count == 0


def test_correction_with_confirmation_reason_is_ineligible(tmp_path: Path) -> None:
    inconsistent = _envelope(
        "INCONSISTENT",
        action="CORRECTION",
        reason="CONFIRM_CORRECT",
    )
    index = DecisionSuggestionIndex.from_sources(
        session_db_path=_ended_db(tmp_path, inconsistent)
    )
    assert index.lookup(
        _row(), identity=_current_identity(), engine_version=ENGINE
    ).status == NO_MATCH


def test_explicit_geometryless_resolution_can_suggest_but_copies_no_note(
    tmp_path: Path,
) -> None:
    geometryless = _envelope(
        "NO-GEOMETRY",
        geometry=False,
        reason="NO_GEOMETRY",
        note="Проверено по оригиналния детайл",
    )
    index = DecisionSuggestionIndex.from_sources(
        session_db_path=_ended_db(tmp_path, geometryless)
    )

    suggestion = index.lookup(
        _row(), identity=_current_identity(), engine_version=ENGINE
    )
    assert suggestion.status == READY
    prefill = suggestion.accepted_prefill(explicitly_accepted=True)
    assert prefill["reason_code"] == "NO_GEOMETRY"
    assert "note" not in prefill
    assert "geometry" not in prefill
    serialized = json.dumps(suggestion.to_dict(), ensure_ascii=False)
    assert "Проверено по оригиналния детайл" not in serialized
    assert "prior_note" not in serialized
    assert suggestion.proposal is not None
    assert suggestion.proposal.geometry_mode == "NO_DXF_GEOMETRY"
    assert suggestion.proposal.historical_note_present is True


def test_geometryless_resolution_without_operator_note_is_rejected(
    tmp_path: Path,
) -> None:
    geometryless = _envelope(
        "NO-NOTE",
        geometry=False,
        reason="NO_GEOMETRY",
        note="",
    )
    index = DecisionSuggestionIndex.from_sources(
        session_db_path=_ended_db(tmp_path, geometryless)
    )
    assert index.lookup(
        _row(), identity=_current_identity(), engine_version=ENGINE
    ).status == NO_MATCH


def test_different_material_resolution_is_a_visible_conflict(
    tmp_path: Path,
) -> None:
    left = _envelope("LEFT", handle="A")
    right = _envelope(
        "RIGHT", handle="B", accepted_shape="F005",
        accepted_segments=(10, 27, 10),
    )
    index = DecisionSuggestionIndex.from_sources(
        session_db_path=_ended_db(tmp_path, left, right)
    )
    suggestion = index.lookup(
        _row(), identity=_current_identity(), engine_version=ENGINE
    )
    assert suggestion.status == CONFLICT
    assert len(suggestion.alternatives) == 2
    assert not suggestion.can_accept
    with pytest.raises(SuggestionNotAcceptable):
        suggestion.accepted_prefill(explicitly_accepted=True)


def test_segment_difference_and_current_handle_cannot_match(
    tmp_path: Path,
) -> None:
    identity = _identity(source="same-dxf", generation="G1")
    index = DecisionSuggestionIndex.from_sources(
        session_db_path=_ended_db(
            tmp_path, _envelope("STRICT", handle="SAME", identity=identity)
        )
    )
    different = index.lookup(
        _row(segments=(46,), length=47),
        identity=DataIdentity("CURRENT", "same-dxf", "G2", "R2"),
        engine_version=ENGINE,
    )
    assert different.status == NO_MATCH

    self_result = index.lookup(
        _row(handle="SAME"),
        identity=DataIdentity("P1", "same-dxf", "G1", "R1"),
        engine_version=ENGINE,
    )
    assert self_result.status == NO_MATCH


def test_delivered_superseding_exclusion_invalidates_old_correction(
    tmp_path: Path,
) -> None:
    correction = _envelope("OLD-CORRECTION", handle="SAME")
    exclusion = _envelope(
        "NEW-EXCLUSION",
        handle="SAME",
        action="EXCLUSION",
        reason="NON_PRODUCTION_POSITION",
        geometry=False,
        supersedes=("OLD-CORRECTION",),
    )
    exclusion["event_type"] = "NON_PRODUCTION_POSITION"
    exclusion["payload"]["action_class"] = "EXCLUSION"
    index = DecisionSuggestionIndex.from_sources(
        session_db_path=_ended_db(tmp_path, correction, exclusion)
    )

    result = index.lookup(
        _row(), identity=_current_identity(), engine_version=ENGINE
    )
    assert result.status == NO_MATCH
    assert index.source_stats.eligible_evidence_count == 0


def test_pending_project_superseder_is_tombstone_not_positive_evidence(
    tmp_path: Path,
) -> None:
    project = tmp_path / "pending-tombstone"
    working = project / "working"
    working.mkdir(parents=True)
    (working / "project.json").write_text(
        json.dumps({
            "name": "pending-tombstone",
            "engine_id": "lion-rebar-master",
            "engine_version": ENGINE,
            "extensions": {
                "source_sha256": "same-dxf",
                "data_identity": _identity(),
            },
        }),
        encoding="utf-8",
    )
    old_payload = _envelope("IGNORED", handle="SAME")["payload"]
    old_payload["stable_event_id"] = "PROJECT-OLD"
    old = append_project_decision_event(
        project, "ENGINEER_RESOLVED", old_payload
    )
    mark_project_decision_delivered(
        project, old["event_id"], session_id="ENDED"
    )
    pending_payload = _envelope(
        "IGNORED-2",
        handle="SAME",
        action="EXCLUSION",
        reason="NON_PRODUCTION_POSITION",
        geometry=False,
    )["payload"]
    pending_payload["stable_event_id"] = "PROJECT-PENDING-EXCLUSION"
    append_project_decision_event(
        project, "NON_PRODUCTION_POSITION", pending_payload
    )

    index = DecisionSuggestionIndex.from_sources(project_root=project)
    result = index.lookup(
        _row(), identity=_current_identity(), engine_version=ENGINE
    )
    assert result.status == NO_MATCH
    assert index.source_stats.delivered_event_count == 1
    assert index.source_stats.lifecycle_tombstone_count == 1


def test_pending_superseder_without_any_delivered_marker_invalidates_db_evidence(
    tmp_path: Path,
) -> None:
    project = tmp_path / "pending-only-tombstone"
    working = project / "working"
    working.mkdir(parents=True)
    (working / "project.json").write_text(
        json.dumps({
            "name": "pending-only-tombstone",
            "engine_id": "lion-rebar-master",
            "engine_version": ENGINE,
            "extensions": {
                "source_sha256": "same-dxf",
                "data_identity": _identity(),
            },
        }),
        encoding="utf-8",
    )

    historical = _envelope("DB-OLD", handle="SAME")
    db_path = _ended_db(tmp_path, historical)
    old_payload = dict(historical["payload"])
    old_payload["stable_event_id"] = "DB-OLD"
    append_project_decision_event(project, "ENGINEER_RESOLVED", old_payload)

    exclusion_payload = _envelope(
        "IGNORED",
        handle="SAME",
        action="EXCLUSION",
        reason="NON_PRODUCTION_POSITION",
        geometry=False,
    )["payload"]
    exclusion_payload["stable_event_id"] = "PENDING-EXCLUSION"
    append_project_decision_event(
        project, "NON_PRODUCTION_POSITION", exclusion_payload
    )

    index = DecisionSuggestionIndex.from_sources(
        session_db_path=db_path,
        project_root=project,
    )
    assert index.lookup(
        _row(), identity=_current_identity(), engine_version=ENGINE
    ).status == NO_MATCH
    assert index.source_stats.delivered_event_count == 1
    assert index.source_stats.lifecycle_tombstone_count == 1


@pytest.mark.parametrize("reason", ["WRONG_GEOMETRY", "WRONG_OBJECT"])
def test_changed_historical_geometry_selection_is_not_transferable_evidence(
    tmp_path: Path, reason: str,
) -> None:
    changed = _envelope(
        "CHANGED-GEOMETRY",
        handle="OLD",
        reason=reason,
    )
    changed["payload"]["geometry"]["handles"] = ["G-REPLACEMENT"]
    index = DecisionSuggestionIndex.from_sources(
        session_db_path=_ended_db(tmp_path, changed)
    )

    assert index.lookup(
        _row(), identity=_current_identity(), engine_version=ENGINE
    ).status == NO_MATCH
    assert index.source_stats.eligible_evidence_count == 0


def test_owner_selected_geometry_basis_survives_db_to_index_roundtrip(
    tmp_path: Path,
) -> None:
    basis = "OWNER_SELECTED_DXF_GEOMETRY"
    index = DecisionSuggestionIndex.from_sources(
        session_db_path=_ended_db(
            tmp_path,
            _envelope(
                "SELECTED-CONTEXT",
                handle="OLD",
                candidate_basis=basis,
            ),
        )
    )

    matching = index.lookup(
        _row(candidate_basis=basis),
        identity=_current_identity(),
        engine_version=ENGINE,
    )
    assert matching.status == READY
    mismatching = index.lookup(
        _row(candidate_basis="DXF_HANDLES"),
        identity=_current_identity(),
        engine_version=ENGINE,
    )
    assert mismatching.status == NO_MATCH


def test_missing_annotation_handle_is_ineligible_and_never_evidence(
    tmp_path: Path,
) -> None:
    index = DecisionSuggestionIndex.from_sources(
        session_db_path=_ended_db(
            tmp_path, _envelope("ANONYMOUS", handle="")
        )
    )
    assert index.source_stats.eligible_evidence_count == 0
    result = index.lookup(
        _row(handle=""), identity=_current_identity(), engine_version=ENGINE
    )
    assert result.status == INELIGIBLE
    assert "annotation_handle" in result.missing_context


def test_self_match_is_blocked_case_insensitively(tmp_path: Path) -> None:
    identity = _identity(source="same-dxf", generation="G1")
    index = DecisionSuggestionIndex.from_sources(
        session_db_path=_ended_db(
            tmp_path, _envelope("CASE-SELF", handle="abc", identity=identity)
        )
    )
    result = index.lookup(
        _row(handle="ABC"),
        identity=DataIdentity("P1", "same-dxf", "G1", "R2"),
        engine_version=ENGINE,
    )
    assert result.status == NO_MATCH


def test_different_geometry_basis_never_matches(tmp_path: Path) -> None:
    index = DecisionSuggestionIndex.from_sources(
        session_db_path=_ended_db(
            tmp_path,
            _envelope(
                "BASIS",
                handle="OLD",
                candidate_basis="DXF_CENTERLINE_AND_DIMENSIONS",
            ),
        )
    )
    result = index.lookup(
        _row(candidate_basis="DIMENSION_ONLY"),
        identity=_current_identity(),
        engine_version=ENGINE,
    )
    assert result.status == NO_MATCH


def test_same_event_in_db_and_portable_journal_is_deduplicated(
    tmp_path: Path,
) -> None:
    project = tmp_path / "P"
    working = project / "working"
    working.mkdir(parents=True)
    project_payload = {
        "name": "P",
        "engine_id": "lion-rebar-master",
        "engine_version": ENGINE,
        "extensions": {
            "source_sha256": "same-dxf",
            "data_identity": _identity(),
        },
    }
    (working / "project.json").write_text(
        json.dumps(project_payload), encoding="utf-8"
    )
    payload = _envelope("IGNORED")["payload"]
    payload["stable_event_id"] = "DUPLICATE"
    envelope = append_project_decision_event(
        project, "ENGINEER_RESOLVED", payload
    )
    mark_project_decision_delivered(
        project, envelope["event_id"], session_id="S-DUP"
    )

    db_path = tmp_path / "sessions.sqlite3"
    db = SessionDatabase(db_path)
    _record(db, "S-DUP", envelope)
    index = DecisionSuggestionIndex.from_sources(
        session_db_path=db_path, project_root=project
    )
    result = index.lookup(
        _row(), identity=_current_identity(), engine_version=ENGINE
    )
    assert result.status == READY
    assert index.source_stats.delivered_event_count == 1
    assert index.source_stats.duplicate_event_count == 1
    assert result.provenance[0].delivery_sources == (
        "PROJECT_OUTBOX_DELIVERED", "SESSION_DB_ENDED",
    )
