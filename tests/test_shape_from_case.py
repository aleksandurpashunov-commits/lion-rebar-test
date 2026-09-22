"""Нова MASTER форма, родена от отказан случай.

Числата са НАСТОЯЩИТЕ пет кобилици от `тест-001` (собственик, 22.09.2026:
„това е Кобилица, но много сложно се чертае в нашия конструктор форми; искам
да мога да го маркирам и да го пусна за одобрение“).

За всяка от тях пакетният отчет вече съдържа::

    O|n=5|r=0.25781,0.08750,0.30937,0.08750,0.25781|a=-45.00,45.00,45.00,-45.00

Формата е изчислена; липсва ѝ само име.
"""
import pytest

from lion_rebar.engine.shape_from_case import (
    ShapeFromCaseError,
    build_draft_shape,
    case_from_report_row,
    collect_cases,
    family_key,
    parse_signature,
    propose_shape,
    symmetry_pattern,
)
from lion_rebar.export.engine_shape_parameters import (
    parameters_for_shape,
    shape_bend_rounding_step,
)


def _ред(handle, text, L, ratios, raw, printed, angles="-45.00,45.00,45.00,-45.00"):
    return {
        "annotation_handle": handle,
        "annotation_text": text,
        "declared_length_cm": L,
        "diameter_mm": 14.0,
        "geometry_length_raw": raw,
        "nearby_dimensions_cm": list(printed),
        "canonical_signature": "O|n=%d|r=%s|a=%s" % (
            len(ratios), ",".join("%.5f" % r for r in ratios), angles),
        "master_shape_code": None,
        "unresolved_curve_count": 0,
        "source_curve_count": 0,
        "candidate_handles": ["G" + handle],
    }


# Петте кобилици, както стоят в отчета на `тест-001`.
КОБИЛИЦИ = [
    _ред("6A04B1", "2x3N14x195", 195.0,
         (0.25781, 0.08750, 0.30937, 0.08750, 0.25781), 193.94,
         [17.0, 50.0, 60.0, 25.0, 35.0, 18.0, 325.0, 12.0]),
    _ред("6A04B7", "2x4N14x175", 175.0,
         (0.29109, 0.10703, 0.20376, 0.10703, 0.29109), 171.77,
         [50.0, 18.0, 35.0, 17.0, 60.0, 25.0]),
    _ред("6A0F6E", "2x3N14x210", 210.0,
         (0.24360, 0.11024, 0.29232, 0.11024, 0.24360), 205.25,
         [23.0, 50.0, 60.0, 25.0, 35.0, 24.0, 12.0]),
    _ред("6A0F75", "2x4N14x185", 185.0,
         (0.27310, 0.13132, 0.19117, 0.13132, 0.27310), 183.08,
         [24.0, 50.0, 35.0, 23.0, 60.0, 25.0]),
]


def _случаи():
    return [case_from_report_row(r, project_name="тест-001") for r in КОБИЛИЦИ]


def test_сигнатурата_се_разлага():
    closed, ratios, angles = parse_signature(КОБИЛИЦИ[0]["canonical_signature"])
    assert closed is False
    assert len(ratios) == 5
    assert angles == (-45.0, 45.0, 45.0, -45.0)


def test_образецът_е_ABCBA():
    _c, ratios, _a = parse_signature(КОБИЛИЦИ[0]["canonical_signature"])
    assert symmetry_pattern(ratios) == "ABCBA"


def test_различните_размери_са_ЕДНО_семейство():
    """Формата е параметрична: 195 и 175 имат различни отношения, но един образец.

    Ако групирането беше по точната сигнатура, петте кобилици щяха да станат
    четири различни форми.
    """

    ключове = {family_key(c) for c in _случаи()}
    assert len(ключове) == 1


def test_размерите_се_четат_от_котите_и_уравнението_затваря():
    предложение = propose_shape(_случаи())

    assert предложение.pattern == "ABCBA"
    assert предложение.length_codes == ("L1", "L2", "L3", "L2", "L1")
    assert предложение.equation.startswith("L = 2L1 + 2L2 + L3")
    assert предложение.rounding_step_cm == 5.0
    assert предложение.case_count == 4

    очаквано = [
        (50.0, 17.0, 60.0, 194.0, 195.0),
        (50.0, 18.0, 35.0, 171.0, 175.0),
        (50.0, 23.0, 60.0, 206.0, 210.0),
        (50.0, 24.0, 35.0, 183.0, 185.0),
    ]
    за_сверка = [
        (v.parameter_values_cm["L1"], v.parameter_values_cm["L2"],
         v.parameter_values_cm["L3"], v.total_cm, v.case.declared_length_cm)
        for v in предложение.values
    ]
    assert за_сверка == очаквано
    assert all(v.closes for v in предложение.values)


def test_участък_без_своя_кота_е_отказ():
    """Размер НЕ се извежда от измереното, когато проектантът не го е написал."""

    ред = dict(КОБИЛИЦИ[0])
    ред["nearby_dimensions_cm"] = [50.0, 17.0]  # липсва котата за средата
    with pytest.raises(ShapeFromCaseError, match="надписана кота"):
        propose_shape([case_from_report_row(ред)])


def test_смес_от_семейства_е_отказ():
    друг = _ред("X1", "4N12x300", 300.0, (0.5, 0.5), 300.0, [150.0],
                angles="-90.00")
    with pytest.raises(ShapeFromCaseError, match="едно семейство"):
        propose_shape([case_from_report_row(КОБИЛИЦИ[0]), case_from_report_row(друг)])


def test_незатварящ_случай_не_ражда_форма():
    ред = dict(КОБИЛИЦИ[0])
    ред["declared_length_cm"] = 260.0  # 194 не стига до 260 по никое правило
    with pytest.raises(ShapeFromCaseError, match="не се затварят"):
        propose_shape([case_from_report_row(ред)])


def test_вече_разпозната_форма_не_ражда_нова():
    ред = dict(КОБИЛИЦИ[0])
    ред["master_shape_code"] = "F004"
    assert case_from_report_row(ред) is None


def test_дъга_не_ражда_форма():
    """Отношенията тогава описват дъга, не прав участък."""

    ред = dict(КОБИЛИЦИ[0])
    ред["unresolved_curve_count"] = 1
    assert case_from_report_row(ред) is None


def test_черновата_никога_не_е_активна():
    форма = build_draft_shape(propose_shape(_случаи()), code="F020", name="КОБИЛИЦА")

    assert форма.state == "DRAFT"
    assert форма.locked is False
    # active НЕ е лостът за одобрение — лостът е state. Форма с
    # active=False се публикува и после мълчаливо не се ползва, защото
    # active_production_shapes филтрира по нея (така стои F010).
    assert форма.active is True
    assert форма.symmetric is True
    assert [s.length_code for s in форма.segments] == ["L1", "L2", "L3", "L2", "L1"]
    assert [(s.bend_after_deg, s.bend_direction) for s in форма.segments[:4]] == [
        (45.0, "RIGHT"), (45.0, "LEFT"), (45.0, "LEFT"), (45.0, "RIGHT"),
    ]


def test_черновата_носи_доказателството_си():
    форма = build_draft_shape(propose_shape(_случаи()), code="F020", name="КОБИЛИЦА")

    assert len(форма.tests) == 4
    assert all(t["source"] == "BORN_FROM_CASE" for t in форма.tests)
    assert {t["annotation_text"] for t in форма.tests} == {
        "2x3N14x195", "2x4N14x175", "2x3N14x210", "2x4N14x185",
    }
    assert "2x3N14x195 (6A04B1)" in форма.notes
    # политиката за добавката е ДАННИ, не код
    assert форма.recognition["bend_allowance"] == {"mode": "ROUND_UP", "step_cm": 5.0}


def test_формата_минава_през_самия_engine():
    """Най-важното: engine-ът приема родената форма и затваря случаите ѝ.

    Без обявената стъпка ``segments_match_total`` би отказал всеки от петте,
    защото кодът на нова форма не може да стои в ``BEND_ROUNDING_SHAPES``.
    """

    предложение = propose_shape(_случаи())
    форма = build_draft_shape(предложение, code="F020", name="КОБИЛИЦА")

    assert shape_bend_rounding_step(форма) == 5.0

    for стойности in предложение.values:
        резултат = parameters_for_shape(
            форма,
            стойности.segment_values_cm,
            production_length_cm=стойности.case.declared_length_cm,
        )
        assert резултат.validation_source == "PINNED_ENGINE_SHAPE_DEFINITION"
        assert резултат.values["L1"] == pytest.approx(
            стойности.parameter_values_cm["L1"])


def test_обявената_стъпка_бие_подразбиращата_се():
    """Формата може да обяви СВОЯ стъпка, различна от общата.

    След 22.09.2026 всяка форма с огъвка получава закръгляне до 5 cm по
    подразбиране, затова липсата на обявена политика вече не е отказ. Полето
    остава нужно за форма, чийто офис закръгля другояче — тук стъпка 10
    отказва 194 -> 195, защото целта ѝ е 200.
    """

    from lion_rebar.export.engine_shape_parameters import (
        EngineShapeParameterError,
        shape_bend_rounding_step,
    )

    предложение = propose_shape(_случаи())
    форма = build_draft_shape(предложение, code="F020", name="КОБИЛИЦА")
    случай = предложение.values[0]

    # без обявена политика формата пак затваря — по общото правило
    форма.recognition.pop("bend_allowance")
    assert shape_bend_rounding_step(форма) is None
    parameters_for_shape(форма, случай.segment_values_cm,
                         production_length_cm=случай.case.declared_length_cm)

    # обявена стъпка 10: 194 сочи 200, а надписът е 195 -> отказ
    форма.recognition["bend_allowance"] = {"mode": "ROUND_UP", "step_cm": 10.0}
    assert shape_bend_rounding_step(форма) == 10.0
    with pytest.raises(EngineShapeParameterError):
        parameters_for_shape(форма, случай.segment_values_cm,
                             production_length_cm=случай.case.declared_length_cm)


def test_събиране_по_семейства_от_цял_отчет():
    групи = collect_cases(КОБИЛИЦИ, project_name="тест-001")
    assert len(групи) == 1
    assert len(next(iter(групи.values()))) == 4


def test_посоката_на_огъвката_е_с_целите_думи():
    """`LEFT`/`RIGHT`/`NONE`, както ги пише ENGINE-ът — не `L`/`R`.

    Намерено при истинска употреба 22.09.2026: собственикът роди „Кобилица“ и
    силуетът излезе навит на спирала. ``shape_preview`` проверява
    ``direction in {"LEFT", "НАЛЯВО"}``, затова „L“ падаше в else-а и ставаше
    надясно — всичките четири огъвки завиваха на една страна.

    В публикувания v0.3.15: 27 пъти „LEFT“, 19 „RIGHT“, 18 „NONE“.
    """

    форма = build_draft_shape(propose_shape(_случаи()), code="F020", name="КОБИЛИЦА")

    посоки = [s.bend_direction for s in форма.segments]
    assert посоки == ["RIGHT", "LEFT", "LEFT", "RIGHT", "NONE"]
    assert not any(p in ("L", "R") for p in посоки)


def test_силуетът_се_разгъва_без_да_се_навива():
    """Без подадена геометрия посоките идват от подписа — огледалният вариант.

    Проверява се само че формата се РАЗГЪВА: двата края са на една височина,
    средата е встрани от тях и прътът върви само надясно. При сгрешена посока
    (първата редакция пишеше „L“/„R“) всички завои отиваха на една страна и
    формата се навиваше на спирала.

    Накъде точно прескача се решава от чертежа — виж
    ``test_средният_участък_излиза_ОТГОРЕ_както_е_начертан``.
    """

    import math

    форма = build_draft_shape(propose_shape(_случаи()), code="F020", name="КОБИЛИЦА")
    дължини = propose_shape(_случаи()).values[0].segment_values_cm

    курс = 0.0
    x = y = 0.0
    върхове = [(0.0, 0.0)]
    for сегмент, дължина in zip(форма.segments, дължини):
        x += дължина * math.cos(math.radians(курс))
        y += дължина * math.sin(math.radians(курс))
        върхове.append((x, y))
        if сегмент.bend_after_deg is not None:
            знак = 1.0 if сегмент.bend_direction == "LEFT" else -1.0
            курс += знак * float(сегмент.bend_after_deg)

    # първият и последният участък са водоравни и на една височина
    assert върхове[0][1] == pytest.approx(0.0)
    assert върхове[-1][1] == pytest.approx(върхове[-2][1])
    # средата е СЛЯЗЛА под началото
    assert върхове[2][1] < -1.0
    # и краят се е върнал на изходната височина
    assert върхове[-1][1] == pytest.approx(0.0, abs=0.001)
    # прътът върви само надясно
    assert all(b[0] > a[0] for a, b in zip(върхове, върхове[1:]))


def test_огледалото_е_допустимо_защото_подписът_не_пази_ръка():
    """Сигнатурата е ориентационно нормализирана — форма от нея няма ръка.

    Отчетът сам го обявява: `orientation_invariant: true` и бележка
    „завъртането и обратната посока не влияят“. Една и съща кобилица дава
    един подпис, независимо дали прескача нагоре или надолу.

    Първата редакция твърдеше ръка и излизаше огледална на чертежа, а
    собственикът оправяше посоките клетка по клетка. 18 от 19 публикувани
    форми са с `mirror_allowed`; изключението е F008, при което горе и долу
    в плочата са различни.
    """

    форма = build_draft_shape(propose_shape(_случаи()), code="F020", name="КОБИЛИЦА")

    assert форма.mirror_allowed is True


# Върховете на `6A04AB` — НАЧЕРТАНАТА кобилица `2x3N14x195` в `тест-001`.
# Прочетени от `extracted_entities.json`; в реда, в който са в полилинията.
ВЪРХОВЕ_6A04AB = [
    (0.0, 0.0),
    (50.0, 0.0),
    (62.0, 12.0),
    (122.0, 12.0),
    (134.0, 0.0),
    (184.0, 0.0),
]


def test_завоите_се_четат_от_начертаната_полилиния():
    """0°, +45°, 0°, −45°, 0° -> ЛЯВО, ДЯСНО, ДЯСНО, ЛЯВО."""

    from lion_rebar.engine.shape_from_case import turn_angles_from_points

    завои = turn_angles_from_points(ВЪРХОВЕ_6A04AB)
    assert завои == (45.0, -45.0, -45.0, 45.0)


def test_средният_участък_излиза_ОТГОРЕ_както_е_начертан():
    """Собственик, 22.09.2026: „това 60 L3 трябва да е отгоре“.

    Подписът е ориентационно нормализиран и даваше обратното: формата се
    раждаше огледална на чертежа и операторът обръщаше посоките на ръка.
    """

    import math

    ред = dict(КОБИЛИЦИ[0])
    случай = case_from_report_row(ред, project_name="тест-001",
                                  geometry_points=ВЪРХОВЕ_6A04AB)
    форма = build_draft_shape(propose_shape([случай]), code="F020", name="КОБИЛИЦА")

    assert [s.bend_direction for s in форма.segments] == [
        "LEFT", "RIGHT", "RIGHT", "LEFT", "NONE",
    ]

    курс = 0.0
    y = 0.0
    височини = [0.0]
    дължини = propose_shape([случай]).values[0].segment_values_cm
    for сегмент, дължина in zip(форма.segments, дължини):
        y += дължина * math.sin(math.radians(курс))
        височини.append(y)
        if сегмент.bend_after_deg is not None:
            знак = 1.0 if сегмент.bend_direction == "LEFT" else -1.0
            курс += знак * float(сегмент.bend_after_deg)

    # средният участък (L3) стои НАД двата края
    assert височини[2] > 0.5
    assert височини[3] == pytest.approx(височини[2])
    assert височини[-1] == pytest.approx(0.0, abs=0.001)


def test_без_подадена_геометрия_поведението_е_старото():
    """Подписът остава резервата, когато чертежът не е подаден."""

    случай = case_from_report_row(dict(КОБИЛИЦИ[0]))
    assert случай.drawn_turn_angles_deg == ()
    форма = build_draft_shape(propose_shape([случай]), code="F020", name="К")
    assert [s.bend_direction for s in форма.segments][:1] == ["RIGHT"]


def test_контролните_тестове_минават_през_самия_engine():
    """Публикуването падаше с пет пъти „FAILED_TEST … Форма не е намерена“.

    Намерено при истинска употреба 22.09.2026. Пишех тестовете на ръка с
    ключ ``id`` и речник за размерите, а ``run_engine_tests`` чете ``test_id``
    и ``shape_code`` и очаква размерите като низ „L1=50; L2=17“. Без
    ``shape_code`` търсенето връщаше нищо и всеки от тестовете падаше — точно
    толкова на брой, колкото са случаите.

    Сега записът минава през каноничния ``test_runner.build_control_test``.
    """

    from lion_rebar.engine.models import EnginePackage
    from lion_rebar.engine.test_runner import run_engine_tests

    форма = build_draft_shape(propose_shape(_случаи()), code="F020", name="КОБИЛИЦА")

    for тест in форма.tests:
        assert тест["shape_code"] == "F020"
        assert тест["test_id"].startswith("F020-CASE-")
        assert isinstance(тест["dimensions"], str) and "L1=" in тест["dimensions"]
        assert тест["expected_total_cm"] > 0

    пакет = EnginePackage(engine_id="lion-rebar-master", title="MASTER",
                          version="0.3.99", state="DRAFT", shapes=[форма])
    резултати = run_engine_tests(пакет)

    assert len(резултати) == len(форма.tests)
    паднали = [r for r in резултати if not r.passed]
    assert not паднали, [(r.test_id, r.message) for r in паднали]


def test_произходът_оцелява_до_контролния_тест():
    """Каноничните ключове не изместват следата към чертежа."""

    форма = build_draft_shape(propose_shape(_случаи()), code="F020", name="КОБИЛИЦА")
    първи = форма.tests[0]

    assert първи["source"] == "BORN_FROM_CASE"
    assert първи["annotation_text"] == "2x3N14x195"
    assert първи["annotation_handle"] == "6A04B1"
    assert първи["production_length_cm"] == 195.0


def test_родената_форма_влиза_в_производствените():
    """Публикувана форма с ``active=False`` е мъртва буква.

    Намерено при истинска употреба 22.09.2026: собственикът одобри и
    публикува F020, версия 0.3.19 стана PRODUCTION — а формата не се
    ползваше, защото ``active_production_shapes`` филтрира по ``active``.
    Интерфейсът дотогава изобщо нямаше отметка за този флаг.
    """

    from lion_rebar.export.engine_shape_parameters import active_production_shapes

    форма = build_draft_shape(propose_shape(_случаи()), code="F020", name="КОБИЛИЦА")

    кодове = [s.code for s in active_production_shapes([форма])]
    assert кодове == ["F020"]
