from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from lion_rebar.review_center_model import is_position_key_legend_reference


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise RuntimeError(f"Expected a JSON object: {path}")
    return value


def _links_by_annotation(payload: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(link.get("annotation_handle") or ""): dict(link)
        for link in (payload.get("links") or [])
        if isinstance(link, dict) and str(link.get("annotation_handle") or "")
    }


def _identity_key(payload: Mapping[str, Any]) -> tuple[str, str, str, str]:
    identity = payload.get("data_identity")
    if not isinstance(identity, Mapping):
        identity = payload
    return tuple(
        str(identity.get(key) or "")
        for key in (
            "project_id",
            "source_sha256",
            "source_generation_id",
            "analysis_run_id",
        )
    )


def repair(project_root: Path, *, expected_restore_count: int | None = None) -> dict[str, Any]:
    root = project_root.resolve(strict=True)
    working = root / "working"
    approved_path = working / "approved_geometry_links.json"
    report_path = working / "final_integrity_report.json"
    if not working.is_dir() or not approved_path.is_file() or not report_path.is_file():
        raise RuntimeError(f"Not a complete LION REBAR project: {root}")

    backups = sorted(
        (working / "integrity_backups").glob("*/approved_geometry_links.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not backups:
        raise RuntimeError("No integrity backup with approved links was found.")

    current = _read_json(approved_path)
    backup_path = backups[0]
    backup = _read_json(backup_path)
    if _identity_key(current) != _identity_key(backup):
        raise RuntimeError("Current links and integrity backup have different data identity.")

    current_by_handle = _links_by_annotation(current)
    backup_by_handle = _links_by_annotation(backup)
    restorable = {
        handle: link
        for handle, link in backup_by_handle.items()
        if handle not in current_by_handle and is_position_key_legend_reference(link)
    }
    if expected_restore_count is not None and len(restorable) != expected_restore_count:
        raise RuntimeError(
            f"Expected {expected_restore_count} legend links, found {len(restorable)}; no write performed."
        )
    if not restorable:
        return {
            "project": str(root),
            "restored": 0,
            "links_before": len(current_by_handle),
            "links_after": len(current_by_handle),
            "backup": None,
        }

    timestamp = datetime.now().astimezone()
    recovery_dir = working / "recovery_backups" / (
        timestamp.strftime("%Y%m%d_%H%M%S") + "_rc30_shared_legend"
    )
    recovery_dir.mkdir(parents=True, exist_ok=False)
    for name in (
        "approved_geometry_links.json",
        "review_center_state.json",
        "final_integrity_report.json",
        "final_integrity_ledger.csv",
    ):
        source = working / name
        if source.is_file():
            shutil.copy2(source, recovery_dir / name)

    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for backup_link in backup.get("links") or []:
        if not isinstance(backup_link, dict):
            continue
        handle = str(backup_link.get("annotation_handle") or "")
        if not handle or handle in seen:
            continue
        if handle in current_by_handle:
            merged.append(current_by_handle[handle])
            seen.add(handle)
        elif handle in restorable:
            restored = dict(restorable[handle])
            restored["recovered_at"] = timestamp.isoformat(timespec="seconds")
            restored["recovered_by"] = "RC30_SHARED_LEGEND_RECOVERY"
            restored["recovery_source"] = str(backup_path)
            merged.append(restored)
            seen.add(handle)
    for handle, link in current_by_handle.items():
        if handle not in seen:
            merged.append(link)
            seen.add(handle)

    current["links"] = merged
    current["updated_at"] = timestamp.isoformat(timespec="seconds")
    current["last_recovery"] = {
        "code": "RC30_SHARED_LEGEND_RECOVERY",
        "at": timestamp.isoformat(timespec="seconds"),
        "source": str(backup_path),
        "restored_annotation_handles": sorted(restorable),
        "preserved_current_links": len(current_by_handle),
    }
    temporary = approved_path.with_suffix(".json.rc30.tmp")
    temporary.write_text(
        json.dumps(current, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(approved_path)
    return {
        "project": str(root),
        "restored": len(restorable),
        "restored_handles": sorted(restorable),
        "links_before": len(current_by_handle),
        "links_after": len(merged),
        "backup": str(recovery_dir),
        "source_backup": str(backup_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("project_root", type=Path)
    parser.add_argument("--expected-restore-count", type=int)
    arguments = parser.parse_args()
    print(json.dumps(
        repair(
            arguments.project_root,
            expected_restore_count=arguments.expected_restore_count,
        ),
        ensure_ascii=False,
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
