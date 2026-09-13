from __future__ import annotations

import copy
from pathlib import Path

from lion_rebar.engine.models import RuleDefinition, utc_now_iso
from lion_rebar.engine.package_io import export_engine_bundle, inspect_engine_bundle
from lion_rebar.engine.test_runner import run_engine_tests
from lion_rebar.engine.validation import validate_engine


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (
    ROOT
    / "engine_updates"
    / "lion-rebar-master_v0.3.13_F003_F005_DISTINCT_CONTEXT_PRODUCTION_DRAFT.lionengine"
)
TARGET = (
    ROOT
    / "engine_updates"
    / "lion-rebar-master_v0.3.14_F005_INDEPENDENT_ARMS_PRODUCTION_DRAFT.lionengine"
)

SHORTER = "BOTH_SHORTER_THAN_MIDDLE"
LONGER = "BOTH_LONGER_THAN_MIDDLE"


def _set_third_f005_dimension_to_l3(f005) -> None:
    for dimension in f005.dimensions:
        if int(dimension.get("№_СЕГМЕНТ", 0) or 0) == 3:
            dimension["КОД_ПОКАЗВАН_РАЗМЕР"] = "L3"
            dimension["БЕЛЕЖКИ"] = "Долно независимо рамо."

    third = next(
        (segment for segment in f005.segments if int(segment.index) == 3),
        None,
    )
    if third is None:
        raise RuntimeError("F005 segment 3 is missing from the source Engine.")
    third.length_code = "L3"
    third.notes = "Долно независимо рамо."
    raw = third.raw if isinstance(third.raw, dict) else {}
    dimension_raw = raw.setdefault("dimension", {})
    if isinstance(dimension_raw, dict):
        dimension_raw["КОД_ПОКАЗВАН_РАЗМЕР"] = "L3"
        dimension_raw["БЕЛЕЖКИ"] = "Долно независимо рамо."
    segment_raw = raw.setdefault("segment", {})
    if isinstance(segment_raw, dict):
        segment_raw["КОД_ДЪЛЖИНА"] = "L3"
        segment_raw["БЕЛЕЖКИ"] = "Долно независимо рамо."
    third.raw = raw


def _upgrade_f005_control_tests(engine) -> None:
    existing_ids: set[str] = set()
    for test in engine.tests:
        test_id = str(test.get("test_id") or test.get("ID_ТЕСТ") or "").strip()
        if test_id:
            existing_ids.add(test_id)
        if test_id != "T-F005-001":
            continue
        test["ОЧАКВАНИ_РАЗМЕРИ_СМ"] = "L1=61; L2=18; L3=61"
        test["ОЧАКВАНА_ОБЩА_ДЪЛЖИНА_СМ"] = 140
        test["БЕЛЕЖКИ"] = (
            "Симетричен контрол: 61 + 18 + 61 = 140 cm, но L1 и L3 "
            "са отделни производствени параметри."
        )
        test["РЕВИЗИЯ"] = "LIB_v1.3"

    if "T-F005-002" not in existing_ids:
        engine.tests.append(
            {
                "ID_ТЕСТ": "T-F005-002",
                "БЕЛЕЖКИ": (
                    "Асиметрична фиба: 90 + 20 + 50 = 160 cm; и двете "
                    "външни рамена са по-дълги от средния сегмент."
                ),
                "ИЗТОЧНИК_ФАЙЛ": "ВЪТРЕШЕН_КОНТРОЛЕН_ТЕСТ",
                "КОД_ФОРМА": "F005",
                "ЛИСТ_ДЕТАЙЛ": "F005",
                "ОЧАКВАНА_ОБЩА_ДЪЛЖИНА_СМ": 160,
                "ОЧАКВАНА_ОРИЕНТАЦИЯ": "Фибата е отворена надясно.",
                "ОЧАКВАНИ_РАЗМЕРИ_СМ": "L1=90; L2=20; L3=50",
                "ПОЗИЦИЯ_В_ЧЕРТЕЖА": "ТЕСТОВА_ПОЗИЦИЯ_5B",
                "РЕВИЗИЯ": "LIB_v1.3",
                "РЕЗУЛТАТ_ТЕСТ": "НЕИЗПЪЛНЕН",
                "СТАТУС": "ПОДГОТВЕН",
            }
        )


def build() -> Path:
    source_engine, _manifest, _names = inspect_engine_bundle(SOURCE)
    if source_engine.version != "0.3.13":
        raise RuntimeError(f"Expected Engine v0.3.13, got {source_engine.version}.")
    engine = copy.deepcopy(source_engine)

    changed_at = utc_now_iso()
    engine.parent_version = engine.version
    engine.version = "0.3.14"
    engine.updated_at = changed_at
    engine.state = "DRAFT"
    engine.published_at = None
    engine.content_sha256 = None
    engine.production_ready = True
    engine.approval_note = (
        "F005 has three independent production arms L1/L2/L3. The declared "
        "F003/F005 U relation checks both outer arms before dimension evidence."
    )

    f003 = engine.find_shape("F003")
    f005 = engine.find_shape("F005")
    if f003 is None or f005 is None:
        raise RuntimeError("F003 or F005 is missing from the source Engine.")

    for shape, relation in ((f003, SHORTER), (f005, LONGER)):
        shape.revision = max(4, int(shape.revision))
        shape.recognition = dict(shape.recognition or {})
        shape.recognition.pop("requires_equal_end_arms", None)
        shape.recognition.update(
            {
                "segment_count": 3,
                "bend_count": 2,
                "distinct_from": ["F005" if shape.code == "F003" else "F003"],
                "u_outer_segment_indices": [1, 3],
                "u_middle_segment_index": 2,
                "u_outer_arms_relation": relation,
            }
        )

    f003.notes = (
        "L1, L2 и L3 са независими. F003 е U-форма, при която и двете "
        "външни рамена са по-къси от средния сегмент извън толерансната лента."
    )
    f003.template_orientation = (
        "Основният среден сегмент L2 е дълъг; независимите крайни винкели "
        "L1 и L3 са по-къси."
    )
    f003.controlled_revision = {
        "at": changed_at,
        "authorized_by": "Александър Пашунов",
        "original_code": "F003",
        "reason": (
            "Изрична relation декларация: и двете външни рамена са "
            "по-къси от средния сегмент."
        ),
    }

    _set_third_f005_dimension_to_l3(f005)
    _upgrade_f005_control_tests(engine)
    f005.symmetric = False
    f005.notes = (
        "Фиба с три независими производствени размера L1, L2 и L3. "
        "И двете външни рамена са по-дълги от средния сегмент; не се "
        "изисква L1 да е равно на L3."
    )
    f005.template_orientation = (
        "Двете независими външни рамена L1 и L3 са отворени надясно; "
        "свързващият среден сегмент L2 е вертикален вляво."
    )
    legacy_form = f005.legacy_raw.setdefault("form", {})
    if isinstance(legacy_form, dict):
        legacy_form["ВЕРСИЯ"] = 1.3
        legacy_form["СИМЕТРИЧНА"] = "НЕ"
        legacy_form["БЕЛЕЖКИ"] = (
            "L1, L2 и L3 са независими променливи размери; L1 и L3 "
            "не са задължително равни."
        )
        legacy_form["ОРИЕНТАЦИЯ_ПО_ШАБЛОН"] = f005.template_orientation
    f005.controlled_revision = {
        "at": changed_at,
        "authorized_by": "Александър Пашунов",
        "original_code": "F005",
        "reason": (
            "F005 приема асиметрични фиби: L1 и L3 са независими, когато "
            "и двете рамена са по-дълги от L2."
        ),
    }

    engine.rules = [
        rule
        for rule in engine.rules
        if rule.code != "R-U-OUTER-MIDDLE-PROPORTION-F003-F005"
    ]
    engine.rules.append(
        RuleDefinition(
            code="R-U-OUTER-MIDDLE-PROPORTION-F003-F005",
            name="Двете външни рамена разделят F003 и F005",
            scope="SHAPE_CLASSIFICATION",
            priority=7,
            enabled=True,
            when={
                "all": [
                    {"field": "segment_count", "operator": "equals", "value": 3},
                    {"field": "bend_count", "operator": "equals", "value": 2},
                    {
                        "field": "engine_u_outer_relation",
                        "operator": "is_declared",
                        "value": True,
                    },
                ]
            },
            then=[
                {
                    "action": "map_both_outer_shorter_than_middle",
                    "shape": "F003",
                },
                {
                    "action": "map_both_outer_longer_than_middle",
                    "shape": "F005",
                },
                {"action": "keep_mixed_or_boundary_ambiguous", "value": True},
            ],
            requires_manual_approval=False,
            notes=(
                "Изпълнява се преди dimension ambiguity. Всяко външно "
                "рамо се сравнява отделно със средния сегмент."
            ),
        )
    )

    geometry = engine.settings.setdefault("geometry_recognition", {})
    geometry["u_subtype_proportion_rule_enabled"] = True
    geometry["u_subtype_arm_ratio_tolerance"] = 0.10
    geometry["u_subtype_requires_both_outer_arms"] = True
    for item in engine.extensions.get("semantic_collision_resolutions", []):
        pair = {
            str(code).strip().upper()
            for code in item.get("shape_codes", [])
        }
        if pair == {"F003", "F005"}:
            item["reason"] = (
                "F003 и F005 споделят U-топология, но са разделени от "
                "Engine-declared two-sided outer-vs-middle relation. Mixed "
                "и гранични случаи остават задължително за ръчен преглед."
            )
    engine.change_log.append(
        {
            "at": changed_at,
            "action": "F005_INDEPENDENT_ARMS_AND_TWO_SIDED_U_RELATION",
            "by": "Александър Пашунов / OpenAI Codex",
            "details": {
                "f003_relation": SHORTER,
                "f005_relation": LONGER,
                "f005_parameters": ["L1", "L2", "L3"],
                "mixed_or_boundary": "AMBIGUOUS",
                "resolver_order": "U_PROPORTION_BEFORE_DIMENSION_AMBIGUITY",
            },
        }
    )

    issues = validate_engine(engine, parent=source_engine, for_publish=True)
    errors = [issue for issue in issues if issue.severity == "ERROR"]
    if errors:
        raise RuntimeError("Engine validation failed: " + "; ".join(i.message for i in errors))
    failed = [result for result in run_engine_tests(engine) if not result.passed]
    if failed:
        raise RuntimeError("Engine tests failed: " + "; ".join(r.message for r in failed))

    return export_engine_bundle(engine, TARGET)


if __name__ == "__main__":
    print(build())
