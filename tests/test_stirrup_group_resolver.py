from __future__ import annotations

import math
from pathlib import Path

from lion_rebar.dxf.geometry_candidates import build_geometry_candidate_context
from lion_rebar.dxf.geometry_core_v2 import polyline_path_from_geometry
from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.dxf.recognition_policy import RecognitionPolicy
from lion_rebar.dxf.stirrup_group_resolver import (
    resolve_stirrup_length_group_annotation,
)
from lion_rebar.engine.package_io import inspect_engine_bundle
from lion_rebar.engine.shape_index import EngineShapeIndex


def _engine():
    path = (
        Path(__file__).resolve().parents[1]
        / "engine_updates"
        / "lion-rebar-master_v0.3.11_ARC_LENGTH_STIRRUP_GROUP_PRODUCTION_DRAFT.lionengine"
    )
    return inspect_engine_bundle(path)[0]


def _stirrup(handle: str, *, dy: float) -> DxfEntityRecord:
    raw = [
        [11.1590072210, 11.9078989661, 0.0],
        [4.3251418444, 19.2084663422, 0.6801808649],
        [0.0, 17.5, 0.0],
        [0.0, 2.5, math.tan(math.radians(90.0) / 4.0)],
        [2.5, 0.0, 0.0],
        [47.5, 0.0, math.tan(math.radians(90.0) / 4.0)],
        [50.0, 2.5, 0.0],
        [50.0, 17.5, math.tan(math.radians(90.0) / 4.0)],
        [47.5, 20.0, 0.0],
        [2.5, 20.0, 0.6563080394],
        [0.6748581559, 15.7915336538, 0.0],
        [7.5087235328, 8.4909662777, 0.0],
    ]
    points = [[x, y + dy, bulge] for x, y, bulge in raw]
    return DxfEntityRecord(
        handle=handle,
        entity_type="LWPOLYLINE",
        layer="Stremena",
        geometry={"points": points, "closed": False},
    )


def _annotation(text: str) -> DxfEntityRecord:
    return DxfEntityRecord(
        handle="ANN",
        entity_type="TEXT",
        layer="ARM_TXT",
        text=text,
        geometry={"insert": [25.0, 0.0, 0.0], "height": 2.5},
    )


def _resolve(entities, annotation, *, policy=None):
    engine = _engine()
    resolved_policy = policy or RecognitionPolicy.from_settings(engine.settings)
    context = build_geometry_candidate_context(entities, policy=resolved_policy)
    return resolve_stirrup_length_group_annotation(
        annotation,
        entities,
        shape_definitions=engine.shapes,
        control_tests=engine.tests,
        policy=resolved_policy,
        context=context,
        shape_index=EngineShapeIndex.from_shapes(engine.shapes),
    )


def _declared_length() -> int:
    path = polyline_path_from_geometry(_stirrup("S", dy=0.0).geometry)
    assert path is not None
    return round(path.exact_length_raw)


def test_unique_remote_stirrup_is_selected_by_exact_arc_length() -> None:
    geometry = _stirrup("S1", dy=100.0)
    annotation = _annotation(f"25N8x{_declared_length()}")

    resolution = _resolve([annotation, geometry], annotation)

    assert resolution is not None
    assert resolution.code == "STIRRUP_EXACT_ARC_LENGTH_UNIQUE"
    assert resolution.member_handles == ("S1",)
    assert resolution.candidate.master_shape_code == "F006"


def test_repeated_row_is_one_group_candidate_not_two_ambiguous_objects() -> None:
    first = _stirrup("S1", dy=100.0)
    second = _stirrup("S2", dy=150.0)
    annotation = _annotation(f"25N8x{_declared_length()}")

    resolution = _resolve([annotation, first, second], annotation)

    assert resolution is not None
    assert resolution.code == "STIRRUP_EXACT_ARC_LENGTH_GROUP"
    assert set(resolution.member_handles) == {"S1", "S2"}
    assert resolution.declared_count == 25


def test_two_length_matches_outside_one_row_remain_unresolved() -> None:
    first = _stirrup("S1", dy=70.0)
    second = _stirrup("S2", dy=220.0)
    annotation = _annotation(f"25N8x{_declared_length()}")
    policy = RecognitionPolicy(stirrup_length_search_distance_raw=260.0)

    resolution = _resolve([annotation, first, second], annotation, policy=policy)

    assert resolution is None


def test_group_larger_than_annotation_count_is_rejected() -> None:
    first = _stirrup("S1", dy=100.0)
    second = _stirrup("S2", dy=150.0)
    annotation = _annotation(f"1N8x{_declared_length()}")

    resolution = _resolve([annotation, first, second], annotation)

    assert resolution is None


# ---------------------------------------------------------------------------
# Рамката на елемента: една форма в рамката е доказателство (s-11, 2026-09-16)
# ---------------------------------------------------------------------------

from datetime import UTC, datetime  # noqa: E402

from lion_rebar.dxf.batch_geometry_report import build_batch_geometry_report  # noqa: E402
from lion_rebar.dxf.stirrup_group_resolver import (  # noqa: E402
    resolve_stirrup_operator_area_annotation,
)
from lion_rebar.export.operator_areas import KIND_GROUP, OperatorArea  # noqa: E402

# Надписът стои далеч от разрезите — като в s-11, на 1–2,5 m встрани.
_FAR_ANNOTATION_Y = -300.0
_AREA_BOUNDS = (-50.0, -350.0, 100.0, 450.0)


def _wider_lower_stirrup(handle: str, *, dy: float) -> DxfEntityRecord:
    """Същата дъгова L и същата кука, друга форма: 53 x 17 вместо 50 x 20.

    Дясната страна се мести с +3, долната — нагоре с 3: горе и долу
    +3 cm, отляво и отдясно −3 cm. Куката в горния ляв ъгъл не се пипа.
    """

    record = _stirrup(handle, dy=0.0)
    points = []
    for x, y, bulge in record.geometry["points"]:
        points.append([x + 3.0 if x > 25.0 else x, (y + 3.0 if y < 5.0 else y) + dy, bulge])
    return DxfEntityRecord(
        handle=handle,
        entity_type="LWPOLYLINE",
        layer="Stremena",
        geometry={"points": points, "closed": False},
    )


def _far_annotation(text: str) -> DxfEntityRecord:
    return DxfEntityRecord(
        handle="ANN",
        entity_type="TEXT",
        layer="ARM_TXT",
        text=text,
        geometry={"insert": [25.0, _FAR_ANNOTATION_Y, 0.0], "height": 2.5},
    )


def _area_resolve(entities, annotation, bounds=_AREA_BOUNDS):
    engine = _engine()
    policy = RecognitionPolicy.from_settings(engine.settings)
    context = build_geometry_candidate_context(entities, policy=policy)
    return resolve_stirrup_operator_area_annotation(
        annotation,
        entities,
        area_bounds=bounds,
        shape_definitions=engine.shapes,
        control_tests=engine.tests,
        policy=policy,
        context=context,
        shape_index=EngineShapeIndex.from_shapes(engine.shapes),
    )


def _group_area(bounds=_AREA_BOUNDS) -> OperatorArea:
    x0, y0, x1, y1 = bounds
    return OperatorArea(
        area_id="A1", x0=x0, y0=y0, x1=x1, y1=y1, kind=KIND_GROUP,
        title_handle="T1", title_text="Стб.Я.3 - (Сх.арм.2 )", frame_index=1,
        decided_by="Александър", note="колоната с разрезите",
        at="2026-09-16T00:00:00+00:00", project_id="P", source_sha256="S",
        source_generation_id="G", analysis_run_id="R",
    )


def test_operator_area_resolves_a_distant_callout_when_every_copy_is_one_shape() -> None:
    first = _stirrup("S1", dy=100.0)
    second = _stirrup("S2", dy=400.0)
    annotation = _far_annotation(f"72N8x{_declared_length()}")
    entities = [annotation, first, second]

    # Радиусът не стига до разрезите, а копията не са в една редица.
    assert _resolve(entities, annotation) is None

    resolution = _area_resolve(entities, annotation)

    assert resolution is not None
    assert resolution.code == "STIRRUP_OPERATOR_AREA_CONGRUENT"
    assert set(resolution.member_handles) == {"S1", "S2"}
    assert resolution.representative_handle == "S1"
    assert resolution.candidate.master_shape_code == "F006"


def test_operator_area_refuses_two_different_shapes_of_the_same_length() -> None:
    first = _stirrup("S1", dy=100.0)
    other = _wider_lower_stirrup("S2", dy=400.0)
    annotation = _far_annotation(f"72N8x{_declared_length()}")

    # Всяко от двете само по себе си е валидно стреме с тази L — иначе
    # отказът по-долу нямаше да доказва нищо.
    alone = _area_resolve([annotation, other], annotation)
    assert alone is not None and alone.member_handles == ("S2",)

    assert _area_resolve([annotation, first, other], annotation) is None


def test_operator_area_ignores_a_different_shape_outside_the_frame() -> None:
    first = _stirrup("S1", dy=100.0)
    outside = _wider_lower_stirrup("S2", dy=900.0)
    annotation = _far_annotation(f"72N8x{_declared_length()}")

    resolution = _area_resolve([annotation, first, outside], annotation)

    assert resolution is not None
    assert resolution.member_handles == ("S1",)


def test_batch_report_resolves_the_stirrup_through_the_drawn_frame(tmp_path) -> None:
    """Целият анализ: без рамка — „НЯМА КАНДИДАТ“, с рамка — решено."""

    engine = _engine()
    annotation = _far_annotation(f"72N8x{_declared_length()}")
    entities = [annotation, _stirrup("S1", dy=100.0), _stirrup("S2", dy=400.0)]

    def row_for(operator_areas):
        result = build_batch_geometry_report(
            entities,
            tmp_path / str(len(operator_areas)),
            project_name="OPERATOR_AREA_STIRRUP",
            generated_at=datetime(2026, 9, 16, 12, 0, tzinfo=UTC),
            shape_definitions=engine.shapes,
            control_tests=engine.tests,
            policy=engine.settings,
            operator_areas=operator_areas,
        )
        import json

        payload = json.loads(result.json_path.read_text(encoding="utf-8"))
        return next(row for row in payload["rows"] if row["annotation_handle"] == "ANN")

    without_frame = row_for(())
    with_frame = row_for((_group_area(),))

    assert without_frame["batch_status"] == "НЯМА КАНДИДАТ"
    assert with_frame["batch_status"] != "НЯМА КАНДИДАТ"
    assert with_frame["analysis_status"] == "РАЗРЕШЕНО ЧРЕЗ РАМКА И ЕДНАКВИ СТРЕМЕНА"
    assert with_frame["stirrup_length_group_resolution_code"] == "STIRRUP_OPERATOR_AREA_CONGRUENT"
    assert with_frame["ownership_reference_only"] is True
    assert with_frame["stirrup_operator_area_title"] == "Стб.Я.3 - (Сх.арм.2 )"
    assert set(with_frame["stirrup_length_group_member_handles"]) == {"S1", "S2"}


def _mirrored_stirrup(handle: str, *, dy: float) -> DxfEntityRecord:
    """Същото стреме, начертано огледално — куката е в десния горен ъгъл."""

    record = _stirrup(handle, dy=0.0)
    points = [[50.0 - x, y + dy, -bulge] for x, y, bulge in record.geometry["points"]]
    return DxfEntityRecord(
        handle=handle,
        entity_type="LWPOLYLINE",
        layer="Stremena",
        geometry={"points": points, "closed": False},
    )


def test_operator_area_mirrored_copy_is_the_same_shape() -> None:
    """s-11: копията в различните разрези са чертани огледално и в обратна посока.

    Участъците са същите (14,8 / 15,4 / 22,4 / 89,5 / 18,8 / 92,6), но
    отпечатъкът ги подрежда от различно място. Сравнение участък по участък
    ги обявяваше за различни форми и 11 позиции оставаха без кандидат.
    """

    first = _stirrup("S1", dy=100.0)
    mirrored = _mirrored_stirrup("S2", dy=400.0)
    annotation = _far_annotation(f"72N8x{_declared_length()}")

    resolution = _area_resolve([annotation, first, mirrored], annotation)

    assert resolution is not None
    assert set(resolution.member_handles) == {"S1", "S2"}


def test_operator_area_accepts_the_overlapping_copy_the_matcher_confirms() -> None:
    """s-11, Ш.7: две от осемте копия са начертани почти едно върху друго.

    Сондата в центъра на представителя връща съседното копие (на 3,7
    единици), а не него — и позицията оставаше без кандидат. Щом всички копия
    са една форма, потвърденото копие е същото стреме и става представител.
    """

    lower = _stirrup("S1", dy=100.0)
    overlapping = _stirrup("S2", dy=108.0)
    annotation = _far_annotation(f"144N8x{_declared_length()}")

    resolution = _area_resolve([annotation, lower, overlapping], annotation)

    assert resolution is not None
    assert set(resolution.member_handles) == {"S1", "S2"}
    assert resolution.candidate.handles == (resolution.representative_handle,)


def _stirrup_of(handle: str, width: float, height: float, *, dy: float) -> DxfEntityRecord:
    """Стреме width × height с кука 10 и радиус 2,5, начертано като копията в s-11.

    Същият шаблон като ``_stirrup`` (50 × 20): куката е в горния ляв ъгъл и
    нейните върхове зависят само от този ъгъл.
    """

    quarter = math.tan(math.radians(90.0) / 4.0)
    raw = [
        [11.1590072210, height - 8.0921010339, 0.0],
        [4.3251418444, height - 0.7915336578, 0.6801808649],
        [0.0, height - 2.5, 0.0],
        [0.0, 2.5, quarter],
        [2.5, 0.0, 0.0],
        [width - 2.5, 0.0, quarter],
        [width, 2.5, 0.0],
        [width, height - 2.5, quarter],
        [width - 2.5, height, 0.0],
        [2.5, height, 0.6563080394],
        [0.6748581559, height - 4.2084663462, 0.0],
        [7.5087235328, height - 11.5090337223, 0.0],
    ]
    return DxfEntityRecord(
        handle=handle,
        entity_type="LWPOLYLINE",
        layer="Stremena",
        geometry={"points": [[x, y + dy, bulge] for x, y, bulge in raw], "closed": False},
    )


def test_operator_area_matches_the_label_by_the_production_equation() -> None:
    """s-11, Стб.Я.1–4: 10 надписа N8x100 оставаха „НЯМА КАНДИДАТ“.

    Стремето е 20 × 20 с кука 10: 2·(20 + 20 + 10) = 100 точно, а оста му
    минава през огъвките и е 103,56. Рамката сравняваше оста с L ±2 cm и не
    намираше нито едно копие. Проектантът пише L = 2·(a + b + кука) + добавка
    0–8 cm; копието отговаря на надписа, когато производственото уравнение го
    приема. Собственик, 2026-09-17: „да, приемаме го така, дадено е от
    проектанта“.
    """

    first = _stirrup_of("S1", 20.0, 20.0, dy=100.0)
    second = _stirrup_of("S2", 20.0, 20.0, dy=400.0)
    path = polyline_path_from_geometry(first.geometry)
    assert path is not None and abs(path.exact_length_raw - 103.56) < 0.01
    annotation = _far_annotation("144N8x100")

    resolution = _area_resolve([annotation, first, second], annotation)

    assert resolution is not None
    assert resolution.code == "STIRRUP_OPERATOR_AREA_CONGRUENT"
    assert set(resolution.member_handles) == {"S1", "S2"}
    assert resolution.candidate.master_shape_code == "F006"


def test_operator_area_leaves_out_copies_the_label_cannot_produce() -> None:
    """s-11, Стб.Я.1: в разреза на фланеца същите стремена са начертани 22,1 × 20.

    2·(22 + 20 + 10) = 104 надвишава L=100 — не е добавка за огъване, а
    друго стреме, затова не участва. Остават копията 20 × 20 от детайлите.
    """

    detail = _stirrup_of("DETAIL", 20.0, 20.0, dy=100.0)
    section = _stirrup_of("SECTION", 22.1, 20.0, dy=250.0)
    web = _stirrup_of("WEB", 20.0, 20.0, dy=400.0)
    annotation = _far_annotation("72N8x100")

    resolution = _area_resolve([annotation, detail, section, web], annotation)

    assert resolution is not None
    assert set(resolution.member_handles) == {"DETAIL", "WEB"}


def test_operator_area_refuses_two_shapes_that_both_close_the_equation() -> None:
    # 20 × 22 дава 104 (добавка 6), 20 × 25 дава 110 (добавка 0): и двете
    # отговарят на L=110, но са различни стремена — отказ, не избор.
    narrow = _stirrup_of("S1", 22.0, 20.0, dy=100.0)
    wide = _stirrup_of("S2", 25.0, 20.0, dy=400.0)
    annotation = _far_annotation("72N8x110")

    assert _area_resolve([annotation, narrow, wide], annotation) is None


def _table_label(text: str) -> list[DxfEntityRecord]:
    """Табличен ред „Надл.арм.“ с кръгче на реда, както в s-11 (ключ 4d)."""

    return [
        DxfEntityRecord(
            handle="TABLE",
            entity_type="INSERT",
            layer="ARMTXT",
            block_name="ARM_NX",
            geometry={"insert": [25.0, _FAR_ANNOTATION_Y, 0.0], "rotation": 0.0},
        ),
        DxfEntityRecord(
            handle="TABLE::BJ-FI-L",
            entity_type="BLOCK_ATTR_TEXT",
            layer="ARMTXT",
            block_name="ARM_NX",
            text=text,
            geometry={"insert": [50.0, _FAR_ANNOTATION_Y - 3.0, 0.0], "rotation": 0.0},
            raw={
                "source": "INSERT_ATTRIBUTES",
                "parent_insert_handle": "TABLE",
                "block_name": "ARM_NX",
            },
        ),
        DxfEntityRecord(
            handle="ROW-KEY",
            entity_type="TEXT",
            layer="0.4",
            text="4d",
            geometry={
                "insert": [-40.0, _FAR_ANNOTATION_Y - 7.2, 0.0],
                "visual_anchor": [-32.56, _FAR_ANNOTATION_Y, 0.0],
                "height": 12.0,
            },
        ),
    ]


def test_table_row_with_a_key_is_not_claimed_by_the_frame_stirrup_rule(tmp_path) -> None:
    """s-11: 10N16x150 и 6N8x320 са таблични редове с ключ, който противоречи
    на легендата (4d е L=140, 4c е L=420). Остават за оператора — но в рамката
    има стремена и едно от тях би могло да затвори уравнението на надписа.
    Позиция с кръгче на реда се описва от легендата, не от стреме наоколо.
    """

    import json

    engine = _engine()
    entities = [
        *_table_label("10N8x100"),
        _stirrup_of("S1", 20.0, 20.0, dy=100.0),
        _stirrup_of("S2", 20.0, 20.0, dy=400.0),
    ]
    result = build_batch_geometry_report(
        entities,
        tmp_path,
        project_name="TABLE_ROW_IN_FRAME",
        generated_at=datetime(2026, 9, 17, 12, 0, tzinfo=UTC),
        shape_definitions=engine.shapes,
        control_tests=engine.tests,
        policy=engine.settings,
        operator_areas=(_group_area(),),
    )
    payload = json.loads(result.json_path.read_text(encoding="utf-8"))
    row = next(
        value for value in payload["rows"] if value["annotation_handle"] == "TABLE::BJ-FI-L"
    )

    assert row["position_key_row_key"] == "4D"
    assert row["batch_status"] == "НЯМА КАНДИДАТ"
    assert not row.get("stirrup_length_group_resolution_code")
