from __future__ import annotations

from dataclasses import dataclass, replace
import math
import re
from typing import Iterable, Sequence

from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.dxf.text_normalization import decode_dxf_text, normalize_for_match


# Ключови думи за заглавие на рамка (използват се и в основния прозорец,
# и във fallback-а, за да няма два различни речника).
TITLE_TOKENS = ("АРМИРОВ", "ДЕТАЙЛ", "ГРЕД", "ДОЛНА", "ГОРНА", "ВЕРТИКАЛ")

# ``ЧЕРТЕЖ`` inside a note is not a title-block marker.  Real title blocks
# use the dedicated label ``ЧЕРТЕЖ:``.  The previous substring search also
# matched prose such as ``от чертежите`` and invented an UNKNOWN frame.
FRAME_MARKER_RE = re.compile(r"^ЧЕРТЕЖ\s*:?$")

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

    @property
    def contains_x(self) -> bool:
        return self.x_min is not None and self.x_max is not None

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
    markers = [
        record
        for record in text_records
        if FRAME_MARKER_RE.fullmatch(_normalize(record.text))
        and _xy(record) is not None
    ]
    if not markers:
        return _detect_single_sheet_frame(records, text_records)
    frames: list[DrawingFrame] = []
    for marker in markers:
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

        title = decode_dxf_text(title_record.text).strip() if title_record else ""
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
