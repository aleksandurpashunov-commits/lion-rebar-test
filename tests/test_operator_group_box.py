"""Two clicks claim a whole group for one beam (rc44).

Owner, 2026-09-13: "защо не добавихме функция която да прави рамка с два
клика, правоъгълна или квадратна. Само начало-край?"

The box is not a new source of truth. It resolves to ordinary beam decisions
-- one per position, each with its own author, reason and audit line -- so
there is no second mechanism that can drift from the first.

Coordinates below are the real s-9 geometry: the eight unresolved positions
sit above and to the left of their beam titles, which is exactly why the
distance heuristic could not place them and why a drawn box can.
"""
from __future__ import annotations

import pytest

from lion_rebar.export.operator_group_box import (
    OperatorBoxError,
    normalise_box,
    resolve_operator_box,
)

# handle, title, x, y
_TITLES = [
    ("BL19", "Гр. B.19 25/55", 113837.0, 2678.0),
    ("BL20", "Гр. B.20 25/40", 114559.0, 2678.0),
    ("BL25", "Гр. B.25 25/55R", 113941.0, 2193.0),
]
# handle, x, y
_UNRESOLVED = [
    ("14C11::BJ-FI-L", 113382.0, 2762.0),
    ("14C1F::BJ-FI-L", 113386.0, 2816.0),
    ("14C66::BJ-FI-L", 113376.0, 2879.0),
    ("14CE7::BJ-FI-L", 113769.0, 2870.0),
    ("14C92::BJ-FI-L", 114211.0, 2806.0),
    ("14FB6::BJ-FI-L", 113594.0, 2279.0),
]


def _resolve(start, end):
    return resolve_operator_box(
        start, end, beam_titles=_TITLES, unresolved=_UNRESOLVED
    )


def test_the_drag_direction_does_not_matter() -> None:
    assert normalise_box((10.0, 20.0), (0.0, 5.0)) == (0.0, 5.0, 10.0, 20.0)
    assert normalise_box((0.0, 5.0), (10.0, 20.0)) == (0.0, 5.0, 10.0, 20.0)


def test_one_box_claims_every_unresolved_position_inside_it() -> None:
    """Around Гр. B.19 and the four details drawn above and left of it."""

    resolution = _resolve((113300.0, 2600.0), (113900.0, 2950.0))

    assert resolution.beam_label_handle == "BL19"
    assert resolution.beam_title == "Гр. B.19 25/55"
    assert set(resolution.annotation_handles) == {
        "14C11::BJ-FI-L",
        "14C1F::BJ-FI-L",
        "14C66::BJ-FI-L",
        "14CE7::BJ-FI-L",
    }


def test_a_position_outside_the_box_is_not_claimed() -> None:
    resolution = _resolve((113300.0, 2600.0), (113900.0, 2950.0))

    assert "14C92::BJ-FI-L" not in resolution.annotation_handles
    assert "14FB6::BJ-FI-L" not in resolution.annotation_handles


class TestRefusals:
    def test_a_box_without_a_beam_title_is_refused(self) -> None:
        """Requiring the title inside is what makes this two clicks instead of
        two clicks plus a guess."""

        with pytest.raises(OperatorBoxError, match="няма заглавие"):
            _resolve((113300.0, 2750.0), (113800.0, 2950.0))

    def test_a_box_covering_two_beams_is_refused(self) -> None:
        with pytest.raises(OperatorBoxError, match="2 заглавия"):
            _resolve((113300.0, 2600.0), (114700.0, 2950.0))

    def test_a_box_with_no_unresolved_position_is_refused(self) -> None:
        """A drawn box must never touch an assignment the geometry already
        proved."""

        with pytest.raises(OperatorBoxError, match="няма позиция"):
            _resolve((113900.0, 2100.0), (114000.0, 2250.0))

    def test_a_degenerate_box_is_refused(self) -> None:
        with pytest.raises(OperatorBoxError, match="нулева"):
            _resolve((113300.0, 2600.0), (113300.0, 2950.0))


# --------------------------------------------------------------------------
# The box resolves into ordinary beam decisions, one per position.
# --------------------------------------------------------------------------


def test_a_drawn_box_records_one_decision_per_position(tmp_path) -> None:
    from lion_rebar.export.beam_decisions import load_beam_decisions, record_box_decisions
    from lion_rebar.project.data_identity import DataIdentity

    identity = DataIdentity(
        project_id="s-9", source_sha256="SHA", source_generation_id="G", analysis_run_id="R"
    )
    resolution = _resolve((113300.0, 2600.0), (113900.0, 2950.0))

    recorded = record_box_decisions(
        tmp_path,
        resolution,
        decided_by="Александър",
        note="рамка около Гр. B.19",
        identity=identity,
        known_beam_handles={handle: title for handle, title, _x, _y in _TITLES},
    )

    assert len(recorded) == 4
    assert {d.beam_name for d in recorded} == {"Гр. B.19 25/55"}
    assert set(load_beam_decisions(tmp_path, identity)) == set(
        resolution.annotation_handles
    )


def test_a_refused_box_leaves_nothing_behind(tmp_path) -> None:
    """All or nothing: a half-recorded box would be worse than none."""

    from lion_rebar.export.beam_decisions import (
        BeamDecisionError,
        audit_trail,
        load_beam_decisions,
        record_box_decisions,
    )
    from lion_rebar.project.data_identity import DataIdentity

    identity = DataIdentity(
        project_id="s-9", source_sha256="SHA", source_generation_id="G", analysis_run_id="R"
    )
    resolution = _resolve((113300.0, 2600.0), (113900.0, 2950.0))

    with pytest.raises(BeamDecisionError):
        record_box_decisions(
            tmp_path,
            resolution,
            decided_by="Александър",
            note="x",  # too short: refused by validate_decision
            identity=identity,
            known_beam_handles={handle: title for handle, title, _x, _y in _TITLES},
        )

    assert load_beam_decisions(tmp_path, identity) == {}
    assert audit_trail(tmp_path) == []
