# -*- coding: utf-8 -*-
"""Независима проверка на новите позиции в `Тест-002` — не по отчета на програмата.

Намирането на слоя `s-Reinf` вдига портата от 0 на 231 от 329. По-голямото число
само по себе си НЕ доказва правилност, затова тук се мери друго:

1. **Съвпада ли надписаната дължина с измерената геометрия** за всяка от новите
   позиции. Проверката е на РАЗПРЕДЕЛЕНИЕТО: 231 случайни връзки не могат да
   дадат нулева разлика.
2. **Един обект — един надпис.** Ако слоят е бил отворен погрешно, един и същ
   обект ще се иска от много надписи.
3. **Носи ли чертежът своя ведомост** (вграден Excel в ``OLE2FRAME``) — ако да,
   числата на проектанта са независим еталон.

Само чете.
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys

sys.path.insert(0, r"C:\lr_exp\release")


def _отчет(копие: str) -> pathlib.Path | None:
    папка = pathlib.Path(копие) / "output" / "04_REVIEW_REPORTS"
    ако = sorted(папка.rglob("*_geometry_batch.json")) if папка.is_dir() else []
    return ако[-1] if ако else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("obobshtenie", type=pathlib.Path)
    parser.add_argument("--dxf", type=pathlib.Path, default=None)
    args = parser.parse_args()

    д = json.loads(args.obobshtenie.read_text(encoding="utf-8"))
    отчет = _отчет(str(д.get("copy") or ""))
    if отчет is None:
        print("няма пакетен отчет в копието")
        return 1
    редове = json.loads(отчет.read_text(encoding="utf-8")).get("rows") or []
    учене = д.get("learning") or {}
    намерени = tuple(учене.get("discovered_layers") or ())
    print("намерени слоеве:", намерени)
    print("редове в отчета:", len(редове))

    # --- 1. разпределение на разликата дължина срещу геометрия --------------
    def _кофа(р):
        if р is None:
            return "без число"
        р = abs(float(р))
        if р == 0:
            return "0.0"
        if р <= 0.5:
            return "<=0.5"
        if р <= 2.1:
            return "<=2.1"
        if р <= 10:
            return "<=10"
        return ">10"

    кофи = collections.Counter(_кофа(р.get("numeric_difference")) for р in редове)
    print("\nразлика надписана - измерена дължина, ВСИЧКИ редове:")
    for к in ("0.0", "<=0.5", "<=2.1", "<=10", ">10", "без число"):
        if кофи[к]:
            print("   %-10s %d" % (к, кофи[к]))

    на_слоя = [р for р in редове
               if str(р.get("candidate_layer") or "") in намерени]
    print("\nредове, чийто кандидат е НА НАМЕРЕНИЯ слой: %d" % len(на_слоя))
    свои = collections.Counter(_кофа(р.get("numeric_difference")) for р in на_слоя)
    for к, в in свои.most_common():
        print("   %-10s %d" % (к, в))

    # --- 2. един обект — един надпис ---------------------------------------
    искан_от = collections.Counter()
    for ред in на_слоя:
        for h in ред.get("candidate_handles") or ():
            искан_от[str(h)] += 1
    многократни = {h: n for h, n in искан_от.items() if n > 1}
    print("\nобекти на намерения слой, заявени от повече от един надпис: %d от %d"
          % (len(многократни), len(искан_от)))
    for h, n in sorted(многократни.items(), key=lambda кв: -кв[1])[:10]:
        print("   %s: %d надписа" % (h, n))

    # --- 3. има ли вградена ведомост ---------------------------------------
    if args.dxf and args.dxf.is_file():
        сурово = args.dxf.read_bytes()
        print("\nвградени обекти в DXF: OLE2FRAME=%d, следи от Excel=%d"
              % (сурово.count(b"OLE2FRAME"),
                 сурово.count(b"Excel.Sheet") + сурово.count(b"Biff8")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
