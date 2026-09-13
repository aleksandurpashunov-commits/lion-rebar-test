from __future__ import annotations

from pathlib import Path

from lion_rebar.engine.models import RuleDefinition, utc_now_iso
from lion_rebar.engine.package_io import export_engine_bundle, inspect_engine_bundle
from lion_rebar.engine.test_runner import run_engine_tests
from lion_rebar.engine.validation import validate_engine


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (
    ROOT
    / "engine_updates"
    / "lion-rebar-master_v0.3.12_POSITION_KEY_F001_CALIBRATION_PRODUCTION_DRAFT.lionengine"
)
TARGET = (
    ROOT
    / "engine_updates"
    / "lion-rebar-master_v0.3.13_F003_F005_DISTINCT_CONTEXT_PRODUCTION_DRAFT.lionengine"
)


def build() -> Path:
    engine, _manifest, _names = inspect_engine_bundle(SOURCE)
    if engine.version != "0.3.12":
        raise RuntimeError(f"Expected Engine v0.3.12, got {engine.version}.")

    changed_at = utc_now_iso()
    engine.parent_version = engine.version
    engine.version = "0.3.13"
    engine.updated_at = changed_at
    engine.state = "DRAFT"
    engine.published_at = None
    engine.content_sha256 = None
    engine.production_ready = True
    engine.approval_note = (
        "F003/F005 are restored as distinct production subtypes. Explicit named "
        "context is required when their U topology is otherwise identical."
    )

    f003 = engine.find_shape("F003")
    f005 = engine.find_shape("F005")
    if f003 is None or f005 is None:
        raise RuntimeError("F003 or F005 is missing from the source Engine.")

    f003.name = "ПРЪТ С ДВА ВИНКЕЛА"
    f003.revision = max(3, int(f003.revision))
    f003.active = True
    f003.state = "LOCKED"
    f003.locked = True
    f003.notes = (
        "L1, L2 и L3 са независими променливи размери. F003 остава отделен "
        "производствен тип от F005; при симетрична U-геометрия се изисква "
        "изричен именуван контекст."
    )
    f003.recognition = {
        key: value
        for key, value in dict(f003.recognition or {}).items()
        if key != "canonical_replacement"
    }
    f003.recognition.update(
        {
            "segment_count": 3,
            "bend_count": 2,
            "named_context_tokens": ["ОТВОРЕН ДЕТАЙЛ", "U-ФОРМА", "U ФОРМА"],
            "distinct_from": ["F005"],
        }
    )
    f003.controlled_revision = {
        "at": changed_at,
        "authorized_by": "Александър Пашунов",
        "original_code": "F003",
        "reason": (
            "Възстановяване на отделния производствен смисъл: F003 е прът с "
            "два винкела, а не каноничен заместител на F005."
        ),
    }

    f005.revision = max(3, int(f005.revision))
    f005.active = True
    f005.state = "LOCKED"
    f005.locked = True
    f005.alias_of = None
    f005.notes = (
        "Фиба с две еднакви рамена L1 и свързващ размер L2. Отделен "
        "производствен подтип; не се преобразува към F003."
    )
    f005.recognition = {
        key: value
        for key, value in dict(f005.recognition or {}).items()
        if key != "canonical_replacement"
    }
    f005.recognition.update(
        {
            "segment_count": 3,
            "bend_count": 2,
            "named_context_tokens": ["ФИБА", "ФИБИ"],
            "requires_equal_end_arms": True,
            "distinct_from": ["F003"],
        }
    )
    f005.controlled_revision = {
        "at": changed_at,
        "authorized_by": "Александър Пашунов",
        "original_code": "F005",
        "reason": (
            "Отмяна на грешното RETIRED/canonical_replacement=F003. Фибата "
            "остава активен и проследим производствен тип."
        ),
    }

    engine.rules = [
        rule
        for rule in engine.rules
        if rule.code
        not in {"R-NAMED-U-SUBTYPE-F003-F005", "R-F006-PRINTED-MASTER-EQUATION"}
    ]
    engine.rules.append(
        RuleDefinition(
            code="R-NAMED-U-SUBTYPE-F003-F005",
            name="Именуван контекст разделя F003 и F005",
            scope="SHAPE_CLASSIFICATION",
            priority=8,
            enabled=True,
            when={
                "all": [
                    {"field": "segment_count", "operator": "equals", "value": 3},
                    {"field": "bend_count", "operator": "equals", "value": 2},
                    {
                        "field": "named_context_count_diameter_signature",
                        "operator": "is_unique",
                        "value": True,
                    },
                ]
            },
            then=[
                {"action": "map_fiba_text_to", "shape": "F005"},
                {"action": "map_open_u_text_to", "shape": "F003"},
                {"action": "keep_geometry_only_ambiguous", "value": True},
            ],
            requires_manual_approval=False,
            notes=(
                "Изпълнява се от batch_geometry_report Закон 30. Никога не "
                "използва само геометрията, за да замени F005 с F003."
            ),
        )
    )
    engine.rules.append(
        RuleDefinition(
            code="R-F006-PRINTED-MASTER-EQUATION",
            name="F006 използва отпечатаните ширина, височина и кука",
            scope="PRODUCTION_DIMENSIONS",
            priority=7,
            enabled=True,
            when={
                "all": [
                    {"field": "master_shape_code", "operator": "equals", "value": "F006"},
                    {"field": "printed_width_height_hook", "operator": "is_complete", "value": True},
                ]
            },
            then=[
                {
                    "action": "require_equation",
                    "formula": "L=2*(width+height+hook)",
                    "tolerance_cm": 2.1,
                },
                {"action": "keep_raw_dxf_length_for_audit", "value": True},
                {"action": "block_if_equation_fails", "value": True},
            ],
            requires_manual_approval=False,
            notes=(
                "Изпълнява се от batch_geometry_report Закон 14 и от Excel "
                "export. Схематичният DXF габарит не е производствена L."
            ),
        )
    )

    geometry = engine.settings.setdefault("geometry_recognition", {})
    geometry["named_u_subtype_context_rule_enabled"] = True
    engine.change_log.append(
        {
            "at": changed_at,
            "action": "RESTORE_DISTINCT_F003_F005_AND_EXPLICIT_CONTEXT_GATE",
            "by": "Александър Пашунов / OpenAI Codex",
            "details": {
                "f003_active": True,
                "f005_active": True,
                "f005_canonical_replacement": None,
                "geometry_only_equal_u": "AMBIGUOUS",
                "explicit_fiba_text": "F005",
                "explicit_open_u_text": "F003",
            },
        }
    )

    issues = validate_engine(engine, for_publish=True)
    errors = [issue for issue in issues if issue.severity == "ERROR"]
    if errors:
        raise RuntimeError("Engine validation failed: " + "; ".join(i.message for i in errors))
    failed = [result for result in run_engine_tests(engine) if not result.passed]
    if failed:
        raise RuntimeError("Engine tests failed: " + "; ".join(r.message for r in failed))

    return export_engine_bundle(engine, TARGET)


if __name__ == "__main__":
    print(build())
