from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from lion_rebar.diagnostics import DiagnosticBundleError, create_diagnostic_bundle


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "TEST_PROJECT"
    for name in ("source", "master", "working", "output", "logs"):
        (root / name).mkdir(parents=True)
    engine = root / "master" / "engine_lion-rebar-master_v0.3.4.json"
    _write_json(engine, {"engine_id": "lion-rebar-master", "version": "0.3.4"})
    _write_json(
        root / "working" / "project.json",
        {
            "schema": "lion-rebar-project/v1",
            "version": "0.1.0",
            "name": "TEST PROJECT",
            "root": str(root),
            "engine_id": "lion-rebar-master",
            "engine_version": "0.3.4",
            "engine_sha256": "engine-sha",
            "extensions": {
                "engine_snapshot": "master/engine_lion-rebar-master_v0.3.4.json",
                "source_sha256": "source-sha",
                "api_token": "must-not-leak",
            },
        },
    )
    return root


def test_bundle_contains_only_allowlisted_reproducible_evidence(tmp_path: Path) -> None:
    root = _project(tmp_path)
    source_payload = b"0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n"
    (root / "source" / "original.dxf").write_bytes(source_payload)
    _write_json(
        root / "working" / "review_center_state.json",
        {
            "decisions": {"A1": {"status": "APPROVED"}},
            "external_path": r"C:\Users\Aleksandar\Desktop\case.dxf",
            "short_absolute_path": "/tmp",
        },
    )
    _write_json(root / "working" / "review_problems.json", {"problems": []})
    _write_json(root / "working" / "approved_geometry_links.json", {"links": []})
    _write_json(root / "working" / "manual_shape_drafts.json", {"drafts": []})
    _write_json(
        root
        / "working"
        / "shape_candidates"
        / "SHAPE_CANDIDATE_A1.json",
        {
            "schema": "lion-rebar-project-shape-candidate/v1",
            "candidate": {"candidate_id": "TEST", "forms": []},
        },
    )
    _write_json(
        root / "working" / "manual_shape_training_examples.json",
        {"examples": []},
    )
    _write_json(root / "working" / "extracted_entities.json", {"entities": []})
    (root / "working" / "review_feedback_events.jsonl").write_text(
        json.dumps(
            {
                "event": "ASSIGN",
                "file": str(root / "source" / "original.dxf"),
                "secret": "hidden",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "working" / "not_allowlisted.txt").write_text("private", encoding="utf-8")

    older = root / "output" / "TEST_PROJECT_BATCH_REVIEW_20260101_000000"
    newer = root / "output" / "TEST_PROJECT_BATCH_REVIEW_20260102_000000"
    older.mkdir()
    newer.mkdir()
    _write_json(
        older / "TEST_PROJECT_geometry_batch.json",
        {
            "schema": "lion-rebar-geometry-batch-report/v3",
            "project_name": "TEST PROJECT",
            "generation": "old",
        },
    )
    _write_json(
        newer / "TEST_PROJECT_geometry_batch.json",
        {
            "schema": "lion-rebar-geometry-batch-report/v3",
            "project_name": "TEST PROJECT",
            "generation": "new",
        },
    )
    (newer / "TEST_PROJECT_geometry_batch.csv").write_text("status\nOK\n", encoding="utf-8")
    (newer / "TEST_PROJECT_geometry_batch.html").write_text(
        "<html>new report</html>",
        encoding="utf-8",
    )
    (newer / "TEST_PROJECT_geometry_batch_RISK.csv").write_text(
        "status\nRISK\n",
        encoding="utf-8",
    )
    (newer / "README_BG.txt").write_text("latest", encoding="utf-8")
    (newer / "ignored.exe").write_bytes(b"never")
    (root / "logs" / "lion.log").write_text(
        f"open={root / 'source' / 'original.dxf'} token=top-secret\n",
        encoding="utf-8",
    )

    output = tmp_path / "diagnostic.zip"
    result = create_diagnostic_bundle(root, output)

    assert result == output
    with zipfile.ZipFile(result) as archive:
        names = set(archive.namelist())
        assert "source/original.dxf" in names
        assert "master/engine_lion-rebar-master_v0.3.4.json" in names
        assert "project/project.json" in names
        assert "working/review_center_state.json" in names
        assert "working/review_problems.json" in names
        assert "working/approved_geometry_links.json" in names
        assert "working/manual_shape_drafts.json" in names
        assert (
            "working/shape_candidates/SHAPE_CANDIDATE_A1.json"
            in names
        )
        assert "working/manual_shape_training_examples.json" in names
        assert "working/extracted_entities.json" in names
        assert "feedback/events.jsonl" in names
        assert "reports/latest/TEST_PROJECT_geometry_batch.json" in names
        assert "reports/latest/TEST_PROJECT_geometry_batch_RISK.csv" in names
        assert "README_BG.txt" in names
        assert "manifest.json" in names
        assert not any("20260101" in name for name in names)
        assert not any("not_allowlisted" in name for name in names)
        assert not any(name.endswith(".exe") for name in names)
        assert archive.read("source/original.dxf") == source_payload

        project_text = archive.read("project/project.json").decode("utf-8")
        state_text = archive.read("working/review_center_state.json").decode("utf-8")
        event_text = archive.read("feedback/events.jsonl").decode("utf-8")
        log_name = next(name for name in names if name.startswith("logs/"))
        log_text = archive.read(log_name).decode("utf-8")
        assert str(root) not in project_text
        assert str(root) not in state_text
        assert str(root) not in event_text
        assert str(root) not in log_text
        assert "must-not-leak" not in project_text
        assert "top-secret" not in log_text
        assert r"C:\Users\Aleksandar" not in state_text
        assert '"/tmp"' not in state_text
        assert '"root": "."' in project_text

        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["schema"] == "lion-rebar-diagnostic-bundle/v1"
        assert manifest["privacy"]["environment_variables_included"] is False
        assert manifest["privacy"]["absolute_paths_in_manifest"] is False
        assert str(root) not in json.dumps(manifest)
        for item in manifest["files"]:
            payload = archive.read(item["path"])
            assert item["sha256"] == hashlib.sha256(payload).hexdigest()
            assert item["size_bytes"] == len(payload)
            assert not item["path"].startswith("/")
            assert ".." not in Path(item["path"]).parts


def test_bundle_can_omit_source_and_uses_safe_default_destination(tmp_path: Path) -> None:
    root = _project(tmp_path)
    (root / "source" / "original.dxf").write_bytes(b"DXF")
    (root / "working" / "feedback_events.jsonl").write_text(
        '{"not":"source DXF"}\n',
        encoding="utf-8",
    )

    result = create_diagnostic_bundle(root, include_source_dxf=False)

    assert result.parent == root / "output" / "05_DIAGNOSTICS"
    assert result.name.startswith("TEST_PROJECT_AI_DIAGNOSTIC_")
    with zipfile.ZipFile(result) as archive:
        assert "source/original.dxf" not in archive.namelist()
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["privacy"]["source_dxf_exact_binary"] is False


def test_bundle_preserves_invalid_jsonl_as_sanitized_diagnostic_record(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    (root / "working" / "review_feedback_events.jsonl").write_text(
        '{"ok": true}\nnot-json\n',
        encoding="utf-8",
    )
    output = tmp_path / "broken.zip"

    create_diagnostic_bundle(root, output)

    with zipfile.ZipFile(output) as archive:
        lines = archive.read("feedback/events.jsonl").decode("utf-8").splitlines()
        assert json.loads(lines[0]) == {"ok": True}
        invalid = json.loads(lines[1])
        assert invalid["_diagnostic_status"] == "INVALID_JSONL_RECORD"
        assert invalid["_line_number"] == 2


def test_traversal_or_symlink_engine_snapshot_is_not_packaged(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    external = tmp_path / "outside.json"
    _write_json(external, {"outside": True})
    project_path = root / "working" / "project.json"
    project = json.loads(project_path.read_text(encoding="utf-8"))
    project["extensions"]["engine_snapshot"] = "../outside.json"
    _write_json(project_path, project)
    for path in (root / "master").glob("engine_*.json"):
        path.unlink()
    symlink = root / "master" / "engine_escape.json"
    try:
        symlink.symlink_to(external)
    except OSError:
        pytest.skip("Symlinks are not available on this platform.")

    output = create_diagnostic_bundle(root, tmp_path / "safe.zip")

    with zipfile.ZipFile(output) as archive:
        names = archive.namelist()
        assert not any(name.startswith("master/") for name in names)
        manifest = json.loads(archive.read("manifest.json"))
        assert "pinned_engine_snapshot" in manifest["missing_optional_evidence"]
        assert ".." not in json.dumps(manifest)


def test_explicit_external_batch_report_is_allowlisted_without_leaking_path(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    report_dir = tmp_path / "Cloud Reports" / "CASE_BATCH_REVIEW_20260727_190000"
    report_dir.mkdir(parents=True)
    report = report_dir / "CASE_geometry_batch.json"
    _write_json(
        report,
        {
            "schema": "lion-rebar-geometry-batch-report/v3",
            "rows": [],
            "external_absolute": str(report_dir),
        },
    )
    (report_dir / "CASE_geometry_batch.csv").write_text("status\n", encoding="utf-8")
    (report_dir / "secret.bin").write_bytes(b"not included")

    output = create_diagnostic_bundle(
        root,
        tmp_path / "external-report.zip",
        latest_report_path=report,
    )

    with zipfile.ZipFile(output) as archive:
        names = archive.namelist()
        assert "reports/latest/CASE_geometry_batch.json" in names
        assert "reports/latest/CASE_geometry_batch.csv" in names
        assert not any(name.endswith(".bin") for name in names)
        payload = archive.read("reports/latest/CASE_geometry_batch.json").decode("utf-8")
        manifest = archive.read("manifest.json").decode("utf-8")
        assert str(report_dir) not in payload
        assert str(report_dir) not in manifest


def test_diagnostic_rejects_batch_report_from_another_project(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    report_dir = tmp_path / "Shared Cloud" / "OTHER_BATCH_REVIEW_20260727_200000"
    report_dir.mkdir(parents=True)
    report = report_dir / "OTHER_geometry_batch.json"
    _write_json(
        report,
        {
            "schema": "lion-rebar-geometry-batch-report/v3",
            "project_name": "OTHER PROJECT",
            "rows": [],
        },
    )
    output = tmp_path / "must-not-mix.zip"

    with pytest.raises(DiagnosticBundleError, match="OTHER PROJECT"):
        create_diagnostic_bundle(
            root,
            output,
            latest_report_path=report,
        )

    assert not output.exists()
