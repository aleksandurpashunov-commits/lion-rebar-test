"""Ведомост, начертана в чертежа като таблица от надписи и линии.

Образецът е с ИСТИНСКИТЕ координати от
``S-_H01-1001_Rev.0_2020-09-23_Bottom_Reinforcement`` — заглавният ред на
y = 4393,9, вертикалните прегради на лявата полутаблица
(145,0 · 203,7 · 262,5 · 321,3 · 405,0 · 743,7 · 845,2 · 946,6) и първите два
записа.

Какво пазят тестовете:

* колоните идват от НАЧЕРТАНАТА мрежа, не от подравняването на числата —
  измерено, числото стои до 17 единици вляво от заглавието си и котвите се
  застъпват;
* всеки ред минава през СОБСТВЕНАТА контрола на ведомостта
  (брой × единична = обща). Контролата е на проектанта, не наша;
* отказът е по-добър от догадката: липсващо заглавие, нестандартен диаметър
  или две числа в една клетка не дават ред.

Измерено при прочита на целия чертеж: 106 реда, 6 отказа, обща маса 7123,2 kg,
и НИТО ЕДИН ред не пада на контролата.
"""
from __future__ import annotations

import pytest

from lion_rebar.dxf.drawn_schedule import read_drawn_schedule

ВИСОЧИНА = 11.299
СЛОЙ = "s-kota-sec"

#: Истинските x на преградите в лявата полутаблица.
ПРЕГРАДИ = (145.0, 203.7, 262.5, 321.3, 405.0, 743.7, 845.2, 946.6)

#: Истинските x на заглавията.
ЗАГЛАВЕН_РЕД = (
    (156.7, "Mark"),
    (217.6, "Pcs"),
    (287.1, "Ø"),
    (341.7, "Single"),
    (473.8, "Dimensioned bending shape"),
    (775.3, "Total"),
    (874.8, "Mass"),
)
Y_ЗАГЛАВИЕ = 4393.9


def _надпис(handle: str, x: float, y: float, текст: str, слой: str = СЛОЙ) -> dict:
    return {
        "handle": handle,
        "entity_type": "MTEXT",
        "layer": слой,
        "text": текст,
        "geometry": {"insert": [x, y, 0.0], "char_height": ВИСОЧИНА},
    }


def _линия(handle: str, x: float, y0: float, y1: float) -> dict:
    return {
        "handle": handle,
        "entity_type": "LINE",
        "layer": СЛОЙ,
        "text": None,
        "geometry": {"start": [x, y0, 0.0], "end": [x, y1, 0.0]},
    }


def _мрежа() -> list[dict]:
    """Преградите, начертани на отрязъци — по няколко на всяка."""

    линии: list[dict] = []
    for индекс, x in enumerate(ПРЕГРАДИ):
        за_ред = 4330.0
        for стъпка in range(6):
            линии.append(
                _линия(f"L{индекс}_{стъпка}", x, за_ред - 120.0, за_ред)
            )
            за_ред -= 50.0
    return линии


def _таблица(*редове: tuple[float, dict[str, str]]) -> list[dict]:
    """Заглавен ред, мрежа и подадените записи."""

    записи = [
        _надпис(f"H{индекс}", x, Y_ЗАГЛАВИЕ, текст)
        for индекс, (x, текст) in enumerate(ЗАГЛАВЕН_РЕД)
    ]
    записи += _мрежа()
    for номер, (y, клетки) in enumerate(редове):
        for име, (x, стойност) in клетки.items():
            записи.append(_надпис(f"R{номер}{име}", x, y, стойност))
    return записи


#: Първите два записа, с истинските си x и стойности.
ЗАПИС_1 = {
    "mark": (155.2, "1"),
    "pcs": (207.0, "16"),
    "dia": (284.1, "12"),
    "single": (334.7, "4.50"),
    "total": (757.6, "72.00"),
    "mass": (867.7, "63.94"),
}
ЗАПИС_2 = {
    "mark": (155.2, "2"),
    "pcs": (207.0, "16"),
    "dia": (284.1, "12"),
    "single": (334.7, "2.35"),
    "total": (757.6, "37.60"),
    "mass": (867.7, "33.39"),
}


# --------------------------------------------------------------------------- #
# истинският случай


def test_vedomostta_se_chete_ot_nachertanata_mrezha() -> None:
    ведомост = read_drawn_schedule(_таблица((4289.1, ЗАПИС_1), (4239.1, ЗАПИС_2)))

    assert ведомост is not None
    assert ведомост.layer == СЛОЙ
    assert [ред.mark for ред in ведомост.rows] == ["1", "2"]
    първи = ведомост.rows[0]
    assert първи.pcs == 16
    assert първи.diameter_mm == 12.0
    assert първи.single_length_cm == pytest.approx(450.0)
    assert първи.total_length_cm == pytest.approx(7200.0)
    assert първи.mass_kg == pytest.approx(63.94)
    assert ведомост.total_mass_kg == pytest.approx(63.94 + 33.39)


def test_sborat_otdelya_svoite_razmeri_ot_chuzhdite() -> None:
    """ДВА свидетеля: клетката дава кандидатите, сборът избира своите.

    Истинският случай — марка 1 е 450 cm, а в клетката ѝ падат три числа:
    10, 220 и 440. Само ``10 + 440`` затваря, и 220 (от съседната скица)
    отпада. Това е същината на искането: не по близост, а по измерване.
    """

    записи = _таблица((4289.1, ЗАПИС_1), (4239.1, ЗАПИС_2))
    записи.append(_надпис("S1", 557.9, 4300.0, "440"))
    записи.append(_надпис("S2", 651.2, 4295.0, "220"))
    записи.append(_надпис("S3", 517.1, 4280.0, "10"))

    ведомост = read_drawn_schedule(записи)

    assert ведомост is not None
    ред = ведомост.rows[0]
    assert ред.single_length_cm == pytest.approx(450.0)
    assert ред.dimensions_status == "ПОТВЪРДЕНИ"
    assert sorted(ред.shape_dimensions) == [10.0, 440.0]
    assert 220.0 in ред.dimension_candidates   # кандидат беше, но не затваря


def test_dva_zatvarjashti_nabora_sa_otkaz() -> None:
    """Нееднозначното не се избира — нищо не сочи кой е верният набор."""

    записи = _таблица((4289.1, ЗАПИС_1), (4239.1, ЗАПИС_2))
    for handle, x, y, текст in (
        ("D1", 500.0, 4300.0, "450"),
        ("D2", 540.0, 4295.0, "200"),
        ("D3", 580.0, 4285.0, "250"),
    ):
        записи.append(_надпис(handle, x, y, текст))

    ред = read_drawn_schedule(записи).rows[0]

    assert ред.dimensions_status == "НЕЕДНОЗНАЧНО"
    assert ред.shape_dimensions == ()


def test_bez_zatvarjasht_nabor_ne_se_vzema_nishto() -> None:
    """Числа в клетката, които не събират дължината, не стават размери."""

    записи = _таблица((4289.1, ЗАПИС_1), (4239.1, ЗАПИС_2))
    записи.append(_надпис("D1", 520.0, 4300.0, "137"))

    ред = read_drawn_schedule(записи).rows[0]

    assert ред.dimensions_status == "НЯМА ЗАТВАРЯЩ НАБОР"
    assert ред.shape_dimensions == ()
    assert ред.dimension_candidates == (137.0,)


def test_gastalak_ot_kandidati_e_otkaz() -> None:
    """Над тавана за кандидати всеки сбор би бил случаен — отказ."""

    from lion_rebar.dxf.drawn_schedule import МАКС_КАНДИДАТИ

    записи = _таблица((4289.1, ЗАПИС_1), (4239.1, ЗАПИС_2))
    for индекс in range(МАКС_КАНДИДАТИ + 1):
        записи.append(
            _надпис(f"D{индекс}", 500.0 + индекс, 4300.0 - индекс, str(7 + индекс))
        )

    ред = read_drawn_schedule(записи).rows[0]

    assert ред.dimensions_status == "ТВЪРДЕ МНОГО КАНДИДАТИ"
    assert ред.shape_dimensions == ()


# --------------------------------------------------------------------------- #
# отказите


def test_lipsvashto_zaglavie_ne_dava_chasten_prochit() -> None:
    """Без дори едно от шестте заглавия таблицата не се чете изобщо."""

    записи = [
        запис
        for запис in _таблица((4289.1, ЗАПИС_1))
        if запис.get("text") != "Mass"
    ]

    assert read_drawn_schedule(записи) is None


def test_sobstvenata_kontrola_na_vedomostta_e_zadalzhitelna() -> None:
    """Брой × единична ≠ обща е ОТКАЗ, а не приет ред."""

    сгрешен = dict(ЗАПИС_1)
    сгрешен["total"] = (757.6, "99.00")   # 16 × 4,50 = 72,00, не 99,00

    ведомост = read_drawn_schedule(_таблица((4289.1, сгрешен)))

    assert ведомост is not None
    assert ведомост.rows == ()
    assert len(ведомост.refusals) == 1
    отказ = ведомост.refusals[0]
    assert отказ.reason == "КОНТРОЛАТА НА ВЕДОМОСТТА НЕ ИЗЛИЗА"
    assert "7200.00" in отказ.detail


def test_nestandartniyat_diametar_e_otkaz() -> None:
    чужд = dict(ЗАПИС_1)
    чужд["dia"] = (284.1, "13")

    ведомост = read_drawn_schedule(_таблица((4289.1, чужд)))

    assert ведомост is not None
    assert ведомост.rows == ()
    assert ведомост.refusals[0].reason == "НЕСТАНДАРТЕН ДИАМЕТЪР"


def test_dve_chisla_v_edna_kletka_ne_se_izbirat() -> None:
    """Размер от скицата, паднал в колоната на марката, спира реда."""

    записи = _таблица((4289.1, ЗАПИС_1))
    записи.append(_надпис("X1", 160.0, 4289.1, "60"))

    ведомост = read_drawn_schedule(записи)

    assert ведомост is not None
    assert ведомост.rows == ()
    assert ведомост.refusals[0].reason == "НЕПЪЛЕН РЕД"
    assert "MARK" in ведомост.refusals[0].detail


def test_nadpisite_po_skitsata_ne_sa_otkaz() -> None:
    """Самотните числа в колоната на формата не са опит за ред.

    Те не стават и размери, защото не затварят дължината — но това е състояние
    на реда, не отказ за целия ред.
    """

    записи = _таблица((4289.1, ЗАПИС_1))
    записи.append(_надпис("S1", 557.9, 4272.3, "440"))

    ведомост = read_drawn_schedule(записи)

    assert ведомост is not None
    assert len(ведомост.rows) == 1
    assert ведомост.refusals == ()
    assert ведомост.rows[0].dimensions_status == "НЯМА ЗАТВАРЯЩ НАБОР"


def test_chertezh_bez_vedomost_vrashta_nishto() -> None:
    assert read_drawn_schedule([]) is None
    assert read_drawn_schedule([_надпис("T1", 0.0, 0.0, "нещо")]) is None


def test_chetecat_ne_mени_podadenite_zapisi() -> None:
    """Чист прочит: нищо не се пише обратно във входа."""

    записи = _таблица((4289.1, ЗАПИС_1))
    преди = [dict(з) for з in записи]

    read_drawn_schedule(записи)

    assert записи == преди
