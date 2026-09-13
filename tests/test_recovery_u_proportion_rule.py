"""Recovery.2: owner's proportion rule for the declared F003/F005 U pair.

The owner's engineering definition (2026-08-06, изрично потвърдена):

* F003 (прът с два винкела): двата крайни винкела -- равни или не -- са
  далеч по-къси от средната част (пример 10N14x450 = 25-400-25);
* F005 (фиба): двете рамена са равни (или почти) и по-дълги от средния
  сегмент -- средното е най-късото (пример 10N14x150 = 70-10-70);
* граничната зона остава видим конфликт за ръчен преглед;
* фиби се срещат и в гредови рамки, а текстът "фиби" може да липсва --
  затова правилото е пропорционално, не рамково и не текстово.

The rule is executable ONLY for a pair the ENGINE itself declares mutually
distinct (``recognition.distinct_from``) where exactly one side declares
``requires_equal_end_arms`` (v0.3.13+).  Engines without the declaration
(v0.3.6) keep the plain conflict -- proportions alone are not allowed to
resolve an undeclared pair.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lion_rebar.dxf.geometry_candidates import find_geometry_candidates
from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.engine.package_io import inspect_engine_bundle

V0313 = "lion-rebar-master_v0.3.13_F003_F005_DISTINCT_CONTEXT_PRODUCTION_DRAFT.lionengine"
V036 = "lion-rebar-master_v0.3.6_DRAFT.lionengine"


def _engine(name):
    bundle = Path(__file__).resolve().parents[1] / "engine_updates" / name
    if not bundle.exists():  # pragma: no cover - shipped with the release
        pytest.skip(f"Engine bundle is not present: {name}")
    engine, _manifest, _names = inspect_engine_bundle(bundle)
    return engine


def _u_case(first: float, middle: float, last: float, text: str):
    # Слоят кодира диаметъра от надписа (напр. 10N14... -> ARM-N14), както в
    # реалните чертежи; несъвпадение би било твърд отказ по Закон 2.
    diameter = text.upper().split("N", 1)[1].split("X", 1)[0].strip()
    layer = f"ARM-N{diameter}"
    annotation = DxfEntityRecord(
        "A1",
        "BLOCK_ATTR_TEXT",
        layer,
        {"insert": [middle / 2.0, -25.0, 0.0]},
        text,
    )
    entities = [
        annotation,
        DxfEntityRecord(
            "G1",
            "LINE",
            layer,
            {"start": [0.0, first, 0.0], "end": [0.0, 0.0, 0.0]},
        ),
        DxfEntityRecord(
            "G2",
            "LINE",
            layer,
            {"start": [0.0, 0.0, 0.0], "end": [middle, 0.0, 0.0]},
        ),
        DxfEntityRecord(
            "G3",
            "LINE",
            layer,
            {"start": [middle, 0.0, 0.0], "end": [middle, last, 0.0]},
        ),
    ]
    return annotation, entities


def _top(engine, annotation, entities):
    analysis = find_geometry_candidates(
        annotation,
        entities,
        shape_definitions=engine.shapes,
        control_tests=engine.tests,
    )
    assert analysis.candidates
    return analysis.candidates[0]


def test_short_end_hooks_resolve_to_clean_f003_v0313():
    """Owner example 10N14x450 = 25-400-25: hooks far shorter than the
    middle -> clean F003 with a recorded contextual rule, no flag."""
    engine = _engine(V0313)
    top = _top(engine, *_u_case(25.0, 400.0, 25.0, "10N14x450"))
    assert top.master_shape_code == "F003"
    assert "F005" not in set(top.master_ambiguous_shape_codes), (
        f"proportions prove F003 (25 << 400) but the conflict survived: "
        f"amb={top.master_ambiguous_shape_codes}"
    )
    assert top.contextual_rule_code == "U_PROPORTION_F003"


def test_long_equal_arms_resolve_to_clean_f005_v0313():
    """Owner example 10N14x150 = 70-10-70: equal arms longer than the middle
    -> clean F005 (фиба) with a recorded contextual rule, no flag."""
    engine = _engine(V0313)
    top = _top(engine, *_u_case(70.0, 10.0, 70.0, "10N14x150"))
    assert top.master_shape_code == "F005"
    assert "F003" not in set(top.master_ambiguous_shape_codes), (
        f"proportions prove F005 (70 > 10) but got "
        f"top1={top.master_shape_code} amb={top.master_ambiguous_shape_codes}"
    )
    assert top.contextual_rule_code == "U_PROPORTION_F005"


def test_real_beam_class_15_295_15_resolves_to_f003_v0313():
    """The real drawing class (20N8x325 = 15/295/15) is exactly the owner's
    F003 definition; it must stop landing in manual review."""
    engine = _engine(V0313)
    top = _top(engine, *_u_case(15.0, 295.0, 15.0, "20N8x325"))
    assert top.master_shape_code == "F003"
    assert "F005" not in set(top.master_ambiguous_shape_codes)
    assert top.contextual_rule_code == "U_PROPORTION_F003"


def test_boundary_zone_keeps_the_visible_conflict_v0313():
    """Arms within the tolerance band around the middle stay FLAGGED."""
    engine = _engine(V0313)
    top = _top(engine, *_u_case(100.0, 100.0, 100.0, "10N14x300"))
    assert {"F003", "F005"} <= set(top.master_ambiguous_shape_codes)
    assert top.contextual_rule_code is None


def test_engine_without_declaration_keeps_the_conflict_v036():
    """v0.3.6 does not declare the pair distinct: proportions alone must not
    resolve an undeclared pair -- the conflict stays."""
    engine = _engine(V036)
    top = _top(engine, *_u_case(25.0, 400.0, 25.0, "10N14x450"))
    assert {"F003", "F005"} <= set(top.master_ambiguous_shape_codes)
    assert top.contextual_rule_code is None
