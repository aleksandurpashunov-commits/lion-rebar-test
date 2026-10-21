"""Отчетът „Разногласия“ като постоянен изход след пакетния анализ.

Какво пази този файл:

* трите проверки се обаждат точно когато трябва и мълчат иначе;
* ``R5`` мери срещу ДЕФИНИЦИЯТА на формата, не срещу изброени кодове — затова
  важи и за форма, родена от случай (F020), и мълчи за непозната форма;
* ``R3`` НЕ влиза в постоянния изход, докато викащият не го поиска;
* отчетът е само отчет: пакетният анализ записва файла и обобщението, без нито
  един ред на позиция да смени статуса, размерите или одобрението си.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from lion_rebar.dxf.batch_geometry_report import build_batch_geometry_report
from lion_rebar.dxf.disagreement_report import (
    CHECKS,
    OPTIONAL_CHECKS,
    build_disagreement_report,
    disagreement_summary_lines,
    disagreements_for_row,
)
from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.engine.models import SegmentDefinition, ShapeDefinition


def _форма(code: str, брой: int, **промени) -> ShapeDefinition:
    """Форма с ``брой`` участъка — толкова, колкото очаква дефиницията ѝ."""

    данни = {
        "code": code,
        "name": f"Проба {code}",
        "state": "LOCKED",
        "active": True,
        "segments": [
            SegmentDefinition(
                index=индекс,
                length_code=f"L{индекс}",
                segment_type="LINE",
                geometry_method="DIRECT_LENGTH",
                angle_mode="AUTO",
            )
            for индекс in range(1, брой + 1)
        ],
    }
    данни.update(промени)
    return ShapeDefinition(**данни)


def _ред(**промени) -> dict:
    ред = {
        "annotation_class": "ACTIONABLE",
        "annotation_handle": "A1",
        "annotation_text": "4N12x310",
        "batch_status": "СИЛНА",
        "master_shape_code": "F004",
        "production_segments_cm": [50.0, 60.0],
        "nearby_dimensions_cm": [50.0, 60.0],
        "candidate_handles": ["B1"],
        "global_conflict": False,
    }
    ред.update(промени)
    return ред


def _индекс(*форми) -> dict:
    return {форма.code: форма for форма in форми}


# --- R2: котите срещу измереното ------------------------------------------


def test_r2_mulchi_kogato_vseki_uchastuk_ima_svoya_kota() -> None:
    находки = disagreements_for_row(_ред(), shapes=_индекс(_форма("F004", 2)))
    assert находки == []


def test_r2_hvashta_izmerenoto_vmesto_nadpisanoto() -> None:
    """Случаят „50 · 16,97 · 60“ при надписани 50/17/60."""

    ред = _ред(
        master_shape_code="F002",
        production_segments_cm=[50.0, 16.970563, 60.0],
        nearby_dimensions_cm=[50.0, 17.0, 60.0],
    )
    находки = disagreements_for_row(ред, shapes=_индекс(_форма("F002", 3)))
    видове = {находка.kind for находка in находки}
    assert видове == {"ИЗМЕРЕНО_ВМЕСТО_НАДПИСАНО"}
    assert "под 0,5 cm" in находки[0].explanation


def test_r2_hvashta_uchastuk_bez_kota() -> None:
    ред = _ред(
        production_segments_cm=[50.0, 91.0],
        nearby_dimensions_cm=[50.0, 60.0],
    )
    находки = disagreements_for_row(ред, shapes=_индекс(_форма("F004", 2)))
    assert [находка.kind for находка in находки] == ["УЧАСТЪК_БЕЗ_КОТА"]


def test_r2_mulchi_bez_koti_izobshto() -> None:
    """Без надписани коти няма ВТОРИ източник — мълчи, вместо да гадае."""

    ред = _ред(nearby_dimensions_cm=[])
    assert disagreements_for_row(ред, shapes=_индекс(_форма("F004", 2))) == []


# --- R4: спорът за геометрия ----------------------------------------------


def test_r4_hvashta_spora_za_edna_geometriya() -> None:
    ред = _ред(global_conflict=True, candidate_handles=["B26", "B30"])
    находки = disagreements_for_row(ред, shapes=_индекс(_форма("F004", 2)))
    assert [находка.kind for находка in находки] == ["СПОР_ЗА_ГЕОМЕТРИЯ"]
    assert "2 кандидата" in находки[0].explanation


# --- R5: броят участъци срещу дефиницията ---------------------------------


def test_r5_meri_sreshtu_definiciyata_a_ne_sreshtu_izbroeni_kodove() -> None:
    """Формата казва колко участъка очаква; тук не се изброява нищо."""

    ред = _ред(
        master_shape_code="F008",
        production_segments_cm=[10.0, 20.0, 30.0, 40.0],
        nearby_dimensions_cm=[10.0, 20.0, 30.0, 40.0],
    )
    находки = disagreements_for_row(ред, shapes=_индекс(_форма("F008", 5)))
    assert [находка.kind for находка in находки] == ["БРОЙ_УЧАСТЪЦИ"]
    assert "F008 очаква 5, а са 4" in находки[0].explanation


def test_r5_vazhi_i_za_forma_rodena_ot_sluchay() -> None:
    """F020 не е в заключения MASTER, но дефиницията ѝ е в engine-а."""

    кобилица = _форма("F020", 5, recognition={"born_from_case": True})
    ред = _ред(
        master_shape_code="F020",
        production_segments_cm=[50.0, 17.0, 60.0],
        nearby_dimensions_cm=[50.0, 17.0, 60.0],
    )
    находки = disagreements_for_row(ред, shapes=_индекс(кобилица))
    assert [находка.kind for находка in находки] == ["БРОЙ_УЧАСТЪЦИ"]
    assert "F020 очаква 5, а са 3" in находки[0].explanation


def test_odobrenata_pozicia_se_meri_po_odobrenite_uchastuci() -> None:
    """Вече одобрена позиция се мери по решението, не по частичния прочит.

    Измерено на 27.09.2026 в `s-10`: девет позиции `N18x410` (F002) носят
    едновременно одобреното ``[260, 40, 110]`` и частичното
    ``production_segments_cm = [15, 395]``, защото анализът е пропуснат
    („ПРОПУСНАТО — ВЕЧЕ Е ОДОБРЕНО В ПРОЕКТА"). В спецификацията влиза
    одобреното — проверено в `s-10_СПЕЦИФИКАЦИЯ_TRACEABILITY.json`. Затова и
    проверките четат него; иначе се обаждат за девет ВЕРНИ позиции.
    """

    ред = _ред(
        annotation_text="134N18x410",
        batch_status="ОДОБРЕНА ВРЪЗКА",
        master_shape_code="F002",
        production_segment_lengths_cm=[260.0, 40.0, 110.0],
        production_segments_cm=[15.0, 395.0],
        nearby_dimensions_cm=[260.0, 40.0, 110.0],
    )
    находки = disagreements_for_row(ред, shapes=_индекс(_форма("F002", 3)))
    assert [находка.kind for находка in находки] == []


def test_bez_odobreni_uchastuci_se_chete_prochitut_na_analiza() -> None:
    """Неодобрена позиция няма одобрен вектор — проверката пази обхвата си."""

    ред = _ред(
        master_shape_code="F002",
        production_segments_cm=[15.0, 395.0],
        nearby_dimensions_cm=[15.0, 395.0],
    )
    находки = disagreements_for_row(ред, shapes=_индекс(_форма("F002", 3)))
    assert [находка.kind for находка in находки] == ["БРОЙ_УЧАСТЪЦИ"]
    assert "F002 очаква 3, а са 2" in находки[0].explanation


def test_r5_mulchi_za_nepoznata_forma() -> None:
    """Отказът е по-добър от догадката: без дефиниция не се твърди нищо."""

    ред = _ред(master_shape_code="F077", production_segments_cm=[1.0, 2.0, 3.0])
    находки = [
        находка
        for находка in disagreements_for_row(ред, shapes=_индекс(_форма("F004", 2)))
        if находка.kind == "БРОЙ_УЧАСТЪЦИ"
    ]
    assert находки == []


# --- отчетът ---------------------------------------------------------------


def test_otchetut_broi_samo_pozicii_za_deystvie() -> None:
    редове = [
        _ред(),
        _ред(annotation_class="CONTEXT", annotation_handle="A2"),
        _ред(annotation_handle="A3", global_conflict=True, candidate_handles=["B1", "B2"]),
    ]
    отчет = build_disagreement_report(редове, shape_definitions=[_форма("F004", 2)])
    assert отчет["scope_positions"] == 2
    assert отчет["affected_positions"] == 1
    assert отчет["checks"]["R4 спор за геометрия"]["findings"] == 1
    assert отчет["checks"]["R4 спор за геометрия"]["consistency"] == 1.0
    assert отчет["checks"]["R2 коти↔измерено"]["findings"] == 0


def test_saglasuvanostta_e_dyalut_na_edno_obyasnenie() -> None:
    редове = [
        _ред(annotation_handle="A1", production_segments_cm=[50.0, 91.0]),
        _ред(annotation_handle="A2", production_segments_cm=[50.0, 92.0]),
        _ред(
            annotation_handle="A3",
            production_segments_cm=[50.0, 60.2],
            nearby_dimensions_cm=[50.0, 60.0],
        ),
    ]
    отчет = build_disagreement_report(редове, shape_definitions=[_форма("F004", 2)])
    проверка = отчет["checks"]["R2 коти↔измерено"]
    assert проверка["findings"] == 3
    assert проверка["dominant_kind"] == "УЧАСТЪК_БЕЗ_КОТА"
    assert проверка["consistency"] == round(2 / 3, 4)


def test_r3_ne_e_v_postoyanniya_izhod() -> None:
    assert "R3 сбор↔надпис" not in CHECKS
    assert "R3 сбор↔надпис" in OPTIONAL_CHECKS
    ред = _ред(production_segments_cm=[50.0, 60.0], production_length_cm=200.0)
    без = build_disagreement_report([ред], shape_definitions=[_форма("F004", 2)])
    with_ = build_disagreement_report(
        [ред], shape_definitions=[_форма("F004", 2)], include_optional=True
    )
    assert "R3 сбор↔надпис" not in без["checks"]
    assert with_["checks"]["R3 сбор↔надпис"]["findings"] == 1


def test_obobshtenieto_nosi_trite_chisla() -> None:
    отчет = build_disagreement_report(
        [_ред(global_conflict=True, candidate_handles=["B1", "B2"])],
        shape_definitions=[_форма("F004", 2)],
    )
    редове = disagreement_summary_lines(отчет)
    слято = "\n".join(редове)
    assert "позиции за действие: 1" in слято
    assert "R4 спор за геометрия: 1 находки, 100% с едно обяснение" in слято
    assert "Постоянно разногласие = правило" in слято


# --- изходът от пакетния анализ -------------------------------------------


def test_paketniyat_analiz_zapisva_razminavaniyata(tmp_path) -> None:
    entities = [
        DxfEntityRecord(
            handle="A1",
            entity_type="BLOCK_ATTR_TEXT",
            layer="ARM",
            text="2N8x100",
            geometry={"insert": [0.0, 0.0, 0.0]},
        )
    ]

    result = build_batch_geometry_report(
        entities,
        tmp_path,
        project_name="RAZMINAVANIYA_REGRESSION",
        generated_at=datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc),
    )

    payload = json.loads(result.json_path.read_text(encoding="utf-8"))
    отчет = payload["disagreements"]
    assert отчет["schema"] == "lion-rebar-disagreement-report/v1"
    assert set(отчет["checks"]) == set(CHECKS)

    csv_път = result.report_dir / "RAZMINAVANIYA_REGRESSION_geometry_batch_razminavaniya.csv"
    assert csv_път.is_file()
    заглавие = csv_път.read_text(encoding="utf-8-sig").splitlines()[0]
    assert заглавие.split(",")[:2] == ["check", "kind"]

    readme = (result.report_dir / "README_BG.txt").read_text(encoding="utf-8")
    assert "Разногласия" in readme
    assert csv_път.name in readme
