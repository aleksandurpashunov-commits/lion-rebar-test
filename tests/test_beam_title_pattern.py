"""Заглавието на гредата се чете и без буквена група.

ЗАЩО СЪЩЕСТВУВА. „prev_s-11_v1" (лист „АРМИРОВКА ГРЕДИ НА КОТА +3.50") даваше
`MISSING_BEAM` за 196 от 290 позиции — две трети от листа. Рамката беше вярна,
позициите бяха разпознати, спираше ги само образецът за заглавие на греда: той
искаше буквена група (`Гр. B.8 25/55`, както пише `s-9`), а този проектант пише
`Гр.1 25/80`.

Измерено преди промяната: 59 заглавия в чертежа, **0 приети**. След нея: 59 от
59, а `s-9` (32), `s-11` (0) и `s-8` (0) остават непроменени.

Текстовете тук са ИСТИНСКИ — прочетени от двата чертежа, не измислени.
"""
from __future__ import annotations

from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.export.excel_export import _detect_beam_labels
from lion_rebar.export.frame_detection import DrawingFrame

#: Както ги пише проектантът на „prev_s-11_v1".
БЕЗ_БУКВА = ("Гр.1 25/80", "Гр.15 25/72", "Гр.20 25/80", "Гр.50 25/80", "Гр.2 25/46")
#: Както ги пише проектантът на „s-9".
С_БУКВА = ("Гр. B.8 25/55", "Гр. B.2 25/50", "Гр. B.5 25/55R", "Гр. B.11 25/34")
#: Текстове, които НЕ са заглавия на греди.
ЧУЖДИ = (
    "АРМИРОВКА ГРЕДИ НА КОТА +3.50",
    "ТАБЛИЦА ЗА ОГЪВАНЕ НА АРМИРОВКA",
    "стр. 59N8/s=20",
    "4N12x240",
    "ГРЕДА",
    "Гр.",
)


def _рамка() -> DrawingFrame:
    return DrawingFrame(
        index=1,
        marker_handle="F1",
        anchor_x=0.0,
        anchor_y=0.0,
        title_text="АРМИРОВКА ГРЕДИ НА КОТА +3.50",
        supporting_text="",
        category_code="BEAMS",
        suggested_sheet_name="греди",
        confidence="HIGH",
        resolution_reason="TEST",
        x_min=-10_000.0,
        x_max=10_000.0,
    )


def _надпис(handle: str, text: str, y: float) -> DxfEntityRecord:
    return DxfEntityRecord(
        handle=handle,
        entity_type="TEXT",
        layer="Section",
        text=text,
        geometry={"insert": [100.0, y, 0.0], "height": 10.0},
    )


def _заглавия(текстове) -> list[str]:
    обекти = [
        _надпис(f"B{n}", текст, 1000.0 - 400.0 * n)
        for n, текст in enumerate(текстове, start=1)
    ]
    return [етикет.title for етикет in _detect_beam_labels(обекти, [_рамка()])]


def test_gredi_bez_bukvena_grupa_se_chetat() -> None:
    """Точно случаят от „prev_s-11_v1": 0 от 59 ставаха 59 от 59."""

    assert _заглавия(БЕЗ_БУКВА) == list(БЕЗ_БУКВА)


def test_gredi_s_bukvena_grupa_ostavat_nepromeneni() -> None:
    """`s-9` четеше 32 от 32 и продължава да ги чете."""

    assert _заглавия(С_БУКВА) == list(С_БУКВА)


def test_chuzhdite_tekstove_ne_stavat_gredi() -> None:
    """Разширението е по буквата, не по всичко, което започва с „ГР“."""

    assert _заглавия(ЧУЖДИ) == []


def test_zaglavieto_na_ramkata_ne_e_greda() -> None:
    """„АРМИРОВКА ГРЕДИ…“ стои в същия чертеж и не бива да стане греда."""

    смесено = ("АРМИРОВКА ГРЕДИ НА КОТА +3.50", "Гр.1 25/80", "Гр. B.8 25/55")
    assert _заглавия(смесено) == ["Гр.1 25/80", "Гр. B.8 25/55"]
