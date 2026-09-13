"""Тестове за производствената порта при вертикални елементи.

Портата отваря нов път: позиция, чиито брой/Ø/L идват от ИМЕНУВАНИ
атрибути на блок (``BX x BP x BJ``) и чиито производствени размери са
изведени от котировките, се смята за доказана.

Мотивът: при такъв източник няма разчитане на свободен текст, няма
преценка по близост и няма гадаене — всяка стойност си има име, а
произходът се записва в ``count_multiplier``.
"""
from __future__ import annotations

from pathlib import Path

from lion_rebar.export.auto_gate import (
    auto_matcher_disposition_passes,
    is_deterministic_production_source,
)

SOURCE = Path("lion_rebar/export/batch_auto_output.py").read_text(encoding="utf-8")


def test_block_source_and_status_without_contract_are_not_enough():
    """Именуваният блок не заменя валидираното сегментно доказателство."""

    assert not is_deterministic_production_source(
        {
            "annotation_handle": "A1::BJ-FI-L",
            "f006_production_status": "F006_PRODUCTION_DERIVED",
        }
    )


def test_deterministic_source_requires_proven_dimensions():
    """Само блок-източник НЕ стига: размерите също трябва да са доказани."""

    assert not is_deterministic_production_source(
        {"annotation_handle": "A1::BJ-FI-L"}
    )
    assert not is_deterministic_production_source(
        {
            "annotation_handle": "A1::BJ-FI-L",
            "dimension_derived_evidence": {"confidence": "HIGH"},
        }
    )


def test_matcher_gate_still_applies_to_free_text_positions():
    """Позиция от свободен текст минава по стария път без отстъпки."""

    assert not auto_matcher_disposition_passes(
        {
            "annotation_handle": "FREE_TEXT",
            "batch_status": "СИЛЕН",
            "matcher_disposition": "MANUAL",
        }
    )


def test_engine_does_not_rederive_proven_sizes():
    """Пре-извеждането от разпокъсана DXF геометрия отхвърля верен резултат."""

    assert "segments_proven" in SOURCE
    start = SOURCE.index("segments_proven =")
    window = SOURCE[start:start + 600]
    assert "derived_segments" in window


def test_quantity_threshold_is_lifted_only_for_computed_counts():
    """Прагът 'над 1000' пази от грешно ПРОЧЕТЕН брой, не от изчислен."""

    start = SOURCE.index("computed_count = ")
    window = SOURCE[start:start + 500]
    assert "count_multiplier_tags" in window
    assert "quantity_review_required" in window
    assert "not computed_count" in window


def test_data_quality_flags_still_block():
    """Изричните data quality флагове НЕ се заобикалят."""

    start = SOURCE.index("computed_count = ")
    window = SOURCE[start:start + 500]
    assert 'list(row.get("data_quality_flags") or [])' in window


def test_ownership_conflict_is_never_bypassed():
    """Конфликтът за собственост остава блокер при всеки източник."""

    assert 'reasons.append(("OWNERSHIP_CONFLICT"' in SOURCE
    start = SOURCE.index('reasons.append(("OWNERSHIP_CONFLICT"')
    window = SOURCE[max(0, start - 200):start]
    assert "deterministic_source" not in window


def test_missing_geometry_is_never_bypassed():
    assert 'reasons.append(("MISSING_GEOMETRY"' in SOURCE
    start = SOURCE.index('reasons.append(("MISSING_GEOMETRY"')
    window = SOURCE[max(0, start - 250):start]
    assert "deterministic_source" not in window


def test_invalid_length_is_never_bypassed():
    """Липсваща дължина блокира безусловно.

    Проверява се самото УСЛОВИЕ, а не околният текст: дефиницията на
    ``deterministic_source`` стои непосредствено преди него и би дала
    фалшив резултат при търсене по прозорец.
    """

    start = SOURCE.index('reasons.append(("INVALID_LENGTH"')
    condition = SOURCE[:start].rsplit("if ", 1)[1]
    assert condition.strip().startswith("production_length_cm is None")
    assert "deterministic_source" not in condition
