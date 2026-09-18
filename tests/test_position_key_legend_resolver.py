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
    _parse_legend_spec,
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
            # Истинският TEXT винаги носи височина (импортерът я записва);
            # по нея ключът се поставя на реда на надписа.
            geometry={"insert": [1097.0, 0.0, 0.0], "height": 12.0},
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
        if layer == "0.4":
            # Височината на ключа в H-3 е 12: от нея импортерът е изчислил
            # отместването на visual_anchor (+11.16, +7.2 при „3b*“).
            geometry["height"] = 12.0
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


def _key_text(handle: str, key: str, x: float, y: float) -> DxfEntityRecord:
    return DxfEntityRecord(
        handle=handle,
        entity_type="TEXT",
        layer="0.4",
        text=key,
        geometry={"insert": [x, y, 0.0], "height": 12.0},
    )


def test_position_key_reads_the_row_key_despite_a_section_bubble_with_the_same_key() -> None:
    """s-11, П.b9: до таблицата „Фусове“ стои балонче 4n от разреза.

    Балончето е на 67 единици от надписа — в радиуса на таблицата, но не на
    реда му. Старото правило броеше кръгчетата с този ключ в радиуса, виждаше
    две и отказваше; 36N16x165 и 22N8x125 оставаха „НЯМА КАНДИДАТ“.
    """

    entities = _fixture_entities()
    entities.insert(-2, _key_text("SECTION-BUBBLE", "4a", 1030.0, 60.0))
    index, policy = _index(entities)

    resolution = resolve_position_key_annotation(entities[-1], index, policy=policy)

    assert resolution is not None
    assert resolution.key == "4A"
    assert resolution.table_circle_handle == "K-TABLE"
    assert resolution.candidate.master_shape_code == "F002"


def test_position_key_refuses_when_the_row_key_differs_from_the_legend() -> None:
    # Редът носи 5a, а Ø и L съвпадат с легендата 4a. Кръгче 4a в радиуса,
    # но извън реда, не е ключ на този надпис: противоречие, не връзка.
    entities = [
        replace(entity, text="5a") if entity.handle == "K-TABLE" else entity
        for entity in _fixture_entities()
    ]
    entities.insert(-2, _key_text("SECTION-BUBBLE", "4a", 1030.0, 60.0))
    index, policy = _index(entities)

    assert resolve_position_key_annotation(entities[-1], index, policy=policy) is None


def test_position_key_refuses_two_keys_on_one_row() -> None:
    entities = _fixture_entities()
    entities.insert(-2, _key_text("SECOND-ON-ROW", "5a", 940.0, 2.0))
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


def _risk_candidate_report(tmp_path, *, override_enabled: bool) -> dict:
    # Прав прът 360 cm на 72 единици: разликата 60 cm е над допуска за ръчен
    # преглед при L=420, но под твърдата граница — кандидатът е „РИСК“.
    entities = _fixture_entities()
    entities.append(_line("LOCAL-WEAK", (1120.0, 40.0), (1480.0, 40.0)))
    engine = _engine()
    settings = deepcopy(engine.settings)
    settings.setdefault("geometry_recognition", {})[
        "position_key_manual_review_override_enabled"
    ] = override_enabled
    result = build_batch_geometry_report(
        entities,
        tmp_path,
        project_name="POSITION_KEY_RISK",
        generated_at=datetime(2026, 9, 17, 12, 0, tzinfo=UTC),
        shape_definitions=engine.shapes,
        control_tests=engine.tests,
        policy=settings,
    )
    payload = json.loads(result.json_path.read_text(encoding="utf-8"))
    return next(
        row
        for row in payload["rows"]
        if row["annotation_handle"] == "PARENT::BJ-FI-L"
    )


def test_position_key_overrides_a_risk_candidate(tmp_path) -> None:
    """s-11, Ш.2,63: 12N18x373 (ключ 4e) и 24N8x373 (ключ 4g).

    Двата надписа тръгваха като „РИСК“ към далечен U-прът от 301 cm, а пасът
    по ключ приемаше само „НЯМА КАНДИДАТ“ и „ЗА РЪЧЕН ПРЕГЛЕД“. После двата се
    сблъскваха в същия прът и оставаха блокирани. Собственик, 2026-09-17:
    „има си позиция с кръгче, така че легендата се гледа“.
    """

    table = _risk_candidate_report(tmp_path, override_enabled=True)

    assert table["position_key_code"] == "4A"
    assert table["position_key_original_batch_status"] == "РИСК"
    assert table["position_key_original_candidate"]["handles"] == ["LOCAL-WEAK"]
    assert table["position_key_manual_override"] is True
    assert table["master_shape_code"] == "F002"
    assert table["candidate_handles"] == ["G1", "G2", "G3"]
    assert table["ownership_reference_only"] is True


def test_position_key_leaves_a_risk_candidate_when_override_is_disabled(tmp_path) -> None:
    table = _risk_candidate_report(tmp_path, override_enabled=False)

    assert table.get("position_key_code") is None
    assert table["batch_status"] == "РИСК"
    assert table["candidate_handles"] == ["LOCAL-WEAK"]


def _with_spec_text(entities, text: str) -> list[DxfEntityRecord]:
    return [
        replace(entity, text=text) if entity.handle == "SPEC" else entity
        for entity in entities
    ]


def test_position_key_legend_spec_accepts_bar_kind_word() -> None:
    """s-11: проектантът пише вида на пръта в легендата — „N14 Фус - L=125“.

    Думата е име, не размер: Ø и L са същите. Без нея цялата легенда на
    фусовете оставаше неразчетена и 14 позиции с верен ключ излизаха
    „НЯМА КАНДИДАТ“ (собственик, 2026-09-16, с отворен „Тип 2“ и кръгче 4j).
    """

    entities = _with_spec_text(_fixture_entities(), "N20 Фус - L=420")
    index, policy = _index(entities)

    assert set(index.entries_by_key) == {"4A"}
    entry = index.entries_by_key["4A"]
    assert entry.legend_spec_text == "N20 Фус - L=420"
    assert (entry.diameter_mm, entry.declared_length_cm) == (20.0, 420.0)
    resolution = resolve_position_key_annotation(entities[-1], index, policy=policy)
    assert resolution is not None
    assert resolution.key == "4A"
    assert resolution.candidate.master_shape_code == "F002"


def test_position_key_bar_kind_word_does_not_relax_the_values() -> None:
    # Думата не бива да отваря вратата за чужди размери: надпис с друга L
    # пак не се свързва с тази легенда.
    entities = _with_spec_text(
        _fixture_entities(annotation_text="134N20x425"), "N20 Фус - L=420"
    )
    index, policy = _index(entities)

    assert resolve_position_key_annotation(entities[-1], index, policy=policy) is None


def test_legend_spec_parser_reads_only_one_name_word() -> None:
    assert _parse_legend_spec("N14 Фус - L=125") == (14.0, 125.0)
    assert _parse_legend_spec("N8 фус. L=100") == (8.0, 100.0)
    # Досегашните изписвания остават.
    assert _parse_legend_spec("N18 L=470") == (18.0, 470.0)
    assert _parse_legend_spec("N18 - L=373") == (18.0, 373.0)
    # Без диаметър, с число в думата или с две думи — не е спецификация.
    assert _parse_legend_spec("Фус - L=125") is None
    assert _parse_legend_spec("N14 Фус2 - L=125") is None
    assert _parse_legend_spec("N14 Фус Стб - L=125") is None


def _dimension(handle, value, first, second, line_point, text_point) -> DxfEntityRecord:
    return DxfEntityRecord(
        handle=handle,
        entity_type="DIMENSION",
        layer="koti",
        geometry={
            "measurement": value,
            "defpoint": [*line_point, 0.0],
            "defpoint2": [*first, 0.0],
            "defpoint3": [*second, 0.0],
            "text_midpoint": [*text_point, 0.0],
        },
    )


def test_legend_f002_reads_its_own_dimensions_despite_the_offset_dimension() -> None:
    """s-11, ключ 4a: легендата котира 110 / 40 / 320 и отместването 4.

    Котата 4 мери напречно на пръта, но досега участваше в A-E тълкуванията
    като L2 или L3. Двусмислието оставяше легендата без размери, а 25-те
    таблични реда получаваха [20, 450] от котите до таблицата.
    """

    entities = _fixture_entities()
    entities.extend(
        [
            # По пръта: оста е хоризонтална, като самите участъци.
            _dimension("D260", 260.0, (0.0, 0.0), (260.0, 0.0), (260.0, -30.0), (130.0, -18.0)),
            _dimension("D40", 40.0, (260.0, 0.0), (300.0, 4.0), (300.0, -30.0), (280.0, -18.0)),
            _dimension("D120", 120.0, (300.0, 4.0), (420.0, 4.0), (420.0, -30.0), (360.0, -18.0)),
            # Напречно: отместването между двете рамена, ос вертикална.
            _dimension("OFFSET4", 4.0, (250.0, 0.0), (310.0, 4.0), (330.0, 4.0), (340.0, 2.0)),
        ]
    )

    index, _policy = _index(entities)

    candidate = index.entries_by_key["4A"].candidate
    assert candidate.master_shape_code == "F002"
    assert tuple(candidate.canonical_segment_lengths_cm) == (260.0, 40.0, 120.0)
    assert "OFFSET4" not in tuple(candidate.dimension_text_handles)


# ---------------------------------------------------------------------------
# Затворено множество: последната свободна двойка
# ---------------------------------------------------------------------------


def _closed_set_entities(
    *,
    second_length: float = 300.0,
    extra: list[DxfEntityRecord] | None = None,
) -> list[DxfEntityRecord]:
    """Легенда с две двойки: първата в радиуса, втората извън него.

    Втората повтаря измереното в „Вертикали до +8.90“: спецификацията стои на
    85 единици от кръгчето си при радиус 60, затова по радиуса не се свързва.
    """

    entities = _fixture_entities()
    entities.extend(
        [
            _line("G4", (3000.0, 0.0), (3000.0 + second_length, 0.0)),
            DxfEntityRecord(
                handle="SPEC-2",
                entity_type="TEXT",
                layer="ARMTXT1",
                text="N16 L=300",
                geometry={"insert": [3000.0, -7.0, 0.0]},
            ),
            DxfEntityRecord(
                handle="K-LEG-2",
                entity_type="TEXT",
                layer="0.4",
                text="5a",
                geometry={"insert": [2915.0, -7.0, 0.0], "height": 12.0},
            ),
        ]
    )
    entities.extend(extra or [])
    return entities


def _key_text(handle: str, key: str, x: float, y: float) -> DxfEntityRecord:
    return DxfEntityRecord(
        handle=handle,
        entity_type="TEXT",
        layer="0.4",
        text=key,
        geometry={"insert": [x, y, 0.0], "height": 12.0},
    )


class TestЗатворенаЛегенда:
    def test_последната_свободна_двойка_се_приема_без_радиуса(self) -> None:
        """Собственик, 2026-09-17: „единствената останала позиция е“.

        Два ключа, две спецификации. Първата двойка се решава в радиуса;
        втората е на 85 единици при радиус 60 и досега оставаше нерешена,
        макар прътът ѝ да е начертан и свободен.
        """

        index, _policy = _index(_closed_set_entities())

        assert sorted(index.entries_by_key) == ["4A", "5A"]
        остатък = index.entries_by_key["5A"]
        assert остатък.closed_set_residual is True
        assert остатък.legend_spec_handle == "SPEC-2"
        assert остатък.legend_circle_handle == "K-LEG-2"
        assert остатък.candidate.handles == ("G4",)
        # Първата двойка не се маркира като остатък.
        assert index.entries_by_key["4A"].closed_set_residual is False

    def test_остатъкът_пак_минава_през_проверките(self) -> None:
        """Прътът е 250 вместо обявените 300 — двойката не се приема.

        Остатъкът избира, доказателството пак проверява. Отказът тук е ценен:
        значи или надписът, или по-ранната връзка е сгрешена.
        """

        index, _policy = _index(_closed_set_entities(second_length=250.0))

        assert sorted(index.entries_by_key) == ["4A"]

    def test_неравен_брой_ключове_и_спецификации_не_е_затворено_множество(
        self,
    ) -> None:
        """Три ключа, две спецификации — свободният ключ не доказва нищо."""

        index, _policy = _index(
            _closed_set_entities(extra=[_key_text("K-LEG-3", "6a", 6000.0, 0.0)])
        )

        assert sorted(index.entries_by_key) == ["4A"]

    def test_два_свободни_ключа_не_дават_остатък(self) -> None:
        """Остатък се допуска най-много един — иначе се верижи."""

        index, _policy = _index(
            _closed_set_entities(
                extra=[
                    _key_text("K-LEG-3", "6a", 6000.0, 0.0),
                    DxfEntityRecord(
                        handle="SPEC-3",
                        entity_type="TEXT",
                        layer="ARMTXT1",
                        text="N12 L=250",
                        geometry={"insert": [6085.0, 0.0, 0.0]},
                    ),
                ]
            )
        )

        assert sorted(index.entries_by_key) == ["4A"]
