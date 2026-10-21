# -*- coding: utf-8 -*-
"""Общността на закръгляването към мрежата — по СЕМЕЙСТВА.

Правилото беше настроено по `d-2` (и проверено срещу един случай в `s-9`).
Затова резултатът върху `d-2` не е доказателство за непознат проектант.

Тук същото правило се прилага върху всички проекти и се отчита ОТДЕЛНО:

* семействата, ползвани за настройване (`d-2`, `s-9`);
* останалите — независима извадка.

За всяка позиция се мери дали правилото:
* **потвърждава** вече доказан вектор;
* **дава нов** вектор там, където няма;
* **противоречи** на доказан вектор;
* **отказва**.

Само чете.
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import pathlib
import sys

sys.path.insert(0, r"C:\lr_exp\release")

from lion_rebar.dxf.geometry_core_v2 import (
    polyline_path_from_geometry,
    semantic_path_from_polyline,
)
from lion_rebar.dxf.grid_rounded_segments import ПРИЕТО, РАЗМИНАВАНЕ, solve

ЧИСТИ = pathlib.Path(r"C:\lr_exp\proekti_chisti")

#: Семействата, върху които правилото беше настроено. Резултатът там НЕ е
#: доказателство за непознат проектант.
НАСТРОЙВАНИ = {"d-2", "s-9"}


def _обекти(проект: str) -> dict[str, dict]:
    ф = ЧИСТИ / проект / "working" / "extracted_entities.json"
    д = json.loads(ф.read_text(encoding="utf-8"))
    return {str(з.get("handle")): з for з in (д.get("entities") or д)}


def _семантични(з: dict) -> list[float] | None:
    вид = str(з.get("entity_type") or "").upper()
    г = з.get("geometry") or {}
    if вид == "LINE":
        за, до = г.get("start"), г.get("end")
        if not (за and до):
            return None
        return [math.dist((float(за[0]), float(за[1])),
                          (float(до[0]), float(до[1])))]
    if вид not in {"LWPOLYLINE", "POLYLINE"}:
        return None
    път = polyline_path_from_geometry(г)
    if път is None:
        return None
    сем = semantic_path_from_polyline(път)
    if not сем.safe_for_linear_matching:
        return None
    т = list(сем.points)
    return [math.dist(т[и], т[и + 1]) for и in range(len(т) - 1)]


def _участъци(ред, обекти):
    изход = []
    for h in (ред.get("candidate_handles") or ()):
        з = обекти.get(str(h))
        if з is None:
            return None
        части = _семантични(з)
        if части is None:
            return None
        изход.extend(части)
    return изход or None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--progoni", type=pathlib.Path, required=True)
    parser.add_argument("--izhod", type=pathlib.Path, default=None)
    args = parser.parse_args()

    по_проекти: dict[str, collections.Counter] = {}
    противоречия: list[dict] = []
    for файл in sorted(args.progoni.glob("*.json")):
        if файл.name.startswith("_"):
            continue
        проект = файл.stem
        д = json.loads(файл.read_text(encoding="utf-8"))
        о = sorted((pathlib.Path(str(д.get("copy") or "")) / "output" /
                    "04_REVIEW_REPORTS").rglob("*_geometry_batch.json"))
        if not о:
            continue
        редове = json.loads(о[-1].read_text(encoding="utf-8")).get("rows") or []
        обекти = _обекти(проект)
        брояч = collections.Counter()
        for ред in редове:
            обявена = float(ред.get("declared_length_cm") or 0)
            if обявена <= 0:
                continue
            участъци = _участъци(ред, обекти)
            if not участъци or len(участъци) < 2:
                continue
            обявен_брой = ред.get("segment_count")
            try:
                if int(обявен_брой) != len(участъци):
                    brояч = брояч  # само за четимост
                    брояч["друга топология"] += 1
                    continue
            except (TypeError, ValueError):
                брояч["без брой участъци"] += 1
                continue
            присъда = solve(участъци, обявена)
            текущ = [float(с) for с in (ред.get("production_segments_cm") or ())]
            ако = str(присъда.get("status") or "")
            if ако != ПРИЕТО:
                брояч["отказ"] += 1
                continue
            нов = присъда["segments_cm"]
            if not текущ:
                брояч["нов вектор"] += 1
            elif (len(текущ) == len(нов)
                  and all(abs(а - б) <= 0.51 for а, б
                          in zip(sorted(текущ), sorted(нов)))):
                брояч["потвърждава доказания"] += 1
            else:
                брояч["ПРОТИВОРЕЧИ"] += 1
                противоречия.append({
                    "проект": проект,
                    "надпис": str(ред.get("annotation_handle") or ""),
                    "текст": str(ред.get("annotation_text") or "")[:26],
                    "семантични": [round(с, 3) for с in участъци],
                    "закръглено": нов, "доказан": текущ,
                    "мрежа": присъда.get("grid_cm"),
                })
        по_проекти[проект] = брояч

    print("%-10s %-10s %-12s %-22s %-13s %-8s %s"
          % ("проект", "набор", "нов вектор", "потвърждава доказания",
             "ПРОТИВОРЕЧИ", "отказ", "друга топология"))
    сборове = {"настройвани": collections.Counter(),
               "независими": collections.Counter()}
    for проект, б in по_проекти.items():
        набор = "настройван" if проект in НАСТРОЙВАНИ else "независим"
        сборове["настройвани" if проект in НАСТРОЙВАНИ else "независими"].update(б)
        print("%-10s %-10s %-12d %-22d %-13d %-8d %d"
              % (проект, набор, б["нов вектор"], б["потвърждава доказания"],
                 б["ПРОТИВОРЕЧИ"], б["отказ"], б["друга топология"]))

    print()
    for име, б in сборове.items():
        общо = б["нов вектор"] + б["потвърждава доказания"] + б["ПРОТИВОРЕЧИ"]
        print("%-12s нов=%-5d потвърждава=%-5d противоречи=%-4d  (точност върху "
              "доказаните: %s)"
              % (име, б["нов вектор"], б["потвърждава доказания"], б["ПРОТИВОРЕЧИ"],
                 ("%.1f %%" % (100.0 * б["потвърждава доказания"]
                               / max(1, б["потвърждава доказания"] + б["ПРОТИВОРЕЧИ"]))
                  )))

    print("\n--- противоречия (до 12) ---")
    for п in противоречия[:12]:
        print("   %-9s %-9s %-24s сем=%-24s закр=%-20s доказан=%s"
              % (п["проект"], п["надпис"][:9], п["текст"][:24],
                 str(п["семантични"])[:24], str(п["закръглено"])[:20],
                 str(п["доказан"])[:24]))

    if args.izhod:
        args.izhod.write_text(json.dumps(
            {"по_проекти": {к: dict(в) for к, в in по_проекти.items()},
             "сборове": {к: dict(в) for к, в in сборове.items()},
             "противоречия": противоречия}, ensure_ascii=False, indent=1),
            encoding="utf-8")
        print("\nзаписано:", args.izhod)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
