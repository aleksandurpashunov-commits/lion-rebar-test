# -*- coding: utf-8 -*-
"""Отпечатаният размер не се изхвърля, за да излезе обявената дължина.

Случаите са истински, от корпуса на 29–30.09.2026 (`B2_INSTALIRANITE.json`):

| позиция | начертано | чертежът отпечатва | влизаше в производството |
|---|---|---|---|
| `Тест-002/702A0E` | `45, 21, 45` = 111 при обявени 110 | 21 и 45 | `[44, 21, 45]` |
| `s-9/15D46` | `600,1 + 25 + 25` = 650,1 при обявени 640 | 25 | `[20, 600, 20]` |
| `Тест-003/69FA15` | `16 + 150` при обявени 170 | 16 | `[150, 20]` |

Измерено: **17 позиции** носеха такъв вектор — 9 в експортния контекст и 8 спрени
само по `UNKNOWN_FRAME`, тоест решение за площта би ги освободило с неразгледан
размерен конфликт. Дефектът беше концентриран в досмятането Δ
(`A_E_TOTAL_MINUS_PRINTED` 11, `A_E_DELTA_SPLIT_TWO_EQUAL_BENDS` 1, без записан
източник 5); всички останали доказатели даваха нула нарушения.

ДЕЛТАТА, която тези тестове пазят: до 30.09 ветото важеше само за ПРЕДЛОЖЕНИЕТО
на закръгляването. Сега проверката е върху КРАЙНИЯ вектор в
`build_batch_export_context` — единственото място, през което минава всичко, което
стига до Excel и DXF, и което се сглобява наново при всяко извикване, затова кеш,
старо одобрение или заредена сесия не могат да го заобиколят.
"""
from __future__ import annotations

import json
from pathlib import Path

from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.engine.models import EnginePackage, SegmentDefinition, ShapeDefinition
from lion_rebar.export.batch_auto_output import build_batch_export_context
from lion_rebar.export.printed_dimension_guard import (
    БЛИЗКА,
    ДОКАЗАНА,
    ПРОТИВОРЕЧИ,
    изхвърлени_отпечатани,
    отпечатани_за_реда,
    присъда_за_реда,
)


# ==========================================================================
# Самото правило
# ==========================================================================


def test_izhvurlen_otpechatan_razmer_se_hvashta():
    """Случаят `Тест-002`: начертаното 45 е отпечатано, а в производството влиза 44."""

    ред = {
        "nearby_dimensions_cm": [21.0, 45.0, 650.0],
        "grid_rounded_segments": {"semantic_segments_cm": [45.0, 21.0, 45.0]},
        "production_segments_cm": [44.0, 21.0, 45.0],
    }
    присъда = присъда_за_реда(ред)
    assert присъда["status"] == ПРОТИВОРЕЧИ
    изхвърлени = присъда["discarded_printed"]
    assert [(и["drawn_cm"], и["printed_cm"], и["installed_cm"]) for и in изхвърлени] == [
        (45.0, 45.0, 44.0)
    ]
    assert присъда["strongest_printed_source"] == БЛИЗКА
    # Правилото НЕ подменя стойност — само казва какво се изхвърля.
    assert "production_segments_cm" in присъда
    assert присъда["production_segments_cm"] == [44.0, 21.0, 45.0]


def test_dvete_kuki_na_s9_izlizat_po_5_cm():
    """Случаят `s-9/15D46`: разминаване от 10 cm, погълнато от двете куки."""

    ред = {
        "nearby_dimensions_cm": [25.0, 600.0],
        "grid_rounded_segments": {"semantic_segments_cm": [600.1, 25.0, 25.0]},
        "production_segments_cm": [20.0, 600.0, 20.0],
    }
    присъда = присъда_за_реда(ред)
    assert присъда["status"] == ПРОТИВОРЕЧИ
    разлики = [и["difference_cm"] for и in присъда["discarded_printed"]]
    assert разлики == [5.0, 5.0]


def test_dokazanata_kota_na_ramoto_e_po_silna_ot_blizkata():
    """Двата източника се различават и присъдата казва кой е.

    Доказаната верига присвоява кота на РАМО с взаимно еднозначно съответствие;
    близката кота казва само „чертежът отпечатва тази стойност някъде наоколо".
    """

    доказана = {
        "chain_printed_dimensions": {"status": "CONFIRMED", "segments_cm": [765.0, 25.0]},
        "grid_rounded_segments": {"semantic_segments_cm": [765.0, 25.0]},
        "production_segments_cm": [765.0, 30.0],
    }
    присъда = присъда_за_реда(доказана)
    assert присъда["status"] == ПРОТИВОРЕЧИ
    assert присъда["strongest_printed_source"] == ДОКАЗАНА

    # Отказана верига не носи доказани рамена — остават само котите наоколо.
    отказана = {
        "chain_printed_dimensions": {"status": "REFUSED", "segments_cm": [1.0, 2.0]},
        "nearby_dimensions_cm": [21.0, 45.0],
    }
    assert отпечатани_за_реда(отказана) == ([], [21.0, 45.0])


def test_prilepvane_pod_dopuska_ne_e_izhvurlyane():
    """Преместване до 0,5 cm е прилепване в рамките на шума, не смяна на размер."""

    ред = {
        "nearby_dimensions_cm": [429.5, 20.0],
        "grid_rounded_segments": {"semantic_segments_cm": [429.5, 20.0]},
        "production_segments_cm": [430.0, 20.0],
    }
    assert присъда_за_реда(ред)["status"] == "OK"


def test_i_novata_stoynost_otpechatana_ne_e_izhvurlyane():
    """Когато чертежът отпечатва И двете стойности, това е избор, не изхвърляне.

    Такъв случай не се решава от това правило — то пази отпечатаното от
    изхвърляне, а не избира между два обявени размера.
    """

    ред = {
        "nearby_dimensions_cm": [23.0, 25.0, 50.0, 60.0],
        "grid_rounded_segments": {"semantic_segments_cm": [50.0, 23.0, 60.0]},
        "production_segments_cm": [50.0, 25.0, 60.0],
    }
    assert присъда_за_реда(ред)["status"] == "OK"


def test_razlichniyat_red_na_uchastacite_ne_e_narushenie():
    """Доказателите подреждат по свой канон — сравнението е по сдвояване, не по индекс.

    Мерено по индекс, `s-9/1471C` излизаше нарушение само защото инсталираният е
    `[25, 405]` при начертани `[405, 25]`: същите числа, друг ред. Заради това
    мярката даваше 21 нарушения вместо 17.
    """

    ред = {
        "nearby_dimensions_cm": [25.0, 405.0],
        "grid_rounded_segments": {"semantic_segments_cm": [405.0, 25.0]},
        "production_segments_cm": [25.0, 405.0],
    }
    assert присъда_за_реда(ред)["status"] == "OK"


def test_alchnoto_sdvoyavane_e_zamenenso_s_tochno():
    """При близки стойности алчното сдвояване лъже; точното — не.

    Начертани `25,27` и `25,0`, инсталирани `25,0` и `30,0`. Алчно първата
    изяжда единствената 25,0 и втората излиза „изпусната" — така мярката даваше
    33 нарушения. Точното присвояване сдвоява 25,27 ↔ 25,0 и вижда, че
    изхвърленото е 25,0 → 30,0.
    """

    изхвърлени, точно = изхвърлени_отпечатани(
        [25.27, 25.0], [25.0, 30.0], [], [25.0])
    assert точно is True
    assert len(изхвърлени) == 1
    # Най-малката ОБЩА разлика сдвоява 25,0 <-> 25,0 (0,0) и 25,27 <-> 30,0
    # (4,73) = 4,73; обратното дава 5,27. Затова изхвърленото е 25,27, не 25,0.
    assert изхвърлени[0]["drawn_cm"] == 25.27
    assert изхвърлени[0]["printed_cm"] == 25.0
    assert изхвърлени[0]["installed_cm"] == 30.0


def test_bez_otpechatani_stoynosti_pravilото_ne_se_proiznasya():
    """Без нито една отпечатана стойност няма какво да се пази."""

    ред = {
        "grid_rounded_segments": {"semantic_segments_cm": [45.0, 21.0, 45.0]},
        "production_segments_cm": [44.0, 21.0, 45.0],
    }
    assert присъда_за_реда(ред) is None


def test_razlichen_broy_uchastaci_e_vazdarzhane():
    """Различен брой участъци значи друга геометрия — правилото се въздържа."""

    ред = {
        "nearby_dimensions_cm": [45.0],
        "grid_rounded_segments": {"semantic_segments_cm": [45.0, 21.0, 45.0]},
        "production_segments_cm": [44.0, 66.0],
    }
    assert присъда_за_реда(ред) is None


# ==========================================================================
# КРАЙНИЯТ РЕЗУЛТАТ: през авторитетния зареждач на експорта
# ==========================================================================


def _engine() -> EnginePackage:
    return EnginePackage(
        engine_id="test-engine",
        title="TEST",
        version="0.3.12",
        production_ready=True,
        shapes=[
            ShapeDefinition(
                code="F001", name="ПРАВ ПРЪТ", state="LOCKED", locked=True,
                segments=[SegmentDefinition(index=1, length_code="L1")],
            ),
            ShapeDefinition(
                code="F004", name="ПРЪТ С ЕДИН ВИНКЕЛ", state="LOCKED", locked=True,
                segments=[SegmentDefinition(index=1, length_code="L1"),
                          SegmentDefinition(index=2, length_code="L2")],
            ),
        ],
    )


def _frame_entities() -> list[DxfEntityRecord]:
    return [
        DxfEntityRecord("FRAME", "TEXT", "$Ramka", {"insert": [0, 0, 0]}, "ЧЕРТЕЖ:"),
        DxfEntityRecord("TITLE", "TEXT", "0", {"insert": [100, 0, 0]}, "АРМИРОВКА ГРЕДИ"),
        DxfEntityRecord("BEAM", "TEXT", "0", {"insert": [100, 750, 0]}, "Гр. B.1 25/50"),
    ]


def _ред_с_кука(handle: str, текст: str, *, дълго: float, кука_начертана: float,
                кука_в_производството: float, обявена: float) -> dict:
    """Прът с една кука: дългото рамо е в мащаб, куката е спорната."""

    return {
        "annotation_handle": handle,
        "annotation_text": текст,
        "annotation_class": "ACTIONABLE",
        "annotation_count": 2,
        "diameter_mm": 8.0,
        "declared_length_cm": обявена,
        "batch_status": "СИЛЕН ПО ГЕОМЕТРИЯ",
        "matcher_disposition": "CONTROL",
        "global_conflict": False,
        "data_quality_flags": [],
        "quantity_review_required": False,
        "master_shape_code": "F004",
        "candidate_handles": ["G1", "G2"],
        "candidate_notes": [],
        # Чертежът отпечатва НАЧЕРТАНАТА кука.
        "nearby_dimensions_cm": [кука_начертана, дълго],
        "grid_rounded_segments": {
            "semantic_segments_cm": [дълго, кука_начертана],
        },
        "canonical_segment_lengths_cm": [дълго, кука_в_производството],
        "production_segments_cm": [дълго, кука_в_производството],
    }


def _отчет(tmp_path: Path, редове: list[dict]) -> Path:
    път = tmp_path / "batch.json"
    път.write_text(json.dumps({"cancelled": False, "rows": редове}, ensure_ascii=False),
                   encoding="utf-8")
    return път


def _обекти(дълго: float, кука: float) -> list[DxfEntityRecord]:
    return _frame_entities() + [
        DxfEntityRecord("T1", "BLOCK_ATTR_TEXT", "ARM_TXT", {"insert": [100, 500, 0]}, "2N8x000"),
        DxfEntityRecord("G1", "LINE", "Arm - N8",
                        {"start": [100, 500, 0], "end": [100 + дълго, 500, 0]}),
        DxfEntityRecord("G2", "LINE", "Arm - N8",
                        {"start": [100 + дълго, 500, 0], "end": [100 + дълго, 500 + кука, 0]}),
    ]


def test_krayniyat_vektor_ne_minava_kogato_izhvurlya_otpechatan_razmer(tmp_path: Path):
    """ДЕЛТАТА: досмятаната кука вече не стига до експортния контекст.

    Начертано `250 + 25` = 275 при обявени 270; чертежът отпечатва 25. Старият
    път приемаше `[250, 20]`, защото сборът излиза 270. Сега редът е блокиран с
    `PRODUCTION_CONTRADICTS_PRINTED` и двата варианта остават видими.
    """

    спорен = _ред_с_кука("T1", "2N8x270", дълго=250.0, кука_начертана=25.0,
                         кука_в_производството=20.0, обявена=270.0)
    # Контекстът нарочно вдига грешка, ако НИТО ЕДНА позиция не мине („празен
    # Excel не бива да изглежда като готова заявка"). Затова влиза и чист ред —
    # така се мери блокирането, а не онази грешка.
    чист = _ред_с_кука("T2", "2N8x275", дълго=250.0, кука_начертана=25.0,
                       кука_в_производството=25.0, обявена=275.0)
    чист["candidate_handles"] = ["G3", "G4"]
    обекти = _обекти(250.0, 25.0) + [
        DxfEntityRecord("T2", "BLOCK_ATTR_TEXT", "ARM_TXT", {"insert": [800, 500, 0]}, "2N8x275"),
        DxfEntityRecord("G3", "LINE", "Arm - N8", {"start": [800, 500, 0], "end": [1050, 500, 0]}),
        DxfEntityRecord("G4", "LINE", "Arm - N8", {"start": [1050, 500, 0], "end": [1050, 525, 0]}),
    ]
    контекст = build_batch_export_context(
        _отчет(tmp_path, [спорен, чист]), entities=обекти, engine=_engine())

    assert [п.annotation_handle for п in контекст.positions] == ["T2"]
    блокери = {б.annotation_handle: б for б in контекст.blockers}
    assert ПРОТИВОРЕЧИ in блокери["T1"].reason_codes
    # Причината носи двата варианта: начертаното/отпечатаното и влизащото.
    текст = блокери["T1"].reason_text
    assert "25" in текст and "20" in текст


def test_ediniyat_i_sasht_vektor_minava_kogato_e_v_mashtab(tmp_path: Path):
    """Контрола: същият прът, но производството ползва отпечатаната кука.

    Ако този тест падне заедно с горния, значи правилото блокира всичко, а не
    само изхвърлянето — затова двата стоят заедно.
    """

    ред = _ред_с_кука("T1", "2N8x275", дълго=250.0, кука_начертана=25.0,
                      кука_в_производството=25.0, обявена=275.0)
    контекст = build_batch_export_context(
        _отчет(tmp_path, [ред]), entities=_обекти(250.0, 25.0), engine=_engine())

    assert [п.annotation_handle for п in контекст.positions] == ["T1"]
    assert контекст.blockers == ()
