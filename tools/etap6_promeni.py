# -*- coding: utf-8 -*-
"""Кои позиции влизат и излизат между два прогона, и с какъв вектор.

Заданието: допустимо е правилна защита да намали броя, недопустимо е това да е
скрито. Затова всяка променена позиция се показва поименно, със стария и новия
производствен вектор и с начертаната геометрия — за да е видимо дали загубената
позиция е носела ИЗМИСЛЕН вектор, или е загубено вярно решение.

Източник за одобрението е авторитетният списък в копието
(``working/approved_geometry_links.json``), не обобщените числа.

Само чете.
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib

ЧИСТИ = pathlib.Path(r"C:\lr_exp\proekti_chisti")


def _обекти(проект: str) -> dict[str, dict]:
    ф = ЧИСТИ / проект / "working" / "extracted_entities.json"
    if not ф.is_file():
        return {}
    д = json.loads(ф.read_text(encoding="utf-8"))
    return {str(з.get("handle")): з for з in (д.get("entities") or д)}


def _дължини(з: dict) -> list[float]:
    г = з.get("geometry") or {}
    т = г.get("points")
    if т:
        т = [(float(п[0]), float(п[1])) for п in т]
        return [round(math.dist(т[и], т[и + 1]), 1) for и in range(len(т) - 1)]
    за, до = г.get("start"), г.get("end")
    if за and до:
        return [round(math.dist((float(за[0]), float(за[1])),
                                (float(до[0]), float(до[1]))), 1)]
    return []


def _начертано(ред: dict, обекти: dict[str, dict]) -> list[float]:
    изход: list[float] = []
    for h in (ред.get("candidate_handles") or ()):
        з = обекти.get(str(h))
        if з is None:
            return []
        изход.extend(_дължини(з))
    return изход


def _прогон(файл: pathlib.Path):
    д = json.loads(файл.read_text(encoding="utf-8"))
    копие = pathlib.Path(str(д.get("copy") or ""))
    отчети = sorted((копие / "output" / "04_REVIEW_REPORTS")
                    .rglob("*_geometry_batch.json"))
    редове = {}
    if отчети:
        for ред in json.loads(отчети[-1].read_text(encoding="utf-8")).get("rows") or ():
            редове[str(ред.get("annotation_handle") or "")] = ред
    връзки = копие / "working" / "approved_geometry_links.json"
    одобрени: set[str] = set()
    if връзки.is_file():
        в = json.loads(връзки.read_text(encoding="utf-8"))
        записи = в.get("links") if isinstance(в, dict) and "links" in в else в
        одобрени = ({str(к) for к in записи} if isinstance(записи, dict)
                    else {str(з.get("annotation_handle") or "") for з in записи or ()})
    return редове, одобрени


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--predi", required=True, type=pathlib.Path)
    parser.add_argument("--sega", required=True, type=pathlib.Path)
    parser.add_argument("--izhod", type=pathlib.Path,
                        default=pathlib.Path(r"C:\lr_exp\rezultat\etap_6\PROMENI.json"))
    args = parser.parse_args()

    всички: list[dict] = []
    for файл in sorted(args.sega.glob("*.json")):
        if файл.name.startswith("_"):
            continue
        проект = файл.stem
        стар_файл = args.predi / файл.name
        if not стар_файл.is_file():
            continue
        нови_редове, нови_одобрени = _прогон(файл)
        стари_редове, стари_одобрени = _прогон(стар_файл)
        if not нови_редове or not стари_редове:
            continue
        обекти = _обекти(проект)
        for ръчка in sorted(set(стари_редове) | set(нови_редове)):
            беше, стана = ръчка in стари_одобрени, ръчка in нови_одобрени
            стар = стари_редове.get(ръчка) or {}
            нов = нови_редове.get(ръчка) or {}
            ст_в = [float(с) for с in (стар.get("production_segments_cm") or ())]
            нв_в = [float(с) for с in (нов.get("production_segments_cm") or ())]
            if беше == стана and ст_в == нв_в:
                continue
            всички.append({
                "проект": проект, "надпис": ръчка,
                "текст": str((нов or стар).get("annotation_text") or "")[:30],
                "форма": str((нов or стар).get("master_shape_code") or ""),
                "обявена": (нов or стар).get("declared_length_cm"),
                "беше_одобрена": беше, "е_одобрена": стана,
                "стар_вектор": ст_в, "нов_вектор": нв_в,
                "начертано": _начертано(нов or стар, обекти),
                "стара_стратегия": str(стар.get("dimension_resolution_strategy") or ""),
                "нова_стратегия": str(нов.get("dimension_resolution_strategy") or ""),
                "старо_състояние": str(стар.get("batch_status") or "")[:22],
                "ново_състояние": str(нов.get("batch_status") or "")[:22],
            })

    загубени = [в for в in всички if в["беше_одобрена"] and not в["е_одобрена"]]
    спечелени = [в for в in всички if not в["беше_одобрена"] and в["е_одобрена"]]
    сменен_вектор = [в for в in всички
                     if в["беше_одобрена"] and в["е_одобрена"]
                     and в["стар_вектор"] != в["нов_вектор"]]

    def _покажи(име, списък):
        print("\n=== %s: %d ===" % (име, len(списък)))
        for в in списък[:30]:
            print("   %-10s %-8s %-30s %-5s L=%-7s" % (
                в["проект"], в["надпис"], в["текст"], в["форма"], в["обявена"]))
            print("        стар вектор=%-26s нов вектор=%-26s начертано=%s"
                  % (str(в["стар_вектор"])[:26], str(в["нов_вектор"])[:26],
                     str(в["начертано"])[:34]))
            print("        стратегия %s -> %s | %s -> %s"
                  % (в["стара_стратегия"] or "—", в["нова_стратегия"] or "—",
                     в["старо_състояние"], в["ново_състояние"]))

    _покажи("ЗАГУБЕНИ одобрения", загубени)
    _покажи("СПЕЧЕЛЕНИ одобрения", спечелени)
    _покажи("СМЕНЕН ВЕКТОР при запазено одобрение", сменен_вектор)

    args.izhod.write_text(json.dumps({
        "загубени": загубени, "спечелени": спечелени,
        "сменен_вектор": сменен_вектор,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\nзаписано:", args.izhod)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
