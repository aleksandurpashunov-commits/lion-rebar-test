"""Надпис по оста описва прът в СВОЯТА посока, не напряко (rc44).

Собственик, 2026-09-19, върху „Плоча кота 14.90“: „тази позиция искам да я
провериш, мисля че я дублира“.

Не беше дубликат. `13N12x240` стои два пъти — веднъж хоризонтално и веднъж
вертикално — защото описва мрежа 240 × 240 с по 13 пръта във всяка посока.
Двата пръта се пресичат и са еднакво дълги, затова разстоянието не ги дели.

Но в **4 от 12** такива надписа matcher-ът беше вързал КРЪСТОСАНО:
хоризонталният надпис сочеше вертикалния прът и обратното. Количествата не се
менят (двата пръта са еднакви), но маркерът в DXF и проследимостта сочат
желязото в другата посока — тиха грешка, която описът не показва.
"""
from __future__ import annotations

from pathlib import Path

from lion_rebar.dxf.batch_geometry_report import _prefer_annotation_axis_candidate
from lion_rebar.dxf.geometry_candidates import (
    build_geometry_candidate_context,
    find_geometry_candidates,
)
from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.dxf.recognition_policy import RecognitionPolicy
from lion_rebar.engine.package_io import inspect_engine_bundle
from lion_rebar.engine.shape_index import EngineShapeIndex


def _engine():
    path = (
        Path(__file__).resolve().parents[1]
        / "engine_updates"
        / "lion-rebar-master_v0.3.14_F005_INDEPENDENT_ARMS_PRODUCTION_DRAFT.lionengine"
    )
    return inspect_engine_bundle(path)[0]


#: Двата пръта от мрежата: хоризонтален и вертикален, по 240, пресичащи се.
_ХОРИЗОНТАЛЕН = DxfEntityRecord(
    "BAR-H",
    "LINE",
    "Arm - N12",
    {"start": [0.0, 0.0, 0.0], "end": [240.0, 0.0, 0.0]},
)
_ВЕРТИКАЛЕН = DxfEntityRecord(
    "BAR-V",
    "LINE",
    "Arm - N12",
    {"start": [120.0, -120.0, 0.0], "end": [120.0, 120.0, 0.0]},
)


def _надпис(rotation: float, x: float, y: float) -> DxfEntityRecord:
    return DxfEntityRecord(
        "ANN",
        "TEXT",
        "ARMTXT",
        {"insert": [x, y, 0.0], "height": 12.0, "rotation": rotation},
        "13N12x240",
    )


def _анализ(annotation, entities):
    engine = _engine()
    policy = RecognitionPolicy.from_settings(engine.settings)
    context = build_geometry_candidate_context(entities, policy=policy)
    analysis = find_geometry_candidates(
        annotation,
        entities,
        max_candidates=8,
        shape_definitions=engine.shapes,
        control_tests=engine.tests,
        policy=policy,
        context=context,
        shape_index=EngineShapeIndex.from_shapes(engine.shapes),
    )
    return analysis, context


def _водещ(analysis) -> str:
    return analysis.candidates[0].handles[0] if analysis.candidates else ""


class TestПосокаНаНадписа:
    def test_хоризонталният_надпис_взима_хоризонталния_прът(self) -> None:
        """Вертикалният прът е по-близо, но надписът не описва него."""

        annotation = _надпис(0.0, 118.0, 6.0)
        entities = [_ХОРИЗОНТАЛЕН, _ВЕРТИКАЛЕН, annotation]
        analysis, context = _анализ(annotation, entities)
        # Без правилото matcher-ът избира НАПРЯКО — точно дефектът от чертежа.
        assert _водещ(analysis) == "BAR-V"

        поправен = _prefer_annotation_axis_candidate(annotation, analysis, context)

        assert _водещ(поправен) == "BAR-H"

    def test_вертикалният_надпис_взима_вертикалния_прът(self) -> None:
        annotation = _надпис(90.0, 126.0, -4.0)
        entities = [_ХОРИЗОНТАЛЕН, _ВЕРТИКАЛЕН, annotation]
        analysis, context = _анализ(annotation, entities)
        assert _водещ(analysis) == "BAR-H"

        поправен = _prefer_annotation_axis_candidate(annotation, analysis, context)

        assert _водещ(поправен) == "BAR-V"


class TestКогаНеСеМеси:
    def test_наклонен_надпис_не_мени_нищо(self) -> None:
        """Правилото важи само за надпис по оста."""

        annotation = _надпис(37.0, 118.0, 6.0)
        entities = [_ХОРИЗОНТАЛЕН, _ВЕРТИКАЛЕН, annotation]
        analysis, context = _анализ(annotation, entities)

        поправен = _prefer_annotation_axis_candidate(annotation, analysis, context)

        assert _водещ(поправен) == _водещ(analysis)

    def test_един_кандидат_остава_както_е(self) -> None:
        annotation = _надпис(0.0, 118.0, 6.0)
        entities = [_ВЕРТИКАЛЕН, annotation]
        analysis, context = _анализ(annotation, entities)

        поправен = _prefer_annotation_axis_candidate(annotation, analysis, context)

        assert поправен is analysis

    def test_различна_дължина_не_се_разменя(self) -> None:
        """Разменят се само взаимозаменяеми кандидати."""

        къс = DxfEntityRecord(
            "BAR-SHORT",
            "LINE",
            "Arm - N12",
            {"start": [0.0, 0.0, 0.0], "end": [180.0, 0.0, 0.0]},
        )
        annotation = _надпис(0.0, 118.0, 6.0)
        entities = [къс, _ВЕРТИКАЛЕН, annotation]
        analysis, context = _анализ(annotation, entities)
        преди = _водещ(analysis)

        поправен = _prefer_annotation_axis_candidate(annotation, analysis, context)

        if преди == "BAR-V":
            assert _водещ(поправен) == "BAR-V"
