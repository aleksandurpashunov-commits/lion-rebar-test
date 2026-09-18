"""Операторска рамка с два клика: „всичко вътре е на тази греда".

Собственик, 2026-09-13: „защо не добавихме функция която да прави рамка с два
клика, правоъгълна или квадратна. Само начало-край?"

По-добро е от списък с падащо меню по три причини: едно действие покрива цяла
група позиции, съвпада с начина, по който инженерът чете чертежа, и работи
еднакво за греди, колони и шайби.

КОГА СЕ ЧЕРТАЕ
--------------
ПРЕДИ анализа, върху вече отворения DXF (собственик, 2026-09-13: „да
направим рамките преди теста на чертежа, за да може програмата да го
прочете"). Затова рамката се пази като ОБЛАСТ, а не като решения за
отделни позиции -- в този момент позиции още няма.

Това е ръчният близнак на автоматичните рамки при колоните: там програмата
сама намира затворения контур и заглавието до него, тук операторът ги
посочва. Надолу по веригата двете се ползват еднакво -- всичко вътре в
рамката принадлежи на елемента, чието заглавие е в нея.

ПРАВИЛА
-------
Заглавието на гредата трябва да е ВЪТРЕ в рамката -- това прави действието
наистина двукликово и премахва гаданието кому принадлежи групата. Нула
заглавия вътре или повече от едно е отказ, не догадка: операторът да
пренарисува рамката по-точно.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Sequence


class OperatorBoxError(ValueError):
    """Рамката не описва еднозначна група — отказва се, не се гадае."""


@dataclass(frozen=True)
class BoxResolution:
    """Какво покрива една операторска рамка."""

    beam_label_handle: str
    beam_title: str
    annotation_handles: tuple[str, ...]


def normalise_box(
    start: tuple[float, float], end: tuple[float, float]
) -> tuple[float, float, float, float]:
    """Двата клика в подреден правоъгълник ``(x0, y0, x1, y1)``.

    Посоката на влаченето няма значение -- операторът може да тръгне от кой
    да е ъгъл.
    """

    x0, x1 = sorted((float(start[0]), float(end[0])))
    y0, y1 = sorted((float(start[1]), float(end[1])))
    return (x0, y0, x1, y1)


def _inside(box: tuple[float, float, float, float], x: float, y: float) -> bool:
    x0, y0, x1, y1 = box
    return x0 <= float(x) <= x1 and y0 <= float(y) <= y1


def resolve_operator_box(
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    beam_titles: Sequence[tuple[str, str, float, float]],
    unresolved: Sequence[tuple[str, float, float]],
) -> BoxResolution:
    """Разчита една операторска рамка.

    ``beam_titles`` са ``(handle, заглавие, x, y)`` за ВСИЧКИ греди в чертежа;
    ``unresolved`` са ``(handle, x, y)`` само за позициите, чиято греда
    автоматиката не е доказала -- рамка никога не пипа вече доказана връзка.
    """

    box = normalise_box(start, end)
    if box[0] == box[2] or box[1] == box[3]:
        raise OperatorBoxError(
            "Рамката е с нулева широчина или височина — начертай я наново."
        )

    titles = [
        (handle, title)
        for handle, title, x, y in beam_titles
        if _inside(box, x, y)
    ]
    if not titles:
        raise OperatorBoxError(
            "В рамката няма заглавие на греда. Обхвани и заглавието, за да е "
            "ясно на коя греда принадлежат позициите."
        )
    if len(titles) > 1:
        names = ", ".join(sorted(title for _handle, title in titles))
        raise OperatorBoxError(
            f"В рамката попадат {len(titles)} заглавия на греди ({names}). "
            "Начертай я около една греда."
        )

    handles = tuple(
        handle for handle, x, y in unresolved if _inside(box, x, y)
    )
    if not handles:
        raise OperatorBoxError(
            "В рамката няма позиция без доказана греда. Вече свързаните "
            "позиции не се променят по този път."
        )

    label_handle, title = titles[0]
    return BoxResolution(
        beam_label_handle=label_handle,
        beam_title=title,
        annotation_handles=handles,
    )


# ---------------------------------------------------------------------------
# Област, начертана ПРЕДИ анализа
# ---------------------------------------------------------------------------
#
# Разликата от ``resolve_operator_box`` по-горе е в момента. Там рамката се
# чертае СЛЕД като Финалният контрол е блокирал позиции и веднага се
# превръща в решения за тях. Тук операторът чертае, преди изобщо да има
# позиции: разчита се само заглавието, а какво е попаднало вътре се решава
# наново при всеки анализ (виж ``export/operator_areas.py``).

#: Заглавие на шайба върху реалния чертеж: „Ш 3-25/600", „Ш 11 - 145/25".
#: Същият образец като ``_SHAIBI_WALL_LABEL_RE`` в ``dxf/proxy_adapter.py``.
#: Проверено върху ``s-7``: хваща 23 текста, всички на слой ``C_TXT_shaibi``.
#: Разхлабване до „Ш<число>" би хванало още 49 — но те са котировки на слоеве
#: ``_text``/``KOTA-TXT`` („Ш10", „Ш3"), не заглавия, затова образецът иска
#: цялото сечение.
_SHAIBI_TITLE_RE = re.compile(r"Ш\s*\d+\s*-?\s*\d+\s*/\s*\d+")

#: Заглавие на колонна група: „К23, К24 (35/35) - 2бр.".
#: Същият образец като ``_COLUMN_GROUP_HEADER_RE`` в ``dxf/proxy_adapter.py``.
_COLUMN_GROUP_TITLE_RE = re.compile(r"(\d+)\s*бр\.?\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class AreaTitle:
    """Кандидат за заглавие на област."""

    handle: str
    text: str
    x: float
    y: float
    frame_index: int
    kind: str


@dataclass(frozen=True)
class AreaResolution:
    """Какво обявява една област — заглавието ѝ, не позициите в нея."""

    box: tuple[float, float, float, float]
    title_handle: str
    title_text: str
    kind: str
    frame_index: int


def collect_area_titles(
    entities: Sequence[object],
    frames: Sequence[object],
) -> list[AreaTitle]:
    """Всички текстове, които могат да бъдат заглавие на област.

    Три вида, всеки със собствения си доказан образец:

    ``BEAM``   „Гр. B.19 25/55" в рамка ``BEAMS`` — взима се направо от
               ``_detect_beam_labels``, за да няма два различни образеца за
               едно и също нещо.
    ``GROUP``  заглавие на шайба или на колонна група — това е, по което
               описът се подрежда блок по блок.

    Текст, който отговаря на повече от един образец, се брои веднъж, като
    гредите имат предимство: те са единственият вид, който носи и
    производствена връзка, не само подредба в листа.
    """

    from lion_rebar.export.excel_export import _detect_beam_labels, _entity_insert
    from lion_rebar.export.frame_detection import assign_point_to_frame

    titles: list[AreaTitle] = []
    seen: set[str] = set()

    for label in _detect_beam_labels(list(entities), list(frames)):
        titles.append(
            AreaTitle(
                handle=str(label.handle),
                text=str(label.title),
                x=float(label.x),
                y=float(label.y),
                frame_index=int(label.frame_index),
                kind="BEAM",
            )
        )
        seen.add(str(label.handle))

    frame_list = list(frames)

    # ЗАГЛАВИЯТА НА ВЕРТИКАЛНИТЕ СЕКЦИИ.
    #
    # „Стб.Ядро B.1 - (Сх.арм.2 )", „K.B.11,11a - 88x25 - 2 бр.(Сх.арм.1 )".
    # Взимат се от ``_detect_vertical_section_labels`` — същия източник, по
    # който описът се подрежда секция по секция, — а не по нов образец: така
    # handle-ът, който областта запазва, е точно този, по който
    # ``_assign_vertical_section`` после я разпознава.
    #
    # Без тях операторът НЕ МОЖЕ да огради вертикален елемент: в ``s-10``
    # ``collect_area_titles`` връщаше нула заглавия, тоест всяка рамка се
    # отказваше с „в рамката няма заглавие".
    from lion_rebar.export.excel_export import _detect_vertical_section_labels

    by_handle = {
        str(getattr(record, "handle", "") or ""): record for record in entities
    }
    for section in _detect_vertical_section_labels(list(entities), frame_list):
        handle = str(section.title_handle or "")
        if not handle or handle in seen:
            continue
        record = by_handle.get(handle)
        point = _entity_insert(record) if record is not None else None
        if point is None:
            continue
        titles.append(
            AreaTitle(
                handle=handle,
                text=str(section.title),
                x=float(point[0]),
                y=float(point[1]),
                frame_index=int(section.frame_index),
                kind="GROUP",
            )
        )
        seen.add(handle)

    for record in entities:
        handle = str(getattr(record, "handle", "") or "")
        if not handle or handle in seen:
            continue
        text = re.sub(r"\s+", " ", str(getattr(record, "text", "") or "")).strip()
        if not text:
            continue
        if not (
            _SHAIBI_TITLE_RE.search(text) or _COLUMN_GROUP_TITLE_RE.search(text)
        ):
            continue
        point = _entity_insert(record)
        if point is None:
            continue
        frame_index = assign_point_to_frame(point[0], point[1], frame_list)
        if frame_index is None:
            continue
        titles.append(
            AreaTitle(
                handle=handle,
                text=text,
                x=float(point[0]),
                y=float(point[1]),
                frame_index=int(frame_index),
                kind="GROUP",
            )
        )
        seen.add(handle)

    return titles


def resolve_operator_area(
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    titles: Sequence[AreaTitle],
    claimed_title_handles: Iterable[str] = (),
) -> AreaResolution:
    """Разчита рамка, начертана преди анализа.

    Иска ТОЧНО ЕДНО СВОБОДНО заглавие вътре — нула или две е отказ, не
    догадка. Позиции не се търсят: в този момент още няма такива, а и целият
    смисъл на областта е анализът да ги намери сам, всеки път наново.

    ``claimed_title_handles`` са заглавията, които вече имат своя рамка. Те
    не се броят за конкуренция, защото новата рамка ще се огъне по тяхната
    (``export/operator_areas.bend_around_existing``). Без това изключение
    огъването няма как да се случи: щедро начертаната рамка захваща и
    съседното заглавие и отказва още тук, преди огъването да е стигнало до
    думата. Собственик, 2026-09-17: рамките „да се огъват“ по съседните.
    """

    box = normalise_box(start, end)
    if box[0] == box[2] or box[1] == box[3]:
        raise OperatorBoxError(
            "Рамката е с нулева широчина или височина — начертай я наново."
        )

    inside = [title for title in titles if _inside(box, title.x, title.y)]
    if not inside:
        raise OperatorBoxError(
            "В рамката няма заглавие. Обхвани и заглавието на гредата "
            "(„Гр. B.19 25/55“), на шайбата („Ш 3-25/600“) или на групата "
            "колони, за да е ясно на кого принадлежат позициите вътре."
        )
    claimed = {str(handle) for handle in claimed_title_handles if str(handle)}
    free = [title for title in inside if str(title.handle) not in claimed]
    if not free:
        names = ", ".join(sorted(f"„{title.text}“" for title in inside))
        raise OperatorBoxError(
            f"Заглавието в рамката вече има своя рамка ({names}). "
            "Оттегли я от „Анализ → Начертани рамки…“, ако трябва да се "
            "промени, или обхвани друго заглавие."
        )
    if len(free) > 1:
        names = ", ".join(sorted(f"„{title.text}“" for title in free))
        raise OperatorBoxError(
            f"В рамката попадат {len(free)} заглавия без рамка ({names}). "
            "Начертай я около едно."
        )

    title = free[0]
    return AreaResolution(
        box=box,
        title_handle=title.handle,
        title_text=title.text,
        kind=title.kind,
        frame_index=title.frame_index,
    )
