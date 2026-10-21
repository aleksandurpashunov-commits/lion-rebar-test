"""Слой с геометрия, който разделителят НЕ приема, стига до екрана.

ЗАЩО. ``dxf/rebar_geometry_evidence.py`` дава три присъди. До разпознаването
стигаше само ``rebar`` (``learning/integration.discover_rebar_geometry_layers``);
``not_rebar`` и ``unknown`` се губеха мълчаливо и операторът не научаваше нито
че геометрията я има, нито защо не е приета. Паспортът на чертежа — екранът
„Какво програмата НЕ разбра“ — е точно мястото за тях.

Два от тестовете тук са доказано ЧЕРВЕНИ срещу кода отпреди промяната:

* ``test_zatvoreniyat_kontur_ne_se_predlaga_a_se_otkazva`` — старото правило
  мери само дължина и ПРЕДЛАГА контура за армировъчен слой (измерено на ``d-2``:
  74 надписа съвпадат, контрола 1). Преди промяната в отказите няма такъв ред, а
  в предложенията има.
* ``test_tankoto_dokazatelstvo_stiga_do_ekrana`` — при ЕДНО съвпадение старото
  правило мълчи (``GEOMETRY_NEAR_MISS_MATCHES`` е 2), тоест преди промяната
  списъкът с откази е празен.

Измерено 2026-10-05 върху 13-те проекта в ``C:\\lr_exp\\proekti``: **6 нови реда**
общо (най-много 3 в един чертеж) и **едно махнато предложение** — „Slab contour“
в ``d-2``. Седем проекта не се променят изобщо.
"""
from __future__ import annotations

from lion_rebar.dxf import convention_profile
from lion_rebar.dxf.convention_profile import (
    GEOMETRY_NEAR_MISS_MATCHES,
    propose_convention_profile,
    proposals_by_role,
)
from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.dxf.rebar_geometry_evidence import MIN_LENGTH_MATCHES

# --------------------------------------------------------------------------- #
# строителни блокчета — същият образец като в ``test_rebar_geometry_evidence``


def _надпис(handle: str, текст: str, at: tuple[float, float], слой: str = "надписи"):
    return DxfEntityRecord(
        handle=handle,
        entity_type="MTEXT",
        layer=слой,
        geometry={"insert": [at[0], at[1], 0.0]},
        text=текст,
    )


def _линия(handle: str, слой: str, начало, край):
    return DxfEntityRecord(
        handle=handle,
        entity_type="LINE",
        layer=слой,
        geometry={
            "start": [начало[0], начало[1], 0.0],
            "end": [край[0], край[1], 0.0],
        },
    )


def _пръти(слой: str, брой: int, дължина: float, *, x0: float = 0.0):
    """Отворени пръти, всеки с надпис до края си — армировка."""

    записи = []
    for индекс in range(брой):
        x = x0 + индекс * 500.0
        записи.append(_линия(f"B{индекс:03d}", слой, (x, 0.0), (x, дължина)))
        записи.append(
            _надпис(f"T{индекс:03d}", f"2N12x{дължина:.0f}/10", (x + 10.0, -10.0))
        )
    return записи


def _затворени_рамки(слой: str, брой: int, височина: float, ширина: float = 60.0):
    """Затворени контури, чиито дълги страни са точно колкото пише в надписа.

    Това е образецът на ``d-2`` „Slab contour“: съвпадение по дължина има, но
    всяка отсечка среща следващата с двата си края.
    """

    записи = []
    for индекс in range(брой):
        x0 = индекс * 5000.0
        ъгли = [
            (x0, 0.0),
            (x0 + ширина, 0.0),
            (x0 + ширина, височина),
            (x0, височина),
        ]
        for ъгъл in range(4):
            записи.append(
                _линия(f"K{индекс}_{ъгъл}", слой, ъгли[ъгъл], ъгли[(ъгъл + 1) % 4])
            )
        записи.append(
            _надпис(f"TK{индекс:03d}", f"2N12x{височина:.0f}/10", (x0 + 20.0, -10.0))
        )
    return записи


def _откази_за_слой(payload: dict, слой: str) -> list[dict]:
    return [
        ред
        for ред in payload["refusals"]
        if ред["layer"] == слой and ред["role"] == "REBAR_GEOMETRY"
    ]


# --------------------------------------------------------------------------- #
# случаите


def test_zatvoreniyat_kontur_ne_se_predlaga_a_se_otkazva() -> None:
    """Структурната присъда бие мярката само по дължина.

    ЧЕРВЕН срещу стария код: там контурът влиза в ПРЕДЛОЖЕНИЯТА.
    """

    записи = _затворени_рамки("КОНТУР", брой=8, височина=380.0)

    payload = propose_convention_profile(записи)

    предложени = {ред["layer"] for ред in proposals_by_role(payload, "REBAR_GEOMETRY")}
    assert "КОНТУР" not in предложени

    откази = _откази_за_слой(payload, "КОНТУР")
    assert len(откази) == 1
    причина = откази[0]["reason"]
    assert "звена от верига" in причина
    assert "не прави контура армировка" in причина


def test_tankoto_dokazatelstvo_stiga_do_ekrana() -> None:
    """Едно съвпадение: старото правило мълчи, новото казва какво е видяло.

    ЧЕРВЕН срещу стария код: там ``refusals`` е празен за този слой.
    """

    assert GEOMETRY_NEAR_MISS_MATCHES > 1, "иначе случаят не е под стария праг"
    записи = _пръти("ТЪНЪК", брой=1, дължина=240.0)
    # Още надписи, за да има чертежът армировъчен текст, но без своя геометрия.
    for индекс in range(6):
        записи.append(
            _надпис(f"X{индекс}", f"4N10x{150 + индекс * 7}/20", (9000.0, индекс * 300.0))
        )

    payload = propose_convention_profile(записи)

    assert proposals_by_role(payload, "REBAR_GEOMETRY") == []
    откази = _откази_за_слой(payload, "ТЪНЪК")
    assert len(откази) == 1
    assert "тънко доказателство" in откази[0]["reason"]
    assert откази[0]["counts"]["length_matches"] == 1


def test_otkazut_nosi_handle_i_za_proverka() -> None:
    """Отказ без примерни handle-и е твърдение, не доказателство."""

    записи = _затворени_рамки("КОНТУР", брой=8, височина=380.0)

    откази = _откази_за_слой(propose_convention_profile(записи), "КОНТУР")

    примери = откази[0]["sample_handles"]
    assert примери, "отказът трябва да сочи обекти в чертежа"
    налични = {
        запис.handle
        for запис in записи
        if str(запис.layer) == "КОНТУР"
    }
    assert set(примери) <= налични


def test_proizhodut_kazva_dali_rechnikut_poznava_sloya() -> None:
    """Операторът трябва да знае дали геометрията изобщо влиза в разпознаването."""

    записи = _пръти("СЪВСЕМ_НЕПОЗНАТ_СЛОЙ", брой=1, дължина=240.0)

    откази = _откази_за_слой(
        propose_convention_profile(записи), "СЪВСЕМ_НЕПОЗНАТ_СЛОЙ"
    )

    assert "речникът не познава слоя" in откази[0]["reason"]


def test_prietiyat_armirovachen_sloy_ne_izliza_kato_otkaz() -> None:
    """Приетото си остава прието — екранът не си противоречи."""

    записи = _пръти("АРМ_ПРОБА", брой=MIN_LENGTH_MATCHES + 3, дължина=380.0)

    payload = propose_convention_profile(записи)

    предложени = {ред["layer"] for ред in proposals_by_role(payload, "REBAR_GEOMETRY")}
    assert "АРМ_ПРОБА" in предложени
    assert _откази_за_слой(payload, "АРМ_ПРОБА") == []


def test_edin_red_na_sloy() -> None:
    """Слой, за който правилото по дължина вече е отказало, не се удвоява."""

    записи = _пръти("ДВОЕН", брой=GEOMETRY_NEAR_MISS_MATCHES, дължина=240.0)
    for индекс in range(6):
        записи.append(
            _надпис(f"X{индекс}", f"4N10x{150 + индекс * 7}/20", (9000.0, индекс * 300.0))
        )

    payload = propose_convention_profile(записи)

    assert len(_откази_за_слой(payload, "ДВОЕН")) == 1


def test_drugite_roli_ne_se_menyat_ot_novata_stapka(monkeypatch) -> None:
    """Защита на работещото: новата стъпка пипа САМО ролята REBAR_GEOMETRY.

    Мери се инвариантът, не предположение за него: същите обекти минават веднъж
    с изключена нова стъпка и веднъж с включена, и всичко извън
    ``REBAR_GEOMETRY`` трябва да е дума по дума същото. Положителна контрола —
    при включената стъпка редовете за ``REBAR_GEOMETRY`` СЕ различават, иначе
    тестът не мери нищо.
    """

    записи = _пръти("АРМ_ПРОБА", брой=MIN_LENGTH_MATCHES + 3, дължина=380.0)
    записи += _затворени_рамки("КОНТУР", брой=8, височина=250.0)
    записи.append(_надпис("Z1", "4 бр. 35/35 (Сх.арм.2)", (0.0, 9000.0), слой="зони"))
    записи.append(_надпис("Z2", "6 бр. 40/40 (Сх.арм.3)", (0.0, 9400.0), слой="зони"))
    записи.append(_надпис("Z3", "8 бр. 30/30 (Сх.арм.4)", (0.0, 9800.0), слой="зони"))

    monkeypatch.setattr(
        convention_profile,
        "_geometry_evidence_verdicts",
        lambda *args, **kwargs: ([], set()),
    )
    без_стъпката = propose_convention_profile(записи)
    monkeypatch.undo()
    със_стъпката = propose_convention_profile(записи)

    def _извън_геометрията(редове):
        return [ред for ред in редове if ред["role"] != "REBAR_GEOMETRY"]

    assert _извън_геометрията(със_стъпката["proposals"]) == _извън_геометрията(
        без_стъпката["proposals"]
    )
    assert _извън_геометрията(със_стъпката["refusals"]) == _извън_геометрията(
        без_стъпката["refusals"]
    )
    # и контролата: по ролята REBAR_GEOMETRY стъпката НАИСТИНА променя нещо
    assert _извън_геометрията(без_стъпката["proposals"]), "случаят е без други роли"
    assert със_стъпката["refusals"] != без_стъпката["refusals"]
