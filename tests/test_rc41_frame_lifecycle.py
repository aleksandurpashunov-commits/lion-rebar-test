"""Focused rc41 regressions for frame detection and lifecycle feedback."""

from __future__ import annotations

import ast
import inspect
import textwrap
from types import SimpleNamespace

import pytest

from lion_rebar.export.frame_detection import detect_drawing_frames, unresolved_frames
from lion_rebar.project.data_identity import DataIdentity
from lion_rebar.project.integrity import IntegrityIssue, _freeze_beam_assignments
from lion_rebar.ui.main_window import (
    MainWindow,
    _blocking_integrity_issue_text,
    _has_frame_resolution_issue,
    _has_position_blockers,
)


def _text(handle: str, text: str, x: float = 100.0, y: float = 200.0):
    return SimpleNamespace(
        handle=handle,
        entity_type="TEXT",
        text=text,
        geometry={"insert": [x, y, 0.0], "height": 21.0},
    )


def _insert(
    handle: str,
    x: float = 0.0,
    y: float = 0.0,
    layer: str = "ramka_1",
):
    return SimpleNamespace(
        handle=handle,
        entity_type="INSERT",
        layer=layer,
        text=None,
        geometry={"insert": [x, y, 0.0]},
    )


def test_prose_with_drawing_words_is_not_a_frame_marker():
    records = [
        _text(
            "N1",
            "Размерите са взети от чертежа и са сверени с останалите чертежите "
            "преди изпълнение на армировката на обекта.",
        )
    ]

    assert detect_drawing_frames(records) == []


def test_unique_explicit_top_title_creates_single_sheet_frame():
    frames = detect_drawing_frames(
        [
            _text("T1", "ГОРНА АРМИРОВКА НА ПЛОЧА"),
            _insert("F1", layer="ramka_1"),
        ]
    )

    assert len(frames) == 1
    assert frames[0].category_code == "TOP"
    assert frames[0].resolution_reason == "SINGLE_SHEET_TITLE_AND_FRAME_INSERT"
    assert frames[0].confidence == "MEDIUM"


def test_vertical_reinforcement_title_creates_verticals_frame():
    frames = detect_drawing_frames(
        [
            _text("V1", "ВЕРТ.АРМИРОВКА"),
            _text("S1", "СХЕМА НА АРМ. (1)", 100.0, 150.0),
            _text("C1", "КОЛОНИ(К) И ШАЙБИ", 100.0, 100.0),
        ]
    )

    assert len(frames) == 1
    assert frames[0].category_code == "VERTICALS"
    assert frames[0].resolution_reason == "SINGLE_SHEET_TITLE_AND_VERTICAL_SECTIONS"
    assert frames[0].confidence == "MEDIUM"


def test_unique_title_without_secondary_evidence_remains_without_frame():
    assert detect_drawing_frames([_text("T1", "ГОРНА АРМИРОВКА НА ПЛОЧА")]) == []


def test_two_frame_inserts_are_not_accepted_as_one_frameless_sheet():
    records = [
        _text("T1", "ГОРНА АРМИРОВКА НА ПЛОЧА"),
        _insert("F1", 0.0, 0.0, "ramka_1"),
        _insert("F2", 10000.0, 0.0, "ramka_2"),
    ]

    assert detect_drawing_frames(records) == []


@pytest.mark.parametrize(
    "layer",
    ["NO_FRAME", "UNFRAMED_REFERENCE", "KEYFRAME", "RAMKADOOR"],
)
def test_misleading_insert_layer_names_do_not_create_frame(layer):
    records = [
        _text("T1", "ГОРНА АРМИРОВКА НА ПЛОЧА"),
        _insert("F1", layer=layer),
    ]

    assert detect_drawing_frames(records) == []


@pytest.mark.parametrize(
    "layer",
    ["$Ramka", "ramka_1", "FRAME-2", "РАМКА 3"],
)
def test_explicit_frame_layer_allowlist_creates_single_sheet_frame(layer):
    records = [
        _text("T1", "ГОРНА АРМИРОВКА НА ПЛОЧА"),
        _insert("F1", layer=layer),
    ]

    frames = detect_drawing_frames(records)
    assert len(frames) == 1
    assert frames[0].category_code == "TOP"
    assert frames[0].resolution_reason == "SINGLE_SHEET_TITLE_AND_FRAME_INSERT"


def test_conflicting_standalone_titles_remain_without_frame():
    records = [
        _text("T1", "ГОРНА АРМИРОВКА", 100.0, 200.0),
        _text("T2", "ДОЛНА АРМИРОВКА", 100.0, 100.0),
    ]

    assert detect_drawing_frames(records) == []


def test_exact_marker_without_title_remains_unresolved_unknown():
    frames = detect_drawing_frames([_text("M1", "ЧЕРТЕЖ:")])

    assert len(frames) == 1
    assert frames[0].category_code == "UNKNOWN"
    assert frames[0].requires_operator_decision
    assert unresolved_frames(frames) == frames


def test_freeze_rejects_unknown_exact_marker_without_assignments(tmp_path):
    extracted = {
        "entities": [
            {
                "handle": "M1",
                "entity_type": "TEXT",
                "layer": "$Ramka",
                "geometry": {"insert": [0.0, 0.0, 0.0], "height": 21.0},
                "text": "ЧЕРТЕЖ:",
            },
            {
                "handle": "A1",
                "entity_type": "BLOCK_ATTR_TEXT",
                "layer": "ARM_TXT",
                "geometry": {"insert": [100.0, 100.0, 0.0]},
                "text": "2N8x100",
            },
        ]
    }

    assignments, issues = _freeze_beam_assignments(
        rows=[{"annotation_class": "ACTIONABLE", "annotation_handle": "A1"}],
        links_by_annotation={"A1": {"annotation_handle": "A1"}},
        extracted_payload=extracted,
        project_root=tmp_path,
        identity=DataIdentity("PROJECT", "s" * 64, "GENERATION", "RUN"),
    )

    assert assignments == {}
    assert [issue.code for issue in issues] == ["FRAME_CATEGORY_UNRESOLVED"]


def test_blocking_integrity_text_renders_error_and_lifecycle_blocker():
    result = SimpleNamespace(
        issues=[
            IntegrityIssue(
                code="FRAME_CATEGORY_UNRESOLVED",
                severity="ERROR",
                message="Рамката изисква инженерно решение.",
            ),
            IntegrityIssue(
                code="INFORMATION_ONLY",
                severity="INFO",
                message="Това не блокира.",
            ),
        ]
    )

    rendered = _blocking_integrity_issue_text(
        result,
        ["Липсва валиден freeze manifest."],
    )

    assert "FRAME_CATEGORY_UNRESOLVED" in rendered
    assert "Рамката изисква инженерно решение." in rendered
    assert "EXPORT_LIFECYCLE" in rendered
    assert "Липсва валиден freeze manifest." in rendered
    assert "INFORMATION_ONLY" not in rendered


def test_frame_resolution_issue_recognizes_explicit_issue_code():
    result = SimpleNamespace(
        issues=[
            IntegrityIssue(
                code="FRAME_CATEGORY_UNRESOLVED",
                severity="ERROR",
                message="Operator decision required.",
            )
        ]
    )

    assert _has_frame_resolution_issue(result) is True


def test_position_blockers_ignore_identity_and_technical_issues():
    technical_only = SimpleNamespace(
        unresolved_positions=0,
        safe_confirmation_pending=0,
        engineering_decisions_pending=0,
        explicit_exclusions=0,
        intrinsic_missing_master=0,
        identity_failures=1,
        issues=[
            IntegrityIssue(
                code="TECHNICAL_FAILURE",
                severity="ERROR",
                message="Technical lifecycle blocker.",
            )
        ],
    )
    one_unresolved = SimpleNamespace(
        unresolved_positions=1,
        safe_confirmation_pending=0,
        engineering_decisions_pending=0,
        explicit_exclusions=0,
        intrinsic_missing_master=0,
        identity_failures=0,
        issues=[],
    )

    assert _has_position_blockers(technical_only) is False
    assert _has_position_blockers(one_unresolved) is True


def test_unknown_frame_offer_uses_import_result_entities_wiring():
    tree = ast.parse(
        textwrap.dedent(inspect.getsource(MainWindow._offer_unknown_frame_resolution))
    )

    entity_assignment = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "entities"
            for target in node.targets
        )
    )
    assert isinstance(entity_assignment.value, ast.IfExp)
    assert ast.unparse(entity_assignment.value.body) == "self.import_result.entities"

    direct_self_entities = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and node.attr == "entities"
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    ]
    assert direct_self_entities == []

    calls = {
        ast.unparse(node.func)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    }
    assert "detect_drawing_frames" in calls
    assert "unresolved_frames" in calls
    assert "QMessageBox.question" in calls
