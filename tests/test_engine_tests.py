from lion_rebar.engine.models import EnginePackage, SegmentDefinition, ShapeDefinition
from lion_rebar.engine.test_runner import build_control_test, run_engine_tests


def test_f009_control_total():
    shape = ShapeDefinition(
        code="F009",
        name="Test",
        state="LOCKED",
        locked=True,
        segments=[
            SegmentDefinition(index=1, length_code="L1"),
            SegmentDefinition(index=2, length_code="L2"),
            SegmentDefinition(index=3, length_code="L3"),
            SegmentDefinition(index=4, length_code="L4"),
            SegmentDefinition(index=5, length_code="L5"),
        ],
    )
    engine = EnginePackage(
        engine_id="test",
        title="test",
        version="1.0.0",
        shapes=[shape],
        tests=[
            {
                "ID_ТЕСТ": "T-F009-001",
                "КОД_ФОРМА": "F009",
                "ОЧАКВАНИ_РАЗМЕРИ_СМ": "L1=8; L2=79; L3=15; L4=79; L5=8",
                "ОЧАКВАНА_ОБЩА_ДЪЛЖИНА_СМ": 189,
            }
        ],
    )
    result = run_engine_tests(engine)
    assert len(result) == 1
    assert result[0].passed
    assert result[0].calculated_cm == 189


def test_pythagorean_segment():
    shape = ShapeDefinition(
        code="F002",
        name="Reduction",
        state="LOCKED",
        locked=True,
        segments=[
            SegmentDefinition(index=1, length_code="L1"),
            SegmentDefinition(
                index=2,
                length_code="L2",
                geometry_method="ПИТАГОР",
                projection_x_code="DX2",
                projection_y_code="DY2",
            ),
            SegmentDefinition(index=3, length_code="L3"),
        ],
    )
    engine = EnginePackage(
        engine_id="test",
        title="test",
        version="1.0.0",
        shapes=[shape],
        tests=[
            {
                "test_id": "P",
                "shape_code": "F002",
                "dimensions": {"L1": 100, "DX2": 3, "DY2": 4, "L3": 50},
                "expected_total_cm": 155,
            }
        ],
    )
    assert run_engine_tests(engine)[0].passed


def test_control_test_builder_creates_matcher_profile():
    shape = ShapeDefinition(
        code="F099",
        name="ПРОБНА",
        state="APPROVED",
        segments=[
            SegmentDefinition(index=1, length_code="L1"),
            SegmentDefinition(index=2, length_code="L2"),
            SegmentDefinition(index=3, length_code="L1"),
        ],
    )

    test = build_control_test(shape, "L1=25; L2=140", test_id="T-F099-001")

    assert test["shape_code"] == "F099"
    assert test["expected_total_cm"] == 190
    assert test["dimensions"] == "L1=25; L2=140"
    assert test["purpose"] == "ENGINE_TEST_AND_MATCHER_PROFILE"
