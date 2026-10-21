# -*- coding: utf-8 -*-
"""Произходът на всяка производствена стойност — сурово, нормализирано, досметнато.

Раздел 2 на заданието иска за спорните позиции да е видимо:

* суровите и нормализираните участъци, С РЕДА им;
* как е получена нормализацията;
* произходът на всяка стойност — директно прочетена, геометрично изчислена или
  досметната от общата дължина;
* **зависи ли нормализацията от обявената обща дължина или от проверявания
  производствен вектор.** Ако зависи, тя не е независимо потвърждение.

Нормализацията се възпроизвежда тук през същите функции, които ползва
програмата (`geometry_core_v2.semantic_path_from_polyline`), за да е сверимо.

Само чете.
"""
from __future__ import annotations

import argparse
import inspect
import json
import math
import pathlib
import sys

sys.path.insert(0, r"C:\lr_exp\release")

from lion_rebar.dxf.geometry_core_v2 import (
    polyline_path_from_geometry,
    semantic_path_from_polyline,
)

ЧИСТИ = pathlib.Path(r"C:\lr_exp\proekti_chisti")


def _обекти(проект: str) -> dict[str, dict]:
    ф = ЧИСТИ / проект / "working" / "extracted_entities.json"
    д = json.loads(ф.read_text(encoding="utf-8"))
    return {str(з.get("handle")): з for з in (д.get("entities") or д)}


def _върхове(з: dict) -> list[tuple[float, float]]:
    г = з.get("geometry") or {}
    т = г.get("points")
    if т:
        return [(float(п[0]), float(п[1])) for п in т]
    за, до = г.get("start"), г.get("end")
    if за and до:
        return [(float(за[0]), float(за[1])), (float(до[0]), float(до[1]))]
    return []


def _участъци(т: list[tuple[float, float]]) -> list[float]:
    return [round(math.dist(т[и], т[и + 1]), 4) for и in range(len(т) - 1)]


def _нормализирани(з: dict) -> tuple[list[float], str]:
    """Семантичните участъци и как са получени."""

    вид = str(з.get("entity_type") or "").upper()
    if вид in {"LINE"}:
        return (_участъци(_върхове(з)),
                "LINE: няма дъги за сгъване — семантичното съвпада със суровото")
    if вид in {"LWPOLYLINE", "POLYLINE"}:
        път = polyline_path_from_geometry(з.get("geometry") or {})
        if път is None:
            return ([], "полилинията не се чете")
        сем = semantic_path_from_polyline(път)
        как = ("semantic_path_from_polyline: сгънати %d допирателни дъги от %d; "
               "представяне %s" % (сем.collapsed_arc_count, път.curve_count,
                                   getattr(сем.representation, "value", сем.representation)))
        return ([round(с, 4) for с in _участъци(list(сем.points))], как)
    return ([], "вид %s не се нормализира тук" % вид)


def _зависи_ли_от_обявената() -> dict:
    """Проверява по ПОДПИСА дали нормализацията вижда обявената дължина."""

    подпис = inspect.signature(semantic_path_from_polyline)
    имена = sorted(подпис.parameters)
    подозрителни = [и for и in имена
                    if any(к in и.lower() for к in
                           ("declared", "length_cm", "production", "total",
                            "segment_lengths"))]
    изходен = inspect.getsource(semantic_path_from_polyline)
    споменава = [к for к in ("declared_length", "production_segments",
                             "declared_length_cm") if к in изходен]
    return {
        "параметри": имена,
        "параметри_свързани_с_обявената": подозрителни,
        "споменава_в_тялото": споменава,
        "независима": not подозрителни and not споменава,
    }


def _отчет(проект: str, папка: pathlib.Path) -> dict[str, dict]:
    д = json.loads((папка / ("%s.json" % проект)).read_text(encoding="utf-8"))
    о = sorted((pathlib.Path(str(д.get("copy") or "")) / "output" /
                "04_REVIEW_REPORTS").rglob("*_geometry_batch.json"))
    if not о:
        return {}
    return {str(р.get("annotation_handle") or ""): р
            for р in json.loads(о[-1].read_text(encoding="utf-8")).get("rows") or ()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("проект")
    parser.add_argument("nadpisi", nargs="+")
    parser.add_argument("--progoni", type=pathlib.Path,
                        default=pathlib.Path(r"C:\lr_exp\rezultat\etap_6\progoni_B"))
    parser.add_argument("--izhod", type=pathlib.Path, default=None)
    args = parser.parse_args()

    зависимост = _зависи_ли_от_обявената()
    print("=== зависи ли нормализацията от обявената дължина ===")
    print(json.dumps(зависимост, ensure_ascii=False, indent=1))
    print()

    редове = _отчет(args.проект, args.progoni)
    обекти = _обекти(args.проект)
    изход: dict[str, dict] = {"нормализация": зависимост, "позиции": {}}

    for ръчка in args.nadpisi:
        ред = редове.get(ръчка) or {}
        print("=== %s  %s" % (ръчка, str(ред.get("annotation_text") or "")[:40]))
        обявена = ред.get("declared_length_cm")
        вектор = [float(с) for с in (ред.get("production_segments_cm") or ())]
        print("   обявена дължина: %s | производствен вектор: %s"
              % (обявена, вектор or "НЯМА"))
        свои = {"текст": str(ред.get("annotation_text") or ""),
                "обявена": обявена, "вектор": вектор,
                "форма": str(ред.get("master_shape_code") or ""),
                "кандидати": []}
        сурови_общо: list[float] = []
        норм_общо: list[float] = []
        for h in (ред.get("candidate_handles") or ()):
            з = обекти.get(str(h))
            if з is None:
                print("   ОБЕКТ %s: липсва" % h)
                continue
            сурови = _участъци(_върхове(з))
            норм, как = _нормализирани(з)
            сурови_общо.extend(сурови)
            норм_общо.extend(норм)
            print("   ОБЕКТ %-8s %-11s слой=%s" % (h, з.get("entity_type"),
                                                   str(з.get("layer"))[:46]))
            print("      сурови участъци (в реда на върховете): %s" % сурови)
            print("      нормализирани участъци:                %s" % норм)
            print("      как: %s" % как)
            свои["кандидати"].append({
                "handle": str(h), "вид": str(з.get("entity_type") or ""),
                "слой": str(з.get("layer") or ""),
                "сурови": сурови, "нормализирани": норм, "как": как,
            })
        свои["сурови_общо"] = сурови_общо
        свои["нормализирани_общо"] = норм_общо
        if сурови_общо:
            print("   СБОР сурово: %.4f | нормализирано: %.4f | обявена: %s"
                  % (sum(сурови_общо), sum(норм_общо), обявена))
        бележки = [str(б) for б in (ред.get("candidate_notes") or ())]
        източници = [б for б in бележки
                     if "A–E" in б or "SEGMENTS_DERIVED" in б
                     or "UNCONFIRMED" in б or "AUTHORITATIVE" in б]
        свои["бележки_за_произход"] = източници
        for б in източници:
            print("   произход: %s" % б[:150])
        изход["позиции"][ръчка] = свои
        print()

    if args.izhod:
        args.izhod.write_text(json.dumps(изход, ensure_ascii=False, indent=1),
                              encoding="utf-8")
        print("записано:", args.izhod)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
