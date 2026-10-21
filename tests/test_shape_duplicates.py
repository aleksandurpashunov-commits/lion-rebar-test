# -*- coding: utf-8 -*-
"""Проверката за дублиране преди раждането на нова MASTER форма.

Досегашната проверка (``detect_shape_semantic_collisions``) сравнява канонични
сигнатури с квантуване на ъглите 0,1°. Измерено: форма, различаваща се с 0,8°,
минава за нова, а огледалната изобщо не се проверява по този път. За matcher-а
обаче двете са неразличими — той сравнява ъгли с ``angle_match_tolerance_deg``.

Тестовете пазят и двете страни: че близките дубликати се хващат И че формите,
които наистина са различни, не се обявяват за дубликат.
"""
from __future__ import annotations

import pytest

from lion_rebar.engine import shape_duplicates as дубл
from lion_rebar.engine.models import SegmentDefinition, ShapeDefinition


def форма(код, завои, кодове=None, *, closed=False):
    кодове = кодове or ["L%d" % (и + 1) for и in range(len(завои) + 1)]
    сегменти = []
    for и in range(len(завои) + 1):
        ъгъл = завои[и] if и < len(завои) else None
        сегменти.append(SegmentDefinition(
            index=и + 1, length_code=кодове[и], segment_type="LINE",
            bend_after_deg=(None if ъгъл is None else abs(ъгъл)),
            bend_direction=(None if ъгъл is None
                            else ("LEFT" if ъгъл >= 0 else "RIGHT")),
        ))
    return ShapeDefinition(code=код, name=код, category="проба",
                           segments=сегменти, active=True, closed=closed)


# --------------------------------------------------------------------------
# Какво ХВАЩА
# --------------------------------------------------------------------------

def test_tochen_dublikat():
    п = дубл.провери(форма("F200", [90.0, 90.0]), [форма("F100", [90.0, 90.0])])
    assert п.вид == дубл.ТОЧЕН
    assert п.е_дубликат is True


@pytest.mark.parametrize("разлика", [0.8, 3.8, 7.9])
def test_blizak_dublikat_pod_dopuska(разлика):
    """Разлика под допуска на matcher-а значи неразличими форми."""

    п = дубл.провери(форма("F200", [90.0 + разлика, 90.0]),
                     [форма("F100", [90.0, 90.0])])
    assert п.вид == дубл.БЛИЗЪК
    assert п.е_дубликат is True
    assert п.находки[0].разлика_deg == pytest.approx(разлика, abs=1e-6)


def test_ogledalnata_forma_e_dublikat():
    п = дубл.провери(форма("F200", [-90.0, 90.0]), [форма("F100", [90.0, -90.0])])
    assert п.вид == дубл.БЛИЗЪК


def test_obarnatiyat_red_e_dublikat():
    п = дубл.провери(форма("F200", [135.0, 90.0]), [форма("F100", [90.0, 135.0])])
    assert п.вид == дубл.БЛИЗЪК


def test_dnesnata_proverka_gi_propuska():
    """Контрола: старата проверка НЕ вижда 0,8° — затова съществува новата."""

    from lion_rebar.engine.models import EnginePackage
    from lion_rebar.engine.validation import detect_shape_semantic_collisions

    лява, дясна = форма("F100", [90.0, 90.0]), форма("F200", [90.8, 90.0])
    пакет = EnginePackage(engine_id="проба", version="0.0.1", title="проба",
                          shapes=[лява, дясна])
    assert detect_shape_semantic_collisions(пакет) == ()
    assert дубл.провери(дясна, [лява]).е_дубликат is True


# --------------------------------------------------------------------------
# Какво НЕ обявява за дубликат
# --------------------------------------------------------------------------

def test_razlichni_agli_nad_dopuska_sa_nova_forma():
    п = дубл.провери(форма("F200", [45.0, 135.0]), [форма("F100", [90.0, 90.0])])
    assert п.вид == дубл.НОВА
    assert п.е_дубликат is False


def test_razlichen_broy_uchastaci_ne_se_sravnyava():
    """Прав прът (1 участък) и трисегментна форма не са едно и също.

    И двете може да нямат записани ъгли — както F001 и F002 в заключения
    MASTER. Без проверка по брой участъци те излизаха като дубликат.
    """

    трисегментна = ShapeDefinition(
        code="F002", name="F002", category="проба", active=True,
        segments=[
            SegmentDefinition(index=и + 1, length_code="L%d" % (и + 1),
                              segment_type="LINE")
            for и in range(3)
        ],
    )
    п = дубл.провери(трисегментна, [форма("F001", [])])
    assert п.вид == дубл.НОВА


def test_razlichen_obrazec_ne_e_dublikat():
    """``ABA`` налага равни рамена, ``ABC`` не — това са различни форми."""

    п = дубл.провери(форма("F200", [90.0, 90.0], ["L1", "L2", "L1"]),
                     [форма("F100", [90.0, 90.0], ["L1", "L2", "L3"])])
    assert п.вид == дубл.РАЗЛИЧИМ_ПО_ДЪЛЖИНИ
    assert п.е_дубликат is False
    assert "образец" in п.находки[0].обяснение


def test_zatvoren_i_otvoren_kontur_ne_se_sravnyavat():
    п = дубл.провери(форма("F200", [90.0, 90.0], closed=True),
                     [форма("F100", [90.0, 90.0], closed=False)])
    assert п.вид == дубл.НОВА


def test_samata_forma_ne_e_dublikat_na_sebe_si():
    ф = форма("F100", [90.0, 90.0])
    assert дубл.провери(ф, [ф]).вид == дубл.НОВА


def test_neaktivna_forma_ne_blokira():
    съществуваща = форма("F100", [90.0, 90.0])
    съществуваща = ShapeDefinition(
        **{**съществуваща.__dict__, "active": False})
    assert дубл.провери(форма("F200", [90.0, 90.0]), [съществуваща]).вид == дубл.НОВА


# --------------------------------------------------------------------------
# Допускът идва от политиката, не от второ скрито число
# --------------------------------------------------------------------------

def test_dopuskat_idva_ot_politikata():
    class Политика:
        angle_match_tolerance_deg = 2.0

    близки = (форма("F200", [95.0, 90.0]), [форма("F100", [90.0, 90.0])])
    assert дубл.провери(*близки).вид == дубл.БЛИЗЪК          # 8° по подразбиране
    assert дубл.провери(*близки, policy=Политика()).вид == дубл.НОВА


def test_izrichniyat_dopusk_bie_politikata():
    class Политика:
        angle_match_tolerance_deg = 2.0

    п = дубл.провери(форма("F200", [95.0, 90.0]), [форма("F100", [90.0, 90.0])],
                     policy=Политика(), допуск_deg=10.0)
    assert п.вид == дубл.БЛИЗЪК
    assert п.допуск_deg == 10.0


# --------------------------------------------------------------------------
# Нормализация на образеца
# --------------------------------------------------------------------------

@pytest.mark.parametrize("вход,очаквано", [
    ("ABC", "ABC"), ("CBA", "ABC"), ("ABA", "ABA"),
    ("BAA", "ABB"), ("ABCDE", "ABCDE"), ("", ""),
])
def test_normalizaciya_na_obrazeca(вход, очаквано):
    assert дубл.нормализирай_образеца(вход) == очаквано


# --------------------------------------------------------------------------
# Калибровка срещу самия заключен MASTER
# --------------------------------------------------------------------------

def test_kalibrovka_sreshtu_zaklyucheniya_master():
    """Върху истинските форми проверката не бива да е широка.

    Ако тя обявеше половината MASTER за дубликати, щеше да е негодна. Пази се
    точно това: остават различими всички освен двете двойки, за които самият
    engine вече знае (F003 ↔ F005 по сигнатура, F009 ↔ F011 огледални).
    """

    from lion_rebar.engine.repository import EngineRepository
    from lion_rebar.utils.app_paths import AppPaths

    pkg = EngineRepository(AppPaths.default().engine_registry).load_current()
    активни = [ф for ф in pkg.shapes if ф.active and ф.segments]
    assert len(активни) >= 10

    сблъскващи = {
        ф.code for ф in активни
        if дубл.провери(ф, активни, policy=None).вид != дубл.НОВА
    }
    # Нищо извън известните двойки не бива да се обявява за дубликат.
    assert сблъскващи <= {"F003", "F005", "F009", "F011"}, сблъскващи
    # И обратното: тези двойки ТРЯБВА да се виждат, иначе проверката е сляпа.
    assert {"F003", "F005"} <= сблъскващи
