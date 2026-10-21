# -*- coding: utf-8 -*-
"""Целият процес за нови форми БЕЗ интерфейс: извличане → изчертаване →
проверка за дублиране → предложение.

Същите функции, които вика Review Center, но подредени в един път, който може
да се пусне, повтори и измери. Изходът е пакет за оператора:

    NOVI_FORMI.json        присъдите, доказателствата и отказите
    <код>.svg              рисунка на всяко предложение

Нищо не се публикува и нищо не се записва в engine-а. Раждането остава
операторско действие; тук се подготвя решението му.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections import Counter, defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from lion_rebar.engine import shape_duplicates as дубл  # noqa: E402
from lion_rebar.engine.repository import EngineRepository  # noqa: E402
from lion_rebar.engine.shape_from_case import (  # noqa: E402
    ShapeFromCaseError, build_draft_shape, can_seed_new_shape, collect_cases,
    propose_shape,
)
from lion_rebar.export import proven_form as форма  # noqa: E402
from lion_rebar.utils.app_paths import AppPaths  # noqa: E402

ЧИСТИ = pathlib.Path(r"C:\lr_exp\proekti_chisti")
ПРОГОНИ = pathlib.Path(r"C:\lr_exp\data\full_path_runs")

#: Колко НЕЗАВИСИМИ случая и от колко РАЗЛИЧНИ чертежа трябват, за да се предложи
#: форма. Документът `docs/NOVI_FORMI_OT_SLUCHAI_BG.md` предлага N = 3, M = 2.
#: Днес програмата ражда и от ЕДИН случай — затова прагът е тук, изричен и
#: измерим, а не скрит в поведението.
НУЖНИ_СЛУЧАИ = 3
НУЖНИ_ЧЕРТЕЖИ = 2


def последен_отчет(проект: str) -> pathlib.Path | None:
    к = sorted(ПРОГОНИ.glob("%s_*/output/04_REVIEW_REPORTS/*/*_geometry_batch.json"
                            % проект))
    return к[-1] if к else None


def main() -> int:
    ап = argparse.ArgumentParser(description="Нови MASTER форми от отказани случаи")
    ап.add_argument("проекти", nargs="*")
    ап.add_argument("--izhod", type=pathlib.Path,
                    default=pathlib.Path(r"C:\lr_exp\rezultat\etap_9\novi_formi"))
    ап.add_argument("--nujni-sluchai", type=int, default=НУЖНИ_СЛУЧАИ)
    ап.add_argument("--nujni-chertezhi", type=int, default=НУЖНИ_ЧЕРТЕЖИ)
    арг = ап.parse_args()
    арг.izhod.mkdir(parents=True, exist_ok=True)

    имена = арг.проекти or [п.name for п in sorted(ЧИСТИ.iterdir()) if п.is_dir()]
    engine = EngineRepository(AppPaths.default().engine_registry).load_current()
    активни = [ф for ф in engine.shapes if ф.active and ф.segments]

    # --- 1. ИЗВЛИЧАНЕ ------------------------------------------------------ #
    брой = Counter()
    случаи_по_семейство: dict[tuple, list] = defaultdict(list)
    чертежи_по_семейство: dict[tuple, set] = defaultdict(set)
    for име in имена:
        отчет = последен_отчет(име)
        if отчет is None:
            брой["без отчет"] += 1
            continue
        данни = json.loads(отчет.read_text(encoding="utf-8"))
        редове = данни.get("rows") or []
        брой["редове"] += len(редове)
        годни = [ред for ред in редове if can_seed_new_shape(ред)]
        брой["годни за раждане"] += len(годни)
        # ``collect_cases`` сама групира по семейство и връща речник.
        for ключ, случаи in collect_cases(годни, project_name=име).items():
            случаи_по_семейство[ключ].extend(случаи)
            чертежи_по_семейство[ключ].add(име)

    print("редове: %d | годни за раждане: %d | семейства: %d"
          % (брой["редове"], брой["годни за раждане"], len(случаи_по_семейство)))

    # --- 2–4. ПРЕДЛАГАНЕ, ДУБЛИРАНЕ, ИЗЧЕРТАВАНЕ ------------------------- #
    изход: dict = {
        "схема": "etap9-novi-formi/v1",
        "engine": {"id": engine.engine_id, "version": engine.version,
                   "активни форми": len(активни)},
        "праг": {"случаи": арг.nujni_sluchai, "чертежи": арг.nujni_chertezhi},
        "преглед": dict(брой),
        "семейства": [],
    }
    номер = max(
        [int(ф.code[1:]) for ф in engine.shapes
         if str(ф.code or "").startswith("F") and ф.code[1:].isdigit()],
        default=9,
    )
    за_отчета = Counter()
    for ключ, случаи in sorted(случаи_по_семейство.items(), key=lambda д: -len(д[1])):
        чертежи = sorted(чертежи_по_семейство[ключ])
        запис: dict = {
            "семейство": [str(ч) for ч in ключ],
            "случаи": len(случаи),
            "чертежи": чертежи,
        }
        if len(случаи) < арг.nujni_sluchai or len(чертежи) < арг.nujni_chertezhi:
            запис["присъда"] = "ПОД ПРАГА"
            запис["причина"] = (
                "%d случая от %d чертежа при изискани %d от %d"
                % (len(случаи), len(чертежи), арг.nujni_sluchai, арг.nujni_chertezhi)
            )
            за_отчета["под прага"] += 1
            изход["семейства"].append(запис)
            continue
        try:
            предложение = propose_shape(случаи)
        except ShapeFromCaseError as exc:
            запис["присъда"] = "ОТКАЗ ПРИ ИЗВЛИЧАНЕТО"
            запис["причина"] = str(exc)
            за_отчета["отказ при извличането"] += 1
            изход["семейства"].append(запис)
            continue

        номер += 1
        код = "F%03d" % номер
        try:
            чернова = build_draft_shape(предложение, code=код, name="ПРЕДЛОЖЕНА %s" % код)
        except ShapeFromCaseError as exc:
            запис["присъда"] = "ОТКАЗ ПРИ СГЛОБЯВАНЕТО"
            запис["причина"] = str(exc)
            за_отчета["отказ при сглобяването"] += 1
            изход["семейства"].append(запис)
            continue

        присъда = дубл.провери(чернова, активни)
        запис.update({
            "код": код,
            "участъци": [str(с) for с in getattr(предложение, "pattern", "") or ""],
            "дублиране": присъда.as_dict(),
            "присъда": ("ДУБЛИКАТ" if присъда.е_дубликат
                        else "ЗА ПРЕГЛЕД" if присъда.вид != дубл.НОВА
                        else "НОВА ФОРМА"),
        })
        за_отчета[запис["присъда"]] += 1

        завои = дубл.завои_на_формата(чернова)
        дължини = [float(с) for с in (getattr(предложение, "example_values", None) or ())]
        if len(дължини) != len(завои) + 1:
            дължини = [100.0] * (len(завои) + 1)
        try:
            svg = форма.начертай(дължини, list(завои))
            (арг.izhod / ("%s.svg" % код)).write_text(svg, encoding="utf-8")
            запис["рисунка"] = "%s.svg" % код
        except Exception as exc:  # noqa: BLE001
            запис["рисунка"] = "НЕ СЕ РИСУВА: %s" % exc
        изход["семейства"].append(запис)

    изход["обобщение"] = dict(за_отчета)
    (арг.izhod / "NOVI_FORMI.json").write_text(
        json.dumps(изход, ensure_ascii=False, indent=1), encoding="utf-8")
    print("присъди:", dict(за_отчета) or "няма семейства над прага")
    for з in изход["семейства"]:
        print("  %-26s случаи=%-3d чертежи=%-2d %s"
              % (з["присъда"], з["случаи"], len(з["чертежи"]),
                 з.get("причина", з.get("код", ""))))
    print("записано:", арг.izhod / "NOVI_FORMI.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
