"""Отнемането на одобрена геометрия изисква изрично потвърждение.

РЕАЛЕН ДЕФЕКТ (Windows пробег): операторът решава 4 позиции, пуска
Финален контрол и вижда 13 нови за преглед. Всяко решение е отнело
геометрия от вече одобрена позиция; тя е загубила одобрението си БЕЗ
нито дума и се е върнала за преглед.

Позициите, които губят геометрията си, са били ВЕРНИ.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from lion_rebar.engineer_decision import (
    GeometryTakeoverRequired,
    save_engineer_decision,
)

MAIN = Path("lion_rebar/ui/main_window.py").read_text(encoding="utf-8")


def _project_with_auto_link(directory: str, geometry: list[str]):
    root = Path(directory)
    working = root / "working"
    working.mkdir(parents=True)
    (working / "approved_geometry_links.json").write_text(json.dumps({
        "schema": "lion-rebar-approved-geometry-links/v1",
        "links": [{
            "annotation_handle": "OWNER",
            "annotation_text": "25N8x135",
            "geometry_handles": geometry,
            # Маркерите са тези, които approval_origin() разпознава.
            "approval_status": "ОДОБРЕНО АВТОМАТИЧНО (ПАКЕТНА ПОРТА)",
            "auto_gate": True,
        }],
        "history": [],
    }, ensure_ascii=False), encoding="utf-8")
    return root


def _payload(handle: str, geometry: list[str]):
    return {
        "annotation": {"handle": handle, "text": "25N8x135"},
        "resolved": {"shape_code": "F006", "count": 25, "diameter_mm": 8,
                     "production_length_cm": 135,
                     "segment_lengths_cm": [10, 20, 35, 20, 35, 10]},
        "geometry": {"handles": geometry},
        "reason_code": "CORRECTION",
        "note": "тест",
    }


def test_taking_approved_geometry_raises_instead_of_stealing():
    with tempfile.TemporaryDirectory() as directory:
        root = _project_with_auto_link(directory, ["G1"])
        try:
            save_engineer_decision(root, _payload("NEW", ["G1"]))
        except GeometryTakeoverRequired as conflict:
            assert conflict.losing_annotation == "OWNER"
            assert "ще загуби одобрението си" in str(conflict)
            return
        raise AssertionError("отнемането трябва да изисква потвърждение")


def test_explicit_confirmation_allows_the_takeover():
    with tempfile.TemporaryDirectory() as directory:
        root = _project_with_auto_link(directory, ["G1"])
        save_engineer_decision(root, _payload("NEW", ["G1"]),
                               allow_geometry_takeover=True)
        payload = json.loads(
            (root / "working" / "approved_geometry_links.json").read_text(
                encoding="utf-8"))
        owners = {link["annotation_handle"] for link in payload["links"]}
        assert "NEW" in owners


def test_different_geometry_is_not_a_takeover():
    with tempfile.TemporaryDirectory() as directory:
        root = _project_with_auto_link(directory, ["G1"])
        save_engineer_decision(root, _payload("NEW", ["G2"]))


def test_same_annotation_is_not_a_takeover():
    """Преработване на собственото решение не е отнемане."""

    with tempfile.TemporaryDirectory() as directory:
        root = _project_with_auto_link(directory, ["G1"])
        save_engineer_decision(root, _payload("OWNER", ["G1"]))


def test_legend_reference_is_shared_without_takeover():
    with tempfile.TemporaryDirectory() as directory:
        root = _project_with_auto_link(directory, ["G1"])
        payload = _payload("NEW", ["G1"])
        payload["geometry"]["basis"] = "POSITION_KEY_LEGEND_REFERENCE"
        payload["original"] = {"review_reason_codes": ["POSITION_KEY_LEGEND_REFERENCE"]}
        save_engineer_decision(root, payload)
        saved = json.loads(
            (root / "working" / "approved_geometry_links.json").read_text(encoding="utf-8")
        )
        assert {link["annotation_handle"] for link in saved["links"]} == {"OWNER", "NEW"}


def test_manual_owner_is_still_a_hard_refusal():
    """Ръчно потвърдена геометрия не се отнема дори с потвърждение."""

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        working = root / "working"
        working.mkdir(parents=True)
        (working / "approved_geometry_links.json").write_text(json.dumps({
            "schema": "lion-rebar-approved-geometry-links/v1",
            "links": [{
                "annotation_handle": "OWNER", "annotation_text": "25N8x135",
                "geometry_handles": ["G1"],
                "approval_status": "ОДОБРЕНО РЪЧНО",
                "approved_by": "USER",
            }],
            "history": [],
        }, ensure_ascii=False), encoding="utf-8")
        try:
            save_engineer_decision(root, _payload("NEW", ["G1"]),
                                   allow_geometry_takeover=True)
        except ValueError as error:
            assert "потвърдена ръчно" in str(error)
            return
        raise AssertionError("ръчното потвърждение не бива да се отнема")


def test_ui_asks_before_taking_over():
    assert "GeometryTakeoverRequired" in MAIN
    assert "_save_decision_with_takeover_prompt" in MAIN
    start = MAIN.index("def _save_decision_with_takeover_prompt")
    body = MAIN[start:start + 1600]
    assert "QMessageBox.question" in body
    assert "allow_geometry_takeover=True" in body


def test_ui_defaults_to_refusing():
    start = MAIN.index("def _save_decision_with_takeover_prompt")
    body = MAIN[start:start + 1600]
    assert "QMessageBox.StandardButton.No," in body


def test_takeover_is_journalled():
    start = MAIN.index("def _save_decision_with_takeover_prompt")
    body = MAIN[start:start + 1600]
    assert "GEOMETRY_TAKEOVER_CONFIRMED" in body


def test_cancelling_writes_nothing():
    start = MAIN.index("def _save_decision_with_takeover_prompt")
    body = MAIN[start:start + 1600]
    assert "return None" in body
