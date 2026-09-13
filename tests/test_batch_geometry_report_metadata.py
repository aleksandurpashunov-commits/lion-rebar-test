from __future__ import annotations

import json
from datetime import datetime, timezone

from lion_rebar.dxf.batch_geometry_report import build_batch_geometry_report
from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.project.data_identity import IDENTITY_KEY, DataIdentity


def test_batch_report_accepts_and_records_engine_metadata(tmp_path) -> None:
    metadata = {
        "engine_id": "lion-rebar-master",
        "engine_version": "0.3.6",
        "content_sha256": "test-sha256",
    }

    entities = [
        DxfEntityRecord(
            handle="A1",
            entity_type="BLOCK_ATTR_TEXT",
            layer="ARM",
            text="2N8x100",
            geometry={"insert": [0.0, 0.0, 0.0]},
        )
    ]

    result = build_batch_geometry_report(
        entities,
        tmp_path,
        project_name="BATCH_METADATA_REGRESSION",
        generated_at=datetime(2026, 8, 1, 16, 0, tzinfo=timezone.utc),
        engine_metadata=metadata,
    )

    payload = json.loads(result.json_path.read_text(encoding="utf-8"))

    assert payload["analysis_engine"] == metadata
    assert payload["processed_annotations"] == 1
    assert payload["summary"]["errors"] == 0
    assert result.zip_path.is_file()


def test_batch_report_and_checkpoints_keep_one_data_identity(tmp_path) -> None:
    identity = DataIdentity(
        project_id="PROJECT-631",
        source_sha256="a" * 64,
        source_generation_id="SOURCE-GENERATION-631",
        analysis_run_id="ANALYSIS-RUN-631",
    )
    entities = [
        DxfEntityRecord(
            handle="A1",
            entity_type="BLOCK_ATTR_TEXT",
            layer="ARM",
            text="2N8x100",
            geometry={"insert": [0.0, 0.0, 0.0]},
        )
    ]

    result = build_batch_geometry_report(
        entities,
        tmp_path,
        project_name="BATCH_IDENTITY_REGRESSION",
        generated_at=datetime(2026, 8, 14, 17, 0, tzinfo=timezone.utc),
        data_identity=identity,
        checkpoint_every=1,
    )

    expected = identity.to_dict()
    payload = json.loads(result.json_path.read_text(encoding="utf-8"))
    progress = json.loads((result.report_dir / "progress.json").read_text(encoding="utf-8"))
    final_progress = json.loads(
        (result.report_dir / "progress_final.json").read_text(encoding="utf-8")
    )

    for candidate in (payload, progress, final_progress):
        assert candidate[IDENTITY_KEY] == expected
        assert candidate["rows"]
        assert all(row[IDENTITY_KEY] == expected for row in candidate["rows"])
