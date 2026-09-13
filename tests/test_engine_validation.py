from copy import deepcopy

from lion_rebar.engine.models import EnginePackage, SegmentDefinition, ShapeDefinition
from lion_rebar.engine.validation import has_errors, validate_engine


def _published_parent() -> EnginePackage:
    return EnginePackage(
        engine_id="lion",
        title="Master",
        version="1.0.0",
        state="PUBLISHED",
        shapes=[
            ShapeDefinition(
                code="F001",
                name="Прав прът",
                state="LOCKED",
                revision=1,
                locked=True,
                segments=[SegmentDefinition(index=1, length_code="L1")],
            )
        ],
    )


def test_locked_shape_change_requires_controlled_revision():
    parent = _published_parent()
    draft = parent.clone_as_draft("1.0.1")
    draft.shapes[0].name = "Changed"
    draft.shapes[0].state = "APPROVED"
    issues = validate_engine(draft, parent=parent, for_publish=True)
    assert has_errors(issues)
    assert any(issue.code == "LOCKED_REVISION_NUMBER" for issue in issues)


def test_controlled_revision_can_pass_structure_validation():
    parent = _published_parent()
    draft = parent.clone_as_draft("1.0.1")
    shape = draft.shapes[0]
    shape.name = "Прав прът — ревизия"
    shape.revision = 2
    shape.locked = False
    shape.state = "APPROVED"
    shape.controlled_revision = {
        "original_code": "F001",
        "authorized_by": "Александър",
        "reason": "Одобрена корекция",
    }
    issues = validate_engine(draft, parent=parent, for_publish=True)
    assert not has_errors(issues)


def test_locked_shape_cannot_disappear():
    parent = _published_parent()
    draft = parent.clone_as_draft("1.0.1")
    draft.shapes.clear()
    issues = validate_engine(draft, parent=parent, for_publish=True)
    assert any(issue.code == "MISSING_LOCKED_SHAPE" for issue in issues)
