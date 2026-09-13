"""Recovery regression: instance-level F003/F005 indistinguishability.

Every valid F005 instance (arms sharing the repeated length code ``L1``) is
also a valid F003 instance (three independent codes, no required relations).
When such a palindromic instance additionally lands close to one of F003's
control profiles -- both shipped profiles, T-F003-001 = (15, 300, 15) and
T-F003-002 = (30, 50, 30), are themselves palindromic in every shipped Engine
including v0.3.13 -- the profile branch wins by far more than
``master_ambiguity_score_gap`` and the score-gap ambiguity stays silent.
The mirror case (an exact T-F005-001 hit reported as unflagged F005) is the
same defect in the opposite direction.

Engine v0.3.13 declares ``distinct_from``/``requires_equal_end_arms`` for the
pair, but no production module consumes those keys, so the declaration alone
changes nothing (проверено: пробата дава идентични резултати с v0.3.6).

The rule under test: when the winner and another applicable shape fully
accept the same concrete instance (same topology plus each shape's own
required relations) and they are not the same engineering identity, the
specific MASTER code cannot be proven from geometry -- the conflict is
mandatory no matter how large the profile-driven score gap is (Закон 16,
ZERO SILENT WRONG).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lion_rebar.dxf.recognition_policy import RecognitionPolicy
from lion_rebar.dxf.shape_recognition import match_master_shapes
from lion_rebar.engine.package_io import inspect_engine_bundle
from lion_rebar.engine.shape_index import EngineShapeIndex

ENGINE_BUNDLES = (
    "lion-rebar-master_v0.3.13_F003_F005_DISTINCT_CONTEXT_PRODUCTION_DRAFT.lionengine",
    "lion-rebar-master_v0.3.6_DRAFT.lionengine",
)

# Segment proportions (cm) of the real silent-wrong holdout rows confirmed in
# the v0.1.41 audit (TEST_HOLDOUT_01): canonical ratios × geometry length.
PALINDROMIC_CASES = (
    ("клас 15510 (20N8x325)", (15.0, 294.81, 15.0)),
    ("клас 1593A (20N8x340)", (15.0, 310.0, 15.0)),
    ("клас 15AB3 (20N10x360)", (15.0, 330.0, 15.0)),
    ("клас 29AFA (8N10x126)", (35.0, 56.07, 35.0)),
)


@pytest.fixture(scope="module", params=ENGINE_BUNDLES)
def engine_fixture(request):
    bundle = Path(__file__).resolve().parents[1] / "engine_updates" / request.param
    if not bundle.exists():  # pragma: no cover - shipped with the release
        pytest.skip(f"Engine bundle is not present: {request.param}")
    engine, _manifest, _names = inspect_engine_bundle(bundle)
    shapes = tuple(engine.shapes)
    tests = tuple(engine.tests or ())
    policy = RecognitionPolicy.from_settings(engine.settings)
    return shapes, tests, EngineShapeIndex.from_shapes(shapes), policy


def _identify(points, shapes, tests, index, policy):
    return match_master_shapes(
        points,
        shapes,
        policy=policy,
        control_tests=tests,
        shape_index=index,
    )


def _u_points(first, middle, last):
    return ((0.0, first), (0.0, 0.0), (middle, 0.0), (middle, last))


@pytest.mark.parametrize("label,lengths", PALINDROMIC_CASES)
def test_palindromic_profile_win_never_hides_the_f005_conflict(
    engine_fixture, label, lengths
):
    shapes, tests, index, policy = engine_fixture
    match = _identify(_u_points(*lengths), shapes, tests, index, policy)
    assert match is not None
    assert match.shape_code in {"F003", "F005"}
    assert {"F003", "F005"} <= set(match.ambiguous_shape_codes), (
        f"{label}: palindromic U ({lengths}) was reported as an unflagged "
        f"{match.shape_code} via profile {match.reference_profile_id}; the "
        "F003/F005 conflict is mandatory because both shapes fully accept "
        "this instance"
    )


def test_f005_profile_win_surfaces_the_same_mutual_conflict(engine_fixture):
    """The conflict is symmetric: an exact T-F005-001 profile win (61/18/61)
    is still an instance F003 fully accepts, so it must be flagged too."""
    shapes, tests, index, policy = engine_fixture
    match = _identify(_u_points(61.0, 18.0, 61.0), shapes, tests, index, policy)
    assert match is not None
    assert {"F003", "F005"} <= set(match.ambiguous_shape_codes)


def test_clearly_asymmetric_u_stays_a_clean_unflagged_f003(engine_fixture):
    """Guard against over-flagging: when the arms differ far beyond the
    relation tolerance F005 is not applicable and F003 must stay clean."""
    shapes, tests, index, policy = engine_fixture
    match = _identify(_u_points(20.0, 280.0, 45.0), shapes, tests, index, policy)
    assert match is not None
    assert match.shape_code == "F003"
    assert "F005" not in set(match.ambiguous_shape_codes)


def test_topology_only_palindromic_u_keeps_its_existing_conflict(engine_fixture):
    """The already-safe topology path (proportions far from any F003 control
    profile) must keep flagging exactly as before the fix."""
    shapes, tests, index, policy = engine_fixture
    match = _identify(_u_points(140.0, 60.0, 140.0), shapes, tests, index, policy)
    assert match is not None
    assert {"F003", "F005"} <= set(match.ambiguous_shape_codes)
