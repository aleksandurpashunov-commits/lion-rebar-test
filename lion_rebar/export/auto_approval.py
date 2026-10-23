"""Автоматично одобрение на позициите, преминали пакетната производствена порта.

Библиотечен порт на собственическия инструмент ``lion_auto_approve``
(ъпдейт 0.1.44.10-fix1), за да се изпълнява като част от пакетния анализ:
всичко, което пакетната порта (``build_batch_export_context``) допуска до
автоматичния Excel, получава одобрена връзка с подпис ``AUTO_BATCH_GATE``.
Наистина ръчните случаи (конфликти, слабо отделени кандидати, разминаващи се
дължини, липсващи кандидати) остават за Review Center.

Правила за безопасност:

* портата НЕ се разхлабва — използва се същата ``build_batch_export_context``;
* съществуващо ръчно одобрение никога не се презаписва;
* всяко автоматично одобрение носи пълния одитен запис (auto_gate: статус,
  disposition, evidence band, отчет);
* повторно изпълнение е идемпотентно (само опреснява собствените AUTO записи);
* преди запис се прави резервно копие на approved_geometry_links.json.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Mapping, Sequence

from lion_rebar.config.constants import APP_VERSION, RECOGNITION_MATCHER_SHA256
from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.engine.models import EnginePackage
from lion_rebar.project.data_identity import DataIdentity, IDENTITY_KEY
from lion_rebar.project.approval_origin import (
    AUTO_APPROVED_BY,
    is_auto_approval,
    is_manual_approval,
)
from lion_rebar.utils.files import canonical_json_bytes, sha256_bytes, sha256_file
from lion_rebar.export.batch_auto_output import (
    BatchAutoOutputError,
    build_batch_export_context,
)
from lion_rebar.export.auto_gate import (
    auto_classification_gate_passes,
    auto_gate_eligibility_mode,
)

AUTO_STATUS = "ОДОБРЕНО АВТОМАТИЧНО (ПАКЕТНА ПОРТА)"
SCHEMA = "lion-rebar-approved-geometry-links/v1"

# Полета, копирани дословно от реда на пакетния отчет в одобрената връзка.
_LINK_FIELDS = (
    "annotation_text",
    "candidate_basis",
    "candidate_notes",
    "canonical_segment_lengths_cm",
    "canonical_turn_angles_deg",
    "contextual_rule_code",
    "contextual_rule_note",
    "declared_length_cm",
    "diameter_layer_match",
    "diameter_mm",
    "distance_raw",
    "effective_geometry_length_cm",
    "geometry_length_raw",
    # Позиция без DXF геометрия носи откъде идва (правата форма от
    # начертаната ведомост) — без това Финалният контрол я обезсилва.
    "geometry_provenance",
    "master_shape_code",
    "master_shape_name",
    "numeric_difference",
    "raw_numeric_difference",
    "segment_count",
)


@dataclass(frozen=True)
class AutoApprovalSummary:
    actionable_count: int
    gate_passed_count: int
    newly_approved_count: int
    kept_manual_count: int
    blocked_count: int
    revoked_stale_count: int = 0
    blocked_reason_counts: dict[str, int] = field(default_factory=dict)
    blocked_handles: tuple[str, ...] = ()
    approved_links_path: str = ""
    error: str = ""

    @property
    def failed(self) -> bool:
        return bool(self.error)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _matcher_identity(matcher_path: Path) -> tuple[str, str]:
    """Return a stable matcher identity in source and frozen runtimes."""

    matcher_path = Path(matcher_path)
    if matcher_path.is_file():
        return sha256_file(matcher_path), "SOURCE_FILE"
    # PyInstaller does not expose shape_recognition.py as a normal source file.
    # The release constant is locked by tests against the source SHA.
    return RECOGNITION_MATCHER_SHA256, "RELEASE_IDENTITY"


def _build_link(position, row: Mapping, *, report_name: str) -> dict:
    # build_batch_export_context already proved these production dimensions
    # against the pinned MASTER Engine. Persist that validated output on the
    # approval link: the raw/canonical DXF traversal can be empty (for example
    # F003 derived from dimensions) and cannot be reconstructed safely later.
    production_segments = [
        float(value)
        for value in (getattr(position, "segment_lengths_cm", ()) or ())
    ]
    production_turns = [
        float(value)
        for value in (getattr(position, "turn_angles_deg", ()) or ())
    ]
    link = {
        "annotation_handle": position.annotation_handle,
        "approval_status": AUTO_STATUS,
        "approved_at": _now(),
        "approved_by": AUTO_APPROVED_BY,
        "automatic_outputs": {
            "position": False,
            "shape": False,
            "spacing": False,
            "specification_row": False,
            "zone": False,
        },
        "candidate_confidence_before_approval": str(
            row.get("candidate_confidence") or ""
        ),
        "geometry_handles": [str(value) for value in position.geometry_handles],
        "geometry_layer": str(row.get("candidate_layer") or ""),
        # build_batch_export_context already validated count/diameter through
        # the same gate as production_length_cm/segments below (positive int,
        # positive float, count_multiplier_problems checked). Persisting them
        # here closes the gap found in the Phase 1 fallback audit: without
        # this, export re-derives count/diameter from raw annotation text for
        # every AUTO_BATCH_GATE position instead of reading the proven value.
        "production_count": int(position.count),
        "production_diameter_mm": float(position.diameter_mm),
        "production_length_cm": float(position.production_length_cm),
        "production_length_source": str(position.production_length_source or ""),
        "production_segment_lengths_cm": production_segments,
        "production_turn_angles_deg": production_turns,
        "auto_gate": {
            "policy_version": "AUTO_GATE_V2",
            "passed": auto_classification_gate_passes(row),
            "eligibility_mode": auto_gate_eligibility_mode(row),
            "batch_status": str(row.get("batch_status") or ""),
            "matcher_disposition": str(row.get("matcher_disposition") or ""),
            "matcher_evidence_band": str(row.get("matcher_evidence_band") or ""),
            "production_dimensions_persisted": bool(production_segments),
            "report": report_name,
        },
    }
    for name in _LINK_FIELDS:
        if name in row:
            link[name] = row[name]
    link["master_shape_code"] = position.shape_code
    link["master_shape_name"] = position.shape_name
    return link


def _link_provenance(
    *,
    report_path: Path,
    source_dxf_path: Path | None,
    engine: EnginePackage,
    data_identity: DataIdentity | Mapping | None = None,
) -> dict:
    """Пълна идентичност на решението: кой отчет, кой DXF, кой Engine, кой код.

    Липсващ hash при последваща проверка се третира като несъвпадащ hash.
    """

    matcher_path = (
        Path(__file__).resolve().parents[1] / "dxf" / "shape_recognition.py"
    )
    matcher_sha256, matcher_identity_source = _matcher_identity(matcher_path)
    engine_payload = engine.to_dict() if hasattr(engine, "to_dict") else {}
    rules_payload = engine_payload.get("rules", []) if isinstance(engine_payload, dict) else []
    identity_payload = (
        data_identity.to_dict()
        if isinstance(data_identity, DataIdentity)
        else dict(data_identity or {})
    )
    return {
        "source_report_sha256": sha256_file(report_path) if report_path.is_file() else "",
        "source_dxf_sha256": (
            sha256_file(source_dxf_path)
            if source_dxf_path is not None and Path(source_dxf_path).is_file()
            else ""
        ),
        "engine_version": str(getattr(engine, "version", "") or ""),
        "engine_hash": (
            sha256_bytes(canonical_json_bytes(engine_payload)) if engine_payload else ""
        ),
        "application_version": APP_VERSION,
        "matcher_sha256": matcher_sha256,
        "matcher_identity_source": matcher_identity_source,
        "rules_sha256": sha256_bytes(canonical_json_bytes(rules_payload)),
        "generated_at": _now(),
        IDENTITY_KEY: identity_payload,
        "project_id": str(identity_payload.get("project_id") or ""),
        "source_generation_id": str(identity_payload.get("source_generation_id") or ""),
        "analysis_run_id": str(identity_payload.get("analysis_run_id") or ""),
    }


def _provenance_matches(link: Mapping, current: Mapping) -> bool:
    """Compare the locked recognition provenance plus optional data identity.

    The historical recovery.4 contract remains valid for projects that do not
    yet carry project/source-generation identity.  Once a current payload does
    carry that identity, project and source generation become mandatory and
    must match.  ``analysis_run_id`` is required on both sides but is not
    compared for equality: a fresh batch run is expected to have a new id while
    still belonging to the same immutable source generation.
    """

    for key in (
        "source_dxf_sha256",
        "engine_hash",
        "application_version",
        "matcher_sha256",
        "rules_sha256",
    ):
        recorded = str(link.get(key) or "")
        expected = str(current.get(key) or "")
        if not recorded or not expected or recorded != expected:
            return False

    identity_expected = any(
        str(current.get(key) or "")
        for key in ("project_id", "source_generation_id", "analysis_run_id")
    )
    if not identity_expected:
        return True

    for key in ("project_id", "source_generation_id"):
        recorded = str(link.get(key) or "")
        expected = str(current.get(key) or "")
        if not recorded or not expected or recorded != expected:
            return False
    return bool(str(link.get("analysis_run_id") or "")) and bool(
        str(current.get("analysis_run_id") or "")
    )


def auto_approve_gate_positions(
    project_root: Path,
    *,
    report_path: Path,
    entities: Sequence[DxfEntityRecord],
    engine: EnginePackage,
    source_dxf_path: Path | None = None,
    data_identity: DataIdentity | Mapping | None = None,
) -> AutoApprovalSummary:
    """Одобрява автоматично точно позициите, минаващи пакетната порта."""

    project_root = Path(project_root)
    report_path = Path(report_path)

    approved_path = project_root / "working" / "approved_geometry_links.json"
    payload = None
    if approved_path.is_file():
        try:
            payload = json.loads(approved_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            payload = None
    if not isinstance(payload, dict) or not isinstance(payload.get("links"), list):
        payload = {"schema": SCHEMA, "links": [], "history": []}

    existing = {
        str(link.get("annotation_handle") or ""): link
        for link in payload.get("links", [])
        if isinstance(link, dict)
    }
    # Only true USER/OWNER links are supplied as project approvals to the
    # production gate.  Previous AUTO links must not supply stale dimensions or
    # convert a freshly recomputed row back to APPROVED_IN_PROJECT.
    manual_links = {
        handle: link for handle, link in existing.items() if is_manual_approval(link)
    }

    try:
        from lion_rebar.export.frame_decisions import load_frame_decisions

        identity_for_frames = (
            data_identity
            if isinstance(data_identity, DataIdentity)
            else DataIdentity(
                project_id=str((data_identity or {}).get("project_id") or ""),
                source_sha256=str((data_identity or {}).get("source_sha256") or ""),
                source_generation_id=str(
                    (data_identity or {}).get("source_generation_id") or ""
                ),
                analysis_run_id=str((data_identity or {}).get("analysis_run_id") or ""),
            )
        )
        context = build_batch_export_context(
            report_path,
            entities=list(entities),
            engine=engine,
            approved_links=manual_links,
            frame_decisions=load_frame_decisions(
                project_root, identity_for_frames,
            ),
        )
    except BatchAutoOutputError as exc:
        return AutoApprovalSummary(
            actionable_count=0,
            gate_passed_count=0,
            newly_approved_count=0,
            kept_manual_count=0,
            blocked_count=0,
            error=str(exc),
        )

    try:
        payload_rows = json.loads(report_path.read_text(encoding="utf-8-sig"))
        rows_by_handle = {
            str(row.get("annotation_handle") or ""): row
            for row in payload_rows.get("rows", [])
            if isinstance(row, dict)
        }
    except (OSError, json.JSONDecodeError):
        payload_rows = {}
        rows_by_handle = {}

    report_identity = (
        dict(payload_rows.get(IDENTITY_KEY) or {})
        if isinstance(payload_rows, dict)
        else {}
    )
    supplied_identity = (
        data_identity.to_dict()
        if isinstance(data_identity, DataIdentity)
        else dict(data_identity or {})
    )
    identity_payload = supplied_identity or report_identity
    if supplied_identity and report_identity and supplied_identity != report_identity:
        return AutoApprovalSummary(
            actionable_count=int(context.actionable_count),
            gate_passed_count=0,
            newly_approved_count=0,
            kept_manual_count=0,
            blocked_count=int(context.actionable_count),
            error="Пакетният отчет не принадлежи на текущия analysis run.",
        )
    if identity_payload:
        mixed_rows = [
            str(row.get("annotation_handle") or "?")
            for row in payload_rows.get("rows", [])
            if isinstance(row, dict)
            and dict(row.get(IDENTITY_KEY) or {}) != identity_payload
        ]
        if mixed_rows:
            return AutoApprovalSummary(
                actionable_count=int(context.actionable_count),
                gate_passed_count=0,
                newly_approved_count=0,
                kept_manual_count=0,
                blocked_count=int(context.actionable_count),
                blocked_handles=tuple(mixed_rows),
                error=(
                    "Пакетният отчет съдържа редове от друг project/source/run: "
                    + ", ".join(mixed_rows[:8])
                    + (" ..." if len(mixed_rows) > 8 else "")
                ),
            )

    provenance = _link_provenance(
        report_path=report_path,
        source_dxf_path=source_dxf_path,
        engine=engine,
        data_identity=identity_payload,
    )
    gate_passed_handles = {
        str(position.annotation_handle) for position in context.positions
    }
    blocked_handles_set = {
        str(blocker.annotation_handle) for blocker in context.blockers
    }

    new_links: list[dict] = []
    kept_manual = 0
    for position in context.positions:
        handle = position.annotation_handle
        current = existing.get(handle)
        if current is not None and is_manual_approval(current):
            # Ръчното одобрение на собственика винаги има предимство.
            kept_manual += 1
            continue
        link = _build_link(
            position,
            rows_by_handle.get(handle, {}),
            report_name=report_path.name,
        )
        link.update(provenance)
        new_links.append(link)

    # Оттегляне на остарели AUTO връзки. Ръчни (USER) връзки не се пипат.
    revoked_links: list[dict] = []
    retained_existing: dict[str, dict] = {}
    for handle, link in existing.items():
        is_auto = is_auto_approval(link)
        if not is_auto:
            retained_existing[handle] = link
            continue
        reason = ""
        if handle in blocked_handles_set:
            reason = "AUTO_STALE_NOW_BLOCKED"
        elif handle not in gate_passed_handles:
            reason = "AUTO_STALE_NOT_IN_CURRENT_GATE"
        elif not _provenance_matches(link, provenance):
            reason = "AUTO_STALE_PROVENANCE_MISMATCH"
        if reason and handle not in {
            str(item.get("annotation_handle") or "") for item in new_links
        }:
            archived = dict(link)
            archived["revoked_at"] = _now()
            archived["revoked_reason"] = reason
            archived["revoked_by_report"] = report_path.name
            revoked_links.append(archived)
            continue
        retained_existing[handle] = link

    blocked_reasons: dict[str, int] = {}
    for blocker in context.blockers:
        for code in blocker.reason_codes:
            blocked_reasons[str(code)] = blocked_reasons.get(str(code), 0) + 1

    if new_links or revoked_links:
        merged = dict(retained_existing)
        for link in new_links:
            merged[str(link["annotation_handle"])] = link
        history = [
            dict(item)
            for item in payload.get("history", [])
            if isinstance(item, dict)
        ]
        history.extend(revoked_links)
        payload["schema"] = SCHEMA
        payload["links"] = list(merged.values())
        payload["history"] = history
        payload["updated_at"] = _now()
        if identity_payload:
            payload[IDENTITY_KEY] = dict(identity_payload)
        approved_path.parent.mkdir(parents=True, exist_ok=True)
        if approved_path.is_file():
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup = approved_path.with_name(
                f"approved_geometry_links.before_auto_{stamp}.json"
            )
            backup.write_bytes(approved_path.read_bytes())
        temporary = approved_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(approved_path)

    return AutoApprovalSummary(
        actionable_count=int(context.actionable_count),
        gate_passed_count=len(context.positions),
        newly_approved_count=len(new_links),
        revoked_stale_count=len(revoked_links),
        kept_manual_count=kept_manual,
        blocked_count=len(context.blockers),
        blocked_reason_counts=blocked_reasons,
        blocked_handles=tuple(
            str(blocker.annotation_handle) for blocker in context.blockers
        ),
        approved_links_path=str(approved_path),
    )
