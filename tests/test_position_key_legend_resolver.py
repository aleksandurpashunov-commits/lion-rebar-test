from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from lion_rebar.dxf.batch_geometry_report import build_batch_geometry_report
from lion_rebar.dxf.geometry_candidates import (
    GeometryCandidate,
    GeometryCandidateAnalysis,
    build_geometry_candidate_context,
)
from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.dxf.position_key_resolver import (
    _shared_family_analyses,
    _strict_protection_candidate_handles,
    build_position_key_legend_index,
    resolve_position_key_annotation,
)
from lion_rebar.dxf.recognition_policy import RecognitionPolicy
from lion_rebar.engine.package_io import inspect_engine_bundle
from lion_rebar.engine.shape_index import EngineShapeIndex


def _engine():
    path = (
        Path(__file__).resolve().parents[1]
        / "engine_updates"
        / "lion-rebar-master_v0.3.10_EVIDENCE_TIEBREAK_PRODUCTION_DRAFT.lionengine"
    )
    return inspect_engine_bundle(path)[0]


def _h3_engine():
    path = (
        Path(__file__).resolve().parents[1]
        / "engine_updates"
        / "lion-rebar-master_v0.3.14_F005_INDEPENDENT_ARMS_PRODUCTION_DRAFT.lionengine"
    )
    return inspect_engine_bundle(path)[0]


def _line(handle: str, start, end) -> DxfEntityRecord:
    return DxfEntityRecord(
        handle=handle,
        entity_type="LINE",
        layer="ARM-GORNA",
        geometry={"start": [*start, 0.0], "end": [*end, 0.0]},
    )


def _fixture_entities(*, annotation_text: str = "134N20x420") -> list[DxfEntityRecord]:
    return [
        # One real F002 legend geometry: 260 + sqrt(40^2 + 4^2) + 120.
        _line("G1", (0.0, 0.0), (260.0, 0.0)),
        _line("G2", (260.0, 0.0), (300.0, 4.0)),
        _line("G3", (300.0, 4.0), (420.0, 4.0)),
        DxfEntityRecord(
            handle="K-LEG",
            entity_type="TEXT",
            layer="0.4",
            text="4a",
            geometry={"insert": [-20.0, -20.0, 0.0]},
        ),
        DxfEntityRecord(
            handle="SPEC",
            entity_type="TEXT",
            layer="ARMTXT1",
            text="N20 L=420",
            geometry={"insert": [0.0, -7.0, 0.0]},
        ),
        DxfEntityRecord(
            handle="K-TABLE",
            entity_type="TEXT",
            layer="0.4",
            text="4a",
            geometry={"insert": [1097.0, 0.0, 0.0]},
        ),
        DxfEntityRecord(
            handle="PARENT",
            entity_type="INSERT",
            layer="ARMTXT",
            block_name="ARM_NX",
            geometry={"insert": [1000.0, 0.0, 0.0]},
        ),
        DxfEntityRecord(
            handle="PARENT::BJ-FI-L",
            entity_type="BLOCK_ATTR_TEXT",
            layer="ARMTXT",
            block_name="ARM_NX",
            text=annotation_text,
            geometry={"insert": [1050.0, 0.0, 0.0]},
            raw={
                "source": "INSERT_ATTRIBUTES",
                "parent_insert_handle": "PARENT",
                "block_name": "ARM_NX",
            },
        ),
    ]


def _index(entities, *, engine=None):
    engine = engine or _engine()
    policy = RecognitionPolicy.from_settings(engine.settings)
    context = build_geometry_candidate_context(entities, policy=policy)
    index = build_position_key_legend_index(
        entities,
        shape_definitions=engine.shapes,
        control_tests=engine.tests,
        policy=policy,
        context=context,
        shape_index=EngineShapeIndex.from_shapes(engine.shapes),
    )
    return index, policy


def _h3_starred_shared_f004_entities() -> list[DxfEntityRecord]:
    """Reduced, self-contained H-3 fixture for the compact starred legend."""

    def text(handle, layer, value, insert, visual_anchor):
        geometry = {
            "insert": [*insert, 0.0],
            "visual_anchor": [*visual_anchor, 0.0],
        }
        if layer == "ARMTXT1":
            x, y = insert
            geometry.update(
                {
                    "height": 12.00000417242383,
                    "rotation": 90.0,
                    "estimated_text_height": 14.400005006908597,
                    "estimated_text_width": 81.84002845593052,
                    "visual_bbox": [
                        [x, y, 0.0],
                        [x, y + 81.84002845593052, 0.0],
                        [x - 14.400005006908597, y, 0.0],
                        [x - 14.400005006908597, y + 81.84002845593052, 0.0],
                    ],
                }
            )
        return DxfEntityRecord(
            handle=handle,
            entity_type="TEXT",
            layer=layer,
            text=value,
            geometry=geometry,
        )

    return [
        _line(
            "706D",
            (5154.110902175691, 1628.717378759578),
            (5154.110902175691, 1268.477885285854),
        ),
        _line(
            "7070",
            (5154.110902175691, 1628.717378759578),
            (5174.110909129644, 1628.717378759578),
        ),
        DxfEntityRecord(
            handle="7073",
            entity_type="DIMENSION",
            layer="KOLTXT",
            geometry={
                "defpoint": [5154.110902176858, 1628.717378759579, 0.0],
                "defpoint2": [5154.110902175691, 1268.477885285854, 0.0],
                "defpoint3": [5154.110902175691, 1628.717378759578, 0.0],
                "dimension_style": "KOTI KOL",
                "display_text": "360",
                "measurement": 360.2394934737242,
                "render_primitives": [
                    {
                        "height": 13.5,
                        "insert": [5141.015429735912, 1448.597632022715, 0.0],
                        "kind": "TEXT",
                        "rotation": 90.0,
                        "text": "360",
                    }
                ],
                "text_midpoint": [5141.015429735912, 1448.597632022715, 0.0],
            },
        ),
        text(
            "707A",
            "ARMTXT1",
            "N22 - L=380",
            (5182.920458093669, 1428.555454454468),
            (5175.720455590215, 1469.4754686824333),
        ),
        text(
            "707C",
            "0.4",
            "3a*",
            (5167.589311656128, 1406.930618022254),
            (5178.749293216475, 1414.1306061257033),
        ),
        text(
            "706E",
            "ARMTXT1",
            "N20 - L=380",
            (5213.400670611218, 1428.555454454468),
            (5206.200668107764, 1469.4754686824333),
        ),
        text(
            "7072",
            "0.4",
            "3b*",
            (5198.069524173677, 1406.930618022254),
            (5209.229505734023, 1414.1306061257033),
        ),
        text(
            "7074",
            "ARMTXT1",
            "N12 - L=380",
            (5245.55509513981, 1428.555454454468),
            (5238.355092636356, 1469.4754686824333),
        ),
        text(
            "7076",
            "0.4",
            "3c*",
            (5230.423948370876, 1406.930618022254),
            (5241.583929931223, 1414.1306061257033),
        ),
        text(
            "7077",
            "ARMTXT1",
            "N10 - L=380",
            (5275.905463418143, 1428.555454454468),
            (5268.705460914689, 1469.4754686824333),
        ),
        text(
            "7079",
            "0.4",
            "3d*",
            (5260.574316980485, 1406.930618022254),
            (5271.734298540831, 1414.1306061257033),
        ),
        DxfEntityRecord(
            handle="8909",
            entity_type="INSERT",
            layer="ARMTXT",
            block_name="ARM_NX",
            geometry={"insert": [6363.509277331793, 3591.658762537347, 0.0]},
        ),
        DxfEntityRecord(
            handle="8909::BJ-FI-L",
            entity_type="BLOCK_ATTR_TEXT",
            layer="ARMTXT",
            block_name="ARM_NX",
            text="12N20x380",
            geometry={"insert": [6398.283621680141, 3584.908760190357, 0.0]},
            raw={
                "source": "INSERT_ATTRIBUTES",
                "parent_insert_handle": "8909",
                "block_name": "ARM_NX",
            },
        ),
        text(
            "8910",
            "0.4",
            "3b*",
            (6333.116210988219, 3586.861398305313),
            (6344.276214868573, 3594.0614008087673),
        ),
    ]


def test_position_key_resolves_remote_f002_with_full_audit_chain() -> None:
    entities = _fixture_entities()
    index, policy = _index(entities)

    assert set(index.entries_by_key) == {"4A"}
    entry = index.entries_by_key["4A"]
    assert entry.legend_spec_handle == "SPEC"
    assert entry.legend_circle_handle == "K-LEG"
    assert entry.occurrence_count == 2
    assert entry.candidate.master_shape_code == "F002"
    assert entry.candidate.handles == ("G1", "G2", "G3")

    resolution = resolve_position_key_annotation(
        entities[-1],
        index,
        policy=policy,
    )
    assert resolution is not None
    assert resolution.code == "POSITION_KEY_LEGEND_REFERENCE"
    assert resolution.key == "4A"
    assert resolution.table_circle_handle == "K-TABLE"
    assert resolution.table_circle_distance_raw == 97.0
    assert resolution.candidate.master_shape_code == "F002"


def test_h3_starred_family_resolves_shared_f004_and_protects_handles() -> None:
    entities = _h3_starred_shared_f004_entities()
    index, policy = _index(entities, engine=_h3_engine())

    assert {"3A*", "3B*", "3C*", "3D*"} <= set(index.entries_by_key)
    for key, expected_diameter in (
        ("3A*", 22.0),
        ("3B*", 20.0),
        ("3C*", 12.0),
        ("3D*", 10.0),
    ):
        entry = index.entries_by_key[key]
        assert entry.diameter_mm == expected_diameter
        assert entry.probe_annotation.text == f"1N{int(expected_diameter)}x380"
        assert entry.candidate.master_shape_code == "F004"
        assert entry.candidate.handles == ("706D", "7070")
        assert entry.candidate.canonical_segment_lengths_cm == (360.0, 20.0)
        assert "POSITION_KEY_LEGEND_SHARED_GEOMETRY_FAMILY" in entry.candidate.basis

    assert index.protected_geometry_handles == frozenset({"706D", "7070"})
    annotation = next(
        entity for entity in entities if entity.handle == "8909::BJ-FI-L"
    )
    resolution = resolve_position_key_annotation(annotation, index, policy=policy)
    assert resolution is not None
    assert resolution.key == "3B*"
    assert resolution.table_circle_handle == "8910"
    assert resolution.candidate.master_shape_code == "F004"
    assert resolution.candidate.handles == ("706D", "7070")


def test_position_key_rejects_duplicate_legend_spec() -> None:
    entities = _fixture_entities()
    entities.append(
        DxfEntityRecord(
            handle="SPEC-DUP",
            entity_type="TEXT",
            layer="ARMTXT1",
            text="N20 L=420",
            geometry={"insert": [5.0, -7.0, 0.0]},
        )
    )

    index, _policy = _index(entities)

    assert "4A" not in index.entries_by_key
    assert index.protected_geometry_handles == frozenset({"G1", "G2", "G3"})


def _exact_candidate(
    handles: tuple[str, ...],
    *,
    rank: int,
    distance: float,
    far: bool = False,
    scale: float | None = None,
) -> GeometryCandidate:
    return GeometryCandidate(
        rank=rank,
        handles=handles,
        layer="ARM-GORNA",
        segment_count=2,
        geometry_length_raw=380.0,
        declared_length_cm=380.0,
        numeric_difference=0.0,
        distance_raw=distance,
        layer_diameter_mm=None,
        diameter_layer_match=False,
        score=float(rank),
        score_gap=10.0,
        distance_gap_raw=10.0,
        confidence=(
            "ДАЛЕЧНА ВРЪЗКА — РЪЧНА ПРОВЕРКА"
            if far
            else "СИЛЕН КАНДИДАТ — НЕОДОБРЕН"
        ),
        basis="adversarial fixture",
        master_shape_code="F004",
        canonical_signature="L",
        canonical_segment_lengths_cm=(360.0, 20.0),
        raw_numeric_difference=0.0,
        uniform_scale_factor=scale,
        review_reason_codes=("FAR_ASSOCIATION",) if far else (),
    )


def _analysis(handle: str, candidates) -> GeometryCandidateAnalysis:
    return GeometryCandidateAnalysis(
        annotation_handle=handle,
        label="1N20x380",
        diameter_mm=20.0,
        declared_length_cm=380.0,
        candidates=tuple(candidates),
        status="fixture",
    )


def test_shared_family_does_not_override_distinct_exact_top() -> None:
    policy = RecognitionPolicy.from_settings(_h3_engine().settings)
    circle_a = DxfEntityRecord(
        handle="C-A",
        entity_type="TEXT",
        layer="0.4",
        text="3a*",
        geometry={"insert": [0.0, 0.0, 0.0]},
    )
    circle_b = replace(circle_a, handle="C-B", text="3b*")
    spec_a = DxfEntityRecord(
        handle="S-A",
        entity_type="TEXT",
        layer="ARMTXT1",
        text="N22 - L=380",
        geometry={"insert": [0.0, 0.0, 0.0]},
    )
    spec_b = replace(
        spec_a,
        handle="S-B",
        text="N20 - L=380",
        geometry={"insert": [30.0, 0.0, 0.0]},
    )
    shared_a = _exact_candidate(("SHARED-1", "SHARED-2"), rank=1, distance=5.0)
    own_b = _exact_candidate(
        ("OWN-B-1", "OWN-B-2"),
        rank=1,
        distance=4.0,
        scale=1.0,
    )
    shared_b = _exact_candidate(
        ("SHARED-1", "SHARED-2"),
        rank=2,
        distance=90.0,
        far=True,
    )
    associations = {
        "3A*": (circle_a, spec_a, 22.0, 380.0),
        "3B*": (circle_b, spec_b, 20.0, 380.0),
    }
    analyses = {
        "3A*": _analysis("A", (shared_a,)),
        "3B*": _analysis("B", (own_b, shared_b)),
    }

    promoted = _shared_family_analyses(
        associations,
        analyses,
        policy=policy,
    )

    assert promoted == {}
    assert analyses["3B*"].candidates[0].handles == ("OWN-B-1", "OWN-B-2")


def test_protection_rejects_two_distinct_exact_candidate_identities() -> None:
    policy = RecognitionPolicy.from_settings(_h3_engine().settings)
    analysis = _analysis(
        "AMBIGUOUS",
        (
            _exact_candidate(("G1", "G2"), rank=1, distance=5.0),
            _exact_candidate(("OTHER-1", "OTHER-2"), rank=2, distance=7.0),
        ),
    )

    assert _strict_protection_candidate_handles(
        analysis,
        declared_length=380.0,
        policy=policy,
    ) == frozenset()


def _radius_fixture(*, specs, keys) -> list[DxfEntityRecord]:
    entities = [_line("RADIUS-GEOMETRY", (59.0, 0.0), (479.0, 0.0))]
    entities.extend(
        DxfEntityRecord(
            handle=handle,
            entity_type="TEXT",
            layer="ARMTXT1",
            text="N20 L=420",
            geometry={"insert": [x, 0.0, 0.0]},
        )
        for handle, x in specs
    )
    entities.extend(
        DxfEntityRecord(
            handle=handle,
            entity_type="TEXT",
            layer="0.4",
            text=key,
            geometry={"insert": [x, 0.0, 0.0]},
        )
        for handle, key, x in keys
    )
    return entities


def test_position_key_rejects_spec_runner_up_just_outside_radius() -> None:
    entities = _radius_fixture(
        specs=(("SPEC-59", 59.0), ("SPEC-61", 61.0)),
        keys=(("KEY", "4a", 0.0),),
    )

    index, _policy = _index(entities)

    assert "4A" not in index.entries_by_key


def test_position_key_rejects_key_runner_up_just_outside_radius() -> None:
    entities = _radius_fixture(
        specs=(("SPEC", 0.0),),
        keys=(("KEY-59", "4a", 59.0), ("KEY-61", "5a", 61.0)),
    )

    index, _policy = _index(entities)

    assert not index.entries_by_key


def test_position_key_does_not_accept_lone_candidate_outside_radius() -> None:
    entities = _radius_fixture(
        specs=(("SPEC-61", 61.0),),
        keys=(("KEY", "4a", 0.0),),
    )

    index, _policy = _index(entities)

    assert "4A" not in index.entries_by_key


def test_position_key_accepts_exact_five_unit_separation_boundary() -> None:
    entities = _radius_fixture(
        specs=(("SPEC-59", 59.0), ("SPEC-64", 64.0)),
        keys=(("KEY", "4a", 0.0),),
    )

    index, _policy = _index(entities)

    assert set(index.entries_by_key) == {"4A"}


def test_position_key_rejects_mutual_nearest_key_tie() -> None:
    entities = _fixture_entities()
    entities.append(
        DxfEntityRecord(
            handle="K-COMPETING",
            entity_type="TEXT",
            layer="0.4",
            text="5a",
            geometry={"insert": [20.0, -20.0, 0.0]},
        )
    )

    index, _policy = _index(entities)

    assert "4A" not in index.entries_by_key
    assert "5A" not in index.entries_by_key


def test_position_key_requires_exact_table_diameter_and_length() -> None:
    entities = _fixture_entities(annotation_text="134N20x421")
    index, policy = _index(entities)

    assert resolve_position_key_annotation(entities[-1], index, policy=policy) is None


def test_batch_report_recovers_reference_without_claiming_local_ownership(
    tmp_path,
) -> None:
    entities = _fixture_entities()
    engine = _engine()

    result = build_batch_geometry_report(
        entities,
        tmp_path,
        project_name="POSITION_KEY_INTEGRATION",
        generated_at=datetime(2026, 8, 2, 12, 0, tzinfo=UTC),
        shape_definitions=engine.shapes,
        control_tests=engine.tests,
        policy=engine.settings,
        engine_metadata={"engine_version": engine.version},
    )
    payload = json.loads(result.json_path.read_text(encoding="utf-8"))
    row = next(
        value
        for value in payload["rows"]
        if value["annotation_handle"] == "PARENT::BJ-FI-L"
    )

    assert result.position_key_legend_resolved_count == 1
    assert result.no_candidate_count == 0
    assert result.geometry_strong_count == 1
    assert row["batch_status"] == "СИЛЕН ПО ГЕОМЕТРИЯ"
    assert row["master_shape_code"] == "F002"
    assert row["candidate_handles"] == ["G1", "G2", "G3"]
    assert row["position_key_code"] == "4A"
    assert row["position_key_legend_reference"] is True
    assert row["ownership_reference_only"] is True
    assert row["geometry_owner_conflict"] is False
    assert "NO_CANDIDATE" not in row["matcher_blocker_codes"]


def _manual_candidate_conflict_fixture() -> list[DxfEntityRecord]:
    entities = _fixture_entities()
    # Exact local F001, deliberately outside the table annotation's primary
    # radius but close to a second annotation.  Without the legend priority the
    # two rows claim the same handle and remain an ownership conflict.
    entities.extend(
        [
            _line("LOCAL-F001", (1200.0, 0.0), (1620.0, 0.0)),
            DxfEntityRecord(
                handle="LOCAL-OWNER",
                entity_type="TEXT",
                layer="ARM_TXT",
                text="1N20x420",
                geometry={"insert": [1200.0, 0.0, 0.0], "height": 3.0},
            ),
        ]
    )
    return entities


def test_position_key_manual_override_precedes_local_ownership(tmp_path) -> None:
    entities = _manual_candidate_conflict_fixture()
    engine = _engine()
    settings = deepcopy(engine.settings)
    settings.setdefault("geometry_recognition", {})[
        "position_key_manual_review_override_enabled"
    ] = True

    result = build_batch_geometry_report(
        entities,
        tmp_path,
        project_name="POSITION_KEY_MANUAL_PRIORITY",
        generated_at=datetime(2026, 8, 3, 12, 0, tzinfo=UTC),
        shape_definitions=engine.shapes,
        control_tests=engine.tests,
        policy=settings,
    )
    payload = json.loads(result.json_path.read_text(encoding="utf-8"))
    rows = {row["annotation_handle"]: row for row in payload["rows"]}
    table = rows["PARENT::BJ-FI-L"]
    local_owner = rows["LOCAL-OWNER"]

    assert result.position_key_manual_override_count == 1
    assert result.ownership_conflict_count == 0
    assert table["position_key_manual_override"] is True
    assert table["position_key_original_batch_status"] == "ЗА РЪЧЕН ПРЕГЛЕД"
    assert table["position_key_original_candidate"]["handles"] == ["LOCAL-F001"]
    assert table["master_shape_code"] == "F002"
    assert table["ownership_reference_only"] is True
    assert local_owner["master_shape_code"] == "F001"
    assert local_owner["batch_status"] in {"СИЛЕН", "СИЛЕН ПО ГЕОМЕТРИЯ"}


def test_position_key_does_not_override_manual_candidate_when_disabled(tmp_path) -> None:
    entities = _manual_candidate_conflict_fixture()
    engine = _engine()

    result = build_batch_geometry_report(
        entities,
        tmp_path,
        project_name="POSITION_KEY_MANUAL_DISABLED",
        generated_at=datetime(2026, 8, 3, 12, 1, tzinfo=UTC),
        shape_definitions=engine.shapes,
        control_tests=engine.tests,
        policy=engine.settings,
    )
    payload = json.loads(result.json_path.read_text(encoding="utf-8"))
    table = next(
        row
        for row in payload["rows"]
        if row["annotation_handle"] == "PARENT::BJ-FI-L"
    )

    assert result.position_key_manual_override_count == 0
    assert table.get("position_key_manual_override") is not True
    assert table["master_shape_code"] == "F001"
    assert table["batch_status"] == "ЗА РЪЧЕН ПРЕГЛЕД"
