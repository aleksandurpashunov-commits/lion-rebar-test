"""Регресионни тестове за множителите в арматурните блокове.

ДЕФЕКТ (открит 2026-08-25): ``_rebar_annotation_from_insert_record``
четеше само ``BJ``, ``FI`` и ``L``, а игнорираше множителите ``BX`` и
``BP``. Реален чертеж на вертикални елементи: блок с BX=39, BP=2, BJ=28
влизаше в описа с **28 бр. вместо 2184** — 78 пъти по-малко, без
предупреждение и без блокер. Тихо грешно количество по критерия на
Project Instructions.

Семантика (потвърдена от Product Owner):
    BX -- брой елементи (K1, K2, K3 ...)
    BP -- множител на броя еднакви елементи
    BJ -- брой елементи от даден вид
    брой = BX * BP * BJ
"""
from __future__ import annotations

from lion_rebar.dxf.importer import _rebar_annotation_from_insert_record
from lion_rebar.dxf.models import DxfEntityRecord


def _insert(**tags):
    return DxfEntityRecord(
        "H1", "INSERT", "",
        {"insert": [0.0, 0.0, 0.0],
         "attributes": [
             {"tag": tag, "text": str(value), "insert": [0.0, 0.0, 0.0],
              "height": 10.0}
             for tag, value in tags.items()
         ]},
    )


def _count(record) -> int:
    return int(record.text.split("N")[0])


# ------------------------------------------------ реални случаи от чертежа

def test_double_multiplier_is_applied():
    """39 x 2 x (28N8x110) = 2184 бр., не 28."""

    record = _rebar_annotation_from_insert_record(
        _insert(BX=39, BP=2, BJ=28, FI=8, L=110))
    assert record is not None
    assert record.text == "2184N8x110"
    assert _count(record) == 39 * 2 * 28


def test_single_multiplier_is_applied():
    """39 x (12N16x400) = 468 бр."""

    record = _rebar_annotation_from_insert_record(
        _insert(BP=39, BJ=12, FI=16, L=400))
    assert _count(record) == 468


def test_three_elements_four_rows_six_hooks():
    """3 x 4 x (6N6x40) = 72 бр. — потвърден от Product Owner пример."""

    record = _rebar_annotation_from_insert_record(
        _insert(BX=3, BP=4, BJ=6, FI=6, L=40))
    assert _count(record) == 72


# ------------------------------------------------ съвместимост

def test_without_multipliers_behaviour_is_unchanged():
    """Блок само с BJ/FI/L работи точно както преди поправката."""

    record = _rebar_annotation_from_insert_record(_insert(BJ=25, FI=8, L=135))
    assert record.text == "25N8x135"


def test_multiplier_of_one_does_not_change_count():
    record = _rebar_annotation_from_insert_record(
        _insert(BX=1, BP=1, BJ=28, FI=8, L=255))
    assert record.text == "28N8x255"


def test_missing_bx_uses_bp_only():
    record = _rebar_annotation_from_insert_record(
        _insert(BP=2, BJ=20, FI=8, L=740))
    assert _count(record) == 40


# ------------------------------------------------ fail-closed

def test_unreadable_multiplier_does_not_invent_a_number():
    """Нечетим множител -> НЕ се гадае; остава оригиналният брой."""

    record = _rebar_annotation_from_insert_record(
        _insert(BX="?", BP=2, BJ=28, FI=8, L=110))
    assert _count(record) == 56


def test_unreadable_count_is_left_untouched():
    record = _rebar_annotation_from_insert_record(
        _insert(BX=39, BP=2, BJ="х", FI=8, L=110))
    assert record is None or "N" in record.text


def test_missing_required_tag_still_returns_none():
    assert _rebar_annotation_from_insert_record(_insert(BX=39, BP=2, FI=8, L=110)) is None


def test_multiplier_provenance_is_recorded():
    """BX и BP влизат в одитната следа, за да е проследим броят."""

    record = _rebar_annotation_from_insert_record(
        _insert(BX=39, BP=2, BJ=28, FI=8, L=110))
    geometry = record.geometry
    assert geometry["count_multiplier"] == 78
    assert geometry["count_multiplier_tags"] == ["BX", "BP"]
    assert geometry["count_before_multiplier"] == "28"
    assert "BX" in geometry["attribute_tags"]
    assert "BP" in geometry["attribute_tags"]


def test_zero_multiplier_is_ignored_not_zeroing_the_count():
    """Множител 0 е безсмислен -> не се прилага, броят остава реален."""

    record = _rebar_annotation_from_insert_record(
        _insert(BX=0, BP=2, BJ=28, FI=8, L=110))
    assert _count(record) == 56
