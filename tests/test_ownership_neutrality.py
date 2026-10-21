"""Кога спорът за една геометрия НЕ е основание за отказ.

Какво пазят тези тестове:

* спор между надписи с ЕДНАКЪВ производствен резултат не блокира — изходът е
  един и същ, каквото и да реши спорът;
* достатъчно е ЕДИН претендент да се различава по диаметър, дължина, форма,
  участъци или огледалност, за да остане отказът (истински случай:
  `4N14x310` срещу `4N16x310` в `Тест-003`);
* изроден ключ (без диаметър/дължина/форма) не доказва неутралност;
* един претендент не е спор;
* неутралността се отнася за НАДПИСИ, не за геометрични обекти.
"""
from __future__ import annotations

from lion_rebar.export.ownership_neutrality import (
    neutrality_report,
    production_neutral_conflicts,
)


def _ред(handle: str, текст: str, обект: str, *, диаметър=12.0, дължина=310.0,
         форма="F001", сегменти=(310.0,), конфликт=True, огледално=False) -> dict:
    return {
        "annotation_class": "ACTIONABLE",
        "annotation_handle": handle,
        "annotation_text": текст,
        "candidate_handles": [обект],
        "global_conflict": конфликт,
        "diameter_mm": диаметър,
        "declared_length_cm": дължина,
        "master_shape_code": форма,
        "canonical_segment_lengths_cm": list(сегменти),
        "master_shape_mirrored": огледално,
    }


def test_ednakvi_pretendenti_ne_blokirat() -> None:
    """Случаят от `d-2`: един и същ надпис два пъти върху един обект."""

    редове = [_ред("A1", "2N14x600/10", "G1", диаметър=14.0, дължина=600.0),
              _ред("A2", "2N14x600/10", "G1", диаметър=14.0, дължина=600.0)]
    неутрални = production_neutral_conflicts(редове)
    assert неутрални == {"A1", "A2"}
    отчет = neutrality_report(редове)
    assert отчет["neutral_objects"] == 1 and отчет["material_objects"] == 0
    assert отчет["neutral_positions"] == 2


def test_razlichen_diametur_ostava_otkaz() -> None:
    """Истинският случай от `Тест-003`: 4N14x310 срещу 4N16x310."""

    редове = [_ред("A1", "4N14x310", "G1", диаметър=14.0),
              _ред("A2", "4N16x310", "G1", диаметър=16.0)]
    assert production_neutral_conflicts(редове) == frozenset()
    отчет = neutrality_report(редове)
    assert отчет["material_objects"] == 1
    assert "СЪЩЕСТВЕН" in отчет["material"][0]["verdict"]


def test_razlichna_dulzhina_ostava_otkaz() -> None:
    """`60N8x94` срещу `60N8x130` в `s-9`."""

    редове = [_ред("A1", "60N8x94", "G1", диаметър=8.0, дължина=94.0, сегменти=(94.0,)),
              _ред("A2", "60N8x130", "G1", диаметър=8.0, дължина=130.0, сегменти=(130.0,))]
    assert production_neutral_conflicts(редове) == frozenset()


def test_razlichni_uchastuci_ostavat_otkaz() -> None:
    редове = [_ред("A1", "4N12x310", "G1", сегменти=(310.0,)),
              _ред("A2", "4N12x310", "G1", сегменти=(100.0, 110.0, 100.0))]
    assert production_neutral_conflicts(редове) == frozenset()


def test_razlichna_ogledalnost_ostava_otkaz() -> None:
    редове = [_ред("A1", "4N12x310", "G1", огледално=False),
              _ред("A2", "4N12x310", "G1", огледално=True)]
    assert production_neutral_conflicts(редове) == frozenset()


def test_razlichna_forma_ostava_otkaz() -> None:
    редове = [_ред("A1", "4N12x310", "G1", форма="F001"),
              _ред("A2", "4N12x310", "G1", форма="F006")]
    assert production_neutral_conflicts(редове) == frozenset()


def test_izroden_klyuch_ne_dokazva_neutralnost() -> None:
    """Без диаметър, дължина и форма нищо не се твърди."""

    редове = [_ред("A1", "?", "G1", диаметър=None, дължина=None, форма=""),
              _ред("A2", "?", "G1", диаметър=None, дължина=None, форма="")]
    assert production_neutral_conflicts(редове) == frozenset()
    отчет = neutrality_report(редове)
    assert отчет["material_objects"] == 1
    assert "НЕДОСТАТЪЧЕН КЛЮЧ" in отчет["material"][0]["verdict"]


def test_edin_pretendent_ne_e_spor() -> None:
    редове = [_ред("A1", "4N12x310", "G1")]
    assert production_neutral_conflicts(редове) == frozenset()
    assert neutrality_report(редове)["contested_objects"] == 0


def test_bez_global_conflict_nishto_ne_se_otpushva() -> None:
    """Ако програмата не е обявила конфликт, тук няма работа."""

    редове = [_ред("A1", "4N12x310", "G1", конфликт=False),
              _ред("A2", "4N12x310", "G1", конфликт=False)]
    assert production_neutral_conflicts(редове) == frozenset()


def test_broyat_ne_e_chast_ot_klyucha() -> None:
    """Броят идва от надписа, не от обекта — различен брой не прави спора съществен."""

    редове = [_ред("A1", "2N14x600", "G1", диаметър=14.0, дължина=600.0),
              _ред("A2", "8N14x600", "G1", диаметър=14.0, дължина=600.0)]
    assert production_neutral_conflicts(редове) == {"A1", "A2"}


def test_samo_ACTIONABLE_uchastva() -> None:
    редове = [_ред("A1", "4N12x310", "G1"), _ред("A2", "4N12x310", "G1")]
    редове[1]["annotation_class"] = "CONTEXT"
    assert production_neutral_conflicts(редове) == frozenset()
