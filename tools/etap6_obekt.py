# -*- coding: utf-8 -*-
"""Изважда сурово даден обект от чертежа, плюс съседите му по разстояние.

Нужно е, когато трябва да се установи ОТКЪДЕ програмата е взела число: полето
``candidate_notes`` казва „източници 2014", а какво е 2014 се вижда само от
самия чертеж.

Само чете.
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib

ЧИСТИ = pathlib.Path(r"C:\lr_exp\proekti_chisti")


def _обекти(проект: str) -> list[dict]:
    файл = ЧИСТИ / проект / "working" / "extracted_entities.json"
    д = json.loads(файл.read_text(encoding="utf-8"))
    return д.get("entities") or д.get("records") or д


def _точка(з: dict):
    г = з.get("geometry") or {}
    for к in ("insert", "text_midpoint", "defpoint", "start"):
        в = г.get(к)
        if в:
            return (float(в[0]), float(в[1]))
    т = г.get("points")
    if т:
        return (float(т[0][0]), float(т[0][1]))
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("проект")
    parser.add_argument("handles", nargs="+")
    parser.add_argument("--sasedi", type=float, default=0.0,
                        help="радиус за съседи в сурови единици")
    parser.add_argument("--vidove", default="",
                        help="само тези видове съседи, през запетая")
    args = parser.parse_args()

    записи = _обекти(args.проект)
    по_ръчка = {str(з.get("handle")): з for з in записи}
    видове = {в.strip().upper() for в in args.vidove.split(",") if в.strip()}

    for h in args.handles:
        з = по_ръчка.get(h)
        if з is None:
            print("=== %s: НЯМА ГО в извлечените обекти" % h)
            continue
        print("=== %s ===" % h)
        print(json.dumps(з, ensure_ascii=False, indent=1)[:1600])
        т = _точка(з)
        if args.sasedi and т:
            print("--- съседи до %.1f сурови единици ---" % args.sasedi)
            близки = []
            for друг in записи:
                if str(друг.get("handle")) == h:
                    continue
                if видове and str(друг.get("entity_type") or "").upper() not in видове:
                    continue
                тд = _точка(друг)
                if тд is None:
                    continue
                р = math.dist(т, тд)
                if р <= args.sasedi:
                    близки.append((р, друг))
            for р, друг in sorted(близки, key=lambda кв: кв[0])[:25]:
                г = друг.get("geometry") or {}
                print("   %7.2f  %-8s %-12s слой=%-38s текст=%s мярка=%s"
                      % (р, друг.get("handle"), друг.get("entity_type"),
                         str(друг.get("layer"))[:38],
                         str(друг.get("text") or "")[:30],
                         г.get("measurement")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
