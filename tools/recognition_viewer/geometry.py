# -*- coding: utf-8 -*-
"""Обектите на чертежа → SVG пътища. Само четене и рисуване.

Рисува се ТОВА, КОЕТО ПРОГРАМАТА ВИЖДА: входът са записите на собствения
вносител (``lion_rebar.dxf.importer``), а не суров ezdxf. Вносителят разгъва
блоковете и чете proxy обектите; ако рисувахме покрай него, щяхме да гледаме
различна картина от тази, по която програмата решава.

Изгледът е с обърнат Y (``scene_y = -dxf_y``) — както в самата програма.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

#: Колко отсечки на четвърт окръжност при разгъване на дъга.
ОТСЕЧКИ_НА_ЧЕТВЪРТ = 8

#: Под толкова единици обект не се рисува — няма какво да се види.
МИН_РАЗМЕР = 1e-6


@dataclass(frozen=True)
class Нарисуван:
    """Един обект, готов за рисуване."""

    handle: str
    entity_type: str
    layer: str
    #: SVG ``d`` на пътя, вече с обърнат Y.
    path: str
    #: Обхват в координатите на SVG (x0, y0, x1, y1).
    bbox: tuple[float, float, float, float]
    is_text: bool = False
    text: str = ""
    #: Къде и колко голям е надписът — в координатите на SVG (с обърнат Y).
    insert: tuple[float, float] | None = None
    size: float = 0.0
    rotation: float = 0.0




B = chr(92)

#: Кодовете на MTEXT, които не са част от съдържанието.
_MTEXT_КОДОВЕ = re.compile(B * 2 + "[A-Za-z][^;" + B * 2 + "]*;|[{}]")


def изчисти_надпис(стойност: Any) -> str:
    """Съдържанието на надписа без форматиращите кодове на MTEXT."""

    B = chr(92)
    текст = str(стойност or "")
    текст = текст.replace(B + "P", " ").replace(B + "~", " ")
    текст = _MTEXT_КОДОВЕ.sub("", текст)
    return " ".join(текст.split())


def _точка(стойност: Any) -> tuple[float, float] | None:
    if isinstance(стойност, (list, tuple)) and len(стойност) >= 2:
        try:
            return float(стойност[0]), float(стойност[1])
        except (TypeError, ValueError):
            return None
    return None


def _d(точки: list[tuple[float, float]], *, затворен: bool = False) -> str:
    """SVG път по върхове, с ОБЪРНАТ Y."""

    части = []
    for индекс, (x, y) in enumerate(точки):
        части.append(f"{'M' if индекс == 0 else 'L'}{x:.3f},{-y:.3f}")
    if затворен and len(точки) > 2:
        части.append("Z")
    return "".join(части)


def _bbox(точки: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    xs = [x for x, _y in точки]
    ys = [-y for _x, y in точки]
    return (min(xs), min(ys), max(xs), max(ys))


def _дъга(
    център: tuple[float, float],
    радиус: float,
    начален: float,
    краен: float,
) -> list[tuple[float, float]]:
    обхват = (краен - начален) % 360.0
    if обхват <= 0:
        обхват = 360.0
    стъпки = max(2, int(math.ceil(обхват / 90.0 * ОТСЕЧКИ_НА_ЧЕТВЪРТ)))
    точки = []
    for индекс in range(стъпки + 1):
        ъгъл = math.radians(начален + обхват * индекс / стъпки)
        точки.append(
            (център[0] + радиус * math.cos(ъгъл), център[1] + радиус * math.sin(ъгъл))
        )
    return точки


def _елипса(geometry: Mapping[str, Any]) -> list[tuple[float, float]]:
    център = _точка(geometry.get("center"))
    голяма = _точка(geometry.get("major_axis"))
    if център is None or голяма is None:
        return []
    try:
        отношение = float(geometry.get("ratio") or 1.0)
        от = float(geometry.get("start_param") or 0.0)
        до = float(geometry.get("end_param") or (2 * math.pi))
    except (TypeError, ValueError):
        return []
    a = math.hypot(*голяма)
    b = a * отношение
    завъртане = math.atan2(голяма[1], голяма[0])
    обхват = (до - от) % (2 * math.pi) or (2 * math.pi)
    стъпки = max(8, int(math.ceil(обхват / (math.pi / 2) * ОТСЕЧКИ_НА_ЧЕТВЪРТ)))
    точки = []
    for индекс in range(стъпки + 1):
        t = от + обхват * индекс / стъпки
        x = a * math.cos(t)
        y = b * math.sin(t)
        точки.append(
            (
                център[0] + x * math.cos(завъртане) - y * math.sin(завъртане),
                център[1] + x * math.sin(завъртане) + y * math.cos(завъртане),
            )
        )
    return точки


def _кръстче(в: tuple[float, float], размер: float) -> list[list[tuple[float, float]]]:
    """Вмъкването се рисува като кръстче — както в самата програма."""

    x, y = в
    return [
        [(x - размер, y), (x + размер, y)],
        [(x, y - размер), (x, y + размер)],
    ]


def нарисувай(record: Any, *, мащаб: float = 1.0) -> list[Нарисуван]:
    """Един запис → нула или повече готови за рисуване пътища."""

    вид = str(getattr(record, "entity_type", "") or "").upper()
    geometry = getattr(record, "geometry", None) or {}
    handle = str(getattr(record, "handle", "") or "")
    слой = str(getattr(record, "layer", "") or "")
    текст = getattr(record, "text", None)

    def готов(точки, *, затворен=False, is_text=False) -> list[Нарисуван]:
        if len(точки) < 2:
            return []
        return [
            Нарисуван(
                handle=handle,
                entity_type=вид,
                layer=слой,
                path=_d(точки, затворен=затворен),
                bbox=_bbox(точки),
                is_text=is_text,
                text=str(текст or ""),
            )
        ]

    if вид == "LINE":
        a, b = _точка(geometry.get("start")), _точка(geometry.get("end"))
        return готов([a, b]) if a and b else []

    if вид in {"LWPOLYLINE", "POLYLINE"}:
        сурови = geometry.get("points") or geometry.get("vertices") or []
        точки = [т for т in (_точка(с) for с in сурови) if т is not None]
        return готов(точки, затворен=bool(geometry.get("closed")))

    if вид == "ARC":
        център = _точка(geometry.get("center"))
        try:
            радиус = float(geometry.get("radius") or 0.0)
            начален = float(geometry.get("start_angle") or 0.0)
            краен = float(geometry.get("end_angle") or 0.0)
        except (TypeError, ValueError):
            return []
        if център is None or радиус <= МИН_РАЗМЕР:
            return []
        return готов(_дъга(център, радиус, начален, краен))

    if вид == "CIRCLE":
        център = _точка(geometry.get("center"))
        try:
            радиус = float(geometry.get("radius") or 0.0)
        except (TypeError, ValueError):
            return []
        if център is None or радиус <= МИН_РАЗМЕР:
            return []
        return готов(_дъга(център, радиус, 0.0, 360.0), затворен=True)

    if вид == "ELLIPSE":
        return готов(_елипса(geometry))

    if вид in {"TEXT", "MTEXT", "BLOCK_ATTR_TEXT"}:
        в = _точка(geometry.get("insert") or geometry.get("position"))
        if в is None:
            return []
        try:
            височина = float(geometry.get("char_height") or geometry.get("height") or 0.0)
        except (TypeError, ValueError):
            височина = 0.0
        if височина <= 0:
            височина = float(geometry.get("estimated_text_height") or 0.0) or мащаб * 2.0
        try:
            завъртане = float(geometry.get("rotation") or 0.0)
        except (TypeError, ValueError):
            завъртане = 0.0
        чист = изчисти_надпис(текст)
        ширина = max(len(чист), 1) * височина * 0.6
        # Надписът се рисува КАТО НАДПИС — числата по желязото трябва да се
        # четат, както са на чертежа. Кръстче вместо текст крие точно това,
        # което операторът сверява.
        return [
            Нарисуван(
                handle=handle, entity_type=вид, layer=слой, path="",
                bbox=(в[0], -в[1] - височина, в[0] + ширина, -в[1] + височина),
                is_text=True, text=чист,
                insert=(в[0], -в[1]), size=височина, rotation=завъртане,
            )
        ]

    if вид == "INSERT":
        в = _точка(geometry.get("insert") or geometry.get("position"))
        if в is None:
            return []
        рамена = _кръстче(в, мащаб * 0.8)
        път = "".join(_d(рамо) for рамо in рамена)
        xs = [x for рамо in рамена for x, _y in рамо]
        ys = [-y for рамо in рамена for _x, y in рамо]
        return [
            Нарисуван(
                handle=handle, entity_type=вид, layer=слой, path=път,
                bbox=(min(xs), min(ys), max(xs), max(ys)),
            )
        ]

    return []


def всички(records: Iterable[Any], *, мащаб: float = 1.0) -> list[Нарисуван]:
    резултат: list[Нарисуван] = []
    for record in records:
        резултат.extend(нарисувай(record, мащаб=мащаб))
    return резултат


def обхват(нарисувани: Iterable[Нарисуван]) -> tuple[float, float, float, float] | None:
    кутии = [н.bbox for н in нарисувани]
    if not кутии:
        return None
    return (
        min(к[0] for к in кутии),
        min(к[1] for к in кутии),
        max(к[2] for к in кутии),
        max(к[3] for к in кутии),
    )
