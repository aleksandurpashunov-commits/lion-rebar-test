# -*- coding: utf-8 -*-
"""Откъде идва ъгълът на всяка огъвка — и какво НЕ го доказва.

Продуктов договор: стандартният режим е „По проекта“; MASTER помага за
съвместими шаблони, но не заменя различна геометрия.

**Премахнатата зависимост.** По-ранна редакция правеше: „рамо извън 8 % от
отпечатания размер → вземи ъглите от MASTER“. Това е необосновано и е махнато:
несъответна дължина не е основание да се вземат чужди ъгли, а разтеглено рамо
може да запази посоката си. `test_mashtabat_veche_ne_e_vhod` пази това.

**Какво остава вход.** Мащабът се ползва само като ПОЛОЖИТЕЛНО доказателство:
две съседни рамена в мащаб правят начертания между тях завой измерване, тоест
„надеждно извлечена посока“ — по-слабо от оразмерен ъгъл и никога препратка към
MASTER. `test_v_mashtab_dava_NADEZHDNO_IZVLECHENA_posoka_ne_dokazan_agal` и
`test_ramo_izvan_mashtab_pravi_agala_nedokazan` държат двете страни.

Двата истински случая, измерени от чертежите:

* `d-1/4B5B` „Есове“ — средното рамо е начертано 24,30 при отпечатани 20
  (+21,5 %), НО огъвките са начертани като допирателни дъги с радиус 1,500 при
  Ø6 (r/d = 2,50) и завои точно −135,00° и +135,00°. Ъглите СА измерени.
* `s-9/14736` двете куки — огъвките са остри ъгли между три прави, при 27,24° и
  152,76°; куката е начертана 23,18 при отпечатани 15. Ъгълът НЕ е измерен.
"""
from __future__ import annotations

import pytest

from lion_rebar.dxf.chain_printed_dimensions import ДЪГА, ОСТЪР
from lion_rebar.export import proven_form as форма


def дъга(завой, радиус=1.5):
    return {"kind": ДЪГА, "turn_deg": завой, "radius_cm": радиус}


def остър(завой):
    return {"kind": ОСТЪР, "turn_deg": завой, "radius_cm": None}


ESOVE = dict(
    участъци_cm=(10.0, 20.0, 10.0),
    начертани_cm=(10.3829, 24.3, 9.924),        # средното рамо е +21,5 %
    огъвки=(дъга(-135.0), дъга(135.0)),
    шаблонни_ъгли_deg=(90.0, 135.0),            # MASTER F007 е C-форма
    диаметър_mm=6.0,
)

КУКИ = dict(
    участъци_cm=(15.0, 405.0, 15.0),
    начертани_cm=(23.1761, 404.7813, 23.1761),  # куките са +54,5 %
    огъвки=(остър(27.2357), остър(152.7643)),
    шаблонни_ъгли_deg=(90.0, 90.0),
    диаметър_mm=8.0,
)


# --------------------------------------------------------------------------
# Премахнатата зависимост от мащаба
# --------------------------------------------------------------------------

def test_razteglenoto_ramo_ne_otmenya_nachertanata_daga():
    """Средното рамо е +21,5 %, но огъвките са начертани → ъглите са измерени.

    Това е ПРЯКАТА проверка за премахнатата зависимост: при старото правило
    същият вход даваше ъглите на MASTER (90°/135°).
    """

    р = форма.реши(**ESOVE)
    assert р.състояние == форма.ИЗВЛЕЧЕНА
    assert р.ъгли_deg == (-135.0, 135.0)
    assert р.доказана is True


def test_v_mashtab_dava_NADEZHDNO_IZVLECHENA_posoka_ne_dokazan_agal():
    """Остър ъгъл между рамена В МАЩАБ е „надеждно извлечена посока“.

    ОЧАКВАНЕТО Е ПРОМЕНЕНО, и ето защо. Първата ми редакция твърдеше, че
    остър ъгъл никога не е доказателство. Измерването го обори: **84 от 132**
    неразрешени позиции в корпуса са начертани в мащаб до 0,22 cm и носят точно
    ±90,00° остри ъгли (`s-9/14723` — участъци [25; 405; 25] при начертани
    [25,0; 404,78; 25,0]). Да се откажат тези ъгли значеше да се обяви за
    недоказано нещо, което чертежът казва еднозначно.

    Какво ЗАЩИТАВА новото очакване: roadmap-ът изрично иска да се различава
    „надеждно извлечена ПОСОКА“ от „изрично ОРАЗМЕРЕН ъгъл“. Това състояние е
    точно първото — по-слабо от оразмеряване и назовано като такова. Мащабът
    тук е ПОЛОЖИТЕЛНО доказателство за местната вярност на чертежа, а НЕ спусък
    да се вземат ъглите на MASTER — това остава махнато (`test_mashtabat_veche_ne_e_vhod`).
    """

    р = форма.реши(
        участъци_cm=(15.0, 405.0, 15.0),
        начертани_cm=(15.0, 405.0, 15.0),        # всичко в мащаб
        огъвки=(остър(90.0), остър(90.0)),
        диаметър_mm=8.0,
    )
    assert р.състояние == форма.ИЗВЛЕЧЕНА
    assert р.ъгли_deg == (90.0, 90.0)
    assert р.в_мащаб == (True, True, True)


def test_ramo_izvan_mashtab_pravi_agala_nedokazan():
    """Куката е +54,5 % извън мащаб → съседните ѝ ъгли НЕ са установени."""

    р = форма.реши(**КУКИ)
    assert р.в_мащаб == (False, True, False)
    assert р.състояние == форма.НЕРАЗРЕШЕН
    assert р.ъгли_deg == ()


def test_mashtabat_veche_ne_e_vhod():
    """Присъдата не се мени, каквито и да са начертаните дължини."""

    основа = dict(ESOVE)
    едно = форма.реши(**основа)
    основа["начертани_cm"] = (10.0, 20.0, 10.0)      # точно в мащаб
    друго = форма.реши(**основа)
    основа["начертани_cm"] = (99.0, 999.0, 99.0)     # безсмислено извън мащаб
    трето = форма.реши(**основа)
    assert едно.ъгли_deg == друго.ъгли_deg == трето.ъгли_deg == (-135.0, 135.0)
    assert едно.състояние == друго.състояние == трето.състояние


# --------------------------------------------------------------------------
# Четирите състояния
# --------------------------------------------------------------------------

def test_orazmereniyat_agal_bie_vsichko():
    р = форма.реши(**{**ESOVE, "оразмерени_ъгли_deg": (90.0, 90.0)})
    assert р.състояние == форма.ОРАЗМЕРЕН
    assert р.ъгли_deg == (90.0, 90.0)


def test_nachertanata_daga_dava_izvlechena_posoka():
    р = форма.реши(**ESOVE)
    assert [о.състояние for о in р.огъвки] == [форма.ИЗВЛЕЧЕНА] * 2
    assert all(о.начертана_като == ДЪГА for о in р.огъвки)
    assert р.огъвки[0].радиус_на_диаметър == pytest.approx(2.5)


def test_ostar_agal_s_odobreno_pravilo_v_obhvat():
    р = форма.реши(**КУКИ, правило="BEAM_ENGINEERING_90_F003",
                   ъгли_по_правило_deg=(90.0, 90.0))
    assert р.състояние == форма.ПО_ПРАВИЛО
    assert р.ъгли_deg == (90.0, 90.0)
    assert all(о.правило == "BEAM_ENGINEERING_90_F003" for о in р.огъвки)
    assert any("PRAVILO_V_OBHVAT" in б for б in р.бележки)


def test_ostar_agal_bez_pravilo_e_nerazreshen():
    """Без правило в обхват НЕ се произвежда предполагаем ъгъл."""

    р = форма.реши(**КУКИ)
    assert р.състояние == форма.НЕРАЗРЕШЕН
    assert р.ъгли_deg == ()
    assert р.доказана is False
    assert any("FORMA_ZA_PREGLED" in б for б in р.бележки)
    assert all(о.липсва for о in р.огъвки)


def test_edna_nerazreshena_ogavka_otmenya_cyalata_forma():
    """Втората огъвка е остра И съседното ѝ рамо е извън мащаб → цялото пада."""

    р = форма.реши(
        участъци_cm=(10.0, 20.0, 10.0),
        начертани_cm=(10.0, 20.0, 40.0),     # третото рамо е 4× извън мащаб
        огъвки=(дъга(-135.0), остър(90.0)),
        диаметър_mm=6.0,
    )
    assert р.състояние == форма.НЕРАЗРЕШЕН
    assert р.ъгли_deg == ()
    assert р.огъвки[0].състояние == форма.ИЗВЛЕЧЕНА
    assert р.огъвки[1].състояние == форма.НЕРАЗРЕШЕН


def test_bezsmislen_radius_ne_e_ogavka():
    """Дъга с радиус 20+ диаметъра при завой 135° е крива, не огъвка."""

    р = форма.реши(
        участъци_cm=(10.0, 20.0, 10.0),
        начертани_cm=(10.0, 20.0, 10.0),
        огъвки=(дъга(-135.0, радиус=100.0), дъга(135.0, радиус=100.0)),
        диаметър_mm=6.0,
    )
    assert р.състояние == форма.НЕРАЗРЕШЕН


def test_bez_diametar_dagata_pak_e_nachertana():
    """Без диаметър не се съди за радиуса, но дъгата си остава начертана."""

    р = форма.реши(**{**ESOVE, "диаметър_mm": None})
    assert р.състояние == форма.ИЗВЛЕЧЕНА


# --------------------------------------------------------------------------
# MASTER не заменя доказаната геометрия
# --------------------------------------------------------------------------

def test_shablonat_ne_zamenya_dokazanoto_no_se_zapisva():
    р = форма.реши(**ESOVE)
    assert р.ъгли_deg == (-135.0, 135.0)          # не 90/135 на шаблона
    assert р.шаблонни_ъгли_deg == (90.0, 135.0)
    assert р.разминаване_deg == pytest.approx(45.0)
    assert any("SHABLONAT_NE_SAVPADA" in б for б in р.бележки)


def test_ugovorenata_forma_e_otdelen_rezhim():
    р = форма.реши(**ESOVE, режим=форма.РЕЖИМ_УГОВОРЕНА)
    assert р.ъгли_deg == (90.0, 135.0)
    assert any("UGOVORENA_FORMA" in б for б in р.бележки)


def test_podrazbiraneto_e_po_proekta(monkeypatch):
    monkeypatch.delenv("LION_REBAR_FORMA_REZHIM", raising=False)
    assert форма.режим_от_настройките(None) == форма.РЕЖИМ_ПРОЕКТ

    class Политика:
        production_form_mode = ""

    assert форма.режим_от_настройките(Политика()) == форма.РЕЖИМ_ПРОЕКТ


def test_rezhimat_se_chete_ot_politikata_i_sredata(monkeypatch):
    class Политика:
        production_form_mode = форма.РЕЖИМ_УГОВОРЕНА

    monkeypatch.delenv("LION_REBAR_FORMA_REZHIM", raising=False)
    assert форма.режим_от_настройките(Политика()) == форма.РЕЖИМ_УГОВОРЕНА
    monkeypatch.setenv("LION_REBAR_FORMA_REZHIM", форма.РЕЖИМ_ПРОЕКТ)
    assert форма.режим_от_настройките(Политика()) == форма.РЕЖИМ_ПРОЕКТ
    monkeypatch.setenv("LION_REBAR_FORMA_REZHIM", "ГЛУПОСТИ")
    assert форма.режим_от_настройките(Политика()) == форма.РЕЖИМ_УГОВОРЕНА


# --------------------------------------------------------------------------
# Пътят през реда
# --------------------------------------------------------------------------

def _ред(**допълнително):
    from lion_rebar.dxf import chain_printed_dimensions as верига

    return {
        верига.ПОЛЕ: {
            "status": верига.ПОТВЪРДЕНО,
            "segments_cm": [15.0, 405.0, 15.0],
            "drawn_lengths_cm": [23.1761, 404.7813, 23.1761],
            "bends": [остър(27.2357), остър(152.7643)],
        },
        "canonical_turn_angles_deg": [90.0, 90.0],
        "diameter_mm": 8.0,
        **допълнително,
    }


def test_za_reda_bez_pravilo_e_nerazreshen():
    р = форма.за_реда(_ред())
    assert р is not None and р.състояние == форма.НЕРАЗРЕШЕН


def test_za_reda_s_pravilo_prilaga_go():
    р = форма.за_реда(_ред(contextual_rule_code="BEAM_ENGINEERING_90_F003"))
    assert р is not None and р.състояние == форма.ПО_ПРАВИЛО
    assert р.ъгли_deg == (90.0, 90.0)


def test_bez_verizhna_prisada_nyama_prisada():
    assert форма.за_реда({"canonical_turn_angles_deg": [90.0]}) is None


def test_nesaglasuvan_broy_ogavki_e_otkaz():
    from lion_rebar.dxf import chain_printed_dimensions as верига

    ред = _ред()
    ред[верига.ПОЛЕ]["bends"] = [остър(90.0)]      # един завой за три рамена
    assert форма.за_реда(ред) is None


# --------------------------------------------------------------------------
# Изчертаване на доказаната форма
# --------------------------------------------------------------------------

def test_risuva_dokazanata_forma_bez_master_shablon():
    svg = форма.начертай([10.0, 20.0, 10.0], [-135.0, 135.0])
    assert svg.lstrip().startswith("<svg")
    for стойност in ("10", "20"):
        assert стойност in svg


def test_razlichnite_agli_davat_razlichna_risunka():
    от_проекта = форма.начертай([10.0, 20.0, 10.0], [-135.0, 135.0])
    от_шаблона = форма.начертай([10.0, 20.0, 10.0], [90.0, 135.0])
    assert от_проекта != от_шаблона


def test_risuva_i_forma_koyato_MASTER_nyama():
    svg = форма.начертай([20.0, 51.0, 55.0, 20.0], [90.0, 82.0, 98.0])
    assert svg.lstrip().startswith("<svg")


@pytest.mark.parametrize("участъци,ъгли", [
    ([10.0], []), ([10.0, 20.0], []), ([10.0, 20.0], [90.0, 90.0]),
])
def test_risuvachat_otkazva_nesaglasuvan_vhod(участъци, ъгли):
    with pytest.raises(ValueError):
        форма.начертай(участъци, ъгли)


# --------------------------------------------------------------------------
# Кога рисунката напуска шаблона на MASTER
# --------------------------------------------------------------------------

def _форма_definition(код, завои):
    from lion_rebar.engine.models import SegmentDefinition, ShapeDefinition

    сегменти = []
    for и in range(len(завои) + 1):
        ъгъл = завои[и] if и < len(завои) else None
        сегменти.append(SegmentDefinition(
            index=и + 1, length_code="L%d" % (и + 1), segment_type="LINE",
            bend_after_deg=(None if ъгъл is None else abs(ъгъл)),
            bend_direction=(None if ъгъл is None
                            else ("LEFT" if ъгъл >= 0 else "RIGHT")),
        ))
    return ShapeDefinition(code=код, name=код, category="", segments=сегменти)


def test_savpadashti_agli_ostavat_pri_master_risuvacha():
    from lion_rebar.export.shape_image import _turns_contradict_shape

    ф = _форма_definition("F007", [-90.0, -135.0])
    assert _turns_contradict_shape(ф, [90.0, 135.0]) is False


def test_dokazanite_agli_izprashtat_kam_drugiya_risuvach():
    from lion_rebar.export.shape_image import _turns_contradict_shape

    ф = _форма_definition("F007", [-90.0, -135.0])
    assert _turns_contradict_shape(ф, [-135.0, 135.0]) is True
