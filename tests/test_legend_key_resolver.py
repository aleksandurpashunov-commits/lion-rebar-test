"""Тестове за легендата с кръгчета при вертикални елементи.

Реален случай: `40N14x385` има кръгче `5d`, а легендата долу в чертежа
казва `N14 L=385`. Без този път позицията оставаше „НЯМА КАНДИДАТ“ и
операторът виждаше празен панел.
"""
from __future__ import annotations

from lion_rebar.dxf.legend_key_resolver import (
    collect_legend,
    key_for_position,
    resolve_from_legend,
)


def _text(value, x, y):
    return {"text": value, "point": (float(x), float(y))}


def _legend_block():
    return [
        _text("5a", 0, 0), _text("N18 L=410", 60, 0),
        _text("За Стб.ядро - глави", 60, 30),
        _text("5b", 0, 200), _text("N16 L=400", 60, 200),
        _text("5c", 0, 400), _text("N8 L=360", 60, 400),
        _text("5d", 0, 600), _text("N14 L=385", 60, 600),
    ]


def test_legend_is_collected_by_proximity():
    legend = collect_legend(_legend_block())
    assert set(legend) == {"5a", "5b", "5c", "5d"}
    assert legend["5d"].diameter_mm == 14
    assert legend["5d"].length_cm == 385.0


def test_purpose_text_is_attached():
    legend = collect_legend(_legend_block())
    assert "ядро" in legend["5a"].purpose


def test_ambiguous_key_is_rejected():
    """Един ключ с две различни стойности е по-лошо от липсващ."""

    texts = [_text("5b", 0, 0), _text("N16 L=400", 60, 0),
             _text("5b", 0, 900), _text("N12 L=300", 60, 900)]
    assert "5b" not in collect_legend(texts)


def test_position_finds_its_key():
    texts = [_text("5d", 100, 100)]
    assert key_for_position((120.0, 110.0), texts) == "5d"


def test_distant_key_is_not_claimed():
    texts = [_text("5d", 5000, 5000)]
    assert key_for_position((0.0, 0.0), texts) is None


def test_resolution_requires_matching_diameter_and_length():
    legend = collect_legend(_legend_block())
    texts = _legend_block() + [_text("5d", 1000, 1000)]
    entry = resolve_from_legend((1010.0, 1000.0), texts, legend,
                                diameter_mm=14, length_cm=385)
    assert entry is not None and entry.key == "5d"


def test_mismatched_diameter_is_refused():
    """Разминаване значи чуждо кръгче -> отказ, не корекция."""

    legend = collect_legend(_legend_block())
    texts = _legend_block() + [_text("5d", 1000, 1000)]
    assert resolve_from_legend((1010.0, 1000.0), texts, legend,
                               diameter_mm=16, length_cm=385) is None


def test_mismatched_length_is_refused():
    legend = collect_legend(_legend_block())
    texts = _legend_block() + [_text("5d", 1000, 1000)]
    assert resolve_from_legend((1010.0, 1000.0), texts, legend,
                               diameter_mm=14, length_cm=400) is None


def test_unknown_key_returns_none():
    legend = collect_legend(_legend_block())
    texts = [_text("9z", 1000, 1000)]
    assert resolve_from_legend((1000.0, 1000.0), texts, legend) is None


def test_h3_starred_key_beats_neighbouring_3f_and_accepts_hyphenated_row():
    texts = [
        # Global legend row.
        _text("3b*", 1000, 1000),
        _text("N20 - L=380", 1000, 1055),
        # Local H-3-like pair: the correct key is only slightly closer than 3f.
        _text("3b*", -54, 9),
        _text("3f", -53, -18),
    ]

    legend = collect_legend(texts)
    assert legend["3b*"].diameter_mm == 20
    assert legend["3b*"].length_cm == 380.0
    assert key_for_position((0.0, 0.0), texts) == "3b*"
    entry = resolve_from_legend(
        (0.0, 0.0),
        texts,
        legend,
        diameter_mm=20,
        length_cm=380,
    )
    assert entry is not None
    assert entry.key == "3b*"


def test_resolver_is_wired_into_the_analysis():
    from pathlib import Path

    source = Path("lion_rebar/dxf/batch_geometry_report.py").read_text(encoding="utf-8")
    assert "from lion_rebar.dxf.legend_key_resolver import (" in source
    assert 'row["legend_key"] = key' in source
    assert 'row["legend_confirmed"] = True' in source


def test_panel_shows_confirmed_legend_only():
    """Показва се само ПОТВЪРДЕНА легенда.

    Непотвърденото кръгче почти винаги е на съседна позиция: стреме
    Ø8 L=115 грабваше 5b на надлъжната армировка Ø16 L=400.
    """

    from pathlib import Path

    source = Path("lion_rebar/ui/review_center.py").read_text(encoding="utf-8")
    assert "Легенда" in source
    assert "легендата не потвърждава" not in source
