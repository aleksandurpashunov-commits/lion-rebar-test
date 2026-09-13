from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from lion_rebar.dxf.batch_geometry_report import build_batch_geometry_report
from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.engine.package_io import export_engine_bundle, inspect_engine_bundle
from lion_rebar.engine.models import utc_now_iso


ROOT = Path(__file__).resolve().parents[3]
SOURCE_ENGINE = ROOT / "downloads" / "lion-rebar-master_v0.3.5_DRAFT.lionengine"
TARGET_ENGINE = ROOT / "downloads" / "lion-rebar-master_v0.3.6_DRAFT.lionengine"
ENTITIES_JSON = ROOT / "audit_holdout" / "working" / "extracted_entities.json"
APPROVALS_JSON = ROOT / "audit_holdout" / "working" / "approved_geometry_links.json"
OUTPUT = ROOT / "holdout_v0141"


def build_engine():
    engine, _, _ = inspect_engine_bundle(SOURCE_ENGINE)
    engine.parent_version = engine.version
    engine.version = "0.3.6"
    engine.updated_at = utc_now_iso()
    engine.state = "DRAFT"
    engine.production_ready = False
    shape = engine.find_shape("F019")
    if shape is None:
        raise RuntimeError("F019 is missing")
    shape.parameter_relations = []
    shape.recognition = {
        **dict(shape.recognition or {}),
        "reference_segment_lengths_cm": [50.0, 286.0, 70.0],
    }
    for test in engine.tests:
        code = str(
            test.get("shape_code")
            or test.get("КОД_ФОРМА")
            or ""
        ).upper()
        if code == "F019":
            test["dimensions"] = "L1=50; L2=286; L3=70"
            test["expected_total_cm"] = 406.0
            test["purpose"] = "ENGINE_TEST_AND_MATCHER_PROFILE"
    engine.change_log.append(
        {
            "at": utc_now_iso(),
            "action": "CORRECT_F019_AND_MATCHER_HOLDOUT",
            "shape": "F019",
            "by": "CODEX",
            "reason": (
                "Holdout DXF proves 50+286+70=406 cm; remove the invalid "
                "L1=L3 relation and replace the 50+306+50 profile."
            ),
        }
    )
    export_engine_bundle(engine, TARGET_ENGINE)
    return engine


def load_entities() -> list[DxfEntityRecord]:
    payload = json.loads(ENTITIES_JSON.read_text(encoding="utf-8"))
    return [DxfEntityRecord(**row) for row in payload["entities"]]


def load_approvals() -> dict[str, dict]:
    payload = json.loads(APPROVALS_JSON.read_text(encoding="utf-8"))
    return {
        str(row["annotation_handle"]): row
        for row in payload.get("links", [])
    }


def main() -> None:
    engine = build_engine()
    entities = load_entities()
    approvals = load_approvals()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    result = build_batch_geometry_report(
        entities,
        OUTPUT,
        project_name="TEST_HOLDOUT_01_V0141",
        approved_links=approvals,
        generated_at=datetime.now().astimezone(),
        shape_definitions=engine.shapes,
        control_tests=engine.tests,
        policy=engine.settings,
        engine_metadata={
            "engine_id": engine.engine_id,
            "engine_version": engine.version,
            "content_sha256": engine.content_sha256,
        },
        checkpoint_every=100,
        skip_approved=False,
    )
    print(result.json_path)


if __name__ == "__main__":
    main()
