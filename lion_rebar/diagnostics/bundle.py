from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Iterable

from lion_rebar.config.constants import APP_NAME, APP_VERSION
from lion_rebar.utils.files import safe_filename


DIAGNOSTIC_SCHEMA = "lion-rebar-diagnostic-bundle/v1"
_MAX_LOG_FILES = 5
_MAX_LOG_TAIL_BYTES = 256 * 1024

_WORKING_JSON_FILES = (
    "review_center_state.json",
    "review_problems.json",
    "approved_geometry_links.json",
    "manual_corrections.json",
    "manual_shape_drafts.json",
    "manual_shape_training_examples.json",
    "detected_positions.json",
    "final_integrity_report.json",
    "extracted_entities.json",
    "layer_classification.json",
)
_FEEDBACK_EVENT_FILES = (
    "review_feedback_events.jsonl",
    "feedback_events.jsonl",
)
_REPORT_EXACT_FILENAMES = {
    "README_BG.txt",
    "progress.json",
    "progress_final.json",
}
_REPORT_SUFFIXES = {
    ".json",
    ".csv",
    ".html",
    ".txt",
}
_SENSITIVE_KEY_PARTS = (
    "password",
    "passwd",
    "secret",
    "api_key",
    "apikey",
    "access_key",
    "private_key",
    "credential",
    "authorization",
    "cookie",
    "session",
    "token",
)
_CREDENTIAL_PATTERN = re.compile(
    r"(?i)\b(password|passwd|secret|api[_-]?key|access[_-]?key|"
    r"authorization|cookie|session|token)\b(\s*[:=]\s*)([^\s,;]+)"
)
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_WINDOWS_ABSOLUTE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])(?:[A-Za-z]:[\\/]|\\\\)[^\r\n\t\"'<>]+"
)
_POSIX_ABSOLUTE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_:])/(?:[^/\s\"'<>]+/)+[^,\s;\"'<>]+"
)


class DiagnosticBundleError(RuntimeError):
    """The requested diagnostic bundle could not be created safely."""


@dataclass(frozen=True)
class _Evidence:
    archive_path: str
    payload: bytes
    category: str
    original_relative_path: str | None = None


def create_diagnostic_bundle(
    project_root: Path,
    output_path: Path | None = None,
    include_source_dxf: bool = True,
    *,
    latest_report_path: Path | None = None,
) -> Path:
    """Create one self-contained, privacy-conscious project diagnostic ZIP.

    Only explicitly allowlisted project evidence is included. JSON and log
    content is sanitized before packaging; no process environment is read or
    exported. The source DXF is included byte-for-byte when requested because
    exact geometry is required to reproduce recognition problems. When batch
    reports are stored outside the project (for example in a cloud review
    folder), ``latest_report_path`` may point to the report JSON or its folder.
    """

    root = Path(project_root).expanduser().resolve()
    if not root.is_dir():
        raise DiagnosticBundleError(f"Проектната папка не съществува: {project_root}")
    working = root / "working"
    project_path = working / "project.json"
    project_data = _read_json_object(project_path)
    declared_root = str(project_data.get("root") or "")
    redaction_roots = tuple(
        value
        for value in (str(root), declared_root)
        if value and value not in {".", "/"}
    )

    if output_path is None:
        project_name = str(project_data.get("name") or root.name)
        token = safe_filename(project_name, "LION_REBAR_PROJECT")
        timestamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
        destination = (
            root
            / "output"
            / "05_DIAGNOSTICS"
            / f"{token}_AI_DIAGNOSTIC_{timestamp}.zip"
        )
    else:
        destination = Path(output_path).expanduser()
        if destination.suffix.lower() != ".zip":
            destination = destination.with_suffix(".zip")
        destination = destination.resolve()

    evidence: list[_Evidence] = []
    missing: list[str] = []

    sanitized_project = _sanitize_json(project_data, root, redaction_roots)
    if not isinstance(sanitized_project, dict):
        sanitized_project = {}
    sanitized_project["root"] = "."
    evidence.append(
        _json_evidence(
            "project/project.json",
            sanitized_project,
            category="project_metadata",
            original_relative_path="working/project.json",
        )
    )

    if include_source_dxf:
        source = _project_file(root, Path("source") / "original.dxf")
        if source is None:
            missing.append("source/original.dxf")
        else:
            evidence.append(
                _binary_file_evidence(
                    source,
                    "source/original.dxf",
                    category="source_dxf",
                    original_relative_path="source/original.dxf",
                )
            )

    engine_path = _pinned_engine_path(root, project_data)
    if engine_path is None:
        missing.append("pinned_engine_snapshot")
    else:
        evidence.append(
            _sanitized_json_file_evidence(
                engine_path,
                f"master/{_safe_archive_filename(engine_path.name, 'engine.json')}",
                category="engine_snapshot",
                original_relative_path=_relative_project_path(root, engine_path),
                project_root=root,
                redaction_roots=redaction_roots,
            )
        )

    for filename in _WORKING_JSON_FILES:
        path = _project_file(root, Path("working") / filename)
        if path is None:
            continue
        evidence.append(
            _sanitized_json_file_evidence(
                path,
                f"working/{filename}",
                category=_working_category(filename),
                original_relative_path=f"working/{filename}",
                project_root=root,
                redaction_roots=redaction_roots,
            )
        )

    candidate_dir = root / "working" / "shape_candidates"
    if candidate_dir.is_dir() and not candidate_dir.is_symlink():
        for candidate_file in sorted(candidate_dir.iterdir(), key=lambda value: value.name):
            if candidate_file.suffix.lower() != ".json":
                continue
            path = _project_file(
                root,
                Path("working") / "shape_candidates" / candidate_file.name,
            )
            if path is None:
                continue
            archive_name = (
                "working/shape_candidates/"
                + _safe_archive_filename(path.name, "shape_candidate.json")
            )
            evidence.append(
                _sanitized_json_file_evidence(
                    path,
                    archive_name,
                    category="shape_candidate",
                    original_relative_path=(
                        f"working/shape_candidates/{candidate_file.name}"
                    ),
                    project_root=root,
                    redaction_roots=redaction_roots,
                )
            )

    feedback_path = _first_existing_project_file(
        root,
        (Path("working") / value for value in _FEEDBACK_EVENT_FILES),
    )
    if feedback_path is not None:
        evidence.append(
            _sanitized_jsonl_file_evidence(
                feedback_path,
                "feedback/events.jsonl",
                category="feedback_events",
                original_relative_path=_relative_project_path(root, feedback_path),
                project_root=root,
                redaction_roots=redaction_roots,
            )
        )

    decision_journal_path = _project_file(
        root, Path("working") / "decision_journal.jsonl"
    )
    if decision_journal_path is not None:
        evidence.append(
            _sanitized_jsonl_file_evidence(
                decision_journal_path,
                "feedback/decision_journal.jsonl",
                category="decision_journal",
                original_relative_path="working/decision_journal.jsonl",
                project_root=root,
                redaction_roots=redaction_roots,
            )
        )

    latest_report, report_is_inside_project = _resolve_latest_batch_report(
        root,
        latest_report_path,
        expected_project_name=str(project_data.get("name") or ""),
    )
    if latest_report is None:
        missing.append("latest_batch_report")
    else:
        report_dir = latest_report.parent
        report_files = _allowlisted_report_files(
            root,
            report_dir,
            allow_external=not report_is_inside_project,
        )
        for report_file in report_files:
            archive_name = (
                "reports/latest/"
                + _safe_archive_filename(report_file.name, "report_artifact")
            )
            if report_file.suffix.lower() == ".json":
                evidence.append(
                    _sanitized_json_file_evidence(
                        report_file,
                        archive_name,
                        category="latest_batch_report",
                        original_relative_path=(
                            _relative_project_path(root, report_file)
                            if report_is_inside_project
                            else None
                        ),
                        project_root=root,
                        redaction_roots=redaction_roots,
                    )
                )
            else:
                evidence.append(
                    _sanitized_text_file_evidence(
                        report_file,
                        archive_name,
                        category="latest_batch_report",
                        original_relative_path=(
                            _relative_project_path(root, report_file)
                            if report_is_inside_project
                            else None
                        ),
                        project_root=root,
                        redaction_roots=redaction_roots,
                    )
                )

    evidence.extend(_log_tail_evidence(root, redaction_roots))
    evidence.append(
        _Evidence(
            archive_path="README_BG.txt",
            payload=_readme_text(
                include_source_dxf=include_source_dxf,
                has_source=any(item.category == "source_dxf" for item in evidence),
            ).encode("utf-8"),
            category="diagnostic_readme",
        )
    )

    _ensure_unique_archive_names(evidence)
    _validate_destination(root, destination, evidence)
    manifest = _build_manifest(
        root=root,
        project_data=sanitized_project,
        evidence=evidence,
        missing=missing,
        include_source_dxf=include_source_dxf,
    )
    manifest_evidence = _json_evidence(
        "manifest.json",
        manifest,
        category="manifest",
    )
    all_entries = [*evidence, manifest_evidence]

    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.stem}.",
        suffix=".tmp.zip",
        dir=str(destination.parent),
    )
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(
            temporary,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
        ) as archive:
            for item in sorted(all_entries, key=lambda value: value.archive_path):
                _validate_archive_path(item.archive_path)
                archive.writestr(item.archive_path, item.payload)
        _verify_created_archive(temporary)
        os.replace(temporary, destination)
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        if isinstance(exc, DiagnosticBundleError):
            raise
        raise DiagnosticBundleError(f"Диагностичният ZIP не можа да бъде създаден: {exc}") from exc
    return destination


def _read_json_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise DiagnosticBundleError(f"Липсва project.json: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DiagnosticBundleError(f"Невалиден project.json: {exc}") from exc
    if not isinstance(value, dict):
        raise DiagnosticBundleError("project.json трябва да съдържа JSON object.")
    return value


def _project_file(root: Path, relative_path: Path) -> Path | None:
    candidate = root / relative_path
    try:
        if candidate.is_symlink() or not candidate.is_file():
            return None
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError):
        return None
    return resolved


def _first_existing_project_file(
    root: Path,
    relative_paths: Iterable[Path],
) -> Path | None:
    for relative_path in relative_paths:
        result = _project_file(root, relative_path)
        if result is not None:
            return result
    return None


def _pinned_engine_path(root: Path, project_data: dict[str, Any]) -> Path | None:
    extensions = project_data.get("extensions")
    snapshot = extensions.get("engine_snapshot") if isinstance(extensions, dict) else None
    if isinstance(snapshot, str) and snapshot.strip():
        relative = Path(snapshot)
        if not relative.is_absolute() and relative.parts[:1] == ("master",):
            candidate = _project_file(root, relative)
            if candidate is not None and candidate.suffix.lower() == ".json":
                return candidate

    master = root / "master"
    try:
        candidates = [
            path
            for path in master.glob("engine_*.json")
            if _project_file(root, path.relative_to(root)) is not None
        ]
    except (OSError, ValueError):
        return None
    if not candidates:
        return None
    return max(candidates, key=lambda value: (value.stat().st_mtime_ns, value.name))


def _resolve_latest_batch_report(
    root: Path,
    explicit: Path | None,
    *,
    expected_project_name: str,
) -> tuple[Path | None, bool]:
    if explicit is not None:
        report = _validate_explicit_report_path(
            explicit,
            expected_project_name=expected_project_name,
        )
        try:
            report.relative_to(root)
        except ValueError:
            return report, False
        return report, True
    return (
        _latest_batch_report(
            root,
            expected_project_name=expected_project_name,
        ),
        True,
    )


def _validate_explicit_report_path(
    value: Path,
    *,
    expected_project_name: str,
) -> Path:
    candidate = Path(value).expanduser()
    try:
        if candidate.is_symlink():
            raise DiagnosticBundleError("Избраният batch отчет е символна връзка.")
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise DiagnosticBundleError(f"Batch отчетът не съществува: {value}") from exc
    if resolved.is_dir():
        report_candidates = [
            path
            for path in resolved.glob("*_geometry_batch.json")
            if path.is_file() and not path.is_symlink()
        ]
        if not report_candidates:
            raise DiagnosticBundleError(
                "В избраната папка няма *_geometry_batch.json."
            )
        resolved = max(
            report_candidates,
            key=lambda path: (path.stat().st_mtime_ns, path.name),
        ).resolve(strict=True)
    if not resolved.is_file() or not resolved.name.endswith("_geometry_batch.json"):
        raise DiagnosticBundleError(
            "Избери batch JSON отчет или неговата BATCH_REVIEW папка."
        )
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DiagnosticBundleError(f"Невалиден batch JSON отчет: {exc}") from exc
    schema = payload.get("schema") if isinstance(payload, dict) else None
    if not str(schema or "").startswith("lion-rebar-geometry-batch-report/"):
        raise DiagnosticBundleError("Избраният JSON не е LION REBAR batch отчет.")
    report_project_name = str(payload.get("project_name") or "").strip()
    expected_name = str(expected_project_name or "").strip()
    if report_project_name and expected_name and report_project_name != expected_name:
        raise DiagnosticBundleError(
            f"Batch отчетът е за проект „{report_project_name}“, а диагностичният "
            f"пакет е за „{expected_name}“."
        )
    return resolved


def _latest_batch_report(
    root: Path,
    *,
    expected_project_name: str,
) -> Path | None:
    output = root / "output"
    if not output.is_dir() or output.is_symlink():
        return None
    candidates: list[Path] = []
    try:
        for path in output.rglob("*_geometry_batch.json"):
            if "_BATCH_REVIEW_" not in path.parent.name:
                continue
            valid = _project_file(root, path.relative_to(root))
            if valid is not None:
                candidates.append(valid)
    except (OSError, ValueError):
        return None
    if not candidates:
        return None
    candidates.sort(
        key=lambda value: (value.stat().st_mtime_ns, str(value)),
        reverse=True,
    )
    expected_name = str(expected_project_name or "").strip()
    for candidate in candidates:
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        schema = str(payload.get("schema") or "")
        if not schema.startswith("lion-rebar-geometry-batch-report/"):
            continue
        report_name = str(payload.get("project_name") or "").strip()
        if not report_name or not expected_name or report_name == expected_name:
            return candidate
    return None


def _allowlisted_report_files(
    root: Path,
    report_dir: Path,
    *,
    allow_external: bool,
) -> list[Path]:
    result: list[Path] = []
    resolved_report_dir = report_dir.resolve()
    try:
        children = tuple(report_dir.iterdir())
    except OSError:
        return result
    for child in children:
        if child.name in _REPORT_EXACT_FILENAMES:
            allowed = True
        else:
            allowed = (
                "_geometry_batch" in child.name
                and child.suffix.lower() in _REPORT_SUFFIXES
            )
        if not allowed:
            continue
        if child.is_symlink() or not child.is_file():
            continue
        if allow_external:
            try:
                valid = child.resolve(strict=True)
                valid.relative_to(resolved_report_dir)
            except (OSError, ValueError):
                valid = None
        else:
            try:
                valid = _project_file(root, child.relative_to(root))
            except ValueError:
                valid = None
        if valid is not None:
            result.append(valid)
    return sorted(result, key=lambda value: value.name)


def _log_tail_evidence(
    root: Path,
    redaction_roots: tuple[str, ...],
) -> list[_Evidence]:
    logs_dir = root / "logs"
    if not logs_dir.is_dir() or logs_dir.is_symlink():
        return []
    candidates: list[Path] = []
    try:
        for path in logs_dir.iterdir():
            if path.suffix.lower() not in {".log", ".txt"}:
                continue
            try:
                valid = _project_file(root, path.relative_to(root))
            except ValueError:
                valid = None
            if valid is not None:
                candidates.append(valid)
    except OSError:
        return []
    candidates.sort(
        key=lambda value: (value.stat().st_mtime_ns, value.name),
        reverse=True,
    )
    result: list[_Evidence] = []
    for index, path in enumerate(candidates[:_MAX_LOG_FILES], start=1):
        payload = _read_tail(path, _MAX_LOG_TAIL_BYTES)
        text = payload.decode("utf-8", errors="replace")
        sanitized = _sanitize_text(text, root, redaction_roots)
        name = _safe_archive_filename(path.stem, f"log_{index}")
        result.append(
            _Evidence(
                archive_path=f"logs/{index:02d}_{name}.tail.txt",
                payload=sanitized.encode("utf-8"),
                category="log_tail",
                original_relative_path=_relative_project_path(root, path),
            )
        )
    return result


def _read_tail(path: Path, maximum_bytes: int) -> bytes:
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        handle.seek(max(0, size - maximum_bytes))
        payload = handle.read(maximum_bytes)
    if size > maximum_bytes:
        newline = payload.find(b"\n")
        if newline >= 0:
            payload = payload[newline + 1 :]
        payload = b"[... earlier log content omitted ...]\n" + payload
    return payload


def _binary_file_evidence(
    path: Path,
    archive_path: str,
    *,
    category: str,
    original_relative_path: str | None,
) -> _Evidence:
    return _Evidence(
        archive_path=_validate_archive_path(archive_path),
        payload=path.read_bytes(),
        category=category,
        original_relative_path=original_relative_path,
    )


def _sanitized_json_file_evidence(
    path: Path,
    archive_path: str,
    *,
    category: str,
    original_relative_path: str | None,
    project_root: Path,
    redaction_roots: tuple[str, ...],
) -> _Evidence:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise DiagnosticBundleError(f"Не може да се прочете {path.name}: {exc}") from exc
    try:
        data = json.loads(text)
        sanitized = _sanitize_json(data, project_root, redaction_roots)
    except json.JSONDecodeError as exc:
        sanitized = {
            "_diagnostic_status": "INVALID_JSON",
            "_parse_error": str(exc),
            "_sanitized_raw_text": _sanitize_text(
                text,
                project_root,
                redaction_roots,
            ),
        }
    return _json_evidence(
        archive_path,
        sanitized,
        category=category,
        original_relative_path=original_relative_path,
    )


def _sanitized_jsonl_file_evidence(
    path: Path,
    archive_path: str,
    *,
    category: str,
    original_relative_path: str,
    project_root: Path,
    redaction_roots: tuple[str, ...],
) -> _Evidence:
    output_lines: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        raise DiagnosticBundleError(f"Не може да се прочете {path.name}: {exc}") from exc
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            value = {
                "_diagnostic_status": "INVALID_JSONL_RECORD",
                "_line_number": line_number,
                "_parse_error": str(exc),
                "_sanitized_raw_text": _sanitize_text(
                    line,
                    project_root,
                    redaction_roots,
                ),
            }
        sanitized = _sanitize_json(value, project_root, redaction_roots)
        output_lines.append(
            json.dumps(sanitized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )
    payload = (("\n".join(output_lines) + "\n") if output_lines else "").encode("utf-8")
    return _Evidence(
        archive_path=_validate_archive_path(archive_path),
        payload=payload,
        category=category,
        original_relative_path=original_relative_path,
    )


def _sanitized_text_file_evidence(
    path: Path,
    archive_path: str,
    *,
    category: str,
    original_relative_path: str | None,
    project_root: Path,
    redaction_roots: tuple[str, ...],
) -> _Evidence:
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as exc:
        raise DiagnosticBundleError(f"Не може да се прочете {path.name}: {exc}") from exc
    return _Evidence(
        archive_path=_validate_archive_path(archive_path),
        payload=_sanitize_text(text, project_root, redaction_roots).encode("utf-8"),
        category=category,
        original_relative_path=original_relative_path,
    )


def _json_evidence(
    archive_path: str,
    data: Any,
    *,
    category: str,
    original_relative_path: str | None = None,
) -> _Evidence:
    return _Evidence(
        archive_path=_validate_archive_path(archive_path),
        payload=(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8"),
        category=category,
        original_relative_path=original_relative_path,
    )


def _sanitize_json(
    value: Any,
    project_root: Path,
    redaction_roots: tuple[str, ...],
) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for raw_key, child in value.items():
            key = str(raw_key)
            if _is_sensitive_key(key):
                result[key] = "<REDACTED>"
            else:
                result[key] = _sanitize_json(child, project_root, redaction_roots)
        return result
    if isinstance(value, list):
        return [_sanitize_json(child, project_root, redaction_roots) for child in value]
    if isinstance(value, tuple):
        return [_sanitize_json(child, project_root, redaction_roots) for child in value]
    if isinstance(value, str):
        if _is_standalone_absolute_path(value):
            return "<ABSOLUTE_PATH_REDACTED>"
        return _sanitize_text(value, project_root, redaction_roots)
    return value


def _sanitize_text(
    text: str,
    project_root: Path,
    redaction_roots: tuple[str, ...],
) -> str:
    result = str(text)
    roots = sorted(set(redaction_roots), key=len, reverse=True)
    for root_text in roots:
        normalized_variants = {
            root_text,
            root_text.replace("\\", "/"),
            root_text.replace("/", "\\"),
        }
        for variant in sorted(normalized_variants, key=len, reverse=True):
            if variant:
                result = result.replace(variant, "<PROJECT_ROOT>")
    result = _CREDENTIAL_PATTERN.sub(
        lambda match: f"{match.group(1)}{match.group(2)}<REDACTED>",
        result,
    )
    result = _BEARER_PATTERN.sub("Bearer <REDACTED>", result)
    result = _WINDOWS_ABSOLUTE_PATTERN.sub("<ABSOLUTE_PATH_REDACTED>", result)
    result = _POSIX_ABSOLUTE_PATTERN.sub("<ABSOLUTE_PATH_REDACTED>", result)
    return result


def _is_sensitive_key(key: str) -> bool:
    normalized = key.strip().lower().replace("-", "_")
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def _is_standalone_absolute_path(value: str) -> bool:
    candidate = value.strip()
    if not candidate or "\n" in candidate or "\r" in candidate:
        return False
    return PurePosixPath(candidate).is_absolute() or PureWindowsPath(candidate).is_absolute()


def _working_category(filename: str) -> str:
    categories = {
        "review_center_state.json": "review_state",
        "review_problems.json": "review_problems",
        "approved_geometry_links.json": "approved_links",
        "manual_corrections.json": "manual_corrections",
        "manual_shape_drafts.json": "manual_shape_drafts",
        "manual_shape_training_examples.json": "manual_training",
        "detected_positions.json": "detected_positions",
        "final_integrity_report.json": "integrity_report",
        "extracted_entities.json": "extracted_entities",
        "layer_classification.json": "layer_classification",
    }
    return categories.get(filename, "working_evidence")


def _safe_archive_filename(value: str, fallback: str) -> str:
    safe = safe_filename(value, fallback)
    return safe.replace("\\", "_").replace("/", "_")


def _relative_project_path(root: Path, path: Path) -> str:
    try:
        relative = path.resolve(strict=True).relative_to(root)
    except (OSError, ValueError) as exc:
        raise DiagnosticBundleError(f"Файл извън проекта: {path}") from exc
    return _validate_archive_path(relative.as_posix())


def _validate_archive_path(value: str) -> str:
    normalized = str(value).replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or normalized.startswith("/")
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or ":" in path.parts[0]
    ):
        raise DiagnosticBundleError(f"Небезопасен път в диагностичния ZIP: {value!r}")
    return path.as_posix()


def _ensure_unique_archive_names(evidence: Iterable[_Evidence]) -> None:
    names: set[str] = set()
    for item in evidence:
        name = _validate_archive_path(item.archive_path)
        folded = name.casefold()
        if folded in names:
            raise DiagnosticBundleError(f"Дублиран файл в диагностичния ZIP: {name}")
        names.add(folded)


def _validate_destination(
    root: Path,
    destination: Path,
    evidence: Iterable[_Evidence],
) -> None:
    if destination.exists() and destination.is_dir():
        raise DiagnosticBundleError("Избраният диагностичен ZIP е папка.")
    try:
        relative = destination.resolve().relative_to(root)
    except ValueError:
        return
    source_paths = {
        item.original_relative_path
        for item in evidence
        if item.original_relative_path is not None
    }
    if relative.as_posix() in source_paths:
        raise DiagnosticBundleError("Диагностичният ZIP не може да презапише проектен файл.")


def _build_manifest(
    *,
    root: Path,
    project_data: dict[str, Any],
    evidence: list[_Evidence],
    missing: list[str],
    include_source_dxf: bool,
) -> dict[str, Any]:
    files = []
    for item in sorted(evidence, key=lambda value: value.archive_path):
        files.append(
            {
                "path": _validate_archive_path(item.archive_path),
                "category": item.category,
                "size_bytes": len(item.payload),
                "sha256": hashlib.sha256(item.payload).hexdigest(),
                "original_project_path": item.original_relative_path,
            }
        )
    return {
        "schema": DIAGNOSTIC_SCHEMA,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "application": {
            "name": APP_NAME,
            "version": APP_VERSION,
        },
        "runtime": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "operating_system": platform.system(),
            "os_release": platform.release(),
            "machine": platform.machine(),
            "architecture_bits": platform.architecture()[0],
            "frozen_application": bool(getattr(sys, "frozen", False)),
        },
        "project": {
            "name": project_data.get("name"),
            "schema": project_data.get("schema"),
            "project_version": project_data.get("version"),
            "engine_id": project_data.get("engine_id"),
            "engine_version": project_data.get("engine_version"),
            "engine_sha256": project_data.get("engine_sha256"),
            "source_sha256": (
                project_data.get("extensions", {}).get("source_sha256")
                if isinstance(project_data.get("extensions"), dict)
                else None
            ),
        },
        "privacy": {
            "environment_variables_included": False,
            "absolute_paths_in_manifest": False,
            "json_and_logs_sanitized": True,
            "source_dxf_exact_binary": bool(include_source_dxf),
        },
        "missing_optional_evidence": sorted(set(missing)),
        "files": files,
    }


def _verify_created_archive(path: Path) -> None:
    try:
        with zipfile.ZipFile(path, "r") as archive:
            bad = archive.testzip()
            if bad is not None:
                raise DiagnosticBundleError(f"Повреден файл в диагностичния ZIP: {bad}")
            for name in archive.namelist():
                _validate_archive_path(name)
    except zipfile.BadZipFile as exc:
        raise DiagnosticBundleError("Създаденият диагностичен ZIP е повреден.") from exc


def _readme_text(*, include_source_dxf: bool, has_source: bool) -> str:
    source_note = (
        "Оригиналният DXF е включен без промени за точна геометрична проверка."
        if include_source_dxf and has_source
        else "Оригиналният DXF не е включен в този пакет."
    )
    return (
        "LION REBAR — AI диагностичен пакет\n"
        "===================================\n\n"
        "Този ZIP е достатъчен за възпроизвеждане и анализ на текущия проектен казус.\n"
        f"{source_note}\n"
        "JSON файловете и логовете са почистени от абсолютни пътища и чувствителни полета.\n"
        "manifest.json съдържа версията, средата и SHA256 на всеки включен файл.\n\n"
        "Изпрати целия ZIP без да добавяш отделни снимки, освен ако визуалният резултат\n"
        "в програмата се различава от данните в пакета.\n"
    )
