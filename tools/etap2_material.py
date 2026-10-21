"""Какво предлага паспортът по СЪДЪРЖАНИЕ върху целия корпус.

Това е материалът за обучение: къде ``propose_convention_profile`` дава
еднозначно предложение (значи съдържанието ДОКАЗВА ролята) и къде отказва
(значи знание от друг чертеж би помогнало).

Чете ``working/extracted_entities.json`` от копията. Само чете.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections import Counter, defaultdict

КОРЕН = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(КОРЕН))

from lion_rebar.dxf.convention_profile import propose_convention_profile  # noqa: E402
from lion_rebar.dxf.models import DxfEntityRecord  # noqa: E402

ПРОЕКТИ = pathlib.Path(r"C:\lr_exp\proekti")

СЕМЕЙСТВО = {
    "т-1": "т-1", "s-9": "s-9", "s-11": "s-11", "s-8": "s-8", "s-10": "s-10",
    "s-7": "s-7", "s-1": "s-1..s-6/а-1", "d-1": "d-1", "d-2": "d-2",
    "Тест-003": "тест-001/Тест-003", "Тест-002": "Тест-002",
    "тест-004": "тест-004/005", "тест-006": "тест-006",
}
ЗАКЛЮЧЕНИ = {"т-1", "Тест-002", "s-7", "s-1..s-6/а-1"}

ПОЛЕТА = ("handle", "entity_type", "layer", "geometry", "text", "block_name",
          "color_index", "linetype", "raw")


def обекти_на(проект: pathlib.Path) -> list[DxfEntityRecord]:
    п = проект / "working" / "extracted_entities.json"
    if not п.is_file():
        return []
    д = json.loads(п.read_text(encoding="utf-8-sig"))
    редове = д if isinstance(д, list) else (д.get("entities") or [])
    изход = []
    for р in редове:
        if not isinstance(р, dict):
            continue
        изход.append(DxfEntityRecord(**{и: р.get(и) for и in ПОЛЕТА
                                       if и in р or и in ("handle", "entity_type", "layer")}))
    return изход


def main() -> int:
    parser = argparse.ArgumentParser(description="Материалът за обучение")
    parser.add_argument("--json", type=pathlib.Path, default=None)
    args = parser.parse_args()

    print("%-10s %-4s %7s %6s %7s  %s" % (
        "проект", "набор", "обекти", "предл", "откази", "предложени роли"))
    print("-" * 92)

    по_проект = {}
    роли_общо: Counter = Counter()
    откази_общо: Counter = Counter()
    # слой -> кои проекти и с каква роля го доказват по съдържание
    слой_роля: dict[tuple[str, str], list[str]] = defaultdict(list)
    слой_отказ: dict[tuple[str, str], list[str]] = defaultdict(list)

    for проект in sorted(ПРОЕКТИ.iterdir()):
        if not проект.is_dir():
            continue
        обекти = обекти_на(проект)
        if not обекти:
            continue
        заключен = СЕМЕЙСТВО.get(проект.name, проект.name) in ЗАКЛЮЧЕНИ
        профил = propose_convention_profile(обекти)
        предл = профил.get("proposals") or []
        откази = профил.get("refusals") or []

        роли = Counter(str(р.get("role") or "") for р in предл)
        for р in предл:
            роли_общо[str(р.get("role") or "")] += 1
            ключ = (str(р.get("layer") or ""), str(р.get("role") or ""))
            слой_роля[ключ].append(проект.name)
        for о in откази:
            откази_общо[str(о.get("role") or "") + " / " + str(о.get("reason") or "")[:40]] += 1
            ключ = (str(о.get("layer") or ""), str(о.get("role") or ""))
            слой_отказ[ключ].append(проект.name)

        по_проект[проект.name] = {
            "набор": "ЗАКЛ" if заключен else "РАЗВ",
            "обекти": len(обекти),
            "предложения": len(предл), "откази": len(откази),
            "роли": dict(роли),
            "предложени": [{"слой": р.get("layer"), "блок": r_блок(р), "роля": р.get("role"),
                            "увереност": р.get("confidence"),
                            "доказателство": str(р.get("evidence") or "")[:120]}
                           for р in предл],
            "откази_подробно": [{"слой": о.get("layer"), "роля": о.get("role"),
                                 "причина": str(о.get("reason") or "")[:110]}
                                for о in откази],
        }
        print("%-10s %-4s %7d %6d %7d  %s" % (
            проект.name, "ЗАКЛ" if заключен else "РАЗВ", len(обекти),
            len(предл), len(откази),
            ", ".join("%s×%d" % (к, v) for к, v in роли.most_common(4)) or "—"))

    print("\n=== ПРЕДЛОЖЕНИ РОЛИ (цял корпус)")
    for роля, брой in роли_общо.most_common():
        print("  %5d  %s" % (брой, роля))

    print("\n=== СЛОЙ+РОЛЯ, ДОКАЗАНИ ПО СЪДЪРЖАНИЕ В ПОВЕЧЕ ОТ ЕДИН ЧЕРТЕЖ")
    print("    (това е материалът, който може да се пренесе)")
    пренос = {к: v for к, v in слой_роля.items() if len(v) > 1}
    for (слой, роля), проекти in sorted(пренос.items(), key=lambda kv: -len(kv[1])):
        print("  x%-2d %-28s %-22s %s" % (len(проекти), слой[:28], роля, ", ".join(проекти)))
    if not пренос:
        print("  (няма)")

    print("\n=== СЛОЙ+РОЛЯ, ДОКАЗАНИ НЯКЪДЕ, но ОТКАЗАНИ другаде")
    print("    (точно тук наученото от чертеж А би помогнало на чертеж Б)")
    намерени = 0
    for ключ, отказали in sorted(слой_отказ.items(), key=lambda kv: -len(kv[1])):
        доказали = слой_роля.get(ключ) or []
        if not доказали:
            continue
        намерени += 1
        слой, роля = ключ
        print("  %-26s %-20s доказан в: %-22s отказан в: %s"
              % (слой[:26], роля, ", ".join(доказали), ", ".join(отказали)))
    if not намерени:
        print("  (няма пряко съвпадение по име на слой)")

    print("\n=== НАЙ-ЧЕСТИ ПРИЧИНИ ЗА ОТКАЗ")
    for причина, брой in откази_общо.most_common(14):
        print("  %5d  %s" % (брой, причина))

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps({
            "по_проект": по_проект,
            "роли_общо": dict(роли_общо),
            "пренос": {"%s|%s" % к: v for к, v in пренос.items()},
            "откази_общо": dict(откази_общо),
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        print("\nзаписан:", args.json)
    return 0


def r_блок(р: dict) -> str:
    return str(р.get("block_name") or "")


if __name__ == "__main__":
    raise SystemExit(main())
