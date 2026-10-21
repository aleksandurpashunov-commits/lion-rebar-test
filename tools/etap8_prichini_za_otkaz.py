# -*- coding: utf-8 -*-
"""Защо верижното правило отказва — и има ли какво да се НАУЧИ от това.

Класифицира отказите на ``chain_printed_dimensions`` по причина и проверява
хипотезата, която ще учим: „размерите на участъците стоят на ЕДИН определен слой
на проектанта". Ако ограничаването на числовите надписи до един слой превръща
отказ в потвърждение СЪС ЗАТВАРЯЩ СЕ СБОР, значи има измерима полза; иначе
приносът е нула и това ще се отчете като нула.

Сборът е независимата проверка: той идва от надписа на позицията, не от слоя.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections import Counter, defaultdict

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
    к = sorted(ПРОГОНИ.glob("%s_*/output/04_REVIEW_REPORTS/*/*_geometry_batch.json" % проект))
    return к[-1] if к else None


def причина(handles, ук, обявена, собствен) -> tuple[str, dict]:
    """Първата спирачка по пътя на правилото."""

    събрано = вер.събери_веригата(handles, ук)
    if събрано is None:
        return "ВЕРИГАТА_НЕ_СЕ_СЪБИРА", {}
    отсечки, слой = събрано
    подредени = вер.подреди_веригата(отсечки)
    if подредени is None:
        return "РАЗКЛОНЕНИЕ_ИЛИ_ПРИМКА", {"отсечки": len(отсечки), "слой": слой}
    рамена = вер.рамена_на_веригата(подредени)
    if рамена is None or len(рамена) < 2:
        return "НЯМА_ГОДНИ_РАМЕНА", {"слой": слой}
    кандидати = вер.надписи_на_рамената(рамена, ук, изключени=[собствен])
    по_слой: dict[str, int] = Counter(н.слой for н, _ in кандидати)
    ключ = {"рамена": len(рамена), "надписи": len(кандидати),
            "слой": слой, "надписи_по_слой": dict(по_слой)}
    if len(кандидати) != len(рамена):
        return ("ПОВЕЧЕ_НАДПИСИ_ОТ_РАМЕНА" if len(кандидати) > len(рамена)
                else "ПО-МАЛКО_НАДПИСИ_ОТ_РАМЕНА"), ключ
    избор = вер._най_добро_присвояване(рамена, кандидати)
    if избор is None:
        return "НЯМА_ГОДНО_ПРИСВОЯВАНЕ", ключ
    надписи, _ц, запас = избор
    стойности = tuple(н.стойност for н in надписи)
    ключ["вектор"] = list(стойности)
    ключ["сбор"] = sum(стойности)
    if запас < вер.ЗАПАС_НА_ПРИСВОЯВАНЕТО:
        return "МАЛЪК_ЗАПАС", {**ключ, "запас": round(запас, 3)}
    if abs(sum(стойности) - обявена) > вер.ДОПУСК_СБОР_CM:
        return "СБОРЪТ_НЕ_СЕ_ЗАТВАРЯ", ключ
    if not вер._редът_е_съгласуван(рамена, стойности):
        return "РЕДЪТ_НЕ_Е_СЪГЛАСУВАН", ключ
    return "?", ключ


def проба_със_слой(handles, ук, обявена, собствен, слой: str):
    """Същото правило, но числовите надписи са само от този слой."""

    свит = ук.само_с_надписи_от(слой)
    return вер.присъда(handles, свит, обявена, изключени_надписи=[собствен])


def main() -> int:
    ап = argparse.ArgumentParser()
    ап.add_argument("проекти", nargs="*")
    ап.add_argument("--izhod", type=pathlib.Path,
                    default=pathlib.Path(r"C:\lr_exp\rezultat\etap_8\PRICHINI_ZA_OTKAZ.json"))
    арг = ап.parse_args()

    имена = арг.проекти or [п.name for п in sorted(ЧИСТИ.iterdir()) if п.is_dir()]
    общо = Counter()
    печалба = Counter()
    подробно: list[dict] = []
    слоеве_на_потвърдените: dict[str, Counter] = defaultdict(Counter)

    for име in имена:
        отчет = последен_отчет(име)
        if отчет is None:
            continue
        данни = json.loads(отчет.read_text(encoding="utf-8"))
        ук = вер.ВериженУказател(обекти(име))
        for ред in (данни.get("rows") or []):
            ключ = str(ред.get("annotation_handle") or "")
            handles = [str(h) for h in (ред.get("candidate_handles") or ())]
            try:
                обявена = float(ред.get("declared_length_cm") or 0.0)
            except (TypeError, ValueError):
                обявена = 0.0
            if not handles or обявена <= 0:
                continue
            собствен = ключ.split("::")[0]
            р = вер.присъда(handles, ук, обявена, изключени_надписи=[собствен])
            if р is not None:
                общо["ПОТВЪРДЕНО"] += 1
                for h in р.надписи_handles:
                    for н in ук.надписи:
                        if н.handle == h:
                            слоеве_на_потвърдените[име][н.слой] += 1
                continue
            код, детайл = причина(handles, ук, обявена, собствен)
            общо[код] += 1
            if код != "ПОВЕЧЕ_НАДПИСИ_ОТ_РАМЕНА":
                continue
            # Хипотезата: ограничаване до ЕДИН слой решава излишъка.
            решен = None
            for слой in sorted(детайл.get("надписи_по_слой") or ()):
                опит = проба_със_слой(handles, ук, обявена, собствен, слой)
                if опит is not None:
                    решен = (слой, list(опит.участъци_cm))
                    break
            if решен:
                печалба[име] += 1
                подробно.append({"проект": име, "позиция": ключ,
                                 "текст": ред.get("annotation_text"),
                                 "обявена": обявена, "слой": решен[0],
                                 "вектор": решен[1], **детайл})

    изход = {"схема": "etap8-prichini/v1", "причини": dict(общо),
             "решими_с_един_слой": dict(печалба),
             "слоеве_на_потвърдените": {k: dict(v) for k, v in слоеве_на_потвърдените.items()},
             "подробно": подробно}
    арг.izhod.parent.mkdir(parents=True, exist_ok=True)
    арг.izhod.write_text(json.dumps(изход, ensure_ascii=False, indent=1), encoding="utf-8")
    for к, v in общо.most_common():
        print("%-32s %d" % (к, v))
    print("решими чрез ограничаване до един слой:", dict(печалба), "общо", sum(печалба.values()))
    print("записано:", арг.izhod)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
