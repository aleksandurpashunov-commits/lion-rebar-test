"""Двата надписани размера бият дописаното рамо.

Реален случай от "тест-001" (операторска проверка на собственика,
21.09.2026): при ``28N12x455`` проектантът е начертал 440 и 13, а в описа
влизаше 440 + 15 -- второто рамо беше ИЗЧИСЛЕНО като ``L - 440``, за да
затвори сумата. Така огъвателната ведомост получаваше размер, какъвто в
чертежа няма, и изглеждаше верен.
"""
from lion_rebar.dxf.vertical_shape_resolver import derive_shape


def _dimension(value, x, y=0.0):
    return {"measurement": float(value), "point": (float(x), float(y))}


def test_printed_pair_is_used_instead_of_computed_leg():
    """440 и 13 са надписани; остатъкът от 2 cm е добавка за огъване."""

    point = (0.0, 0.0)
    dimensions = [_dimension(440.0, 10.0), _dimension(13.0, 20.0)]

    evidence = derive_shape(point, dimensions, 455.0, expected_shape="F004")

    assert evidence is not None
    assert evidence.shape_code == "F004"
    assert sorted(evidence.segments) == [13.0, 440.0]
    assert 15.0 not in evidence.segments


def test_exact_closure_still_wins_and_is_unchanged():
    """Точното затваряне не се променя: 675 + 10 = 685."""

    point = (0.0, 0.0)
    dimensions = [_dimension(675.0, 10.0), _dimension(10.0, 20.0)]

    evidence = derive_shape(point, dimensions, 685.0, expected_shape="F004")

    assert evidence is not None
    assert sorted(evidence.segments) == [10.0, 675.0]


def test_two_qualifying_pairs_are_refused():
    """Две съвпадения е отказ, не „най-близкото“.

    Реален случай ``11N8x115``: наблизо стоят и 12, и 13, а 100 + 12 и
    100 + 13 минават закръглянето до 115. Позицията отива при оператора.
    """

    point = (0.0, 0.0)
    dimensions = [_dimension(100.0, 10.0), _dimension(12.0, 12.0),
                  _dimension(13.0, 14.0)]

    evidence = derive_shape(point, dimensions, 115.0, expected_shape="F004")

    assert evidence is None or 12.0 not in evidence.segments or \
        13.0 not in evidence.segments
    if evidence is not None:
        # допуска се само дописаното рамо (старото поведение), но никога
        # мълчалив избор между двете надписани двойки
        assert evidence.computed_count >= 1


def test_f002_keeps_the_closing_pair():
    """Двойката за F004 не бива да се преозначава като F002.

    ``derive_shape`` преозначава намереното при ``wanted='F002'``
    (``code = wanted``). Причината пазачът да остане е СТРУКТУРНА, не заради
    допуска: F002 е прът с редукция и общата му дължина минава през
    хипотенузата (``L1 + hypot(DX2, DY2) + L3``), тоест двусегментен отговор
    за нея е грешен по устройство.

    (До 22.09.2026 тук се твърдеше и че F002 няма добавка за огъване. След
    решението на собственика всяка форма С огъвка я получава, затова онази
    част от мотива отпадна — пазачът обаче остава.)
    """

    point = (0.0, 0.0)
    dimensions = [_dimension(440.0, 10.0), _dimension(13.0, 12.0),
                  _dimension(15.0, 90.0)]

    evidence = derive_shape(point, dimensions, 455.0, expected_shape="F002")

    assert evidence is not None
    assert evidence.shape_code == "F002"
    # затварящата двойка остава: 440 + 15 = 455
    assert abs(sum(evidence.segments) - 455.0) <= 0.05
    assert 13.0 not in evidence.segments


def test_deficit_beyond_rounding_is_not_accepted_as_a_pair():
    """Сбор 447 при надпис 455 не е закръгляне -- двойката не се приема."""

    point = (0.0, 0.0)
    dimensions = [_dimension(440.0, 10.0), _dimension(7.0, 20.0)]

    evidence = derive_shape(point, dimensions, 455.0, expected_shape="F004")

    assert evidence is None or sorted(evidence.segments) != [7.0, 440.0]
