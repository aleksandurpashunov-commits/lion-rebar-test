# -*- coding: utf-8 -*-
"""Одобрени позиции, при които уравнението за дължината НЕ се затваря.

Проверка за ГРЕШНО СИЛНА позиция. CLAUDE.md: една грешно силна позиция е
основание за неприемане, независимо от процента.

Мери се по СОБСТВЕНОТО правило на програмата — ``segments_match_total`` от
``engine/bend_allowance`` — а не по плосък допуск. Правият прът (F001) няма
огъвки и добавка не му се полага; огънатият има.

Изброява само ОДОБРЕНИТЕ (от ``working/approved_geometry_links.json`` в копието,
авторитетният списък), не всички редове.

Само чете.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, r"C:\lr_exp\release")

from lion_rebar.engine.bend_allowance import segments_match_total


def _отчет(копие: str) -> pathlib.Path | None:
    папка = pathlib.Path(копие) / "output" / "04_REVIEW_REPORTS"
    ако = sorted(папка.rglob("*_geometry_batch.json")) if папка.is_dir() else []
    return ако[-1] if ако else None


def _одобрени(копие: str) -> set[str]:
    файл = pathlib.Path(копие) / "working" / "approved_geometry_links.json"
    if not файл.is_file():
        return set()
    д = json.loads(файл.read_text(encoding="utf-8"))
    записи = д.get("links") if isinstance(д, dict) and "links" in д else д
    if isinstance(записи, dict):
        return {str(к) for к in записи}
    return {str(з.get("annotation_handle") or "") for з in записи or ()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("papki", nargs="+", type=pathlib.Path,
                        help="папки с обобщенията на прогоните")
    args = parser.parse_args()

    общо = 0
    for папка in args.papki:
        print("=== %s ===" % папка)
        for файл in sorted(папка.glob("*.json")):
            if файл.name.startswith("_"):
                continue
            д = json.loads(файл.read_text(encoding="utf-8"))
            копие = str(д.get("copy") or "")
            отчет = _отчет(копие) if копие else None
            if отчет is None:
                continue
            одобрени = _одобрени(копие)
            редове = json.loads(отчет.read_text(encoding="utf-8")).get("rows") or []
            лоши = []
            for ред in редове:
                ръчка = str(ред.get("annotation_handle") or "")
                if ръчка not in одобрени:
                    continue
                обявена = ред.get("declared_length_cm")
                измерена = ред.get("effective_geometry_length_cm")
                форма = str(ред.get("master_shape_code") or "")
                if not обявена or измерена is None or not форма:
                    continue
                if segments_match_total(форма, float(измерена), float(обявена)):
                    continue
                лоши.append({
                    "надпис": ръчка,
                    "текст": str(ред.get("annotation_text") or "")[:30],
                    "форма": форма,
                    "участъци": ред.get("segment_count"),
                    "обявена": float(обявена),
                    "измерена": round(float(измерена), 3),
                    "разлика": round(abs(float(обявена) - float(измерена)), 3),
                    "състояние": str(ред.get("batch_status") or "")[:22],
                    "слой": str(ред.get("candidate_layer") or "")[:20],
                })
            общо += len(лоши)
            print("%-10s одобрени=%-4d уравнението НЕ се затваря при: %d"
                  % (файл.stem, len(одобрени), len(лоши)))
            for л in лоши[:12]:
                print("     %-8s %-30s %-5s уч=%-3s обявена=%-7s измерена=%-9s "
                      "разлика=%-7s %s | %s"
                      % (л["надпис"], л["текст"], л["форма"], л["участъци"],
                         л["обявена"], л["измерена"], л["разлика"],
                         л["състояние"], л["слой"]))
    print("\nвсичко:", общо)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
