"""Column-by-column working sheet layout for the koloni dialect (rc44).

Owner-requested 2026-09-12: "опис в колони - подробно колона по колона или
група от колони". The drawing itself is organised as one titled block per
column group ("К23, К24 (35/35) - 2бр."), so the working sheet is too.

"Общо" is deliberately untouched by this: it still aggregates one row per
production identity across every column, which is exactly what the owner
asked for ("на втората страница ... всички да са сумирани без значение кое
в коя колона е").
"""
from __future__ import annotations

from lion_rebar.export.excel_export import (
    ColumnGroupSection,
    ExportPosition,
    _column_group_sections,
    _column_group_sort_key,
)


def _position(
    handle: str,
    *,
    label: str,
    shape: str = "F006",
    diameter: float = 8.0,
    length: float = 170.0,
    segments: tuple[float, ...] = (8.0, 51.0, 26.0, 51.0, 26.0, 8.0),
    count: int = 50,
) -> ExportPosition:
    return ExportPosition(
        annotation_handle=handle,
        annotation_text=f"{count}N{int(diameter)}x{int(length)}",
        count=count,
        diameter_mm=diameter,
        production_length_cm=length,
        shape_code=shape,
        shape_name=shape,
        shape_revision=1,
        geometry_handles=(),
        frame_index=1,
        segment_lengths_cm=segments,
        column_group_label=label,
    )


def test_each_column_group_becomes_its_own_titled_block() -> None:
    positions = [
        _position("A", label="%%UK23, K24 (35/35) - 2бр."),
        _position("B", label="%%UK30, K31 (35/90) - 2бр."),
    ]

    sections = _column_group_sections(positions)

    assert [section.title for section in sections] == [
        "K23, K24 (35/35) - 2бр.",
        "K30, K31 (35/90) - 2бр.",
    ]
    assert all(isinstance(section, ColumnGroupSection) for section in sections)
    assert [len(section.groups) for section in sections] == [1, 1]


def test_autocad_underline_control_code_is_stripped_from_the_title() -> None:
    sections = _column_group_sections(
        [_position("A", label="%%UK32 - 1бр.")]
    )

    assert sections[0].title == "K32 - 1бр."


def test_identical_specs_merge_inside_a_group_but_never_across_groups() -> None:
    """Within one column group the same production identity is one line
    ("25*2=50N8x140"); the same identity in a DIFFERENT column group stays a
    separate line, because the sheet is read column by column."""

    same_group = [
        _position("A", label="%%UK23, K24 (35/35) - 2бр."),
        _position("B", label="%%UK23, K24 (35/35) - 2бр."),
    ]
    other_group = [_position("C", label="%%UK30, K31 (35/90) - 2бр.")]

    sections = _column_group_sections([*same_group, *other_group])

    by_title = {section.title: section for section in sections}
    merged = by_title["K23, K24 (35/35) - 2бр."]
    assert len(merged.groups) == 1
    assert len(merged.groups[0].positions) == 2
    assert len(by_title["K30, K31 (35/90) - 2бр."].groups) == 1
    # Same production identity, still two separate source rows.
    assert merged.groups[0].key == by_title["K30, K31 (35/90) - 2бр."].groups[0].key


def test_different_specs_inside_one_group_each_get_their_own_row() -> None:
    positions = [
        _position("A", label="%%UK23, K24 (35/35) - 2бр."),
        _position(
            "B",
            label="%%UK23, K24 (35/35) - 2бр.",
            shape="F001",
            diameter=14.0,
            length=415.0,
            segments=(415.0,),
            count=16,
        ),
    ]

    sections = _column_group_sections(positions)

    assert len(sections) == 1
    assert len(sections[0].groups) == 2


def test_blocks_are_ordered_by_column_number_with_unlabelled_last() -> None:
    positions = [
        _position("A", label="%%UK30, K31 (35/90) - 2бр."),
        _position("B", label=""),
        _position("C", label="%%UK23, K24 (35/35) - 2бр."),
        _position("D", label="%%UВП1 (25/50) - 1бр."),
    ]

    sections = _column_group_sections(positions)

    assert [section.title for section in sections] == [
        "K23, K24 (35/35) - 2бр.",
        "K30, K31 (35/90) - 2бр.",
        "ВП1 (25/50) - 1бр.",
        "",
    ]


def test_sort_key_puts_an_unlabelled_block_after_every_real_one() -> None:
    assert _column_group_sort_key("%%UK9 - 1бр.") < _column_group_sort_key("")
    assert _column_group_sort_key("K2 - 1бр.") < _column_group_sort_key("K10 - 1бр.")


def test_production_identity_is_independent_of_the_column_group() -> None:
    """The layout label must never leak into ``group_key`` -- that key is what
    "Общо" aggregates on, and it has to stay identical across columns."""

    here = _position("A", label="%%UK23, K24 (35/35) - 2бр.")
    there = _position("B", label="%%UK75, K76 (25/50) - 6бр.")

    assert here.group_key == there.group_key
