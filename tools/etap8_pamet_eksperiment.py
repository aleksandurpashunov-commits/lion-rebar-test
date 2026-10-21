# -*- coding: utf-8 -*-
"""Експериментът за паметта: А (замразен етап 7) / Б (нов код без памет) /
В (същият нов код с предварително научена памет).

Цикълът се изпълнява от САМАТА програма: обучаващите чертежи се пускат с
включено обучение и включен автоматичен цикъл, програмата сама наблюдава,
предлага, проверява и приема; после хранилището се ЗАМРАЗЯВА и се ползва върху
изпитвания чертеж, който НЕ е участвал в обучението (``LION_REBAR_LOCKED_FAMILIES``).

Между стъпките не се редактират правила и не се одобрява от името на оператора.

Пример:

    python tools/etap8_pamet_eksperiment.py \
        --uchi s-8 s-9 --izpit тест-006 \
        --izhod C:/lr_exp/rezultat/etap_8/pamet
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time

КОРЕН = pathlib.Path(__file__).resolve().parents[1]
ЧИСТИ = pathlib.Path(r"C:\lr_exp\proekti_chisti")
FULL_PATH = КОРЕН / ".claude" / "skills" / "lion-rebar-full-path" / "full_path.py"


def пусни(проект: str, отчет: pathlib.Path, среда: dict) -> dict:
    т0 = time.time()
    готово = subprocess.run(
        [sys.executable, str(FULL_PATH), str(ЧИСТИ / проект), "--json", str(отчет)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=среда, cwd=str(КОРЕН))
    д = json.loads(отчет.read_text(encoding="utf-8")) if отчет.is_file() else {}
    д["_seconds"] = round(time.time() - т0, 1)
    д["_exit"] = готово.returncode
    if not д:
        д["_stderr"] = (готово.stderr or готово.stdout or "")[-400:]
    return д


def основна_среда() -> dict:
    среда = dict(os.environ)
    среда.update({"LION_REBAR_DATA_ROOT": r"C:\lr_exp\data",
                  "PYTHONIOENCODING": "utf-8",
                  "QT_QPA_PLATFORM": "offscreen"})
    return среда


def кратко(д: dict) -> dict:
    g = д.get("gate") or {}
    f = д.get("final_control") or {}
    l = д.get("learning") or {}
    return {
        "порта": g.get("approved"),
        "actionable": g.get("actionable"),
        "блокирани": sorted(str(б.get("handle")) for б in (g.get("blocked_positions") or ())),
        "фин_контрол": f.get("approved_after"),
        "нерешени": f.get("unresolved_positions"),
        "експорт": (д.get("export") or {}).get("status") or д.get("stop_code"),
        "секунди": д.get("_seconds"),
        "памет": {
            "включена": l.get("enabled"),
            "правила": l.get("rules_total"),
            "приети": l.get("rules_accepted"),
            "новоприети": l.get("newly_accepted"),
            "наблюдения": l.get("observations"),
            "хранилище": l.get("store_path"),
            "приложени": l.get("applied"),
            "грешка": l.get("error"),
        },
    }


def main() -> int:
    ап = argparse.ArgumentParser()
    ап.add_argument("--uchi", nargs="+", required=True, help="обучаващи проекти")
    ап.add_argument("--izpit", nargs="+", required=True, help="изпитвани проекти")
    ап.add_argument("--izhod", type=pathlib.Path, required=True)
    арг = ап.parse_args()
    арг.izhod.mkdir(parents=True, exist_ok=True)
    хранилище = арг.izhod / "pamet_obuchena"
    хранилище.mkdir(parents=True, exist_ok=True)
    файл = хранилище / "naucheno.json"
    # Разделителят е точка и запетая — вж. `full_path.py`.
    заключени = ";".join(арг.izpit)

    изход: dict = {"схема": "etap8-pamet/v1", "обучение": арг.uchi,
                   "изпит": арг.izpit, "заключени_семейства": заключени}

    # --- 1. ОБУЧЕНИЕ: програмата сама наблюдава, предлага, приема ---------- #
    print("=== ОБУЧЕНИЕ (памет ВКЛ, автоматичен цикъл ВКЛ) ===")
    изход["прогони_обучение"] = {}
    for проект in арг.uchi:
        среда = основна_среда()
        среда.update({"LION_REBAR_LEARNING": "on",
                      "LION_REBAR_AUTOCYCLE": "on",
                      "LION_REBAR_NAUCHENO": str(файл),
                      "LION_REBAR_NAUCHENO_ALLOW_MISSING": "1",
                      "LION_REBAR_LOCKED_FAMILIES": заключени})
        д = пусни(проект, арг.izhod / ("obuchenie_%s.json" % проект), среда)
        к = кратко(д)
        изход["прогони_обучение"][проект] = к
        print("  %-10s порта=%-5s правила=%-4s приети=%-4s новоприети=%s" % (
            проект, к["порта"], к["памет"]["правила"], к["памет"]["приети"],
            к["памет"]["новоприети"]))

    # --- 2. ЗАМРАЗЯВАНЕ --------------------------------------------------- #
    замразено = арг.izhod / "pamet_zamrazena"
    if замразено.exists():
        shutil.rmtree(замразено)
    shutil.copytree(хранилище, замразено)
    научено = (json.loads((замразено / "naucheno.json").read_text(encoding="utf-8"))
               if (замразено / "naucheno.json").is_file() else {})
    правила = научено.get("rules") or []
    изход["замразена_памет"] = {
        "път": str(замразено / "naucheno.json"),
        "общо": len(правила),
        "по_състояние": {},
        "приети": [],
    }
    for п in правила:
        с = str(п.get("state"))
        изход["замразена_памет"]["по_състояние"][с] = \
            изход["замразена_памет"]["по_състояние"].get(с, 0) + 1
        if с == "ACCEPTED":
            изход["замразена_памет"]["приети"].append({
                "rule_id": п.get("rule_id"), "role": п.get("role"),
                "signature": п.get("signature"),
                "families": [x.get("family") for x in (п.get("positives") or ())],
            })
    print("=== ЗАМРАЗЕНА ПАМЕТ: %d правила, %s ===" % (
        len(правила), изход["замразена_памет"]["по_състояние"]))
    for п in изход["замразена_памет"]["приети"]:
        print("   ПРИЕТО %s %s %s" % (п["rule_id"], п["role"],
                                      json.dumps(п["signature"], ensure_ascii=False)))

    # --- 3. Б и В върху изпитваните ---------------------------------------- #
    изход["Б"], изход["В"] = {}, {}
    for проект in арг.izpit:
        print("=== ИЗПИТ %s ===" % проект)
        среда_б = основна_среда()
        среда_б.update({"LION_REBAR_LEARNING": "off", "LION_REBAR_AUTOCYCLE": "off"})
        б = кратко(пусни(проект, арг.izhod / ("B_%s.json" % проект), среда_б))
        изход["Б"][проект] = б
        print("  Б (без памет): порта=%-5s блок=%-3d експорт=%-22s %.0f s" % (
            б["порта"], len(б["блокирани"]), str(б["експорт"])[:22], б["секунди"] or 0))

        среда_в = основна_среда()
        среда_в.update({"LION_REBAR_LEARNING": "on", "LION_REBAR_AUTOCYCLE": "off",
                        "LION_REBAR_NAUCHENO": str(замразено / "naucheno.json")})
        в = кратко(пусни(проект, арг.izhod / ("V_%s.json" % проект), среда_в))
        изход["В"][проект] = в
        print("  В (с памет)  : порта=%-5s блок=%-3d експорт=%-22s %.0f s" % (
            в["порта"], len(в["блокирани"]), str(в["експорт"])[:22], в["секунди"] or 0))

        само_б = sorted(set(б["блокирани"]) - set(в["блокирани"]))
        само_в = sorted(set(в["блокирани"]) - set(б["блокирани"]))
        изход.setdefault("разлика", {})[проект] = {
            "отпаднала_блокада_в_В": само_б,
            "нова_блокада_в_В": само_в,
            "порта_Б": б["порта"], "порта_В": в["порта"],
            "принос": (в["порта"] or 0) - (б["порта"] or 0),
            "приложени_правила": в["памет"]["приложени"],
        }
        print("  ПРИНОС НА ПАМЕТТА: %+d позиции | отпаднали блокади: %s | нови: %s" % (
            (в["порта"] or 0) - (б["порта"] or 0), само_б or "няма", само_в or "няма"))

    (арг.izhod / "EKSPERIMENT.json").write_text(
        json.dumps(изход, ensure_ascii=False, indent=1), encoding="utf-8")
    print("записано:", арг.izhod / "EKSPERIMENT.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
