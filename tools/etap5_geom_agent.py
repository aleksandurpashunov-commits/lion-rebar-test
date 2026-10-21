"""Измерване на разделителя ``rebar_geometry_evidence`` върху 11 проекта.

Еднократен скрипт за етап 5, не е обща команда. Чете САМО
``working/extracted_entities.json`` на копията в ``C:\\lr_exp\\proekti`` и пише
един файл: ``C:\\lr_exp\\rezultat\\etap_5\\GEOM_AGENT.json``.

Пускане (от ``release/``):

    PYTHONIOENCODING=utf-8 LION_REBAR_DATA_ROOT=C:\\lr_exp\\data \\
        .venv/Scripts/python.exe tools/etap5_geom_agent.py

Еталоните долу са ръчно потвърдени от собственика и съществуват само тук — в
самия разделител няма нито едно име на слой, файл или handle.
"""
from __future__ import annotations

import json
import math
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lion_rebar.dxf.rebar_geometry_evidence import (  # noqa: E402
    CODED_DIAMETER_MIN_MATCHES,
    MAX_CLAIMED_BOUND_SHARE,
    MIN_CLAIMED_FOR_REFUSAL,
    MIN_CONTROL_RATIO,
    MIN_LENGTH_MATCHES,
    VERDICT_NOT_REBAR,
    VERDICT_REBAR,
    VERDICT_UNKNOWN,
    evaluate_rebar_geometry,
)

PROJECT_ROOT = Path(r"C:\lr_exp\proekti")
OUTPUT = Path(r"C:\lr_exp\rezultat\etap_5\GEOM_AGENT.json")
PROJECTS = [
    "d-1", "d-2", "s-7", "s-8", "s-9", "s-10", "s-11",
    "Тест-002", "Тест-003", "тест-004", "тест-006",
]

#: Еталони: армировъчни слоеве, потвърдени от собственика.
REFERENCE_REBAR = {
    ("d-2", "AutoCAD_Structural_Detailing_Surface reinforcement - bars - bottom"),
    ("d-2", "AutoCAD_Structural_Detailing_Surface reinforcement - bars - top"),
    ("s-8", "Arm - N10"), ("s-8", "Arm - N12"), ("s-8", "Arm - N14"),
    ("s-9", "Arm - N10"), ("s-9", "Arm - N12"), ("s-9", "Arm - N14"),
    ("s-9", "ARM_Bars"), ("тест-006", "ARM_Bars"),
    ("s-11", "Stremena"), ("тест-004", "Stremena"),
    ("тест-006", "Arm - N16"),
    ("Тест-003", "ARM.1"), ("Тест-003", "ARM.MR.2"),
}

#: Еталони: слоеве, които НЕ са армировка.
REFERENCE_NOT_REBAR = {
    ("d-2", "AutoCAD_Structural_Detailing_Slab contour"),
    ("d-2", "found_slab"), ("d-2", "found&slab"), ("d-2", "col_wall"),
    ("d-2", "axes"), ("d-2", "symbol"),
    ("d-2", "hatch"), ("d-2", "hatch&muster"), ("d-2", "hatch_muster"),
    ("Тест-003", "TEKST NA ZABELEJKI"), ("Тест-003", "TEKST NA SHAIBITE"),
    ("Тест-003", "TEKST NA KOLONI"),
}


# --------------------------------------------------------------------------- #
# отхвърлените признаци — мерят се тук, за да е видно ЗАЩО са отхвърлени


def _point(value):
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            return (float(value[0]), float(value[1]))
        except (TypeError, ValueError):
            return None
    return None


def _points(entity):
    geometry = entity.get("geometry") or {}
    for key in ("points", "vertices", "polyline_points"):
        raw = geometry.get(key)
        if isinstance(raw, list) and len(raw) >= 2:
            out = [p for p in (_point(v) for v in raw) if p is not None]
            if len(out) >= 2:
                return out
    start, end = _point(geometry.get("start")), _point(geometry.get("end"))
    if start is not None and end is not None:
        return [start, end]
    return []


def _direction(points):
    dx = points[-1][0] - points[0][0]
    dy = points[-1][1] - points[0][1]
    norm = math.hypot(dx, dy)
    return (dx / norm, dy / norm) if norm > 1e-9 else (0.0, 0.0)


def rejected_signals(entities, layer):
    """Стойностите на отхвърлените признаци за един слой."""

    types = Counter()
    mine = []
    for entity in entities:
        if str(entity.get("layer") or "") != layer:
            continue
        types[str(entity.get("entity_type") or "")] += 1
        points = _points(entity)
        if len(points) >= 2:
            mine.append(points)
    total = sum(types.values()) or 1

    # успоредни семейства: поне два съседа с почти същата посока и дължина
    family = 0
    if len(mine) >= 3:
        cell = 100.0
        grid = defaultdict(list)
        for index, points in enumerate(mine):
            mx = sum(p[0] for p in points) / len(points)
            my = sum(p[1] for p in points) / len(points)
            grid[(int(mx // cell), int(my // cell))].append(index)
        lengths = [
            sum(math.dist(p[i], p[i + 1]) for i in range(len(p) - 1)) for p in mine
        ]
        for index, points in enumerate(mine):
            mx = sum(p[0] for p in points) / len(points)
            my = sum(p[1] for p in points) / len(points)
            cx, cy = int(mx // cell), int(my // cell)
            near = []
            for dx in (-2, -1, 0, 1, 2):
                for dy in (-2, -1, 0, 1, 2):
                    near.extend(grid.get((cx + dx, cy + dy), ()))
            di = _direction(points)
            found = 0
            for other in near:
                if other == index:
                    continue
                dj = _direction(mine[other])
                if abs(di[0] * dj[0] + di[1] * dj[1]) < 0.985:
                    continue
                if abs(lengths[other] - lengths[index]) > 0.03 * max(1.0, lengths[index]):
                    continue
                found += 1
                if found >= 2:
                    break
            if found >= 2:
                family += 1

    # отношение обиколка/диагонал на обхващащия правоъгълник
    ratios = []
    for points in mine:
        length = sum(math.dist(points[i], points[i + 1]) for i in range(len(points) - 1))
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        diagonal = math.hypot(max(xs) - min(xs), max(ys) - min(ys))
        if diagonal > 1e-9:
            ratios.append(length / diagonal)
    ratios.sort()

    return {
        "entity_type_mix": dict(types),
        "text_entity_share": round(
            (types.get("TEXT", 0) + types.get("MTEXT", 0)) / total, 3),
        "dimension_entity_share": round(types.get("DIMENSION", 0) / total, 3),
        "hatch_entity_share": round(types.get("HATCH", 0) / total, 3),
        "parallel_family_share": round(family / max(1, len(mine)), 3),
        "perimeter_over_bbox_diagonal_median": (
            round(ratios[len(ratios) // 2], 3) if ratios else None),
    }


# --------------------------------------------------------------------------- #


def load(project):
    path = PROJECT_ROOT / project / "working" / "extracted_entities.json"
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)["entities"]


def reference_of(project, layer):
    if (project, layer) in REFERENCE_REBAR:
        return True
    if (project, layer) in REFERENCE_NOT_REBAR:
        return False
    return None


def main():
    per_layer = []
    matrix = defaultdict(int)
    timing = {}
    for project in PROJECTS:
        entities = load(project)
        started = time.perf_counter()
        evidence = evaluate_rebar_geometry(entities)
        timing[project] = {
            "seconds": round(time.perf_counter() - started, 3),
            "entities": len(entities),
            "linear_objects": evidence.linear_objects,
            "annotations_with_length": evidence.annotations_with_length,
        }
        seen = set()
        for layer, item in sorted(evidence.layers.items()):
            seen.add(layer)
            reference = reference_of(project, layer)
            per_layer.append({
                "project": project,
                "layer": layer,
                "verdict": item.verdict,
                "score": round(item.score, 3),
                "signals": dict(item.signals),
                "rejected_signals": rejected_signals(entities, layer),
                "is_rebar_reference": reference,
                "why": item.why,
                "sample_handles": list(item.sample_handles),
            })
            key = {True: "reference_rebar", False: "reference_not_rebar",
                   None: "unlabelled"}[reference]
            matrix[f"{key}__{item.verdict}"] += 1
        # еталоните без нито едно съвпадение по дължина също влизат в отчета:
        # правилото им отказва по липса на доказателство, което е вярно
        for reference_set, flag in ((REFERENCE_REBAR, True),
                                    (REFERENCE_NOT_REBAR, False)):
            for proj, layer in sorted(reference_set):
                if proj != project or layer in seen:
                    continue
                per_layer.append({
                    "project": project,
                    "layer": layer,
                    "verdict": VERDICT_UNKNOWN,
                    "score": 0.0,
                    "signals": {"length_matches": 0},
                    "rejected_signals": rejected_signals(entities, layer),
                    "is_rebar_reference": flag,
                    "why": "нито един надпис не намира своята дължина в този слой",
                    "sample_handles": [],
                })
                key = "reference_rebar" if flag else "reference_not_rebar"
                matrix[f"{key}__{VERDICT_UNKNOWN}"] += 1

    wrong_accept = matrix.get(f"reference_not_rebar__{VERDICT_REBAR}", 0)
    misses = (matrix.get(f"reference_rebar__{VERDICT_UNKNOWN}", 0)
              + matrix.get(f"reference_rebar__{VERDICT_NOT_REBAR}", 0))
    report = {
        "schema": "lion-rebar-etap5-geom-agent/v1",
        "generated_for": "етап 5 — разделител армировка / контур",
        "module": "lion_rebar/dxf/rebar_geometry_evidence.py",
        "entry_point": (
            "evaluate_rebar_geometry(records, *, policy=None, "
            "search_radius_raw=None, endpoint_tolerance_raw=None)"
        ),
        "projects": PROJECTS,
        "method": (
            "Съвместно доказателство на два независими признака. (1) Надписна "
            "следа: както в convention_profile._propose_rebar_geometry_layers — "
            "надпис с прочетена дължина намира в радиуса на политиката обект от "
            "слоя с точно тази дължина, с контрола при дължина + 13 cm. (2) "
            "Структурна следа върху ТОЧНО ТЕЗИ заявени обекти: армировъчният "
            "прът е отворен път с поне един свободен край, а контурът е "
            "затворена верига — всяка отсечка среща следващата с двата си края. "
            "Краищата се слепват с endpoint_tolerance_raw на политиката (0,75), "
            "тоест без нов праг. Приема се само когато и двата признака са "
            "налице; при силна (1) и паднала (2) присъдата е not_rebar (случаят "
            "Slab contour); при тънка (1) — unknown."
        ),
        "signals_kept": [
            {"signal": "length_matches",
             "why": "необходимата надписна следа; праг 5 — непроменен от "
                    "измереното правило в convention_profile"},
            {"signal": "control_matches / control_ratio",
             "why": "контролата при дължина + 13 cm; праг 4x — непроменен"},
            {"signal": "claimed_bound_share",
             "why": "РАЗДЕЛИТЕЛЯТ. Дял на заявените обекти, свързани с двата си "
                    "края или затворени. Измерено: 0,00 при всички еталонни "
                    "армировъчни слоеве (най-лошо 0,01 в Тест-002 s-Reinf), "
                    "1,00 при d-2 Slab contour (148 от 148) и при Тест-003 "
                    "SHAIBI. Нечувствителен към допуска за слепване: същото "
                    "разделяне при 0,05 / 0,25 / 0,75 / 2,0 / 5,0 сурови "
                    "единици и при относителен допуск 0,0002–0,05 от медианната "
                    "дължина — 250x обхват без промяна."},
            {"signal": "claimed_self_closed_share",
             "why": "затвореният сам по себе си обект не е прът; влиза в "
                    "claimed_bound_share и се отчита отделно за оператора"},
            {"signal": "claimed_segment_count_max",
             "why": "закон на програмата: max_component_segments = 16 за една "
                    "форма. Измерено: стремената дават 11 отсечки (s-11, "
                    "тест-004), заявените обекти на Slab contour — 1. Не "
                    "разделя този случай, но пази срещу дълга отворена "
                    "полилиния, начертала целия контур с един обект."},
            {"signal": "claimed_refused_share",
             "why": "портата. Дял на заявените обекти, отказани по СВОИТЕ "
                    "признаци (затворен, свързан с двата края, или над 16 "
                    "отсечки). Присъдата за слоя е сбор от присъдите за "
                    "обектите му — слой не става армировъчен, докато обектите "
                    "му са отказани един по един. На този корпус е равен на "
                    "claimed_bound_share, защото никой заявен обект не "
                    "надхвърля 16 отсечки."},
            {"signal": "coded_diameter_mm + coded_diameter_agreements/clashes",
             "why": "второ, независимо кодиране. Позволява приемане при 3 "
                    "съвпадения, когато името кодира Ø, надписите го "
                    "потвърждават и контролата е 0. Печели тест-006 Arm - N16; "
                    "в целия корпус слоевете с кодиран Ø са само Arm - N10/12/"
                    "14/16 — всичките армировъчни, тоест нула риск."},
            {"signal": "layer_bound_share",
             "why": "същият признак, но върху целия слой. Мери се и се показва, "
                    "НО не е порта: истински армировъчен слой дава 0,32 (s-9 и "
                    "тест-006 ARM_Bars — пръти, които се допират), а най-ниският "
                    "контурен е 0,71 (RZR) — запасът е само 2x, докато при "
                    "заявените обекти е 0,00 срещу 1,00."},
        ],
        "signals_rejected_and_why": [
            {"signal": "distinct_claimants_max (колко РАЗЛИЧНИ надписа заявяват "
                       "един и същ обект)",
             "why": "хипотезата не се потвърждава. Измерено: Slab contour 4, но "
                    "d-2 bars - top също 4 и s-8 Arm - N12 — 2. Освен това "
                    "заявените обекти на контура са 148 при 74 надписа (2,0 "
                    "обекта на надпис), а истинският Arm - N12 дава 276 при 86 "
                    "(3,2) — по-двусмислен от контура. Признакът не разделя."},
            {"signal": "closedness / self-intersection / bounding-box aspect на "
                       "отделния обект",
             "why": "контурът на плочата е начертан с 692 ПРАВИ отсечки: "
                    "заявените обекти са единични LINE с 1 отсечка, отношение "
                    "обиколка/диагонал 1,0 — точно както правият прът. "
                    "Затвореността е свойство на веригата, не на обекта, и "
                    "точно затова се мери свързаността на краищата."},
            {"signal": "parallel_family_share (пръти вървят на семейства)",
             "why": "обърнат на еталоните. Измерено: Slab contour 0,54, а "
                    "истинските s-8 Arm - N14 0,03, Тест-003 ARM.USIL. 0,01, "
                    "s-11 Stremena 0,00. Признакът наказва точно верните слоеве."},
            {"signal": "dimension / text entities на слоя",
             "why": "постоянен върху отчетените слоеве: НИТО ЕДИН слой с поне "
                    "едно съвпадение по дължина не носи DIMENSION или TEXT в "
                    "11-те проекта. Нула информация — не се ползва като порта, "
                    "за да няма непроверен праг."},
            {"signal": "HATCH на слоя",
             "why": "би дал погрешен ОТКАЗ: Тест-003 ARM.1 е еталонна "
                    "армировка и носи 23 HATCH; ARM.2 — 11. Забраната „всеки "
                    "hatch* е шум“ е за ИМЕТО на слоя, не за обектите."},
            {"signal": "обща дължина спрямо разпределението на надписаните "
                       "дължини (in-range share)",
             "why": "припокрива се. Измерено: Slab contour 0,71 срещу bars "
                    "0,85–0,93, но ARM_Bars (истински) е 0,43 — под контура. "
                    "Няма разделяща стойност."},
            {"signal": "споделяне между рамки/зони",
             "why": "не е измерено и не е вкарано: иска frame_detection, тоест "
                    "нова зависимост и ново поведение при рамки без граници. "
                    "Свързаността на краищата разделя случая напълно и без "
                    "него; вкарването му би добавило риск без печалба."},
            {"signal": "тип на обектите (LINE/LWPOLYLINE/ARC/CIRCLE/HATCH/TEXT)",
             "why": "не разделя: и контурът, и повърхностната армировка в d-2 "
                    "са 100% LINE. Типът влиза само структурно — слой без "
                    "линейни обекти няма как да получи съвпадение по дължина."},
        ],
        "thresholds_and_how_measured": {
            "MIN_LENGTH_MATCHES": {
                "value": MIN_LENGTH_MATCHES,
                "how": "непроменен от convention_profile (истинските слоеве "
                       "4–170 съвпадения, шумът 1–2)",
            },
            "MIN_CONTROL_RATIO": {
                "value": MIN_CONTROL_RATIO,
                "how": "непроменен от convention_profile",
            },
            "MAX_CLAIMED_BOUND_SHARE": {
                "value": MAX_CLAIMED_BOUND_SHARE,
                "how": "измерено на 11 проекта: 0,00 при 15 еталонни "
                       "армировъчни слоя (най-лошо 0,01), 1,00 при Slab contour "
                       "и SHAIBI. Прагът е сложен близо до армировъчната "
                       "страна, защото погрешното приемане е по-скъпо от "
                       "пропуска. Присъдата не се променя при никоя стойност "
                       "между 0,02 и 0,99.",
            },
            "CODED_DIAMETER_MIN_MATCHES": {
                "value": CODED_DIAMETER_MIN_MATCHES,
                "how": "единственият слой, който добавя, е тест-006 Arm - N16 "
                       "(3 съвпадения, 3 от 3 съгласни, контрола 0)",
            },
            "MIN_CLAIMED_FOR_REFUSAL": {
                "value": MIN_CLAIMED_FOR_REFUSAL,
                "how": "под 3 заявени обекта не се твърди и отказ: Slab contour "
                       "има 148, Тест-003 SHAIBI — 1 и остава unknown",
            },
            "endpoint_tolerance_raw": {
                "value": "от RecognitionPolicy (0,75)",
                "how": "НЕ е нов праг — това е допускът, с който програмата "
                       "вече решава дали два сегмента се срещат. Проверена "
                       "чувствителност: разделянето е същото при 0,05–5,0 "
                       "сурови единици и при 0,0002–0,05 от медианната дължина.",
            },
        },
        "confusion_matrix": dict(sorted(matrix.items())),
        "runtime": timing,
        "headline": {
            "wrong_acceptances": wrong_accept,
            "misses": misses,
            "accepted_reference_rebar": matrix.get(
                f"reference_rebar__{VERDICT_REBAR}", 0),
            "refused_reference_not_rebar": (
                matrix.get(f"reference_not_rebar__{VERDICT_NOT_REBAR}", 0)
                + matrix.get(f"reference_not_rebar__{VERDICT_UNKNOWN}", 0)),
            "unlabelled_accepted": matrix.get(f"unlabelled__{VERDICT_REBAR}", 0),
        },
        "limitations": [
            "Еталоните са 16 армировъчни и 12 неармировъчни слоя в 11 проекта, "
            "от които РЕАЛНО спорни (с поне едно съвпадение по дължина) са 3 "
            "армировъчни и 1 контурен в d-2 плюс Arm-слоевете в s-8/s-9/"
            "тест-006. Останалите неармировъчни еталони получават нула "
            "съвпадения и се отказват по липса на доказателство, не от новия "
            "признак — те не проверяват разделителя.",
            "Корпусът е по-малък, отколкото изглежда: s-8 дава същите числа за "
            "Arm - N10/N12/N14 като s-9 (същият чертеж, различен обхват), а "
            "d-2 е единственият ASD диалект. Разделящият признак е проверен "
            "срещу ЕДИН истински контурен случай.",
            "Пропуски (отказани истински армировъчни слоеве): s-9 и тест-006 "
            "ARM_Bars (2 и 5 съвпадения), тест-004 Stremena (3), Тест-003 ARM.1 "
            "и ARM.MR.2 (по 2). Всичките падат по НАДПИСНАТА следа (бройка или "
            "превес над контролата), не по новия структурен признак. Това е "
            "нарочно: прагът на бройката не е пипан.",
            "Признакът мери свързаност В РАМКИТЕ НА СЛОЯ. Контур, начертан на "
            "слоя на армировката, и армировка, начертана като верига от "
            "допиращи се сегменти, остават неразделени. Второто е измерено: "
            "ARM_Bars дава 0,31–0,32 свързани обекта на слоя.",
            "Модулът не е закачен към разпознаването: нищо в програмата още не "
            "го вика. Мерено е върху working/extracted_entities.json, не през "
            "целия път импорт → анализ → Финален контрол.",
            "HATCH, CIRCLE и ELLIPSE не дават линейна дължина в текущия прочит "
            "(същия като на convention_profile), тоест слой само от тях не може "
            "да бъде отчетен в никоя посока.",
        ],
        "per_layer": per_layer,
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=1)
    print(f"записан: {OUTPUT}")
    print(f"погрешни приемания: {wrong_accept}   пропуски: {misses}")
    for key, value in sorted(matrix.items()):
        print(f"  {key:44s} {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
