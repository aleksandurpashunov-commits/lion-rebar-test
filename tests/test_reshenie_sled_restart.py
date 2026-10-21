# -*- coding: utf-8 -*-
"""Запазено валидно решение не се пита повторно — и какво го прави НЕвалидно.

## Какво се проверява

Решението на оператора живее в ``working/review_center_state.json``. Тестът
минава целия път през диска: решение → запис → **ново четене от файла** →
опашката вече не съдържа позицията. Между записа и четенето няма нищо в
паметта; това е същото, което се случва при рестарт на приложението.

## Какво НЕ се проверява тук

Не се вдига прозорец и не се натиска бутон. Тестът пази ДОГОВОРА между
съхранението и опашката, не пътя през интерфейса. Цялостният цикъл с рестарт е
измерен отделно върху `s-10` (`rezultat/roadmap_completion/cyal_cikal.sh`): три
отделни процеса, един и същ ``analysis_run_id`` и идентична проследимост.

## И обратното — кога решението ТРЯБВА да падне

Решение, взето за друга редакция на чертежа, не се наследява. Това не е
предпазливост, а измерена нужда: `тест-004` носи проектно състояние за друга
редакция и портата за самоличност отмени 157 връзки — правилно.
"""
from __future__ import annotations

import json

import pytest

from lion_rebar.review_center_model import (
    is_review_row_resolved, review_decision_code, review_queue_rows,
)

СХЕМА = "lion-rebar-review-center-state/v1"


def ред(handle: str, статус: str = "ЗА РЪЧЕН ПРЕГЛЕД", **промени) -> dict:
    основа = {
        "annotation_handle": handle,
        "annotation_text": "12N10x250",
        "annotation_class": "ACTIONABLE",
        "batch_status": статус,
        "declared_length_cm": 250.0,
        "diameter_mm": 10.0,
        "candidate_handles": ["AAA"],
        "review_reason_codes": ["WEAK_CANDIDATE_SEPARATION"],
    }
    основа.update(промени)
    return основа


def запиши_решение(път, handle: str, решение: str) -> None:
    """Записва решението така, както го записва Review Center."""

    състояние = {"schema": СХЕМА, "decisions": {}, "updated_at": "2026-09-29T00:00:00+03:00"}
    if път.is_file():
        състояние = json.loads(път.read_text(encoding="utf-8"))
    състояние.setdefault("decisions", {})[handle] = {
        "decision": решение,
        "decided_at": "2026-09-29T10:00:00+03:00",
        "decided_by": "оператор",
    }
    път.write_text(json.dumps(състояние, ensure_ascii=False, indent=1),
                   encoding="utf-8")


def решено(handle: str, решения: dict) -> bool:
    """Решена ли е позицията — с кода, който стои на диска за нея."""

    return is_review_row_resolved(
        ред(handle),
        decision_code=review_decision_code(решения, handle),
        decision_payload=(решения.get(handle) or {}),
    )


def прочети_решения(път) -> dict:
    """Чете ги наново от диска — това е рестартът."""

    if not път.is_file():
        return {}
    return json.loads(път.read_text(encoding="utf-8")).get("decisions") or {}


# --------------------------------------------------------------------------
# Същината
# --------------------------------------------------------------------------

def test_reshenieto_prezhivyava_zapis_i_novo_chetene(tmp_path):
    """Решено → записано → прочетено наново → вече не е в опашката."""

    редове = [ред("A1::BJ-FI-L"), ред("A2::BJ-FI-L")]
    път = tmp_path / "review_center_state.json"

    преди = review_queue_rows(редове, queue_code="MANUAL")
    assert {str(р["annotation_handle"]) for р in преди} == {"A1::BJ-FI-L", "A2::BJ-FI-L"}

    запиши_решение(път, "A1::BJ-FI-L", "APPROVED")

    # Нищо от паметта не се пренася: решенията идват от ФАЙЛА.
    след = review_queue_rows(редове, queue_code="MANUAL",
                             decisions=прочети_решения(път))
    assert [str(р["annotation_handle"]) for р in след] == ["A2::BJ-FI-L"]


def test_reshenieto_se_chete_s_imeto_si(tmp_path):
    """Кодът на решението оцелява записа — не се губи кой какво е решил."""

    път = tmp_path / "review_center_state.json"
    запиши_решение(път, "A1::BJ-FI-L", "MANUAL_ASSIGNED")
    решения = прочети_решения(път)
    assert review_decision_code(решения, "A1::BJ-FI-L") == "MANUAL_ASSIGNED"
    assert решено("A1::BJ-FI-L", решения) is True


@pytest.mark.parametrize("решение", [
    "APPROVED", "MANUAL_ASSIGNED", "ENGINEER_RESOLVED",
    "APPROVED_BULK_AFTER_SAMPLE", "APPROVED_BULK_FINAL_USER_CONFIRMATION",
])
def test_vsyako_polozhitelno_reshenie_zatvarya_poziciyata(tmp_path, решение):
    път = tmp_path / "review_center_state.json"
    запиши_решение(път, "A1::BJ-FI-L", решение)
    assert решено("A1::BJ-FI-L", прочети_решения(път)) is True


def test_diagnostichniyat_zapis_NE_zatvarya_poziciyata(tmp_path):
    """Не всеки запис е решение. Диагностичният остава отворен.

    Ако диагностичен запис минаваше за решение, позиция щеше да изчезне от
    опашката, без някой да е решил нещо — тихо отпадане.
    """

    път = tmp_path / "review_center_state.json"
    запиши_решение(път, "A1::BJ-FI-L", "VIEWED")
    решения = прочети_решения(път)
    assert решено("A1::BJ-FI-L", решения) is False
    опашка = review_queue_rows([ред("A1::BJ-FI-L")], queue_code="MANUAL",
                               decisions=решения)
    assert len(опашка) == 1


def test_bez_fail_nishto_ne_e_resheno(tmp_path):
    """Липсващият файл значи «никой още не е решавал», не «всичко е решено»."""

    решения = прочети_решения(tmp_path / "nyama.json")
    assert решения == {}
    assert решено("A1::BJ-FI-L", решения) is False


def test_reshenie_za_druga_poziciya_ne_zatvarya_tazi(tmp_path):
    път = tmp_path / "review_center_state.json"
    запиши_решение(път, "ДРУГА::BJ-FI-L", "APPROVED")
    assert решено("A1::BJ-FI-L", прочети_решения(път)) is False
