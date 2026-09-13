"""An operator can name the beam the automation could not prove (rc44).

Owner-reported 2026-09-13, project ``s-9``: Final Control refused the export
with eight ``BEAM_ASSIGNMENT_NOT_UNIQUE`` issues and told the owner to
"remove the technical cause", while the program offered no way to do so.
Frames have had an operator decision for a long time; beams had none, so the
project was stuck with no exit.

Widening the geometric window is not the answer and was measured, not
assumed: on all 460 positions of that drawing it resolves 3 more but moves 27
already-correct positions onto a neighbouring beam. The thresholds are doing
their job. What was missing is a human answer with an audit trail.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lion_rebar.export.beam_decisions import (
    BeamDecisionError,
    OPERATOR_METHOD,
    audit_trail,
    load_beam_decisions,
    record_beam_decision,
    withdraw_beam_decision,
)
from lion_rebar.project.data_identity import DataIdentity

_KNOWN = {"BL19": "Гр. B.19 25/55", "BL30": "Гр. B.30 25/40"}


def _identity(run: str = "R1") -> DataIdentity:
    return DataIdentity(
        project_id="s-9",
        source_sha256="SHA",
        source_generation_id="G1",
        analysis_run_id=run,
    )


def _record(root: Path, handle: str = "14C98::BJ-FI-L", label: str = "BL19", **kw):
    params = {
        "annotation_handle": handle,
        "beam_label_handle": label,
        "decided_by": "Александър",
        "note": "детайлът е над заглавието на Гр. B.19",
        "identity": _identity(),
        "assignment_is_unresolved": True,
        "known_beam_handles": _KNOWN,
    }
    params.update(kw)
    return record_beam_decision(root, **params)


def test_a_recorded_decision_is_returned_for_the_same_analysis(tmp_path: Path) -> None:
    _record(tmp_path)

    assert load_beam_decisions(tmp_path, _identity()) == {"14C98::BJ-FI-L": "BL19"}


def test_the_decision_carries_the_beam_name_and_the_author(tmp_path: Path) -> None:
    decision = _record(tmp_path)

    assert decision.beam_name == "Гр. B.19 25/55"
    assert decision.decided_by == "Александър"
    assert OPERATOR_METHOD == "OPERATOR_BEAM_DECISION"


def test_a_decision_from_another_analysis_run_is_not_inherited(tmp_path: Path) -> None:
    """The same identity gate the frame decisions use: a decision taken on a
    different DXF or a different run must never carry over silently."""

    _record(tmp_path)

    assert load_beam_decisions(tmp_path, _identity(run="R2")) == {}


def test_re_deciding_the_same_position_replaces_the_previous_answer(
    tmp_path: Path,
) -> None:
    _record(tmp_path, label="BL19")
    _record(tmp_path, label="BL30")

    assert load_beam_decisions(tmp_path, _identity()) == {"14C98::BJ-FI-L": "BL30"}


def test_withdrawing_a_decision_removes_it_and_is_recorded(tmp_path: Path) -> None:
    _record(tmp_path)

    assert (
        withdraw_beam_decision(
            tmp_path,
            annotation_handle="14C98::BJ-FI-L",
            withdrawn_by="Александър",
            note="сгрешена греда",
        )
        is True
    )
    assert load_beam_decisions(tmp_path, _identity()) == {}
    assert [entry["action"] for entry in audit_trail(tmp_path)] == ["RECORD", "WITHDRAW"]


class TestRefusals:
    def test_a_position_the_automation_already_solved_is_refused(
        self, tmp_path: Path
    ) -> None:
        """This path exists for the unproven ones only. It must never become a
        way to overrule a proven geometric assignment."""

        with pytest.raises(BeamDecisionError):
            _record(tmp_path, assignment_is_unresolved=False)

    def test_a_beam_that_does_not_exist_in_the_drawing_is_refused(
        self, tmp_path: Path
    ) -> None:
        with pytest.raises(BeamDecisionError):
            _record(tmp_path, label="BL99")

    def test_an_unsigned_decision_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(BeamDecisionError):
            _record(tmp_path, decided_by="   ")

    def test_a_decision_without_a_reason_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(BeamDecisionError):
            _record(tmp_path, note="x")

    def test_nothing_is_written_when_the_decision_is_refused(
        self, tmp_path: Path
    ) -> None:
        with pytest.raises(BeamDecisionError):
            _record(tmp_path, label="BL99")

        assert load_beam_decisions(tmp_path, _identity()) == {}
        assert audit_trail(tmp_path) == []
