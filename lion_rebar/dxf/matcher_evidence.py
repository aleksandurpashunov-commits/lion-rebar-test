from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from lion_rebar.dxf.recognition_policy import (
    DEFAULT_RECOGNITION_POLICY,
    RecognitionPolicy,
)


class MatcherDisposition(str, Enum):
    """Machine-stable recommendation returned by the evidence layer."""

    # This is eligibility evidence only.  The module never writes an approval;
    # a separate workflow must apply project/owner policy before any mutation.
    AUTO_ELIGIBLE = "AUTO_ELIGIBLE"
    # Backwards-friendly alias for callers using the shorter conceptual band.
    AUTO = "AUTO_ELIGIBLE"  # noqa: PIE796 - public backwards-compatible enum alias
    CONTROL = "CONTROL"
    MANUAL = "MANUAL"
    UNKNOWN = "UNKNOWN"


class EvidenceBand(str, Enum):
    """Named evidence bands; deliberately not presented as fake percentages."""

    EXACT = "EXACT"
    CLEAR = "CLEAR"
    REVIEW = "REVIEW"
    REJECT = "REJECT"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class MatcherCalibration:
    """Release evidence that allows a clear match to become ``AUTO_ELIGIBLE``.

    A regression suite generated from MASTER geometry proves invariance, but it
    does not by itself calibrate false accepts in real drawings.  Automatic
    acceptance is therefore enabled only by an explicit calibration record
    which includes held-out, manually labelled field cases and no false accept.

    No made-up "98%" confidence is derived here.  The caller supplies a record
    produced by the regression/field validation pipeline.
    """

    calibration_id: str
    engine_fingerprint: str
    validated_shape_codes: tuple[str, ...] = field(default_factory=tuple)
    regression_case_count: int = 0
    holdout_case_count: int = 0
    false_accept_count: int = 0
    released_for_auto: bool = False
    notes: tuple[str, ...] = field(default_factory=tuple)

    def permits_auto(self, shape_code: str | None) -> bool:
        code = str(shape_code or "").strip().upper()
        scope = {value.strip().upper() for value in self.validated_shape_codes}
        return bool(
            code
            and self.released_for_auto
            and self.calibration_id.strip()
            and self.engine_fingerprint.strip()
            and self.regression_case_count > 0
            and self.holdout_case_count > 0
            and self.false_accept_count == 0
            and (code in scope or "*" in scope)
        )

    def blocking_reason(self, shape_code: str | None) -> str | None:
        if self.permits_auto(shape_code):
            return None
        code = str(shape_code or "").strip().upper() or "—"
        if not self.released_for_auto:
            return "Калибрацията не е освободена за автоматично приемане."
        if not self.calibration_id.strip() or not self.engine_fingerprint.strip():
            return "Калибрацията няма проследим идентификатор или Engine отпечатък."
        if self.regression_case_count <= 0:
            return "Липсват преминали регресионни случаи."
        if self.holdout_case_count <= 0:
            return "Липсват независими реални контролни случаи."
        if self.false_accept_count:
            return (
                "Калибрацията съдържа грешно автоматично приемане "
                f"({self.false_accept_count})."
            )
        return f"MASTER форма {code} не е в обхвата на тази калибрация."


@dataclass(frozen=True)
class MatcherDecisionPolicy:
    """Safety posture around the measurable recognition policy."""

    recognition: RecognitionPolicy = DEFAULT_RECOGNITION_POLICY
    require_calibration_for_auto: bool = True
    require_reference_profile_for_auto: bool = True
    require_confirmed_layer_diameter_for_auto: bool = True

    @classmethod
    def from_settings(
        cls,
        settings: Mapping[str, Any] | RecognitionPolicy | None,
    ) -> MatcherDecisionPolicy:
        if isinstance(settings, RecognitionPolicy):
            return cls(recognition=settings)
        recognition = RecognitionPolicy.from_settings(settings)
        decision = (
            settings.get("matcher_decision", {})
            if isinstance(settings, Mapping)
            else {}
        )
        if not isinstance(decision, Mapping):
            decision = {}
        return cls(
            recognition=recognition,
            require_calibration_for_auto=_setting_bool(
                decision,
                "require_calibration_for_auto",
                True,
            ),
            require_reference_profile_for_auto=_setting_bool(
                decision,
                "require_reference_profile_for_auto",
                True,
            ),
            require_confirmed_layer_diameter_for_auto=_setting_bool(
                decision,
                "require_confirmed_layer_diameter_for_auto",
                True,
            ),
        )


@dataclass(frozen=True)
class MatcherDecisionContext:
    """Evidence outside one geometry candidate.

    ``master_approved`` is intentionally explicit.  A code being present is
    not proof that the referenced revision is APPROVED/LOCKED.
    """

    annotation_complete: bool = True
    master_approved: bool = False
    global_geometry_conflict: bool = False
    geometry_owner_conflict: bool = False
    branched_geometry: bool = False
    disconnected_geometry: bool = False
    foreign_geometry_in_candidate: bool = False
    layer_noise_detected: bool = False
    quantity_review_required: bool = False
    alternative_count: int = 0
    data_quality_flags: tuple[str, ...] = field(default_factory=tuple)
    rejection_counts: Mapping[str, int] = field(default_factory=dict)

    @classmethod
    def from_report_row(
        cls,
        row: Mapping[str, Any],
        *,
        approved_master_codes: Iterable[str] = (),
    ) -> MatcherDecisionContext:
        master_code = str(row.get("master_shape_code") or "").strip().upper()
        approved = {str(value).strip().upper() for value in approved_master_codes}
        annotation_class = str(row.get("annotation_class") or "ACTIONABLE").upper()
        notes = _string_tuple(row.get("candidate_notes"))
        return cls(
            annotation_complete=annotation_class == "ACTIONABLE",
            master_approved=bool(master_code and master_code in approved),
            global_geometry_conflict=bool(row.get("global_conflict")),
            geometry_owner_conflict=bool(row.get("geometry_owner_conflict")),
            branched_geometry=bool(row.get("branched_geometry")),
            disconnected_geometry=bool(row.get("disconnected_geometry")),
            foreign_geometry_in_candidate=bool(
                row.get("foreign_geometry_in_candidate")
            ),
            layer_noise_detected=bool(row.get("layer_noise_detected"))
            or any("ШУМ" in value.upper() for value in notes),
            quantity_review_required=bool(row.get("quantity_review_required")),
            alternative_count=len(_sequence(row.get("alternative_candidates"))),
            data_quality_flags=_string_tuple(row.get("data_quality_flags")),
            rejection_counts=(
                dict(row.get("rejection_counts") or {})
                if isinstance(row.get("rejection_counts"), Mapping)
                else {}
            ),
        )


@dataclass(frozen=True)
class TransformEvidence:
    rotation_normalized: bool
    rotation_deg: float | None
    mirrored: bool
    reversed_direction: bool
    uniform_scale_factor: float | None
    mirror_requires_review: bool
    explanation: str


@dataclass(frozen=True)
class EvidenceItem:
    code: str
    category: str
    band: EvidenceBand
    message: str
    value: Any = None
    threshold: str | None = None
    source: str = "MATCHER"


@dataclass(frozen=True)
class SafetyGate:
    code: str
    disposition: MatcherDisposition
    message: str


@dataclass(frozen=True)
class MatcherDecision:
    disposition: MatcherDisposition
    evidence_band: EvidenceBand
    form_classification_confidence: EvidenceBand
    geometry_ownership_confidence: EvidenceBand
    annotation_handle: str | None
    candidate_handles: tuple[str, ...]
    master_shape_code: str | None
    master_shape_name: str | None
    summary: str
    reasons: tuple[str, ...]
    safety_gates: tuple[SafetyGate, ...]
    evidence: tuple[EvidenceItem, ...]
    form_classification_evidence: tuple[EvidenceItem, ...]
    geometry_ownership_evidence: tuple[EvidenceItem, ...]
    transforms: TransformEvidence
    calibration_id: str | None = None

    @property
    def is_auto_eligible(self) -> bool:
        return self.disposition is MatcherDisposition.AUTO_ELIGIBLE

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["disposition"] = self.disposition.value
        payload["evidence_band"] = self.evidence_band.value
        payload["form_classification_confidence"] = (
            self.form_classification_confidence.value
        )
        payload["geometry_ownership_confidence"] = (
            self.geometry_ownership_confidence.value
        )
        payload["safety_gates"] = [
            {
                **asdict(gate),
                "disposition": gate.disposition.value,
            }
            for gate in self.safety_gates
        ]
        payload["evidence"] = [
            {
                **asdict(item),
                "band": item.band.value,
            }
            for item in self.evidence
        ]
        payload["form_classification_evidence"] = [
            {
                **asdict(item),
                "band": item.band.value,
            }
            for item in self.form_classification_evidence
        ]
        payload["geometry_ownership_evidence"] = [
            {
                **asdict(item),
                "band": item.band.value,
            }
            for item in self.geometry_ownership_evidence
        ]
        return payload


def evaluate_matcher_candidate(
    candidate: Any | None,
    *,
    context: MatcherDecisionContext | None = None,
    policy: MatcherDecisionPolicy | RecognitionPolicy | Mapping[str, Any] | None = None,
    calibration: MatcherCalibration | None = None,
    annotation_handle: str | None = None,
) -> MatcherDecision:
    """Explain and classify one ranked geometry candidate.

    The function is intentionally pure: it does not approve links, update the
    Engine or mutate reports.  It can consume a ``GeometryCandidate`` instance
    or a batch-report row because field aliases are resolved conservatively.
    """

    context = context or MatcherDecisionContext()
    decision_policy = _resolve_decision_policy(policy)
    recognition = decision_policy.recognition
    candidate_handles = _string_tuple(_read(candidate, "handles", "candidate_handles"))
    master_code = _optional_text(_read(candidate, "master_shape_code"))
    master_name = _optional_text(_read(candidate, "master_shape_name"))
    annotation_handle = annotation_handle or _optional_text(
        _read(candidate, "annotation_handle")
    )

    transforms = _transform_evidence(candidate)
    evidence = _build_evidence(candidate, context, recognition)
    form_evidence, ownership_evidence = _split_decision_evidence(evidence)
    form_confidence = _aggregate_evidence_band(form_evidence)
    ownership_confidence = _aggregate_evidence_band(ownership_evidence)
    gates = _safety_gates(candidate, context, recognition, evidence)
    disposition, reasons = _recommend_disposition(
        candidate,
        context=context,
        policy=decision_policy,
        calibration=calibration,
        evidence=evidence,
        gates=gates,
    )
    overall_band = _overall_band(evidence, disposition)
    summary = _summary(
        disposition,
        master_code=master_code,
        gates=gates,
        calibration=calibration,
    )
    return MatcherDecision(
        disposition=disposition,
        evidence_band=overall_band,
        form_classification_confidence=form_confidence,
        geometry_ownership_confidence=ownership_confidence,
        annotation_handle=annotation_handle,
        candidate_handles=candidate_handles,
        master_shape_code=master_code,
        master_shape_name=master_name,
        summary=summary,
        reasons=tuple(reasons),
        safety_gates=tuple(gates),
        evidence=tuple(evidence),
        form_classification_evidence=form_evidence,
        geometry_ownership_evidence=ownership_evidence,
        transforms=transforms,
        calibration_id=calibration.calibration_id if calibration else None,
    )


def evaluate_report_row(
    row: Mapping[str, Any],
    *,
    approved_master_codes: Iterable[str],
    policy: MatcherDecisionPolicy | RecognitionPolicy | Mapping[str, Any] | None = None,
    calibration: MatcherCalibration | None = None,
) -> MatcherDecision:
    """Convenience adapter for a row from ``batch_geometry_report``."""

    context = MatcherDecisionContext.from_report_row(
        row,
        approved_master_codes=approved_master_codes,
    )
    return evaluate_matcher_candidate(
        row,
        context=context,
        policy=policy,
        calibration=calibration,
        annotation_handle=_optional_text(row.get("annotation_handle")),
    )


def _build_evidence(
    candidate: Any | None,
    context: MatcherDecisionContext,
    policy: RecognitionPolicy,
) -> list[EvidenceItem]:
    if candidate is None:
        return [
            EvidenceItem(
                code="NO_CANDIDATE",
                category="availability",
                band=EvidenceBand.UNKNOWN,
                message="Matcher не е върнал геометричен кандидат.",
            )
        ]

    evidence: list[EvidenceItem] = []
    handles = _string_tuple(_read(candidate, "handles", "candidate_handles"))
    master_code = _optional_text(_read(candidate, "master_shape_code"))
    ambiguous = _string_tuple(_read(candidate, "master_ambiguous_shape_codes"))
    signature = _optional_text(_read(candidate, "canonical_signature"))
    segment_count = _safe_int(_read(candidate, "segment_count"))
    profile_id = _optional_text(_read(candidate, "master_reference_profile_id"))

    if not handles:
        evidence.append(
            EvidenceItem(
                code="NO_GEOMETRY_HANDLES",
                category="topology",
                band=EvidenceBand.UNKNOWN,
                message="Кандидатът няма проследими DXF handles.",
            )
        )
    if not master_code:
        topology_band = EvidenceBand.UNKNOWN
        topology_message = "Не е намерена активна MASTER форма."
    elif ambiguous:
        topology_band = EvidenceBand.REVIEW
        topology_message = (
            f"Топологията съвпада с повече от една MASTER форма: "
            f"{', '.join(ambiguous)}."
        )
    elif profile_id:
        topology_band = EvidenceBand.EXACT
        topology_message = (
            f"Съвпадение с контролен профил {profile_id} на {master_code}."
        )
    else:
        topology_band = EvidenceBand.CLEAR
        topology_message = (
            f"Съвпадение с топологията на {master_code}, без конкретен "
            "контролен профил."
        )
    evidence.append(
        EvidenceItem(
            code="TOPOLOGY_MATCH",
            category="topology",
            band=topology_band,
            message=topology_message,
            value={
                "master_shape_code": master_code,
                "segment_count": segment_count,
                "canonical_signature": signature,
                "ambiguous_shape_codes": list(ambiguous),
                "reference_profile_id": profile_id,
            },
            source="MASTER",
        )
    )

    declared = _safe_float(_read(candidate, "declared_length_cm"))
    geometry = _safe_float(
        _read(candidate, "effective_geometry_length_cm", "geometry_length_raw")
    )
    difference = _safe_float(_read(candidate, "numeric_difference"))
    if difference is None and declared is not None and geometry is not None:
        difference = abs(geometry - declared)
    length_band, length_message, length_threshold = _length_band(
        declared,
        geometry,
        difference,
        policy,
        master_shape_code=master_code,
    )
    evidence.append(
        EvidenceItem(
            code="PARAMETER_LENGTH",
            category="parameters",
            band=length_band,
            message=length_message,
            value={
                "declared_length_cm": declared,
                "effective_geometry_length_cm": geometry,
                "difference_cm": difference,
                "raw_difference_cm": _safe_float(
                    _read(candidate, "raw_numeric_difference")
                ),
                "segment_lengths_cm": list(
                    _float_tuple(_read(candidate, "canonical_segment_lengths_cm"))
                ),
                "turn_angles_deg": list(
                    _float_tuple(_read(candidate, "canonical_turn_angles_deg"))
                ),
            },
            threshold=length_threshold,
            source="ANNOTATION_AND_DXF",
        )
    )

    distance = _safe_float(_read(candidate, "distance_raw"))
    distance_band, distance_message, distance_threshold = _distance_band(
        declared,
        distance,
        policy,
    )
    evidence.append(
        EvidenceItem(
            code="TEXT_DISTANCE",
            category="association",
            band=distance_band,
            message=distance_message,
            value={
                "distance_raw": distance,
                "centroid_distance_raw": _safe_float(
                    _read(candidate, "centroid_distance_raw")
                ),
            },
            threshold=distance_threshold,
            source="DXF_SPATIAL_INDEX",
        )
    )

    score_gap = _safe_float(
        _read(candidate, "score_gap", "candidate_score_gap")
    )
    distance_gap_raw = _safe_float(
        _read(candidate, "distance_gap_raw", "candidate_distance_gap_raw")
    )
    layer_diameter = _safe_float(_read(candidate, "layer_diameter_mm"))
    generic_layer = layer_diameter is None
    score_advantage_threshold = (
        float(policy.generic_layer_strong_score_gap)
        if generic_layer
        else float(policy.strong_score_gap)
    )
    distance_advantage_threshold = (
        float(policy.generic_layer_strong_distance_advantage_raw)
        if generic_layer
        else float(policy.strong_distance_advantage_raw)
    )
    reason_codes = {
        str(value).strip().upper()
        for value in _sequence(_read(candidate, "review_reason_codes"))
        if str(value).strip()
    }
    dimension_candidate_tiebreak = (
        "DIMENSION_CANDIDATE_TIEBREAK" in reason_codes
    )
    separation_band, separation_message, separation_basis = _separation_band(
        score_gap=score_gap,
        score_advantage_threshold=score_advantage_threshold,
        distance_gap_raw=distance_gap_raw,
        distance_advantage_threshold=distance_advantage_threshold,
        dimension_candidate_tiebreak=dimension_candidate_tiebreak,
        alternative_count=context.alternative_count,
    )
    evidence.append(
        EvidenceItem(
            code="CANDIDATE_SEPARATION",
            category="ambiguity",
            band=separation_band,
            message=separation_message,
            value={
                "score_gap": score_gap,
                "score_advantage_threshold": score_advantage_threshold,
                "distance_gap_raw": distance_gap_raw,
                "distance_advantage_threshold_raw": distance_advantage_threshold,
                "dimension_candidate_tiebreak": dimension_candidate_tiebreak,
                "resolution_basis": separation_basis,
                "alternative_count": context.alternative_count,
                "master_ambiguous_shape_codes": list(ambiguous),
            },
            threshold=(
                f"score ≥ {score_advantage_threshold:g} или "
                f"пространствена преднина ≥ {distance_advantage_threshold:g} "
                "или доказано A–E разделяне"
            ),
            source="MATCHER_RANKING",
        )
    )

    annotation_diameter = _safe_float(_read(candidate, "diameter_mm"))
    layer_match = bool(_read(candidate, "diameter_layer_match"))
    if layer_diameter is None:
        layer_band = EvidenceBand.REVIEW
        layer_message = (
            "Слоят не кодира диаметър; Ø се взема от надписа и не е независимо "
            "потвърден."
        )
    elif layer_match:
        layer_band = EvidenceBand.EXACT
        layer_message = "Диаметърът от слоя съвпада с надписа."
    else:
        layer_band = EvidenceBand.REJECT
        layer_message = "Диаметърът от слоя противоречи на надписа."
    evidence.append(
        EvidenceItem(
            code="LAYER_DIAMETER",
            category="layer",
            band=layer_band,
            message=layer_message,
            value={
                "candidate_layer": _optional_text(
                    _read(candidate, "layer", "candidate_layer")
                ),
                "layer_diameter_mm": layer_diameter,
                "annotation_diameter_mm": annotation_diameter,
                "diameter_layer_match": layer_match,
                "layer_noise_detected": context.layer_noise_detected,
                "foreign_geometry_in_candidate": context.foreign_geometry_in_candidate,
            },
            source="DXF_LAYER",
        )
    )

    near_gap = bool(_read(candidate, "near_gap_healed"))
    gap = _safe_float(_read(candidate, "near_gap_bridge_gap_raw"))
    evidence.append(
        EvidenceItem(
            code="GEOMETRY_GAP",
            category="topology",
            band=EvidenceBand.REVIEW if near_gap else EvidenceBand.EXACT,
            message=(
                f"Една контролирана празнина {gap:g} е свързана за преглед."
                if near_gap and gap is not None
                else (
                    "Има контролирано свързана празнина."
                    if near_gap
                    else "Не е използвано възстановяване на празнина."
                )
            ),
            value={
                "healed": near_gap,
                "gap_raw": gap,
                "bridge_handles": list(
                    _string_tuple(_read(candidate, "near_gap_bridge_handles"))
                ),
            },
            threshold=(
                f"строго ≤ {policy.endpoint_tolerance_raw:g}; "
                f"контролирано ≤ {policy.near_gap_max_raw:g}"
            ),
            source="DXF_ENDPOINT_GRAPH",
        )
    )

    evidence.append(
        EvidenceItem(
            code="LAYER_NOISE",
            category="layer",
            band=(
                EvidenceBand.REJECT
                if context.foreign_geometry_in_candidate
                else (
                    EvidenceBand.REVIEW
                    if context.layer_noise_detected
                    else EvidenceBand.EXACT
                )
            ),
            message=(
                "Кандидатът съдържа чужда геометрия."
                if context.foreign_geometry_in_candidate
                else (
                    "Открит е шум от други слоеве около кандидата."
                    if context.layer_noise_detected
                    else "Няма сигнал за чужда геометрия в кандидата."
                )
            ),
            value={
                "rejection_counts": dict(context.rejection_counts),
            },
            source="CANDIDATE_CLEANUP",
        )
    )
    ownership_rejected = bool(_read(candidate, "ownership_rejected"))
    ownership_conflict = bool(
        context.global_geometry_conflict or context.geometry_owner_conflict
    )
    if ownership_rejected:
        ownership_band = EvidenceBand.REJECT
        ownership_message = (
            "Кандидатът е отхвърлен, защото геометрията принадлежи по-силно "
            "на друг надпис."
        )
    elif ownership_conflict:
        ownership_band = EvidenceBand.REVIEW
        ownership_message = (
            "Една и съща DXF геометрия участва в конкурентни позиции."
        )
    elif handles:
        ownership_band = EvidenceBand.CLEAR
        ownership_message = (
            "Няма записан конфликт за собствеността на избраната DXF геометрия."
        )
    else:
        ownership_band = EvidenceBand.UNKNOWN
        ownership_message = "Липсва DXF геометрия за проверка на собствеността."
    evidence.append(
        EvidenceItem(
            code="GEOMETRY_OWNERSHIP",
            category="ownership",
            band=ownership_band,
            message=ownership_message,
            value={
                "global_geometry_conflict": context.global_geometry_conflict,
                "geometry_owner_conflict": context.geometry_owner_conflict,
                "ownership_rejected": ownership_rejected,
                "winner_annotations": list(
                    _string_tuple(_read(candidate, "ownership_winner_annotations"))
                ),
            },
            source="GLOBAL_GEOMETRY_OWNERSHIP",
        )
    )
    if hooked_closure_is_confirmed_by_dimensions(candidate):
        присъда = _read(
            candidate, "f006_owned_dimension_confirmation"
        ) or {}
        evidence.append(
            EvidenceItem(
                code="HOOKED_CLOSURE_DIMENSIONS_CONFIRMED",
                category="production",
                band=EvidenceBand.EXACT,
                message=(
                    "Отворените крайни куки са потвърдени от собствените коти "
                    "на контура: същият производствен вектор от два независими "
                    "източника."
                ),
                value={
                    "owner_geometry_handle": присъда.get("owner_geometry_handle"),
                    "dimension_handles": присъда.get("dimension_handles"),
                    "segments_from_owned_dimensions_cm": присъда.get(
                        "segments_from_owned_dimensions_cm"
                    ),
                },
                source="DXF_OWNED_DIMENSIONS",
            )
        )
    return evidence


def hooked_closure_is_confirmed_by_dimensions(candidate: Any | None) -> bool:
    """Има ли второ, независимо доказателство за вектора на стремето.

    Присъдата се пише от ``batch_geometry_report._confirm_f006_hooked_closure_dimensions``
    ПРЕДИ оценката на matcher-а. Липсващо поле значи „не".
    """

    from lion_rebar.dxf.f006_owned_dimension_confirmation import (
        hooked_closure_is_confirmed,
        ПОЛЕ,
    )

    присъда = _read(candidate, ПОЛЕ)
    if присъда is None:
        return False
    return hooked_closure_is_confirmed({ПОЛЕ: присъда})


def _safety_gates(
    candidate: Any | None,
    context: MatcherDecisionContext,
    policy: RecognitionPolicy,
    evidence: Sequence[EvidenceItem],
) -> list[SafetyGate]:
    gates: list[SafetyGate] = []

    def add(code: str, disposition: MatcherDisposition, message: str) -> None:
        if code not in {gate.code for gate in gates}:
            gates.append(SafetyGate(code, disposition, message))

    handles = _string_tuple(_read(candidate, "handles", "candidate_handles"))
    master_code = _optional_text(_read(candidate, "master_shape_code"))
    ambiguous = _string_tuple(_read(candidate, "master_ambiguous_shape_codes"))
    reason_codes = {
        value.strip().upper()
        for value in _string_tuple(_read(candidate, "review_reason_codes"))
    }

    if candidate is None or not handles:
        add(
            "NO_CANDIDATE",
            MatcherDisposition.UNKNOWN,
            "Липсва проследима DXF геометрия.",
        )
    if not context.annotation_complete:
        add(
            "INCOMPLETE_TEXT",
            MatcherDisposition.UNKNOWN,
            "Надписът няма пълен брой, Ø и числова L.",
        )
    if not master_code:
        add(
            "NO_MASTER_MATCH",
            MatcherDisposition.UNKNOWN,
            "Няма съвпадение с активна MASTER форма.",
        )
    elif not context.master_approved:
        add(
            "UNAPPROVED_MASTER",
            MatcherDisposition.UNKNOWN,
            "Намерената MASTER ревизия не е потвърдена като APPROVED/LOCKED.",
        )
    if ambiguous or "AMBIGUOUS_MASTER" in reason_codes:
        add(
            "AMBIGUOUS_MASTER",
            MatcherDisposition.MANUAL,
            "Повече от една MASTER форма остава геометрично валидна.",
        )
    if context.global_geometry_conflict or context.geometry_owner_conflict:
        add(
            "GEOMETRY_OWNERSHIP_CONFLICT",
            MatcherDisposition.MANUAL,
            "Една и съща DXF геометрия участва в конкурентни позиции.",
        )
    if context.branched_geometry:
        add(
            "BRANCHED_GEOMETRY",
            MatcherDisposition.UNKNOWN,
            "Разклонената геометрия не определя една армировъчна форма.",
        )
    if context.disconnected_geometry and not bool(
        _read(candidate, "near_gap_healed")
    ):
        add(
            "DISCONNECTED_GEOMETRY",
            MatcherDisposition.UNKNOWN,
            "Геометрията съдържа несвързани компоненти без одитиран мост.",
        )
    if context.foreign_geometry_in_candidate:
        add(
            "FOREIGN_GEOMETRY",
            MatcherDisposition.MANUAL,
            "Кандидатът включва обект, който не принадлежи на позицията.",
        )
    if bool(_read(candidate, "near_gap_healed")) or "NEAR_GAP_HEALED" in reason_codes:
        add(
            "NEAR_GAP",
            MatcherDisposition.MANUAL,
            "Използвана е контролирана чертожна празнина; изисква потвърждение.",
        )
    if "HOOKED_CLOSURE" in reason_codes:
        # Ръчното потвърждение се иска, защото затворената MASTER форма е
        # сравнена при РАЗШИРЕН допуск на отношенията (0,20-0,25 вместо 0,05,
        # `shape_recognition`). Когато проектантът сам е надписал котите ВЪРХУ
        # същия контур и те независимо дават същия производствен вектор,
        # решението вече не стъпва на разширения допуск, а на две съвпадащи
        # независими доказателства. Само тогава спирачката не се слага; всяко
        # друго състояние (разминаване, липса на коти, незатворено уравнение)
        # я запазва. Останалите спирачки не се пипат.
        if not hooked_closure_is_confirmed_by_dimensions(candidate):
            add(
                "HOOKED_CLOSURE",
                MatcherDisposition.MANUAL,
                (
                    "Отворени крайни куки са сравнени със затворена MASTER форма; "
                    "връзката винаги изисква ръчно потвърждение."
                ),
            )
    if bool(_read(candidate, "mirror_requires_review")) or "MIRROR_REVIEW" in reason_codes:
        add(
            "MIRROR_NOT_PERMITTED",
            MatcherDisposition.MANUAL,
            "Огледалният вариант е намерен, но не е разрешен автоматично от Engine.",
        )
    if "SCHEMATIC_SCALE" in reason_codes:
        add(
            "SCHEMATIC_SCALE",
            MatcherDisposition.MANUAL,
            "DXF геометрията е в схематичен, а не производствен мащаб.",
        )
    if "FAR_ASSOCIATION" in reason_codes:
        add(
            "FAR_ASSOCIATION",
            MatcherDisposition.MANUAL,
            "Надписът е извън основния радиус за еднозначна връзка.",
        )
    if reason_codes.intersection(
        {"F006_PRINTED_DIMENSIONS_MISSING", "F006_PRINTED_DIMENSIONS_MISMATCH"}
    ):
        add(
            "F006_PRODUCTION_DIMENSIONS",
            MatcherDisposition.MANUAL,
            "Отпечатаните размери на F006 не възпроизвеждат безопасно заявената производствена L.",
        )

    layer_item = _evidence_by_code(evidence, "LAYER_DIAMETER")
    if layer_item is not None and layer_item.band is EvidenceBand.REJECT:
        add(
            "LAYER_MISMATCH",
            MatcherDisposition.UNKNOWN,
            "Диаметърът от слоя и надписа си противоречат.",
        )
    length_item = _evidence_by_code(evidence, "PARAMETER_LENGTH")
    if length_item is not None:
        if length_item.band is EvidenceBand.REJECT:
            add(
                "LENGTH_MISMATCH",
                MatcherDisposition.MANUAL,
                "Разликата в L е извън контролния допуск.",
            )
        elif length_item.band is EvidenceBand.UNKNOWN:
            add(
                "LENGTH_NOT_COMPARABLE",
                MatcherDisposition.UNKNOWN,
                "Липсват достатъчно данни за контрол на L.",
            )
    distance_item = _evidence_by_code(evidence, "TEXT_DISTANCE")
    if distance_item is not None:
        if distance_item.band is EvidenceBand.REJECT:
            add(
                "FAR_ASSOCIATION",
                MatcherDisposition.UNKNOWN,
                "Надписът е извън максималния радиус за връзка.",
            )
        elif distance_item.band is EvidenceBand.REVIEW:
            add(
                "FAR_ASSOCIATION",
                MatcherDisposition.MANUAL,
                "Връзката надпис–геометрия е само във втория контролен пръстен.",
            )
    separation_item = _evidence_by_code(evidence, "CANDIDATE_SEPARATION")
    if (
        separation_item is not None
        and separation_item.band in {EvidenceBand.REVIEW, EvidenceBand.REJECT}
    ):
        add(
            "WEAK_CANDIDATE_SEPARATION",
            MatcherDisposition.MANUAL,
            "Първият кандидат няма достатъчно ясно предимство.",
        )
    return gates


def _recommend_disposition(
    candidate: Any | None,
    *,
    context: MatcherDecisionContext,
    policy: MatcherDecisionPolicy,
    calibration: MatcherCalibration | None,
    evidence: Sequence[EvidenceItem],
    gates: Sequence[SafetyGate],
) -> tuple[MatcherDisposition, list[str]]:
    if any(gate.disposition is MatcherDisposition.UNKNOWN for gate in gates):
        return (
            MatcherDisposition.UNKNOWN,
            [gate.message for gate in gates],
        )
    if gates:
        return (
            MatcherDisposition.MANUAL,
            [gate.message for gate in gates],
        )

    master_code = _optional_text(_read(candidate, "master_shape_code"))
    profile_id = _optional_text(_read(candidate, "master_reference_profile_id"))
    layer_item = _evidence_by_code(evidence, "LAYER_DIAMETER")
    reasons: list[str] = []
    auto_ready = True

    if policy.require_reference_profile_for_auto and not profile_id:
        auto_ready = False
        reasons.append(
            "Съвпадението е по MASTER топология, но няма проверен контролен профил."
        )
    if (
        policy.require_confirmed_layer_diameter_for_auto
        and (layer_item is None or layer_item.band is not EvidenceBand.EXACT)
    ):
        auto_ready = False
        reasons.append("Диаметърът не е независимо потвърден от DXF слоя.")
    if context.layer_noise_detected:
        auto_ready = False
        reasons.append("Има близък слой/геометричен шум за контролна извадка.")
    if context.quantity_review_required:
        auto_ready = False
        reasons.append("Необичайният брой изисква контрол преди спецификация.")
    if context.data_quality_flags:
        auto_ready = False
        reasons.append(
            "Има проверки по данните: " + ", ".join(context.data_quality_flags)
        )

    core_codes = {
        "TOPOLOGY_MATCH",
        "PARAMETER_LENGTH",
        "TEXT_DISTANCE",
        "CANDIDATE_SEPARATION",
    }
    core = [item for item in evidence if item.code in core_codes]
    if any(
        item.band not in {EvidenceBand.EXACT, EvidenceBand.CLEAR}
        for item in core
    ):
        return (
            MatcherDisposition.MANUAL,
            reasons
            or ["Едно или повече основни доказателства остават за преглед."],
        )

    if policy.require_calibration_for_auto:
        if calibration is None:
            auto_ready = False
            reasons.append(
                "Няма освободена калибрация върху независими реални случаи."
            )
        else:
            calibration_reason = calibration.blocking_reason(master_code)
            if calibration_reason:
                auto_ready = False
                reasons.append(calibration_reason)

    if auto_ready:
        return (
            MatcherDisposition.AUTO_ELIGIBLE,
            [
                "Всички твърди проверки са преминати.",
                "Съвпадението е еднозначно и е в обхвата на освободена калибрация.",
            ],
        )
    return (
        MatcherDisposition.CONTROL,
        reasons
        or ["Кандидатът е еднозначен, но остава в контролна извадка."],
    )


def _length_band(
    declared: float | None,
    geometry: float | None,
    difference: float | None,
    policy: RecognitionPolicy,
    *,
    master_shape_code: str | None = None,
) -> tuple[EvidenceBand, str, str | None]:
    if declared is None or geometry is None or difference is None:
        return EvidenceBand.UNKNOWN, "L не може да бъде сравнена.", None
    exact = policy.exact_length_tolerance_for_master(
        declared,
        master_shape_code,
    )
    review = policy.review_length_tolerance(declared)
    hard = policy.hard_length_difference_limit(declared)
    threshold = (
        f"точна ≤ {exact:g} cm; контролна ≤ {review:g} cm; "
        f"максимална ≤ {hard:g} cm"
    )
    if difference <= exact:
        return (
            EvidenceBand.EXACT,
            f"DXF L и зададената L съвпадат в точния допуск (Δ={difference:g} cm).",
            threshold,
        )
    if difference <= review:
        return (
            EvidenceBand.REVIEW,
            f"Разликата Δ={difference:g} cm е само в контролния допуск.",
            threshold,
        )
    if difference <= hard:
        return (
            EvidenceBand.REJECT,
            f"Разликата Δ={difference:g} cm е извън контролния допуск.",
            threshold,
        )
    return (
        EvidenceBand.REJECT,
        f"Разликата Δ={difference:g} cm е извън максималния допуск.",
        threshold,
    )


def _distance_band(
    declared: float | None,
    distance: float | None,
    policy: RecognitionPolicy,
) -> tuple[EvidenceBand, str, str | None]:
    if declared is None or distance is None:
        return EvidenceBand.UNKNOWN, "Разстоянието до надписа не е измерено.", None
    strong = policy.strong_distance_limit(declared)
    primary = policy.primary_search_distance_limit(declared)
    extended = policy.search_distance_limit(declared)
    threshold = (
        f"силна връзка ≤ {strong:g}; основен радиус ≤ {primary:g}; "
        f"втори пръстен ≤ {extended:g}"
    )
    if distance <= strong:
        return (
            EvidenceBand.EXACT,
            f"Геометрията е в силния радиус на надписа ({distance:g}).",
            threshold,
        )
    if distance <= primary:
        return (
            EvidenceBand.CLEAR,
            f"Геометрията е в основния радиус на надписа ({distance:g}).",
            threshold,
        )
    if distance <= extended:
        return (
            EvidenceBand.REVIEW,
            f"Геометрията е само във втория контролен пръстен ({distance:g}).",
            threshold,
        )
    return (
        EvidenceBand.REJECT,
        f"Геометрията е извън максималния радиус ({distance:g}).",
        threshold,
    )


def _separation_band(
    *,
    score_gap: float | None,
    score_advantage_threshold: float,
    distance_gap_raw: float | None,
    distance_advantage_threshold: float,
    dimension_candidate_tiebreak: bool,
    alternative_count: int,
) -> tuple[EvidenceBand, str, str]:
    """Classify candidate separation with the same measurable gates as ranking.

    ``geometry_candidates._confidence_for`` has always allowed three independent
    ways to prove a unique local owner: score advantage, spatial advantage, or
    a strict A–E dimension tiebreak.  Recovery.4's evidence layer looked only at
    score, which reclassified already-strong candidates as MANUAL.  Keep the
    evidence decision aligned with the ranking laws instead of inventing a new
    threshold.
    """

    if (
        alternative_count <= 0
        and score_gap is None
        and distance_gap_raw is None
    ):
        return EvidenceBand.EXACT, "Няма конкурентен кандидат.", "NO_ALTERNATIVE"

    if dimension_candidate_tiebreak:
        return (
            EvidenceBand.CLEAR,
            "Пълното уравнение на размерите A–E разделя водещия кандидат "
            "от близката алтернатива.",
            "DIMENSION_A_E",
        )

    if (
        score_gap is not None
        and score_gap >= max(0.0, float(score_advantage_threshold))
    ):
        return (
            EvidenceBand.CLEAR,
            f"Първият кандидат има измеримо score предимство ({score_gap:g}).",
            "SCORE_GAP",
        )

    if (
        distance_gap_raw is not None
        and distance_gap_raw >= max(0.0, float(distance_advantage_threshold))
    ):
        return (
            EvidenceBand.CLEAR,
            "Първият кандидат има измеримо пространствено предимство "
            f"({distance_gap_raw:g} чертожни единици).",
            "DISTANCE_GAP",
        )

    if score_gap is None:
        return (
            EvidenceBand.REVIEW,
            "Има алтернативи, но score разликата не е записана и няма "
            "достатъчна пространствена или A–E преднина.",
            "INSUFFICIENT_EVIDENCE",
        )
    if score_gap > 0:
        distance_text = (
            f", пространствена преднина {distance_gap_raw:g}"
            if distance_gap_raw is not None
            else ""
        )
        return (
            EvidenceBand.REVIEW,
            "Предимството пред следващия кандидат е недостатъчно "
            f"(score {score_gap:g}{distance_text}).",
            "INSUFFICIENT_EVIDENCE",
        )
    return (
        EvidenceBand.REJECT,
        "Първият кандидат няма положително предимство.",
        "NO_POSITIVE_ADVANTAGE",
    )


_FORM_CLASSIFICATION_EVIDENCE_CODES = frozenset(
    {
        "NO_CANDIDATE",
        "NO_GEOMETRY_HANDLES",
        "TOPOLOGY_MATCH",
        "GEOMETRY_GAP",
    }
)
_GEOMETRY_OWNERSHIP_EVIDENCE_CODES = frozenset(
    {
        "NO_CANDIDATE",
        "NO_GEOMETRY_HANDLES",
        "PARAMETER_LENGTH",
        "TEXT_DISTANCE",
        "CANDIDATE_SEPARATION",
        "LAYER_DIAMETER",
        "LAYER_NOISE",
        "GEOMETRY_OWNERSHIP",
    }
)


def _split_decision_evidence(
    evidence: Sequence[EvidenceItem],
) -> tuple[tuple[EvidenceItem, ...], tuple[EvidenceItem, ...]]:
    """Split shape identity from exact DXF ownership evidence.

    Segment lengths remain useful for deciding which nearby geometry belongs
    to an annotation, but they do not define a parameterised MASTER shape.
    Consequently ``PARAMETER_LENGTH`` belongs to ownership, while topology and
    controlled-gap evidence belong to form classification.
    """

    form = tuple(
        item
        for item in evidence
        if item.code in _FORM_CLASSIFICATION_EVIDENCE_CODES
    )
    ownership = tuple(
        item
        for item in evidence
        if item.code in _GEOMETRY_OWNERSHIP_EVIDENCE_CODES
    )
    return form, ownership


def _aggregate_evidence_band(evidence: Sequence[EvidenceItem]) -> EvidenceBand:
    """Return a deterministic named band without inventing a percentage."""

    if not evidence:
        return EvidenceBand.UNKNOWN
    bands = {item.band for item in evidence}
    if EvidenceBand.UNKNOWN in bands:
        return EvidenceBand.UNKNOWN
    if EvidenceBand.REJECT in bands:
        return EvidenceBand.REJECT
    if EvidenceBand.REVIEW in bands:
        return EvidenceBand.REVIEW
    if bands == {EvidenceBand.EXACT}:
        return EvidenceBand.EXACT
    return EvidenceBand.CLEAR


def review_group_components(row: Mapping[str, Any]) -> dict[str, str]:
    """Return the auditable fields used for conservative review grouping.

    The annotation is intentionally kept byte-for-byte as text.  We do not
    merge labels that merely look similar.  Geometry handles are intentionally
    excluded, because repeated instances of the same form have different DXF
    handles.
    """

    ambiguous = sorted(
        {
            str(value).strip().upper()
            for value in _sequence(row.get("master_ambiguous_shape_codes"))
            if str(value).strip()
        }
    )
    conflict_parts: list[str] = []
    if bool(row.get("master_conflict")) or ambiguous:
        conflict_parts.append(
            "MASTER:" + (",".join(ambiguous) if ambiguous else "YES")
        )
    if bool(row.get("global_conflict")) or bool(
        row.get("geometry_owner_conflict")
    ):
        conflict_parts.append("OWNERSHIP")
    conflict = "+".join(conflict_parts) if conflict_parts else "NONE"
    disposition = str(row.get("matcher_disposition") or "")
    if not disposition or disposition.strip().upper() == "UNCLASSIFIED":
        # Historical v3 reports predate matcher dispositions.  Their
        # ``batch_status`` is the safest available non-merging fallback.
        disposition = str(row.get("batch_status") or "UNCLASSIFIED")
    return {
        "annotation_text": str(row.get("annotation_text") or ""),
        "master_shape_code": str(row.get("master_shape_code") or "").strip().upper(),
        "canonical_signature": str(row.get("canonical_signature") or ""),
        "candidate_layer": str(row.get("candidate_layer") or ""),
        "matcher_disposition": disposition.strip().upper(),
        "conflict": conflict,
    }


def build_review_group_key(row: Mapping[str, Any]) -> str:
    """Build a stable, versioned review-group key from exact evidence."""

    canonical = json.dumps(
        review_group_components(row),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"RG2-{digest}"


def _transform_evidence(candidate: Any | None) -> TransformEvidence:
    rotation_normalized = bool(
        _read(candidate, "orientation_invariant")
        if _read(candidate, "orientation_invariant") is not None
        else _read(candidate, "canonical_signature")
    )
    mirrored = bool(_read(candidate, "master_shape_mirrored"))
    reversed_direction = bool(_read(candidate, "master_shape_reversed"))
    scale = _safe_float(_read(candidate, "uniform_scale_factor"))
    mirror_review = bool(_read(candidate, "mirror_requires_review"))
    transformations: list[str] = []
    if rotation_normalized:
        transformations.append("завъртането е нормализирано")
    else:
        transformations.append("няма записано доказателство за нормализирано завъртане")
    if mirrored:
        transformations.append("намерен е огледален вариант")
    if reversed_direction:
        transformations.append("обходът е от обратния край")
    if scale is not None and not math.isclose(scale, 1.0, abs_tol=1e-9):
        transformations.append(f"равномерният мащаб е {scale:g}×")
    return TransformEvidence(
        rotation_normalized=rotation_normalized,
        # The current matcher compares relative turns and intentionally does not
        # need/store one absolute rotation angle.  ``None`` is honest evidence.
        rotation_deg=None,
        mirrored=mirrored,
        reversed_direction=reversed_direction,
        uniform_scale_factor=scale,
        mirror_requires_review=mirror_review,
        explanation="; ".join(transformations).capitalize() + ".",
    )


def _overall_band(
    evidence: Sequence[EvidenceItem],
    disposition: MatcherDisposition,
) -> EvidenceBand:
    if disposition is MatcherDisposition.UNKNOWN:
        return EvidenceBand.UNKNOWN
    if disposition is MatcherDisposition.MANUAL:
        return EvidenceBand.REVIEW
    core_codes = {
        "TOPOLOGY_MATCH",
        "PARAMETER_LENGTH",
        "TEXT_DISTANCE",
        "CANDIDATE_SEPARATION",
    }
    core_bands = {item.band for item in evidence if item.code in core_codes}
    if core_bands == {EvidenceBand.EXACT}:
        return EvidenceBand.EXACT
    return EvidenceBand.CLEAR


def _summary(
    disposition: MatcherDisposition,
    *,
    master_code: str | None,
    gates: Sequence[SafetyGate],
    calibration: MatcherCalibration | None,
) -> str:
    code = master_code or "неизвестна форма"
    if disposition is MatcherDisposition.AUTO_ELIGIBLE:
        return (
            f"{code}: еднозначно и калибрирано съвпадение — "
            "кандидат за автоматизация; не е записано одобрение."
        )
    if disposition is MatcherDisposition.CONTROL:
        return f"{code}: еднозначен кандидат — контролна извадка."
    if disposition is MatcherDisposition.MANUAL:
        return (
            f"{code}: изисква инженерно решение — "
            f"{gates[0].message if gates else 'доказателствата не са еднозначни'}"
        )
    return (
        f"{code}: недостатъчно или противоречиво доказателство — "
        f"{gates[0].message if gates else 'няма кандидат'}"
    )


def _evidence_by_code(
    evidence: Sequence[EvidenceItem],
    code: str,
) -> EvidenceItem | None:
    return next((item for item in evidence if item.code == code), None)


_REPORT_ALIASES: dict[str, tuple[str, ...]] = {
    "handles": ("candidate_handles",),
    "layer": ("candidate_layer",),
    "score": ("candidate_score",),
    "score_gap": ("candidate_score_gap",),
    "distance_gap_raw": ("candidate_distance_gap_raw",),
    "confidence": ("candidate_confidence",),
    "basis": ("candidate_basis",),
    "notes": ("candidate_notes",),
}


def _read(candidate: Any | None, *names: str) -> Any:
    if candidate is None:
        return None
    expanded: list[str] = []
    for name in names:
        expanded.append(name)
        expanded.extend(_REPORT_ALIASES.get(name, ()))
    if isinstance(candidate, Mapping):
        for name in expanded:
            if name in candidate:
                value = candidate.get(name)
                if value is not None:
                    return value
        return None
    for name in expanded:
        if hasattr(candidate, name):
            value = getattr(candidate, name)
            if value is not None:
                return value
    return None


def _resolve_decision_policy(
    value: MatcherDecisionPolicy | RecognitionPolicy | Mapping[str, Any] | None,
) -> MatcherDecisionPolicy:
    if isinstance(value, MatcherDecisionPolicy):
        return value
    if isinstance(value, RecognitionPolicy):
        return MatcherDecisionPolicy(recognition=value)
    if isinstance(value, Mapping):
        return MatcherDecisionPolicy.from_settings(value)
    return MatcherDecisionPolicy()


def _setting_bool(
    values: Mapping[str, Any],
    key: str,
    default: bool,
) -> bool:
    value = values.get(key, default)
    if isinstance(value, str):
        return value.strip().upper() in {"1", "TRUE", "YES", "ДА", "Y"}
    return bool(value)


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _safe_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _sequence(value: Any) -> tuple[Any, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(value)
    return ()


def _string_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value else ()
    if not isinstance(value, (list, tuple, set, frozenset)):
        return ()
    return tuple(str(item) for item in value if str(item))


def _float_tuple(value: Any) -> tuple[float, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    output: list[float] = []
    for item in value:
        parsed = _safe_float(item)
        if parsed is not None:
            output.append(parsed)
    return tuple(output)
