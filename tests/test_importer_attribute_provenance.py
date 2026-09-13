from __future__ import annotations

from lion_rebar.dxf.importer import _rebar_annotation_from_insert_record
from lion_rebar.dxf.models import DxfEntityRecord


def _attribute(handle: str, tag: str, text: str) -> dict:
    return {
        "handle": handle,
        "tag": tag,
        "text": text,
        "insert": [100.0, 200.0, 0.0],
        "height": 10.0,
        "rotation": 0.0,
        "layer": "ARM_TXT",
        "visual_bbox": [[100.0, 200.0, 0.0], [110.0, 210.0, 0.0]],
    }


def test_duplicate_block_attributes_are_audited_without_changing_legacy_selection():
    record = DxfEntityRecord(
        handle="BLOCK1",
        entity_type="INSERT",
        layer="ARM_TXT",
        geometry={
            "insert": [100.0, 200.0, 0.0],
            "rotation": 0.0,
            "attributes": [
                _attribute("BJ_OLD", "BJ", "12"),
                _attribute("BJ_LAST", "BJ", "4362"),
                _attribute("FI", "FI", "10"),
                _attribute("L", "L", "97"),
            ],
        },
        block_name="ARM_N",
    )

    derived = _rebar_annotation_from_insert_record(record)

    assert derived is not None
    # Compatibility is deliberate: the current application historically uses
    # the last duplicate tag value.  The stabilisation release audits it rather
    # than silently changing 428 already parsed annotations.
    assert derived.text == "4362N10x97"
    assert derived.raw["duplicate_attribute_tags"] == ["BJ"]
    assert derived.raw["attribute_tag_counts"]["BJ"] == 2
    assert [item["value"] for item in derived.raw["all_attribute_values"]["BJ"]] == [
        "12",
        "4362",
    ]
    assert derived.raw["attribute_selection_policy"] == "LAST_VALUE_COMPATIBILITY"


def test_normal_block_attributes_keep_full_provenance():
    record = DxfEntityRecord(
        handle="BLOCK2",
        entity_type="INSERT",
        layer="ARM_TXT",
        geometry={
            "insert": [0.0, 0.0, 0.0],
            "attributes": [
                _attribute("BJ", "BJ", "400"),
                _attribute("FI", "FI", "12"),
                _attribute("L", "L", "300"),
            ],
        },
        block_name="ARM_N",
    )

    derived = _rebar_annotation_from_insert_record(record)

    assert derived is not None
    assert derived.text == "400N12x300"
    assert derived.raw["duplicate_attribute_tags"] == []
    assert derived.raw["attributes"]["BJ"]["value"] == "400"
