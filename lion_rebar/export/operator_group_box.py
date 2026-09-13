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

from dataclasses import dataclass
from typing import Sequence


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
