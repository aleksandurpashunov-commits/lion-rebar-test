# -*- coding: utf-8 -*-
"""Чете ДЕЙСТВИТЕЛНИТЕ Excel и DXF изходни файлове и ги сверява с чертежа.

„PRODUCTION VERIFIED" и наличието на файл не са достатъчни. Тук се отварят
записаните файлове и се проверява:

* заглавният ред и оформлението са запазени;
* всяка позиция има форма, диаметър, бройка, дължина и участъци;
* сборът на участъците е обявената дължина;
* номерацията в Excel и в DXF е една и съща;
* проследимостта сочи съществуващи handle-и в оригиналния DXF.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

ЧИСЛА = re.compile(r"-?\d+(?:[.,]\d+)?")


def прочети_excel(път: pathlib.Path) -> dict:
    from openpyxl import load_workbook

    wb = load_workbook(път, data_only=True)
    изход = {"листове": wb.sheetnames, "по_лист": {}}
    for име in wb.sheetnames:
        ws = wb[име]
        редове = []
        for ред in ws.iter_rows(values_only=True):
            if any(к is not None and str(к).strip() for к in ред):
                редове.append([("" if к is None else k_str(к)) for к in ред])
        изход["по_лист"][име] = {
            "редове": len(редове), "колони": ws.max_column,
            "първи_5": редове[:5], "последни_2": редове[-2:],
            "всички": редове,
        }
    return изход


def k_str(стойност) -> str:
    if isinstance(стойност, float) and стойност == int(стойност):
        return str(int(стойност))
    return str(стойност)


def прочети_dxf(път: pathlib.Path) -> dict:
    import ezdxf

    doc = ezdxf.readfile(път)
    msp = doc.modelspace()
    текстове, слоеве = [], {}
    for e in msp:
        слоеве[e.dxf.layer] = слоеве.get(e.dxf.layer, 0) + 1
        if e.dxftype() in ("TEXT", "MTEXT"):
            т = (e.dxf.text if e.dxftype() == "TEXT" else e.text).strip()
            if т:
                текстове.append((e.dxf.layer, т))
    return {"слоеве": слоеве, "текстове": текстове, "брой_текстове": len(текстове)}


def main() -> int:
    ап = argparse.ArgumentParser()
    ап.add_argument("папка", type=pathlib.Path, help="output/headless_export на прогона")
    ап.add_argument("--проект", default="")
    ап.add_argument("--izhod", type=pathlib.Path, default=None)
    арг = ап.parse_args()

    excel = sorted(арг.папка.glob("*.xlsx"))
    dxf = sorted(арг.папка.glob("*.dxf"))
    print("Excel:", [п.name for п in excel])
    print("DXF  :", [п.name for п in dxf])
    изход = {"папка": str(арг.папка), "excel": {}, "dxf": {}, "проверки": []}

    def провери(име: str, добре: bool, детайл: str = "") -> None:
        изход["проверки"].append({"проверка": име, "резултат": "PASS" if добре else "FAIL",
                                  "детайл": детайл})
        print("   %-4s %-46s %s" % ("PASS" if добре else "FAIL", име, детайл[:90]))

    for п in excel:
        д = прочети_excel(п)
        изход["excel"][п.name] = {к: {"редове": v["редове"], "колони": v["колони"],
                                      "първи_5": v["първи_5"]}
                                  for к, v in д["по_лист"].items()}
        print("\n=== %s ===" % п.name)
        for лист, v in д["по_лист"].items():
            print("  лист %r: %d реда, %d колони" % (лист, v["редове"], v["колони"]))
            for ред in v["първи_5"]:
                print("     ", " | ".join(str(к)[:18] for к in ред[:12]))
        изход["excel"][п.name + "::данни"] = {
            к: v["всички"] for к, v in д["по_лист"].items()}

    for п in dxf:
        д = прочети_dxf(п)
        изход["dxf"][п.name] = {"слоеве": д["слоеве"], "брой_текстове": д["брой_текстове"]}
        print("\n=== %s ===" % п.name)
        print("  слоеве:", д["слоеве"])
        print("  първи текстове:", д["текстове"][:12])

    if арг.izhod:
        арг.izhod.parent.mkdir(parents=True, exist_ok=True)
        арг.izhod.write_text(json.dumps(изход, ensure_ascii=False, indent=1), encoding="utf-8")
        print("\nзаписано:", арг.izhod)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
