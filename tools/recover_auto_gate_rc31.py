from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.engine.models import EnginePackage
from lion_rebar.export.auto_approval import auto_approve_gate_positions
from lion_rebar.export.auto_gate import auto_classification_gate_passes
from lion_rebar.export.batch_auto_output import build_batch_export_context
from lion_rebar.export.frame_decisions import load_frame_decisions
from lion_rebar.project.approval_origin import is_auto_approval, is_manual_approval
from lion_rebar.project.data_identity import current_identity, mismatch_reason
from lion_rebar.project.integrity import reconcile_project_integrity
from lion_rebar.project.models import ProjectModel
from lion_rebar.utils.files import sha256_file


REVOCATION_REASON = "AUTO_LINK_ROW_FAILS_GATE"


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise RuntimeError(f"Expected a JSON object: {path}")
    return value


def _records(payload: Mapping[str, Any]) -> list[DxfEntityRecord]:
    records: list[DxfEntityRecord] = []
    for raw in payload.get("entities") or []:
        if not isinstance(raw, Mapping) or not str(raw.get("handle") or ""):
            continue
        records.append(
            DxfEntityRecord(
                handle=str(raw.get("handle") or ""),
                entity_type=str(raw.get("entity_type") or ""),
                layer=str(raw.get("layer") or ""),
                geometry=dict(raw.get("geometry") or {}),
                text=raw.get("text"),
                block_name=raw.get("block_name"),
                color_index=raw.get("color_index"),
                linetype=raw.get("linetype"),
                raw=dict(raw.get("raw") or {}),
            )
        )
    return records


def _load_project(root: Path) -> tuple[ProjectModel, dict[str, Any]]:
    payload = _read_json(root / "working" / "project.json")
    project = ProjectModel.from_dict(payload)
    project.root = str(root)
    return project, payload


def _load_engine(root: Path, project: ProjectModel) -> EnginePackage:
    relative = str(project.extensions.get("engine_snapshot") or "").strip()
    if not relative:
        raise RuntimeError("Project has no pinned Engine snapshot.")
    path = root / relative
    engine = EnginePackage.from_dict(_read_json(path))
    if project.engine_id and engine.engine_id != project.engine_id:
        raise RuntimeError("Pinned Engine id does not match project.json.")
    if project.engine_version and engine.version != project.engine_version:
        raise RuntimeError("Pinned Engine version does not match project.json.")
    if (
        project.engine_sha256
        and engine.content_sha256
        and engine.content_sha256 != project.engine_sha256
    ):
        raise RuntimeError("Pinned Engine checksum does not match project.json.")
    return engine


def _resolve_report(project: ProjectModel, explicit: Path | None) -> Path:
    if explicit is not None:
        path = explicit
    else:
        path = Path(str(project.extensions.get("latest_batch_report_path") or ""))
    if not str(path) or not path.is_file():
        raise RuntimeError(f"Current batch report was not found: {path}")
    return path.resolve()


def _recovery_dir(working: Path) -> Path:
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    candidate = working / "recovery_backups" / f"{stamp}_rc31_auto_gate"
    suffix = 1
    while candidate.exists():
        candidate = working / "recovery_backups" / f"{stamp}_rc31_auto_gate_{suffix}"
        suffix += 1
    candidate.mkdir(parents=True)
    return candidate


def _decision_fingerprint(state_path: Path) -> str:
    decisions = _read_json(state_path).get("decisions") or {}
    encoded = json.dumps(
        decisions,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def recover(
    project_root: Path,
    *,
    report_path: Path | None = None,
    expected_restore_count: int | None = None,
    apply: bool = False,
) -> dict[str, Any]:
    root = project_root.resolve(strict=True)
    working = root / "working"
    approved_path = working / "approved_geometry_links.json"
    state_path = working / "review_center_state.json"
    extracted_path = working / "extracted_entities.json"
    if not all(path.is_file() for path in (approved_path, state_path, extracted_path)):
        raise RuntimeError(f"Not a complete LION REBAR project: {root}")

    project, _project_payload = _load_project(root)
    identity = current_identity(project)
    if identity is None or not identity.analysis_run_id:
        raise RuntimeError("Project has no complete current data identity.")
    report = _resolve_report(project, report_path)
    report_payload = _read_json(report)
    identity_reason = mismatch_reason(report_payload, identity, require_run=True)
    if identity_reason:
        raise RuntimeError(f"Batch report identity mismatch: {identity_reason}")

    extracted = _read_json(extracted_path)
    extracted_reason = mismatch_reason(extracted, identity, require_run=False)
    if extracted_reason:
        raise RuntimeError(f"Extracted entities identity mismatch: {extracted_reason}")
    entities = _records(extracted)
    entity_handles = {str(record.handle) for record in entities}
    engine = _load_engine(root, project)

    approved = _read_json(approved_path)
    active = {
        str(link.get("annotation_handle") or ""): dict(link)
        for link in approved.get("links") or []
        if isinstance(link, Mapping) and str(link.get("annotation_handle") or "")
    }
    rows = {
        str(row.get("annotation_handle") or ""): dict(row)
        for row in report_payload.get("rows") or []
        if isinstance(row, Mapping) and str(row.get("annotation_handle") or "")
    }
    current_report_hash = sha256_file(report)

    archived: dict[str, dict[str, Any]] = {}
    for raw in approved.get("history") or []:
        if not isinstance(raw, Mapping):
            continue
        link = dict(raw)
        handle = str(link.get("annotation_handle") or "")
        reason = str(
            link.get("revocation_decision")
            or link.get("revocation_reason")
            or ""
        )
        if handle and handle not in active and is_auto_approval(link) and reason == REVOCATION_REASON:
            archived[handle] = link

    manual_links = {
        handle: link for handle, link in active.items() if is_manual_approval(link)
    }
    context = build_batch_export_context(
        report,
        entities=entities,
        engine=engine,
        approved_links=manual_links,
        frame_decisions=load_frame_decisions(root, identity),
    )
    gate_positions = {
        str(position.annotation_handle): position for position in context.positions
    }

    restorable: dict[str, dict[str, Any]] = {}
    for handle, link in archived.items():
        row = rows.get(handle)
        if row is None or not auto_classification_gate_passes(row):
            continue
        if handle not in gate_positions:
            continue
        if mismatch_reason(link, identity, require_run=True):
            continue
        if str(link.get("source_report_sha256") or "") != current_report_hash:
            continue
        geometry = tuple(str(value) for value in link.get("geometry_handles") or [])
        candidates = tuple(str(value) for value in row.get("candidate_handles") or [])
        if not geometry or geometry != candidates:
            continue
        if any(value not in entity_handles for value in geometry):
            continue
        restorable[handle] = link

    if expected_restore_count is not None and len(restorable) != expected_restore_count:
        raise RuntimeError(
            f"Expected {expected_restore_count} restorable AUTO links, found "
            f"{len(restorable)}; no write performed."
        )
    if set(archived) != set(restorable):
        rejected = sorted(set(archived) - set(restorable))
        raise RuntimeError(
            "Some archived AUTO_LINK_ROW_FAILS_GATE links failed current proof: "
            + ", ".join(rejected)
        )

    missing_gate_handles = set(gate_positions) - set(active)
    if missing_gate_handles != set(restorable):
        raise RuntimeError(
            "Current production gate and archived recovery set differ: "
            f"gate-only={sorted(missing_gate_handles - set(restorable))}, "
            f"archive-only={sorted(set(restorable) - missing_gate_handles)}"
        )

    result: dict[str, Any] = {
        "project": str(root),
        "report": str(report),
        "report_sha256": current_report_hash,
        "apply": bool(apply),
        "links_before": len(active),
        "manual_links_preserved": len(manual_links),
        "gate_passed_positions": len(gate_positions),
        "gate_blocked_positions": len(context.blockers),
        "restorable": len(restorable),
        "restorable_handles": sorted(restorable),
        "expected_links_after": len(active) + len(restorable),
    }
    if not apply:
        return result

    backup_dir = _recovery_dir(working)
    for name in (
        "approved_geometry_links.json",
        "review_center_state.json",
        "review_feedback_events.jsonl",
        "decision_journal.jsonl",
        "final_integrity_report.json",
        "final_integrity_ledger.csv",
        "final_control_freeze.json",
        "project.json",
    ):
        source = working / name
        if source.is_file():
            shutil.copy2(source, backup_dir / name)
    decisions_hash_before = _decision_fingerprint(state_path)

    source_relative = str(project.source_dxf or "")
    source_path = root / source_relative if source_relative else root / "source" / "original.dxf"
    auto_summary = auto_approve_gate_positions(
        root,
        report_path=report,
        entities=entities,
        engine=engine,
        source_dxf_path=source_path,
        data_identity=identity,
    )
    if auto_summary.failed:
        raise RuntimeError(f"AUTO gate recovery failed: {auto_summary.error}")
    if _decision_fingerprint(state_path) != decisions_hash_before:
        raise RuntimeError("Review Center decisions changed during AUTO recovery.")

    after_auto = _read_json(approved_path)
    active_after_auto = {
        str(link.get("annotation_handle") or ""): dict(link)
        for link in after_auto.get("links") or []
        if isinstance(link, Mapping) and str(link.get("annotation_handle") or "")
    }
    expected_after = len(active) + len(restorable)
    if len(active_after_auto) != expected_after or not set(restorable) <= set(active_after_auto):
        raise RuntimeError(
            f"AUTO recovery produced {len(active_after_auto)} links; expected {expected_after}."
        )

    integrity = reconcile_project_integrity(root, report_path=report, apply=True)
    if not (
        integrity.full_specification_complete
        and integrity.approved_after == expected_after
        and integrity.stale_approvals_revoked == 0
        and integrity.unresolved_positions == 0
        and integrity.duplicate_groups_remaining == 0
        and integrity.identity_failures == 0
    ):
        raise RuntimeError(
            "Final Integrity did not reach the expected complete state: "
            + json.dumps(integrity.to_dict(), ensure_ascii=False)
        )
    if _decision_fingerprint(state_path) != decisions_hash_before:
        raise RuntimeError("Review Center decisions changed during Final Integrity.")

    origins = Counter(
        str(link.get("approved_by") or "UNKNOWN")
        for link in _read_json(approved_path).get("links") or []
        if isinstance(link, Mapping)
    )
    result.update(
        {
            "backup": str(backup_dir),
            "links_after": integrity.approved_after,
            "auto_gate_refreshed": auto_summary.newly_approved_count,
            "stale_revoked": integrity.stale_approvals_revoked,
            "unresolved": integrity.unresolved_positions,
            "duplicates_remaining": integrity.duplicate_groups_remaining,
            "identity_failures": integrity.identity_failures,
            "full_specification_complete": integrity.full_specification_complete,
            "approval_origins": dict(sorted(origins.items())),
            "final_report": str(working / "final_integrity_report.json"),
        }
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("project_root", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--expected-restore-count", type=int)
    parser.add_argument("--apply", action="store_true")
    arguments = parser.parse_args()
    print(
        json.dumps(
            recover(
                arguments.project_root,
                report_path=arguments.report,
                expected_restore_count=arguments.expected_restore_count,
                apply=arguments.apply,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
