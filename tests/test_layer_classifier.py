from __future__ import annotations

from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.session.layer_classifier import (
    classify_layers,
    classification_by_layer,
    filter_analysis_entities,
)


def _record(layer: str, entity_type: str, *, text: str | None = None) -> DxfEntityRecord:
    return DxfEntityRecord(
        handle=f"{layer}-{entity_type}",
        entity_type=entity_type,
        layer=layer,
        text=text,
    )


def test_layer_classifier_keeps_unknown_visible_and_hides_only_certain_noise() -> None:
    report = classify_layers(
        [
            _record("ARM_Bars", "LINE"),
            _record("ARMTXT", "TEXT", text="2N12x700"),
            _record("Defpoints", "POINT"),
            _record("HATCH_BG", "HATCH"),
            _record("Designer_New_Style", "LINE"),
            _record("$Ramka", "LINE"),
            _record("Arm_stremena", "LINE"),
        ]
    )
    roles = classification_by_layer(report)
    assert roles["ARM_Bars"]["role"] == "REBAR_GEOMETRY"
    assert roles["ARMTXT"]["role"] == "REBAR_TEXT"
    assert roles["Defpoints"]["role"] == "NOISE"
    assert roles["Defpoints"]["default_visible"] is False
    assert roles["HATCH_BG"]["role"] == "NOISE"
    assert roles["Designer_New_Style"]["role"] == "UNKNOWN"
    assert roles["Designer_New_Style"]["default_visible"] is True
    assert roles["$Ramka"]["role"] == "FRAME"
    assert roles["Arm_stremena"]["role"] == "STIRRUPS"


def test_layer_classifier_report_is_auditable() -> None:
    report = classify_layers(
        [_record("Defpoints", "POINT"), _record("0", "LINE")],
        source_sha256="abc",
    )
    assert report["schema"] == "lion-rebar-layer-classification/v1"
    assert report["source_sha256"] == "abc"
    assert report["summary"]["layer_count"] == 2
    assert report["summary"]["noise_layer_count"] == 1
    assert report["summary"]["unknown_layer_count"] == 1


def test_only_high_confidence_noise_is_removed_from_analysis() -> None:
    entities = [
        _record("Defpoints", "POINT"),
        _record("Designer_New_Style", "LINE"),
        _record("ARM_Bars", "LINE"),
    ]
    report = classify_layers(entities)
    filtered = filter_analysis_entities(entities, report)
    assert {record.layer for record in filtered} == {"Designer_New_Style", "ARM_Bars"}
