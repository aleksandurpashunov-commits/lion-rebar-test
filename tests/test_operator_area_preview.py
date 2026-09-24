"""Прегледът на рамката брои ПОЗИЦИИ и не пита за заглавието на гредата.

ЗАЩО СЪЩЕСТВУВА. „тест-006", 2026-09-24: собственикът огражда 31 греди и на
**16 от тях** програмата иска обосновка — „автоматиката е свързала тези с друга
греда“. Измерено след това:

* всичките 42 „премествани“ са ЗАГЛАВИЯ на греди („Гр. A.11 25/34“) и бележки
  за стремена („стр. 59N8/s=20“). Нито една позиция;
* сверка на автоматиката срещу същите 31 рамки: **146 от 146** съвпадения;
* целият път, пуснат два пъти — с рамките и без тях — дава един и същ ledger до
  клетка и една и съща греда на всяка от 139-те одобрени позиции.

Тоест мотивът е пазел нищо, а операторът го е писал шестнайсет пъти.

Причината: прегледът обикаляше ВСИЧКИ DXF обекти и питаше ``_assign_beam_label``
за всеки. Тя е направена за позиции — детайли вдясно и под заглавието си — и
върната ѝ точката на самото заглавие, отговаря с гредата на реда НАД него.
"""
from __future__ import annotations

import ast
import functools
import os
import pathlib

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.export.operator_areas import KIND_BEAM, KIND_GROUP
from lion_rebar.ui.main_window import operator_area_preview

#: Рамката ГРЕДИ и две заглавия една под друга, както в „тест-006".
СТЪПКА = 400.0


def _текст(handle: str, text: str, x: float, y: float, layer: str = "ARMTXT",
           тип: str = "BLOCK_ATTR_TEXT", height: float = 10.0) -> DxfEntityRecord:
    return DxfEntityRecord(
        handle=handle,
        entity_type=тип,
        layer=layer,
        text=text,
        geometry={"insert": [x, y, 0.0], "height": height},
    )


def _чертеж() -> list[DxfEntityRecord]:
    """Щемпел, две заглавия на греди и по една позиция под всяко."""

    обекти = [
        _текст("F1", "ЧЕРТЕЖ:", 0.0, 2000.0, layer="ramka", тип="TEXT"),
        _текст("F2", "АРМИРОВКА ГРЕДИ НА КОТА +6.50", 200.0, 2000.0,
               layer="ramka", тип="TEXT"),
        # Заглавията на гредите — едно над друго, на разстояние СТЪПКА.
        _текст("B1", "Гр. A.1 25/50", 100.0, 1000.0, layer="Section", тип="TEXT"),
        _текст("B2", "Гр. A.2 25/50", 100.0, 1000.0 - СТЪПКА, layer="Section", тип="TEXT"),
        # По една истинска позиция под всяко заглавие.
        _текст("P1", "4N12x240", 300.0, 1000.0 - 0.745 * СТЪПКА),
        _текст("P2", "6N14x310", 300.0, 1000.0 - СТЪПКА - 0.745 * СТЪПКА),
        # Бележка за стремената до второто заглавие — НЕ е позиция.
        _текст("N2", "стр. 59N8/s=20", 260.0, 1000.0 - СТЪПКА + 20.0,
               layer="Section", тип="TEXT"),
    ]
    return обекти


def _рамка_на_втората_греда() -> tuple[float, float, float, float]:
    """Правоъгълник около „Гр. A.2“ — заглавието, бележката и позицията ѝ."""

    return (50.0, 1000.0 - СТЪПКА - 0.9 * СТЪПКА, 400.0, 1000.0 - СТЪПКА + 60.0)


def test_broi_samo_pozicii_a_ne_vsichki_obekti() -> None:
    """В рамката има заглавие, бележка и една позиция — броят е 1."""

    обхванати, _премествани = operator_area_preview(
        _чертеж(),
        box=_рамка_на_втората_греда(),
        kind=KIND_BEAM,
        title_handle="B2",
    )
    assert обхванати == 1, "заглавието на гредата и бележката за стремената не са позиции"


def test_ne_pita_za_zaglavieto_na_sobstvenata_si_greda() -> None:
    """Точно случаят от „тест-006“: 16 от 31 рамки искаха мотив за нищо."""

    _обхванати, премествани = operator_area_preview(
        _чертеж(),
        box=_рамка_на_втората_греда(),
        kind=KIND_BEAM,
        title_handle="B2",
    )
    assert премествани == [], премествани


def test_istinsko_premestvane_vse_pak_se_pokazva() -> None:
    """Пази обратното: позиция на ЧУЖДА греда си остава видима."""

    обекти = _чертеж()
    # Рамката на втората греда се разтяга нагоре и хваща позицията на първата.
    x0, y0, x1, _y1 = _рамка_на_втората_греда()
    широка = (x0, y0, x1, 1000.0)

    обхванати, премествани = operator_area_preview(
        обекти, box=широка, kind=KIND_BEAM, title_handle="B2",
    )

    assert обхванати == 2
    assert [т for т, _г in премествани] == ["4N12x240"]
    assert премествани[0][1] == "Гр. A.1 25/50"


def test_grupovata_ramka_ne_pita_za_gredi() -> None:
    """При ``GROUP`` въпросът за греда изобщо не стои."""

    обхванати, премествани = operator_area_preview(
        _чертеж(),
        box=(50.0, 0.0, 400.0, 2000.0),
        kind=KIND_GROUP,
        title_handle="B2",
    )
    assert обхванати == 2
    assert премествани == []


def test_savetite_po_koito_se_ogava_ne_se_broyat() -> None:
    """Съседната рамка си пази позициите — те не са на новата."""

    x0, y0, x1, _y1 = _рамка_на_втората_греда()
    широка = (x0, y0, x1, 1000.0)
    съседна = (x0, 1000.0 - 0.9 * СТЪПКА, x1, 1000.0)

    обхванати, премествани = operator_area_preview(
        _чертеж(), box=широка, bent_around=(съседна,),
        kind=KIND_BEAM, title_handle="B2",
    )

    assert обхванати == 1
    assert премествани == []


def test_bez_text_mtext_v_politikata_ostavat_samo_atributite() -> None:
    """Политиката решава кои типове са надписи — не прегледът."""

    обекти = _чертеж() + [
        _текст("P3", "2N10x150", 320.0, 1000.0 - СТЪПКА - 0.745 * СТЪПКА, тип="TEXT"),
    ]
    кутия = _рамка_на_втората_греда()

    с_текст, _ = operator_area_preview(
        обекти, box=кутия, kind=KIND_BEAM, title_handle="B2",
        policy={"include_text_mtext_annotations": True},
    )
    без_текст, _ = operator_area_preview(
        обекти, box=кутия, kind=KIND_BEAM, title_handle="B2",
        policy={"include_text_mtext_annotations": False},
    )
    assert с_текст == 2
    assert без_текст == 1


# --- кога изобщо се пита -----------------------------------------------------
#
# Проверките са на ИЗВОРА, както в ``test_operator_paths_wired.py``: диалогът
# иска прозорец и човек, а правилото кога се показва е точно това, което
# дразнеше собственика — 31 греди значеха 62 натискания.


@functools.lru_cache(maxsize=None)
def _тялото(име: str) -> str:
    """Тялото на един метод от ``main_window.py``.

    Файлът е 287 KB; разбира се веднъж за целия модул, а не на всеки тест.
    """

    извор = pathlib.Path("lion_rebar/ui/main_window.py").read_text(encoding="utf-8")
    дърво = ast.parse(извор)
    for възел in ast.walk(дърво):
        if isinstance(възел, ast.FunctionDef) and възел.name == име:
            return ast.get_source_segment(извор, възел) or ""
    raise AssertionError(f"няма функция {име}")


def test_motivut_se_iska_samo_pri_premestvane() -> None:
    тяло = _тялото("_operator_box_drawn")
    assert "if премествани:" in тяло
    assert "QInputDialog.getText" in тяло
    # мотивът е в клона за преместване, не другаде
    преди_мотива = тяло.split("QInputDialog.getText")[0]
    assert преди_мотива.rstrip().endswith("note, accepted =")


def test_vaprosat_s_da_ne_ostava_samo_za_prazna_ramka() -> None:
    тяло = _тялото("_operator_box_drawn")
    assert "elif not обхванати:" in тяло
    assert "Рамката не хвана НИТО ЕДНА позиция." in тяло


def test_rutinnata_ramka_ne_pita_nishto() -> None:
    """Обхваща позиции, не мести нищо -> записва се без нито един диалог."""

    тяло = _тялото("_operator_box_drawn")
    клон = тяло.split("elif not обхванати:", 1)[1].split("        else:", 1)[1]
    клон = клон.split("try:", 1)[0]
    assert "QMessageBox" not in клон, клон[:200]
    assert "QInputDialog" not in клон, клон[:200]


def test_zapisanoto_ne_e_dialog() -> None:
    """Кутията „Записано" беше второто натискане на всяка рамка."""

    тяло = _тялото("_operator_box_drawn")
    assert "QMessageBox.information(" not in тяло
    assert "statusBar().showMessage(" in тяло
