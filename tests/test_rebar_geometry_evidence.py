"""Разделителят отделя армировка от контур и отказва при тънко доказателство.

Синтетичните случаи проверяват ОБРАЗЕЦА. Истинският случай е изрязан от ``d-2``
(слоят ``…Slab contour``, приет погрешно от старото правило: 74 надписа съвпадат
по дължина, контрола 1) — пазят се само десет надписа и двайсет обекта, но
координатите са истинските и веригата на контура е цяла. Ако някой ден
структурната проверка изчезне, точно този тест става червен.

Изрязаното се възпроизвежда с ``tools/etap5_geom_agent.py`` върху целия чертеж.
"""
from __future__ import annotations

import pytest

from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.dxf.rebar_geometry_evidence import (
    MIN_LENGTH_MATCHES,
    VERDICT_NOT_REBAR,
    VERDICT_REBAR,
    VERDICT_UNKNOWN,
    classify_layer,
    diameter_from_layer_name,
    evaluate_rebar_geometry,
)

ПЛОЧА = "AutoCAD_Structural_Detailing_Slab contour"
ПРЪТИ = "AutoCAD_Structural_Detailing_Surface reinforcement - bars - bottom"
НАДПИСИ = "AutoCAD_Structural_Detailing_Distribution description"


# --------------------------------------------------------------------------- #
# строителни блокчета


def _надпис(handle: str, текст: str, at: tuple[float, float], слой: str = "txt"):
    return DxfEntityRecord(
        handle=handle,
        entity_type="MTEXT",
        layer=слой,
        geometry={"insert": [at[0], at[1], 0.0]},
        text=текст,
    )


def _линия(handle: str, слой: str, начало, край):
    return DxfEntityRecord(
        handle=handle,
        entity_type="LINE",
        layer=слой,
        geometry={"start": [начало[0], начало[1], 0.0],
                  "end": [край[0], край[1], 0.0]},
    )


def _полилиния(handle: str, слой: str, точки, *, closed: bool = False):
    return DxfEntityRecord(
        handle=handle,
        entity_type="LWPOLYLINE",
        layer=слой,
        geometry={"points": [[x, y] for x, y in точки], "closed": closed},
    )


def _семейство_пръти(слой: str, брой: int, дължина: float, *, x0: float = 0.0):
    """Пръти: отделни отворени отсечки, всяка с надпис до своя край.

    Надписът стои до КРАЯ, защото правилото мери разстояние до върховете на
    обекта — така е и в ``convention_profile``.
    """

    записи = []
    for индекс in range(брой):
        x = x0 + индекс * 500.0
        записи.append(_линия(f"B{индекс:03d}", слой, (x, 0.0), (x, дължина)))
        записи.append(_надпис(
            f"T{индекс:03d}", f"2N12x{дължина:.0f}/10", (x + 10.0, -10.0)
        ))
    return записи


def _затворен_контур(слой: str, ширина: float, височина: float, *, x0=0.0, y0=0.0,
                     префикс="C"):
    """Контур от четири прави отсечки: всеки край среща следващия."""

    ъгли = [(x0, y0), (x0 + ширина, y0), (x0 + ширина, y0 + височина),
            (x0, y0 + височина)]
    записи = []
    for индекс in range(4):
        записи.append(_линия(
            f"{префикс}{индекс}", слой, ъгли[индекс], ъгли[(индекс + 1) % 4]
        ))
    return записи


def _ръб_на_плоча(слой: str, брой: int, височина: float, *, ширина: float = 60.0,
                  надписи_на_рамка: int = 2):
    """Ръбът на плоча: тесни затворени рамки, чиито дълги страни са с дължина
    точно колкото пише в съседния надпис. Така изглежда истинският случай в
    ``d-2`` — виж ``D2_ОБЕКТИ``. По няколко надписа на рамка, за да е налице и
    хипотезата „един и същ обект се заявява от много надписа“."""

    записи = []
    for индекс in range(брой):
        x0 = индекс * 5000.0
        записи += _затворен_контур(слой, ширина, височина, x0=x0,
                                   префикс=f"K{индекс}_")
        for пореден in range(надписи_на_рамка):
            записи.append(_надпис(
                f"T{индекс:03d}_{пореден}", f"2N12x{височина:.0f}/10",
                (x0 + 20.0, -10.0 - пореден * 5.0),
            ))
    return записи


# --------------------------------------------------------------------------- #
# синтетични случаи


def test_синтетичен_армировъчен_слой_е_приет():
    записи = _семейство_пръти("ARM_PROBA", брой=8, дължина=380.0)
    оценка = evaluate_rebar_geometry(записи)
    слой = оценка.layers["ARM_PROBA"]
    assert слой.verdict == VERDICT_REBAR
    assert слой.signals["length_matches"] == 8
    assert слой.signals["claimed_bound_share"] == 0.0
    assert слой.signals["claimed_free_endpoint_share"] == 1.0
    assert слой.score > 0.5
    assert оценка.rebar_layers() == ("ARM_PROBA",)


def test_синтетичен_контур_не_е_армировка():
    """Затворен контур, чиито страни са дълги точно колкото пишат надписите.

    Всяка страна е заявена от по няколко различни надписа — точно хипотезата
    „контурът се заявява от много“. Приемането пада не от бройката заявители, а
    от това, че всяка страна е звено от затворена верига.
    """

    записи = _ръб_на_плоча("KONTUR_PROBA", брой=6, височина=380.0)
    оценка = evaluate_rebar_geometry(записи)
    слой = оценка.layers["KONTUR_PROBA"]
    assert слой.verdict == VERDICT_NOT_REBAR
    assert слой.signals["length_matches"] >= MIN_LENGTH_MATCHES
    assert слой.signals["claimed_bound_share"] == 1.0
    assert слой.signals["distinct_claimants_max"] >= 2
    assert слой.score == 0.0
    assert "верига" in слой.why
    assert оценка.rebar_layers() == ()
    assert оценка.refused_layers() == ("KONTUR_PROBA",)


def test_затворената_полилиния_не_минава_за_прът():
    """Един затворен обект с обиколка колкото надписа също не е прът."""

    точки = [(0.0, 0.0), (100.0, 0.0), (100.0, 90.0), (0.0, 90.0), (0.0, 0.0)]
    записи = [_полилиния("P1", "LAMELA", точки, closed=True)]
    for индекс in range(6):
        записи.append(_надпис(f"T{индекс}", "2N12x380/10", (50.0, 45.0 + индекс)))
    оценка = evaluate_rebar_geometry(записи)
    слой = оценка.layers["LAMELA"]
    assert слой.verdict == VERDICT_UNKNOWN  # един заявен обект не стига и за отказ
    assert слой.signals["claimed_self_closed_share"] == 1.0
    assert слой.objects[0].verdict == VERDICT_NOT_REBAR
    assert "затворен" in слой.objects[0].why


def test_тънкото_доказателство_дава_unknown():
    """Две съвпадения не са отговор — нито „да“, нито „не“."""

    записи = _семейство_пръти("ARM_MALKO", брой=2, дължина=380.0)
    оценка = evaluate_rebar_geometry(записи)
    слой = оценка.layers["ARM_MALKO"]
    assert слой.verdict == VERDICT_UNKNOWN
    assert слой.signals["length_matches"] == 2
    assert "тънко доказателство" in слой.why
    assert оценка.rebar_layers() == ()


def test_контролата_спира_случайното_съвпадение():
    """Слой, който дава и контролни съвпадения, не се приема на бройка."""

    записи = _семейство_пръти("ARM_SLUCHAJ", брой=6, дължина=380.0)
    # шест обекта с дължина 393 = 380 + 13: контролата вече не е нула
    for индекс in range(6):
        x = 100000.0 + индекс * 500.0
        записи.append(_линия(f"K{индекс}", "ARM_SLUCHAJ", (x, 0.0), (x, 393.0)))
        записи.append(_надпис(f"KT{индекс}", "2N12x380/10", (x + 10.0, -10.0)))
    оценка = evaluate_rebar_geometry(записи)
    слой = оценка.layers["ARM_SLUCHAJ"]
    assert слой.signals["control_matches"] == 6
    assert слой.verdict == VERDICT_UNKNOWN
    assert "контролата" in слой.why


def test_кодираният_диаметър_отваря_праг_при_три_съвпадения():
    записи = _семейство_пръти("Arm - N12", брой=3, дължина=380.0)
    оценка = evaluate_rebar_geometry(записи)
    слой = оценка.layers["Arm - N12"]
    assert слой.verdict == VERDICT_REBAR
    assert слой.gates["coded_diameter_support"] is True
    assert слой.signals["coded_diameter_mm"] == 12.0
    assert слой.signals["coded_diameter_agreements"] == 3


def test_разминаващият_се_диаметър_затваря_прага():
    """Ø в името срещу Ø в надписа: разминаването не е подкрепа."""

    записи = _семейство_пръти("Arm - N16", брой=3, дължина=380.0)  # надписите са N12
    оценка = evaluate_rebar_geometry(записи)
    слой = оценка.layers["Arm - N16"]
    assert слой.signals["coded_diameter_clashes"] == 3
    assert слой.gates["coded_diameter_support"] is False
    assert слой.verdict == VERDICT_UNKNOWN


def test_прекалено_много_отсечки_за_една_форма():
    """Дълга отворена полилиния, начертала целия контур, не е една форма."""

    точки = [(float(индекс) * 20.0, 0.0 if индекс % 2 else 20.0)
             for индекс in range(30)]
    записи = [_полилиния("P1", "DALGA", точки)]
    дължина = 0.0
    предишна = точки[0]
    for точка in точки[1:]:
        дължина += ((точка[0] - предишна[0]) ** 2 + (точка[1] - предишна[1]) ** 2) ** 0.5
        предишна = точка
    for индекс in range(6):
        записи.append(_надпис(f"T{индекс}", f"2N12x{дължина:.0f}/10",
                              (точки[3][0], точки[3][1] + индекс)))
    оценка = evaluate_rebar_geometry(записи)
    слой = оценка.layers["DALGA"]
    assert слой.signals["claimed_segment_count_max"] == 29
    assert слой.objects[0].verdict == VERDICT_NOT_REBAR
    assert "отсечки" in слой.objects[0].why


def test_присъдата_за_слоя_е_сбор_от_присъдите_за_обектите():
    """Слой не става армировъчен, докато обектите му са отказани един по един.

    Тук краищата са СВОБОДНИ — пада само проверката за брой отсечки. Ако слоят
    се съдеше отделно от обектите си, тази присъда би била „армировка“.
    """

    записи = []
    for рамо in range(6):
        x0 = рамо * 10000.0
        точки = [(x0 + индекс * 20.0, 0.0 if индекс % 2 else 20.0)
                 for индекс in range(30)]
        записи.append(_полилиния(f"P{рамо}", "DALGA_MNOGO", точки))
        дължина = 0.0
        предишна = точки[0]
        for точка in точки[1:]:
            дължина += ((точка[0] - предишна[0]) ** 2
                        + (точка[1] - предишна[1]) ** 2) ** 0.5
            предишна = точка
        записи.append(_надпис(f"T{рамо}", f"2N12x{дължина:.0f}/10",
                              (точки[0][0] + 10.0, -10.0)))
    оценка = evaluate_rebar_geometry(записи)
    слой = оценка.layers["DALGA_MNOGO"]
    assert слой.signals["length_matches"] >= MIN_LENGTH_MATCHES
    assert слой.signals["claimed_bound_share"] == 0.0      # краищата са свободни
    assert слой.signals["claimed_refused_share"] == 1.0    # но обектите са отказани
    assert слой.verdict == VERDICT_NOT_REBAR
    assert all(обект.verdict == VERDICT_NOT_REBAR for обект in слой.objects)


# --------------------------------------------------------------------------- #
# истинският случай от d-2


#: (handle, текст, x, y) — надписите, изрязани от d-2.
D2_НАДПИСИ = [
    ("2599", " 14N8x220/20-Д.А.-1ред", 47844.95, 22019.16),
    ("26CD", " 7N12x245/20-Д.А.-2ред", 47864.72, 22471.27),
    ("2899", " 214N12x380/20-Г.А.-1ред", 51486.42, 21453.89),
    ("2A02", " 104N12x380/20-Г.А.-1ред", 51523.82, 22156.44),
    ("2A5D", " 75N12x380/20-Г.А.-1ред", 53916.07, 22160.31),
    ("39F0", " 3N12x380/10", 50631.68, 24768.47),
    ("3A2F", " 2N14x600/10", 47153.35, 24305.69),
    ("3A4F", " 2N14x600/10", 46925.57, 24281.48),
    ("3A87", " 2N14x720/10", 48583.63, 24281.75),
]

#: (handle, слой, върхове) — обектите, изрязани от d-2.
D2_ОБЕКТИ = [
    ("257D", ПЛОЧА, [(48064.06, 22169.63), (47789.06, 22169.63)]),
    ("257E", ПЛОЧА, [(47789.06, 22169.63), (47789.06, 21950.13)]),
    ("257F", ПЛОЧА, [(47789.06, 21950.13), (48064.06, 21950.13)]),
    ("39DD", ПЛОЧА, [(50722.64, 25092.9), (50698.97, 25092.9)]),
    ("39DE", ПЛОЧА, [(50698.97, 25092.9), (50698.97, 24712.9)]),
    ("39DF", ПЛОЧА, [(50698.97, 24712.9), (50722.64, 24712.9)]),
    ("3A21", ПЛОЧА, [(47085.54, 24912.07), (47071.12, 24912.07)]),
    ("3A22", ПЛОЧА, [(47071.12, 24912.07), (47071.12, 24312.27)]),
    ("3A23", ПЛОЧА, [(47071.12, 24312.27), (47085.54, 24312.27)]),
    ("3A41", ПЛОЧА, [(46980.99, 24912.07), (46966.57, 24912.07)]),
    ("3A42", ПЛОЧА, [(46966.57, 24912.07), (46966.57, 24312.27)]),
    ("3A43", ПЛОЧА, [(46966.57, 24312.27), (46980.99, 24312.27)]),
    ("3A79", ПЛОЧА, [(48680.66, 24972.07), (48666.24, 24972.07)]),
    ("3A7A", ПЛОЧА, [(48666.24, 24972.07), (48666.24, 24252.27)]),
    ("3A7B", ПЛОЧА, [(48666.24, 24252.27), (48680.66, 24252.27)]),
    ("259A", ПРЪТИ, [(47831.76, 21950.13), (47831.76, 22169.63)]),
    ("26BB", ПРЪТИ, [(47791.56, 22482.28), (48036.56, 22482.28)]),
    ("289A", ПРЪТИ, [(51472.91, 21157.15), (51473.1, 21537.15)]),
    ("2A03", ПРЪТИ, [(51513.0, 21782.63), (51513.0, 22162.63)]),
    ("2A5E", ПРЪТИ, [(53903.1, 21782.63), (53903.1, 22162.63)]),
]


def _d2_записи():
    записи = [
        _надпис(handle, текст, (x, y), НАДПИСИ)
        for handle, текст, x, y in D2_НАДПИСИ
    ]
    записи += [
        _линия(handle, слой, върхове[0], върхове[-1])
        for handle, слой, върхове in D2_ОБЕКТИ
    ]
    return записи


def test_истинският_slab_contour_се_отказва_а_прътите_се_приемат():
    оценка = evaluate_rebar_geometry(_d2_записи())

    плоча = оценка.layers[ПЛОЧА]
    assert плоча.verdict == VERDICT_NOT_REBAR
    assert плоча.signals["length_matches"] >= MIN_LENGTH_MATCHES
    assert плоча.signals["claimed_bound_share"] == 1.0

    пръти = оценка.layers[ПРЪТИ]
    assert пръти.verdict == VERDICT_REBAR
    assert пръти.signals["length_matches"] >= MIN_LENGTH_MATCHES
    assert пръти.signals["claimed_bound_share"] == 0.0

    assert оценка.rebar_layers() == (ПРЪТИ,)
    assert оценка.refused_layers() == (ПЛОЧА,)


def test_един_надпис_намира_дължината_си_и_в_контура_и_в_пръта():
    """Сърцето на случая: дължината съвпада и на двете места.

    Надпис „14N8x220“ намира и страна на контура (219,5), и истинския прът
    (219,5). Разделя ги само свързаността на краищата — ако тази проверка падне,
    двата слоя стават неразличими.
    """

    оценка = evaluate_rebar_geometry(_d2_записи())
    заявени = {
        обект.handle: обект
        for слой in (ПЛОЧА, ПРЪТИ)
        for обект in оценка.layers[слой].objects
    }
    assert "2599" in заявени["257E"].claiming_annotations
    assert "2599" in заявени["259A"].claiming_annotations
    assert заявени["257E"].verdict == VERDICT_NOT_REBAR
    assert заявени["257E"].free_end_count == 0
    assert заявени["259A"].verdict == VERDICT_REBAR
    assert заявени["259A"].free_end_count == 2


@pytest.mark.parametrize("допуск", [0.05, 0.25, 0.75, 2.0, 5.0])
def test_присъдата_не_зависи_от_допуска_за_слепване(допуск):
    """Признакът е стабилен: измерено същото разделяне в 100x обхват."""

    оценка = evaluate_rebar_geometry(_d2_записи(), endpoint_tolerance_raw=допуск)
    assert оценка.layers[ПЛОЧА].verdict == VERDICT_NOT_REBAR
    assert оценка.layers[ПРЪТИ].verdict == VERDICT_REBAR


# --------------------------------------------------------------------------- #
# поведение на модула


def test_речник_и_запис_дават_една_и_съща_присъда():
    """Входът може да е ``DxfEntityRecord`` или речникът от екстрактора."""

    записи = _d2_записи()
    речници = [запис.to_dict() for запис in записи]
    едно = evaluate_rebar_geometry(записи)
    друго = evaluate_rebar_geometry(речници)
    assert едно.to_dict() == друго.to_dict()


def test_чертежът_не_се_променя():
    """Модулът не пипа подадените обекти — само чете."""

    записи = _d2_записи()
    преди = [запис.to_dict() for запис in записи]
    evaluate_rebar_geometry(записи)
    assert [запис.to_dict() for запис in записи] == преди


def test_слой_без_нито_едно_съвпадение_не_се_отчита():
    записи = _семейство_пръти("ARM_PROBA", брой=8, дължина=380.0)
    записи += _затворен_контур("OSI", ширина=9000.0, височина=7000.0, префикс="O")
    оценка = evaluate_rebar_geometry(записи)
    assert "OSI" not in оценка.layers
    присъда = classify_layer(записи, "OSI")
    assert присъда.verdict == VERDICT_UNKNOWN
    assert присъда.signals["length_matches"] == 0
    assert оценка.verdict("OSI") == VERDICT_UNKNOWN


def test_classify_layer_дава_същото_като_пълната_оценка():
    записи = _d2_записи()
    пълна = evaluate_rebar_geometry(записи)
    assert classify_layer(записи, ПЛОЧА).to_dict() == пълна.layers[ПЛОЧА].to_dict()
    assert classify_layer(записи, ПРЪТИ).to_dict() == пълна.layers[ПРЪТИ].to_dict()


@pytest.mark.parametrize(
    "име, очакван",
    [
        ("Arm - N12", 12.0),
        ("ARM_N8", 8.0),
        ("arm - n16", 16.0),
        ("Stremena", None),
        ("AutoCAD_Structural_Detailing_Slab contour", None),
        ("txt_N", None),
    ],
)
def test_кодиран_диаметър_в_името(име, очакван):
    assert diameter_from_layer_name(име) == очакван


def test_празен_чертеж_не_дава_присъди():
    оценка = evaluate_rebar_geometry([])
    assert оценка.layers == {}
    assert оценка.annotations_with_length == 0
    assert оценка.linear_objects == 0
    assert оценка.rebar_layers() == ()
