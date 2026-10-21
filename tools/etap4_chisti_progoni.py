"""Пуска целия път върху ЧИСТИТЕ копия и ПАЗИ отчетите.

Нужно е, защото досегашните пакетни отчети идват от копия С предварителни
човешки одобрения. За честен регистър трябват отчети от копия без тях.

Обучението е ИЗКЛЮЧЕНО (``LION_REBAR_LEARNING=off``,
``LION_REBAR_AUTOCYCLE=off``): това е базовото състояние на програмата.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import time

КОРЕН = pathlib.Path(__file__).resolve().parents[1]
ЧИСТИ = pathlib.Path(r"C:\lr_exp\proekti_chisti")
FULL_PATH = КОРЕН / ".claude" / "skills" / "lion-rebar-full-path" / "full_path.py"


def main() -> int:
    parser = argparse.ArgumentParser(description="Целият път върху чисти копия")
    parser.add_argument("проекти", nargs="*")
    parser.add_argument("--izhod", type=pathlib.Path,
                        default=pathlib.Path(r"C:\lr_exp\rezultat\etap_4\chisti_progoni"))
    parser.add_argument("--uchi", action="store_true", help="с включено обучение")
    parser.add_argument("--hranilishte-shablon", default="",
                        help="шаблон за хранилище на проект, напр. ...\bez_{}.json")
    args = parser.parse_args()

    args.izhod.mkdir(parents=True, exist_ok=True)
    имена = args.проекти or [п.name for п in sorted(ЧИСТИ.iterdir()) if п.is_dir()]
    изход = {}
    for име in имена:
        проект = ЧИСТИ / име
        if not проект.is_dir():
            print("няма чисто копие:", име)
            continue
        отчет = args.izhod / ("%s.json" % име)
        среда = dict(os.environ)
        среда.update({"LION_REBAR_DATA_ROOT": r"C:\lr_exp\data",
                      "PYTHONIOENCODING": "utf-8"})
        if not args.uchi:
            среда["LION_REBAR_LEARNING"] = "off"
            среда["LION_REBAR_AUTOCYCLE"] = "off"
        else:
            # При финалното сравнение паметта е ЗАМРАЗЕНА: цикълът е изключен,
            # за да не се променя между режимите.
            среда["LION_REBAR_AUTOCYCLE"] = "off"
            if args.hranilishte_shablon:
                среда["LION_REBAR_NAUCHENO"] = args.hranilishte_shablon.format(име)
        т0 = time.time()
        готово = subprocess.run(
            [sys.executable, str(FULL_PATH), str(проект), "--json", str(отчет)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=среда, cwd=str(КОРЕН))
        д = json.loads(отчет.read_text(encoding="utf-8")) if отчет.is_file() else {}
        g = д.get("gate") or {}
        f = д.get("final_control") or {}
        e = д.get("export") or {}
        изход[име] = {
            "status": д.get("status"), "stop_code": д.get("stop_code"),
            "actionable": g.get("actionable"), "gate": g.get("approved"),
            # Истинският брой, не дължината на списъка: `full_path.py` реже
            # `blocked_positions` на първите 50, затова `len(...)` се НАСИЩА на
            # 50 и не може да отчете нищо по-лошо. Измерено на 29.09.2026:
            # сборът излизаше **197** вместо 249, защото `d-2` (54) и
            # `Тест-002` (98) се броеха по 50. Полето на портата носи цялото.
            "blocked": g.get("blocked"),
            "blocked_v_spisaka": len(g.get("blocked_positions") or ()),
            "final_control": f.get("approved_after"),
            "export": e.get("status"), "copy": д.get("copy"),
            "seconds": round(time.time() - т0, 1),
            "exit": готово.returncode,
        }
        print("%-10s порта=%-5s блок=%-4s фин.к=%-5s %-22s %.0f s" % (
            име, g.get("approved"), изход[име]["blocked"], f.get("approved_after"),
            (e.get("status") or д.get("stop_code") or "")[:22], изход[име]["seconds"]))
        if готово.returncode not in (0, 3) and not д:
            print("     изход %d; край на грешката: %s" % (
                готово.returncode, (готово.stderr or готово.stdout or "")[-300:]))

    (args.izhod / "_obobshtenie.json").write_text(
        json.dumps(изход, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\nзаписан:", args.izhod / "_obobshtenie.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
