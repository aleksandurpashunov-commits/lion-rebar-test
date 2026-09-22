"""Предложение за позиция БЕЗ форма иска всеки участък да е надписан.

ЗАЩО. Собственик, 22.09.2026, върху `тест-001`: за „4N14x310“ Review Center
предлагаше **F003 · 51 + 208 + 51 cm**. Петдесет и едно е кота на съседна
фигура, а 208 е 310 − 2×51 — тоест изчислено наум. Прътът е прав: самият
собственик го реши на 21.09 като F001 с причина „липсва или не е необходима
отделна геометрия“ и бележка „вярно“.

Без разпозната форма нищо не ограничава досмятането: уравнението приема всяка
близка кота и довършва остатъка. Три точни на вид размера са по-опасни от
празно поле, защото операторът вижда отговор вместо въпрос.

Проверката е срещу САМИТЕ коти, а не по ``ShapeEvidence.computed_count``: при
F004 то е ``3 − измерени`` и при две надписани дава 1, тоест не брои
досметнатите.
"""
from lion_rebar.dxf.batch_geometry_report import _every_segment_is_printed
from lion_rebar.dxf.vertical_shape_resolver import derive_shape


def _кота(стойност, x, y=0.0):
    return {"measurement": float(стойност), "point": (float(x), float(y))}


def test_досметнатият_участък_не_е_надписан():
    """Реалният случай: една кота 51 и обявени 310 -> 208 се досмята."""

    коти = [51.0, 26.0, 13.0]
    evidence = derive_shape((0.0, 0.0), [_кота(v, 10.0 * i)
                                         for i, v in enumerate(коти, 1)], 310.0)

    assert evidence is not None
    assert 208.0 in evidence.segments
    assert not _every_segment_is_printed(evidence.segments, коти)


def test_две_надписани_коти_остават_предложение():
    """Когато чертежът наистина носи размерите, предложението се пази."""

    коти = [60.0, 250.0]
    evidence = derive_shape((0.0, 0.0), [_кота(60.0, 10.0), _кота(250.0, 20.0)], 310.0)

    assert evidence is not None
    assert sorted(evidence.segments) == [60.0, 250.0]
    assert _every_segment_is_printed(evidence.segments, коти)


def test_празни_коти_не_дават_предложение():
    assert not _every_segment_is_printed((50.0, 17.0), [])
    assert not _every_segment_is_printed((), [50.0, 17.0])


def test_допускът_е_същият_като_при_раждането_на_форма():
    """1,2 cm — колкото и ``shape_from_case.DIMENSION_SNAP_CM``."""

    from lion_rebar.engine.shape_from_case import DIMENSION_SNAP_CM
    from lion_rebar.dxf.batch_geometry_report import _SUGGESTION_PRINTED_TOLERANCE_CM

    assert _SUGGESTION_PRINTED_TOLERANCE_CM == DIMENSION_SNAP_CM
    assert _every_segment_is_printed((50.0,), [51.1])
    assert not _every_segment_is_printed((50.0,), [51.5])
