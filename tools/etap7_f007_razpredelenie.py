# -*- coding: utf-8 -*-
"""Подкрепя ли начертаното разпределението L/4 + L/2 + L/4 за F007.

Правилото `F007_ESOVE_CURVED_CENTERLINE` възстановява производствените участъци
като `L/4 + L/2 + L/4` **от обявената обща дължина**. Заданието: самата обща
дължина не доказва това разпределение.

Тук за всяка засегната позиция се измерва какво казва ЧЕРТЕЖЪТ:

* видимите прави участъци и отношението им;
* семантичният път (допирателните дъги сгънати до пресечните точки) — това е
  най-близкото до производствената интерпретация, което геометрията дава;
* сравнение на семантичните участъци с (L/4, L/2, L/4).

Само чете.
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys

sys.path.insert(0, r"C:\lr_exp\release")

from lion_rebar.dxf.geometry_core_v2 import (
    arc_path_from_geometry,
    polyline_path_from_geometry,
    semantic_path_from_polyline,
)

ЧИСТИ = pathlib.Path(r"C:\lr_exp\proekti_chisti")


def _обекти(проект: str) -> dict[str, dict]:
    ф = ЧИСТИ / проект / "working" / "extracted_entities.json"
    д = json.loads(ф.read_text(encoding="utf-8"))
    return {str(з.get("handle")): з for з in (д.get("entities") or д)}


def _дължини(точки) -> list[float]:
    т = [(float(п[0]), float(п[1])) for п in точки]
    return [round(math.dist(т[и], т[и + 1]), 3) for и in range(len(т) - 1)]


def _пътища(з: dict):
    """(сурови участъци, семантични участъци, брой дъги, радиуси)."""

    вид = str(з.get("entity_type") or "").upper()
    г = з.get("geometry") or {}
    if вид == "LINE":
        за, до = г.get("start"), г.get("end")
        if not (за and до):
            return None
        д = _дължини([за, до])
        return д, д, 0, ()
    път = (arc_path_from_geometry(г) if вид == "ARC"
           else polyline_path_from_geometry(г))
    if път is None:
        return None
    сем = semantic_path_from_polyline(път)
    return (_дължини(път.points), _дължини(сем.points),
            път.curve_count, tuple(round(float(р), 3) for р in път.arc_radii_raw))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--progoni", type=pathlib.Path, required=True)
    parser.add_argument("--izhod", type=pathlib.Path, default=None)
    args = parser.parse_args()

    находки = []
    броеве = {"общо": 0, "семантичното ПОДКРЕПЯ разпределението": 0,
              "семантичното НЕ го подкрепя": 0, "няма годна геометрия": 0}
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
        for ред in редове:
            дока = ред.get("production_segment_evidence") or {}
            ако = str(дока.get("source") or "")
            if ако != "F007_CONTEXTUAL_MASTER_CANONICAL":
                continue
            броеве["общо"] += 1
            обявена = float(ред.get("declared_length_cm") or 0)
            вектор = [float(с) for с in (ред.get("production_segments_cm") or ())]
            сурови, семантични, дъги, радиуси = [], [], 0, ()
            за_обект = None
            for h in (ред.get("candidate_handles") or ()):
                з = обекти.get(str(h))
                if з is None:
                    continue
                р = _пътища(з)
                if р is None:
                    continue
                с, сем, n, рад = р
                сурови.extend(с); семантични.extend(сем)
                дъги += n; радиуси = радиуси + рад
                за_обект = з
            ако_подкрепя = None
            if len(семантични) == len(вектор) and вектор:
                разлики = [abs(а - б) for а, б in zip(sorted(семантични), sorted(вектор))]
                ако_подкрепя = max(разлики) <= 6.0
            броеве["семантичното ПОДКРЕПЯ разпределението" if ако_подкрепя
                    else ("семантичното НЕ го подкрепя" if ако_подкрепя is False
                          else "няма годна геометрия")] += 1
            находки.append({
                "проект": проект,
                "надпис": str(ред.get("annotation_handle") or ""),
                "текст": str(ред.get("annotation_text") or "")[:26],
                "обявена": обявена, "вектор": вектор,
                "сурови": сурови, "семантични": семантични,
                "дъги": дъги, "радиуси": list(радиуси),
                "слой": str((за_обект or {}).get("layer") or ""),
                "подкрепя": ако_подкрепя,
            })

    print("=== F007 по контекстуалното правило ===")
    for к, в in броеве.items():
        print("   %-42s %d" % (к, в))
    print()
    print("%-9s %-9s %-24s %-9s %-22s %-26s %s" % (
        "проект", "надпис", "текст", "обявена", "вектор (L/4,L/2,L/4)",
        "семантични участъци", "подкрепя"))
    for н in находки[:28]:
        print("%-9s %-9s %-24s %-9s %-22s %-26s %s" % (
            н["проект"], н["надпис"][:9], н["текст"][:24], н["обявена"],
            str(н["вектор"])[:22], str(н["семантични"])[:26], н["подкрепя"]))

    if args.izhod:
        args.izhod.write_text(json.dumps({"броеве": броеве, "находки": находки},
                                         ensure_ascii=False, indent=1),
                              encoding="utf-8")
        print("\nзаписано:", args.izhod)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
