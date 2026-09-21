from __future__ import annotations

from dataclasses import dataclass, replace
import math
import re
from typing import Iterable, Sequence

from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.dxf.text_normalization import decode_dxf_text, normalize_for_match
from lion_rebar.dxf.text_parser import looks_like_rebar_text


# Ключови думи за заглавие на рамка (използват се и в основния прозорец,
# и във fallback-а, за да няма два различни речника).
TITLE_TOKENS = ("АРМИРОВ", "ДЕТАЙЛ", "ГРЕД", "ДОЛНА", "ГОРНА", "ВЕРТИКАЛ")

# ``ЧЕРТЕЖ`` inside a note is not a title-block marker.  Real title blocks
# use the dedicated label ``ЧЕРТЕЖ:``.  The previous substring search also
# matched prose such as ``от чертежите`` and invented an UNKNOWN frame.
FRAME_MARKER_RE = re.compile(r"^ЧЕРТЕЖ\s*:?$")

#: Целият щемпел в един обект: „ЧЕРТЕЖ: <заглавие>“. Мери се върху декодирания
#: текст, за да остане главно/малко както го е написал проектантът.
FRAME_MARKER_WITH_TITLE_RE = re.compile(r"^ЧЕРТЕЖ\s*:\s*(.+)$", re.IGNORECASE)

#: С колко пъти групата заглавия трябва да води по разстлан, за да бъде приета
#: за заглавия на ЛИСТОВЕ (виж ``_detect_title_only_frames``). Измерено
#: 2026-09-20 в „тест-01“: 6562 срещу 56 единици, тоест 117 пъти. Прагът е
#: отношение, за да не зависи от мащаба на чертежа.
TITLE_SPREAD_MARGIN = 4.0

# Some designers deliver a single logical sheet without the ``ЧЕРТЕЖ:``
# title-block field. Such a file may be accepted automatically only when one
# (and exactly one) short, explicit category title is present. These anchored
# patterns deliberately exclude notes and generic mentions of reinforcement.
SINGLE_SHEET_TITLE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("BOTTOM", re.compile(r"^(?:ПЛАН\s+)?ДОЛНА\s+АРМИРОВ")),
    ("TOP", re.compile(r"^(?:ПЛАН\s+)?ГОРНА\s+АРМИРОВ")),
    ("DETAILS", re.compile(r"^ДЕТАЙЛ(?:И)?\s+АРМИРОВ")),
    (
        "BEAMS",
        re.compile(
            r"^(?:АРМИРОВ(?:КА)?\s+(?:НА\s+)?ГРЕДИ\b|"
            r"ГРЕДИ\s*[-—:]?\s*АРМИРОВ)"
        ),
    ),
    (
        "VERTICALS",
        re.compile(
            r"^(?:ВЕРТ(?:ИКАЛ(?:НА|НИ)?)?\s*\.?\s*АРМИРОВ|"
            r"АРМИРОВ(?:КА)?\s+ВЕРТИКАЛ)"
        ),
    ),
    # Owner-confirmed 2026-09-12: the second designer's koloni/shaibi
    # sheets title themselves "АРМИРОВКА КОЛОНИ ОТ КОТА +18,18 ДО КОТА
    # +21,48" / "АРМИРОВКА ШАЙБИ ОТ КОТА ..." on layer "$Ramka", with a
    # matching single title-block INSERT on layer "ramka_1" -- real,
    # unambiguous single-sheet evidence, just not the BEAMS/VERTICALS
    # wording this vocabulary already knew. Deliberately separate from
    # VERTICALS: that category also demands vertical-schedule title+
    # divider evidence downstream (_validate_vertical_assignments) that
    # this simpler dialect does not have and does not need.
    ("COLUMNS", re.compile(r"^АРМИРОВ(?:КА)?\s+КОЛОНИ\b")),
    ("WALLS", re.compile(r"^АРМИРОВ(?:КА)?\s+ШАЙБИ\b")),
)

# Fallback радиус: колко височини на текста около маркера се търси заглавие,
# когато доказаният прозорец не е дал резултат.
FALLBACK_RADIUS_HEIGHTS = 12.0
FALLBACK_RADIUS_MIN = 200.0

# Във fallback-а речникът е ПО-СТРОГ от основния прозорец.
# Доказателство (Belgravia): общата бележка "плочи, греди, колони и шайби !"
# съдържа "ГРЕД" и се хващаше като заглавие на рамка. Реалните заглавия при
# този проектант винаги съдържат "АРМИРОВ" или "ДЕТАЙЛ":
#   "ДОЛНА АРМИРОВКА НА КОТА +11.90", "ДЕТАЙЛИ АРМИРОВКА ...",
#   "АРМИРОВКА ГРЕДИ НА КОТА +11,90".
# Височината на текста НЕ е надежден дискриминатор (бележката е 27.3,
# колкото е и височината на маркер), затова разделяме по речник.
FALLBACK_TITLE_TOKENS = ("АРМИРОВ", "ДЕТАЙЛ")

# Ако два кандидата на близко разстояние дават РАЗЛИЧНА категория,
# рамката се обявява за двусмислена и отива при оператора.
FALLBACK_AMBIGUITY_RATIO = 1.25

CATEGORY_ORDER = {
    "BOTTOM": 10,
    "BOTTOM_EXTRA": 20,
    "TOP": 30,
    "TOP_EXTRA": 40,
    "DETAILS": 50,
    "BEAMS": 60,
    # Одобрено от Product Owner (2026-08-24): вертикалната армировка получава
    # собствен лист като всички останали категории. Реален случай:
    # "АРМИРОВКА ВЕРТИКАЛИ ДО КОТА +8,90".
    "VERTICALS": 70,
    "UNKNOWN": 999,
}

CATEGORY_SHEET_NAMES = {
    "BOTTOM": "долна",
    "BOTTOM_EXTRA": "долна усилване",
    "TOP": "горна",
    "TOP_EXTRA": "горна усилване",
    "DETAILS": "детайли",
    "BEAMS": "греди",
    "VERTICALS": "вертикали",
    "COLUMNS": "колони",
    "WALLS": "шайби",
    "UNKNOWN": "неопределен",
}


@dataclass(frozen=True)
class DrawingFrame:
    index: int
    marker_handle: str
    anchor_x: float
    anchor_y: float
    title_text: str
    supporting_text: str
    category_code: str
    suggested_sheet_name: str
    confidence: str
    resolution_reason: str = "PRIMARY_WINDOW"
    title_handle: str = ""
    title_distance: float = 0.0
    marker_text_height: float = 0.0
    resolved_by: str = "AUTO"
    #: Лентата на чертежа по x, изчислена от самото съдържание.
    #: ``None`` значи, че лентите не са могли да се разделят честно —
    #: тогава се пада обратно на най-близката котва.
    x_min: float | None = None
    x_max: float | None = None
    #: Граници по y. Пълнят се САМО при обявена рамка (начертан правоъгълник
    #: на класифициращ се слой). Един лист може да държи планове един над друг
    #: и тогава лентата по x не ги дели — виж ``_detect_declared_area_frames``.
    y_min: float | None = None
    y_max: float | None = None

    @property
    def contains_x(self) -> bool:
        return self.x_min is not None and self.x_max is not None

    @property
    def is_declared_area(self) -> bool:
        return self.contains_x and self.y_min is not None and self.y_max is not None

    def contains_point(self, x: float, y: float) -> bool:
        return (
            self.is_declared_area
            and float(self.x_min) <= x <= float(self.x_max)
            and float(self.y_min) <= y <= float(self.y_max)
        )

    @property
    def is_unknown(self) -> bool:
        return self.category_code == "UNKNOWN"

    @property
    def requires_operator_decision(self) -> bool:
        """UNKNOWN рамка НЕ се познава автоматично — иска операторско решение."""

        return self.is_unknown and self.resolved_by == "AUTO"

    @property
    def combined_title(self) -> str:
        return " ".join(value for value in (self.title_text, self.supporting_text) if value).strip()


def detect_drawing_frames(entities: Iterable[DxfEntityRecord]) -> list[DrawingFrame]:
    records = list(entities)
    text_records = [
        record
        for record in records
        if record.entity_type.upper() in {"TEXT", "MTEXT", "BLOCK_ATTR_TEXT"}
        and str(record.text or "").strip()
    ]
    # Щемпелът идва в два законни вида: „ЧЕРТЕЖ:“ като свой обект и заглавието
    # до него (основният проектант), или цялото „ЧЕРТЕЖ: <заглавие>“ в ЕДИН
    # обект. Второто е измерено на 2026-09-20 в „KOFR_B“: 43 MTEXT заглавия на
    # слой ``ramki``, заради които рамките бяха нула и портата отказваше всички
    # 2974 позиции. Прототипът показа нула промяна в осемте познати чертежа —
    # никой от тях не пише заглавието в един обект.
    markers: list[tuple[DxfEntityRecord, str]] = []
    for record in text_records:
        if _xy(record) is None:
            continue
        if FRAME_MARKER_RE.fullmatch(_normalize(record.text)):
            markers.append((record, ""))
            continue
        decoded = " ".join(decode_dxf_text(record.text).split())
        inline = FRAME_MARKER_WITH_TITLE_RE.match(decoded)
        if inline and inline.group(1).strip():
            markers.append((record, inline.group(1).strip()))
    if not markers:
        # Редът е нарочен: обявената рамка (решение на оператора) бие изведената
        # по заглавие, а изведената по заглавие бие единичния лист, защото
        # няколко заглавия са по-силно доказателство от едно.
        declared = _detect_declared_area_frames(records)
        if declared:
            return declared
        by_title = _detect_title_only_frames(records, text_records)
        if by_title:
            return by_title
        return _detect_single_sheet_frame(records, text_records)
    frames: list[DrawingFrame] = []
    for marker, inline_title in markers:
        mx, my = _xy(marker) or (0.0, 0.0)
        nearby = []
        supporting = []
        for record in text_records:
            if record.handle == marker.handle:
                continue
            point = _xy(record)
            if point is None:
                continue
            x, y = point
            dx = x - mx
            dy = y - my
            if 50.0 <= dx <= 1800.0 and -12.0 <= dy <= 15.0:
                normalized = _normalize(record.text)
                if any(token in normalized for token in ("АРМИРОВ", "ДЕТАЙЛ", "ГРЕД", "ДОЛНА", "ГОРНА")):
                    nearby.append((abs(dy) + dx / 10000.0, record))
            if 50.0 <= dx <= 1800.0 and -85.0 <= dy <= 25.0:
                normalized = _normalize(record.text)
                if "УСИЛВА" in normalized or "ОСНОВНА МРЕЖА" in normalized:
                    supporting.append((abs(dy) + dx / 10000.0, record))
        title_record = min(nearby, key=lambda item: item[0])[1] if nearby else None
        support_record = min(supporting, key=lambda item: item[0])[1] if supporting else None
        reason = "PRIMARY_WINDOW"
        title_distance = 0.0
        if inline_title:
            # Заглавието е в самия щемпел: не се търси съседен надпис и не се
            # пуска резервният радиус — доказателството е в един обект.
            title_record = marker
            reason = "INLINE_TITLE"

        # --- FALLBACK: адаптивен радиус, мащабиран по височината на текста ---
        # Основният прозорец е с ТВЪРДИ абсолютни разстояния (dx 50..1800,
        # dy -12..15), настроени по чертежите на основния проектант. Върху
        # Belgravia коректното заглавие стои на dy = -46 при височина на
        # текста 21 и затова рамката оставаше UNKNOWN.
        # Fallback-ът се задейства САМО когато основният прозорец не е дал
        # заглавие, така че поведението при вече работещите чертежи е
        # непроменено (fail-safe: не може да влоши доказан резултат).
        if title_record is None:
            height = _text_height(marker)
            radius = max(height * FALLBACK_RADIUS_HEIGHTS, FALLBACK_RADIUS_MIN)
            candidates = []
            for record in text_records:
                if record.handle == marker.handle:
                    continue
                point = _xy(record)
                if point is None:
                    continue
                dx = point[0] - mx
                dy = point[1] - my
                distance = math.hypot(dx, dy)
                if distance > radius:
                    continue
                normalized = _normalize(record.text)
                if any(token in normalized for token in FALLBACK_TITLE_TOKENS):
                    candidates.append((distance, record))
            if candidates:
                candidates.sort(key=lambda item: item[0])
                best_distance, best_record = candidates[0]
                best_category = classify_title(decode_dxf_text(best_record.text))[0]
                rival = next(
                    (
                        item
                        for item in candidates[1:]
                        if item[0] <= max(best_distance, 1e-9) * FALLBACK_AMBIGUITY_RATIO
                        and classify_title(decode_dxf_text(item[1].text))[0] != best_category
                    ),
                    None,
                )
                if rival is not None:
                    # Двусмислено доказателство -> без предположение.
                    reason = "FALLBACK_AMBIGUOUS"
                else:
                    title_distance, title_record = best_distance, best_record
                    reason = "FALLBACK_RADIUS"

        title = (
            inline_title
            if inline_title
            else (decode_dxf_text(title_record.text).strip() if title_record else "")
        )
        support = decode_dxf_text(support_record.text).strip() if support_record else ""
        category, confidence = classify_title(title, support)
        if reason == "FALLBACK_RADIUS" and confidence == "HIGH":
            # Заглавието е намерено извън доказания прозорец: категорията е
            # изведена коректно, но доказателството е по-слабо.
            confidence = "MEDIUM"
        if category == "UNKNOWN" and reason != "FALLBACK_AMBIGUOUS":
            reason = "NO_TITLE_FOUND" if title_record is None else "TITLE_NOT_CLASSIFIED"
        frames.append(
            DrawingFrame(
                index=0,
                marker_handle=marker.handle,
                anchor_x=mx,
                anchor_y=my,
                title_text=title,
                supporting_text=support,
                category_code=category,
                suggested_sheet_name=CATEGORY_SHEET_NAMES[category],
                confidence=confidence,
                resolution_reason=reason,
                title_handle=title_record.handle if title_record else "",
                title_distance=round(float(title_distance), 3),
                marker_text_height=round(_text_height(marker), 3),
            )
        )

    frames.sort(key=lambda frame: (-round(frame.anchor_y / 500.0), frame.anchor_x))
    result: list[DrawingFrame] = []
    used_names: dict[str, int] = {}
    for index, frame in enumerate(frames, start=1):
        base = frame.suggested_sheet_name
        used_names[base] = used_names.get(base, 0) + 1
        name = base if used_names[base] == 1 else f"{base} {used_names[base]}"
        result.append(
            DrawingFrame(
                index=index,
                marker_handle=frame.marker_handle,
                anchor_x=frame.anchor_x,
                anchor_y=frame.anchor_y,
                title_text=frame.title_text,
                supporting_text=frame.supporting_text,
                category_code=frame.category_code,
                suggested_sheet_name=name,
                confidence=frame.confidence,
                resolution_reason=frame.resolution_reason,
                title_handle=frame.title_handle,
                title_distance=frame.title_distance,
                marker_text_height=frame.marker_text_height,
                resolved_by=frame.resolved_by,
            )
        )
    bands = _frame_bands(records, result)
    if bands is None:
        return result
    return [
        replace(frame, x_min=bands[frame.index][0], x_max=bands[frame.index][1])
        if frame.index in bands
        else frame
        for frame in result
    ]


def _anchored_sheet_category(record: DxfEntityRecord) -> str:
    """Категория по ЗАКОТВЕН образец за заглавие на лист, или празно.

    Същият прочит като в ``_detect_single_sheet_frame``: образците в
    ``SINGLE_SHEET_TITLE_PATTERNS`` са закотвени в началото и нарочно изключват
    бележки и общи споменавания на армировка.

    ``classify_title`` е по-широк — търси ключовата дума КЪДЕТО И ДА Е в текста —
    и затова не бива да избира кандидати за лист. Измерено: бележката
    „колони, шайби и вътр.сут.стени от фундамент до покрив“ от „Сграда бета“
    дава през него ``WALLS``/``HIGH``, тоест би станала лист „шайби“.
    """

    decoded = decode_dxf_text(record.text)
    normalized = _normalize(decoded)
    if not normalized or len(normalized) > 180 or "\n" in decoded:
        return ""
    return next(
        (
            category_code
            for category_code, pattern in SINGLE_SHEET_TITLE_PATTERNS
            if pattern.search(normalized)
        ),
        "",
    )


def _detect_title_only_frames(
    records: list[DxfEntityRecord],
    text_records: list[DxfEntityRecord],
) -> list[DrawingFrame]:
    """Няколко листа, познати само по заглавията си, без щемпел „ЧЕРТЕЖ:“.

    Собственик, 2026-09-20: „да си направи рамка и да и даде име като разпознае
    името както в тест-01“.

    ЗАЩО. Измерено върху `тест-01` (нов офис): чертежът носи „Долна армировка на
    кота +6,68“ и „Горна армировка на кота +6,68“ на слой ``ANTETKA``, и двете
    се класифицират еднозначно. Но щемпел „ЧЕРТЕЖ:“ няма, а резервният път за
    ЕДИН лист вижда ДВЕ заглавия и правилно отказва. Затова рамки бяха нула и
    портата спираше всичките 137 позиции, макар 120 да са силни по геометрия.
    С две обявени рамки същият чертеж дава 90 автоматично одобрени.

    ДОКАЗАТЕЛСТВОТО Е ГРУПА ЕДНОРОДНИ ЗАГЛАВИЯ, НЕ ЕДНО СЛУЧАЙНО:

      * заглавието се класифицира еднозначно и с висока увереност;
      * поне две такива заглавия, на ЕДИН И СЪЩ слой и с еднаква височина на
        текста — така бележка в легенда не става лист;
      * ако две различни групи са с еднакъв, най-голям брой — отказ, защото не
        се избира между два еднакво силни прочита;
      * лентите се режат от съдържанието (``_frame_bands``); не излязат ли
        честно — отказ;
      * всяка лента трябва да съдържа поне един армировъчен надпис, иначе
        рамката е върху празно място.

    Пътят се ползва САМО когато няма щемпел и няма обявена рамка, за да не мени
    нито един вече работещ чертеж.
    """

    кандидати: list[tuple[DxfEntityRecord, str, str]] = []
    for record in text_records:
        if _xy(record) is None:
            continue
        категория = _anchored_sheet_category(record)
        if not категория:
            continue
        заглавие = decode_dxf_text(record.text).strip()
        if заглавие:
            кандидати.append((record, заглавие, категория))
    if len(кандидати) < 2:
        return []

    групи: dict[tuple[str, float], list[tuple[DxfEntityRecord, str, str]]] = {}
    for record, заглавие, категория in кандидати:
        # ``getattr``, защото модулът приема и по-бедни записи: няколко
        # регресионни теста подават заместители само с handle, тип, текст и
        # геометрия. Липсващият слой не бива да чупи търсенето на рамки.
        ключ = (str(getattr(record, "layer", "") or ""), round(_text_height(record), 1))
        групи.setdefault(ключ, []).append((record, заглавие, категория))
    годни = [сп for сп in групи.values() if len(сп) >= 2]
    if not годни:
        return []

    def разстлан(сп: list[tuple[DxfEntityRecord, str, str]]) -> float:
        xs = [(_xy(record) or (0.0, 0.0))[0] for record, _з, _к in сп]
        return max(xs) - min(xs)

    # Заглавието на ЛИСТ дели листове, затова стои далеч от другите заглавия;
    # етикетът на ДЕТАЙЛ стои вътре в един лист, до себеподобните си. Измерено
    # 2026-09-20 в „тест-01“: заглавията на слой ANTETKA са на 6562 единици едно
    # от друго, а двата етикета „Детайл за …“ на слой Text — на 56, тоест 117
    # пъти по-близо. Затова се избира групата с най-голям разстлан, и то само
    # когато води с ясен превес; иначе отказ, вместо избор вместо оператора.
    най_много = max(len(сп) for сп in годни)
    водачи = sorted(
        (сп for сп in годни if len(сп) == най_много),
        key=разстлан,
        reverse=True,
    )
    най_голяма = водачи[0]
    други = [сп for сп in годни if сп is not най_голяма]
    ако_има = max((разстлан(сп) for сп in други), default=0.0)
    if ако_има > 0.0 and разстлан(най_голяма) < ако_има * TITLE_SPREAD_MARGIN:
        return []
    if len(водачи) > 1 and разстлан(водачи[1]) > 0.0 and (
        разстлан(най_голяма) < разстлан(водачи[1]) * TITLE_SPREAD_MARGIN
    ):
        return []

    точки = {(_xy(record) or (0.0, 0.0)) for record, _з, _к in най_голяма}
    if len(точки) != len(най_голяма):
        return []

    # Листовете на един чертеж се ДЕЛЯТ по категория: долна, горна, детайли,
    # греди. Две заглавия с ЕДНА И СЪЩА категория не са два листа, а два
    # етикета вътре в един.
    #
    # Измерено 2026-09-21 — дефект в първата редакция на този път: в
    # „S- H03-3902 … Top_Reinforcement_at_Roof_Level“ единственото заглавие на
    # лист е „Горна армировка“, но то е само едно, а „Детайл“ и „Детайли за
    # продънване“ са две. Затова пътят направи два листа „детайли“ и 39
    # позиции легнаха на ЧУЖД лист. Разстланът не помогна — етикетите бяха
    # достатъчно далеч един от друг.
    #
    # Изискването за различни категории е и по-безопасно: чертеж с два листа от
    # една категория остава за оператора, вместо да бъде разделен по догадка.
    категории = {категория for _r, _з, категория in най_голяма}
    if len(категории) != len(най_голяма):
        return []

    сурови: list[DrawingFrame] = []
    for record, заглавие, категория in най_голяма:
        x, y = _xy(record) or (0.0, 0.0)
        сурови.append(
            DrawingFrame(
                index=0,
                marker_handle=str(record.handle or ""),
                anchor_x=x,
                anchor_y=y,
                title_text=заглавие,
                supporting_text="",
                category_code=категория,
                suggested_sheet_name=CATEGORY_SHEET_NAMES[категория],
                confidence="HIGH",
                resolution_reason="TITLE_ONLY_BAND",
                title_handle=str(record.handle or ""),
                marker_text_height=round(_text_height(record), 3),
            )
        )

    сурови.sort(key=lambda frame: (-round(frame.anchor_y / 500.0), frame.anchor_x))
    наименувани: list[DrawingFrame] = []
    ползвани: dict[str, int] = {}
    for индекс, frame in enumerate(сурови, start=1):
        база = frame.suggested_sheet_name
        ползвани[база] = ползвани.get(база, 0) + 1
        име = база if ползвани[база] == 1 else f"{база} {ползвани[база]}"
        наименувани.append(replace(frame, index=индекс, suggested_sheet_name=име))

    ленти = _frame_bands(records, наименувани)
    if ленти is None or len(ленти) != len(наименувани):
        return []
    с_ленти = [
        replace(frame, x_min=ленти[frame.index][0], x_max=ленти[frame.index][1])
        for frame in наименувани
    ]

    # Рамка върху празно място не е лист.
    надписи = [
        _xy(record)
        for record in text_records
        if looks_like_rebar_text(decode_dxf_text(record.text))
    ]
    надписи = [точка for точка in надписи if точка is not None]
    for frame in с_ленти:
        ниско = float(frame.x_min if frame.x_min is not None else 0.0)
        високо = float(frame.x_max if frame.x_max is not None else 0.0)
        if not any(ниско <= x <= високо for x, _y in надписи):
            return []
    return с_ленти


def _detect_declared_area_frames(
    records: list[DxfEntityRecord],
) -> list[DrawingFrame]:
    """Обявени рамки: затворен правоъгълник на слой, чието ИМЕ се класифицира.

    Собственик, 2026-09-19, върху първия чертеж от друг проектант
    (AutoCAD Structural Detailing, `Тест-желязо\\d-2`): „ще ти направя 4 рамки
    с номера 1 долна скара, 2 долна усилване, 3 горна скара, 4 горна
    усилване“ — и ги начерта, всяка на свой слой.

    ЗАЩО. Този чертеж няма щемпел и няма нито един текст с „АРМИРОВ“ или
    „ЧЕРТЕЖ“: заглавието му живее само в името на файла. Затова
    ``detect_drawing_frames`` връщаше нула рамки, портата отказваше всяка
    позиция с ``MISSING_FRAME`` и от 178 позиции се одобряваха 0 — макар
    разпознаването да даваше 124 силни.

    Един лист държи четири плана: основна скара и усилване, долу и горе. Затова
    обявената рамка носи и граници по y — лентата по x не дели планове, които
    стоят един над друг.

    ДОКАЗАТЕЛСТВОТО Е ДЕКЛАРАЦИЯ, НЕ ДОГАДКА. Операторът чертае правоъгълника и
    го кръщава; програмата само проверява:

      * точно един затворен правоъгълник на слоя — два са отказ за този слой;
      * името на слоя да се класифицира еднозначно (``classify_title``);
        „Подробна спецификация“ дава ``UNKNOWN`` и се пропуска;
      * различните обявени рамки да не се застъпват.

    Пътят се ползва САМО когато щемпелът не е дал рамки, за да не мени нито
    един вече работещ чертеж.
    """

    by_layer: dict[str, list[tuple[float, float, float, float]]] = {}
    for record in records:
        if str(record.entity_type or "").upper() not in {"LWPOLYLINE", "POLYLINE"}:
            continue
        points = (record.geometry or {}).get("points") or (record.geometry or {}).get(
            "vertices"
        )
        if not isinstance(points, (list, tuple)) or not 4 <= len(points) <= 5:
            continue
        try:
            xs = [float(point[0]) for point in points]
            ys = [float(point[1]) for point in points]
        except (TypeError, ValueError, IndexError):
            continue
        # Правоъгълник: точно две различни стойности по всяка ос.
        if len({round(value, 3) for value in xs}) != 2:
            continue
        if len({round(value, 3) for value in ys}) != 2:
            continue
        by_layer.setdefault(str(record.layer or ""), []).append(
            (min(xs), min(ys), max(xs), max(ys))
        )

    declared: list[tuple[str, str, tuple[float, float, float, float]]] = []
    for layer, boxes in sorted(by_layer.items()):
        if len(boxes) != 1:
            continue
        category, confidence = classify_title(layer.replace("-", " ").replace("_", " "))
        if category == "UNKNOWN" or confidence != "HIGH":
            continue
        declared.append((layer, category, boxes[0]))
    if not declared:
        return []
    for left in range(len(declared)):
        for right in range(left + 1, len(declared)):
            a = declared[left][2]
            b = declared[right][2]
            if not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1]):
                # Застъпени обявени рамки: не се знае чия е позицията.
                return []

    frames = [
        DrawingFrame(
            index=0,
            marker_handle="",
            anchor_x=(box[0] + box[2]) / 2.0,
            anchor_y=(box[1] + box[3]) / 2.0,
            title_text=layer,
            supporting_text="",
            category_code=category,
            suggested_sheet_name=CATEGORY_SHEET_NAMES[category],
            confidence="HIGH",
            resolution_reason="DECLARED_AREA_LAYER",
            resolved_by="OPERATOR",
            x_min=box[0],
            x_max=box[2],
            y_min=box[1],
            y_max=box[3],
        )
        for layer, category, box in declared
    ]
    frames.sort(key=lambda frame: (CATEGORY_ORDER[frame.category_code], frame.anchor_x))
    result: list[DrawingFrame] = []
    used: dict[str, int] = {}
    for index, frame in enumerate(frames, start=1):
        base = frame.suggested_sheet_name
        used[base] = used.get(base, 0) + 1
        name = base if used[base] == 1 else f"{base} {used[base]}"
        result.append(replace(frame, index=index, suggested_sheet_name=name))
    return result


def _detect_single_sheet_frame(
    records: list[DxfEntityRecord],
    text_records: list[DxfEntityRecord],
) -> list[DrawingFrame]:
    """Resolve one frameless sheet from one unambiguous explicit title.

    This is not a largest-rectangle or nearest-text guess. The unique title
    must also have independent single-sheet evidence: exactly one frame INSERT,
    or (for a vertical schedule) explicit scheme plus column/wall headings.
    Anything weaker remains fail-closed and returns no frame.
    """

    candidates: list[tuple[DxfEntityRecord, str]] = []
    for record in text_records:
        decoded = decode_dxf_text(record.text)
        normalized = _normalize(decoded)
        if not normalized or len(normalized) > 180 or "\n" in decoded:
            continue
        category = next(
            (
                category_code
                for category_code, pattern in SINGLE_SHEET_TITLE_PATTERNS
                if pattern.search(normalized)
            ),
            "",
        )
        if category and _xy(record) is not None:
            candidates.append((record, category))
    if len(candidates) != 1:
        return []

    title_record, category = candidates[0]
    frame_inserts = [
        record
        for record in records
        if str(record.entity_type or "").upper() == "INSERT"
        and _xy(record) is not None
        and _is_frame_layer(record.layer)
    ]
    marker_record: DxfEntityRecord
    resolution_reason: str
    if len(frame_inserts) == 1:
        marker_record = frame_inserts[0]
        resolution_reason = "SINGLE_SHEET_TITLE_AND_FRAME_INSERT"
    elif not frame_inserts and category == "VERTICALS" and _has_vertical_schedule_evidence(
        text_records,
        title_handle=title_record.handle,
    ):
        marker_record = title_record
        resolution_reason = "SINGLE_SHEET_TITLE_AND_VERTICAL_SECTIONS"
    else:
        return []

    anchor_x, anchor_y = _xy(marker_record) or (0.0, 0.0)
    title_x, title_y = _xy(title_record) or (anchor_x, anchor_y)
    title = decode_dxf_text(title_record.text).strip()
    if category in {"BOTTOM", "TOP"} and "УСИЛВА" in _normalize(title):
        category = f"{category}_EXTRA"
    return [
        DrawingFrame(
            index=1,
            marker_handle=marker_record.handle,
            anchor_x=anchor_x,
            anchor_y=anchor_y,
            title_text=title,
            supporting_text="",
            category_code=category,
            suggested_sheet_name=CATEGORY_SHEET_NAMES[category],
            confidence="MEDIUM",
            resolution_reason=resolution_reason,
            title_handle=title_record.handle,
            title_distance=round(math.hypot(title_x - anchor_x, title_y - anchor_y), 3),
            marker_text_height=round(_text_height(marker_record), 3),
            resolved_by="AUTO",
        )
    ]


def _is_frame_layer(layer: object) -> bool:
    normalized = str(layer or "").strip().upper()
    return bool(
        re.fullmatch(
            r"\$?(?:RAMKA|FRAME|РАМКА)(?:[_\-\s]*\d+)?",
            normalized,
        )
    )


def _has_vertical_schedule_evidence(
    text_records: list[DxfEntityRecord],
    *,
    title_handle: str,
) -> bool:
    texts = [
        _normalize(record.text)
        for record in text_records
        if record.handle != title_handle
        and 0 < len(_normalize(record.text)) <= 180
    ]
    has_named_verticals = any(
        re.match(r"^(?:КОЛОНИ|ШАЙБИ)\b", text)
        for text in texts
    )
    has_scheme = any(
        re.match(r"^СХЕМА\s+НА\s+(?:АРМ|КОНСТР)", text)
        for text in texts
    )
    return has_named_verticals and has_scheme


def apply_operator_frame_decisions(
    frames: list[DrawingFrame],
    decisions: dict[str, str] | None,
) -> list[DrawingFrame]:
    """Прилага операторски решения за категория на UNKNOWN рамки.

    ``decisions`` е {marker_handle: category_code}. Прилага се САМО върху
    рамки, които автоматиката е оставила UNKNOWN — операторът не може да
    презапише доказана автоматична категория през този път.
    Решението е проследимо: ``resolved_by="OPERATOR"``.
    """

    if not decisions:
        return frames
    result: list[DrawingFrame] = []
    for frame in frames:
        category = decisions.get(frame.marker_handle)
        if (
            category
            and frame.is_unknown
            and category in CATEGORY_SHEET_NAMES
            and category != "UNKNOWN"
        ):
            result.append(
                DrawingFrame(
                    index=frame.index,
                    marker_handle=frame.marker_handle,
                    anchor_x=frame.anchor_x,
                    anchor_y=frame.anchor_y,
                    title_text=frame.title_text,
                    supporting_text=frame.supporting_text,
                    category_code=category,
                    suggested_sheet_name=CATEGORY_SHEET_NAMES[category],
                    confidence="OPERATOR",
                    resolution_reason="OPERATOR_DECISION",
                    title_handle=frame.title_handle,
                    title_distance=frame.title_distance,
                    marker_text_height=frame.marker_text_height,
                    resolved_by="OPERATOR",
                    x_min=frame.x_min,
                    x_max=frame.x_max,
                )
            )
        else:
            result.append(frame)
    return result


def unresolved_frames(frames: list[DrawingFrame]) -> list[DrawingFrame]:
    """Рамки, които изискват операторско решение преди production export."""

    return [frame for frame in frames if frame.requires_operator_decision]


def frame_provenance_rows(frames: list[DrawingFrame]) -> list[dict]:
    """Проследим запис за всяка рамка — за evidence и export manifest."""

    return [
        {
            "index": frame.index,
            "marker_handle": frame.marker_handle,
            "anchor_x": frame.anchor_x,
            "anchor_y": frame.anchor_y,
            "title_text": frame.title_text,
            "supporting_text": frame.supporting_text,
            "title_handle": frame.title_handle,
            "title_distance": frame.title_distance,
            "marker_text_height": frame.marker_text_height,
            "category_code": frame.category_code,
            "confidence": frame.confidence,
            "resolution_reason": frame.resolution_reason,
            "resolved_by": frame.resolved_by,
            "sheet_name": frame.suggested_sheet_name,
        }
        for frame in frames
    ]


def classify_title(title: str, supporting_text: str = "") -> tuple[str, str]:
    normalized = _normalize(f"{title} {supporting_text}")
    extra = "УСИЛВА" in normalized
    if "ДОЛНА" in normalized:
        return ("BOTTOM_EXTRA" if extra else "BOTTOM", "HIGH")
    if "ГОРНА" in normalized:
        return ("TOP_EXTRA" if extra else "TOP", "HIGH")
    if "ДЕТАЙЛ" in normalized:
        return "DETAILS", "HIGH"
    if "ГРЕД" in normalized:
        return "BEAMS", "HIGH"
    if "ВЕРТИКАЛ" in normalized or re.search(
        r"\bВЕРТ\s*\.?\s*АРМИРОВ",
        normalized,
    ):
        return "VERTICALS", "HIGH"
    # ШАЙБИ и КОЛОНИ бяха обявени на две други места в този модул -- имат свои
    # листове в ``CATEGORY_SHEET_NAMES`` и свои образци в
    # ``SINGLE_SHEET_TITLE_PATTERNS`` -- но липсваха тук. Затова заглавие
    # „Армировка шайби“ оставаше UNKNOWN, а в „KOFR_B“ листовете „ЧЕРТЕЖ: ШАЙБИ
    # 1-6“ и „ЧЕРТЕЖ: ТАБЛИЦА НА КОЛОНИТЕ“ не получаваха категория. Проверките
    # са НАКРАЯ, за да не отнемат заглавие на вече работещите категории:
    # „ДОЛНА АРМИРОВКА ПЛОЧА И ГРЕДИ“ остава BOTTOM, както досега.
    if "ШАЙБ" in normalized:
        return "WALLS", "HIGH"
    if "КОЛОН" in normalized:
        return "COLUMNS", "HIGH"
    return "UNKNOWN", "LOW"


def _frame_bands(
    records: Sequence[DxfEntityRecord],
    frames: Sequence[DrawingFrame],
) -> dict[int, tuple[float, float]] | None:
    """Лентата на всеки чертеж по x, изчислена от съдържанието.

    НЯМА ПРАГ. Реже се по най-широката празнина и само толкова пъти,
    колкото е нужно всяка лента да съдържа точно един щемпел — броят на
    щемпелите калибрира рязането сам. Праг, нагоден по един чертеж, би се
    счупил на следващия: в ``s-9`` вярната стойност е между 3 736 и 5 689.

    Връща ``None``, когато лентите не излизат честно — например чертежи,
    подредени в два реда вместо в редица. Тогава остава старото правило:
    по-добре познато поведение, отколкото ново гадане.
    """

    xs = sorted(
        point[0] for point in (_xy(record) for record in records) if point
    )
    if not xs:
        return None
    anchors = sorted((float(frame.anchor_x), frame.index) for frame in frames)
    if len(anchors) == 1:
        return {anchors[0][1]: (xs[0], xs[-1])}

    gaps = sorted(
        ((b - a, (a + b) / 2.0) for a, b in zip(xs, xs[1:]) if b > a),
        reverse=True,
    )
    cuts: list[float] = []
    needed = len(anchors) - 1
    for _width, place in gaps:
        candidate = sorted(cuts + [place])
        counts = [0] * (len(candidate) + 1)
        for anchor_x, _index in anchors:
            slot = 0
            while slot < len(candidate) and anchor_x > candidate[slot]:
                slot += 1
            counts[slot] += 1
        # Разрез, който оставя лента без щемпел, е безсмислен: той цепи
        # един чертеж на две вместо да раздели два съседни.
        if 0 in counts:
            continue
        cuts = candidate
        if len(cuts) == needed:
            break
    if len(cuts) != needed:
        return None

    edges = [xs[0]] + cuts + [xs[-1]]
    return {
        index: (edges[position], edges[position + 1])
        for position, (_anchor_x, index) in enumerate(anchors)
    }


def assign_point_to_frame(x: float, y: float, frames: list[DrawingFrame]) -> int | None:
    """Коя рамка държи тази точка.

    Лентата побеждава разстоянието. Най-близката котва остава само за
    чертежи, при които лентите не са могли да се изчислят — щемпелът не е
    в средата на своя чертеж и разстоянието до него не е граница.
    """

    if not frames:
        return None
    # Обявената рамка е правоъгълник, начертан от оператора — тя бие и
    # лентата, и разстоянието. Точката трябва да е в ТОЧНО ЕДНА: две
    # припокриващи се обявени рамки са отказ, не догадка.
    declared = [frame for frame in frames if frame.contains_point(x, y)]
    if len(declared) == 1:
        return declared[0].index
    if declared:
        return None
    if any(frame.is_declared_area for frame in frames):
        # Има обявени рамки, но точката не е в нито една: тя е извън
        # плановете (таблица, легенда, бележки) и няма лист.
        return None
    banded = sorted(
        (frame for frame in frames if frame.contains_x),
        key=lambda frame: float(frame.x_min),
    )
    for frame in banded:
        if float(frame.x_min) <= x <= float(frame.x_max):
            return frame.index
    nearest = min(
        frames,
        key=lambda frame: (x - frame.anchor_x) ** 2 + (y - frame.anchor_y) ** 2,
    )
    return nearest.index


def sanitize_sheet_name(value: str, *, fallback: str = "лист") -> str:
    cleaned = re.sub(r"[\\/*?:\[\]]", "-", str(value or "").strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" '") or fallback
    return cleaned[:31]


def _text_height(record: DxfEntityRecord) -> float:
    for key in ("char_height", "height", "text_height"):
        try:
            value = float(record.geometry.get(key) or 0.0)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return 0.0


def _xy(record: DxfEntityRecord) -> tuple[float, float] | None:
    value = record.geometry.get("insert") or record.geometry.get("visual_anchor")
    try:
        return float(value[0]), float(value[1])
    except (TypeError, ValueError, IndexError):
        return None


def _normalize(value: str | None) -> str:
    """Нормализация ЗА ТЪРСЕНЕ, вкл. декодиране на AutoCAD escape.

    До 6.3.1 тук нямаше декодиране, затова текстове, записани като
    ``\\U+0427...``, не се разпознаваха и рамките оставаха ненамерени
    (Belgravia: 0 маркера върху суров текст, 14 след декодиране).
    """

    return normalize_for_match(value)
