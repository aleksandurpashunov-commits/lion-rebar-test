from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Iterable

from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.dxf.text_parser import looks_like_rebar_text


LAYER_CLASSIFICATION_SCHEMA = "lion-rebar-layer-classification/v1"
CLASSIFIER_VERSION = "1.0"

RELEVANT_ROLES = {
    "REBAR_GEOMETRY",
    "REBAR_TEXT",
    "DIMENSIONS",
    "LEADERS",
    "BEAM_GEOMETRY",
    "STIRRUPS",
    "LEGEND",
    "FRAME",
    "USER_HELPER",
    "UNKNOWN",
}


def classify_layers(
    entities: Iterable[DxfEntityRecord],
    *,
    declared_layers: Iterable[str] | None = None,
    source_sha256: str | None = None,
) -> dict[str, Any]:
    """Classify DXF layers conservatively without changing recognition.

    Only high-confidence NOISE layers are hidden by default. UNKNOWN remains
    visible and relevant so a new designer style can never disappear silently.
    """

    by_layer: dict[str, list[DxfEntityRecord]] = defaultdict(list)
    for entity in entities:
        by_layer[str(entity.layer or "0")].append(entity)
    for layer in declared_layers or ():
        by_layer.setdefault(str(layer), [])

    rows: list[dict[str, Any]] = []
    for layer in sorted(by_layer, key=lambda value: value.casefold()):
        records = by_layer[layer]
        counts = Counter(str(record.entity_type or "") for record in records)
        text_records = [record for record in records if record.text is not None]
        rebar_text_count = sum(
            1 for record in text_records if looks_like_rebar_text(str(record.text or ""))
        )
        role, confidence, reasons = _classify_one(
            layer,
            counts=counts,
            text_count=len(text_records),
            rebar_text_count=rebar_text_count,
            total=len(records),
        )
        relevant = role in RELEVANT_ROLES
        default_visible = not (role == "NOISE" and confidence >= 0.95)
        rows.append(
            {
                "layer": layer,
                "role": role,
                "confidence": round(float(confidence), 3),
                "relevant_for_analysis": relevant,
                "default_visible": default_visible,
                "entity_count": len(records),
                "entity_types": dict(sorted(counts.items())),
                "text_count": len(text_records),
                "rebar_text_count": rebar_text_count,
                "reasons": reasons,
            }
        )

    role_counts = Counter(str(row["role"]) for row in rows)
    return {
        "schema": LAYER_CLASSIFICATION_SCHEMA,
        "classifier_version": CLASSIFIER_VERSION,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_sha256": source_sha256,
        "summary": {
            "layer_count": len(rows),
            "relevant_layer_count": sum(bool(row["relevant_for_analysis"]) for row in rows),
            "noise_layer_count": role_counts.get("NOISE", 0),
            "unknown_layer_count": role_counts.get("UNKNOWN", 0),
            "auto_hidden_layer_count": sum(not bool(row["default_visible"]) for row in rows),
            "role_counts": dict(sorted(role_counts.items())),
        },
        "layers": rows,
    }


def classification_by_layer(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(row.get("layer") or ""): dict(row)
        for row in report.get("layers", [])
        if isinstance(row, dict) and str(row.get("layer") or "")
    }


def high_confidence_noise_layers(
    report: dict[str, Any],
    *,
    minimum_confidence: float = 0.95,
) -> set[str]:
    return {
        str(row.get("layer") or "")
        for row in report.get("layers", [])
        if isinstance(row, dict)
        and str(row.get("role") or "") == "NOISE"
        and float(row.get("confidence") or 0.0) >= minimum_confidence
        and str(row.get("layer") or "")
    }


def filter_analysis_entities(
    entities: Iterable[DxfEntityRecord],
    report: dict[str, Any],
) -> list[DxfEntityRecord]:
    noise = high_confidence_noise_layers(report)
    return [entity for entity in entities if str(entity.layer or "") not in noise]


def _classify_one(
    layer: str,
    *,
    counts: Counter[str],
    text_count: int,
    rebar_text_count: int,
    total: int,
) -> tuple[str, float, list[str]]:
    normalized = _normalize(layer)
    tokens = set(filter(None, re.split(r"[^A-ZА-Я0-9]+", normalized)))
    reasons: list[str] = []

    if _contains_any(normalized, ("DEFPOINT", "HATCH", "SOLIDFILL", "WIPEOUT")):
        return "NOISE", 1.0, ["layer-name: technical/non-production noise"]
    if total and counts.get("HATCH", 0) / max(1, total) >= 0.9:
        return "NOISE", 0.98, ["entity-profile: >=90% HATCH"]
    if _contains_any(normalized, ("RAMKA", "РАМКА", "FRAME", "BORDER", "TITLEBLOCK")):
        return "FRAME", 0.99, ["layer-name: drawing frame/title block"]
    if _contains_any(normalized, ("LEGEND", "ЛЕГЕНД")):
        return "LEGEND", 0.99, ["layer-name: legend"]
    if _contains_any(normalized, ("STREM", "СТРЕМ", "BIGLA", "БИГЛ", "TIES")):
        return "STIRRUPS", 0.99, ["layer-name: stirrup/tie geometry"]
    if _contains_any(normalized, ("ARMTXT", "ARM_TXT", "REBAR_TEXT", "ARMN")):
        return "REBAR_TEXT", 0.99, ["layer-name: rebar annotation"]
    if _contains_any(normalized, ("DIM", "KOT", "КОТ", "COT", "РАЗМЕР")):
        return "DIMENSIONS", 0.96, ["layer-name: dimensions"]
    if counts.get("DIMENSION", 0) and counts.get("DIMENSION", 0) >= max(1, total // 2):
        return "DIMENSIONS", 0.95, ["entity-profile: dimension entities dominate"]
    if _contains_any(normalized, ("LEADER", "MLEADER", "VODACH", "ВОДАЧ")):
        return "LEADERS", 0.97, ["layer-name: leaders"]
    if counts.get("LEADER", 0) and counts.get("LEADER", 0) >= max(1, total // 2):
        return "LEADERS", 0.95, ["entity-profile: leader entities dominate"]
    if _contains_any(normalized, ("GRED", "ГРЕД", "BEAM")):
        return "BEAM_GEOMETRY", 0.96, ["layer-name: beam geometry"]
    if _contains_any(normalized, ("LION", "HELPER", "USER", "AI_", "GREDI")):
        return "USER_HELPER", 0.97, ["layer-name: user/helper layer"]
    if _contains_any(normalized, ("ARM", "REBAR", "АРМ", "FIBI", "ФИБ")):
        if text_count and rebar_text_count >= max(1, text_count // 2):
            return "REBAR_TEXT", 0.94, ["layer-name + text-profile: rebar annotations"]
        return "REBAR_GEOMETRY", 0.94, ["layer-name: reinforcement geometry"]
    if rebar_text_count:
        ratio = rebar_text_count / max(1, text_count)
        if ratio >= 0.5:
            return "REBAR_TEXT", min(0.95, 0.75 + ratio * 0.2), [
                "text-profile: rebar-like texts dominate"
            ]
    if total == 0:
        return "UNKNOWN", 0.3, ["empty layer"]
    if tokens == {"0"} or normalized in {"0", "LAYER0"}:
        return "UNKNOWN", 0.45, ["generic layer; kept visible"]
    return "UNKNOWN", 0.4, ["no safe rule; kept visible and relevant"]


def _normalize(value: str) -> str:
    return str(value or "").strip().upper().replace("$", "")


def _contains_any(value: str, needles: tuple[str, ...]) -> bool:
    return any(needle in value for needle in needles)
