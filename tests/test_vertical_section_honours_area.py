"""Начертаната рамка решава и вертикалната секция (rc44).

Собственик, 2026-09-15: рамките „да са само оформящи за да знае програмата
коя шайба, колона и геометрията и надписите къде принадлежат".

КАКВО БЕШЕ КРИВО
----------------
``_assign_vertical_section`` избира най-близкото заглавие ОТГОРЕ и отказва
само когато вторият кандидат е по-близо от ``VERTICAL_SECTION_MIN_MARGIN``.
Прагът е ``1.0`` — при чертеж в милиметри това значи „никога не отказвай".

Реалният случай, ``s-10``: в секцията „K.B.11,11a - 88x25 - 2 бр." паднаха
стремена **115x20** и **165x20**. Елементът е широк 88 cm — 165 cm стреме
не влиза в него. Двата надписа са най-слабо назначените в целия чертеж
(марж 116 при 126 надписа), а прагът ги пусна като сигурни. Заедно с 325
кукички Ø6 това са 97 kg от 312 kg под тази секция, тоест 31% чуждо желязо.

Груповата област съществуваше, но етикетът ѝ (``column_group_label``) се
ползва от клон, до който VERTICALS лист не стига: разклонението за
вертикални секции е преди него.

КАКВО Е СЕГА
------------
Областта се пита ПЪРВА и сочи истински handle на заглавие; секциите се
разпознават по същия handle, тоест съвпадението е точно, не по близост.
Ако handle-ът не отговаря на никоя секция, остава геометрията — рамката е
за нещо друго и не бива да блокира описа.
"""
from __future__ import annotations

from lion_rebar.export.excel_export import (
    VerticalSectionLabel,
    _assign_vertical_section,
)
from lion_rebar.export.operator_areas import KIND_BEAM, KIND_GROUP, OperatorArea


def _секция(order: int, title: str, handle: str, divider_y: float) -> VerticalSectionLabel:
    return VerticalSectionLabel(
        frame_index=1,
        title_handle=handle,
        divider_handle=f"D{handle}",
        title=title,
        kind="COLUMN",
        order=order,
        xmin=0.0,
        xmax=1000.0,
        divider_y=divider_y,
    )


def _област(title_handle: str, title: str, kind: str = KIND_GROUP) -> OperatorArea:
    return OperatorArea(
        area_id=f"A{title_handle}",
        x0=100.0,
        y0=100.0,
        x1=900.0,
        y1=900.0,
        kind=kind,
        title_handle=title_handle,
        title_text=title,
        frame_index=1,
        decided_by="оператор",
        note="оградено преди анализа",
        at="2026-09-15T12:00:00+00:00",
        project_id="p",
        source_sha256="s",
        source_generation_id="g",
        analysis_run_id="r",
    )


#: „K.B.11,11a" е по-далеч отгоре, „Стб.Ядро B.1" е по-близо — затова
#: геометрията дава ядрото, а операторът е оградил колоната.
_ЯДРО = _секция(1, "Стб.Ядро B.1", "T1", divider_y=1200.0)
_КОЛОНА = _секция(2, "K.B.11,11a - 88x25", "T2", divider_y=5000.0)
_СЕКЦИИ = [_ЯДРО, _КОЛОНА]
_ТОЧКА = (500.0, 500.0)


class TestБезРамка:
    def test_геометрията_дава_по_близкото_заглавие(self) -> None:
        решение = _assign_vertical_section(*_ТОЧКА, 1, _СЕКЦИИ)
        assert решение is not None
        assert решение.section.title == "Стб.Ядро B.1"
        assert решение.method == "AUTO_VERTICAL_SECTION_V1"


class TestСРамка:
    def test_рамката_бие_геометрията(self) -> None:
        решение = _assign_vertical_section(
            *_ТОЧКА, 1, _СЕКЦИИ, operator_areas=[_област("T2", "K.B.11,11a - 88x25")]
        )
        assert решение is not None
        assert решение.section.title == "K.B.11,11a - 88x25"

    def test_личи_че_решението_е_на_оператора(self) -> None:
        """Иначе в проследимостта не се вижда кое е ръчно и кое познато."""

        решение = _assign_vertical_section(
            *_ТОЧКА, 1, _СЕКЦИИ, operator_areas=[_област("T2", "K.B.11,11a - 88x25")]
        )
        assert решение.method == "OPERATOR_AREA_VERTICAL"

    def test_рамката_потвърждава_и_когато_съвпада(self) -> None:
        решение = _assign_vertical_section(
            *_ТОЧКА, 1, _СЕКЦИИ, operator_areas=[_област("T1", "Стб.Ядро B.1")]
        )
        assert решение.section.title == "Стб.Ядро B.1"
        assert решение.method == "OPERATOR_AREA_VERTICAL"


class TestНеПречи:
    def test_точка_извън_рамката_си_остава_на_геометрията(self) -> None:
        решение = _assign_vertical_section(
            5000.0, 500.0, 1, _СЕКЦИИ,
            operator_areas=[_област("T2", "K.B.11,11a - 88x25")],
        )
        # Извън областта -> старият път. Тук x е извън xmax на секциите,
        # затова решението е отказ, не чуждо назначение.
        assert решение is None or решение.method == "AUTO_VERTICAL_SECTION_V1"

    def test_гредова_рамка_не_пипа_вертикалите(self) -> None:
        """Рамка около греда няма работа във вертикален лист."""

        решение = _assign_vertical_section(
            *_ТОЧКА, 1, _СЕКЦИИ,
            operator_areas=[_област("T2", "Гр. B.7", kind=KIND_BEAM)],
        )
        assert решение.section.title == "Стб.Ядро B.1"
        assert решение.method == "AUTO_VERTICAL_SECTION_V1"

    def test_непознат_handle_не_блокира(self) -> None:
        """Рамка около нещо, което не е секционно заглавие, се подминава."""

        решение = _assign_vertical_section(
            *_ТОЧКА, 1, _СЕКЦИИ, operator_areas=[_област("НЯМА", "друго")]
        )
        assert решение is not None
        assert решение.section.title == "Стб.Ядро B.1"
        assert решение.method == "AUTO_VERTICAL_SECTION_V1"

    def test_празен_списък_е_старото_поведение(self) -> None:
        без = _assign_vertical_section(*_ТОЧКА, 1, _СЕКЦИИ)
        с_празен = _assign_vertical_section(*_ТОЧКА, 1, _СЕКЦИИ, operator_areas=[])
        assert без == с_празен


class TestИзключването:
    """Оградената секция взима САМО каквото е в рамката ѝ.

    Без това ограждането на един елемент не го чисти: рамката прибира
    каквото е вътре, но каквото е вън пак пада върху него по близост.

    В ``s-10`` трите чужди позиции на „K.B.11,11a" стоят ИЗВЪН заглавието му
    — на ``x 4263..4314`` при край ``4267`` — тоест рамка около колоната
    нямаше да ги премести. С изключването същата рамка ги праща в
    „Стб.Ядро B.3": ядро, където стреме 165x20 е възможно, за разлика от
    колона широка 88 cm.
    """

    #: Извън рамката (x > 900), но по-близо до ЯДРОТО по геометрия.
    _ИЗВЪН = (950.0, 500.0)

    def test_без_рамка_точката_отива_при_по_близкото(self) -> None:
        решение = _assign_vertical_section(*self._ИЗВЪН, 1, _СЕКЦИИ)
        assert решение.section.title == "Стб.Ядро B.1"

    def test_оградена_секция_не_взима_отвън(self) -> None:
        решение = _assign_vertical_section(
            *self._ИЗВЪН, 1, _СЕКЦИИ, operator_areas=[_област("T1", "Стб.Ядро B.1")]
        )
        assert решение is not None
        assert решение.section.title == "K.B.11,11a - 88x25"
        assert решение.method == "AUTO_VERTICAL_SECTION_V1"

    def test_неоградените_секции_не_се_пипат(self) -> None:
        """Ограждаш една — останалите продължават както преди."""

        решение = _assign_vertical_section(
            *self._ИЗВЪН, 1, _СЕКЦИИ, operator_areas=[_област("T2", "K.B.11,11a - 88x25")]
        )
        assert решение.section.title == "Стб.Ядро B.1"

    def test_вътре_в_рамката_пак_е_нейно(self) -> None:
        решение = _assign_vertical_section(
            *_ТОЧКА, 1, _СЕКЦИИ, operator_areas=[_област("T1", "Стб.Ядро B.1")]
        )
        assert решение.section.title == "Стб.Ядро B.1"
        assert решение.method == "OPERATOR_AREA_VERTICAL"
