# -*- coding: utf-8 -*-
"""Ведомост, НАЧЕРТАНА в самия чертеж като таблица от надписи.

ЗАЩО СЪЩЕСТВУВА
---------------
Собственик, 06.10.2026: „Имаме разпозната форма, имаме надпис, имаме dimension
(разпределение на права + огъната част). Друго не ни трябва.“

Измерено на ``S-_H01-1001_Rev.0_2020-09-23_Bottom_Reinforcement`` — чертеж от
бюро, с което не е работено: в целия чертеж няма **нито един** ``DIMENSION``
обект, затова размерният доказател мълчи. Но слой ``s-kota-sec`` носи
ведомостта на проектанта, начертана като таблица от 914 надписа:

    Mark | Pcs | Ø | Single length [mm] | Dimensioned bending shape | Total length [m] | Mass [kg]
       1 |  16 | 12 |              4.50 | …                         |            72.00 |      63.94
      12 | 159 |  8 |              1.15 | …                         |           182.85 |      72.23

Това е ТРЕТИЯТ диалект ведомост след вградения Excel (``ole_schedule``) и ASD
(``asd_schedule``). Тук таблицата е само текст и линии.

КАКВО ПРАВИ И КАКВО НЕ ПРАВИ
----------------------------
Модулът ЧЕТЕ и нищо повече: без запис на диск, без глобално състояние, без
``eval``. Връща редовете такива, каквито пишат в чертежа, заедно с причините за
всеки отказан ред. Нищо не се прилага към разпознаването — това е отделно
решение.

**Колоните се намират от заглавния ред, не по предположение за подредба.**
Таблицата тук е в ДВЕ полутаблици една до друга и заглавията се повтарят;
всяка полутаблица си има свои котви по x.

**Отказът е по-добър от догадката:**

* липсва ли дори едно от шестте заглавия — връща се ``None``, а не частичен
  прочит;
* клетка извън обхвата на своята колона не се присвоява на съседната;
* ред без всичките шест стойности е ОТКАЗ с причина, не ред с празни полета;
* и най-важното — всеки ред минава през **собствената контрола на ведомостта**:
  ``брой × единична дължина = обща дължина``. Ред, който не я минава, не се
  приема. Контролата е на проектанта, не наша.

Измерено при първия прочит на този чертеж: 109 реда, 107 минават контролата,
146 от 152 позиции на анализа намират своя ред по Ø и единична дължина.
"""
from __future__ import annotations

import itertools
import math
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

DRAWN_SCHEDULE_SCHEMA = "lion-rebar-drawn-schedule/v1"
READER_VERSION = "1.0"

#: Заглавията на колоните в този диалект. Шестте числови са ЗАДЪЛЖИТЕЛНИ:
#: без което и да е от тях таблицата не се чете изобщо.
ЗАДЪЛЖИТЕЛНИ_КОЛОНИ = ("MARK", "PCS", "DIA", "SINGLE", "TOTAL", "MASS")

#: Как се разпознава всяко заглавие. Сравнява се с НОРМАЛИЗИРАН текст (без
#: регистър и излишни интервали), цяло съвпадение — за да не хване „Mark“ в
#: „Remarks“.
ЗАГЛАВИЯ: dict[str, tuple[str, ...]] = {
    "MARK": ("MARK", "ПОЗ", "ПОЗИЦИЯ"),
    "PCS": ("PCS", "БР", "БР.", "БРОЙ"),
    "DIA": ("Ø", "O", "DIA", "ДИАМЕТЪР"),
    "SINGLE": ("SINGLE", "SINGLE LENGTH", "ЕДИНИЧНА"),
    "SHAPE": (
        "DIMENSIONED BENDING SHAPE",
        "BENDING SHAPE",
        "ФОРМА",
    ),
    "TOTAL": ("TOTAL", "TOTAL LENGTH", "ОБЩА"),
    "MASS": ("MASS", "WEIGHT", "МАСА", "ТЕГЛО"),
}

#: Допуск на собствената контрола на ведомостта. Измерено на този чертеж:
#: при вярно разчетени колони отклонението е под 0,5 %, а стойността тук е с
#: запас за закръглянето, с което проектантът пише двата знака.
КОНТРОЛЕН_ДОПУСК = 0.02

#: Стандартните диаметри. Ред с „диаметър“ извън тях не е ред от ведомост.
СТАНДАРТНИ_ДИАМЕТРИ = (6.0, 8.0, 10.0, 12.0, 14.0, 16.0, 18.0, 20.0, 22.0, 25.0, 28.0, 32.0, 36.0, 40.0)

#: Колоните и редовете се четат от НАЧЕРТАНАТА мрежа на таблицата, не от
#: подравняването на числата. Измерено на този чертеж: числото стои до 17
#: единици вляво от заглавието си, затова котвите се застъпват и не стават за
#: граници — а вертикалните линии дават точните прегради (лявата полутаблица:
#: 145,0 · 203,7 · 262,5 · 321,3 · 405,0 · 743,7 · 845,2 · 946,6).
#:
#: Всяка вертикална преграда е начертана на отрязъци — по един на ред — и
#: оттам идват и границите на редовете.
МИН_ОТРЯЗЪЦИ_ЗА_ПРЕГРАДА = 5

#: Отрязък под тази дължина не е ред от таблица.
МИН_ВИСОЧИНА_НА_РЕД = 5.0

#: Два надписа са на ЕДИН ред, когато са в тази част от височината на буквите.
РЕД_ДЯЛ_ОТ_ВИСОЧИНАТА = 0.6

# --------------------------------------------------------------------------- #
# Размерите по скицата: ДВА независими свидетеля
#
# Собственик, 06.10.2026: „ако пробваш да събереш и съпоставиш освен по
# отстояние от надписа при гъсто населени и измерването, така че да
# минимализираме възможностите за грешка“.
#
# Само отстоянието не стига — при гъсто подредени скици в съседната клетка
# падат чужди числа. Затова се иска и ВТОРИ свидетел: сборът на избраните
# размери да затваря надписаната единична дължина.
#
# Измерено на 106-те записа в този чертеж: при 63 от 66-те еднозначни набора
# разликата „единична − сбор“ е **точно 0,0 cm**. Тоест проектантът изписва
# размери, които СЪБИРАТ дължината, без добавка — и затова проверката може да е
# строга. При допуск ±0,51 cm: **66 записа с ЕДИН затварящ набор**, 1
# нееднозначен и 39 без нито един. Нееднозначното и липсващото са ОТКАЗ, не
# избор по близост.

#: Допуск при затварянето. Същият симетричен допуск като навсякъде другаде.
РАЗМЕРЕН_ДОПУСК_CM = 0.51

#: Най-много участъци в една форма — над това не се търси набор.
МАКС_УЧАСТЪЦИ = 6

#: Над толкова кандидата в клетката търсенето се отказва: гъсталак, в който
#: всеки сбор би бил случаен.
МАКС_КАНДИДАТИ = 14

_ЧИСЛО = re.compile(r"^[-+]?\d+(?:[.,]\d+)?$")


@dataclass(frozen=True)
class ScheduleRow:
    """Един ред от ведомостта, както пише в чертежа."""

    mark: str
    pcs: int
    diameter_mm: float
    single_length_cm: float
    total_length_cm: float
    mass_kg: float
    #: Размерите по скицата, ПОТВЪРДЕНИ от сбора си. Празно, когато няма
    #: еднозначен затварящ набор — виж ``dimensions_status``.
    shape_dimensions: tuple[float, ...] = ()
    #: ``ПОТВЪРДЕНИ`` · ``НЯМА ЗАТВАРЯЩ НАБОР`` · ``НЕЕДНОЗНАЧНО`` ·
    #: ``ТВЪРДЕ МНОГО КАНДИДАТИ``
    dimensions_status: str = "НЯМА ЗАТВАРЯЩ НАБОР"
    #: Числата в клетката, от които е избирано — за проверка от оператора.
    dimension_candidates: tuple[float, ...] = ()
    sample_handles: tuple[str, ...] = ()

    @property
    def check_total_cm(self) -> float:
        return self.pcs * self.single_length_cm

    def to_dict(self) -> dict[str, Any]:
        return {
            "mark": self.mark,
            "pcs": self.pcs,
            "diameter_mm": self.diameter_mm,
            "single_length_cm": self.single_length_cm,
            "total_length_cm": self.total_length_cm,
            "mass_kg": self.mass_kg,
            "shape_dimensions": list(self.shape_dimensions),
            "dimensions_status": self.dimensions_status,
            "dimension_candidates": list(self.dimension_candidates),
            "sample_handles": list(self.sample_handles),
        }


@dataclass(frozen=True)
class ScheduleRefusal:
    """Ред, който НЕ е приет, и защо. Отказът се връща, не се премълчава."""

    reason: str
    detail: str
    values: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"reason": self.reason, "detail": self.detail, "values": dict(self.values)}


@dataclass(frozen=True)
class DrawnSchedule:
    schema: str
    reader_version: str
    layer: str
    rows: tuple[ScheduleRow, ...]
    refusals: tuple[ScheduleRefusal, ...]
    #: Котвите на колоните по x, по полутаблици — за проверка и за отчет.
    columns: tuple[dict[str, float], ...] = ()

    @property
    def total_mass_kg(self) -> float:
        return sum(row.mass_kg for row in self.rows)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "reader_version": self.reader_version,
            "layer": self.layer,
            "row_count": len(self.rows),
            "refusal_count": len(self.refusals),
            "total_mass_kg": round(self.total_mass_kg, 2),
            "columns": [dict(c) for c in self.columns],
            "rows": [row.to_dict() for row in self.rows],
            "refusals": [refusal.to_dict() for refusal in self.refusals],
        }


# --------------------------------------------------------------------------- #
# помощни


def _attr(record: Any, name: str) -> Any:
    if isinstance(record, Mapping):
        return record.get(name)
    return getattr(record, name, None)


def _нормализиран(текст: str) -> str:
    return " ".join(str(текст or "").split()).strip().upper()


def _число(текст: str) -> float | None:
    стойност = str(текст or "").strip().replace(",", ".")
    if not _ЧИСЛО.match(стойност.replace(",", ".")):
        return None
    try:
        число = float(стойност)
    except ValueError:
        return None
    return число if math.isfinite(число) else None


def _точка(record: Any) -> tuple[float, float] | None:
    geometry = _attr(record, "geometry") or {}
    стойност = geometry.get("insert") or geometry.get("position")
    if isinstance(стойност, (list, tuple)) and len(стойност) >= 2:
        try:
            return float(стойност[0]), float(стойност[1])
        except (TypeError, ValueError):
            return None
    return None


def _височина(record: Any) -> float:
    geometry = _attr(record, "geometry") or {}
    for ключ in ("char_height", "height", "text_height"):
        try:
            стойност = float(geometry.get(ключ) or 0.0)
        except (TypeError, ValueError):
            continue
        if стойност > 0:
            return стойност
    return 0.0


@dataclass(frozen=True)
class _Надпис:
    x: float
    y: float
    text: str
    height: float
    handle: str


def _надписи(records: Iterable[Any], layer: str | None) -> list[_Надпис]:
    събрани: list[_Надпис] = []
    for record in records:
        текст = str(_attr(record, "text") or "").strip()
        if not текст:
            continue
        if layer is not None and str(_attr(record, "layer") or "") != layer:
            continue
        точка = _точка(record)
        if точка is None:
            continue
        събрани.append(
            _Надпис(
                x=точка[0],
                y=точка[1],
                text=текст,
                height=_височина(record),
                handle=str(_attr(record, "handle") or ""),
            )
        )
    return събрани


def _заглавие_на(текст: str) -> str | None:
    нормализиран = _нормализиран(текст)
    for ключ, думи in ЗАГЛАВИЯ.items():
        if нормализиран in думи:
            return ключ
    return None


def _ленти(надписи: Sequence[_Надпис], допуск: float) -> list[list[_Надпис]]:
    """Надписите, групирани по редове (еднаква височина на реда)."""

    подредени = sorted(надписи, key=lambda н: (-н.y, н.x))
    ленти: list[list[_Надпис]] = []
    текуща: list[_Надпис] = []
    посл: float | None = None
    for надпис in подредени:
        if посл is not None and abs(надпис.y - посл) > допуск:
            ленти.append(текуща)
            текуща = []
        текуща.append(надпис)
        посл = надпис.y
    if текуща:
        ленти.append(текуща)
    return ленти


def _полутаблици(лента: Sequence[_Надпис]) -> list[dict[str, float]]:
    """Котвите по x за всяка полутаблица в заглавния ред.

    Таблицата може да е изписана веднъж или няколко пъти една до друга. Нова
    полутаблица започва там, където заглавието ``MARK`` се появи отново.
    """

    намерени: list[dict[str, float]] = []
    текуща: dict[str, float] = {}
    for надпис in sorted(лента, key=lambda н: н.x):
        ключ = _заглавие_на(надпис.text)
        if ключ is None:
            continue
        if ключ == "MARK" and текуща:
            намерени.append(текуща)
            текуща = {}
        if ключ not in текуща:
            текуща[ключ] = надпис.x
    if текуща:
        намерени.append(текуща)
    return [к for к in намерени if all(име in к for име in ЗАДЪЛЖИТЕЛНИ_КОЛОНИ)]


def _в_колоната(x: float, котви: dict[str, float], име: str, край: float) -> bool:
    начало = котви[име]
    следващи = [с for с in котви.values() if с > начало]
    граница = min(следващи) if следващи else край
    # Клетката е подравнена спрямо котвата си; допуска се малко отместване
    # наляво, защото числата се центрират в колоната.
    return (начало - (граница - начало) * 0.35) <= x < граница



@dataclass(frozen=True)
class _Преграда:
    x: float
    отрязъци: tuple[tuple[float, float], ...]


def _вертикални_прегради(records: Iterable[Any], layer: str) -> list[_Преграда]:
    """Вертикалните линии на таблицата, групирани по x.

    Всяка преграда е начертана на отрязъци — по един на ред — и затова носи и
    границите на редовете.
    """

    по_x: dict[float, list[tuple[float, float]]] = {}
    for record in records:
        if str(_attr(record, "layer") or "") != layer:
            continue
        if str(_attr(record, "entity_type") or "").upper() != "LINE":
            continue
        geometry = _attr(record, "geometry") or {}
        начало, край = geometry.get("start"), geometry.get("end")
        if not (
            isinstance(начало, (list, tuple))
            and isinstance(край, (list, tuple))
            and len(начало) >= 2
            and len(край) >= 2
        ):
            continue
        try:
            x1, y1 = float(начало[0]), float(начало[1])
            x2, y2 = float(край[0]), float(край[1])
        except (TypeError, ValueError):
            continue
        if abs(x1 - x2) > 0.5 or abs(y1 - y2) < МИН_ВИСОЧИНА_НА_РЕД:
            continue
        по_x.setdefault(round(x1, 1), []).append((min(y1, y2), max(y1, y2)))
    прегради = [
        _Преграда(x=x, отрязъци=tuple(sorted(отрязъци, reverse=True)))
        for x, отрязъци in по_x.items()
        if len(отрязъци) >= МИН_ОТРЯЗЪЦИ_ЗА_ПРЕГРАДА
    ]
    прегради.sort(key=lambda п: п.x)
    return прегради


def _колони_по_мрежата(
    котви: dict[str, float],
    прегради: Sequence[_Преграда],
) -> tuple[dict[str, tuple[float, float]], _Преграда] | None:
    """Границите на всяка колона, взети от НАЧЕРТАНИТЕ прегради.

    За всяка котва се взима преградата непосредствено вляво и тази
    непосредствено вдясно. Връща се и преградата, чиито отрязъци дават
    редовете на тази полутаблица.
    """

    имена = [
        име
        for име in sorted(
            set(ЗАДЪЛЖИТЕЛНИ_КОЛОНИ) | {"SHAPE"},
            key=lambda к: котви.get(к, math.inf),
        )
        if име in котви
    ]
    if not имена:
        return None
    ляво, дясно = котви[имена[0]], котви[имена[-1]]
    в_обхвата = [п for п in прегради if ляво - 40.0 <= п.x <= дясно + 160.0]
    if len(в_обхвата) < len(имена) + 1:
        return None
    xs = [п.x for п in в_обхвата]
    граници: dict[str, tuple[float, float]] = {}
    for име in имена:
        котва = котви[име]
        преди = [x for x in xs if x <= котва]
        след = [x for x in xs if x > котва]
        if not преди or not след:
            return None
        граници[име] = (max(преди), min(след))
    # Всяка колона трябва да е отделна: две котви в една клетка значат, че
    # мрежата не отговаря на заглавията — отказ, не догадка.
    if len(set(граници.values())) != len(граници):
        return None
    return граници, в_обхвата[0]



def _zatvarjasht_nabor(
    кандидати: Sequence[float],
    цел_cm: float,
) -> tuple[tuple[float, ...] | None, str]:
    """Единственият набор размери, който ЗАТВАРЯ надписаната дължина.

    Два свидетеля вместо един: клетката казва кои числа са близо, а сборът
    казва кои от тях са размерите на ТАЗИ позиция. Нееднозначното е отказ —
    при два затварящи набора нищо не сочи кой е верният.
    """

    стойности = [с for с in кандидати if с > 0]
    if not стойности:
        return None, "НЯМА ЗАТВАРЯЩ НАБОР"
    if len(стойности) > МАКС_КАНДИДАТИ:
        return None, "ТВЪРДЕ МНОГО КАНДИДАТИ"
    намерени: list[tuple[float, ...]] = []
    for брой in range(1, min(МАКС_УЧАСТЪЦИ, len(стойности)) + 1):
        for набор in itertools.combinations(стойности, брой):
            if abs(sum(набор) - цел_cm) <= РАЗМЕРЕН_ДОПУСК_CM:
                намерени.append(набор)
                if len(намерени) > 1:
                    return None, "НЕЕДНОЗНАЧНО"
    if not намерени:
        return None, "НЯМА ЗАТВАРЯЩ НАБОР"
    return намерени[0], "ПОТВЪРДЕНИ"



def read_drawn_schedule(
    records: Iterable[Any],
    *,
    layer: str | None = None,
) -> DrawnSchedule | None:
    """Ведомостта, начертана в чертежа, или ``None``, ако такава няма.

    ``layer`` ограничава четенето до един слой. Без него се търси в целия
    чертеж — заглавният ред и начертаната мрежа решават, не името на слоя.
    """

    записи = list(records)
    всички = _надписи(записи, layer)
    if not всички:
        return None

    височини = [н.height for н in всички if н.height > 0]
    средна = (sum(височини) / len(височини)) if височини else 1.0
    допуск = средна * РЕД_ДЯЛ_ОТ_ВИСОЧИНАТА

    заглавен: list[_Надпис] | None = None
    котви: list[dict[str, float]] = []
    for лента in _ленти(всички, допуск):
        полутаблици = _полутаблици(лента)
        if полутаблици:
            заглавен = list(лента)
            котви = полутаблици
            break
    if заглавен is None or not котви:
        return None

    по_handle = {str(_attr(з, "handle") or ""): з for з in записи}
    слой = str(_attr(по_handle.get(заглавен[0].handle), "layer") or layer or "")
    прегради = _вертикални_прегради(записи, слой)
    if not прегради:
        return None

    долна_граница = min(н.y for н in заглавен)
    редове: list[ScheduleRow] = []
    откази: list[ScheduleRefusal] = []
    колони_за_отчет: list[dict[str, float]] = []

    for полутаблица in котви:
        намерено = _колони_по_мрежата(полутаблица, прегради)
        if намерено is None:
            откази.append(ScheduleRefusal(
                "МРЕЖАТА НЕ ОТГОВАРЯ НА ЗАГЛАВИЯТА",
                "вертикалните прегради не ограждат всяка колона поотделно",
                {"anchors": {к: round(v, 1) for к, v in полутаблица.items()}},
            ))
            continue
        граници, _преграда = намерено
        колони_за_отчет.append({к: round(v[0], 1) for к, v in граници.items()})

        # Редовете се определят от самите числа: шестте стойности на един запис
        # са на ЕДНА височина. Отрязъците на преградите не стават за това —
        # измерено, те са по 120 единици при стъпка на реда ~50 и се застъпват,
        # тоест един „отрязък“ хваща два записа.
        в_полутаблицата = [
            н
            for н in всички
            if н.y < долна_граница
            and any(ляво <= н.x < дясно for ляво, дясно in граници.values())
        ]
        редове_на_полутаблицата: list[tuple[float, list[_Надпис]]] = [
            (лента[0].y, list(лента))
            for лента in _ленти(в_полутаблицата, допуск)
        ]
        # Размерите от колоната на формата стоят НАД и ПОД реда на марката —
        # раздават се по най-близкия ред, но само вътре в тази полутаблица.
        ляво_форма, дясно_форма = граници.get("SHAPE", (math.inf, -math.inf))
        ys = [y for y, _р in редове_на_полутаблицата]
        разстояния = sorted(
            abs(ys[и] - ys[и + 1]) for и in range(len(ys) - 1)
        ) if len(ys) > 1 else []
        стъпка_на_реда = (
            разстояния[len(разстояния) // 2] if разстояния else средна * 4.0
        )
        заготовки: list[tuple[float, ScheduleRow]] = []
        for _y, в_реда in редове_на_полутаблицата:
            if not в_реда:
                continue
            клетки: dict[str, list[_Надпис]] = {}
            for надпис in в_реда:
                for име, (ляво, дясно) in граници.items():
                    if ляво <= надпис.x < дясно:
                        клетки.setdefault(име, []).append(надпис)
                        break
            стойности: dict[str, float] = {}
            handles: list[str] = []
            липсва = ""
            for име in ЗАДЪЛЖИТЕЛНИ_КОЛОНИ:
                числа = [
                    (_число(н.text), н) for н in (клетки.get(име) or [])
                ]
                числа = [(с, н) for с, н in числа if с is not None]
                if len(числа) != 1:
                    липсва = f"{име}: {len(числа)} числа в клетката"
                    break
                стойности[име] = числа[0][0]
                handles.append(числа[0][1].handle)
            if липсва:
                # Лентата без нито едно число в колоната на МАРКАТА не е опит
                # за ред: това са надписите по скицата на формата, които стоят
                # на свои височини. Те не са отказ и не се съобщават.
                извън_формата = [
                    н
                    for н in в_реда
                    if _число(н.text) is not None
                    and not (ляво_форма <= н.x < дясно_форма)
                ]
                if извън_формата:
                    откази.append(ScheduleRefusal(
                        "НЕПЪЛЕН РЕД", липсва,
                        {"y": round(_y, 1), "texts": [н.text for н in в_реда][:8]},
                    ))
                continue

            брой = int(стойности["PCS"])
            диаметър = float(стойности["DIA"])
            единична_cm = float(стойности["SINGLE"]) * 100.0
            обща_cm = float(стойности["TOTAL"]) * 100.0
            маса = float(стойности["MASS"])
            марка = "%g" % стойности["MARK"]
            общи = {
                "mark": марка, "pcs": брой, "diameter_mm": диаметър,
                "single_length_cm": round(единична_cm, 2),
                "total_length_cm": round(обща_cm, 2),
            }
            if брой <= 0 or единична_cm <= 0 or обща_cm <= 0 or маса <= 0:
                откази.append(ScheduleRefusal(
                    "НЕПОЛОЖИТЕЛНА СТОЙНОСТ", "ред с нула или отрицателно число", общи))
                continue
            if диаметър not in СТАНДАРТНИ_ДИАМЕТРИ:
                откази.append(ScheduleRefusal(
                    "НЕСТАНДАРТЕН ДИАМЕТЪР",
                    f"Ø{диаметър:g} не е от стандартния набор", общи))
                continue
            очаквана = брой * единична_cm
            if abs(очаквана - обща_cm) > КОНТРОЛЕН_ДОПУСК * max(обща_cm, 1.0):
                откази.append(ScheduleRefusal(
                    "КОНТРОЛАТА НА ВЕДОМОСТТА НЕ ИЗЛИЗА",
                    f"брой × единична = {очаквана:.2f} cm, "
                    f"а обявената обща е {обща_cm:.2f} cm",
                    общи,
                ))
                continue
            заготовки.append((
                числа[0][1].y if числа else _y,
                ScheduleRow(
                    mark=марка,
                    pcs=брой,
                    diameter_mm=диаметър,
                    single_length_cm=round(единична_cm, 2),
                    total_length_cm=round(обща_cm, 2),
                    mass_kg=маса,
                    shape_dimensions=(),
                    sample_handles=tuple(handles),
                ),
            ))

        # Размерите по скицата: клетката на записа дава КАНДИДАТИТЕ, а сборът
        # решава кои от тях са размерите. Виж обяснението при константите.
        заготовки.sort(key=lambda двойка: -двойка[0])
        центрове = [ц for ц, _р in заготовки]
        стъпки = sorted(
            abs(центрове[и] - центрове[и + 1]) for и in range(len(центрове) - 1)
        )
        стъпка = стъпки[len(стъпки) // 2] if стъпки else средна * 4.0
        for индекс, (център, ред) in enumerate(заготовки):
            горе = (центрове[индекс - 1] + център) / 2 if индекс > 0 else център + стъпка
            долу = (
                (центрове[индекс + 1] + център) / 2
                if индекс + 1 < len(центрове)
                else център - стъпка
            )
            кандидати = sorted(
                {
                    round(float(_число(надпис.text)), 2)
                    for надпис in в_полутаблицата
                    if ляво_форма <= надпис.x < дясно_форма
                    and долу <= надпис.y < горе
                    and _число(надпис.text) is not None
                }
            )
            набор, състояние = _zatvarjasht_nabor(кандидати, ред.single_length_cm)
            редове.append(
                ScheduleRow(
                    mark=ред.mark,
                    pcs=ред.pcs,
                    diameter_mm=ред.diameter_mm,
                    single_length_cm=ред.single_length_cm,
                    total_length_cm=ред.total_length_cm,
                    mass_kg=ред.mass_kg,
                    shape_dimensions=tuple(набор or ()),
                    dimensions_status=състояние,
                    dimension_candidates=tuple(кандидати),
                    sample_handles=ред.sample_handles,
                )
            )

    return DrawnSchedule(
        schema=DRAWN_SCHEDULE_SCHEMA,
        reader_version=READER_VERSION,
        layer=слой,
        rows=tuple(редове),
        refusals=tuple(откази),
        columns=tuple(колони_за_отчет),
    )


__all__ = [
    "DRAWN_SCHEDULE_SCHEMA",
    "DrawnSchedule",
    "ScheduleRefusal",
    "ScheduleRow",
    "read_drawn_schedule",
]
