r"""Базова линия: целият път върху всеки пробен проект, всеки в свой процес.

Пуска ``full_path.py`` поотделно за всяка папка — в отделен процес, за да не
носи един проект кеширано състояние в следващия — и записва сбит JSON с
числата на всеки етап.

Базовата линия не е истина за чертежа. Тя казва какво дава кодът на даден
комит, за да се види след промяна кои числа са мръднали и къде; всяко
мръднало число се проверява поотделно.

Пускане от release/:
    .venv\Scripts\python.exe .claude\skills\lion-rebar-full-path\baseline.py "I:\Моят диск\Тест-желязо"
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
RELEASE_ROOT = HERE.parents[2]


def _git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(RELEASE_ROOT), *args],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def _app_version() -> str:
    text = (RELEASE_ROOT / "lion_rebar" / "config" / "constants.py").read_text(encoding="utf-8")
    match = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', text)
    return match.group(1) if match else ""


def _compact(summary: dict[str, Any]) -> dict[str, Any]:
    project = summary.get("project") or {}
    imported = summary.get("import") or {}
    analysis = summary.get("analysis") or {}
    auto = summary.get("auto_approval") or {}
    gate = summary.get("gate") or {}
    final = summary.get("final_control") or {}
    export = summary.get("export") or {}
    safety = summary.get("safety") or {}
    return {
        "status": summary.get("status"),
        "stop_code": summary.get("stop_code") or "",
        "engine": f"{project.get('engine_version', '')} {str(project.get('engine_sha256') or '')[:12]}".strip(),
        "entities": imported.get("entities"),
        "analysis_entities": imported.get("analysis_entities"),
        "annotations": analysis.get("total_annotations", imported.get("annotations")),
        "actionable": analysis.get("actionable"),
        "batch_errors": analysis.get("errors"),
        "auto_gate_passed": auto.get("gate_passed_count"),
        "auto_new": auto.get("newly_approved_count"),
        "auto_manual_kept": auto.get("kept_manual_count"),
        "auto_blocked": auto.get("blocked_count"),
        "auto_blocked_reasons": auto.get("blocked_reason_counts"),
        "approved": gate.get("approved"),
        "gate_actionable": gate.get("actionable"),
        "gate_blocked": gate.get("blocked"),
        "gate_blocker_reasons": gate.get("blocker_reasons"),
        "gate_error": gate.get("error"),
        "final_complete": final.get("full_specification_complete"),
        "final_actionable": final.get("actionable_positions"),
        "final_approved": final.get("approved_after"),
        "final_non_production": final.get("non_production_positions"),
        "final_unresolved": final.get("unresolved_positions"),
        "final_error_codes": final.get("error_issue_codes"),
        "final_links_by_origin": final.get("links_by_origin"),
        "export_status": export.get("status"),
        "excel_positions": export.get("excel_positions"),
        "dxf_markers": export.get("dxf_markers"),
        "dialog_defaults_match_frozen_config": export.get("dialog_defaults_match_frozen_config"),
        "carried_review_decisions": summary.get("carried_review_decisions"),
        "source_unchanged": safety.get("source_unchanged"),
        "cloud_unchanged": safety.get("cloud_unchanged"),
        "seconds": (summary.get("seconds") or {}).get("total"),
    }


def _row(name: str, record: dict[str, Any]) -> str:
    status = str(record.get("status")) + (f" {record['stop_code']}" if record.get("stop_code") else "")
    return (
        f"{name:<6} {status:<32} надписи={record.get('annotations')} "
        f"одобрени={record.get('approved')}/{record.get('gate_actionable')} "
        f"финален={record.get('final_approved')}+{record.get('final_non_production')}"
        f"/{record.get('final_actionable')} експорт={record.get('export_status')} "
        f"{record.get('seconds')} s"
    )


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Базова линия на целия път за папка с пробни проекти.")
    parser.add_argument("projects_parent", type=Path, help="папка, в която всяка подпапка е проект")
    parser.add_argument("--only", nargs="*", help="само тези проекти (по име на папка)")
    parser.add_argument("--output", type=Path, help="по подразбиране baselines/<дата>_<комит>.json до скрипта")
    parser.add_argument("--keep-copies", action="store_true", help="не изтривай копията след всеки проект")
    args = parser.parse_args(argv)

    data_root = os.environ.get("LION_REBAR_DATA_ROOT")
    if not data_root:
        print("LION_REBAR_DATA_ROOT не е зададен — отказ (виж .claude/settings.json).", file=sys.stderr)
        return 2
    parent = args.projects_parent.resolve()
    projects = sorted(
        (path for path in parent.iterdir() if (path / "working" / "project.json").is_file()),
        key=lambda path: path.name.casefold(),
    )
    if args.only:
        wanted = {name.casefold() for name in args.only}
        projects = [path for path in projects if path.name.casefold() in wanted]
    if not projects:
        print(f"Няма проекти в {parent}", file=sys.stderr)
        return 1

    head = _git("rev-parse", "--short", "HEAD")
    dirty = bool(_git("status", "--porcelain", "--", "lion_rebar"))
    started = datetime.now()
    output = args.output or HERE / "baselines" / f"{started:%Y-%m-%d}_{head or 'nogit'}.json"
    work_root = Path(data_root).expanduser() / "full_path_runs" / f"baseline_{started:%Y%m%d_%H%M%S}"
    work_root.mkdir(parents=True, exist_ok=True)

    records: dict[str, dict[str, Any]] = {}
    for project in projects:
        summary_file = work_root / f"{project.name}.json"
        command = [
            sys.executable, str(HERE / "full_path.py"), str(project),
            "--work-root", str(work_root), "--json", str(summary_file),
        ]
        if not args.keep_copies:
            command.append("--discard-copy")
        completed = subprocess.run(
            command, cwd=RELEASE_ROOT, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        try:
            record = _compact(json.loads(summary_file.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            record = {"status": "NO_SUMMARY", "stderr_tail": completed.stderr[-2000:]}
        record["exit_code"] = completed.returncode
        records[project.name] = record
        print(_row(project.name, record), flush=True)

    payload = {
        "schema": "lion-rebar-full-path-baseline/v1",
        "generated_at": started.astimezone().isoformat(timespec="seconds"),
        "git_head": head + ("+промени в lion_rebar" if dirty else ""),
        "app_version": _app_version(),
        "projects_parent": str(parent),
        "measure": (
            "approved = len(build_batch_export_context(...).positions) само с ръчните "
            "връзки, както в auto_approval.py; final_* = reconcile_project_integrity; "
            "подробности: full_path.py"
        ),
        "summaries_folder": str(work_root),
        "projects": records,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"БАЗОВА ЛИНИЯ {output}")
    bad = [name for name, record in records.items() if record.get("status") not in {"OK", "STOPPED"}]
    if bad:
        print(f"НЕ СА МИНАЛИ ЧИСТО: {', '.join(bad)}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
