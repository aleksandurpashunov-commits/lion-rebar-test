"""Read-only suggestions from delivered human corrections.

Only active, complete ENGINEER_RESOLVED v2 CORRECTION/OVERRIDE events are
eligible.  They need either DXF geometry or an explicit geometryless reason
with an operator note.  Looking up a suggestion never mutates the project;
prefill values require an explicit acceptance flag.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
from pathlib import Path
import sqlite3
from typing import Any

from lion_rebar.engineer_decision import ACTION_CORRECTION, ACTION_OVERRIDE
from lion_rebar.project.data_identity import DataIdentity
from lion_rebar.session.decision_contract import (
    DECISION_CONTRACT_SCHEMA,
    DECISION_EVENT_TYPES,
    JOURNAL_NAME,
    OUTBOX_NAME,
)
from lion_rebar.session.learning_pack import _decision_rows

READY = "READY"
CONFLICT = "CONFLICT"
NO_MATCH = "NO_MATCH"
INELIGIBLE = "INELIGIBLE"
_ELIGIBLE_ACTIONS = frozenset({ACTION_CORRECTION, ACTION_OVERRIDE})
_EVENT_TYPE = "ENGINEER_RESOLVED"
_GEOMETRYLESS_REASONS = frozenset({
    "NO_GEOMETRY",
    "WRONG_GEOMETRY",
    "WRONG_OBJECT",
    "PARTIAL_GEOMETRY",
    "DUPLICATE_GEOMETRY",
})
_CONTEXT_FIELDS = (
    "engine_version", "annotation_text", "count", "diameter_mm",
    "declared_length_cm", "candidate_shape_code", "candidate_segments_cm",
    "candidate_turn_angles_deg", "problem_codes", "geometry_class",
    "geometry_provenance", "candidate_layer", "batch_status",
    "matcher_disposition", "matcher_evidence_band", "candidate_confidence",
    "contextual_rule_code", "canonical_signature", "numeric_difference",
    "alternative_candidate_count", "frame_category_code",
    "data_quality_flags",
)


class ExplicitSuggestionAcceptanceRequired(RuntimeError):
    pass


class SuggestionNotAcceptable(RuntimeError):
    pass


@dataclass(frozen=True)
class StrictDecisionContext:
    fields: tuple[tuple[str, str], ...]
    digest: str

    def to_dict(self) -> dict[str, str]:
        return dict(self.fields)


@dataclass(frozen=True)
class ProductionResolution:
    shape_code: str
    shape_name: str
    shape_revision: int
    count: int
    diameter_mm: float
    production_length_cm: float
    segment_lengths_cm: tuple[float, ...]
    turn_angles_deg: tuple[float, ...]
    action_class: str
    reason_code: str
    reason: str
    geometry_mode: str = "DXF_HANDLES"
    historical_note_present: bool = False

    @property
    def signature(self) -> str:
        return _digest({
            "shape_code": self.shape_code,
            "shape_revision": self.shape_revision,
            "count": self.count,
            "diameter_mm": _number(self.diameter_mm),
            "production_length_cm": _number(self.production_length_cm),
            "segments": [_number(v) for v in self.segment_lengths_cm],
            "turns": [_number(v) for v in self.turn_angles_deg],
            "action_class": self.action_class,
            "reason_code": self.reason_code,
            "geometry_mode": self.geometry_mode,
        })

    def to_dict(self) -> dict[str, Any]:
        return {
            "shape_code": self.shape_code,
            "shape_name": self.shape_name,
            "shape_revision": self.shape_revision,
            "count": self.count,
            "diameter_mm": self.diameter_mm,
            "production_length_cm": self.production_length_cm,
            "segment_lengths_cm": list(self.segment_lengths_cm),
            "turn_angles_deg": list(self.turn_angles_deg),
            "action_class": self.action_class,
            "reason_code": self.reason_code,
            "reason": self.reason,
            "geometry_mode": self.geometry_mode,
            "historical_note_present": self.historical_note_present,
        }

    def prefill(self) -> dict[str, Any]:
        # Geometry handles/layer and historical note text never cross cases.
        values = {
            key: value for key, value in self.to_dict().items()
            if key not in {"action_class", "historical_note_present"}
        }
        values["requires_fresh_note"] = self.geometry_mode == "NO_DXF_GEOMETRY"
        return values


@dataclass(frozen=True)
class SuggestionProvenance:
    stable_event_id: str
    session_id: str
    occurred_at: str
    project_name: str
    source_sha256: str
    drawing_family_id: str
    source_generation_id: str
    annotation_handle: str
    engine_version: str
    action_class: str
    reason_code: str
    delivery_sources: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        result = dict(self.__dict__)
        result["delivery_sources"] = list(self.delivery_sources)
        return result


@dataclass(frozen=True)
class _Evidence:
    context: StrictDecisionContext
    resolution: ProductionResolution
    provenance: SuggestionProvenance
    independent_instance_id: str


@dataclass(frozen=True)
class SuggestionAlternative:
    resolution: ProductionResolution
    provenance: tuple[SuggestionProvenance, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "resolution": self.resolution.to_dict(),
            "evidence_count": len(self.provenance),
            "provenance": [item.to_dict() for item in self.provenance],
        }


@dataclass(frozen=True)
class DecisionSuggestion:
    status: str
    explanation: str
    context: StrictDecisionContext | None = None
    proposal: ProductionResolution | None = None
    provenance: tuple[SuggestionProvenance, ...] = ()
    alternatives: tuple[SuggestionAlternative, ...] = ()
    missing_context: tuple[str, ...] = ()

    @property
    def can_accept(self) -> bool:
        return self.status == READY and self.proposal is not None

    def accepted_prefill(
        self, *, explicitly_accepted: bool = False,
    ) -> dict[str, Any]:
        if not explicitly_accepted:
            raise ExplicitSuggestionAcceptanceRequired(
                "Предложението попълва полета само след изрично приемане."
            )
        if not self.can_accept or self.proposal is None:
            raise SuggestionNotAcceptable(
                "Липсва еднозначно предложение за приемане."
            )
        return self.proposal.prefill()

    def acceptance_audit(self) -> dict[str, Any]:
        if not self.can_accept or self.context is None or self.proposal is None:
            raise SuggestionNotAcceptable("Липсва еднозначно предложение.")
        return {
            "schema": "lion-rebar-decision-suggestion-acceptance/v1",
            "context_signature": self.context.digest,
            "resolution_signature": self.proposal.signature,
            "source_event_ids": [p.stable_event_id for p in self.provenance],
            "explicit_human_acceptance_required": True,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "explanation": self.explanation,
            "can_accept": self.can_accept,
            "explicit_acceptance_required": True,
            "context_signature": self.context.digest if self.context else None,
            "context": self.context.to_dict() if self.context else {},
            "proposal": self.proposal.to_dict() if self.proposal else None,
            "evidence_count": len(self.provenance),
            "provenance": [p.to_dict() for p in self.provenance],
            "alternatives": [a.to_dict() for a in self.alternatives],
            "missing_context": list(self.missing_context),
        }


@dataclass(frozen=True)
class SuggestionSourceStats:
    delivered_event_count: int = 0
    duplicate_event_count: int = 0
    eligible_evidence_count: int = 0
    rejected_event_count: int = 0
    lifecycle_tombstone_count: int = 0
    warnings: tuple[str, ...] = ()


@dataclass
class _DeliveredEvent:
    event: dict[str, Any]
    delivery_sources: set[str] = field(default_factory=set)


class DecisionSuggestionIndex:
    """Strict lookup index; it contains no method that applies a decision."""

    def __init__(
        self, evidence: Iterable[_Evidence] = (),
        *, source_stats: SuggestionSourceStats | None = None,
    ) -> None:
        buckets: dict[str, list[_Evidence]] = defaultdict(list)
        for item in evidence:
            buckets[item.context.digest].append(item)
        self._buckets = {key: tuple(value) for key, value in buckets.items()}
        self.source_stats = source_stats or SuggestionSourceStats()

    @classmethod
    def from_sources(
        cls, *, session_db_path: Path | str | None = None,
        project_root: Path | str | None = None,
    ) -> "DecisionSuggestionIndex":
        gathered: list[_DeliveredEvent] = []
        warnings: list[str] = []
        if session_db_path is not None:
            try:
                gathered.extend(_completed_session_events(Path(session_db_path)))
            except (OSError, sqlite3.Error, ValueError) as exc:
                warnings.append(f"SESSION_DB_UNAVAILABLE: {exc}")
        if project_root is not None:
            try:
                gathered.extend(_delivered_project_events(Path(project_root)))
            except (OSError, ValueError) as exc:
                warnings.append(f"PROJECT_JOURNAL_UNAVAILABLE: {exc}")
        merged, duplicate_count = _merge_events(gathered)
        evidence, rejected = _eligible_evidence(merged.values())
        delivered_count = sum(
            1
            for item in merged.values()
            if item.delivery_sources
            & {"SESSION_DB_ENDED", "PROJECT_OUTBOX_DELIVERED"}
        )
        lifecycle_tombstone_count = sum(
            1
            for item in merged.values()
            if "PROJECT_JOURNAL_PENDING_TOMBSTONE" in item.delivery_sources
            and not (
                item.delivery_sources
                & {"SESSION_DB_ENDED", "PROJECT_OUTBOX_DELIVERED"}
            )
        )
        return cls(evidence, source_stats=SuggestionSourceStats(
            delivered_event_count=delivered_count,
            duplicate_event_count=duplicate_count,
            eligible_evidence_count=len(evidence),
            rejected_event_count=rejected,
            lifecycle_tombstone_count=lifecycle_tombstone_count,
            warnings=tuple(warnings),
        ))

    def lookup(
        self, row: Mapping[str, Any], *,
        identity: DataIdentity | Mapping[str, Any], engine_version: str,
    ) -> DecisionSuggestion:
        context, missing = strict_context_from_review_row(
            row, identity=identity, engine_version=engine_version
        )
        if context is None:
            return DecisionSuggestion(
                INELIGIBLE,
                "Няма безопасно предложение: липсва " + ", ".join(missing) + ".",
                missing_context=missing,
            )
        current_handle = _upper(row.get("annotation_handle"))
        current_generation = _identity_value(identity, "source_generation_id")
        matches = tuple(
            item for item in self._buckets.get(context.digest, ())
            if not (
                current_handle
                and _upper(item.provenance.annotation_handle) == current_handle
                and item.provenance.source_generation_id == current_generation
            )
        )
        if not matches:
            return DecisionSuggestion(
                NO_MATCH,
                "Няма доставено предишно решение със същия строг контекст.",
                context=context,
            )

        # Same source+handle is one independent case.  Equal duplicates collapse,
        # but differing answers remain visible and block acceptance.
        unique: dict[tuple[str, str], _Evidence] = {}
        for item in sorted(
            matches,
            key=lambda v: (v.provenance.occurred_at, v.provenance.stable_event_id),
        ):
            unique[(item.independent_instance_id, item.resolution.signature)] = item
        grouped: dict[str, list[_Evidence]] = defaultdict(list)
        for item in unique.values():
            grouped[item.resolution.signature].append(item)
        alternatives = tuple(
            SuggestionAlternative(
                items[0].resolution,
                tuple(e.provenance for e in sorted(
                    items,
                    key=lambda v: (
                        v.provenance.occurred_at, v.provenance.stable_event_id
                    ),
                    reverse=True,
                )),
            )
            for _, items in sorted(grouped.items())
        )
        if len(alternatives) != 1:
            return DecisionSuggestion(
                CONFLICT,
                f"Открити са {len(alternatives)} различни решения. "
                "Нищо не се попълва.",
                context=context, alternatives=alternatives,
            )
        chosen = alternatives[0]
        target_sha = _identity_value(identity, "source_sha256")
        same_source = any(
            target_sha and p.source_sha256 == target_sha
            for p in chosen.provenance
        )
        scope = "същия DXF" if same_source else "напълно съвпадащ друг контекст"
        return DecisionSuggestion(
            READY,
            f"Предложение от {len(chosen.provenance)} доставено решение "
            f"({scope}); изисква изрично приемане.",
            context=context, proposal=chosen.resolution,
            provenance=chosen.provenance, alternatives=alternatives,
        )


def strict_context_from_review_row(
    row: Mapping[str, Any], *,
    identity: DataIdentity | Mapping[str, Any], engine_version: str,
) -> tuple[StrictDecisionContext | None, tuple[str, ...]]:
    """Canonical before-decision signature; missing critical fields fail closed."""

    version = _upper(engine_version)
    annotation = _upper(
        row.get("annotation_text") or row.get("source_annotation_text")
    )
    count = _positive_integer_text(
        row.get("annotation_count") or row.get("original_count")
        or row.get("production_count")
    )
    diameter = _positive_number_text(
        row.get("diameter_mm") or row.get("original_diameter_mm")
        or row.get("production_diameter_mm")
    )
    length = _positive_number_text(
        row.get("declared_length_cm") or row.get("original_length_cm")
        or row.get("production_length_cm")
    )
    shape = _upper(
        row.get("master_shape_code") or row.get("suggested_shape_code")
    )
    handles = _string_values(
        row.get("candidate_handles") or row.get("geometry_handles")
    )
    segments = _number_list_text(_first_list(
        row.get("canonical_segment_lengths_cm"),
        row.get("production_segments_cm"),
        row.get("suggested_segments_cm"),
        row.get("suggested_segment_lengths_cm"),
    ))
    if shape == "F001" and not segments and length:
        segments = length
    geometryless_context = not handles
    required = {
        "engine_version": version, "annotation_text": annotation,
        "count": count, "diameter_mm": diameter,
        "declared_length_cm": length, "candidate_shape_code": shape,
        # The handle is deliberately required but excluded from the digest:
        # it prevents anonymous/self evidence while still allowing a distinct
        # annotation with the exact same engineering context to match.
        "annotation_handle": _upper(row.get("annotation_handle")),
        "project_id": _identity_value(identity, "project_id"),
        "source_generation_id": _identity_value(identity, "source_generation_id"),
    }
    if not geometryless_context:
        required.update({
            "candidate_segments_cm": segments,
            "candidate_handles": "|".join(handles),
        })
    missing = [name for name, value in required.items() if not value]
    if row.get("global_conflict") or row.get("geometry_owner_conflict"):
        missing.append("unconflicted_geometry")
    if row.get("master_conflict"):
        missing.append("unambiguous_master")
    if missing:
        return None, tuple(sorted(set(missing)))
    values = {
        "engine_version": version,
        "annotation_text": annotation,
        "count": count,
        "diameter_mm": diameter,
        "declared_length_cm": length,
        "candidate_shape_code": shape,
        "candidate_segments_cm": segments,
        "candidate_turn_angles_deg": _number_list_text(
            row.get("canonical_turn_angles_deg")
            or row.get("suggested_turn_angles_deg")
        ),
        "problem_codes": _codes(
            row.get("matcher_blocker_codes"), row.get("review_reason_codes")
        ) or "NONE",
        "geometry_class": (
            "NO_DXF_GEOMETRY"
            if geometryless_context
            else f"HANDLES:{len(handles)}"
        ),
        "geometry_provenance": (
            _upper(
                row.get("geometry_provenance") or row.get("candidate_basis")
            )
            or "DXF_HANDLES"
        ),
        "candidate_layer": _upper(
            row.get("candidate_layer") or row.get("geometry_layer")
        ),
        "batch_status": _upper(row.get("batch_status")),
        "matcher_disposition": _upper(row.get("matcher_disposition")),
        "matcher_evidence_band": _upper(row.get("matcher_evidence_band")),
        "candidate_confidence": _upper(row.get("candidate_confidence")),
        "contextual_rule_code": _upper(row.get("contextual_rule_code")),
        "canonical_signature": _canonical(row.get("canonical_signature")),
        "numeric_difference": _optional_number(row.get("numeric_difference")),
        "alternative_candidate_count": _nonnegative_integer_text(
            row.get("alternative_candidate_count")
        ),
        "frame_category_code": _upper(row.get("frame_category_code")),
        "data_quality_flags": _codes(row.get("data_quality_flags")) or "NONE",
    }
    fields = tuple((name, values[name]) for name in _CONTEXT_FIELDS)
    return StrictDecisionContext(fields, _digest(dict(fields))), ()


def _completed_session_events(path: Path) -> list[_DeliveredEvent]:
    """Read delivered decisions from ended sessions without writing the DB."""

    if not path.is_file():
        return []
    with sqlite3.connect(str(path), timeout=5.0) as connection:
        connection.row_factory = sqlite3.Row
        tables = {
            str(row[0]) for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if not {"events", "sessions"}.issubset(tables):
            raise ValueError("Session DB няма events/sessions таблици")
        rows = connection.execute(
            """
            SELECT e.*
            FROM events AS e
            JOIN sessions AS s ON s.session_id = e.session_id
            WHERE s.status = 'ENDED'
              AND e.stable_event_id IS NOT NULL
            ORDER BY e.occurred_at, e.event_id
            """
        ).fetchall()

    result: list[_DeliveredEvent] = []
    for row in rows:
        try:
            payload = json.loads(str(row["payload_json"] or "{}"))
        except (TypeError, json.JSONDecodeError):
            payload = {}
        event_type = _upper(row["event_type"])
        if event_type not in DECISION_EVENT_TYPES:
            continue
        event = {
            "event_id": int(row["event_id"]),
            "stable_event_id": str(row["stable_event_id"] or ""),
            "session_id": str(row["session_id"] or ""),
            "at": str(row["occurred_at"] or ""),
            "event_type": event_type,
            "project": str(row["project_name"] or ""),
            "project_name": str(row["project_name"] or ""),
            "project_root": str(row["project_root"] or ""),
            "source_sha256": str(row["source_sha256"] or ""),
            "engine_version": str(row["engine_version"] or ""),
            "matcher_sha256": str(row["matcher_sha256"] or ""),
            "annotation_handle": str(row["annotation_handle"] or ""),
            "annotation_text": str(row["annotation_text"] or ""),
            "payload": payload if isinstance(payload, dict) else {},
        }
        result.append(_DeliveredEvent(event, {"SESSION_DB_ENDED"}))
    return result


def _delivered_project_events(project_root: Path) -> list[_DeliveredEvent]:
    """Read delivered evidence plus local lifecycle-only tombstones.

    A pending local correction/exclusion must immediately invalidate older
    delivered evidence, but it can never be positive suggestion evidence until
    the durable outbox marks it DELIVERED.
    """

    working = project_root / "working"
    delivered_session: dict[str, str] = {}
    for row in _read_jsonl(working / OUTBOX_NAME):
        event_id = _text(row.get("event_id"))
        if event_id and _upper(row.get("state")) == "DELIVERED":
            delivered_session[event_id] = _text(row.get("session_id"))
    result: list[_DeliveredEvent] = []
    for envelope in _read_jsonl(working / JOURNAL_NAME):
        event_id = _text(envelope.get("event_id"))
        event_type = _upper(envelope.get("event_type"))
        is_delivered = event_id in delivered_session
        is_lifecycle_tombstone = bool(
            envelope.get("supersedes_event_ids")
        ) or event_type == "GROUP_UNDONE"
        if not is_delivered and not is_lifecycle_tombstone:
            continue
        source = _mapping(envelope.get("source_identity"))
        payload = _mapping(envelope.get("payload"))
        annotation = _mapping(payload.get("annotation"))
        handles = envelope.get("annotation_handles")
        handle = (
            str(handles[0])
            if isinstance(handles, list) and handles
            else _text(payload.get("annotation_handle"))
            or _text(annotation.get("handle"))
        )
        project_name = _text(source.get("project_name")) or project_root.name
        event = {
            "event_id": event_id,
            "stable_event_id": event_id,
            "session_id": delivered_session.get(event_id, ""),
            "at": _text(envelope.get("occurred_at")),
            "event_type": event_type,
            "project": project_name,
            "project_name": project_name,
            "project_root": str(project_root),
            "source_sha256": _text(source.get("source_sha256")),
            "engine_version": _text(source.get("engine_version")),
            "annotation_handle": handle,
            "annotation_text": (
                _text(payload.get("annotation_text"))
                or _text(annotation.get("text"))
            ),
            "payload": {"human_decision": envelope},
        }
        delivery_source = (
            "PROJECT_OUTBOX_DELIVERED"
            if is_delivered
            else "PROJECT_JOURNAL_PENDING_TOMBSTONE"
        )
        result.append(_DeliveredEvent(event, {delivery_source}))
    return result


def _merge_events(
    values: Iterable[_DeliveredEvent],
) -> tuple[dict[str, _DeliveredEvent], int]:
    """Deduplicate Session DB + portable journal copies by stable event id."""

    merged: dict[str, _DeliveredEvent] = {}
    duplicate_count = 0
    for value in values:
        event_id = _text(value.event.get("stable_event_id"))
        if not event_id:
            continue
        existing = merged.get(event_id)
        if existing is None:
            merged[event_id] = _DeliveredEvent(
                dict(value.event), set(value.delivery_sources)
            )
            continue
        duplicate_count += 1
        existing.delivery_sources.update(value.delivery_sources)
        for key, item in value.event.items():
            if existing.event.get(key) in (None, "", {}, []):
                existing.event[key] = item
    return merged, duplicate_count


def _eligible_evidence(
    delivered: Iterable[_DeliveredEvent],
) -> tuple[list[_Evidence], int]:
    """Validate envelopes, supersession and complete production evidence."""

    all_values = list(delivered)
    lifecycle: list[_DeliveredEvent] = []
    candidates: list[_DeliveredEvent] = []
    for value in all_values:
        event = value.event
        envelope = _mapping(
            _mapping(event.get("payload")).get("human_decision")
        )
        stable_id = _text(event.get("stable_event_id"))
        if (
            envelope.get("schema") != DECISION_CONTRACT_SCHEMA
            or _text(envelope.get("event_id")) != stable_id
            or _upper(envelope.get("event_type")) not in DECISION_EVENT_TYPES
        ):
            continue
        lifecycle.append(value)
        if (
            _upper(envelope.get("event_type")) == _EVENT_TYPE
            and bool(envelope.get("active", True))
            and value.delivery_sources
            & {"SESSION_DB_ENDED", "PROJECT_OUTBOX_DELIVERED"}
        ):
            candidates.append(value)

    normalized = _decision_rows([item.event for item in lifecycle])
    by_id = {
        _text(item.event.get("stable_event_id")): item
        for item in candidates
    }
    evidence: list[_Evidence] = []
    for row in normalized:
        event_id = _text(row.get("event_id"))
        delivered_event = by_id.get(event_id)
        if delivered_event is None or not _row_is_eligible(row):
            continue
        event = delivered_event.event
        envelope = _mapping(
            _mapping(event.get("payload")).get("human_decision")
        )
        payload = _mapping(envelope.get("payload"))
        # Recheck the immutable raw event.  The learning-pack normalizer keeps
        # backward-compatible fallbacks (for example empty resolved handles may
        # inherit candidate handles); suggestions must never use those fallbacks.
        raw_action = _upper(payload.get("action_class"))
        raw_geometry = _mapping(payload.get("geometry"))
        raw_reason = _upper(payload.get("reason_code"))
        resolved_handles = _string_values(raw_geometry.get("handles"))
        original = _mapping(payload.get("original"))
        row_context = _mapping(payload.get("row_context"))
        original_handles = _string_values(
            original.get("geometry_handles")
            or row_context.get("candidate_handles")
        )
        has_geometry = bool(resolved_handles)
        valid_geometryless = bool(
            raw_reason in _GEOMETRYLESS_REASONS
            and _text(payload.get("note") or payload.get("operator_note"))
        )
        # A historical WRONG_GEOMETRY/WRONG_OBJECT decision may contain a
        # replacement selection made by the engineer. Handles are local DXF
        # object identities and are deliberately never transferred to a new
        # case. Reusing such an event while keeping the current candidate
        # handles would silently resurrect the very geometry that was rejected
        # in the historical case, so changed selections are not suggestion
        # evidence at all.
        changed_geometry_selection = bool(
            raw_reason in _GEOMETRYLESS_REASONS
            and resolved_handles
            and resolved_handles != original_handles
        )
        if raw_action not in _ELIGIBLE_ACTIONS or not (
            has_geometry or valid_geometryless
        ) or raw_reason == "CONFIRM_CORRECT" or (
            raw_reason == "NO_GEOMETRY" and has_geometry
        ) or changed_geometry_selection:
            continue
        identity = _mapping(envelope.get("source_identity"))
        context, _ = strict_context_from_review_row(
            _historical_context(row, payload),
            identity=identity,
            engine_version=(
                _text(event.get("engine_version"))
                or _text(identity.get("engine_version"))
            ),
        )
        resolution = _resolution(row, payload)
        if context is None or resolution is None:
            continue

        source_sha = (
            _text(identity.get("source_sha256"))
            or _text(event.get("source_sha256"))
        )
        drawing_family = _text(identity.get("drawing_family_id"))
        if not drawing_family:
            drawing_family = hashlib.sha256((
                source_sha or _text(identity.get("source_generation_id"))
            ).encode("utf-8")).hexdigest()
        annotation_handle = _text(row.get("annotation_handle"))
        normalized_handle = _upper(annotation_handle)
        if not normalized_handle:
            continue
        instance_id = hashlib.sha256(
            f"{drawing_family}|{normalized_handle}".encode("utf-8")
        ).hexdigest()
        provenance = SuggestionProvenance(
            stable_event_id=event_id,
            session_id=_text(event.get("session_id")),
            occurred_at=_text(row.get("at")) or _text(event.get("at")),
            project_name=(
                _text(event.get("project_name")) or _text(row.get("project"))
            ),
            source_sha256=source_sha,
            drawing_family_id=drawing_family,
            source_generation_id=_text(identity.get("source_generation_id")),
            annotation_handle=annotation_handle,
            engine_version=_text(event.get("engine_version")),
            action_class=resolution.action_class,
            reason_code=resolution.reason_code,
            delivery_sources=tuple(sorted(delivered_event.delivery_sources)),
        )
        evidence.append(
            _Evidence(context, resolution, provenance, instance_id)
        )
    return evidence, max(0, len(all_values) - len(evidence))


def _row_is_eligible(row: Mapping[str, Any]) -> bool:
    complete = _mapping(row.get("field_completeness"))
    geometry_evidence = bool(
        complete.get("geometry")
        or (
            _upper(row.get("reason_code")) in _GEOMETRYLESS_REASONS
            and _text(row.get("note"))
        )
    )
    return bool(
        _upper(row.get("event_type")) == _EVENT_TYPE
        and row.get("active", True)
        and not row.get("superseded")
        and not row.get("invalidated_by_undo")
        and _upper(row.get("action_class")) in _ELIGIBLE_ACTIONS
        and _upper(row.get("reason_code")) != "CONFIRM_CORRECT"
        and row.get("complete_production_resolution")
        and geometry_evidence
        and not row.get("ownership_conflict")
        and _upper(row.get("shape_semantics_status")) == "VERSIONED"
        and _upper(row.get("reason_code")) != "NON_PRODUCTION_POSITION"
    )


def _historical_context(
    row: Mapping[str, Any], payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Recover the exact pre-decision Review Center context."""

    context = _mapping(payload.get("row_context"))
    original = _mapping(payload.get("original"))
    result = dict(context)
    result.update({
        "annotation_handle": row.get("annotation_handle"),
        "annotation_text": row.get("source_annotation_text"),
        "annotation_count": row.get("original_count"),
        "diameter_mm": row.get("original_diameter_mm"),
        "declared_length_cm": row.get("original_length_cm"),
        "master_shape_code": row.get("suggested_shape_code"),
        "canonical_segment_lengths_cm": list(
            row.get("suggested_segment_lengths_cm") or []
        ),
        "candidate_handles": list(row.get("candidate_handles") or []),
        # Do not leak the chosen correction reason into the match key.
        "review_reason_codes": list(
            context.get("review_reason_codes")
            or original.get("review_reason_codes")
            or []
        ),
        "global_conflict": bool(row.get("ownership_conflict")),
        "geometry_owner_conflict": bool(row.get("ownership_conflict")),
    })
    geometry = _mapping(payload.get("geometry"))
    result.setdefault(
        "candidate_basis",
        context.get("candidate_basis")
        or original.get("candidate_basis")
        or geometry.get("basis"),
    )
    result.setdefault(
        "data_quality_flags",
        context.get("data_quality_flags")
        or original.get("data_quality_flags")
        or [],
    )
    result["master_conflict"] = bool(
        context.get("master_conflict") or original.get("master_conflict")
    )
    result.setdefault("batch_status", original.get("batch_status"))
    result.setdefault(
        "matcher_disposition", original.get("matcher_disposition")
    )
    return result


def _resolution(
    row: Mapping[str, Any], payload: Mapping[str, Any],
) -> ProductionResolution | None:
    resolved = _mapping(payload.get("resolved"))
    count = _positive_integer(row.get("accepted_count"))
    diameter = _positive_number(row.get("accepted_diameter_mm"))
    length = _positive_number(row.get("accepted_length_cm"))
    revision = _positive_integer(
        row.get("accepted_shape_revision"), allow_zero=True
    )
    segments = _number_list(
        row.get("accepted_segment_lengths_cm"), positive=True
    )
    turns = _number_list(
        resolved.get("turn_angles_deg"), positive=False
    )
    shape = _upper(row.get("accepted_shape_code"))
    if (
        count is None or diameter is None or length is None
        or revision is None or not segments or not shape
    ):
        return None
    return ProductionResolution(
        shape_code=shape,
        shape_name=_text(row.get("accepted_shape_name")) or shape,
        shape_revision=revision,
        count=count,
        diameter_mm=diameter,
        production_length_cm=length,
        segment_lengths_cm=tuple(segments),
        turn_angles_deg=tuple(turns),
        action_class=_upper(row.get("action_class")),
        reason_code=_upper(row.get("reason_code")) or "OTHER",
        reason=_text(row.get("reason")),
        geometry_mode=(
            "DXF_HANDLES"
            if _string_values(_mapping(payload.get("geometry")).get("handles"))
            else "NO_DXF_GEOMETRY"
        ),
        historical_note_present=bool(_text(row.get("note"))),
    )


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    result = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        try:
            value = json.loads(line)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            result.append(value)
    return result


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _text(value: Any) -> str:
    return " ".join(str(value if value is not None else "").split()).strip()


def _upper(value: Any) -> str:
    return _text(value).upper()


def _digest(value: Any) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _canonical(value: Any) -> str:
    if value in (None, "", [], {}, ()):
        return ""
    if isinstance(value, (Mapping, list, tuple)):
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    return _upper(value)


def _number(value: Any) -> str:
    try:
        number = Decimal(str(value).replace(",", "."))
    except (InvalidOperation, ValueError):
        return ""
    return format(number.normalize(), "f") if number.is_finite() else ""


def _positive_number(value: Any) -> float | None:
    try:
        number = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _positive_number_text(value: Any) -> str:
    value = _positive_number(value)
    return _number(value) if value is not None else ""


def _optional_number(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        number = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return _upper(value)
    return _number(number) if math.isfinite(number) else ""


def _positive_integer(value: Any, *, allow_zero: bool = False) -> int | None:
    try:
        number = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None
    minimum = 0 if allow_zero else 1
    if (
        not math.isfinite(number) or number < minimum
        or abs(number - round(number)) > 1e-6
    ):
        return None
    return int(round(number))


def _positive_integer_text(value: Any) -> str:
    value = _positive_integer(value)
    return str(value) if value is not None else ""


def _nonnegative_integer_text(value: Any) -> str:
    if value in (None, ""):
        return ""
    number = _positive_integer(value, allow_zero=True)
    return str(number) if number is not None else _upper(value)


def _number_list(value: Any, *, positive: bool) -> list[float]:
    if not isinstance(value, (list, tuple)):
        return []
    result = []
    for item in value:
        try:
            number = float(str(item).replace(",", "."))
        except (TypeError, ValueError):
            return []
        if not math.isfinite(number) or (positive and number <= 0):
            return []
        result.append(number)
    return result


def _number_list_text(value: Any) -> str:
    values = _number_list(value, positive=False)
    return "|".join(_number(item) for item in values)


def _first_list(*values: Any) -> list[Any]:
    for value in values:
        if isinstance(value, (list, tuple)) and value:
            return list(value)
    return []


def _string_values(value: Any) -> tuple[str, ...]:
    source = (
        value.split(",") if isinstance(value, str)
        else value if isinstance(value, (list, tuple, set))
        else ()
    )
    return tuple(sorted({_upper(item) for item in source if _text(item)}))


def _codes(*values: Any) -> str:
    result: set[str] = set()
    for value in values:
        source = (
            value.split(",") if isinstance(value, str)
            else value if isinstance(value, (list, tuple, set))
            else ()
        )
        result.update(_upper(item) for item in source if _text(item))
    return "|".join(sorted(result))


def _identity_value(
    identity: DataIdentity | Mapping[str, Any], name: str,
) -> str:
    if isinstance(identity, DataIdentity):
        return _text(getattr(identity, name, ""))
    return _text(identity.get(name)) if isinstance(identity, Mapping) else ""
