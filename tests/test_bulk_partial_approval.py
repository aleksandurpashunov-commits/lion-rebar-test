"""Групово одобрение: проблемната позиция се пропуска, групата продължава.

РЕАЛЕН ДЕФЕКТ (Windows пробег на rc25): груповото потвърждение отменяше
ЦЕЛИЯ пакет заради една позиция с конфликт за собственост —
21 годни позиции оставаха неодобрени заради една чужда геометрия:

    „Общото потвърждение беше отказано: Геометрия 5506 вече е одобрена
     за друг надпис 69DC::BJ-FI-L.“

Конфликтът е свойство на ПОЗИЦИЯТА, не на групата.
"""
from __future__ import annotations

import ast
from pathlib import Path

MAIN = Path("lion_rebar/ui/main_window.py").read_text(encoding="utf-8")


def _bulk_body() -> str:
    start = MAIN.index("skipped: list[tuple[str, str]] = []")
    end = MAIN.index("return output_path, approved_handles", start)
    return MAIN[start:end]


def test_ownership_conflict_skips_instead_of_aborting():
    body = _bulk_body()
    assert "вече е одобрена за друг надпис" in body
    conflict = body.index("вече е одобрена за друг надпис")
    window = body[max(0, conflict - 300):conflict + 200]
    assert "skipped.append" in window
    assert "raise RuntimeError" not in window


def test_duplicate_geometry_in_batch_skips():
    body = _bulk_body()
    assert "същата геометрия е подадена" in body
    duplicate = body.index("същата геометрия е подадена")
    window = body[max(0, duplicate - 300):duplicate + 200]
    assert "raise RuntimeError" not in window


def test_missing_candidate_skips():
    body = _bulk_body()
    assert "липсва кандидат" in body
    missing = body.index("липсва кандидат")
    window = body[max(0, missing - 250):missing + 150]
    assert "raise RuntimeError" not in window


def test_ineligible_row_skips():
    body = _bulk_body()
    assert "недопустим ред за масово приемане" in body
    window_start = body.index("недопустим ред за масово приемане")
    window = body[max(0, window_start - 250):window_start + 150]
    assert "raise RuntimeError" not in window


def test_no_raise_remains_inside_the_row_loop():
    """Нито един отказ вътре в цикъла не бива да отменя целия пакет."""

    body = _bulk_body()
    assert "raise RuntimeError" not in body


def test_skipped_positions_are_reported_to_the_operator():
    """Мълчаливо пропускане е по-лошо от отказ."""

    assert "Част от позициите не бяха одобрени" in MAIN
    assert "Пропуснати:" in MAIN
    assert "остават за индивидуално решение" in MAIN


def test_identity_check_still_aborts_before_the_loop():
    """Чужд project/source остава безусловен отказ — това е друг клас."""

    assert "Масовото решение съдържа редове извън текущия project/source/run" in MAIN
    guard = MAIN.index("Масовото решение съдържа редове извън текущия")
    loop = MAIN.index("skipped: list[tuple[str, str]] = []")
    assert guard < loop, "identity проверката е ПРЕДИ цикъла и остава отказ"


def test_approved_handles_are_still_returned():
    body = _bulk_body()
    assert "approved_handles" in body


def test_legend_reference_bypasses_local_ownership_conflict_only():
    body = _bulk_body()
    assert "shared_legend_reference = is_position_key_legend_reference(row)" in body
    assert "owner and owner != annotation and not shared_legend_reference" in body
    assert "batch_owner and batch_owner != annotation and not shared_legend_reference" in body


def test_bulk_function_parses():
    ast.parse(MAIN)
