# -*- coding: utf-8 -*-
"""Сравнение на два (или три) прогона ПО САМОЛИЧНОСТ НА ПОЗИЦИИТЕ.

Броят не стига: правилна защита може да намали броя, а грешка може да го
запази. Затова се сравняват handle-ите: кои позиции влизат, кои отпадат и с
какви производствени числа.

Вход: папките на прогоните (``tools/etap4_chisti_progoni.py --izhod``).
"""
from __future__ import annotations

import argparse
import json
import pathlib
from collections import Counter


def позиции(папка: pathlib.Path, проект: str) -> dict:
    път = папка / ("%s.json" % проект)
    if not път.is_file():
        return {}
    д = json.loads(път.read_text(encoding="utf-8"))
    g = д.get("gate") or {}
    блокирани = {str(б.get("handle")): sorted(б.get("reasons") or ())
                 for б in (g.get("blocked_positions") or ())}
    return {
        "порта": g.get("approved"),
        "actionable": g.get("actionable"),
        "фин_контрол": (д.get("final_control") or {}).get("approved_after"),
        "нерешени": (д.get("final_control") or {}).get("unresolved_positions"),
        "експорт": (д.get("export") or {}).get("status") or д.get("stop_code"),
        "блокирани": блокирани,
    }


def main() -> int:
    ап = argparse.ArgumentParser()
    ап.add_argument("папки", nargs="+", type=pathlib.Path)
    ап.add_argument("--imena", default="")
    ап.add_argument("--izhod", type=pathlib.Path,
                    default=pathlib.Path(r"C:\lr_exp\rezultat\etap_8\SRAVNENIE.json"))
    арг = ап.parse_args()
    имена = (арг.imena.split(",") if арг.imena
             else [п.name for п in арг.папки])
    проекти = sorted({п.stem for папка in арг.папки for п in папка.glob("*.json")
                      if not п.stem.startswith("_")})

    изход = {"схема": "etap8-sravnenie/v1", "прогони": имена, "проекти": {}}
    сборове = {име: Counter() for име in имена}
    print("%-10s %s" % ("проект", " ".join("%-26s" % и for и in имена)))
    for проект in проекти:
        редове = [позиции(папка, проект) for папка in арг.папки]
        клетки = []
        for р in редове:
            клетки.append("%4s/%-4s бл=%-3s %-9s" % (
                р.get("порта"), р.get("actionable"), len(р.get("блокирани") or {}),
                str(р.get("експорт") or "")[:9]))
        print("%-10s %s" % (проект, " ".join("%-26s" % к for к in клетки)))
        # разлика по самоличност спрямо ПЪРВИЯ прогон
        основа = set((редове[0].get("блокирани") or {}))
        разлики = {}
        for име, р in zip(имена[1:], редове[1:]):
            текущи = set(р.get("блокирани") or {})
            разлики[име] = {
                "отпаднала_блокада": sorted(основа - текущи),
                "нова_блокада": sorted(текущи - основа),
            }
        изход["проекти"][проект] = {
            "прогони": dict(zip(имена, редове)), "разлики_спрямо_първия": разлики,
        }
        for име, р in zip(имена, редове):
            сборове[име]["порта"] += р.get("порта") or 0
            сборове[име]["actionable"] += р.get("actionable") or 0
            сборове[име]["блокирани"] += len(р.get("блокирани") or {})
            сборове[име]["експорт"] += 1 if str(р.get("експорт") or "") == "PRODUCTION VERIFIED" else 0
    изход["общо"] = {и: dict(с) for и, с in сборове.items()}
    print()
    for име in имена:
        с = сборове[име]
        print("%-26s порта=%-5d actionable=%-5d блокирани=%-4d експортиращи проекта=%d"
              % (име, с["порта"], с["actionable"], с["блокирани"], с["експорт"]))
    арг.izhod.parent.mkdir(parents=True, exist_ok=True)
    арг.izhod.write_text(json.dumps(изход, ensure_ascii=False, indent=1), encoding="utf-8")
    print("записано:", арг.izhod)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
