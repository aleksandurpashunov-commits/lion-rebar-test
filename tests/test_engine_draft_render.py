"""Формите над F009 се рисуват като в редактора на форми.

ЗАЩО. Собственик, 23.09.2026, за позиции 79–83 в „Тест-003“: „не са по
одобрения ни стандарт за изобразяване на геометрията — нищо не се вижда“. И
после, показвайки OPEN_ENGINE редактора: „много добре ги рисува и много лесно“.

Дотогава ``shape_image`` пращаше всяка форма над F009 през СВОЙ, по-груб
рисувач: тънка линия без размерни вериги и надписи ``L1=50 cm`` върху самия
прът. Сега рисува ``engine_draft_render`` — буквален пренос на общия рисувач от
заключения ``OPEN_ENGINE_v0.3.1_MASTER_F009_LOCKED.html``.

Числата тук са от РЕАЛНИТЕ чернови в редактора, така както собственикът ги
показа на екрана — F012 „Стълбище-Манш 1“ 264/96/15 и F021 „Кобилица-1“
17/30/24/50 — за да може всеки да ги сравни едно към едно.
"""
from __future__ import annotations

import math
import re

import pytest

from lion_rebar.engine.models import SegmentDefinition, ShapeDefinition
from lion_rebar.export.engine_draft_render import (
    DraftSegment,
    choose_dimension_sign,
    fit_draft,
    fmt_draft,
    initial_heading,
    physical_points,
    render_draft_svg,
    render_shape_draft_svg,
    segments_from_shape,
    separate_overlapping_ends,
)


def _манш1() -> tuple[DraftSegment, ...]:
    """F012 „Стълбище-Манш 1“: ключ ``E|264@29R|96@90R|15``."""

    return (
        DraftSegment(264.0, 29.0, "RIGHT"),
        DraftSegment(96.0, 90.0, "RIGHT"),
        DraftSegment(15.0),
    )


def _кобилица1() -> tuple[DraftSegment, ...]:
    """F021 „Кобилица-1“, както е в редактора — начална посока НАГОРЕ."""

    return (
        DraftSegment(17.0, 90.0, "RIGHT"),
        DraftSegment(30.0, 45.0, "RIGHT"),
        DraftSegment(24.0, 45.0, "LEFT"),
        DraftSegment(50.0),
    )


def _форма(code: str, segments, initial_direction: str = "RIGHT") -> ShapeDefinition:
    return ShapeDefinition(
        code=code,
        name=code,
        state="LOCKED",
        locked=True,
        active=True,
        initial_direction=initial_direction,
        segments=[
            SegmentDefinition(
                index=index,
                length_code=f"L{index}",
                bend_after_deg=item.bend_after_deg,
                bend_direction=item.bend_direction,
                dimension_position=item.dimension_position,
                dimension_offset=item.dimension_offset,
            )
            for index, item in enumerate(segments, start=1)
        ],
    )


# --- геометрията -----------------------------------------------------------


def test_завоят_важи_след_участъка_и_последният_няма():
    """``physicalPoints``: посоката се мени СЛЕД участъка."""

    points = physical_points(_манш1())

    assert len(points) == 4
    assert points[0] == (0.0, 0.0)
    # L1 = 264 надясно, без завой преди него
    assert points[1] == pytest.approx((264.0, 0.0))
    # след L1 завой 29° НАДЯСНО, тоест посоката става -29°
    dx = points[2][0] - points[1][0]
    dy = points[2][1] - points[1][1]
    assert math.degrees(math.atan2(dy, dx)) == pytest.approx(-29.0)
    assert math.hypot(dx, dy) == pytest.approx(96.0)


def test_наляво_е_обратно_на_часовниковата():
    напред = physical_points((DraftSegment(10.0, 90.0, "LEFT"), DraftSegment(10.0)))
    assert напред[2][1] == pytest.approx(10.0)
    assert напред[2][0] == pytest.approx(10.0, abs=1e-9)

    назад = physical_points((DraftSegment(10.0, 90.0, "RIGHT"), DraftSegment(10.0)))
    assert назад[2][1] == pytest.approx(-10.0)


@pytest.mark.parametrize(
    "дума, градуси",
    [("RIGHT", 0.0), ("НАДЯСНО", 0.0), ("UP", 90.0), ("НАГОРЕ", 90.0),
     ("LEFT", 180.0), ("НАЛЯВО", 180.0), ("DOWN", -90.0), ("НАДОЛУ", -90.0)],
)
def test_началната_посока_е_на_четири_страни(дума, градуси):
    assert initial_heading(дума) == градуси


def test_непозната_начална_посока_пада_на_надясно():
    assert initial_heading("КЪМ_МОРЕТО") == 0.0
    assert initial_heading(None) == 0.0


def test_началната_посока_върти_цялата_фигура():
    """Кобилица-1 легнала: нагоре я прави широка, надясно — висока."""

    надясно = physical_points(_кобилица1(), initial_direction="RIGHT")
    нагоре = physical_points(_кобилица1(), initial_direction="UP")

    def размах(points):
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        return max(xs) - min(xs), max(ys) - min(ys)

    ш1, в1 = размах(надясно)
    ш2, в2 = размах(нагоре)
    assert в1 > ш1          # надясно: отвесна
    assert ш2 > в2          # нагоре: водоравна
    assert ш2 == pytest.approx(в1)


def test_вписването_центрира_и_обръща_Y():
    tx = fit_draft([(0.0, 0.0), (100.0, 0.0)])
    ляво, дясно = tx((0.0, 0.0)), tx((100.0, 0.0))
    assert (ляво[0] + дясно[0]) / 2 == pytest.approx(50.0)
    assert ляво[1] == pytest.approx(50.0)
    # по-високо в чертожните координати значи по-малко Y на екрана
    горе = fit_draft([(0.0, 0.0), (0.0, 100.0)])((0.0, 100.0))
    долу = fit_draft([(0.0, 0.0), (0.0, 100.0)])((0.0, 0.0))
    assert горе[1] < долу[1]


# --- размерите -------------------------------------------------------------


def _надписи(svg: str) -> list[str]:
    return re.findall(r'class="draft-dimension-text"[^>]*>([^<]+)<', svg)


def _завъртания(svg: str) -> list[float]:
    return [float(x) for x in re.findall(r"rotate\((-?[\d.]+)", svg)]


def test_размерът_е_УСПОРЕДЕН_на_участъка():
    """Същината на поправката: наклоненият участък получава завъртян надпис.

    Старият рисувач мереше ПРОЕКЦИЯТА по осите и слагаше водоравен надпис —
    затова „96 cm“ при стълбищното рамо не се четеше.
    """

    svg = render_draft_svg(_манш1())
    завъртания = _завъртания(svg)

    assert len(завъртания) == 3
    # Екранните координати са с обърнат Y, затова знакът е обратен на
    # чертожния: L2 слиза надясно и надписът му ляга на +29°.
    assert завъртания[0] == pytest.approx(0.0, abs=0.01)     # L1 водоравен
    assert завъртания[1] == pytest.approx(29.0, abs=0.01)    # L2 под 29°
    assert завъртания[2] == pytest.approx(-61.0, abs=0.01)   # L3 сведено от -119°


def test_завъртането_е_сведено_до_четимо():
    """Надпис никога не стои с главата надолу: ъгълът е в [-90, 90]."""

    for ъгъл in (0.0, 45.0, 135.0, 179.0, -135.0):
        svg = render_draft_svg((DraftSegment(50.0, ъгъл, "LEFT"), DraftSegment(50.0)))
        for завъртане in _завъртания(svg):
            assert -90.0 <= завъртане <= 90.0


def test_надписът_носи_мерната_единица():
    svg = render_draft_svg(_манш1())
    assert _надписи(svg) == ["264 cm", "96 cm", "15 cm"]


def test_размерът_има_изнасящи_линии_и_стрелки():
    svg = render_draft_svg(_манш1())
    assert svg.count('class="draft-extension"') == 6      # по две на участък
    assert svg.count('class="draft-dimension-line"') == 3
    assert "draftArrowStart" in svg and "draftArrowEnd" in svg


def test_скритият_размер_не_се_рисува():
    svg = render_draft_svg((
        DraftSegment(50.0, 90.0, "LEFT", dimension_position="СКРИЙ"),
        DraftSegment(50.0),
    ))
    assert _надписи(svg) == ["50 cm"]


def test_отстъпът_идва_от_сегмента_и_е_ограничен():
    близо = render_draft_svg((DraftSegment(50.0, 90.0, "LEFT", dimension_offset=2.0),
                              DraftSegment(50.0)))
    далеч = render_draft_svg((DraftSegment(50.0, 90.0, "LEFT", dimension_offset=18.0),
                              DraftSegment(50.0)))
    assert близо != далеч

    # Извън 2..18 се прибира обратно, а нулата пада на подразбирането 6.
    под = render_draft_svg((DraftSegment(50.0, 90.0, "LEFT", dimension_offset=-99.0),
                            DraftSegment(50.0)))
    над = render_draft_svg((DraftSegment(50.0, 90.0, "LEFT", dimension_offset=999.0),
                            DraftSegment(50.0)))
    assert под == близо
    assert над == далеч


@pytest.mark.parametrize(
    "позиция, очакван",
    [("TOP", 1.0), ("BOTTOM", -1.0), ("LEFT", -1.0), ("RIGHT", 1.0)],
)
def test_изричната_страна_решава(позиция, очакван):
    знак = choose_dimension_sign(позиция, (1.0, -1.0), (0.0, 0.0), (0.0, 0.0), 0)
    assert знак == очакван


def test_авто_изнася_навън_от_фигурата():
    # Средата е вдясно от центъра, нормалата сочи натам -> навън е положително.
    assert choose_dimension_sign("AUTO", (1.0, 0.0), (10.0, 0.0), (0.0, 0.0), 0) == 1.0
    assert choose_dimension_sign("AUTO", (-1.0, 0.0), (10.0, 0.0), (0.0, 0.0), 0) == -1.0


def test_авто_редува_когато_навън_не_значи_нищо():
    в_центъра = ((0.0, 1.0), (0.0, 0.0), (0.0, 0.0))
    assert choose_dimension_sign("AUTO", *в_центъра, 0) == -1.0
    assert choose_dimension_sign("AUTO", *в_центъра, 1) == 1.0


# --- форматът --------------------------------------------------------------


@pytest.mark.parametrize(
    "стойност, очакван",
    [(375.0, "375"), (374.999999999, "375"), (12.75, "12.75"),
     (379.5, "379.5"), (0.0, "0"), (float("nan"), "0"), (float("inf"), "0")],
)
def test_форматирането_е_на_редактора(стойност, очакван):
    assert fmt_draft(стойност) == очакван


# --- застъпващите се краища ------------------------------------------------


def test_застъпващите_се_краища_се_разделят():
    """Отметката „Разделяй застъпващи се крайни сегменти“ — само графично."""

    # Прътът се връща по СОБСТВЕНАТА си линия: L1 надясно 100, обръщане на
    # 180° и връщане 30 + 70 обратно до началото.
    points = physical_points((
        DraftSegment(100.0, 180.0, "LEFT"),
        DraftSegment(30.0, 0.0, "LEFT"),
        DraftSegment(70.0),
    ))
    преди = list(points)
    разделени = separate_overlapping_ends(points)

    assert разделени is True
    assert points[:2] == преди[:2]          # първият участък не мърда
    assert points[-1] != преди[-1]


def test_несъвпадащи_краища_не_се_пипат():
    points = physical_points(_манш1())
    преди = list(points)
    assert separate_overlapping_ends(points) is False
    assert points == преди


# --- формата от ENGINE -----------------------------------------------------


def test_дължините_идват_отвън_а_редът_от_дефиницията():
    форма = _форма("F021", _кобилица1(), initial_direction="UP")
    участъци = segments_from_shape(форма, [17.0, 30.0, 24.0, 50.0])

    assert [s.length_cm for s in участъци] == [17.0, 30.0, 24.0, 50.0]
    assert [s.bend_after_deg for s in участъци] == [90.0, 45.0, 45.0, None]
    assert [s.turns_left for s in участъци] == [False, False, True, False]


def test_различен_брой_размери_е_отказ():
    форма = _форма("F012", _манш1())
    with pytest.raises(ValueError, match="очаква 3"):
        segments_from_shape(форма, [264.0, 96.0])


def test_рисунката_на_форма_ползва_нейната_начална_посока():
    форма = _форма("F021", _кобилица1(), initial_direction="UP")
    svg = render_shape_draft_svg(форма, [17.0, 30.0, 24.0, 50.0])
    assert _надписи(svg) == ["17 cm", "30 cm", "24 cm", "50 cm"]
    # 24-ката е под 45°, а 17 и 30 и 50 са по осите
    завъртания = sorted(abs(x) for x in _завъртания(svg))
    # 17 е отвесен (90°), 30 и 50 са водоравни (0°), а 24 е под 45°.
    assert завъртания == pytest.approx([0.0, 0.0, 45.0, 90.0], abs=0.01)


def test_помощните_неща_на_редактора_остават_вън():
    """Пунктираната рамка и оранжевите точки са за редактора, не за описа."""

    # Стиловете им стоят в ``<style>`` и при двата случая; проверява се
    # дали САМИТЕ фигури се чертаят.
    svg = render_draft_svg(_манш1())
    assert '<rect class="safe-zone"' not in svg
    assert '<circle class="draft-point"' not in svg

    пълна = render_draft_svg(_манш1(), include_safe_zone=True, include_end_points=True)
    assert '<rect class="safe-zone"' in пълна
    assert пълна.count('<circle class="draft-point"') == 2


def test_празна_форма_е_отказ():
    with pytest.raises(ValueError):
        render_draft_svg(())


# --- маршрутът: коя форма кой рисувач получава ------------------------------


def test_заключените_форми_остават_на_MASTER_а_останалите_не():
    """Границата е на F009 — дотам заключеният HTML описва сам рисунките.

    Доказано червен срещу стария код: там границата беше ``MASTER_FORM_CODES``
    за параметрите, но рисунката на всичко над F009 минаваше през собствен,
    по-груб рисувач в ``shape_image``.
    """

    from lion_rebar.export.shape_image import LOCKED_MASTER_DRAWN_CODES

    for index in range(1, 10):
        assert f"F{index:03d}" in LOCKED_MASTER_DRAWN_CODES
    for code in ("F010", "F012", "F013", "F019", "F020", "F021"):
        assert code not in LOCKED_MASTER_DRAWN_CODES


def test_старият_груб_рисувач_е_махнат():
    """``render_engine_shape_svg`` рисуваше надписи ``L1=50 cm`` върху пръта.

    Оставен на място, той е втори стандарт, който чака да бъде извикан пак.
    """

    import lion_rebar.export.shape_image as shape_image

    assert not hasattr(shape_image, "render_engine_shape_svg")
