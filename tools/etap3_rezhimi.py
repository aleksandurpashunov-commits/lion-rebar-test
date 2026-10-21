"""Режими А/Б/В при НОРМАЛНИТЕ настройки, върху ЧИСТИ копия.

За всеки целеви проект:

  1. обучение БЕЗ неговото семейство (честен пренос);
  2. режим Б — същият код, наученото ИЗКЛЮЧЕНО;
  3. режим В — същият код, наученото ВКЛЮЧЕНО след рестарт.

Изброените в кода имена на слоеве са ВКЛЮЧЕНИ и в двата режима — това са
нормалните настройки. Диагностичният ключ ``LION_REBAR_HARDCODED_ALIASES`` НЕ се
ползва тук.

Всеки прогон минава целия път: импорт → анализ → порта → Финален контрол →
Excel + DXF, върху копие на чистото копие.
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
РЕЗУЛТАТ = pathlib.Path(r"C:\lr_exp\rezultat\etap_3\rezhimi")
FULL_PATH = КОРЕН / ".claude" / "skills" / "lion-rebar-full-path" / "full_path.py"
OBUCHI = КОРЕН / "tools" / "etap2_obuchi.py"

СЕМЕЙСТВО = {
    "т-1": "т-1", "s-9": "s-9", "s-11": "s-11", "s-8": "s-8", "s-10": "s-10",
    "s-7": "s-7", "s-1": "s-1..s-6/а-1", "d-1": "d-1", "d-2": "d-2",
    "Тест-003": "тест-001/Тест-003", "Тест-002": "Тест-002",
    "тест-004": "тест-004/005", "тест-006": "тест-006",
}


def _pyexe() -> str:
    return os.environ.get("LION_REBAR_PY") or sys.executable


def _пусни(аргументи: list[str], среда: dict[str, str]) -> tuple[int, str]:
    цяла = dict(os.environ)
    цяла.update(среда)
    цяла["PYTHONIOENCODING"] = "utf-8"
    т0 = time.time()
    готово = subprocess.run([_pyexe(), *аргументи], capture_output=True,
                            text=True, encoding="utf-8", errors="replace",
                            env=цяла, cwd=str(КОРЕН))
    return готово.returncode, (готово.stdout or "") + (готово.stderr or "")


def _числа(п: pathlib.Path) -> dict:
    if not п.is_file():
        return {}
    д = json.loads(п.read_text(encoding="utf-8"))
    g, f, e = д.get("gate") or {}, д.get("final_control") or {}, д.get("export") or {}
    l = д.get("learning") or {}
    return {
        "status": д.get("status"), "stop_code": д.get("stop_code"),
        "actionable": g.get("actionable"), "gate": g.get("approved"),
        "blocked": len(g.get("blocked_positions") or ()),
        "blocked_positions": g.get("blocked_positions") or [],
        "final_control": f.get("approved_after"),
        "unresolved": f.get("unresolved_positions"),
        "export": e.get("status"), "excel_positions": e.get("excel_positions"),
        "dxf_markers": e.get("dxf_markers"),
        "seconds": (д.get("seconds") or {}).get("total"),
        "links_by_origin": f.get("links_by_origin"),
        "learning": {
            "enabled": l.get("learning"),
            "policy_in_use": l.get("policy_in_use"),
            "policy_changed": (l.get("bridge") or {}).get("policy_changed"),
            "provenance": (l.get("bridge") or {}).get("provenance") or [],
            "store_rules": (l.get("store") or {}).get("rule_count"),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Режими А/Б/В при нормални настройки")
    parser.add_argument("проекти", nargs="+")
    parser.add_argument("--json", type=pathlib.Path,
                        default=pathlib.Path(r"C:\lr_exp\rezultat\etap_3\REZHIMI_normalni.json"))
    args = parser.parse_args()

    РЕЗУЛТАТ.mkdir(parents=True, exist_ok=True)
    данни = pathlib.Path(r"C:\lr_exp\data")
    изход = {}

    for име in args.проекти:
        чисто = ЧИСТИ / име
        if not чисто.is_dir():
            print("няма чисто копие за", име)
            continue
        семейство = СЕМЕЙСТВО.get(име, име)
        хранилище = РЕЗУЛТАТ / ("naucheno_bez_%s.json" % име)

        print("=" * 76)
        print("ЦЕЛ %s   (обучение БЕЗ семейство %s)" % (име, семейство))
        код, _ = _пусни([str(OBUCHI), "--izklyuchi-semeystvo", семейство,
                         "--hranilishte", str(хранилище), "--otnovo"],
                        {"LION_REBAR_DATA_ROOT": str(данни)})
        if код != 0:
            print("   обучението се провали")
            continue
        правила = json.loads(хранилище.read_text(encoding="utf-8"))
        приети = [р for р in правила.get("rules") or () if р.get("state") == "ACCEPTED"]
        print("   хранилище: %d правила, %d приети" % (len(правила.get("rules") or ()), len(приети)))

        редове = {}
        for етикет, среда in (
            ("Б", {"LION_REBAR_LEARNING": "off"}),
            ("В", {}),
        ):
            отчет = РЕЗУЛТАТ / ("%s_%s.json" % (етикет, име))
            обкръжение = {"LION_REBAR_DATA_ROOT": str(данни),
                          "LION_REBAR_NAUCHENO": str(хранилище)}
            обкръжение.update(среда)
            код, изхода = _пусни(
                [str(FULL_PATH), str(чисто), "--discard-copy", "--json", str(отчет)],
                обкръжение)
            числа = _числа(отчет)
            редове[етикет] = числа
            print("   %s  порта=%-5s фин.к=%-5s експорт=%-20s блок=%-3s %s" % (
                етикет, числа.get("gate"), числа.get("final_control"),
                числа.get("export") or числа.get("stop_code"),
                числа.get("blocked"), числа.get("status")))
            обявени = ((числа.get("learning") or {}).get("policy_in_use") or {}).get(
                "declared_rebar_geometry_layers")
            if обявени:
                print("        обявени армировъчни слоеве: %s" % обявени)

        if "Б" in редове and "В" in редове:
            б, в = редове["Б"], редове["В"]
            разлика = {
                "порта": (в.get("gate") or 0) - (б.get("gate") or 0),
                "финален_контрол": (в.get("final_control") or 0) - (б.get("final_control") or 0),
                "блокирани": (в.get("blocked") or 0) - (б.get("blocked") or 0),
            }
            print("   Б -> В: порта %+d | финален контрол %+d | блокирани %+d"
                  % (разлика["порта"], разлика["финален_контрол"], разлика["блокирани"]))
            редове["разлика_Б_В"] = разлика
        изход[име] = редове

    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(изход, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\nзаписан:", args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
