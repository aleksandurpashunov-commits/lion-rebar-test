from __future__ import annotations

import json
from pathlib import Path

from lion_rebar.dxf.batch_geometry_report import _annotation_class
from lion_rebar.dxf.text_parser import parse_rebar_text


def test_all_alfa_annotations_keep_the_v0122_parser_result():
    fixture = Path(__file__).parent / "fixtures" / "alfa_annotation_parser_baseline.json"
    payload = json.loads(fixture.read_text(encoding="utf-8"))

    assert len(payload["rows"]) == 596
    for expected in payload["rows"]:
        parsed = parse_rebar_text(expected["text"] or "")
        assert parsed.count == expected["count"], expected["handle"]
        assert parsed.diameter_mm == expected["diameter_mm"], expected["handle"]
        assert parsed.length_cm == expected["length_cm"], expected["handle"]
        assert _annotation_class(parsed) == expected["annotation_class"], expected["handle"]
