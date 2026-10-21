# -*- coding: utf-8 -*-
"""Единен регистър на прогоните и на позициите по устойчив идентификатор.

Раздел 1 на заданието. Регистърът отговаря на два въпроса, на които обобщените
числа не отговарят:

1. **Кой прогон е дал този резултат** — run ID, версия на кода, отпечатъци на
   входа, настройки и отпечатък на паметта преди и след.
2. **Какво е станало с ВСЯКА позиция** — включително онези извън портата,
   защото точно там изчезват позиции незабелязано.

Устойчивият идентификатор е тройката
``(проект, SHA-256 на изходния DXF, handle на надписа)``. Handle-ът е устойчив в
рамките на една редакция на чертежа; SHA-256 разграничава редакциите.

Само чете.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys

sys.path.insert(0, r"C:\lr_exp\release")

СХЕМА = "lion-rebar-run-index/v1"
СЪСТОЯНИЯ = ("одобрена", "блокирана", "извън портата", "не е за действие")


def _отчет(копие: pathlib.Path) -> pathlib.Path | None:
    папка = копие / "output" / "04_REVIEW_REPORTS"
    ако = sorted(папка.rglob("*_geometry_batch.json")) if папка.is_dir() else []
    return ако[-1] if ако else None


def _одобрени(копие: pathlib.Path) -> set[str]:
    файл = копие / "working" / "approved_geometry_links.json"
    if not файл.is_file():
        return set()
    д = json.loads(файл.read_text(encoding="utf-8"))
    записи = д.get("links") if isinstance(д, dict) and "links" in д else д
    if isinstance(записи, dict):
        return {str(к) for к in записи}
    return {str(з.get("annotation_handle") or "") for з in записи or ()}


def _прогон(файл: pathlib.Path) -> dict | None:
    """Един прогон: самоличност плюс всяка позиция по устойчив ключ."""

    д = json.loads(файл.read_text(encoding="utf-8"))
    копие = pathlib.Path(str(д.get("copy") or ""))
    отчет = _отчет(копие) if копие else None
    if отчет is None:
        return None
    редове = json.loads(отчет.read_text(encoding="utf-8")).get("rows") or []
    одобрени = _одобрени(копие)
    порта = д.get("gate") or {}
    блокирани = {str(р.get("handle") or "")
                 for р in порта.get("blocked_positions") or ()}
    самоличност = (д.get("project") or {}).get("identity") or {}
    проект = (д.get("project") or {}).get("name") or файл.stem
    сха = str(самоличност.get("source_sha256") or "")

    позиции: dict[str, dict] = {}
    for ред in редове:
        ръчка = str(ред.get("annotation_handle") or "")
        if not ръчка:
            continue
        за_действие = str(ред.get("annotation_class") or "") == "ACTIONABLE"
        ако = ("не е за действие" if not за_действие
               else "одобрена" if ръчка in одобрени
               else "блокирана" if ръчка in блокирани
               else "извън портата")
        позиции["%s|%s|%s" % (проект, сха, ръчка)] = {
            "проект": проект, "sha256": сха, "надпис": ръчка,
            "текст": str(ред.get("annotation_text") or "")[:40],
            "състояние": ако,
            "форма": str(ред.get("master_shape_code") or ""),
            "вектор": [float(с) for с in (ред.get("production_segments_cm") or ())],
            "източник": str((ред.get("production_segment_evidence") or {}).get("source")
                            or ред.get("dimension_resolution_strategy") or ""),
            "присъда": str(ред.get("matcher_disposition") or ""),
        }

    return {
        "файл": str(файл),
        "проект": проект,
        "run": д.get("run") or {},
        "identity": самоличност,
        "engine": (д.get("project") or {}).get("engine_version"),
        "порта": порта.get("approved"),
        "позиции": позиции,
    }


def _индекс(папка: pathlib.Path) -> dict:
    прогони = {}
    for файл in sorted(папка.glob("*.json")):
        if файл.name.startswith("_"):
            continue
        п = _прогон(файл)
        if п is not None:
            прогони[п["проект"]] = п
    return прогони


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sega", required=True, type=pathlib.Path)
    parser.add_argument("--predi", type=pathlib.Path, default=None)
    parser.add_argument("--izhod", type=pathlib.Path,
                        default=pathlib.Path(
                            r"C:\lr_exp\rezultat\etap_6_dovurshvane\REGISTUR.json"))
    args = parser.parse_args()

    сега = _индекс(args.sega)
    преди = _индекс(args.predi) if args.predi else {}

    print("=== прогони ===")
    print("%-10s %-9s %-14s %-9s %s" % ("проект", "порта", "run_id", "engine", "памет"))
    for име, п in сега.items():
        run = п.get("run") or {}
        памет = ((run.get("store_before") or {}).get("sha256") or "")[:8] or "—"
        print("%-10s %-9s %-14s %-9s %s"
              % (име, п.get("порта"), (run.get("run_id") or "НЯМА")[:12],
                 п.get("engine") or "—", памет))

    липсва_самоличност = [и for и, п in сега.items() if not (п.get("run") or {}).get("run_id")]
    if липсва_самоличност:
        print("\nБЕЗ run_id (прогонът не е записал самоличност): %s"
              % ", ".join(липсва_самоличност))

    всички_сега = {}
    for п in сега.values():
        всички_сега.update(п["позиции"])
    print("\nпозиции общо: %d" % len(всички_сега))
    по_състояние = {}
    for з in всички_сега.values():
        по_състояние[з["състояние"]] = по_състояние.get(з["състояние"], 0) + 1
    for с in СЪСТОЯНИЯ:
        print("   %-18s %d" % (с, по_състояние.get(с, 0)))

    отчет = {"schema": СХЕМА, "прогони": {
        и: {к: v for к, v in п.items() if к != "позиции"} for и, п in сега.items()}}

    if преди:
        всички_преди = {}
        for п in преди.values():
            всички_преди.update(п["позиции"])
        изчезнали = sorted(set(всички_преди) - set(всички_сега))
        появили = sorted(set(всички_сега) - set(всички_преди))
        сменили = []
        for к in set(всички_сега) & set(всички_преди):
            а, б = всички_преди[к], всички_сега[к]
            if а["състояние"] != б["състояние"] or а["вектор"] != б["вектор"]:
                сменили.append({"ключ": к, "преди": а, "сега": б})
        print("\n=== срещу предишния прогон ===")
        print("   ТИХО ИЗЧЕЗНАЛИ позиции: %d" % len(изчезнали))
        for к in изчезнали[:15]:
            print("      %s  %s" % (к, всички_преди[к]["текст"]))
        print("   нови позиции: %d" % len(появили))
        print("   сменили състояние или вектор: %d" % len(сменили))
        преход = {}
        for с in сменили:
            ключ = "%s -> %s" % (с["преди"]["състояние"], с["сега"]["състояние"])
            преход[ключ] = преход.get(ключ, 0) + 1
        for к, в in sorted(преход.items(), key=lambda кв: -кв[1]):
            print("      %-38s %d" % (к, в))
        отчет["изчезнали"] = [{"ключ": к, **всички_преди[к]} for к in изчезнали]
        отчет["нови"] = [{"ключ": к, **всички_сега[к]} for к in появили]
        отчет["сменили"] = сменили

    отчет["позиции"] = всички_сега
    args.izhod.write_text(json.dumps(отчет, ensure_ascii=False, indent=1),
                          encoding="utf-8")
    print("\nзаписано:", args.izhod)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
