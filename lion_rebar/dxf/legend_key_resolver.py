"""Разрешаване на позиции чрез легендата с кръгчета.

При вертикалните елементи проектантът маркира надлъжната армировка с
кръгче (``5a``, ``5b``, ``5c``, ``5d``), а долу в чертежа стои легенда:

    5a -> "N18 L=410"  +  "За Стб.ядро - глави"
    5b -> "N16 L=400"  +  "За Стб.колони над кота 0,00"

Кръгчето дава Ø и дължина; текстът до него дава предназначението.

ЗАЩО Е ОТДЕЛЕН МОДУЛ
--------------------
Позиция, чието кръгче сочи към легендата, няма собствени котировки около
себе си -- размерите ѝ идват от друго място в чертежа. Без този път тя
остава ``НЯМА КАНДИДАТ`` и операторът вижда празен панел, макар данните
да са в чертежа.

FAIL-CLOSED
-----------
Съответствие се приема само когато Ø И дължината от легендата съвпадат с
надписа на позицията. При разминаване -- отказ, не корекция.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

#: Кръгче: номер + буква и незадължителна звездичка, напр. ``5b``/``3b*``.
#: Регистърът няма значение.  Звездичката е част от ключа и не трябва да се
#: губи, защото ``3b`` и ``3b*`` могат да са различни легендни позиции.
KEY_PATTERN = re.compile(r"^(\d{1,3}[A-Za-zА-Яа-я](?:\*)?)$")

#: Легенден ред: ``N16 L=400`` или реалният вариант ``N16 - L=400``.
LEGEND_PATTERN = re.compile(
    r"^N\s*(\d+)\s*(?:-\s*)?L\s*=\s*(\d+(?:[.,]\d+)?)\s*$",
    re.IGNORECASE,
)

#: Радиус, в който кръгче се смята за принадлежащо на позиция.
#: Умишлено тесен: при 220 единици стреме Ø8 L=115 грабваше кръгчето 5b на
#: съседната надлъжна армировка (Ø16 L=400) и панелът показваше подвеждащ
#: ред. По-добре нищо, отколкото чуждо кръгче.
KEY_RADIUS = 120.0

#: Радиус, в който легенден ред се свързва с кръгчето си.
LEGEND_RADIUS = 260.0


def _text(value: Any) -> str:
    return " ".join(str(value if value is not None else "").split()).strip()


def _number(value: str) -> float | None:
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class LegendEntry:
    """Един ред от легендата, свързан с ключа си."""

    key: str
    diameter_mm: int
    length_cm: float
    purpose: str = ""

    def to_dict(self) -> dict:
        return {
            "key": self.key, "diameter_mm": self.diameter_mm,
            "length_cm": self.length_cm, "purpose": self.purpose,
        }


def _distance(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def collect_legend(texts: Sequence[Mapping[str, Any]]) -> dict[str, LegendEntry]:
    """Сглобява легендата: кръгче -> Ø и дължина.

    Легендата се разпознава по СЪВПАДЕНИЕ НА РАЗПОЛОЖЕНИЕТО: легенден ред
    ``N16 L=400`` се свързва с най-близкото кръгче. Ключ, който се среща
    повече от веднъж с различни стойности, се ОТХВЪРЛЯ -- двусмислена
    легенда е по-лоша от липсваща.
    """

    keys: list[tuple[str, tuple[float, float]]] = []
    rows: list[tuple[int, float, tuple[float, float]]] = []
    notes: list[tuple[str, tuple[float, float]]] = []
    for item in texts:
        point = item.get("point")
        value = _text(item.get("text"))
        if not point or not value:
            continue
        key_match = KEY_PATTERN.match(value)
        if key_match:
            keys.append((key_match.group(1).lower(), point))
            continue
        legend_match = LEGEND_PATTERN.match(value)
        if legend_match:
            diameter = int(legend_match.group(1))
            length = _number(legend_match.group(2))
            if length:
                rows.append((diameter, length, point))
            continue
        if value.lower().startswith("за "):
            notes.append((value, point))

    found: dict[str, list[LegendEntry]] = {}
    for diameter, length, point in rows:
        nearby = [(distance, key) for key, key_point in keys
                  if (distance := _distance(point, key_point)) <= LEGEND_RADIUS]
        if not nearby:
            continue
        nearby.sort()
        key = nearby[0][1]
        purpose = ""
        note_candidates = [(_distance(point, note_point), note)
                           for note, note_point in notes]
        if note_candidates:
            note_candidates.sort()
            if note_candidates[0][0] <= LEGEND_RADIUS:
                purpose = note_candidates[0][1]
        found.setdefault(key, []).append(
            LegendEntry(key=key, diameter_mm=diameter, length_cm=length,
                        purpose=purpose))

    legend: dict[str, LegendEntry] = {}
    for key, entries in found.items():
        unique = {(entry.diameter_mm, entry.length_cm) for entry in entries}
        if len(unique) == 1:
            legend[key] = entries[0]
    return legend


def key_for_position(point, texts: Sequence[Mapping[str, Any]]) -> str | None:
    """Намира кръгчето, което принадлежи на позицията."""

    best: tuple[float, str] | None = None
    for item in texts:
        item_point = item.get("point")
        value = _text(item.get("text"))
        if not item_point or not value:
            continue
        match = KEY_PATTERN.match(value)
        if not match:
            continue
        distance = _distance(point, item_point)
        if distance > KEY_RADIUS:
            continue
        if best is None or distance < best[0]:
            best = (distance, match.group(1).lower())
    return best[1] if best else None


def resolve_from_legend(
    point,
    texts: Sequence[Mapping[str, Any]],
    legend: Mapping[str, LegendEntry],
    *,
    diameter_mm: int | None = None,
    length_cm: float | None = None,
) -> LegendEntry | None:
    """Разрешава позиция чрез легендата.

    Приема се САМО когато Ø и дължината от легендата съвпадат с надписа на
    позицията. Разминаване означава, че кръгчето е чуждо -- тогава отказ.
    """

    key = key_for_position(point, texts)
    if not key:
        return None
    entry = legend.get(key)
    if entry is None:
        return None
    if diameter_mm is not None and int(diameter_mm) != entry.diameter_mm:
        return None
    if length_cm is not None and abs(float(length_cm) - entry.length_cm) > 0.5:
        return None
    return entry


def legend_texts_from_entities(entities: Iterable[Any]) -> list[dict]:
    """Извлича текстовете, нужни за легендата, от импортираните обекти."""

    result: list[dict] = []
    for entity in entities:
        text = getattr(entity, "text", None)
        if not text:
            continue
        geometry = getattr(entity, "geometry", None) or {}
        # TEXT insertion is a baseline point and can fall closer to a
        # neighbouring row in compact legends.  Prefer the rendered anchor
        # produced by the importer; keep fallbacks for legacy/project tests.
        point = (
            geometry.get("visual_anchor")
            or geometry.get("insert")
            or geometry.get("location")
        )
        if not point:
            continue
        result.append({"text": text, "point": (point[0], point[1])})
    return result
