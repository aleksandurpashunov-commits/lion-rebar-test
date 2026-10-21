# -*- coding: utf-8 -*-
"""Сверява новите производствени вектори с ведомостта на проектанта.

Ведомостта НЕ участва в извличането: векторът идва от закръгляне на самата
геометрия към мрежата, а проверката на сбора ползва надписа на позицията. Затова
ведомостта е независим еталон за същото решение.

Сравнява се по (диаметър, обявена дължина) — единственият ключ, по който
ведомостта може да се свърже с извлечените позиции. Където ключът е нееднозначен,
това се казва изрично.

Само чете.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, r"C:\lr_exp\release")

ЧИСТИ = pathlib.Path(r"C:\lr_exp\proekti_chisti")


def _записи(проект: str):
    from lion_rebar.dxf.models import DxfEntityRecord

    ф = ЧИСТИ / проект / "working" / "extracted_entities.json"
    д = json.loads(ф.read_text(encoding="utf-8"))
    изход = []
    for з in (д.get("entities") or д):
        изход.append(DxfEntityRecord(
            handle=str(з.get("handle")),
            entity_type=str(з.get("entity_type") or ""),
            layer=str(з.get("layer") or ""),
            geometry=з.get("geometry") or {},
            text=з.get("text"),
        ))
    return изход


def _редове(проект: str, папка: pathlib.Path):
    д = json.loads((папка / ("%s.json" % проект)).read_text(encoding="utf-8"))
    о = sorted((pathlib.Path(str(д.get("copy") or "")) / "output" /
                "04_REVIEW_REPORTS").rglob("*_geometry_batch.json"))
    return json.loads(о[-1].read_text(encoding="utf-8")).get("rows") or [] if о else []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("проект")
    parser.add_argument("--progoni", type=pathlib.Path, required=True)
    parser.add_argument("--izhod", type=pathlib.Path, default=None)
    args = parser.parse_args()

    from lion_rebar.dxf.asd_schedule import read_schedule

    ведомост = read_schedule(_записи(args.проект))
    редове_вед = list(getattr(ведомост, "rows", None) or ведомост.get("rows") or ())
    print("редове във ведомостта: %d" % len(редове_вед))

    # ключ (диаметър, дължина) -> редове от ведомостта
    по_ключ: dict[tuple, list] = {}
    for р in редове_вед:
        д = р.get("diameter_mm") if isinstance(р, dict) else getattr(р, "diameter_mm", None)
        L = р.get("length_cm") if isinstance(р, dict) else getattr(р, "length_cm", None)
        if д is None or L is None:
            continue
        по_ключ.setdefault((round(float(д), 1), round(float(L), 1)), []).append(р)

    резултат = []
    редове = _редове(args.проект, args.progoni)
    for ред in редове:
        присъда = ред.get("grid_rounded_segments") or {}
        if str(присъда.get("status") or "") != "GRID_ROUNDED":
            continue
        if присъда.get("confirms_existing"):
            continue
        вектор = [float(с) for с in (ред.get("production_segments_cm") or ())]
        if not вектор:
            continue
        ключ = (round(float(ред.get("diameter_mm") or 0), 1),
                round(float(ред.get("declared_length_cm") or 0), 1))
        свои = по_ключ.get(ключ) or []
        детайли = []
        for р in свои:
            d = р.get("detail_cm") if isinstance(р, dict) else getattr(р, "detail_cm", None)
            n = р.get("position") if isinstance(р, dict) else getattr(р, "position", None)
            if d:
                детайли.append((n, [round(float(с), 1) for с in d]))
        присъда_сверка = "НЯМА РЕД ВЪВ ВЕДОМОСТТА"
        ако = None
        if детайли:
            съвпадащи = [(n, d) for n, d in детайли
                         if sorted(d) == sorted(round(с, 1) for с in вектор)]
            if съвпадащи:
                присъда_сверка = "СЪВПАДА"
                ако = съвпадащи[0]
            else:
                присъда_сверка = "РАЗЛИЧАВА СЕ"
                ако = детайли[0]
        elif свои:
            присъда_сверка = "ВЕДОМОСТТА НЯМА ДЕТАЙЛ"
        резултат.append({
            "надпис": str(ред.get("annotation_handle") or ""),
            "текст": str(ред.get("annotation_text") or "")[:30],
            "ключ": list(ключ), "вектор": вектор,
            "редове_с_този_ключ": len(свои),
            "ведомост": ако, "присъда": присъда_сверка,
        })

    брой = {}
    for р in резултат:
        брой[р["присъда"]] = брой.get(р["присъда"], 0) + 1
    print("\n=== сверка на НОВИТЕ вектори с ведомостта ===")
    for к, в in sorted(брой.items()):
        print("   %-26s %d" % (к, в))
    print()
    print("%-8s %-28s %-22s %-26s %s" % ("надпис", "текст", "вектор от геометрията",
                                          "ведомост (поз., детайл)", "присъда"))
    for р in резултат:
        print("%-8s %-28s %-22s %-26s %s" % (
            р["надпис"], р["текст"], str(р["вектор"]),
            str(р["ведомост"] or "—")[:26], р["присъда"]))

    if args.izhod:
        args.izhod.write_text(json.dumps(
            {"броеве": брой, "редове": резултат}, ensure_ascii=False, indent=1),
            encoding="utf-8")
        print("\nзаписано:", args.izhod)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
