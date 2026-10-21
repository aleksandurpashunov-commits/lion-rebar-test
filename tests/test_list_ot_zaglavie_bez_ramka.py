"""Лист от заглавие, когато чертежът няма рамков блок.

РЕШЕНИЕ НА СОБСТВЕНИКА, 06.10.2026. Образецът е изрязан от
``S-_H01-1001_Rev.0_2020-09-23_Bottom_Reinforcement_at_lv.-0.13`` — чертеж от
бюро, с което не е работено. Там заглавието на листа е на два реда:

    „Долна армировка“                  слой A User Elements 1 (0.20mm), h=25,0
    „на кота -0,13/ -0,28/ -0,70“      същият слой, същата височина, 38,1 по-долу

и в целия чертеж **няма нито един ``INSERT``**. Старото правило искаше рамков
блок за втори свидетел и затова даваше нула рамки, нула изнесени позиции и нито
един операторски изход.

Числата в тестовете са истинските от чертежа, за да не се разминава образецът с
измереното: височина 25,0, отстояние 38,1 (1,52 пъти височината), хоризонтално
отместване 65,5 (2,6 пъти).

Два теста тук са доказано ЧЕРВЕНИ срещу кода отпреди решението, а двата
отрицателни контролни пазят това, което НЕ се е променило.
"""
from __future__ import annotations

from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.export.frame_detection import detect_drawing_frames

ВИСОЧИНА = 25.0
СЛОЙ = "A User Elements 1 (0.20mm)"


def _надпис(
    handle: str,
    текст: str,
    при: tuple[float, float],
    *,
    слой: str = СЛОЙ,
    височина: float = ВИСОЧИНА,
) -> DxfEntityRecord:
    return DxfEntityRecord(
        handle=handle,
        entity_type="MTEXT",
        layer=слой,
        geometry={"insert": [при[0], при[1], 0.0], "char_height": височина},
        text=текст,
    )


def _вмъкване(handle: str, слой: str) -> DxfEntityRecord:
    return DxfEntityRecord(
        handle=handle,
        entity_type="INSERT",
        layer=слой,
        geometry={"insert": [0.0, 0.0, 0.0]},
        block_name="нещо",
    )


def _заглавието() -> list[DxfEntityRecord]:
    """Двата реда с истинските координати от чертежа."""

    return [
        _надпис("14D", "Долна армировка", (6175.0, 244.9)),
        _надпис("2C9", "на кота -0,13/ -0,28/ -0,70", (6109.5, 206.8)),
    ]


# --------------------------------------------------------------------------- #
# доказано червени срещу стария код


def test_dvuredno_zaglavie_bez_vmakvaniya_pravi_edin_list() -> None:
    рамки = detect_drawing_frames(_заглавието())

    assert len(рамки) == 1
    рамка = рамки[0]
    assert рамка.category_code == "BOTTOM"
    assert рамка.resolution_reason == "SINGLE_SHEET_TITLE_ONLY"
    # по-слабото доказателство е видимо: категорията НЕ е потвърдена
    assert рамка.confidence == "LOW"


def test_vtoriyat_red_vliza_v_zaglavieto_na_lista() -> None:
    """Собственик: „долу в чертежа ясно пише към коя рамка се отнася“."""

    рамка = detect_drawing_frames(_заглавието())[0]

    assert рамка.title_text == "Долна армировка"
    assert рамка.supporting_text == "на кота -0,13/ -0,28/ -0,70"
    assert рамка.combined_title == "Долна армировка на кота -0,13/ -0,28/ -0,70"


# --------------------------------------------------------------------------- #
# отрицателни контролни: какво НЕ се е променило


def test_vmakvane_na_podvezhdasht_sloy_vse_oshte_otkazva() -> None:
    """Чертеж, който ИМА блокове, просто не на рамков слой, остава без рамка.

    Това е разликата, която първата ми редакция изпусна: условието е „няма нито
    едно вмъкване“, а не „няма вмъкване на рамков слой“.
    """

    записи = [*_заглавието(), _вмъкване("B1", "KEYFRAME")]

    assert detect_drawing_frames(записи) == []


def test_dva_kandidata_za_vtori_red_ne_se_izbirat() -> None:
    """Два реда отдолу е ОТКАЗ за подкрепящия текст, не избор между тях.

    Листът пак се прави — заглавието е едно; празен остава само вторият ред.
    """

    записи = [
        *_заглавието(),
        _надпис("2CA", "второ нещо отдолу", (6109.5, 206.8)),
    ]

    рамка = detect_drawing_frames(записи)[0]

    assert рамка.category_code == "BOTTOM"
    assert рамка.supporting_text == ""


def test_vtori_red_ot_drug_sloy_ne_se_prikacha() -> None:
    """Същото място, друг слой — не е същият надпис."""

    записи = [
        _надпис("14D", "Долна армировка", (6175.0, 244.9)),
        _надпис("2C9", "на кота -0,13", (6109.5, 206.8), слой="Text - General"),
    ]

    рамка = detect_drawing_frames(записи)[0]

    assert рамка.supporting_text == ""


def test_dve_zaglaviya_ostavat_otkaz() -> None:
    """Правилото иска ЕДНО съвпадение. Две е отказ, както преди решението."""

    записи = [
        _надпис("14D", "Долна армировка", (6175.0, 244.9)),
        _надпис("15D", "Горна армировка", (6175.0, 1244.9)),
    ]

    assert detect_drawing_frames(записи) == []
