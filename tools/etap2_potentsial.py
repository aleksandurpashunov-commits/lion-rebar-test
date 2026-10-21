"""Колко носят слоевете, които класификаторът по ИМЕ не разпознава.

Подрежда направленията от раздел 4 по измерен потенциал, преди да се строи
каквото и да е. Чете ``working/layer_classification.json`` и
``working/extracted_entities.json`` от копията на проектите. Само чете.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections import Counter, defaultdict

КОРЕН = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(КОРЕН))

ПРОЕКТИ = pathlib.Path(r"C:\lr_exp\proekti")

СЕМЕЙСТВО = {
    "т-1": "т-1", "s-9": "s-9", "s-11": "s-11", "s-8": "s-8", "s-10": "s-10",
    "s-7": "s-7", "s-1": "s-1..s-6/а-1", "d-1": "d-1", "d-2": "d-2",
    "Тест-003": "тест-001/Тест-003", "Тест-002": "Тест-002",
    "тест-004": "тест-004/005", "тест-006": "тест-006",
}
ЗАКЛЮЧЕНИ = {"т-1", "Тест-002", "s-7", "s-1..s-6/а-1"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Потенциал по направления")
    parser.add_argument("--json", type=pathlib.Path, default=None)
    args = parser.parse_args()

    print("%-10s %-4s %6s %6s %8s %9s %9s" % (
        "проект", "набор", "слоеве", "UNKNW", "обекти_U", "арм.текст", "дял_текст"))
    print("-" * 70)

    редове = {}
    сбор = Counter()
    сбор_р = Counter()
    роли_общо: Counter = Counter()
    неразпознати_имена: Counter = Counter()

    for проект in sorted(ПРОЕКТИ.iterdir()):
        if not проект.is_dir():
            continue
        п = проект / "working" / "layer_classification.json"
        if not п.is_file():
            continue
        д = json.loads(п.read_text(encoding="utf-8-sig"))
        слоеве = д.get("layers") or []
        заключен = СЕМЕЙСТВО.get(проект.name, проект.name) in ЗАКЛЮЧЕНИ

        общо_текст = sum(int(р.get("rebar_text_count") or 0) for р in слоеве)
        непознати = [р for р in слоеве if str(р.get("role") or "") == "UNKNOWN"]
        обекти_u = sum(int(р.get("entity_count") or 0) for р in непознати)
        текст_u = sum(int(р.get("rebar_text_count") or 0) for р in непознати)
        for р in слоеве:
            роли_общо[str(р.get("role") or "")] += 1
        for р in непознати:
            if int(р.get("rebar_text_count") or 0) or int(р.get("entity_count") or 0) > 50:
                неразпознати_имена[str(р.get("layer") or "")] += 1

        редове[проект.name] = {
            "набор": "ЗАКЛ" if заключен else "РАЗВ",
            "слоеве": len(слоеве), "unknown": len(непознати),
            "обекти_в_unknown": обекти_u,
            "арм_текст_в_unknown": текст_u,
            "арм_текст_общо": общо_текст,
            "имена_на_unknown": [str(р.get("layer") or "") for р in непознати][:40],
        }
        for ключ, стойност in (("слоеве", len(слоеве)), ("unknown", len(непознати)),
                               ("обекти_u", обекти_u), ("текст_u", текст_u),
                               ("текст_общо", общо_текст)):
            сбор[ключ] += стойност
            if not заключен:
                сбор_р[ключ] += стойност

        дял = ("%.1f%%" % (текст_u * 100.0 / общо_текст)) if общо_текст else "n/a"
        print("%-10s %-4s %6d %6d %8d %9d %9s" % (
            проект.name, "ЗАКЛ" if заключен else "РАЗВ", len(слоеве),
            len(непознати), обекти_u, текст_u, дял))

    for етикет, с in (("ЦЯЛ КОРПУС", сбор), ("РАЗВОЕН", сбор_р)):
        дял = ("%.1f%%" % (с["текст_u"] * 100.0 / с["текст_общо"])) if с["текст_общо"] else "n/a"
        print("\n=== %s" % етикет)
        print("  слоеве общо                     %6d" % с["слоеве"])
        print("  UNKNOWN по име                  %6d  (%.1f%%)"
              % (с["unknown"], с["unknown"] * 100.0 / max(1, с["слоеве"])))
        print("  обекти в UNKNOWN слоеве         %6d" % с["обекти_u"])
        print("  армировъчни надписи в UNKNOWN   %6d  от %d (%s)"
              % (с["текст_u"], с["текст_общо"], дял))

    print("\n=== РОЛИ, КОИТО ИМЕННИЯТ КЛАСИФИКАТОР ДАВА (всички проекти)")
    for роля, брой in роли_общо.most_common():
        print("  %5d  %s" % (брой, роля))

    print("\n=== НЕРАЗПОЗНАТИ ИМЕНА С ТЕЖЕСТ (в колко проекта се срещат)")
    for име, брой in неразпознати_имена.most_common(25):
        print("  x%-3d %s" % (брой, име or "(празно)"))

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps({
            "по_проект": редове,
            "сбор_цял_корпус": dict(сбор),
            "сбор_развоен": dict(сбор_р),
            "роли": dict(роли_общо),
            "неразпознати_имена": dict(неразпознати_имена),
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        print("\nзаписан:", args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
