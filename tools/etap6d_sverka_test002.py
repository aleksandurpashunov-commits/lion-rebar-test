# -*- coding: utf-8 -*-
"""Независима сверка на `Тест-002` с вградените ведомости на проектанта.

Заданието иска четири РАЗЛИЧНИ присъди, а не една:

1. вътрешните проверки са преминали;
2. резултатът е **независимо потвърден**;
3. резултатът остава **непроверен**;
4. има **противоречие**.

Данните, с които решението е получено, не могат едновременно да са независима
проверка на същото решение. Затова:

* **Ø и обявената дължина** идват от позиционния надпис в чертежа за програмата
  и от Excel таблицата за проектанта — два независими записа, сравними;
* **броят** също (надписът срещу колона „Брой");
* **номерът на позицията** е нееднозначен: 65 номера се повтарят между петте
  ведомости, затова еднозначни са само номерата, които са в ТОЧНО ЕДНА.

Само чете.
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import sys

sys.path.insert(0, r"C:\lr_exp\release")

ЧИСТИ = pathlib.Path(r"C:\lr_exp\proekti_chisti")


def _ведомости(dxf: pathlib.Path):
    from lion_rebar.dxf.ole_schedule import detect_schedule_table, read_embedded_documents

    изход = []
    for док in read_embedded_documents(dxf):
        книга = getattr(док, "workbook", None)
        if книга is None:
            continue
        for лист in книга.sheets:
            таблица = detect_schedule_table(лист)
            редове = list(getattr(таблица, "rows", ()) or ())
            if редове:
                изход.append((getattr(док, "handle", "?"),
                              getattr(таблица, "sheet_name", "") or "", редове))
    return изход


def _число(стойност):
    if стойност is None:
        return None
    if isinstance(стойност, (int, float)):
        return float(стойност)
    м = re.search(r"-?\d+(?:[.,]\d+)?", str(стойност))
    return float(м.group(0).replace(",", ".")) if м else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proekt", default="Тест-002")
    parser.add_argument("--progoni", type=pathlib.Path, required=True)
    parser.add_argument("--izhod", type=pathlib.Path, default=None)
    args = parser.parse_args()

    dxf = ЧИСТИ / args.proekt / "source" / "original.dxf"
    ведомости = _ведомости(dxf)
    print("прочетени ведомости: %d" % len(ведомости))

    # --- позициите на проектанта, събрани по (Ø, дължина) ------------------
    вед_по_ключ: dict[tuple, int] = collections.Counter()
    вед_редове = 0
    for handle, заглавие, редове in ведомости:
        print("   %-8s %-52s редове=%d" % (handle, str(заглавие)[:52], len(редове)))
        for р in редове:
            # Колоните са ИМЕНУВАНИ. Внимание: в „Дължина, сm" буквата „с" е
            # кирилска (U+0441) — търсенето е по начало на името, не по равенство.
            if not isinstance(р, dict):
                continue
            def _вземи(началo):
                for име, стойност in р.items():
                    if име.endswith("__source") or име.startswith("_"):
                        continue
                    if име.strip().lower().startswith(началo):
                        return стойност
                return None
            брой = _число(_вземи("брой"))
            диаметър = _число(_вземи("ø"))
            дължина = _число(_вземи("дължина"))
            позиция = _число(_вземи("поз"))
            if not (диаметър and дължина and брой and позиция):
                continue
            вед_редове += 1
            вед_по_ключ[(round(диаметър, 1), round(дължина, 1))] += int(брой)

    print("\nредове с годни числа: %d | различни (Ø, дължина): %d | общо броя: %d"
          % (вед_редове, len(вед_по_ключ), sum(вед_по_ключ.values())))

    # --- позициите на програмата -------------------------------------------
    д = json.loads((args.progoni / ("%s.json" % args.proekt)).read_text(encoding="utf-8"))
    копие = pathlib.Path(str(д.get("copy") or ""))
    о = sorted((копие / "output" / "04_REVIEW_REPORTS").rglob("*_geometry_batch.json"))
    редове = json.loads(о[-1].read_text(encoding="utf-8")).get("rows") or []
    връзки = копие / "working" / "approved_geometry_links.json"
    одобрени = set()
    if връзки.is_file():
        в = json.loads(връзки.read_text(encoding="utf-8"))
        зап = в.get("links") if isinstance(в, dict) and "links" in в else в
        одобрени = ({str(к) for к in зап} if isinstance(зап, dict)
                    else {str(з.get("annotation_handle") or "") for з in зап or ()})

    прог_по_ключ: dict[tuple, int] = collections.Counter()
    прог_позиции: dict[tuple, list] = collections.defaultdict(list)
    for ред in редове:
        ръчка = str(ред.get("annotation_handle") or "")
        if ръчка not in одобрени:
            continue
        д_мм = _число(ред.get("diameter_mm"))
        L = _число(ред.get("declared_length_cm"))
        n = _число(ред.get("annotation_count")) or 0
        if not (д_мм and L):
            continue
        ключ = (round(д_мм, 1), round(L, 1))
        прог_по_ключ[ключ] += int(n)
        прог_позиции[ключ].append(ръчка)

    # --- четирите присъди ---------------------------------------------------
    присъди = collections.Counter()
    подробно = []
    for ключ, брой in sorted(прог_по_ключ.items()):
        вед = вед_по_ключ.get(ключ)
        ако = len(прог_позиции[ключ])
        if вед is None:
            присъда = "НЕПРОВЕРЕН (няма такъв ред във ведомостта)"
        elif вед == брой:
            присъда = "НЕЗАВИСИМО ПОТВЪРДЕН"
        else:
            присъда = "ПРОТИВОРЕЧИЕ"
        присъди[присъда] += ако
        подробно.append({"ключ": list(ключ), "позиции": ако,
                         "брой_програма": брой, "брой_ведомост": вед,
                         "присъда": присъда})

    само_вед = {к: в for к, в in вед_по_ключ.items() if к not in прог_по_ключ}
    print("\n=== присъди върху ОДОБРЕНИТЕ позиции на програмата ===")
    for п, n in присъди.most_common():
        print("   %-46s %d позиции" % (п, n))
    print("   САМО ВЪВ ВЕДОМОСТТА (програмата няма такива): %d ключа, %d броя"
          % (len(само_вед), sum(само_вед.values())))

    print("\n--- ключове с противоречие (до 15) ---")
    for з in подробно:
        if з["присъда"] == "ПРОТИВОРЕЧИЕ":
            print("   Ø%-5s x %-8s позиции=%-4d програма=%-6d ведомост=%s"
                  % (з["ключ"][0], з["ключ"][1], з["позиции"],
                     з["брой_програма"], з["брой_ведомост"]))

    if args.izhod:
        args.izhod.write_text(json.dumps(
            {"ведомости": [{"handle": h, "заглавие": з, "редове": len(р)}
                           for h, з, р in ведомости],
             "присъди": dict(присъди), "подробно": подробно,
             "само_във_ведомостта": {str(к): в for к, в in само_вед.items()}},
            ensure_ascii=False, indent=1), encoding="utf-8")
        print("\nзаписано:", args.izhod)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
