"""Потвърденият профил се записва проследимо и отказва каквото не бива.

Дисциплината е същата като при решението за рамка: автор, време, самоличност на
данните, одитна следа — и мотив, когато човек решава различно от измереното.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lion_rebar.project.convention_profile_decisions import (
    ACCEPTED,
    AUDIT_NAME,
    CHANGED,
    PROFILE_NAME,
    REJECTED,
    ConventionProfileError,
    confirmed_roles,
    load_convention_profile,
    record_convention_profile,
)
from lion_rebar.project.data_identity import DataIdentity


def _identity(**промени) -> DataIdentity:
    полета = {
        "project_id": "проект-1",
        "source_sha256": "abc123",
        "source_generation_id": "генерация-1",
        "analysis_run_id": "анализ-1",
    }
    полета.update(промени)
    return DataIdentity(**полета)


def _решение(**промени) -> dict:
    ред = {
        "target_kind": "LAYER",
        "layer": "Колони",
        "block_name": "",
        "role": "REBAR_GEOMETRY",
        "verdict": ACCEPTED,
        "proposed_role": "REBAR_GEOMETRY",
        "evidence": "101 надписа имат до себе си обект със своята дължина",
        "note": "",
    }
    ред.update(промени)
    return ред


def test_приетото_предложение_се_записва_без_мотив(tmp_path: Path) -> None:
    записани = record_convention_profile(
        tmp_path,
        decisions=[_решение()],
        confirmed_by="Александър",
        identity=_identity(),
        proposer_version="1.0",
    )

    assert len(записани) == 1
    payload = json.loads((tmp_path / "working" / PROFILE_NAME).read_text(encoding="utf-8"))
    assert payload["confirmed_by"] == "Александър"
    assert payload["proposer_version"] == "1.0"
    assert payload["project_id"] == "проект-1"
    assert payload["source_sha256"] == "abc123"
    assert payload["decisions"][0]["role"] == "REBAR_GEOMETRY"
    assert payload["decisions"][0]["at"]
    # доказателството, което операторът е видял, остава в записа
    assert "101 надписа" in payload["decisions"][0]["evidence"]

    одит = (tmp_path / "working" / AUDIT_NAME).read_text(encoding="utf-8").strip().splitlines()
    assert len(одит) == 1
    assert json.loads(одит[0])["event"] == "CONVENTION_PROFILE_DECISION"


def test_смяна_на_ролята_без_мотив_се_отказва(tmp_path: Path) -> None:
    with pytest.raises(ConventionProfileError, match="мотив"):
        record_convention_profile(
            tmp_path,
            decisions=[_решение(verdict=CHANGED, role="POSITION_ANNOTATION")],
            confirmed_by="Александър",
            identity=_identity(),
        )
    assert not (tmp_path / "working" / PROFILE_NAME).exists()


def test_отхвърлянето_иска_мотив_и_не_носи_роля(tmp_path: Path) -> None:
    with pytest.raises(ConventionProfileError, match="мотив"):
        record_convention_profile(
            tmp_path,
            decisions=[_решение(verdict=REJECTED, role="")],
            confirmed_by="Александър",
            identity=_identity(),
        )
    with pytest.raises(ConventionProfileError, match="не носи роля"):
        record_convention_profile(
            tmp_path,
            decisions=[
                _решение(verdict=REJECTED, role="REBAR_GEOMETRY", note="не е желязо")
            ],
            confirmed_by="Александър",
            identity=_identity(),
        )


def test_непозната_роля_и_липсващ_автор_се_отказват(tmp_path: Path) -> None:
    with pytest.raises(ConventionProfileError, match="Непозната роля"):
        record_convention_profile(
            tmp_path,
            decisions=[_решение(role="КАКВОТО_И_ДА_Е", verdict=CHANGED, note="проба")],
            confirmed_by="Александър",
            identity=_identity(),
        )
    with pytest.raises(ConventionProfileError, match="автор"):
        record_convention_profile(
            tmp_path,
            decisions=[_решение()],
            confirmed_by="   ",
            identity=_identity(),
        )


def test_два_записа_за_един_слой_се_отказват(tmp_path: Path) -> None:
    with pytest.raises(ConventionProfileError, match="две решения"):
        record_convention_profile(
            tmp_path,
            decisions=[_решение(), _решение(note="и пак")],
            confirmed_by="Александър",
            identity=_identity(),
        )


def test_празен_профил_не_се_записва(tmp_path: Path) -> None:
    with pytest.raises(ConventionProfileError, match="нито едно решение"):
        record_convention_profile(
            tmp_path,
            decisions=[],
            confirmed_by="Александър",
            identity=_identity(),
        )


def test_профил_от_друг_чертеж_не_се_наследява(tmp_path: Path) -> None:
    record_convention_profile(
        tmp_path,
        decisions=[_решение()],
        confirmed_by="Александър",
        identity=_identity(),
    )

    свой = load_convention_profile(tmp_path, identity=_identity())
    assert свой and len(свой["decisions"]) == 1

    чужд = load_convention_profile(tmp_path, identity=_identity(source_sha256="друг"))
    assert чужд is not None
    assert чужд["decisions"] == []
    assert чужд["identity_mismatch"] == "SOURCE_SHA256_MISMATCH"


def test_отхвърлените_не_влизат_в_ролите(tmp_path: Path) -> None:
    record_convention_profile(
        tmp_path,
        decisions=[
            _решение(),
            _решение(
                layer="Plumbing",
                verdict=REJECTED,
                role="",
                proposed_role="REBAR_GEOMETRY",
                note="това е водопровод, не желязо",
            ),
        ],
        confirmed_by="Александър",
        identity=_identity(),
    )

    роли = confirmed_roles(load_convention_profile(tmp_path, identity=_identity()))
    assert роли == {"Колони": "REBAR_GEOMETRY"}


def test_потвърждаването_днес_не_пипа_замразяването(tmp_path: Path) -> None:
    """Пин на днешното поведение — и капан за деня, в който се промени.

    Профилът още НЕ влиза в разпознаването, затова записът му не мени инженерен
    резултат и не бива да обезсилва Финалния контрол. Когато профилът започне да
    се чете от разпознаването, този тест ще падне — и тогава обезсилването
    трябва да се добави НАРОЧНО, а не да се появи случайно.
    """

    from lion_rebar.project.final_control_lifecycle import FREEZE_NAME, freeze_state

    преди = freeze_state(tmp_path)
    record_convention_profile(
        tmp_path,
        decisions=[_решение()],
        confirmed_by="Александър",
        identity=_identity(),
    )

    assert freeze_state(tmp_path) == преди
    assert not (tmp_path / "working" / FREEZE_NAME).exists()
