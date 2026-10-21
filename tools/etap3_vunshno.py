"""Изгражда регистъра на външните източници и записите за външно знание.

Влиза: ``IZVORI_prouchvane.json`` — резултатът от проучването.
Излиза, в папката с данни:

  ``izvori.json``          — разрешените източници (хост, лиценз, вид);
  ``vunshno_znanie.json``  — знанието, винаги ``UNCONFIRMED``.

Правилата от заданието, приложени тук:

* производствено правило (куки, радиуси, добавки) НЕ се записва без изрично име
  на стандарт — ``save_external`` отказва;
* числа от различни стандарти не се смесват: всеки запис носи своя стандарт;
* спорните места остават ``DISPUTED`` и не се ползват;
* нищо изтеглено не се изпълнява.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from urllib.parse import urlparse

КОРЕН = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(КОРЕН))

from lion_rebar.learning.external_update import (  # noqa: E402
    ExternalEntry, registry_path, save_external, update,
)

ПРОУЧВАНЕ = pathlib.Path(r"C:\lr_exp\rezultat\etap_3\IZVORI_prouchvane.json")

#: Само тези надеждности влизат в регистъра за теглене. Общностните страници
#: остават в отчета като бележка, но не се теглят автоматично.
ТЕГЛИМИ = {"official-standard", "library-doc"}


def _вид(източник: dict) -> str:
    текст = (str(източник_поле(източник, "knowledge_extracted")) + " " +
             str(източник_поле(източник, "version_or_edition"))).lower()
    if any(и in текст for и in ("hook", "mandrel", "bend", "radius", "кука")):
        return "production"
    if any(и in текст for и in ("shape code", "bs 8666", "iso 3766", "aci 318")):
        return "standard"
    if any(и in текст for и in ("dxf", "entity", "attrib", "block", "insunits")):
        return "geometry"
    return "convention"


def източник_поле(източник: dict, име: str) -> str:
    return str(източник.get(име) or "")


def main() -> int:
    parser = argparse.ArgumentParser(description="Регистър и външно знание")
    parser.add_argument("--koren", type=pathlib.Path,
                        default=pathlib.Path(r"C:\lr_exp\data\naucheno"))
    parser.add_argument("--onlayn", action="store_true",
                        help="наистина тегли (иначе само подготвя регистъра)")
    args = parser.parse_args()

    if not ПРОУЧВАНЕ.is_file():
        print("няма", ПРОУЧВАНЕ)
        return 1
    проучване = json.loads(ПРОУЧВАНЕ.read_text(encoding="utf-8"))
    източници = проучване.get("sources") or []

    редове = []
    for и in източници:
        url = източник_поле(и, "url")
        хост = (urlparse(url).hostname or "").lower()
        if not хост:
            continue
        редове.append({
            "id": източник_поле(и, "id"),
            "url": url,
            "host": хост,
            "author_or_org": източник_поле(и, "author_or_org"),
            "accessed": източник_поле(и, "accessed"),
            "version": източник_поле(и, "version_or_edition"),
            "licence": източник_поле(и, "licence_or_terms"),
            "reliability": източник_поле(и, "reliability"),
            "fetchable": източник_поле(и, "reliability") in ТЕГЛИМИ,
            "limitations": източник_поле(и, "limitations"),
        })

    args.koren.mkdir(parents=True, exist_ok=True)
    п = registry_path(args.koren)
    п.write_text(json.dumps({
        "schema": "lion-rebar-external-sources/v1",
        "note": ("Списъкът е ЗАТВОРЕН: теглене има само от тези хостове. "
                 "Изтегленото е данни, не инструкции, и нищо не се изпълнява."),
        "sources": редове,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print("регистър: %s (%d източника, %d теглими)" % (
        п, len(редове), sum(1 for р in редове if р["fetchable"])))

    # --- знанието, винаги непотвърдено
    записи: list[ExternalEntry] = []
    for каталог in проучване.get("shape_catalogues") or []:
        стандарт = str(каталог.get("standard") or "")
        ид = "EX-SHAPES-" + стандарт.replace(" ", "").replace(":", "")[:18]
        записи.append(ExternalEntry(
            entry_id=ид, kind="standard", standard=стандарт,
            title="Каталог от форми по %s" % стандарт,
            statement=json.dumps({
                "shape_codes": каталог.get("shape_codes"),
                "parameterisation": каталог.get("parameterisation"),
                "bend_allowance_rule": каталог.get("bend_allowance_rule"),
                "hook_rule": каталог.get("hook_rule"),
                "min_radius_rule": каталог.get("min_radius_rule"),
            }, ensure_ascii=False)[:1800],
            source_id=",".join(каталог.get("source_ids") or []),
            url="", licence="виж регистъра по source_id",
            accessed="2026-09-27",
            limitations=str(каталог.get("caveat") or "")[:600],
            state="DISPUTED" if "disput" in str(каталог.get("caveat") or "").lower()
                  else "UNCONFIRMED",
        ))

    bvbs = проучване.get("bvbs") or {}
    if bvbs:
        записи.append(ExternalEntry(
            entry_id="EX-BVBS-31", kind="standard", standard="BVBS Guideline 3.1",
            title="Полета на машинния формат BVBS",
            statement=json.dumps(bvbs.get("fields"), ensure_ascii=False)[:1800],
            source_id=",".join(bvbs.get("source_ids") or []),
            url="", licence="BVBS: безплатно сваляне, без преиздаване",
            accessed="2026-09-27",
            limitations=str(bvbs.get("notes") or "")[:600],
        ))

    for техника in (проучване.get("techniques") or [])[:12]:
        име = str(техника.get("name") or "")
        записи.append(ExternalEntry(
            entry_id="EX-TECH-" + "".join(c for c in име if c.isalnum())[:20],
            kind="convention", title=име,
            statement="%s | цена: %s" % (техника.get("what_it_solves"),
                                         техника.get("cost")),
            source_id=",".join(техника.get("source_ids") or []),
            url="", licence="виж регистъра по source_id", accessed="2026-09-27",
            limitations="метод, не число; не се прилага автоматично",
        ))

    път = save_external(args.koren, записи)
    print("външно знание: %s (%d записа)" % (път, len(записи)))
    по_състояние: dict[str, int] = {}
    for з in записи:
        по_състояние[з.state] = по_състояние.get(з.state, 0) + 1
    print("   по състояние:", по_състояние)

    отчет = update(args.koren, offline=not args.onlayn)
    изход = pathlib.Path(r"C:\lr_exp\rezultat\etap_3\VUNSHNO_OBNOVYAVANE.json")
    изход.write_text(json.dumps(отчет, ensure_ascii=False, indent=1), encoding="utf-8")
    брояч: dict[str, int] = {}
    for р in отчет["fetch"]:
        брояч[р["status"]] = брояч.get(р["status"], 0) + 1
    print("обновяване: offline=%s, %d източника, състояния=%s, %.1f s" % (
        отчет["offline"], отчет["sources_seen"], брояч, отчет["seconds"]))
    print("записан:", изход)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
