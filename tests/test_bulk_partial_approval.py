"""Групово одобрение: проблемната позиция се пропуска, групата продължава.

РЕАЛЕН ДЕФЕКТ (Windows пробег на rc25): груповото потвърждение отменяше
ЦЕЛИЯ пакет заради една позиция с конфликт за собственост —
21 годни позиции оставаха неодобрени заради една чужда геометрия:

    „Общото потвърждение беше отказано: Геометрия 5506 вече е одобрена
     за друг надпис 69DC::BJ-FI-L.“

Конфликтът е свойство на ПОЗИЦИЯТА, не на групата.

ЗАЩО ТОЗИ ФАЙЛ Е ПРЕНАПИСАН (01.10.2026). Дотук тестът четеше ИЗХОДНИЯ ТЕКСТ на
``ui/main_window.py`` между две котви и търсеше низове в него. Пазеше верния
принцип по най-слабия възможен начин: минаваше и при код, който съдържа думите,
без да ги изпълнява, и падаше при всяко преместване на кода.

Проверката по членове вече живее в ``review/bulk_member_verdicts.py`` — една
функция, ползвана и от потвърждението ПРЕДИ прилагането (§ 6.3), и от писача.
Затова тук се проверява ПОВЕДЕНИЕТО ѝ: всеки ред получава присъда, отказът на
един не отменя останалите, а причината е конкретна и поименна. Запазени са и
твърденията за самия прозорец, които не са за тази функция: проверката за
самоличност остава БЕЗУСЛОВЕН отказ преди цикъла, а пропуснатите се съобщават.
"""
from __future__ import annotations

import ast
from pathlib import Path

from lion_rebar.review.bulk_member_verdicts import (
    БЕЗ_КАНДИДАТ,
    НЕДОПУСТИМ,
    bulk_member_verdicts,
    refusal_lines,
    summarise,
)

MAIN = Path("lion_rebar/ui/main_window.py").read_text(encoding="utf-8")


def _ред(handle: str, *, геометрия=("G1",), **промени) -> dict:
    """Ред от пакетния отчет, който минава строгата порта за масово приемане."""

    ред = {
        "annotation_handle": handle,
        "annotation_text": "4N12x240 (%s)" % handle,
        "annotation_class": "ACTIONABLE",
        "batch_status": "СИЛЕН",
        "matcher_disposition": "CONTROL",
        "annotation_count": 4,
        "diameter_mm": 12.0,
        "declared_length_cm": 240.0,
        "geometry_length_raw": 240.0,
        "numeric_difference": 0.0,
        "distance_raw": 10.0,
        "candidate_handles": list(геометрия),
        "candidate_layer": "ARM12",
        "diameter_layer_match": True,
        "master_shape_code": "F001",
        "segment_count": 1,
    }
    ред.update(промени)
    return ред


def _връзка(handle: str, геометрия=("G1",)) -> dict:
    return {"annotation_handle": handle, "geometry_handles": list(геометрия)}


# --- всеки ред получава присъда, отказът на един не отменя другите ----------


def test_vseki_red_poluchava_prisada():
    присъди = bulk_member_verdicts([_ред("A"), _ред("B", геометрия=("G2",))])

    assert len(присъди) == 2
    assert all(член.verdict for член in присъди)
    assert all(член.reason for член in присъди)
    assert all(член.accepted for член in присъди)


def test_ownership_conflict_skips_instead_of_aborting():
    """Чужда геометрия спира СВОЯ ред, не пакета."""

    присъди = bulk_member_verdicts(
        [_ред("A"), _ред("B", геометрия=("G9",))],
        existing_links=[_връзка("ЧУЖД", ("G9",))],
    )

    по_handle = {член.annotation_handle: член for член in присъди}
    assert по_handle["A"].accepted
    assert not по_handle["B"].accepted
    assert "вече е одобрена за друг надпис" in по_handle["B"].reason
    assert "ЧУЖД" in по_handle["B"].reason


def test_duplicate_geometry_in_batch_skips():
    """Две позиции за една геометрия: първата я взема, втората получава отказ."""

    присъди = bulk_member_verdicts([_ред("A"), _ред("B")])

    по_handle = {член.annotation_handle: член for член in присъди}
    assert по_handle["A"].accepted
    assert not по_handle["B"].accepted
    assert "същата геометрия е подадена и от A" in по_handle["B"].reason


def test_missing_candidate_skips():
    присъди = bulk_member_verdicts([_ред("A"), _ред("B", candidate_handles=[])])

    по_handle = {член.annotation_handle: член for член in присъди}
    assert по_handle["A"].accepted
    assert по_handle["B"].reason == БЕЗ_КАНДИДАТ


def test_ineligible_row_skips():
    присъди = bulk_member_verdicts(
        [_ред("A"), _ред("B", геометрия=("G2",), data_quality_flags=["НЕЩО"])]
    )

    по_handle = {член.annotation_handle: член for член in присъди}
    assert по_handle["A"].accepted
    assert по_handle["B"].reason == НЕДОПУСТИМ


def test_edin_otkaz_ne_otmenya_ostanalite():
    """Същината на дефекта от rc25, измерена: 1 отказ, 20 приети."""

    редове = [_ред("X%d" % индекс, геометрия=("G%d" % индекс,)) for индекс in range(20)]
    редове.append(_ред("СПОРЕН", геометрия=("G0",)))

    сводка = summarise(bulk_member_verdicts(редове))

    assert сводка["total"] == 21
    assert сводка["accepted"] == 20
    assert сводка["refused"] == 1


def test_legend_reference_bypasses_local_ownership_conflict_only():
    """Позиционният ключ в легендата дели геометрия нарочно и не се спира."""

    легенда = _ред("ЛЕГЕНДА", position_key_legend_reference=True)
    присъди = bulk_member_verdicts(
        [_ред("A"), легенда],
        existing_links=[_връзка("ЧУЖД", ("G1",))],
    )

    по_handle = {член.annotation_handle: член for член in присъди}
    # Обикновеният ред получава отказ заради чуждата собственост...
    assert not по_handle["A"].accepted
    # ... а легендният не, защото делението е по замисъл.
    assert по_handle["ЛЕГЕНДА"].accepted
    assert по_handle["ЛЕГЕНДА"].shared_legend_reference is True


def test_prichinite_sa_poimenni_i_ne_se_otryazvat_naujkim():
    редове = [_ред("A")] + [
        _ред("B%d" % индекс, candidate_handles=[]) for индекс in range(12)
    ]

    редове_за_показване = refusal_lines(bulk_member_verdicts(редове), limit=10)

    assert len(редове_за_показване) == 11
    assert редове_за_показване[-1].endswith("и още 2")
    assert all(БЕЗ_КАНДИДАТ in ред for ред in редове_за_показване[:10])


def test_prisadite_sa_v_reda_na_podavaneto():
    """Редът решава кой взема спорната геометрия — и той е видим."""

    присъди = bulk_member_verdicts([_ред("ПЪРВИ"), _ред("ВТОРИ")])

    assert [член.annotation_handle for член in присъди] == ["ПЪРВИ", "ВТОРИ"]


# --- твърдения за прозореца, които не са за тази функция --------------------


def test_skipped_positions_are_reported_to_the_operator():
    """Мълчаливо пропускане е по-лошо от отказ."""

    assert "Част от позициите не бяха одобрени" in MAIN
    assert "Пропуснати:" in MAIN
    assert "остават за индивидуално решение" in MAIN


def test_operatorat_vizhda_broya_predi_prilaganeto():
    """§ 6.3: потвърждението показва КОЛКО ще минат, не колко са подадени."""

    assert "_bulk_member_preview" in MAIN
    assert "Ще бъдат записани %d от %d подадени." in MAIN
    # И четирите масови действия минават през него.
    assert MAIN.count("self._bulk_member_preview(") == 4


def test_identity_check_still_aborts_before_the_loop():
    """Чужд project/source остава безусловен отказ — това е друг клас."""

    assert "Масовото решение съдържа редове извън текущия project/source/run" in MAIN
    guard = MAIN.index("Масовото решение съдържа редове извън текущия")
    loop = MAIN.index("verdicts = bulk_member_verdicts(")
    assert guard < loop, "identity проверката е ПРЕДИ цикъла и остава отказ"


def test_approved_handles_are_still_returned():
    assert "return output_path, approved_handles" in MAIN


def test_bulk_function_parses():
    ast.parse(MAIN)
