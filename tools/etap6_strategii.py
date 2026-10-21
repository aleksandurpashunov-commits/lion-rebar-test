# -*- coding: utf-8 -*-
"""Кои позиции ползват досмятане по разликата и съвпадат ли с начертаното.

`dimension_resolver.A_E_TOTAL_MINUS_PRINTED` извежда един липсващ размер като
Δ = обявена обща дължина − сбора на отпечатаните. Геометричното съвпадение влиза
само като БОНУС в оценката (`exact_geometry_matches`), не като порта. Тук се мери
колко позиции минават през това и с каква грешка спрямо НАЧЕРТАНОТО.

За всяка позиция: производственият вектор срещу дължините, смятани направо от
върховете на кандидатите, подредени по реда на обхождане.

Само чете.
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import pathlib

ЧИСТИ = pathlib.Path(r"C:\lr_exp\proekti_chisti")


def _обекти(проект: str) -> dict[str, dict]:
    файл = ЧИСТИ / проект / "working" / "extracted_entities.json"
    д = json.loads(файл.read_text(encoding="utf-8"))
    записи = д.get("entities") or д.get("records") or д
    return {str(з.get("handle")): з for з in записи}


def _дължини(запис: dict) -> list[float]:
    г = запис.get("geometry") or {}
    точки = г.get("points")
    if точки:
        т = [(float(п[0]), float(п[1])) for п in точки]
        return [math.dist(т[и], т[и + 1]) for и in range(len(т) - 1)]
    за, до = г.get("start"), г.get("end")
    if за and до:
        return [math.dist((float(за[0]), float(за[1])),
                          (float(до[0]), float(до[1])))]
    return []


def _начертано(ред: dict, обекти: dict[str, dict]) -> list[float]:
    """Дължините на участъците по реда на обхождане на кандидатите."""

    изход: list[float] = []
    for h in (ред.get("candidate_handles") or ()):
        з = обекти.get(str(h))
        if з is None:
            return []
        изход.extend(_дължини(з))
    return изход


def _отчет(проект: str, папка: pathlib.Path) -> list[dict]:
    д = json.loads((папка / ("%s.json" % проект)).read_text(encoding="utf-8"))
    копие = pathlib.Path(str(д.get("copy") or ""))
    о = sorted((копие / "output" / "04_REVIEW_REPORTS").rglob("*_geometry_batch.json"))
    return json.loads(о[-1].read_text(encoding="utf-8")).get("rows") or [] if о else []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--progoni", type=pathlib.Path,
                        default=pathlib.Path(r"C:\lr_exp\rezultat\etap_5\progoni_B"))
    parser.add_argument("--izhod", type=pathlib.Path,
                        default=pathlib.Path(r"C:\lr_exp\rezultat\etap_6\STRATEGII.json"))
    args = parser.parse_args()

    по_стратегия = collections.Counter()
    находки: list[dict] = []
    for файл in sorted(args.progoni.glob("*.json")):
        if файл.name.startswith("_"):
            continue
        проект = файл.stem
        редове = _отчет(проект, args.progoni)
        if not редове:
            continue
        обекти = _обекти(проект)
        for ред in редове:
            стратегия = str(ред.get("dimension_resolution_strategy") or "")
            if not стратегия:
                continue
            по_стратегия[стратегия] += 1
            вектор = [float(с) for с in (ред.get("production_segments_cm") or ())]
            начертано = _начертано(ред, обекти)
            ако_същ_брой = len(вектор) == len(начертано) and bool(вектор)
            # ПО СТОЙНОСТИ: сравнени като множества, защото програмата
            # нормализира посоката на обхождане. Това е истинската находка.
            грешки = ([abs(в - н) for в, н in
                       zip(sorted(вектор), sorted(начертано))]
                      if ако_същ_брой else [])
            # ПО НАРЕДБА: същите стойности, друг ред — отделен въпрос, важен за
            # машинния изход, но не разминаване в размерите.
            наредба = (ако_същ_брой
                       and max((abs(в - н) for в, н in zip(вектор, начертано)),
                               default=0.0) > 0.51
                       and max(грешки, default=0.0) <= 0.51)
            находки.append({
                "проект": проект,
                "надпис": str(ред.get("annotation_handle") or ""),
                "текст": str(ред.get("annotation_text") or "")[:32],
                "форма": str(ред.get("master_shape_code") or ""),
                "стратегия": стратегия,
                "вектор": вектор,
                "начертано": [round(н, 3) for н in начертано],
                "макс_грешка": round(max(грешки), 3) if грешки else None,
                "само_наредба": наредба,
                "съпоставими": ако_същ_брой,
                "състояние": str(ред.get("batch_status") or "")[:24],
                "присъда": str(ред.get("matcher_disposition") or ""),
                "коти": list(ред.get("dimension_text_handles") or ()),
            })

    print("=== позиции по стратегия ===")
    for с, n in по_стратегия.most_common():
        print("   %-34s %d" % (с, n))

    досметнати = [н for н in находки if "TOTAL_MINUS_PRINTED" in н["стратегия"]
                  or "DELTA_SPLIT" in н["стратегия"]]
    print("\n=== досмятане по разликата: %d позиции ===" % len(досметнати))
    кофи = collections.Counter()
    for н in досметнати:
        г = н["макс_грешка"]
        кофи["несъпоставими" if г is None else
             "<=0.51" if г <= 0.51 else
             "<=2" if г <= 2 else
             "<=5" if г <= 5 else
             "<=20" if г <= 20 else ">20"] += 1
    for к in ("<=0.51", "<=2", "<=5", "<=20", ">20", "несъпоставими"):
        if кофи[к]:
            print("   макс. грешка спрямо начертаното %-14s %d" % (к, кофи[к]))

    само_ред = [н for н in досметнати if н.get("само_наредба")]
    print("   от тях: същите стойности, само друга НАРЕДБА: %d" % len(само_ред))

    лоши = sorted((н for н in досметнати
                   if н["макс_грешка"] is not None and н["макс_грешка"] > 5),
                  key=lambda н: -н["макс_грешка"])
    print("\n--- най-разминаващите се (%d) ---" % len(лоши))
    for н in лоши[:20]:
        print("   %-10s %-7s %-30s %-5s вектор=%-24s начертано=%-24s грешка=%.1f %s"
              % (н["проект"], н["надпис"], н["текст"], н["форма"],
                 str(н["вектор"])[:24], str(н["начертано"])[:24],
                 н["макс_грешка"], н["присъда"]))

    args.izhod.write_text(json.dumps(
        {"по_стратегия": dict(по_стратегия), "находки": находки},
        ensure_ascii=False, indent=1), encoding="utf-8")
    print("\nзаписано:", args.izhod)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
