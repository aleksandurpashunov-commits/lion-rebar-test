# -*- coding: utf-8 -*-
"""Регистър по САМОЛИЧНОСТ за 86-те F007 „Есове“ и четирите куки.

Отговаря на F1.4 и F1.5 от roadmap-а: проследи ги окончателно, включително
съставните обекти, собствените коти, реда и посоките; провери 11-те случая с
предишен вектор ``[7,5; 25; 7,5]``; измери ПОВТОРЕНИЯТА отделно.

Повторение = същият прът, начертан на друго място или в копие на чертежа.
Разпознава се по еднаква начертана геометрия (закръглена), а не по текста на
надписа — 86-те носят различни бройки, но са един и същ прът.
"""
from __future__ import annotations

import argparse
import glob
import json
import pathlib
import sys
from collections import Counter, defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

ПРОГОНИ = pathlib.Path(r"C:\lr_exp\data\full_path_runs")


def последен_отчет(проект: str, преди: str = "") -> pathlib.Path | None:
    шаблон = str(ПРОГОНИ / ("%s_*/output/04_REVIEW_REPORTS/*/*_geometry_batch.json" % проект))
    к = sorted(glob.glob(шаблон))
    if преди:
        к = [п for п in к if преди in п]
    return pathlib.Path(к[-1]) if к else None


def main() -> int:
    ап = argparse.ArgumentParser()
    ап.add_argument("--izhod", type=pathlib.Path,
                    default=pathlib.Path(r"C:\lr_exp\rezultat\roadmap_completion\F1_F007_REGISTAR.json"))
    ап.add_argument("--progon", default="", help="част от името на папката на прогона")
    арг = ап.parse_args()

    проекти = ("d-1", "d-2", "s-10", "s-11", "s-7", "s-8", "s-9",
               "Тест-002", "Тест-003", "тест-004", "тест-006")
    редове: list[dict] = []
    по_геометрия: dict[tuple, list[str]] = defaultdict(list)
    брой = Counter()

    for проект in проекти:
        отчет = последен_отчет(проект, арг.progon)
        if отчет is None:
            брой["без отчет"] += 1
            continue
        данни = json.loads(отчет.read_text(encoding="utf-8"))
        for ред in (данни.get("rows") or []):
            форма_код = str(ред.get("master_shape_code") or "")
            текст = str(ред.get("annotation_text") or "")
            есове = форма_код == "F007" and "N6x40" in текст.replace(" ", "")
            кука = "N8x435" in текст.replace(" ", "") or "N8x1050" in текст.replace(" ", "")
            if not (есове or кука):
                continue
            пф = ред.get("proven_form") or {}
            вер = ред.get("chain_printed_dimensions") or {}
            начертани = tuple(round(float(с), 2) for с in (вер.get("drawn_lengths_cm") or ()))
            завои = tuple(round(float(о.get("turn_deg") or 0.0), 1)
                          for о in (вер.get("bends") or ()))
            # Ключът е САМАТА начертана геометрия: 86-те носят различни
            # бройки в надписа, но са един и същ прът. Проектът НЕ влиза в
            # ключа — иначе копие на чертежа би минало за друг прът.
            ключ = (начертани, завои)
            запис = {
                "проект": проект,
                "позиция": str(ред.get("annotation_handle") or ""),
                "текст": текст,
                "вид": "ЕСОВЕ" if есове else "КУКА",
                "форма": форма_код,
                "статус": str(ред.get("batch_status") or ""),
                "производствени": ред.get("production_segments_cm"),
                "ъгли": ред.get("canonical_turn_angles_deg"),
                "верижни_участъци": вер.get("segments_cm"),
                "начертани": list(начертани),
                "огъвки": [о.get("kind") for о in (вер.get("bends") or ())],
                "завои": list(завои),
                "форма_състояние": пф.get("state"),
                "форма_доказана": пф.get("proven"),
                "геометрия": вер.get("geometry_handles"),
                "надписи": вер.get("dimension_text_handles"),
            }
            редове.append(запис)
            по_геометрия[ключ].append("%s/%s" % (проект, запис["позиция"]))
            брой[запис["вид"]] += 1
            брой["състояние:%s" % (пф.get("state") or "НЯМА")] += 1
            if запис["производствени"] and list(запис["производствени"]) == [7.5, 25.0, 7.5]:
                брой["със стария вектор [7,5; 25; 7,5]"] += 1

    различни = {("начертани %s завои %s" % (list(к[0]), list(к[1]))): v
                for к, v in по_геометрия.items()}
    изход = {
        "схема": "roadmap-f007-registar/v1",
        "броеве": dict(брой),
        "различни_пръта": len(по_геометрия),
        "повторения": {к: len(v) for к, v in различни.items()},
        "редове": редове,
    }
    арг.izhod.parent.mkdir(parents=True, exist_ok=True)
    арг.izhod.write_text(json.dumps(изход, ensure_ascii=False, indent=1), encoding="utf-8")

    print("позиции: %d (Есове %d, куки %d)"
          % (len(редове), брой["ЕСОВЕ"], брой["КУКА"]))
    print("РАЗЛИЧНИ пръта по начертана геометрия: %d" % len(по_геометрия))
    for к, v in sorted(различни.items(), key=lambda д: -len(д[1])):
        по_проект = Counter(п.split("/")[0] for п in v)
        print("   %-46s × %-3d  %s" % (к[:46], len(v), dict(по_проект)))
    for к, v in sorted(брой.items()):
        if к.startswith("състояние:") or "вектор" in к:
            print("   %-46s %d" % (к, v))
    print("записано:", арг.izhod)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
