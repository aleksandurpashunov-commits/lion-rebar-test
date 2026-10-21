"""ДЕМОНСТРАЦИЯ на автоматичния цикъл: програмата учи, докато работи.

Хранилището тръгва ПРАЗНО. Чертежите се обработват един след друг по нормалния
път (импорт → анализ → порта → Финален контрол → експорт). След всеки прогон се
отчита какво е имало хранилището ПРЕДИ него, какво е приложило и какво е научило.

Нищо не се пуска ръчно между прогоните: обучението става вътре в прогона
(``full_path`` → ``_автоматичен_цикъл`` → ``learning/auto_cycle.run_cycle``).
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


def _pyexe() -> str:
    return os.environ.get("LION_REBAR_PY") or sys.executable


def _състояние(път: pathlib.Path) -> dict:
    if not път.is_file():
        return {"rules": 0, "accepted": 0, "bytes": 0, "accepted_ids": []}
    д = json.loads(път.read_text(encoding="utf-8"))
    приети = [р for р in д.get("rules") or () if р.get("state") == "ACCEPTED"]
    return {"rules": len(д.get("rules") or ()), "accepted": len(приети),
            "bytes": път.stat().st_size,
            "accepted_ids": [р["rule_id"] for р in приети]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Автоматичният цикъл в действие")
    parser.add_argument("проекти", nargs="+")
    parser.add_argument("--hranilishte", type=pathlib.Path,
                        default=pathlib.Path(r"C:\lr_exp\rezultat\etap_3\cikal\naucheno.json"))
    parser.add_argument("--json", type=pathlib.Path,
                        default=pathlib.Path(r"C:\lr_exp\rezultat\etap_3\AVTOMATICHEN_CIKAL.json"))
    args = parser.parse_args()

    args.hranilishte.parent.mkdir(parents=True, exist_ok=True)
    for име in (args.hranilishte, args.hranilishte.parent / "nablyudeniya.jsonl"):
        if име.is_file():
            име.unlink()
    print("хранилището тръгва ПРАЗНО:", args.hranilishte)

    редове = []
    for и, име in enumerate(args.проекти, start=1):
        чисто = ЧИСТИ / име
        if not чисто.is_dir():
            print("няма чисто копие за", име)
            continue
        преди = _състояние(args.hranilishte)
        отчет = args.hranilishte.parent / ("run_%02d_%s.json" % (и, име))
        среда = dict(os.environ)
        среда.update({
            "LION_REBAR_DATA_ROOT": r"C:\lr_exp\data",
            "LION_REBAR_NAUCHENO": str(args.hranilishte),
            "PYTHONIOENCODING": "utf-8",
        })
        т0 = time.time()
        subprocess.run([_pyexe(), str(FULL_PATH), str(чисто), "--discard-copy",
                        "--json", str(отчет)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=среда, cwd=str(КОРЕН))
        след = _състояние(args.hranilishte)
        д = json.loads(отчет.read_text(encoding="utf-8")) if отчет.is_file() else {}
        обучение = д.get("learning") or {}
        цикъл = обучение.get("auto_cycle") or {}
        мост = обучение.get("bridge") or {}
        приложени = [str(р.get("rule_id")) for р in (мост.get("provenance") or [])]
        g = д.get("gate") or {}

        ред = {
            "ред": и, "проект": име,
            "хранилище_преди": преди, "хранилище_след": след,
            "приложени_правила": приложени,
            "цикълът_работи": цикъл.get("ran"),
            "наблюдения": цикъл.get("observations_total"),
            "новоприети": цикъл.get("accepted_now") or [],
            "неразпознати": len(цикъл.get("unrecognised") or []),
            "порта": g.get("approved"), "actionable": g.get("actionable"),
            "секунди": round(time.time() - т0, 1),
        }
        редове.append(ред)
        print("%d. %-10s преди: %2d правила/%d приети -> след: %2d/%d | "
              "приложени %d | наблюдения %s | неразпознати %s | порта %s/%s | %.0f s"
              % (и, име, преди["rules"], преди["accepted"], след["rules"],
                 след["accepted"], len(приложени), ред["наблюдения"],
                 ред["неразпознати"], ред["порта"], ред["actionable"],
                 ред["секунди"]))
        if приложени:
            for р in (мост.get("provenance") or []):
                print("       ползва %-34s %-20s %s" % (
                    р.get("rule_id"), р.get("role"), str(р.get("layer"))[:26]))

    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps({"редове": редове}, ensure_ascii=False, indent=1),
                         encoding="utf-8")
    print("\nзаписан:", args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
