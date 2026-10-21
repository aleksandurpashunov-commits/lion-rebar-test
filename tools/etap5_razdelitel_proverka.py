# -*- coding: utf-8 -*-
"""Независима проверка на различителя армировка/контур — от мен, не по отчета му.

Пуска ``evaluate_rebar_geometry`` върху извлечените обекти на всеки проект и
показва присъдата за слоевете, които СЕГА не минават речника
(``_rebar_geometry_layer_role`` дава ``None``). Точно това е разликата, която
раздел 3 иска да измери: намерен кандидат срещу приета принадлежност.

Показва отделно двата случая, които решават дали различителят е годен:

* `Тест-002 / s-Reinf` — истинска армировка, която речникът пропуска (274 обекта);
* `d-2 / …Surface reinforcement…` — истинска армировка, но обявяването ѝ
  ИЗМЕРЕНО влоши портата (124 -> 105). Тя не бива да се отваря само по име.

Само чете.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, r"C:\lr_exp\release")

from lion_rebar.dxf.geometry_candidates import _rebar_geometry_layer_role
from lion_rebar.dxf.rebar_geometry_evidence import (
    VERDICT_NOT_REBAR,
    VERDICT_REBAR,
    evaluate_rebar_geometry,
)

ЧИСТИ = pathlib.Path(r"C:\lr_exp\proekti_chisti")


def _обекти(проект: pathlib.Path) -> list[dict]:
    файл = проект / "working" / "extracted_entities.json"
    if not файл.is_file():
        return []
    д = json.loads(файл.read_text(encoding="utf-8"))
    return д.get("entities") or д.get("records") or (д if isinstance(д, list) else [])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("проекти", nargs="*")
    parser.add_argument("--izhod", type=pathlib.Path,
                        default=pathlib.Path(r"C:\lr_exp\rezultat\etap_5\RAZDELITEL.json"))
    args = parser.parse_args()

    имена = args.проекти or [п.name for п in sorted(ЧИСТИ.iterdir()) if п.is_dir()]
    изход: dict[str, dict] = {}
    for име in имена:
        обекти = _обекти(ЧИСТИ / име)
        if not обекти:
            print("%-10s няма извлечени обекти" % име)
            continue
        доказателство = evaluate_rebar_geometry(обекти)
        свои: dict[str, dict] = {}
        for слой, ред in (доказателство.layers or {}).items():
            роля = _rebar_geometry_layer_role(слой)
            свои[слой] = {
                "речник": роля,
                "присъда": ред.verdict,
                "обекти": len(getattr(ред, "objects", ()) or ()),
                "защо": (getattr(ред, "why", "") or "")[:160],
            }
        изход[име] = свои
        нови = {с: р for с, р in свои.items()
                if р["речник"] is None and р["присъда"] == VERDICT_REBAR}
        отказани = {с: р for с, р in свои.items()
                    if р["присъда"] == VERDICT_NOT_REBAR}
        print("%-10s слоеве с доказателство=%-3d | НОВИ (речникът пропуска, "
              "различителят приема)=%-2d | отказани=%d"
              % (име, len(свои), len(нови), len(отказани)))
        for с, р in sorted(нови.items()):
            print("      + %-46s обекти=%-4d %s" % (с[:46], р["обекти"], р["защо"][:60]))
        for с, р in sorted(отказани.items()):
            print("      - %-46s речник=%-22s %s"
                  % (с[:46], р["речник"], р["защо"][:52]))

    args.izhod.write_text(json.dumps(изход, ensure_ascii=False, indent=1),
                          encoding="utf-8")
    print("\nзаписано:", args.izhod)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
