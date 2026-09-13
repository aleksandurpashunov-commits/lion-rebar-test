"""Regression tests for A1 — the F009 locked-profile fallback removal.

A1 changes exactly one line in ``_points_from_shape``:

    -        else _FIXED_MASTER_SEGMENT_PROFILES.get(shape.code.strip().upper())
    +        else None

Before A1 the locked F009 control profile (8 + 79 + 15 + 79 + 8 = 189 cm) was
forced onto the topology-only fallback.  Any F009 geometry with different
parametric lengths therefore failed the ratio gate, F009 could never win, and
F010 took the position silently.

These three tests pin the intended behaviour.  They are written so they run
under pytest and also standalone:  ``python3 test_f009_locked_profile.py``.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

# Standalone execution: put the release root on sys.path so that ``lion_rebar``
# imports without an externally set PYTHONPATH.  Harmless under pytest.
_RELEASE_ROOT = str(Path(__file__).resolve().parents[1])
if _RELEASE_ROOT not in sys.path:
    sys.path.insert(0, _RELEASE_ROOT)

from lion_rebar.dxf.recognition_policy import DEFAULT_RECOGNITION_POLICY
from lion_rebar.dxf.shape_recognition import (
    _FIXED_MASTER_SEGMENT_PROFILES,
    ShapeReferenceProfile,
    match_master_shapes,
    points_from_reference_profile,
    reference_profiles_for_shape,
)
from lion_rebar.engine.package_io import inspect_engine_bundle
from lion_rebar.engine.shape_index import EngineShapeIndex

ENGINE_CANDIDATES = (
    "engine_updates/lion-rebar-master_v0.3.6_DRAFT.lionengine",
    "../../downloads/lion-rebar-master_v0.3.6_DRAFT.lionengine",
)
LOCKED_F009_PROFILE = (8.0, 79.0, 15.0, 79.0, 8.0)


def _load_engine():
    root = Path(__file__).resolve().parents[1]
    for relative in ENGINE_CANDIDATES:
        path = (root / relative).resolve()
        if path.is_file():
            engine, _manifest, _warnings = inspect_engine_bundle(path)
            return engine
    raise AssertionError("Engine v0.3.6 bundle not found next to the release tree")


def _shape(engine, code: str):
    for shape in engine.shapes:
        if shape.code.strip().upper() == code:
            return shape
    raise AssertionError(f"{code} missing from the Engine package")


def _scaled_points(engine, code: str, factors: tuple[float, ...]):
    """Build a valid parametric variant of one MASTER shape."""
    shape = _shape(engine, code)
    base = reference_profiles_for_shape(shape, engine.tests)[0].segment_lengths_cm
    assert len(base) == len(factors)
    lengths = tuple(
        float(value) * float(factor) for value, factor in zip(base, factors)
    )
    profile = ShapeReferenceProfile(
        profile_id=f"A1-REG-{code}",
        segment_lengths_cm=lengths,
        source="A1_REGRESSION",
    )
    return points_from_reference_profile(shape, profile), lengths


def test_f009_locked_control_profile_remains_reachable() -> None:
    """A1 must not delete the locked profile — only stop forcing it."""
    engine = _load_engine()
    shape = _shape(engine, "F009")

    assert _FIXED_MASTER_SEGMENT_PROFILES.get("F009") == LOCKED_F009_PROFILE, (
        "the locked F009 control geometry must stay declared in the matcher"
    )

    profiles = reference_profiles_for_shape(shape, engine.tests)
    assert profiles, "F009 must still expose at least one control profile"
    totals = [
        sum(profile.segment_lengths_cm)
        for profile in profiles
    ]
    assert any(math.isclose(total, 189.0, rel_tol=1e-6) for total in totals), (
        "the approved 8+79+15+79+8 = 189 cm control profile must remain "
        f"reachable through reference_profiles_for_shape; got totals {totals}"
    )


def test_f009_parametric_variant_is_recognised_as_f009() -> None:
    """The core A1 defect: a scaled F009 used to be reported as F010."""
    engine = _load_engine()
    index = EngineShapeIndex.from_engine(engine)
    points, lengths = _scaled_points(
        engine, "F009", (1.30, 0.80, 1.55, 0.80, 1.30)
    )

    match = match_master_shapes(
        points,
        engine.shapes,
        policy=DEFAULT_RECOGNITION_POLICY,
        shape_index=index,
    )
    assert match is not None, f"no candidate at all for F009 lengths {lengths}"
    assert match.shape_code.strip().upper() == "F009", (
        "a parametric F009 must resolve to F009 once the locked profile is no "
        f"longer forced onto the topology-only fallback; got {match.shape_code}"
    )


def test_f009_f010_competition_is_flagged_not_silent() -> None:
    """A1 removes the silent risk; the dispute must stay visible."""
    engine = _load_engine()
    index = EngineShapeIndex.from_engine(engine)

    for code, factors in (
        ("F009", (1.30, 0.80, 1.55, 0.80, 1.30)),
        ("F010", (0.90, 1.40, 0.70, 1.65, 1.10)),
    ):
        points, lengths = _scaled_points(engine, code, factors)
        match = match_master_shapes(
            points,
            engine.shapes,
            policy=DEFAULT_RECOGNITION_POLICY,
            shape_index=index,
        )
        assert match is not None, f"{code}: no candidate for {lengths}"
        ambiguous = {
            item.strip().upper() for item in match.ambiguous_shape_codes
        }
        assert ambiguous, (
            f"{code}: the F009/F010 competition must be reported as an "
            "ambiguity so a wrong top-1 can never be silent"
        )
        assert {"F009", "F010"} <= ambiguous, (
            f"{code}: both competing MASTER codes must appear in the "
            f"ambiguity set; got {sorted(ambiguous)}"
        )
        assert match.shape_code.strip().upper() != "" and not (
            match.shape_code.strip().upper() != code and not ambiguous
        ), f"{code}: a wrong top-1 must never be unflagged"


if __name__ == "__main__":
    failures = 0
    for name, test in sorted(globals().items()):
        if not name.startswith("test_") or not callable(test):
            continue
        try:
            test()
        except AssertionError as error:
            failures += 1
            print(f"FAILED {name}: {error}")
        else:
            print(f"PASSED {name}")
    raise SystemExit(1 if failures else 0)
