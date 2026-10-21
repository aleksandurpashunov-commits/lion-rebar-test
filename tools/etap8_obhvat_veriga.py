# -*- coding: utf-8 -*-
"""Обхватът на верижното правило върху ЦЕЛИЯ корпус, без да се пипа поведението.

Чете вече записаните пакетни отчети от базовата линия и прилага
``chain_printed_dimensions`` към всеки ред. Отговаря на три въпроса:

* на колко позиции правилото изобщо се произнася;
* колко от тях СЪВПАДАТ с вече доказан по друг път вектор (потвърждение);
* колко му ПРОТИВОРЕЧАТ (това са случаите за човек, не за автоматика).

Нищо не се записва обратно в проектите.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from lion_rebar.dxf import chain_printed_dimensions as вер  # noqa: E402
from lion_rebar.dxf.models import DxfEntityRecord  # noqa: E402

ЧИСТИ = pathlib.Path(r"C:\lr_exp\proekti_chisti")
ПРОГОНИ = pathlib.Path(r"C:\lr_exp\data\full_path_runs")


def обекти(проект: str) -> list[DxfEntityRecord]:
    път = ЧИСТИ / проект / "working" / "extracted_entities.json"
    данни = json.loads(път.read_text(encoding="utf-8"))
    полета = DxfEntityRecord.__dataclass_fields__
    return [DxfEntityRecord(**{k: v for k, v in e.items() if k in полета})
            for e in данни["entities"]]


def последен_отчет(проект: str) -> pathlib.Path | None:
    кандидати = sorted(ПРОГОНИ.glob("%s_*/output/04_REVIEW_REPORTS/*/*_geometry_batch.json"
                                     % проект))
    return кандидати[-1] if кандидати else None


def редове(път: pathlib.Path) -> list[dict]:
    данни = json.loads(път.read_text(encoding="utf-8"))
    for ключ in ("rows", "annotations", "results", "entries"):
        ако = данни.get(ключ)
        if isinstance(ако, list):
            return ако
    return []


def main() -> int:
    ап = argparse.ArgumentParser()
    ап.add_argument("проекти", nargs="*")
    ап.add_argument("--izhod", type=pathlib.Path,
                    default=pathlib.Path(r"C:\lr_exp\rezultat\etap_8\OBHVAT_VERIGA.json"))
    ап.add_argument("--obhvat", type=float, default=None,
                    help="обхват на собствената кота, в височини на текста")
    ап.add_argument("--izlishni", type=int, default=None,
                    help="колко излишни числови надписа се допускат")
    арг = ап.parse_args()
    if арг.obhvat is not None:
        вер.ОБХВАТ_ВИСОЧИНИ = арг.obhvat
    if арг.izlishni is not None:
        вер.МАКС_ИЗЛИШНИ = арг.izlishni
    print("настройки: обхват=%.1f височини, излишни<=%d"
          % (вер.ОБХВАТ_ВИСОЧИНИ, вер.МАКС_ИЗЛИШНИ))

    имена = арг.проекти or [п.name for п in sorted(ЧИСТИ.iterdir()) if п.is_dir()]
    изход: dict[str, object] = {"схема": "etap8-obhvat-veriga/v1", "проекти": {}}
    общо = Counter()
    всички_находки: list[dict] = []

    for име in имена:
        отчет = последен_отчет(име)
        if отчет is None:
            изход["проекти"][име] = {"грешка": "няма пакетен отчет"}
            print("%-10s няма пакетен отчет" % име)
            continue
        ред_списък = редове(отчет)
        ук = вер.ВериженУказател(обекти(име))
        брой = Counter()
        присъди: dict[str, вер.ВерижноДоказателство] = {}
        детайли: dict[str, dict] = {}
        for ред in ред_списък:
            ключ = str(ред.get("annotation_handle") or ред.get("handle") or "")
            handles = [str(h) for h in (ред.get("candidate_handles") or ())]
            try:
                обявена = float(ред.get("declared_length_cm") or 0.0)
            except (TypeError, ValueError):
                обявена = 0.0
            if not handles or обявена <= 0:
                брой["без вход"] += 1
                continue
            р = вер.присъда(handles, ук, обявена, изключени_надписи=[ключ.split("::")[0]])
            if р is None:
                брой["без присъда"] += 1
                continue
            присъди[ключ] = р
            детайли[ключ] = {
                "текст": ред.get("annotation_text"),
                "форма": ред.get("master_shape_code"),
                "доказан": ред.get("production_segments_cm"),
                "верижен": list(р.участъци_cm),
                "добавени": list(р.добавени_handles),
                "надписи": list(р.надписи_handles),
                "запас": round(р.запас, 3) if р.запас != float("inf") else "inf",
                "височини": round(р.най_далечен_надпис_височини, 3),
                "завои": [round(з, 2) for з in р.завои_deg],
            }
        падат = вер.уникално_притежание(присъди)
        for ключ in падат:
            брой["падат по притежание"] += 1
        for ключ, р in присъди.items():
            if ключ in падат:
                continue
            доказан = детайли[ключ]["доказан"]
            if isinstance(доказан, (list, tuple)) and доказан:
                ако_съвпада = (len(доказан) == len(р.участъци_cm) and all(
                    abs(float(a) - float(b)) <= 0.11
                    for a, b in zip(доказан, р.участъци_cm)))
                брой["съвпада с доказан" if ако_съвпада else "ПРОТИВОРЕЧИ"] += 1
                детайли[ключ]["присъда"] = "СЪВПАДА" if ако_съвпада else "ПРОТИВОРЕЧИ"
            else:
                брой["НОВ вектор"] += 1
                детайли[ключ]["присъда"] = "НОВ"
            всички_находки.append({"проект": име, "позиция": ключ, **детайли[ключ]})
        изход["проекти"][име] = {"редове": len(ред_списък), **брой}
        общо.update(брой)
        print("%-10s редове=%-5d нов=%-4d съвпада=%-4d противоречи=%-3d притежание=%-3d без=%d"
              % (име, len(ред_списък), брой["НОВ вектор"], брой["съвпада с доказан"],
                 брой["ПРОТИВОРЕЧИ"], брой["падат по притежание"], брой["без присъда"]))

    изход["общо"] = dict(общо)
    изход["находки"] = всички_находки
    арг.izhod.parent.mkdir(parents=True, exist_ok=True)
    арг.izhod.write_text(json.dumps(изход, ensure_ascii=False, indent=1), encoding="utf-8")
    print("ОБЩО:", dict(общо))
    print("записано:", арг.izhod)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
