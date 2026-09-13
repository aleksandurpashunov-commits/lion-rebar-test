from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from lion_rebar.export.auto_approval import auto_approve_gate_positions
from lion_rebar.export.batch_auto_output import build_batch_export_context
from lion_rebar.export.excel_export import load_export_context
from lion_rebar.export.frame_decisions import load_frame_decisions
from lion_rebar.project.approval_origin import is_auto_approval, is_manual_approval
from lion_rebar.project.data_identity import current_identity, mismatch_reason
from lion_rebar.project.integrity import reconcile_project_integrity
from tools.recover_auto_gate_rc31 import (
    _decision_fingerprint,
    _load_engine,
    _load_project,
    _read_json,
    _records,
    _resolve_report,
)

_RECOVERY_PATHS = (
    "approved_geometry_links.json",
    "review_center_state.json",
    "review_feedback_events.jsonl",
    "decision_journal.jsonl",
    "final_integrity_report.json",
    "final_integrity_ledger.csv",
    "final_control_freeze.json",
    "final_control_export_config.json",
    "final_control_sources/batch_report.json",
    "project.json",
)


def _fingerprint(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _backup_dir(working: Path) -> Path:
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    candidate = working / "recovery_backups" / f"{stamp}_rc32_production_dimensions"
    suffix = 1
    while candidate.exists():
        candidate = working / "recovery_backups" / (
            f"{stamp}_rc32_production_dimensions_{suffix}"
        )
        suffix += 1
    candidate.mkdir(parents=True)
    return candidate


def _backup_recovery_files(
    working: Path,
    backup: Path,
) -> tuple[dict[str, bool], set[str]]:
    existed: dict[str, bool] = {}
    for relative in _RECOVERY_PATHS:
        source = working / relative
        existed[relative] = source.is_file()
        if source.is_file():
            target = backup / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    auto_backups = {
        path.name
        for path in working.glob("approved_geometry_links.before_auto_*.json")
        if path.is_file()
    }
    (backup / "recovery_backup_state.json").write_text(
        json.dumps(
            {
                "schema": "lion-rebar-rc32-recovery-backup/v1",
                "existed": existed,
                "auto_backups_before": sorted(auto_backups),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return existed, auto_backups


def _restore_recovery_files(
    working: Path,
    backup: Path,
    *,
    existed: Mapping[str, bool],
    auto_backups_before: set[str],
) -> None:
    for relative in _RECOVERY_PATHS:
        target = working / relative
        backup_file = backup / relative
        if bool(existed.get(relative)):
            if not backup_file.is_file():
                raise RuntimeError(f"Rollback backup is missing: {backup_file}")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup_file, target)
        else:
            target.unlink(missing_ok=True)
        Path(str(target) + ".tmp").unlink(missing_ok=True)
    for path in working.glob("approved_geometry_links.before_auto_*.json"):
        if path.is_file() and path.name not in auto_backups_before:
            path.unlink()


def _refresh_action(
    *,
    missing_count: int,
    expected_refresh_count: int | None,
    apply: bool,
) -> str:
    if (
        expected_refresh_count is not None
        and missing_count != expected_refresh_count
    ):
        raise RuntimeError(
            f"Expected {expected_refresh_count} missing AUTO vectors, found "
            f"{missing_count}; no write performed."
        )
    if not apply:
        return "DRY_RUN"
    if expected_refresh_count is None:
        raise RuntimeError(
            "--expected-refresh-count is mandatory with --apply; no write performed."
        )
    return "NO_OP" if missing_count == 0 else "APPLY"


def _active_links(payload: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(link.get("annotation_handle") or ""): dict(link)
        for link in payload.get("links") or []
        if isinstance(link, Mapping) and str(link.get("annotation_handle") or "")
    }


def _missing_auto_vectors(
    links: Mapping[str, Mapping[str, Any]],
    positions: Mapping[str, Any],
) -> dict[str, Any]:
    missing: dict[str, Any] = {}
    for handle, raw_link in links.items():
        link = dict(raw_link)
        if not is_auto_approval(link):
            continue
        position = positions.get(handle)
        if position is None:
            raise RuntimeError(f"AUTO link is absent from the current gate: {handle}")
        shape_code = str(getattr(position, "shape_code", "") or "").upper()
        if shape_code == "F001":
            continue
        current = link.get("production_segment_lengths_cm")
        if isinstance(current, list) and current:
            continue
        proved = tuple(
            float(value)
            for value in (getattr(position, "segment_lengths_cm", ()) or ())
        )
        if not proved:
            raise RuntimeError(
                f"Current gate has no production segment vector for {handle}/{shape_code}."
            )
        missing[handle] = position
    return missing


def refresh(
    project_root: Path,
    *,
    report_path: Path | None = None,
    expected_refresh_count: int | None = None,
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
    reason = mismatch_reason(report_payload, identity, require_run=True)
    if reason:
        raise RuntimeError(f"Batch report identity mismatch: {reason}")
    extracted = _read_json(extracted_path)
    reason = mismatch_reason(extracted, identity, require_run=False)
    if reason:
        raise RuntimeError(f"Extracted entities identity mismatch: {reason}")

    entities = _records(extracted)
    engine = _load_engine(root, project)
    approved = _read_json(approved_path)
    links = _active_links(approved)
    manual_links = {
        handle: link for handle, link in links.items() if is_manual_approval(link)
    }
    manual_fingerprint = _fingerprint(manual_links)
    decisions_fingerprint = _decision_fingerprint(state_path)

    context = build_batch_export_context(
        report,
        entities=entities,
        engine=engine,
        approved_links=manual_links,
        frame_decisions=load_frame_decisions(root, identity),
    )
    positions = {
        str(position.annotation_handle): position for position in context.positions
    }
    missing = _missing_auto_vectors(links, positions)
    action = _refresh_action(
        missing_count=len(missing),
        expected_refresh_count=expected_refresh_count,
        apply=apply,
    )

    result: dict[str, Any] = {
        "project": str(root),
        "report": str(report),
        "apply": bool(apply),
        "links_before": len(links),
        "manual_links_preserved": len(manual_links),
        "gate_positions": len(positions),
        "gate_blockers": len(context.blockers),
        "missing_auto_vectors": len(missing),
        "missing_handles": sorted(missing),
        "no_op": action == "NO_OP",
    }
    if action != "APPLY":
        return result

    backup = _backup_dir(working)
    existed, auto_backups_before = _backup_recovery_files(working, backup)

    try:
        source_relative = str(project.source_dxf or "")
        source_path = (
            root / source_relative
            if source_relative
            else root / "source" / "original.dxf"
        )
        auto_summary = auto_approve_gate_positions(
            root,
            report_path=report,
            entities=entities,
            engine=engine,
            source_dxf_path=source_path,
            data_identity=identity,
        )
        if auto_summary.failed:
            raise RuntimeError(f"AUTO dimension refresh failed: {auto_summary.error}")
        if _decision_fingerprint(state_path) != decisions_fingerprint:
            raise RuntimeError("Review Center decisions changed during AUTO refresh.")

        refreshed = _active_links(_read_json(approved_path))
        if len(refreshed) != len(links):
            raise RuntimeError(
                f"AUTO refresh changed the active position count: {len(links)} -> "
                f"{len(refreshed)}."
            )
        refreshed_manual = {
            handle: link
            for handle, link in refreshed.items()
            if is_manual_approval(link)
        }
        if _fingerprint(refreshed_manual) != manual_fingerprint:
            raise RuntimeError("USER/OWNER approved links changed during AUTO refresh.")
        for handle, position in missing.items():
            link = refreshed.get(handle, {})
            expected_segments = [
                float(value)
                for value in (getattr(position, "segment_lengths_cm", ()) or ())
            ]
            expected_turns = [
                float(value)
                for value in (getattr(position, "turn_angles_deg", ()) or ())
            ]
            if link.get("production_segment_lengths_cm") != expected_segments:
                raise RuntimeError(
                    f"Persisted segment vector mismatch after refresh: {handle}"
                )
            if link.get("production_turn_angles_deg") != expected_turns:
                raise RuntimeError(
                    f"Persisted turn vector mismatch after refresh: {handle}"
                )

        integrity = reconcile_project_integrity(root, report_path=report, apply=True)
        if not (
            integrity.full_specification_complete
            and integrity.approved_after == len(links)
            and integrity.unresolved_positions == 0
            and integrity.duplicate_groups_remaining == 0
            and integrity.identity_failures == 0
            and not integrity.export_preflight_error
        ):
            raise RuntimeError(
                "Final Control did not reach an export-ready state: "
                + json.dumps(integrity.to_dict(), ensure_ascii=False)
            )
        if _decision_fingerprint(state_path) != decisions_fingerprint:
            raise RuntimeError("Review Center decisions changed during Final Control.")
        final_links = _active_links(_read_json(approved_path))
        final_manual = {
            handle: link
            for handle, link in final_links.items()
            if is_manual_approval(link)
        }
        if _fingerprint(final_manual) != manual_fingerprint:
            raise RuntimeError("Final Control changed USER/OWNER approved links.")
        _frames, export_positions = load_export_context(project, entities, engine)
        if len(export_positions) != len(links):
            raise RuntimeError(
                f"Export context returned {len(export_positions)}/{len(links)} positions."
            )
    except Exception as exc:
        try:
            _restore_recovery_files(
                working,
                backup,
                existed=existed,
                auto_backups_before=auto_backups_before,
            )
        except Exception as rollback_exc:
            raise RuntimeError(
                f"Recovery failed ({exc}); automatic rollback also failed "
                f"({rollback_exc}). Manual backup: {backup}"
            ) from exc
        raise RuntimeError(
            f"Recovery failed and live artifacts were restored from {backup}: {exc}"
        ) from exc

    result.update(
        {
            "backup": str(backup),
            "links_after": len(final_links),
            "auto_links_refreshed": auto_summary.newly_approved_count,
            "final_control_complete": integrity.full_specification_complete,
            "export_positions": len(export_positions),
            "freeze_manifest": integrity.freeze_manifest_path,
        }
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("project_root", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--expected-refresh-count", type=int)
    parser.add_argument("--apply", action="store_true")
    arguments = parser.parse_args()
    print(
        json.dumps(
            refresh(
                arguments.project_root,
                report_path=arguments.report,
                expected_refresh_count=arguments.expected_refresh_count,
                apply=arguments.apply,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
