from __future__ import annotations

import hashlib
import itertools
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence
from xml.sax.saxutils import escape

MASTER_ENGINE_FILENAME = "OPEN_ENGINE_v0.3.1_MASTER_F009_LOCKED.html"
MASTER_ENGINE_SHA256 = "38a63caf629f77ceaceef6ea86542f357d4f1a220217367f16aac92a74269a77"
MASTER_ENGINE_VERSION = "0.3.1"
MASTER_FORM_CODES = tuple(f"F{index:03d}" for index in range(1, 10))
MASTER_TOTAL_TOLERANCE_CM = 0.11

#: Формите, добавени в ENGINE след заключения v0.3.1 (стълбищните рамена и
#: площадки от v0.3.2, капителите). Всички са ВЕРИГИ от прави сегменти: в
#: ENGINE всеки сегмент е ``ДИРЕКТНА_ДЪЛЖИНА``, тоест сам по себе си
#: производствен размер, без проекции — ъглите между тях са само посока.
#: Затова тук стои една таблица вместо шест отделни описания.
#:
#: Дотогава ``parameters_from_segments`` връщаше „Неподдържан MASTER код" за
#: всяка от тях, докато диалозите за ръчен избор ги предлагаха на оператора
#: (те филтрират по ``active``): изборът водеше до грешка, не до форма.
#:
#: Посоката е в градуси срещу часовниковата стрелка — 0 надясно, 90 нагоре,
#: 180 наляво, 270 надолу. Всеки завой е ``(страна, градуси)``, където „L"
#: завърта наляво. Стойностите идват от ``template_orientation`` на самите
#: ENGINE дефиниции.
#:
#: Ъглите на стълбищните форми са РЕФЕРЕНТНИ, не определящи: дефинициите им
#: носят ``angle_mode='AUTO'`` без ``min/max_angle_deg``, защото стълбищата
#: се проектират с различен наклон. Тук те служат само за силуета в
#: схематичната рисунка — производствените размери са самите дължини.
_CHAIN_FORMS: dict[str, tuple[float, tuple[tuple[str, float], ...]]] = {
    # НЕСТАНДАРТНА ФОРМА – КАПИТЕЛИ: правоъгълна обиколка с пет рамена.
    "F011": (180.0, (("L", 90.0), ("L", 90.0), ("L", 90.0), ("L", 90.0))),
    # СТЪЛБИЩНО РАМО 1: полегат марш, после право коляно.
    "F012": (0.0, (("R", 29.0), ("R", 90.0))),
    # СТЪЛБИЩНО РАМО 2: най-простото — едно рамо и марш.
    "F013": (0.0, (("R", 29.0),)),
    # СТЪЛБИЩНО РАМО 3: Z плюс право коляно накрая.
    "F014": (0.0, (("L", 29.0), ("R", 29.0), ("R", 90.0))),
    # СТЪЛБИЩНА ПЛОЩАДКА 1: право коляно, после полегат изход.
    "F016": (270.0, (("L", 90.0), ("R", 29.0))),
    # СТЪЛБИЩНО РАМО 5: чисто Z — късо рамо, дълъг марш, късо рамо.
    # 30° е схематичен избор между двата измерени наклона (29° при
    # контролната позиция 8N10x406, 42° при „4N16x323").
    "F019": (0.0, (("L", 30.0), ("R", 30.0))),
}

#: Колко сегмента очаква всяка верижна форма — с един повече от завоите.
_CHAIN_SEGMENT_COUNTS = {
    code: len(turns) + 1 for code, (_start, turns) in _CHAIN_FORMS.items()
}


def _chain_points(code: str, lengths: Sequence[float]) -> list[tuple[float, float]]:
    """Върховете на верижна форма, започвайки от началото на координатите."""

    start, turns = _CHAIN_FORMS[code]
    heading = math.radians(start)
    points: list[tuple[float, float]] = [(0.0, 0.0)]
    for index, length in enumerate(lengths):
        x, y = points[-1]
        points.append(
            (x + length * math.cos(heading), y + length * math.sin(heading))
        )
        if index < len(turns):
            side, angle = turns[index]
            heading += math.radians(angle if side == "L" else -angle)
    return points

#: Максимална добавка за огъване при F006. Измерена в реален чертеж:
#: при 43 от 60 стремета точно 5 cm, единични 4 и 6.
from lion_rebar.engine.bend_allowance import (  # noqa: E402
    BEND_ALLOWANCE_LIMIT_CM,
    segments_match_total,
)


class MasterEngineError(RuntimeError):
    pass


class MasterDimensionError(MasterEngineError):
    pass


@dataclass(frozen=True)
class MasterPreflightResult:
    filename: str
    sha256: str
    version: str
    form_codes: tuple[str, ...]
    test_count: int
    tests_passed: int


@dataclass(frozen=True)
class MasterShapeParameters:
    shape_code: str
    values: dict[str, float]
    expanded_segments_cm: tuple[float, ...]
    total_length_cm: float


_LIBRARY_CACHE: dict[str, Any] | None = None


def master_engine_path() -> Path:
    return Path(__file__).resolve().parent.parent / "resources" / MASTER_ENGINE_FILENAME


def master_engine_preflight() -> MasterPreflightResult:
    path = master_engine_path()
    if not path.is_file():
        raise MasterEngineError(f"Липсва заключеният MASTER renderer: {path}")
    sha = _sha256(path)
    if sha != MASTER_ENGINE_SHA256:
        raise MasterEngineError(
            "OPEN_ENGINE v0.3.1 MASTER е променен или повреден. "
            f"Очакван SHA256 {MASTER_ENGINE_SHA256}, получен {sha}."
        )
    text = path.read_text(encoding="utf-8")
    if "ENGINE v0.3.1 MASTER · F001–F009 LOCKED" not in text:
        raise MasterEngineError("Файлът не е одобреният OPEN_ENGINE v0.3.1 MASTER.")
    library = load_master_library()
    forms = library.get("ФОРМИ") or []
    form_codes = tuple(str(item.get("КОД_ФОРМА") or "") for item in forms)
    if form_codes != MASTER_FORM_CODES:
        raise MasterEngineError(
            "MASTER библиотеката не съдържа точно заключените F001–F009 в очаквания ред."
        )
    tests = library.get("ТЕСТОВЕ") or []
    passed = 0
    for test in tests:
        code = str(test.get("КОД_ФОРМА") or "")
        values = _parse_dimensions(str(test.get("ОЧАКВАНИ_РАЗМЕРИ_СМ") or ""))
        expected = float(test.get("ОЧАКВАНА_ОБЩА_ДЪЛЖИНА_СМ") or 0.0)
        calculated = total_for(code, values)
        if abs(calculated - expected) > MASTER_TOTAL_TOLERANCE_CM:
            raise MasterEngineError(
                f"MASTER тест {test.get('ID_ТЕСТ')} е неуспешен: "
                f"очаква {expected:g} cm, изчислява {calculated:g} cm."
            )
        # Also exercise the actual SVG renderer. This blocks export if a
        # locked form can no longer be drawn.
        render_master_svg(code, values, include_safe_zone=False)
        passed += 1
    return MasterPreflightResult(
        filename=path.name,
        sha256=sha,
        version=MASTER_ENGINE_VERSION,
        form_codes=form_codes,
        test_count=len(tests),
        tests_passed=passed,
    )


def load_master_library() -> dict[str, Any]:
    global _LIBRARY_CACHE
    if _LIBRARY_CACHE is not None:
        return _LIBRARY_CACHE
    text = master_engine_path().read_text(encoding="utf-8")
    match = re.search(
        r"const\s+DEFAULT_LIBRARY\s*=\s*(\{.*?\});\s*let\s+library\s*=",
        text,
        flags=re.DOTALL,
    )
    if not match:
        raise MasterEngineError("DEFAULT_LIBRARY не може да бъде извлечена от MASTER файла.")
    try:
        library = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise MasterEngineError(f"MASTER библиотеката е невалиден JSON: {exc}") from exc
    if not isinstance(library, dict):
        raise MasterEngineError("MASTER библиотеката няма очакваната структура.")
    _LIBRARY_CACHE = library
    return library


def parameters_from_segments(
    shape_code: str,
    segment_lengths_cm: Sequence[float],
    *,
    production_length_cm: float | None = None,
    turn_angles_deg: Sequence[float] = (),
) -> MasterShapeParameters:
    code = str(shape_code or "").strip().upper()
    lengths = tuple(float(value) for value in segment_lengths_cm)
    if any(not math.isfinite(value) or value <= 0 for value in lengths):
        raise MasterDimensionError(f"{code}: сегментните размери трябва да са положителни.")
    total = float(production_length_cm) if production_length_cm is not None else sum(lengths)
    if not math.isfinite(total) or total <= 0:
        raise MasterDimensionError(f"{code}: липсва валидна производствена дължина.")

    values: dict[str, float]
    expanded: tuple[float, ...]
    if code == "F001":
        if len(lengths) != 1:
            raise MasterDimensionError(f"F001 очаква 1 сегмент, получени са {len(lengths)}.")
        # A straight bar has one authoritative production dimension. DXF
        # geometry is frequently schematic (for example 269.9 for a declared
        # 270 cm bar), so the VID label must always show the reviewed total.
        values = {"L1": total}
        expanded = (total,)
    elif code == "F002":
        if len(lengths) != 3:
            raise MasterDimensionError(f"F002 очаква 3 сегмента, получени са {len(lengths)}.")
        # Locked Engine orientation: the longer straight arm is the lower
        # left L1, the reduction climbs to the right, and the shorter L3
        # continues at the upper right. DXF traversal may arrive reversed;
        # traversal direction is not a production identity and must not flip
        # the approved MASTER picture.
        outer_a, diagonal, outer_b = lengths
        lower_main = max(outer_a, outer_b)
        upper_tail = min(outer_a, outer_b)
        angle = abs(float(turn_angles_deg[0])) if turn_angles_deg else 45.0
        values = {
            "L1": lower_main,
            "DX2": abs(diagonal * math.cos(math.radians(angle))),
            "DY2": abs(diagonal * math.sin(math.radians(angle))),
            "L3": upper_tail,
        }
        expanded = (lower_main, diagonal, upper_tail)
    elif code == "F003":
        if len(lengths) != 3:
            raise MasterDimensionError(f"F003 очаква 3 сегмента, получени са {len(lengths)}.")
        values = {"L1": lengths[0], "L2": lengths[1], "L3": lengths[2]}
        expanded = lengths
    elif code == "F004":
        if len(lengths) != 2:
            raise MasterDimensionError(f"F004 очаква 2 сегмента, получени са {len(lengths)}.")
        values = {"L1": lengths[0], "L2": lengths[1]}
        expanded = lengths
    elif code == "F005":
        if len(lengths) == 2:
            # Backward-compatible input for projects created with the locked
            # v0.3.1 renderer. New Engine packages always provide all three
            # independent segments, while the old two-parameter form still
            # expands deterministically to a symmetric hairpin.
            arm1, bridge = lengths
            arm2 = arm1
            expanded = (arm1, bridge, arm2)
        elif len(lengths) == 3:
            arm1, bridge, arm2 = lengths
            expanded = lengths
        else:
            raise MasterDimensionError(f"F005 очаква 2 параметъра или 3 сегмента, получени са {len(lengths)}.")
        values = {"L1": arm1, "L2": bridge, "L3": arm2}
    elif code == "F006":
        if len(lengths) == 3:
            hook, side_a, side_b = lengths
            # MASTER orientation is canonical and independent of the DXF
            # drawing direction: the larger rectangle side is always L2
            # (horizontal/top), while the smaller side is L3 (vertical/side).
            width, height = max(side_a, side_b), min(side_a, side_b)
            expanded = (hook, width, height, width, height, hook)
        elif len(lengths) >= 6:
            hook1, side_a1, side_b1, side_a2, side_b2, hook2 = lengths[:6]
            _require_equal("F006 куки", hook1, hook2)
            _require_equal("F006 първа двойка страни", side_a1, side_a2)
            _require_equal("F006 втора двойка страни", side_b1, side_b2)
            hook = hook1
            width = max(side_a1, side_b1)
            height = min(side_a1, side_b1)
            # Expanded segments are rewritten in the locked MASTER traversal,
            # not the source/DXF orientation.
            expanded = (hook, width, height, width, height, hook)
        else:
            raise MasterDimensionError(f"F006 очаква 3 параметъра или 6 сегмента, получени са {len(lengths)}.")
        values = {"L1": hook, "L2": width, "L3": height}
    elif code == "F007":
        if len(lengths) == 2:
            arm, top = lengths
            expanded = (arm, top, arm)
        elif len(lengths) == 3:
            arm1, top, arm2 = lengths
            _require_equal("F007 крайни рамена", arm1, arm2)
            arm = arm1
            expanded = lengths
        else:
            raise MasterDimensionError(f"F007 очаква 2 параметъра или 3 сегмента, получени са {len(lengths)}.")
        values = {"L1": arm, "L2": top}
    elif code == "F008":
        if len(lengths) == 3:
            foot, height, top = lengths
            expanded = (foot, height, top, height, foot)
        elif len(lengths) == 5:
            foot1, height1, top, height2, foot2 = lengths
            _require_equal("F008 крачета", foot1, foot2)
            _require_equal("F008 височини", height1, height2)
            foot, height = foot1, height1
            expanded = lengths
        else:
            raise MasterDimensionError(f"F008 очаква 3 параметъра или 5 сегмента, получени са {len(lengths)}.")
        values = {"L1": foot, "L2": height, "L3": top}
    elif code == "F009":
        if len(lengths) != 5:
            raise MasterDimensionError(f"F009 очаква 5 сегмента, получени са {len(lengths)}.")
        values = {f"L{index}": value for index, value in enumerate(lengths, start=1)}
        expanded = lengths
    elif code == "F010":
        if len(lengths) != 5:
            raise MasterDimensionError(f"F010 очаква 5 сегмента, получени са {len(lengths)}.")
        values = {f"L{index}": value for index, value in enumerate(lengths, start=1)}
        expanded = lengths
    elif code in _CHAIN_FORMS:
        # Верижните форми (виж ``_CHAIN_FORMS``): всеки сегмент е собствен
        # производствен размер, без проекции. Наклонът не скъсява нито един
        # от тях — той е само посоката между два съседни.
        #
        # Собственик, 2026-09-13, за „4N16x323" [60, 203, 60] под 42°:
        # „Като тази за стълбищен манш." Контролната позиция на F019 в ENGINE
        # е 8N10x406 [50, 286, 70] при около 29° — различен наклон, същата
        # форма, което е и причината ъгълът да не е част от определението.
        expected = _CHAIN_SEGMENT_COUNTS[code]
        if len(lengths) != expected:
            raise MasterDimensionError(
                f"{code} очаква {expected} сегмента, получени са {len(lengths)}."
            )
        values = {f"L{index}": value for index, value in enumerate(lengths, start=1)}
        expanded = lengths
    else:
        raise MasterDimensionError(f"Неподдържан MASTER код: {code}")

    calculated = total_for(code, values)
    # Owner-confirmed engineering decision (2026-09-09): production length
    # comes from the designer's annotation, not from precisely measured
    # geometry — DXF drawing tools do not guarantee millimeter accuracy, and
    # this product reads a rebar specification, it does not audit the
    # designer's drafting precision. F006 (stirrups) tolerates more drift
    # than other shapes because its bend geometry compounds small drafting
    # errors across more segments. See docs/KNOWN_LIMITATIONS_v0.1.0_BG.md.
    row_tolerance = 2.1 if code == "F006" else MASTER_TOTAL_TOLERANCE_CM
    # Единен източник на истина (lion_rebar.engine.bend_allowance).
    if not segments_match_total(code, calculated, total, tolerance=row_tolerance):
        raise MasterDimensionError(
            f"{code}: MASTER формулата дава {calculated:g} cm, "
            f"а производствената дължина е {total:g} cm. "
            "Размерите не се нагласят автоматично."
        )
    return MasterShapeParameters(code, values, expanded, calculated)


def render_master_svg(
    shape_code: str,
    values: dict[str, float],
    *,
    include_safe_zone: bool = False,
) -> str:
    code = str(shape_code or "").strip().upper()
    values = {str(key).upper(): float(value) for key, value in values.items()}
    if code == "F006":
        body = _f006_svg(values, include_safe_zone=include_safe_zone)
    elif code == "F009":
        body = _f009_svg(values, include_safe_zone=include_safe_zone)
    else:
        body = _generic_svg(code, values, include_safe_zone=include_safe_zone)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="-12 -10 124 120" '
        'preserveAspectRatio="xMidYMid meet" role="img">'
        + body
        + "</svg>"
    )


def total_for(code: str, values: dict[str, float]) -> float:
    def n(key: str) -> float:
        return float(values.get(key, 0.0) or 0.0)

    code = str(code or "").upper()
    if code == "F001":
        return n("L1")
    if code == "F002":
        return n("L1") + math.hypot(n("DX2"), n("DY2")) + n("L3")
    if code == "F003":
        return n("L1") + n("L2") + n("L3")
    if code == "F004":
        return n("L1") + n("L2")
    if code == "F005":
        # L3 was implicit in the locked v0.3.1 renderer. Keep that input
        # compatible while making the v0.3.14 three-arm contract authoritative.
        return n("L1") + n("L2") + (
            n("L3") if "L3" in values else n("L1")
        )
    if code == "F006":
        return 2 * n("L1") + 2 * n("L2") + 2 * n("L3")
    if code == "F007":
        return 2 * n("L1") + n("L2")
    if code == "F008":
        return 2 * n("L1") + 2 * n("L2") + n("L3")
    if code == "F009":
        return sum(n(f"L{index}") for index in range(1, 6))
    if code == "F010":
        return sum(n(f"L{index}") for index in range(1, 6))
    if code in _CHAIN_FORMS:
        return sum(
            n(f"L{index}")
            for index in range(1, _CHAIN_SEGMENT_COUNTS[code] + 1)
        )
    return 0.0


def _generic_svg(code: str, values: dict[str, float], *, include_safe_zone: bool) -> str:
    lines: list[list[tuple[float, float]]] = []
    curves: list[dict[str, tuple[float, float]]] = []
    dims: list[tuple[str, tuple[float, float], tuple[float, float], float, str]] = []
    l1, l2, l3 = values.get("L1", 0.0), values.get("L2", 0.0), values.get("L3", 0.0)
    l4, l5 = values.get("L4", 0.0), values.get("L5", 0.0)
    dx, dy = values.get("DX2", 0.0), values.get("DY2", 0.0)
    rear_index: int | None = None
    if code == "F001":
        lines = [[(0, 0), (l1, 0)]]
        dims = [("h", (0, 0), (l1, 0), l1, "below")]
    elif code == "F002":
        # Literal port of geometry('F002') from the locked Engine HTML.
        # The reduction is dimension-dependent; only its tiny real DY2 may be
        # raised schematically by the exact 8% MASTER visibility rule.
        dy_vis = max(abs(dy), max(l1, l3, dx) * 0.08) * (-1.0 if dy < 0 else 1.0)
        p = [
            (0, 0),
            (l1, 0),
            (l1 + dx, dy_vis),
            (l1 + dx + l3, dy_vis),
        ]
        lines = [p]
        dy_text_shift = -10.0 if dy >= 0 else 10.0
        dims = [
            # The rebar polyline above is exact MASTER geometry. These three
            # optional values affect only dimension-line/text layout in the
            # compact Excel VID cell: offset, label dx and label dy.
            ("h", p[0], p[1], l1, "below", 16.0, 0.0, 0.0),
            ("h", p[1], p[2], dx, "above"),
            ("v", p[1], p[2], abs(dy), "left", 11.0, -2.0, dy_text_shift),
            ("h", p[2], p[3], l3, "below"),
        ]
    elif code == "F003":
        if l1 > l2 and l3 > l2:
            # Two dominant outer arms: canonical production view opens to the
            # right (the former F005 presentation).  Actual unequal arm
            # lengths remain visible; no dimensions are averaged or invented.
            bridge_floor = max(max(l1, l3) * 0.18, 1.0)
            visual_l2 = max(l2, bridge_floor)
            p = [(l1, visual_l2), (0, visual_l2), (0, 0), (l3, 0)]
            lines = [p]
            dims = [
                ("h", p[0], p[1], l1, "above"),
                ("v", p[1], p[2], l2, "left"),
                ("h", p[2], p[3], l3, "below"),
            ]
        else:
            # One dominant middle segment: the two shorter end arms point up.
            # A schematic floor keeps very short bends legible in the Excel
            # VID cell while the printed dimensions stay authoritative.
            hook_floor = max(l2 * 0.26, 1.0)
            visual_l1 = max(l1, hook_floor)
            visual_l3 = max(l3, hook_floor)
            p = [(0, visual_l1), (0, 0), (l2, 0), (l2, visual_l3)]
            lines = [p]
            dims = [
                ("v", p[0], p[1], l1, "left"),
                ("h", p[1], p[2], l2, "below"),
                ("v", p[2], p[3], l3, "right"),
            ]
    elif code == "F004":
        # Same schematic rule as F003: the hook is never allowed to collapse
        # into an almost invisible tick, while its printed value stays real.
        visual_l2 = max(l2, l1 * 0.26, 1.0)
        p = [(0, 0), (l1, 0), (l1, visual_l2)]
        lines = [p]
        dims = [("h", p[0], p[1], l1, "below"), ("v", p[1], p[2], l2, "right")]
    elif code == "F005":
        # F005 has three independent production dimensions in Engine v0.3.14.
        # A visual-only bridge floor keeps both real outer-arm labels legible;
        # no production dimension is averaged or rewritten.
        visual_l2 = max(l2, max(l1, l3) * 0.18, 1.0)
        p = [(l1, visual_l2), (0, visual_l2), (0, 0), (l3, 0)]
        lines = [p]
        dims = [
            ("h", p[0], p[1], l1, "above"),
            ("v", p[1], p[2], l2, "left"),
            ("h", p[2], p[3], l3, "below"),
        ]
    elif code == "F007":
        # F007 is a schematic C-profile. Extreme real proportions (for
        # example L1=50, L2=25) used to push the curved return through the
        # left arm and made the three dimension labels tiny and far apart in
        # the fixed Excel VID cell. Keep the printed values authoritative,
        # but apply the same minimum-visible-segment rule already used by the
        # other MASTER open shapes so the C silhouette always remains clear.
        visual_l1 = max(l1, l2 * 0.28, 1.0)
        visual_l2 = max(l2, visual_l1 * 1.25, 1.0)
        lines = [[(0, 0), (0, visual_l1), (visual_l2, visual_l1)]]
        return_end = (visual_l2 - 0.65 * visual_l1, 0.12 * visual_l1)
        curves = [
            {
                "p0": (visual_l2, visual_l1),
                "c1": (visual_l2 + 0.15 * visual_l1, 0.7 * visual_l1),
                "c2": (visual_l2 - 0.25 * visual_l1, 0.25 * visual_l1),
                "p1": return_end,
            }
        ]
        # L1 is used twice by the locked F007 identity (L1 + L2 + L1).
        # Show the repeated arm explicitly so the Excel picture reads
        # unambiguously as 10 / 20 / 10, not just 10 / 20.
        dims = [
            ("v", (0, 0), (0, visual_l1), l1, "left", 5.5),
            ("h", (0, visual_l1), (visual_l2, visual_l1), l2, "above", 5.5),
            ("v", (visual_l2, visual_l1), return_end, l1, "right", 5.5),
        ]
    elif code == "F008":
        a = l1 / math.sqrt(2.0)
        p = [(-a, -a), (0, 0), (0, l2), (l3, l2), (l3, 0), (l3 + a, a)]
        lines = [p]
        rear_index = 4
        dims = [
            ("h", p[2], p[3], l3, "above"),
            ("v", p[1], p[2], l2, "left"),
            ("h", p[4], p[5], l1, "below"),
        ]
    elif code == "F010":
        # Canonical F009/F010 production identity.  The form always opens to
        # the right, independent of DXF rotation, mirror or traversal.
        p = [
            (0, 0),
            (l1, 0),
            (l1, l2),
            (l1 - l3, l2),
            (l1 - l3, l2 - l4),
            (l1 - l3 + l5, l2 - l4),
        ]
        lines = [p]
        dims = [
            ("h", p[0], p[1], l1, "below"),
            ("v", p[1], p[2], l2, "right"),
            ("h", p[2], p[3], l3, "above"),
            ("v", p[3], p[4], l4, "left"),
            ("h", p[4], p[5], l5, "below"),
        ]
    elif code in _CHAIN_FORMS:
        # Една рисунка за всички верижни форми. Силуетът се строи от
        # таблицата, а размерът на всеки сегмент е РЕАЛНАТА му дължина, не
        # проекция — при F002 проекциите DX2/DY2 са самите производствени
        # параметри, тук не са.
        #
        # Рисунката е схематична по замисъл (``drawing_mode='СХЕМАТИЧНО'`` в
        # ENGINE): показва разпознаваемия силует, а числата до сегментите
        # носят производствената истина.
        count = _CHAIN_SEGMENT_COUNTS[code]
        chain = [values.get(f"L{index}", 0.0) for index in range(1, count + 1)]
        p = _chain_points(code, chain)
        lines = [p]
        dims = []
        for index, length in enumerate(chain):
            a, b = p[index], p[index + 1]
            вертикален = abs(b[1] - a[1]) > abs(b[0] - a[0])
            if вертикален:
                dims.append(("v", a, b, length, "right" if b[0] >= a[0] else "left"))
            else:
                dims.append(("h", a, b, length, "below" if b[1] <= a[1] else "above"))
    else:
        raise MasterDimensionError(f"MASTER renderer няма дефиниция за {code}.")

    points = [point for line in lines for point in line]
    for curve in curves:
        points.extend([curve["p0"], curve["c1"], curve["c2"], curve["p1"]])
    tx = _fit_geometry(points)
    svg = _defs(mode="f008" if code == "F008" else "f007" if code == "F007" else "generic")
    if include_safe_zone:
        svg += '<rect class="safe-zone" x="10" y="12" width="80" height="76" rx="1"/>'
    for line_index, line in enumerate(lines):
        if code == "F008" and line_index == 0 and rear_index is not None:
            front = " ".join(_point_text(tx(point)) for point in line[:5])
            rear = " ".join(_point_text(tx(point)) for point in line[4:])
            svg += f'<polyline class="rebar-line" points="{front}"/>'
            svg += f'<polyline class="rebar-line rear-leg" points="{rear}"/>'
            rp = tx(line[5])
            svg += f'<text class="rear-label" x="{rp[0] + 1:g}" y="{rp[1] - 2:g}">назад</text>'
        else:
            svg += '<polyline class="rebar-line" points="' + " ".join(_point_text(tx(point)) for point in line) + '"/>'
    for curve in curves:
        p0, c1, c2, p1 = (tx(curve[key]) for key in ("p0", "c1", "c2", "p1"))
        svg += (
            f'<path class="rebar-line" d="M {p0[0]:g} {p0[1]:g} '
            f'C {c1[0]:g} {c1[1]:g}, {c2[0]:g} {c2[1]:g}, {p1[0]:g} {p1[1]:g}"/>'
        )
    for dim in dims:
        svg += _dim_svg(dim, tx)
    return svg


def _f006_svg(values: dict[str, float], *, include_safe_zone: bool) -> str:
    # Literal Python port of f006SvgFromUploadedV023() from the supplied
    # OPEN_ENGINE_v0.3.1_MASTER_F009_LOCKED.html. Do not simplify this code:
    # its visual hook separation is the approved MASTER behaviour.
    l1, l2, l3 = values["L1"], values["L2"], values["L3"]
    canvas_h = 100.0
    safe = {"minX": 10.0, "maxX": 90.0, "minY": 15.0, "maxY": 85.0}
    width_safe = safe["maxX"] - safe["minX"]
    height_safe = safe["maxY"] - safe["minY"]
    min_visible = 12.0
    # All F006 VID drawings use the approved position-74 visual standard
    # (10 / 45 / 25) regardless of actual dimensions. The labels below still
    # display the real L1/L2/L3, with the larger rectangle side normalized as
    # L2 on top by parameters_from_segments().
    visual_hook, visual_width, visual_height = 10.0, 45.0, 25.0
    diag = visual_hook / math.sqrt(2.0)
    vectors = [
        (diag, diag),
        (-visual_width, 0),
        (0, -visual_height),
        (visual_width, 0),
        (0, visual_height),
        (-diag, -diag),
    ]
    physical = [(0.0, 0.0)]
    for vx, vy in vectors:
        px, py = physical[-1]
        physical.append((px + vx, py + vy))
    pb = _bounds(physical)
    span_x, span_y = pb[1] - pb[0], pb[3] - pb[2]
    scale = min(width_safe / span_x if span_x > 0 else math.inf, height_safe / span_y if span_y > 0 else math.inf)
    if not math.isfinite(scale):
        scale = 1.0
    display = [(0.0, 0.0)]
    display_vectors: list[tuple[float, float]] = []
    for vx, vy in vectors:
        dx, dy = vx * scale, vy * scale
        if abs(dy) > 0 and abs(dy) < min_visible:
            dy = math.copysign(min_visible, dy)
        display_vectors.append((dx, dy))
        px, py = display[-1]
        display.append((px + dx, py + dy))
    hb = _bounds(display)
    display_w, display_h = hb[1] - hb[0], hb[3] - hb[2]
    q = min(width_safe / display_w if display_w > width_safe else 1.0, height_safe / display_h if display_h > height_safe else 1.0)
    scaled = [(x * q, y * q) for x, y in display]
    sb = _bounds(scaled)
    off_x = (safe["minX"] + safe["maxX"]) / 2 - (sb[0] + sb[1]) / 2
    off_y = (safe["minY"] + safe["maxY"]) / 2 - (sb[2] + sb[3]) / 2
    points = [(x + off_x, y + off_y) for x, y in scaled]
    segs = [
        {
            "displayStart": points[index],
            "displayEnd": points[index + 1],
            "displayVector": (display_vectors[index][0] * q, display_vectors[index][1] * q),
        }
        for index in range(len(vectors))
    ]
    i_point = segs[1]["displayStart"]
    b_point = segs[1]["displayEnd"]
    d_point = segs[2]["displayEnd"]
    x_point = segs[3]["displayEnd"]
    left = min(b_point[0], d_point[0])
    right = max(i_point[0], x_point[0])
    bottom = min(d_point[1], x_point[1])
    top = max(b_point[1], i_point[1])
    width, height = right - left, top - bottom
    hook_dx = min(abs(segs[0]["displayVector"][0]), width * 0.25)
    hook_dy = min(abs(segs[0]["displayVector"][1]), height * 0.6)
    min_gap = 12.0
    shift_x = min(width * 0.14, hook_dx * 0.85)
    shift_y = min(height * 0.34, hook_dy * 1.2)
    hyp = math.hypot(hook_dx, hook_dy)

    def gap_now() -> float:
        return (hook_dy * shift_x + hook_dx * shift_y) / hyp if hyp else 0.0

    if gap_now() < min_gap and hook_dx:
        needed = (min_gap * hyp - hook_dy * shift_x) / hook_dx
        shift_y = min(height * 0.78, max(shift_y, needed))
    if gap_now() < min_gap and hook_dy:
        needed = (min_gap * hyp - hook_dx * shift_y) / hook_dy
        shift_x = min(width * 0.3, max(shift_x, needed))
    hook1_end = (right - shift_x, top)
    hook1_start = (hook1_end[0] - hook_dx, hook1_end[1] - hook_dy)
    hook2_start = (right, top - shift_y)
    hook2_end = (hook2_start[0] - hook_dx, hook2_start[1] - hook_dy)

    def sy(point: tuple[float, float]) -> tuple[float, float]:
        return point[0], canvas_h - point[1]

    h1s, h1e, h2s, h2e = sy(hook1_start), sy(hook1_end), sy(hook2_start), sy(hook2_end)
    rect_y = canvas_h - top
    rect_top, rect_bottom = rect_y, rect_y + height
    # Keep the labels close to the actual stirrup. The former 7-unit gap was
    # inherited from the large Engine canvas and looked detached after the
    # image was reduced to an Excel row.
    dim_top = rect_top - 5.5
    dim_left = left - 5.5
    hook_y1, hook_y2 = canvas_h - hook1_start[1], canvas_h - hook1_end[1]
    # The hook value is displayed inside the stirrup, centred in the clear
    # upper-right area. External hook dimension lines made the small Excel
    # image visually crowded and were prone to cropping.
    # Place the hook value in the clear interior, not on top of either
    # diagonal hook line.  This remains tied to the fixed F006 profile while
    # the printed value is the real reviewed L1.
    hook_label_x = right - width * 0.4
    hook_label_y = rect_top + height * 0.42
    svg = _defs(mode="f006")
    if include_safe_zone:
        svg += f'<rect class="safe-zone" x="{safe["minX"]:g}" y="{canvas_h-safe["maxY"]:g}" width="{width_safe:g}" height="{height_safe:g}" rx="1.5"/>'
    radius = min(width, height) * 0.055
    svg += f'<rect class="rebar-line" x="{left:g}" y="{rect_y:g}" width="{width:g}" height="{height:g}" rx="{radius:g}" ry="{radius:g}"/>'
    svg += f'<line class="rebar-line" x1="{h1e[0]:g}" y1="{h1e[1]:g}" x2="{h1s[0]:g}" y2="{h1s[1]:g}"/>'
    svg += f'<line class="rebar-line" x1="{h2s[0]:g}" y1="{h2s[1]:g}" x2="{h2e[0]:g}" y2="{h2e[1]:g}"/>'
    svg += _dimension_group_horizontal(left, right, rect_top, dim_top, l2)
    svg += _dimension_group_vertical(dim_left, rect_top, rect_bottom, left, l3, text_side="left")
    svg += (
        f'<text class="dimension-text hook-inside-label" x="{hook_label_x:g}" y="{hook_label_y:g}" '
        f'text-anchor="middle" dominant-baseline="central">{_fmt(l1)} cm</text>'
    )
    return svg


def _f009_svg(values: dict[str, float], *, include_safe_zone: bool) -> str:
    lengths = [values[f"L{index}"] for index in range(1, 6)]
    points = [(0.0, 0.0)]
    heading = -90.0
    for index, length in enumerate(lengths):
        radians = math.radians(heading)
        start = points[-1]
        points.append((start[0] + length * math.cos(radians), start[1] + length * math.sin(radians)))
        if index < len(lengths) - 1:
            heading -= 90.0
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    span = max(max(xs) - min(xs), max(ys) - min(ys), 1.0)
    shift = min(4.0, max(1.5, span * 0.04))
    points[4] = (points[4][0] + shift, points[4][1])
    points[5] = (points[5][0] + shift, points[5][1])
    min_x, max_x, min_y, max_y = _bounds(points)
    width, height = max(max_x - min_x, 1.0), max(max_y - min_y, 1.0)
    scale = min(58 / width, 46 / height)
    center_x, center_y = (min_x + max_x) / 2, (min_y + max_y) / 2

    def tx(point: tuple[float, float]) -> tuple[float, float]:
        return 50 + (point[0] - center_x) * scale, 50 - (point[1] - center_y) * scale

    screen = [tx(point) for point in points]
    center = (sum(p[0] for p in screen) / len(screen), sum(p[1] for p in screen) / len(screen))
    positions = ["BOTTOM", "RIGHT", "LEFT", "LEFT", "RIGHT"]
    offsets = [6, 6, 6, 6, 8]
    svg = _defs(mode="f009")
    if include_safe_zone:
        svg += '<rect class="safe-zone" x="10" y="12" width="80" height="76" rx="1"/>'
    svg += '<polyline class="f009-rebar" points="' + " ".join(_point_text(p) for p in screen) + '"/>'
    for index, value in enumerate(lengths):
        svg += _f009_dimension(screen[index], screen[index + 1], value, positions[index], offsets[index], index, center)
    return svg


def _f009_dimension(start, end, value, position, offset, index, center) -> str:
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dy)
    normal = (-dy / length, dx / length)
    mid = ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)
    if position == "TOP":
        sign = 1 if normal[1] <= 0 else -1
    elif position == "BOTTOM":
        sign = 1 if normal[1] >= 0 else -1
    elif position == "LEFT":
        sign = 1 if normal[0] <= 0 else -1
    elif position == "RIGHT":
        sign = 1 if normal[0] >= 0 else -1
    else:
        outward = (mid[0] - center[0]) * normal[0] + (mid[1] - center[1]) * normal[1]
        sign = (-1 if index % 2 == 0 else 1) if abs(outward) < 0.2 else (1 if outward >= 0 else -1)
    nx, ny = normal[0] * sign, normal[1] * sign
    a = (start[0] + nx * offset, start[1] + ny * offset)
    b = (end[0] + nx * offset, end[1] + ny * offset)
    text = (mid[0] + nx * (offset + 2.2), mid[1] + ny * (offset + 2.2))
    rotation = math.degrees(math.atan2(dy, dx))
    if rotation > 90:
        rotation -= 180
    if rotation < -90:
        rotation += 180
    return (
        f'<g><line class="f009-extension" x1="{start[0]:g}" y1="{start[1]:g}" x2="{a[0]:g}" y2="{a[1]:g}"/>'
        f'<line class="f009-extension" x1="{end[0]:g}" y1="{end[1]:g}" x2="{b[0]:g}" y2="{b[1]:g}"/>'
        f'<line class="f009-dimension" x1="{a[0]:g}" y1="{a[1]:g}" x2="{b[0]:g}" y2="{b[1]:g}"/>'
        f'<text class="f009-text" x="{text[0]:g}" y="{text[1]:g}" text-anchor="middle" dominant-baseline="central" '
        f'transform="rotate({rotation:g} {text[0]:g} {text[1]:g})">{_fmt(value)} cm</text></g>'
    )


def _fit_geometry(points: Sequence[tuple[float, float]]):
    min_x, max_x, min_y, max_y = _bounds(points)
    width, height = max(max_x - min_x, 1.0), max(max_y - min_y, 1.0)
    scale = min(66 / width, 58 / height)
    center_x, center_y = (min_x + max_x) / 2, (min_y + max_y) / 2
    return lambda point: (50 + (point[0] - center_x) * scale, 50 - (point[1] - center_y) * scale)


def _dim_svg(dim, tx) -> str:
    kind, a0, b0, value, position, *layout = dim
    a, b = tx(a0), tx(b0)
    x1, y1, x2, y2 = a[0], a[1], b[0], b[1]
    offset = float(layout[0]) if layout else 7.0
    label_dx = float(layout[1]) if len(layout) >= 2 else 0.0
    label_dy = float(layout[2]) if len(layout) >= 3 else 0.0
    if kind == "h":
        base = min(y1, y2) - offset if position == "above" else max(y1, y2) + offset
        label_x = (x1 + x2) / 2 + label_dx
        label_y = base - 2 + label_dy
        return (
            f'<line class="extension" x1="{x1:g}" y1="{y1:g}" x2="{x1:g}" y2="{base:g}"/>'
            f'<line class="extension" x1="{x2:g}" y1="{y2:g}" x2="{x2:g}" y2="{base:g}"/>'
            f'<line class="dimension-line" x1="{x1:g}" y1="{base:g}" x2="{x2:g}" y2="{base:g}"/>'
            f'<text class="dimension-text" x="{label_x:g}" y="{label_y:g}" text-anchor="middle" dominant-baseline="central">{_fmt(value)} cm</text>'
        )
    base = min(x1, x2) - offset if position == "left" else max(x1, x2) + offset
    lx = (base - 2 if position == "left" else base + 2) + label_dx
    ly = (y1 + y2) / 2 + label_dy
    return (
        f'<line class="extension" x1="{x1:g}" y1="{y1:g}" x2="{base:g}" y2="{y1:g}"/>'
        f'<line class="extension" x1="{x2:g}" y1="{y2:g}" x2="{base:g}" y2="{y2:g}"/>'
        f'<line class="dimension-line" x1="{base:g}" y1="{y1:g}" x2="{base:g}" y2="{y2:g}"/>'
        f'<text class="dimension-text" x="{lx:g}" y="{ly:g}" text-anchor="middle" dominant-baseline="central" transform="rotate(-90 {lx:g} {ly:g})">{_fmt(value)} cm</text>'
    )


def _defs(*, mode: str = "generic") -> str:
    common_style = (
        '<style>.rebar-line{fill:none;stroke:#172033;stroke-width:1.2;stroke-linecap:round;stroke-linejoin:round}'
        '.rear-leg{stroke:#172033}.dimension-line{stroke:#2f6698;stroke-width:.62;marker-start:url(#dimArrowStart);marker-end:url(#dimArrowEnd)}'
        '.extension{stroke:#7f96ad;stroke-width:.42;stroke-dasharray:1.2 1}.dimension-text{fill:#17365d;font-family:Arial,sans-serif;font-size:4.6px;font-weight:800}'
        '.rear-label{fill:#667085;font-size:2.5px;font-weight:700}.safe-zone{fill:none;stroke:#cbd5e1;stroke-width:.45;stroke-dasharray:2 2}</style>'
    )
    if mode == "f009":
        return (
            '<defs><style>.f009-rebar{fill:none;stroke:#172033;stroke-width:1.15;stroke-linecap:round;stroke-linejoin:round}'
            '.f009-dimension{stroke:#2f6698;stroke-width:.58;marker-start:url(#f009ArrowStart);marker-end:url(#f009ArrowEnd)}'
            '.f009-extension{stroke:#7f96ad;stroke-width:.4;stroke-dasharray:1 1}.f009-text{fill:#17365d;font-family:Arial,sans-serif;font-size:4.3px;font-weight:800}'
            '.safe-zone{fill:none;stroke:#cbd5e1;stroke-width:.45;stroke-dasharray:2 2}</style>'
            '<marker id="f009ArrowStart" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="2.5" markerHeight="2.5" orient="auto-start-reverse"><path d="M 9 1 L 1 5 L 9 9" fill="none" stroke="#2f6698" stroke-width="1.4"/></marker>'
            '<marker id="f009ArrowEnd" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="2.5" markerHeight="2.5" orient="auto"><path d="M 1 1 L 9 5 L 1 9" fill="none" stroke="#2f6698" stroke-width="1.4"/></marker></defs>'
        )
    if mode == "f008":
        chair_style = (
            '<style>.rebar-line{fill:none;stroke:#172033;stroke-width:1.2;stroke-linecap:round;stroke-linejoin:round}'
            '.rear-leg{stroke:#172033}.dimension-line{stroke:#2f6698;stroke-width:.62;marker-start:url(#dimArrowStart);marker-end:url(#dimArrowEnd)}'
            '.extension{stroke:#7f96ad;stroke-width:.42;stroke-dasharray:1.2 1}.dimension-text{fill:#17365d;font-family:Arial,sans-serif;font-size:5.6px;font-weight:800}'
            '.rear-label{fill:#4b5565;font-family:Arial,sans-serif;font-size:3.5px;font-weight:800}.safe-zone{fill:none;stroke:#cbd5e1;stroke-width:.45;stroke-dasharray:2 2}</style>'
        )
        return (
            '<defs>' + chair_style
            + '<marker id="dimArrowStart" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="3" markerHeight="3" orient="auto-start-reverse"><path d="M 9 1 L 1 5 L 9 9" fill="none" stroke="#2f6698" stroke-width="1.5"/></marker>'
            + '<marker id="dimArrowEnd" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="3" markerHeight="3" orient="auto"><path d="M 1 1 L 9 5 L 1 9" fill="none" stroke="#2f6698" stroke-width="1.5"/></marker></defs>'
        )
    if mode == "f006":
        # Literal marker definitions embedded by f006SvgFromUploadedV023().
        stirrup_style = (
            '<style>.rebar-line{fill:none;stroke:#172033;stroke-width:1.2;stroke-linecap:round;stroke-linejoin:round}'
            '.rear-leg{stroke:#172033}.dimension-line{stroke:#2f6698;stroke-width:.62;marker-start:url(#dimArrowStart);marker-end:url(#dimArrowEnd)}'
            '.extension{stroke:#7f96ad;stroke-width:.42;stroke-dasharray:1.2 1}.dimension-text{fill:#17365d;font-family:Arial,sans-serif;font-size:6px;font-weight:800}'
            '.rear-label{fill:#667085;font-size:2.5px;font-weight:700}.safe-zone{fill:none;stroke:#cbd5e1;stroke-width:.45;stroke-dasharray:2 2}</style>'
        )
        return (
            '<defs>' + stirrup_style
            + '<marker id="dimArrowStart" markerWidth="4" markerHeight="4" refX="1.5" refY="2" orient="auto"><path d="M4,0 L0,2 L4,4" fill="none" stroke="currentColor" stroke-width="0.8"/></marker>'
            + '<marker id="dimArrowEnd" markerWidth="4" markerHeight="4" refX="2.5" refY="2" orient="auto"><path d="M0,0 L4,2 L0,4" fill="none" stroke="currentColor" stroke-width="0.8"/></marker></defs>'
        )
    if mode == "f007":
        c_shape_style = (
            '<style>.rebar-line{fill:none;stroke:#172033;stroke-width:1.2;stroke-linecap:round;stroke-linejoin:round}'
            '.rear-leg{stroke:#172033}.dimension-line{stroke:#2f6698;stroke-width:.62;marker-start:url(#dimArrowStart);marker-end:url(#dimArrowEnd)}'
            '.extension{stroke:#7f96ad;stroke-width:.42;stroke-dasharray:1.2 1}.dimension-text{fill:#17365d;font-family:Arial,sans-serif;font-size:6px;font-weight:800}'
            '.rear-label{fill:#667085;font-size:2.5px;font-weight:700}.safe-zone{fill:none;stroke:#cbd5e1;stroke-width:.45;stroke-dasharray:2 2}</style>'
        )
        return (
            '<defs>' + c_shape_style
            + '<marker id="dimArrowStart" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="3" markerHeight="3" orient="auto-start-reverse"><path d="M 9 1 L 1 5 L 9 9" fill="none" stroke="#2f6698" stroke-width="1.5"/></marker>'
            + '<marker id="dimArrowEnd" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="3" markerHeight="3" orient="auto"><path d="M 1 1 L 9 5 L 1 9" fill="none" stroke="#2f6698" stroke-width="1.5"/></marker></defs>'
        )
    # The generic draw() function in OPEN_ENGINE v0.3.1 uses these markers.
    return (
        '<defs>' + common_style
        + '<marker id="dimArrowStart" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="3" markerHeight="3" orient="auto-start-reverse"><path d="M 9 1 L 1 5 L 9 9" fill="none" stroke="#2f6698" stroke-width="1.5"/></marker>'
        + '<marker id="dimArrowEnd" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="3" markerHeight="3" orient="auto"><path d="M 1 1 L 9 5 L 1 9" fill="none" stroke="#2f6698" stroke-width="1.5"/></marker></defs>'
    )

def _dimension_group_horizontal(left: float, right: float, rect_top: float, dim_top: float, value: float) -> str:
    return (
        f'<g class="dimension"><line class="extension" x1="{left:g}" y1="{rect_top:g}" x2="{left:g}" y2="{dim_top:g}"/>'
        f'<line class="extension" x1="{right:g}" y1="{rect_top:g}" x2="{right:g}" y2="{dim_top:g}"/>'
        f'<line class="dimension-line" x1="{left:g}" y1="{dim_top:g}" x2="{right:g}" y2="{dim_top:g}"/>'
        f'<text class="dimension-text" x="{(left+right)/2:g}" y="{dim_top-2:g}" text-anchor="middle" dominant-baseline="central">{_fmt(value)} cm</text></g>'
    )


def _dimension_group_vertical(x: float, top: float, bottom: float, rect_x: float, value: float, *, text_side: str) -> str:
    text_x = x - 3.5 if text_side == "left" else x + 3.5
    return (
        f'<g class="dimension"><line class="extension" x1="{rect_x:g}" y1="{top:g}" x2="{x:g}" y2="{top:g}"/>'
        f'<line class="extension" x1="{rect_x:g}" y1="{bottom:g}" x2="{x:g}" y2="{bottom:g}"/>'
        f'<line class="dimension-line" x1="{x:g}" y1="{top:g}" x2="{x:g}" y2="{bottom:g}"/>'
        f'<text class="dimension-text" x="{text_x:g}" y="{(top+bottom)/2:g}" text-anchor="middle" dominant-baseline="central" '
        f'transform="rotate(-90 {text_x:g} {(top+bottom)/2:g})">{_fmt(value)} cm</text></g>'
    )


def _bounds(points: Sequence[tuple[float, float]]) -> tuple[float, float, float, float]:
    xs, ys = [point[0] for point in points], [point[1] for point in points]
    # Same order as the JavaScript MASTER helper: minX, maxX, minY, maxY.
    return min(xs), max(xs), min(ys), max(ys)


def _point_text(point: tuple[float, float]) -> str:
    return f"{point[0]:g},{point[1]:g}"


def _fmt(value: float) -> str:
    value = float(value)
    return str(int(round(value))) if abs(value - round(value)) < 1e-8 else f"{value:.2f}".rstrip("0").rstrip(".")


def _parse_dimensions(text: str) -> dict[str, float]:
    values: dict[str, float] = {}
    for item in str(text or "").split(";"):
        match = re.match(r"^\s*([A-Z]+\d*)\s*=\s*(-?\d+(?:[.,]\d+)?)\s*$", item, flags=re.IGNORECASE)
        if match:
            values[match.group(1).upper()] = float(match.group(2).replace(",", "."))
    return values


def _require_equal(label: str, left: float, right: float) -> None:
    if abs(float(left) - float(right)) > MASTER_TOTAL_TOLERANCE_CM:
        raise MasterDimensionError(f"{label} трябва да са еднакви: {left:g} срещу {right:g} cm.")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
