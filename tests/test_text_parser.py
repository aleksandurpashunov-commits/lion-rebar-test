from lion_rebar.dxf.text_parser import parse_rebar_text


def test_parse_n_format_with_multiplication_sign():
    result = parse_rebar_text("32N10×350")
    assert result.count == 32
    assert result.diameter_mm == 10
    assert result.length_cm == 350
    assert result.confidence > 0.9


def test_parse_phi_and_l_format():
    result = parse_rebar_text("4Ø12 L=250")
    assert result.count == 4
    assert result.diameter_mm == 12
    assert result.length_cm == 250


def test_parse_cyrillic_f_and_comma_decimal():
    result = parse_rebar_text("8 Ф 10 x 635,5")
    assert result.count == 8
    assert result.diameter_mm == 10
    assert result.length_cm == 635.5


def test_non_rebar_text_is_not_forced():
    result = parse_rebar_text("ГРЕДА B.14 25/50")
    assert result.matched_pattern is None
    assert result.confidence == 0


def test_large_counts_are_preserved_exactly_for_mass_rebar_positions():
    for text, expected in (
        ("4362N10x97", 4362),
        ("3038N10x47", 3038),
        ("1990N10x199", 1990),
        ("400N10x140", 400),
        ("400N12x300", 400),
    ):
        result = parse_rebar_text(text)
        assert result.count == expected
        assert result.matched_pattern == "COUNT_N_DIAMETER_X_LENGTH"
        assert result.confidence == 0.98
