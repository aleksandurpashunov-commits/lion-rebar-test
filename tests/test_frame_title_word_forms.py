"""Щемпелът „ЧЕРТЕЖ“ + „АРМИРАНЕ НА ШАЙБА N“ — словоформата, не прагът.

Измерено 2026-09-21 върху истинския чертеж на трети офис
(`S-SR-WALL_7_8_9.dxf`, инж. Георги Цвятков): щемпелът е точно нашият формат —
три маркера „ЧЕРТЕЖ“ на слой ``STANDARD`` с височина 7,5, и до всеки от тях
заглавието „АРМИРАНЕ НА ШАЙБА 7/8/9“ с височина 15,0 на

    dx = 211,1   dy = -10,2

тоест **вътре** в доказания основен прозорец (``dx 50..1800``,
``dy -12..15``). ``classify_title`` вече връща за него („WALLS“, „HIGH“).

Единствената причина рамките да излизат ``NO_TITLE_FOUND``, категорията им да
остава ``UNKNOWN`` и ``UNKNOWN_FRAME`` да спира ВСИЧКИТЕ 805 позиции е, че
филтърът за кандидат-заглавие искаше думата „АРМИРОВ“, а проектантът е написал
„АРМИРАНЕ“ — две словоформи на един корен.

Затова поправката е в РЕЧНИКА, не в разстояние: коренът „АРМИР“ покрива и
„армировка“, и „армиране“, и „армирана“. Нито един праг не се мести — това е
нарочно, защото повтарящият се дефект в проекта е близост, приета за
принадлежност.
"""
from __future__ import annotations

from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.export.frame_detection import (
    TITLE_TOKENS,
    detect_drawing_frames,
    unresolved_frames,
)

# Измерените стойности от чертежа: маркерът е по-нисък от заглавието си.
МАРКЕР_ВИСОЧИНА = 7.5
ЗАГЛАВИЕ_ВИСОЧИНА = 15.0
DX = 211.1
DY = -10.2


def _текст(handle: str, text: str, *, insert, layer: str, height: float) -> DxfEntityRecord:
    return DxfEntityRecord(
        handle=handle,
        entity_type="TEXT",
        layer=layer,
        geometry={"insert": list(insert), "height": height},
        text=text,
    )


def _щемпел(индекс: int, *, x: float, y: float, заглавие: str) -> list[DxfEntityRecord]:
    """Маркер „ЧЕРТЕЖ“ и заглавието му на измереното отстояние."""

    return [
        _текст(
            f"M{индекс}",
            "ЧЕРТЕЖ",
            insert=(x, y, 0.0),
            layer="STANDARD",
            height=МАРКЕР_ВИСОЧИНА,
        ),
        _текст(
            f"T{индекс}",
            заглавие,
            insert=(x + DX, y + DY, 0.0),
            layer="STANDARD",
            height=ЗАГЛАВИЕ_ВИСОЧИНА,
        ),
    ]


def test_armirane_word_form_resolves_the_wall_sheets() -> None:
    """Трите шайби от истинския чертеж получават категория ШАЙБИ."""

    entities: list[DxfEntityRecord] = []
    for индекс, (x, y, номер) in enumerate(
        ((-4070.0, -15599.0, 7), (1844.0, -15599.0, 8), (7912.0, -15619.0, 9)),
        start=1,
    ):
        entities.extend(_щемпел(индекс, x=x, y=y, заглавие=f"АРМИРАНЕ НА ШАЙБА {номер}"))

    frames = detect_drawing_frames(entities)

    assert len(frames) == 3
    assert [frame.category_code for frame in frames] == ["WALLS", "WALLS", "WALLS"]
    assert [frame.title_text for frame in frames] == [
        "АРМИРАНЕ НА ШАЙБА 7",
        "АРМИРАНЕ НА ШАЙБА 8",
        "АРМИРАНЕ НА ШАЙБА 9",
    ]
    # Заглавието е в основния прозорец, затова доказателството е силно:
    # резервният радиус не се задейства и увереността не се понижава.
    assert [frame.resolution_reason for frame in frames] == ["PRIMARY_WINDOW"] * 3
    assert not unresolved_frames(frames)


def test_armirovka_word_form_keeps_working() -> None:
    """Старата словоформа не бива да пострада от новия корен."""

    entities = _щемпел(1, x=0.0, y=0.0, заглавие="ДОЛНА АРМИРОВКА")

    frames = detect_drawing_frames(entities)

    assert len(frames) == 1
    assert frames[0].category_code == "BOTTOM"
    assert frames[0].resolution_reason == "PRIMARY_WINDOW"


def test_root_covers_both_word_forms() -> None:
    """Речникът пази корена, а не двете форми поотделно."""

    assert "АРМИР" in TITLE_TOKENS
    assert all("АРМИРОВ" != token for token in TITLE_TOKENS)


def test_bare_armirane_note_is_not_a_title_without_a_marker() -> None:
    """Коренът е по-широк, затова проверката за бележка без щемпел остава.

    Бележка, която съдържа „армиране“, но не стои до маркер „ЧЕРТЕЖ“, не прави
    рамка. Прозорецът е geometrичното доказателство и то не се разхлабва.
    """

    entities = [
        _текст(
            "N1",
            "Армирането да се извърши съгласно проекта",
            insert=(5000.0, 5000.0, 0.0),
            layer="Text - General",
            height=15.0,
        )
    ]

    assert detect_drawing_frames(entities) == []
