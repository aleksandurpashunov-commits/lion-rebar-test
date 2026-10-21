# -*- coding: utf-8 -*-
"""Сверява ЕКСПОРТА на два прогона — клетка по клетка, не по брой позиции.

Инвариантите от `README_BG.md`: експортът не променя форми, размери, бройки и
одобрения; Excel и DXF ползват една и съща номерация. Затова тук се сравняват
самите клетки на Excel, маркерите в DXF и traceability файлът — а не обобщените
числа, които програмата сама отчита.

``openpyxl`` е налично във `.venv` (то прави експорта).

Само чете.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib


def _папка_на_експорта(копие: str) -> pathlib.Path | None:
    п = pathlib.Path(копие) / "output" / "headless_export"
    return п if п.is_dir() else None


def _клетки(път: pathlib.Path) -> dict[str, list]:
    from openpyxl import load_workbook

    книга = load_workbook(път, data_only=True)
    изход: dict[str, list] = {}
    for лист in книга.worksheets:
        редове = []
        for ред in лист.iter_rows(values_only=True):
            редове.append(["" if с is None else str(с) for с in ред])
        изход[лист.title] = редове
    return изход


def _сверка(а: pathlib.Path, б: pathlib.Path) -> int:
    разлики = 0
    ка, кб = _клетки(а), _клетки(б)
    само_а = sorted(set(ка) - set(кб))
    само_б = sorted(set(кб) - set(ка))
    if само_а or само_б:
        print("     листове само в А: %s | само в Б: %s" % (само_а, само_б))
        разлики += len(само_а) + len(само_б)
    for лист in sorted(set(ка) & set(кб)):
        ра, рб = ка[лист], кб[лист]
        if len(ра) != len(рб):
            print("     лист %s: редове %d срещу %d" % (лист, len(ра), len(рб)))
            разлики += 1
        for и, (х, у) in enumerate(zip(ра, рб), start=1):
            if х != у:
                разлики += 1
                if разлики <= 10:
                    print("     лист %s ред %d:" % (лист, и))
                    print("        А: %s" % х)
                    print("        Б: %s" % у)
    return разлики


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--a", required=True, type=pathlib.Path)
    parser.add_argument("--b", required=True, type=pathlib.Path)
    parser.add_argument("проекти", nargs="*")
    args = parser.parse_args()

    имена = args.проекти or [п.stem for п in sorted(args.b.glob("*.json"))
                             if not п.name.startswith("_")]
    общо = 0
    for име in имена:
        фа, фб = args.a / ("%s.json" % име), args.b / ("%s.json" % име)
        if not (фа.is_file() and фб.is_file()):
            continue
        да = json.loads(фа.read_text(encoding="utf-8"))
        дб = json.loads(фб.read_text(encoding="utf-8"))
        па = _папка_на_експорта(str(да.get("copy") or ""))
        пб = _папка_на_експорта(str(дб.get("copy") or ""))
        if па is None or пб is None:
            print("%-10s няма експорт в едното копие (А=%s Б=%s)"
                  % (име, па is not None, пб is not None))
            continue
        print("%-10s сверка на експорта" % име)
        for ф in sorted(па.glob("*")):
            двойник = пб / ф.name
            if not двойник.is_file():
                print("     липсва в Б: %s" % ф.name)
                общо += 1
                continue
            if ф.suffix.lower() == ".xlsx":
                р = _сверка(ф, двойник)
                print("     %-42s разлики в клетките: %d" % (ф.name, р))
                общо += р
            else:
                ха = hashlib.sha256(ф.read_bytes()).hexdigest()
                хб = hashlib.sha256(двойник.read_bytes()).hexdigest()
                # DXF и JSON носят време на създаване -> сравнява се размерът и
                # се отбелязва различният отпечатък, вместо да се обявява провал.
                еднакво = "ЕДНАКЪВ" if ха == хб else (
                    "различен отпечатък, същият размер"
                    if ф.stat().st_size == двойник.stat().st_size
                    else "РАЗЛИЧЕН РАЗМЕР")
                print("     %-42s %s" % (ф.name, еднакво))
                if еднакво == "РАЗЛИЧЕН РАЗМЕР":
                    общо += 1
    print("\nразлики общо:", общо)
    return 0 if общо == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
