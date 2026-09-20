"""Паспортът на чертежа предлага по съдържание и отказва при двусмислие.

Случаите тук са изведени от измерването на 2026-09-20 върху петте различни
диалекта, но са построени синтетично: проверява се ОБРАЗЕЦЪТ, не конкретен
чертеж. Няма твърдо кодирани handle-и, слоеве или бройки от определен файл.
"""
from __future__ import annotations

import math

import pytest

from lion_rebar.dxf.convention_profile import (
    PROPOSABLE_ROLES,
    propose_convention_profile,
    proposals_by_role,
)
from lion_rebar.dxf.models import DxfEntityRecord


def _text(handle: str, layer: str, текст: str, at: tuple[float, float]) -> DxfEntityRecord:
    return DxfEntityRecord(
        handle=handle,
        entity_type="TEXT",
        layer=layer,
        geometry={"insert": [at[0], at[1], 0.0]},
        text=текст,
    )


def _circle(handle: str, layer: str, at: tuple[float, float], радиус: float) -> DxfEntityRecord:
    return DxfEntityRecord(
        handle=handle,
        entity_type="CIRCLE",
        layer=layer,
        geometry={"center": [at[0], at[1], 0.0], "radius": радиус},
    )


def _label_block(
    handle: str,
    layer: str,
    block_name: str,
    тагове: dict[str, str],
) -> DxfEntityRecord:
    return DxfEntityRecord(
        handle=handle,
        entity_type="INSERT",
        layer=layer,
        block_name=block_name,
        geometry={
            "insert": [0.0, 0.0, 0.0],
            "attributes": [
                {"tag": таг, "text": стойност, "handle": f"{handle}-{таг}"}
                for таг, стойност in тагове.items()
            ],
        },
    )


def _keys_in_circles(
    брой: int,
    *,
    layer: str = "ключове",
    мащаб: float = 1.0,
    отстояние: float = 9.0,
    радиус: float = 10.0,
) -> list[DxfEntityRecord]:
    """Ключ „цифра + буква“ в своето кръгче, точно както е измерено.

    ``мащаб`` умножава координатите и радиуса — така тестът доказва, че правилото
    е за ОТНОШЕНИЕ, а не за разстояние в единици на чертежа.
    """

    записи: list[DxfEntityRecord] = []
    for индекс in range(брой):
        център = (1000.0 * индекс * мащаб, 0.0)
        записи.append(_circle(f"C{индекс}", layer, център, радиус * мащаб))
        записи.append(
            _text(
                f"T{индекс}",
                layer,
                f"{индекс + 1}a",
                (център[0] + отстояние * мащаб, 0.0),
            )
        )
    return записи


def test_ключът_се_познава_по_кръгчето() -> None:
    payload = propose_convention_profile(_keys_in_circles(6))

    предложения = proposals_by_role(payload, "POSITION_KEY")
    assert len(предложения) == 1
    предложение = предложения[0]
    assert предложение["layer"] == "ключове"
    assert предложение["counts"]["inside_circle"] == 6
    assert "кръгче" in предложение["evidence"]
    assert len(предложение["sample_handles"]) == 3


def test_правилото_е_за_отношение_не_за_разстояние() -> None:
    """Същият чертеж, увеличен десет пъти, дава същото предложение."""

    малък = propose_convention_profile(_keys_in_circles(6, мащаб=1.0))
    голям = propose_convention_profile(_keys_in_circles(6, мащаб=10.0))

    assert proposals_by_role(малък, "POSITION_KEY")[0]["counts"] == (
        proposals_by_role(голям, "POSITION_KEY")[0]["counts"]
    )


def test_кратки_надписи_далеч_от_кръгче_се_отказват() -> None:
    """Измереният фалшив кандидат: ключове по образец, но на 13 радиуса от кръг."""

    записи = _keys_in_circles(6, отстояние=140.0)

    payload = propose_convention_profile(записи)

    assert proposals_by_role(payload, "POSITION_KEY") == []
    откази = [row for row in payload["refusals"] if row["role"] == "POSITION_KEY"]
    assert len(откази) == 1
    assert "стоят в кръгче" in откази[0]["reason"]


def test_смесеният_слой_иска_потвърждение() -> None:
    записи = _keys_in_circles(6)
    for индекс in range(20):
        записи.append(_text(f"X{индекс}", "ключове", f"забележка {индекс}", (0.0, 100.0 + индекс)))

    payload = propose_convention_profile(записи)

    assert proposals_by_role(payload, "POSITION_KEY") == []
    откази = [row for row in payload["refusals"] if row["role"] == "POSITION_KEY"]
    assert откази and "смесен слой" in откази[0]["reason"]


def test_заглавието_на_зона_се_познава_по_бр_и_размер() -> None:
    записи = [
        _text("A1", "заглавия", "%%UK23, K24 (35/35) - 2бр.", (0.0, 0.0)),
        _text("A2", "заглавия", "%%UK22, K25 (25/50) - 2бр.", (0.0, 100.0)),
        _text("A3", "заглавия", "Ш 10 (Сх.арм.2)", (0.0, 200.0)),
    ]

    payload = propose_convention_profile(записи)

    предложения = proposals_by_role(payload, "ZONE_TITLE")
    assert len(предложения) == 1
    assert предложения[0]["counts"]["zone_titles"] == 3


def test_две_заглавия_не_стигат() -> None:
    записи = [
        _text("A1", "заглавия", "K23 (35/35) - 2бр.", (0.0, 0.0)),
        _text("A2", "заглавия", "K22 (25/50) - 2бр.", (0.0, 100.0)),
    ]

    payload = propose_convention_profile(записи)

    assert proposals_by_role(payload, "ZONE_TITLE") == []
    откази = [row for row in payload["refusals"] if row["role"] == "ZONE_TITLE"]
    assert откази and "не се предлага" in откази[0]["reason"]


def test_позиционните_надписи_се_познават_по_образеца_на_production_кода() -> None:
    записи = [
        _text(f"P{индекс}", "надписи", f"{10 + индекс}N14x385", (0.0, 50.0 * индекс))
        for индекс in range(6)
    ]

    payload = propose_convention_profile(записи)

    предложения = proposals_by_role(payload, "POSITION_ANNOTATION")
    assert len(предложения) == 1
    assert предложения[0]["counts"]["rebar_texts"] == 6


def test_надписът_блок_се_познава_по_стойностите_а_не_по_имената_на_таговете() -> None:
    """Таговете може да се казват как да е — диаметърът се познава по стойностите."""

    записи = [
        _label_block("B1", "надписи", "СЕМЕЙСТВО", {"БРОЙ": "90", "Д": "10", "ДЪЛЖ": "47"}),
        _label_block("B2", "надписи", "СЕМЕЙСТВО", {"БРОЙ": "1454", "Д": "10", "ДЪЛЖ": "199"}),
        _label_block("B3", "надписи", "СЕМЕЙСТВО", {"БРОЙ": "246", "Д": "12", "ДЪЛЖ": "800"}),
        _label_block("B4", "надписи", "СЕМЕЙСТВО", {"БРОЙ": "37", "Д": "14", "ДЪЛЖ": "1200"}),
    ]

    payload = propose_convention_profile(записи)

    предложения = proposals_by_role(payload, "POSITION_LABEL_BLOCK")
    assert len(предложения) == 1
    assert предложения[0]["details"]["diameter_tag"] == "Д"
    assert предложения[0]["details"]["diameter_values"] == [10, 12, 14]


def test_два_тага_с_диаметри_са_отказ() -> None:
    """Измерен случай: броят носи {6, 20, 25, 28} — всички случайно диаметри."""

    записи = [
        _label_block("B1", "надписи", "ДВУСМИСЛЕН", {"BJ": "6", "FI": "6", "L": "40"}),
        _label_block("B2", "надписи", "ДВУСМИСЛЕН", {"BJ": "20", "FI": "8", "L": "325"}),
        _label_block("B3", "надписи", "ДВУСМИСЛЕН", {"BJ": "25", "FI": "8", "L": "120"}),
        _label_block("B4", "надписи", "ДВУСМИСЛЕН", {"BJ": "28", "FI": "6", "L": "200"}),
    ]

    payload = propose_convention_profile(записи)

    assert proposals_by_role(payload, "POSITION_LABEL_BLOCK") == []
    откази = [row for row in payload["refusals"] if row["role"] == "POSITION_LABEL_BLOCK"]
    assert len(откази) == 1
    assert "решава операторът" in откази[0]["reason"]
    assert "BJ" in откази[0]["reason"] and "FI" in откази[0]["reason"]


def test_блок_без_диаметри_не_е_позиционен_надпис() -> None:
    """Осите и котите носят атрибути, но никой от тях не е диаметър."""

    записи = [
        _label_block("O1", "оси", "OS100", {"ОС": "21"}),
        _label_block("O2", "оси", "OS100", {"ОС": "37"}),
        _label_block("O3", "оси", "OS100", {"ОС": "40"}),
    ]

    payload = propose_convention_profile(записи)

    assert proposals_by_role(payload, "POSITION_LABEL_BLOCK") == []
    откази = [row for row in payload["refusals"] if row["role"] == "POSITION_LABEL_BLOCK"]
    assert откази and "не изглежда позиционен надпис" in откази[0]["reason"]


def test_паспортът_никога_не_обявява_нещо_за_шум() -> None:
    """Той предлага роли; скриването на обект е промяна в инженерния резултат."""

    записи = _keys_in_circles(6)
    записи.append(_text("H1", "хеч", "каквото и да е", (0.0, 0.0)))

    payload = propose_convention_profile(записи)

    роли = {row["role"] for row in payload["proposals"]}
    assert роли <= set(PROPOSABLE_ROLES)
    assert "NOISE" not in repr(payload)
    # нито един ключ за видимост не се връща
    for row in payload["proposals"]:
        assert "default_visible" not in row
        assert "relevant_for_analysis" not in row


@pytest.mark.parametrize("брой", [0, 1, 4])
def test_под_минимума_ключове_мълчи(брой: int) -> None:
    payload = propose_convention_profile(_keys_in_circles(брой))

    assert proposals_by_role(payload, "POSITION_KEY") == []
    assert [row for row in payload["refusals"] if row["role"] == "POSITION_KEY"] == []


def test_празният_чертеж_не_чупи_паспорта() -> None:
    payload = propose_convention_profile([])

    assert payload["proposals"] == []
    assert payload["refusals"] == []
    assert payload["summary"]["layer_count"] == 0


def test_обобщението_брои_предложенията_по_роля() -> None:
    записи = _keys_in_circles(6)
    записи += [
        _text("A1", "заглавия", "K23 (35/35) - 2бр.", (0.0, 0.0)),
        _text("A2", "заглавия", "K22 (25/50) - 2бр.", (0.0, 100.0)),
        _text("A3", "заглавия", "Ш 10 (Сх.арм.2)", (0.0, 200.0)),
    ]

    payload = propose_convention_profile(записи)

    assert payload["summary"]["role_counts"]["POSITION_KEY"] == 1
    assert payload["summary"]["role_counts"]["ZONE_TITLE"] == 1
    assert payload["summary"]["proposal_count"] == len(payload["proposals"])
    assert math.isclose(payload["proposals"][0]["confidence"], round(payload["proposals"][0]["confidence"], 3))
