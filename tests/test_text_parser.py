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


def test_kirilskoto_h_ne_se_dogazhda_za_razdelitel():
    """Кирилското „х" (U+0445) НЕ се приема за разделител — измерено защо.

    `normalize_text` заменя `×` (U+00D7), но не кирилското `х`, а
    `_GENERAL_N_PATTERN` иска `[xX×*]`. Затова `32N10х350` дава диаметър без
    дължина: отказ, не догадка.

    Мярката, на която стъпва това очакване (29.09.2026, `K3_KIRILSKO_H.json`):
    в корпуса от 11 чертежа има **18 358 надписа**, от които **293 съдържат
    кирилско х** — всичките в български думи („арх.", „Хор.арм.", „шахматно",
    „Сх.арм"). Надписи, които биха се РАЗБРАЛИ след замяна х→x: **0**. Надписи,
    които биха СМЕНИЛИ бройка или дължина: **0**. Тоест приемането на буквата
    днес не печели нито една позиция, а отказът не губи нито една.

    Ако някога се реши буквата да се приема, този тест трябва да се смени с
    обосновка и с ново измерване — не само защото „изглежда еднакво".
    """

    кирилско = parse_rebar_text("32N10\u0445350")
    assert кирилско.diameter_mm == 10
    assert кирилско.length_cm is None

    латинско = parse_rebar_text("32N10x350")
    assert латинско.diameter_mm == 10
    assert латинско.length_cm == 350.0
    assert латинско.count == 32


def test_prozata_s_kirilsko_h_ne_stava_pozicia():
    """Бележките с кирилско х не се разбират като надпис и след замяна.

    ЧЕСТНО ЗА СИЛАТА НА ТОЗИ ТЕСТ (поправено след независим преглед, 29.09.2026):
    сам по себе си той НЕ разграничава нищо — в тези три текста няма цифра до
    буквата „х", затова и двата варианта отпадат по очевидни причини. Тестът
    служи само като закотвяне на три ИСТИНСКИ низа от корпуса (`d-1`, `s-7`),
    за да е видимо какви са те.

    Разграничаващото доказателство е другаде и е измерено, не твърдяно:
    сканирането на 18 358 надписа (`K3_KIRILSKO_H.json`) дава 0 надписа, които
    печелят или менят разбор след замяна х→x, а положителната контрола в
    `test_kirilskoto_h_ne_se_dogazhda_za_razdelitel` показва, че мярката МОЖЕ да
    хване случай, когато има такъв.
    """

    for текст in (
        "Хор.арм. в шайби 20 броя/s=15",
        "/шахматно през 40см/",
        "СХЕМА НА АРМ. (Сх.арм 1)",
    ):
        сменен = текст.replace("\u0445", "x").replace("\u0425", "X")
        for вариант in (текст, сменен):
            резултат = parse_rebar_text(вариант)
            assert резултат.length_cm is None, вариант
