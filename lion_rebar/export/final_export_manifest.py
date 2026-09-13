"""FINAL_EXPORT_MANIFEST.json — проследимост на завършен production export.

P0-7. След успешен export трябва да може да се отговори: коя версия, кой
engine, кой matcher, кой изходен DXF, кой Final Control, и какви точно са
байтовете на Ledger-а, Excel-а и финалния DXF.

Модулът е чист (без Qt и без ezdxf), за да е тестваем в CI.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "lion-rebar-final-export-manifest/v2"
MANIFEST_NAME = "FINAL_EXPORT_MANIFEST.json"

_ARTIFACT_ROLES = (
    "FINAL_LEDGER",
    "EXCEL",
    "FINAL_DXF",
    "EXPORT_CONFIG",
    "EXCEL_TRACE",
    "DXF_TRACE",
    "FINAL_CONTROL_FREEZE",
)


def sha256_file(path: Path | str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _artifact(role: str, path: Path | str | None) -> dict:
    if path is None:
        return {"role": role, "path": "", "sha256": "", "bytes": 0, "present": False}
    p = Path(path)
    if not p.is_file():
        return {"role": role, "path": str(p), "sha256": "", "bytes": 0, "present": False}
    data = p.read_bytes()
    return {
        "role": role,
        "path": str(p.resolve()),
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
        "present": True,
    }


def build_final_export_manifest(
    *,
    app_version: str,
    engine_id: str,
    engine_version: str,
    engine_sha256: str,
    matcher_sha256: str,
    engine_bundle_sha256: str = "",
    project_id: str,
    source_sha256: str,
    source_generation_id: str,
    analysis_run_id: str,
    final_control_id: str,
    final_control_sha256: str = "",
    final_ledger_path: Path | str | None = None,
    excel_path: Path | str | None = None,
    final_dxf_path: Path | str | None = None,
    project_root: Path | str | None = None,
    export_config_path: Path | str | None = None,
    excel_trace_path: Path | str | None = None,
    dxf_trace_path: Path | str | None = None,
    final_control_path: Path | str | None = None,
    engineer_resolved_without_geometry_count: int = 0,
    non_production_position_count: int = 0,
    ledger_row_count: int = 0,
    excel_row_count: int = 0,
    frame_provenance: list[dict] | None = None,
    unresolved_frame_count: int = 0,
    exported_at: str | None = None,
) -> dict:
    """Сглобява манифеста. Не пише файл — виж ``write_final_export_manifest``."""

    artifacts = [
        _artifact("FINAL_LEDGER", final_ledger_path),
        _artifact("EXCEL", excel_path),
        _artifact("FINAL_DXF", final_dxf_path),
        _artifact("EXPORT_CONFIG", export_config_path),
        _artifact("EXCEL_TRACE", excel_trace_path),
        _artifact("DXF_TRACE", dxf_trace_path),
        _artifact("FINAL_CONTROL_FREEZE", final_control_path),
    ]
    missing = [a["role"] for a in artifacts if not a["present"]]
    final_control_artifact = next(
        artifact for artifact in artifacts
        if artifact["role"] == "FINAL_CONTROL_FREEZE"
    )
    effective_final_control_sha256 = (
        final_control_sha256 or str(final_control_artifact.get("sha256") or "")
    )
    return {
        "schema": SCHEMA,
        "project_root": str(Path(project_root).resolve()) if project_root else "",
        "exported_at": exported_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "build": {
            "app_version": app_version,
            "engine_id": engine_id,
            "engine_version": engine_version,
            # engine_sha256 = хеш на .lionengine ФАЙЛА (това, което стои в
            # acceptance contract-а). engine_content_sha256 = хеш на
            # разпакетираното съдържание. До rc5 се записваше само второто и
            # изглеждаше като разминаване с договора.
            "engine_sha256": engine_bundle_sha256 or engine_sha256,
            "engine_content_sha256": engine_sha256,
            "matcher_sha256": matcher_sha256,
        },
        "identity": {
            "project_id": project_id,
            "source_sha256": source_sha256,
            "source_generation_id": source_generation_id,
            "analysis_run_id": analysis_run_id,
            "final_control_id": final_control_id,
            "final_control_sha256": effective_final_control_sha256,
        },
        "artifacts": artifacts,
        "counts": {
            "final_ledger_rows": int(ledger_row_count),
            "excel_rows": int(excel_row_count),
            "engineer_resolved_without_geometry": int(engineer_resolved_without_geometry_count),
            "non_production_positions": int(non_production_position_count),
            "unresolved_frames": int(unresolved_frame_count),
        },
        "frames": list(frame_provenance or []),
        "completeness": {
            "missing_artifacts": missing,
            "complete": not missing,
            # Windows приемане: Ledger пази ВСИЧКИ позиции (вкл. изключените с
            # доказателство), Excel съдържа само производствените. Равенство
            # 165 == 164 е грешният контракт; верният е:
            #   ledger - non_production == excel
            "ledger_matches_excel": (
                int(ledger_row_count) - int(non_production_position_count)
                == int(excel_row_count)
            ),
            "ledger_excel_equation": (
                f"{int(ledger_row_count)} - {int(non_production_position_count)} "
                f"= {int(excel_row_count)}"
            ),
        },
    }


def write_final_export_manifest(directory: Path | str, manifest: dict) -> Path:
    """Записва манифеста атомарно в ``directory/FINAL_EXPORT_MANIFEST.json``."""

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / MANIFEST_NAME
    tmp = target.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    # Windows/OneDrive могат да заключат целта за кратко (WinError 32).
    # Изчаква се, вместо манифестът да изчезне мълчаливо.
    last: OSError | None = None
    for attempt in range(6):
        try:
            os.replace(tmp, target)
            return target
        except PermissionError as exc:
            last = exc
        except OSError as exc:
            if getattr(exc, "winerror", None) not in (32, 33):
                raise
            last = exc
        time.sleep(0.25 * (attempt + 1))
    tmp.unlink(missing_ok=True)
    raise PermissionError(
        f"Export манифестът не може да бъде записан (файлът е зает): {target}"
    ) from last


def verify_final_export_manifest(manifest_path: Path | str) -> list[str]:
    """Проверява манифеста срещу файловете на диска.

    Връща списък с проблеми; празен списък означава, че export-ът е
    непокътнат. Липсващ или променен артефакт е ГРЕШКА, не предупреждение.
    """

    path = Path(manifest_path)
    problems: list[str] = []
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"MANIFEST_UNREADABLE: {exc}"]
    if manifest.get("schema") != SCHEMA:
        problems.append(f"UNSUPPORTED_SCHEMA: {manifest.get('schema')!r}")
    artifacts = manifest.get("artifacts", [])
    if not isinstance(artifacts, list):
        return [*problems, "ARTIFACTS_INVALID: manifest artifacts не е списък."]
    by_role: dict[str, dict] = {}
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            problems.append("ARTIFACT_INVALID: невалиден запис за артефакт.")
            continue
        role = artifact.get("role", "?")
        normalized_role = str(role).upper()
        if normalized_role in by_role:
            problems.append(f"ARTIFACT_ROLE_DUPLICATE: {normalized_role}")
        else:
            by_role[normalized_role] = artifact
        if not artifact.get("present"):
            problems.append(f"ARTIFACT_MISSING: {role}")
            continue
        target = Path(artifact.get("path", ""))
        if not target.is_file():
            problems.append(f"ARTIFACT_NOT_ON_DISK: {role} -> {target}")
            continue
        if sha256_file(target) != artifact.get("sha256"):
            problems.append(f"ARTIFACT_SHA_MISMATCH: {role}")
    for required_role in _ARTIFACT_ROLES:
        if required_role not in by_role:
            problems.append(f"ARTIFACT_ROLE_MISSING: {required_role}")

    freeze_artifact = by_role.get("FINAL_CONTROL_FREEZE", {})
    freeze_path = Path(str(freeze_artifact.get("path") or ""))
    config_artifact = by_role.get("EXPORT_CONFIG", {})
    if freeze_path.is_file():
        try:
            freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            problems.append(f"FINAL_CONTROL_FREEZE_UNREADABLE: {exc}")
        else:
            if freeze.get("schema") != "lion-rebar-final-control-freeze/v1":
                problems.append("FINAL_CONTROL_FREEZE_UNSUPPORTED_SCHEMA")
            if freeze.get("status") != "VALID":
                problems.append("FINAL_CONTROL_FREEZE_NOT_VALID")
            recorded_sha = str(
                (manifest.get("identity") or {}).get("final_control_sha256") or ""
            )
            if not recorded_sha or recorded_sha != freeze_artifact.get("sha256"):
                problems.append("FINAL_CONTROL_SHA_MISMATCH")
            freeze_config = next(
                (
                    item for item in freeze.get("artifacts", [])
                    if isinstance(item, dict)
                    and str(item.get("role") or "").upper() == "EXPORT_CONFIG"
                ),
                None,
            )
            if freeze_config is None:
                problems.append("FREEZE_EXPORT_CONFIG_MISSING")
            else:
                if freeze_config.get("sha256") != config_artifact.get("sha256"):
                    problems.append("FREEZE_EXPORT_CONFIG_SHA_MISMATCH")
    elif freeze_artifact.get("present"):
        problems.append("FINAL_CONTROL_FREEZE_NOT_ON_DISK")

    # Реалният Excel trace се сравнява със замразеното съответствие.
    # Липсващ, нечетим или непознат trace е production отказ.
    project_root = str(manifest.get("project_root") or "").strip()
    trace_artifact = by_role.get("EXCEL_TRACE", {})
    trace_path = Path(str(trace_artifact.get("path") or ""))
    if not project_root:
        problems.append("PROJECT_ROOT_MISSING: manifest-ът няма project_root.")
    elif not trace_path.is_file():
        problems.append("EXCEL_TRACE_NOT_ON_DISK: липсва реалният Excel trace.")
    else:
        try:
            trace = json.loads(trace_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            problems.append(f"EXCEL_TRACE_UNREADABLE: {exc}")
        else:
            from lion_rebar.export.frozen_export_config import (
                verify_trace_against_freeze,
            )

            problems.extend(verify_trace_against_freeze(project_root, trace))

    counts = manifest.get("counts", {})
    ledger_rows = int(counts.get("final_ledger_rows") or 0)
    excel_rows = int(counts.get("excel_rows") or 0)
    non_production = int(counts.get("non_production_positions") or 0)
    if ledger_rows - non_production != excel_rows:
        problems.append(
            "LEDGER_EXCEL_ROW_MISMATCH: "
            f"{ledger_rows} - {non_production} != {excel_rows} "
            "(Ledger минус непроизводствените трябва да е равен на Excel)")
    if int(counts.get("unresolved_frames") or 0) > 0:
        problems.append(f"UNRESOLVED_FRAMES: {counts.get('unresolved_frames')}")
    return problems
