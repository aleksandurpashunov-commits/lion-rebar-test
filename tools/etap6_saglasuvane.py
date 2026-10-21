# -*- coding: utf-8 -*-
"""Съгласува преминали, блокирани и необработени позиции — по самоличност.

Раздел 3 на етап 6 иска машинна проверка, че числата в отчетите се събират, и
обяснение на три несъответствия от етап 5:

1. позиции с кандидат повече от позициите „за действие";
2. `d-2`: 124 от 178 и 105 от 178, а блокираните са 50 и в двата режима;
3. „19 новоблокирани и 19 освободени" при нетен спад от 19.

Всяка позиция се отнася в ТОЧНО ЕДНА група, и групите трябва да покриват
всички редове. Разминаване се изброява поименно, не се обобщава.

Само чете.
"""
from __future__ import annotations

import argparse
import json
import pathlib

СЪСТОЯНИЯ = ("одобрена", "блокирана", "извън портата", "не е за действие")


def _прогон(файл: pathlib.Path) -> dict:
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
    порта = д.get("gate") or {}
    блокирани = {str(р.get("handle") or "")
                 for р in порта.get("blocked_positions") or ()}
    return {"редове": редове, "одобрени": одобрени, "блокирани": блокирани,
            "порта": порта, "анализ": д.get("analysis") or {}}


def _групи(п: dict) -> dict[str, set[str]]:
    групи = {и: set() for и in СЪСТОЯНИЯ}
    for ръчка, ред in п["редове"].items():
        за_действие = str(ред.get("annotation_class") or "") == "ACTIONABLE"
        if not за_действие:
            групи["не е за действие"].add(ръчка)
        elif ръчка in п["одобрени"]:
            групи["одобрена"].add(ръчка)
        elif ръчка in п["блокирани"]:
            групи["блокирана"].add(ръчка)
        else:
            групи["извън портата"].add(ръчка)
    return групи


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("папки", nargs="+", type=pathlib.Path)
    parser.add_argument("--proekt", default="")
    parser.add_argument("--izhod", type=pathlib.Path,
                        default=pathlib.Path(r"C:\lr_exp\rezultat\etap_6\SAGLASUVANE.json"))
    args = parser.parse_args()

    изход: dict[str, dict] = {}
    провали = 0
    for папка in args.папки:
        име_папка = папка.name
        изход[име_папка] = {}
        print("=== %s ===" % папка)
        print("%-10s %6s %7s %8s %7s %8s %7s %s"
              % ("проект", "редове", "действ.", "одобрени", "блок.", "извън",
                 "неакт.", "проверка"))
        for файл in sorted(папка.glob("*.json")):
            if файл.name.startswith("_"):
                continue
            if args.proekt and файл.stem != args.proekt:
                continue
            п = _прогон(файл)
            if not п["редове"]:
                continue
            групи = _групи(п)
            за_действие = sum(len(групи[и]) for и in СЪСТОЯНИЯ[:3])
            общо = sum(len(в) for в in групи.values())
            обявени_действ = (п["анализ"] or {}).get("actionable")
            обявени_одобр = (п["порта"] or {}).get("approved")

            бележки = []
            if len(п["редове"]) != общо:
                бележки.append("редовете не се покриват")
            if обявени_действ is not None and обявени_действ != за_действие:
                бележки.append("за действие: отчет %s, преброени %d"
                               % (обявени_действ, за_действие))
            if обявени_одобр is not None and обявени_одобр != len(групи["одобрена"]):
                бележки.append("одобрени: отчет %s, преброени %d"
                               % (обявени_одобр, len(групи["одобрена"])))
            # Уникалност на позициите: handle-ите са ключ на речника, затова
            # дублиране би се проявило като по-малко редове от отчета.
            if len(set(п["редове"])) != len(п["редове"]):
                бележки.append("дублирани handle-и")
            провали += len(бележки)
            print("%-10s %6d %7d %8d %7d %8d %7d %s"
                  % (файл.stem, len(п["редове"]), за_действие,
                     len(групи["одобрена"]), len(групи["блокирана"]),
                     len(групи["извън портата"]), len(групи["не е за действие"]),
                     "; ".join(бележки) or "ОК"))
            изход[име_папка][файл.stem] = {
                и: sorted(групи[и]) for и in СЪСТОЯНИЯ
            } | {"бележки": бележки}
        print()

    print("несъответствия общо:", провали)
    args.izhod.write_text(json.dumps(изход, ensure_ascii=False, indent=1),
                          encoding="utf-8")
    print("записано:", args.izhod)
    return 0 if провали == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
