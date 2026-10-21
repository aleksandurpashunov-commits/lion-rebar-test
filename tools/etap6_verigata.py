# -*- coding: utf-8 -*-
"""Цялата верига за една позиция: детайл → надпис → коти → геометрия → изход.

Раздел 4 на етап 6 иска за всеки спорен случай да се покаже целият път, а не
крайното число. Затова тук нищо не се обобщава: изваждат се СУРОВИТЕ обекти от
чертежа и се смятат дължините на участъците направо от върховете.

Търси се по (диаметър, обявена дължина) — ключът, по който ведомостта е сверена —
и се изброяват ВСИЧКИ съвпадащи надписи, за да е видимо дали съпоставянето е
еднозначно.

Само чете.
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys

sys.path.insert(0, r"C:\lr_exp\release")

ЧИСТИ = pathlib.Path(r"C:\lr_exp\proekti_chisti")


def _обекти(проект: pathlib.Path) -> dict[str, dict]:
    файл = проект / "working" / "extracted_entities.json"
    д = json.loads(файл.read_text(encoding="utf-8"))
    записи = д.get("entities") or д.get("records") or д
    return {str(з.get("handle")): з for з in записи}


def _върхове(запис: dict) -> list[tuple[float, float]]:
    г = запис.get("geometry") or {}
    точки = г.get("points")
    if точки:
        return [(float(т[0]), float(т[1])) for т in точки]
    за, до = г.get("start"), г.get("end")
    if за and до:
        return [(float(за[0]), float(за[1])), (float(до[0]), float(до[1]))]
    return []


def _участъци(върхове: list[tuple[float, float]]) -> list[float]:
    return [round(math.dist(върхове[и], върхове[и + 1]), 4)
            for и in range(len(върхове) - 1)]


def _ъгли(върхове: list[tuple[float, float]]) -> list[float]:
    ъгли = []
    for и in range(1, len(върхове) - 1):
        а = (върхове[и][0] - върхове[и - 1][0], върхове[и][1] - върхове[и - 1][1])
        б = (върхове[и + 1][0] - върхове[и][0], върхове[и + 1][1] - върхове[и][1])
        ъгъл = math.degrees(math.atan2(б[1], б[0]) - math.atan2(а[1], а[0]))
        ъгли.append(round((ъгъл + 180) % 360 - 180, 2))
    return ъгли


def _отчет(проект: str, папка: pathlib.Path) -> list[dict]:
    """Пакетният отчет от последния прогон на този проект."""

    файл = папка / ("%s.json" % проект)
    д = json.loads(файл.read_text(encoding="utf-8"))
    копие = pathlib.Path(str(д.get("copy") or ""))
    отчети = sorted((копие / "output" / "04_REVIEW_REPORTS")
                    .rglob("*_geometry_batch.json"))
    if not отчети:
        raise SystemExit("няма пакетен отчет в %s" % копие)
    return json.loads(отчети[-1].read_text(encoding="utf-8")).get("rows") or []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("проект")
    parser.add_argument("--diametar", type=float, required=True)
    parser.add_argument("--dulzhina", type=float, required=True)
    parser.add_argument("--progoni", type=pathlib.Path,
                        default=pathlib.Path(r"C:\lr_exp\rezultat\etap_5\progoni_B"))
    args = parser.parse_args()

    редове = _отчет(args.проект, args.progoni)
    обекти = _обекти(ЧИСТИ / args.проект)

    свои = [р for р in редове
            if float(р.get("diameter_mm") or 0) == args.diametar
            and float(р.get("declared_length_cm") or 0) == args.dulzhina]
    print("НАДПИСИ с Ø%g x %g cm: %d" % (args.diametar, args.dulzhina, len(свои)))
    print()
    for ред in свои:
        print("--- надпис %s: %s" % (ред.get("annotation_handle"),
                                     str(ред.get("annotation_text"))[:40]))
        for к in ("annotation_count", "master_shape_code", "batch_status",
                  "matcher_disposition", "candidate_layer", "candidate_handles",
                  "segment_count", "effective_geometry_length_cm",
                  "numeric_difference", "production_segments_cm",
                  "canonical_segment_lengths_cm", "canonical_turn_angles_deg",
                  "dimension_derived_evidence", "review_reason_codes",
                  "local_f006_printed_dimension_handles", "zone_label",
                  "beam_label"):
            в = ред.get(к)
            if в not in (None, "", [], {}):
                print("    %-32s %s" % (к, json.dumps(в, ensure_ascii=False)[:150]))
        бележки = [str(б) for б in (ред.get("candidate_notes") or ())]
        for б in бележки:
            print("    бележка: %s" % б[:140])
        for h in (ред.get("candidate_handles") or ()):
            з = обекти.get(str(h))
            if з is None:
                print("    ОБЕКТ %s: няма го в извлечените" % h)
                continue
            в = _върхове(з)
            print("    ОБЕКТ %s  %s  слой=%s  върхове=%d"
                  % (h, з.get("entity_type"), з.get("layer"), len(в)))
            if в:
                print("       участъци от геометрията: %s" % _участъци(в))
                print("       ъгли в възлите:          %s" % _ъгли(в))
                print("       сбор: %.4f" % sum(_участъци(в)))
                print("       затворена: %s" % ((з.get("geometry") or {}).get("closed")))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
