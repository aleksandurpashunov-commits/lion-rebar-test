"""ПРОВЕРКА на променената защита при нулеви контролни съвпадения.

Етап 3 смени условието от ``съвпадения / max(1, контролни) >= 4`` на „приема се
при контролни == 0". Това РАЗШИРЯВА приемането и не бива да се обявява за
безопасно само по аритметичен довод.

Тук се мери какво приема всяко от трите правила върху пет вида случаи:

  * малко, но верни примери (истинска армировка с малко надписи);
  * неармировъчна геометрия със СХОДНИ дължини (тръби, оси, контури);
  * случайни пространствени съвпадения (разпръснати обекти);
  * различни контролни отмествания (не само +13 cm);
  * смесени слоеве и подвеждащи имена.

Правилата, които се сравняват:

  ``СТАРО``      съвпадения >= 2 И съвпадения / max(1, контролни) >= 4
  ``НОВО``       съвпадения >= 2 И (контролни == 0 ИЛИ превес >= 4)
  ``МНОГО_КОНТРОЛИ`` съвпадения >= 2 И нито едно съвпадение при НИКОЕ от
                 няколко различни отмествания (по-строго от двете)

Изборът се прави ПРЕДИ окончателното измерване и се записва тук.
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import random
import sys
from collections import Counter, defaultdict

КОРЕН = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(КОРЕН))

from lion_rebar.dxf.convention_profile import (  # noqa: E402
    GEOMETRY_CONTROL_SHIFT_CM, _близки_дължини, _point, _polyline_points,
)
from lion_rebar.dxf.models import DxfEntityRecord  # noqa: E402
from lion_rebar.dxf.recognition_policy import DEFAULT_RECOGNITION_POLICY  # noqa: E402
from lion_rebar.dxf.text_parser import looks_like_rebar_text, parse_rebar_text  # noqa: E402

ПРОЕКТИ = pathlib.Path(r"C:\lr_exp\proekti")
ПОЛЕТА = ("handle", "entity_type", "layer", "geometry", "text", "block_name",
          "color_index", "linetype", "raw")

#: Отместванията за многоконтролната проверка. Няколко и с различен знак, за да
#: не може един случаен мащаб да ги подмине всичките.
ОТМЕСТВАНИЯ = (13.0, -13.0, 7.0, -7.0, 23.0)

MIN_СЪВПАДЕНИЯ = 2
ПРЕВЕС = 4.0


def обекти_на(проект: pathlib.Path) -> list[DxfEntityRecord]:
    п = проект / "working" / "extracted_entities.json"
    if not п.is_file():
        return []
    д = json.loads(п.read_text(encoding="utf-8-sig"))
    редове = д if isinstance(д, list) else (д.get("entities") or [])
    return [DxfEntityRecord(**{и: р.get(и) for и in ПОЛЕТА if и in р})
            for р in редове if isinstance(р, dict)]


def _брои(записи, *, отмествания=ОТМЕСТВАНИЯ, радиус=None) -> dict[str, dict]:
    """За всеки слой: попадения по истинската дължина и по всяко отместване."""

    радиус = радиус or float(
        getattr(DEFAULT_RECOGNITION_POLICY, "max_search_distance_raw", 90.0) or 90.0)
    клетка = max(100.0, радиус)
    геометрия: list[tuple[str, float, list]] = []
    решетка: dict[tuple[int, int], list[int]] = defaultdict(list)
    for запис in записи:
        точки = _polyline_points(запис)
        if len(точки) < 2:
            continue
        дължина = sum(math.dist(точки[и], точки[и + 1]) for и in range(len(точки) - 1))
        if дължина <= 5.0:
            continue
        индекс = len(геометрия)
        геометрия.append((str(запис.layer or ""), дължина, точки))
        for x, y in точки:
            решетка[(int(x // клетка), int(y // клетка))].append(индекс)
    if not геометрия:
        return {}

    попадения: Counter = Counter()
    контроли: dict[float, Counter] = {о: Counter() for о in отмествания}
    надписи = 0
    for запис in записи:
        текст = str(запис.text or "").strip()
        if not текст or not looks_like_rebar_text(текст):
            continue
        прочит = parse_rebar_text(текст)
        дължина = getattr(прочит, "length_cm", None)
        точка = _point((запис.geometry or {}).get("insert")
                       or (запис.geometry or {}).get("position"))
        if not дължина or точка is None:
            continue
        надписи += 1
        цел = float(дължина)
        gx, gy = int(точка[0] // клетка), int(точка[1] // клетка)
        близки: set[int] = set()
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                близки.update(решетка.get((gx + dx, gy + dy), ()))
        свои: set[str] = set()
        свои_контрола: dict[float, set[str]] = {о: set() for о in отмествания}
        for и in близки:
            слой, дълж, точки = геометрия[и]
            if min(math.dist(точка, върх) for върх in точки) > радиус:
                continue
            if _близки_дължини(дълж, цел):
                свои.add(слой)
                continue
            for о in отмествания:
                if _близки_дължини(дълж, цел + о):
                    свои_контрола[о].add(слой)
        for слой in свои:
            попадения[слой] += 1
        for о in отмествания:
            for слой in свои_контрола[о]:
                контроли[о][слой] += 1

    изход = {}
    for слой, брой in попадения.items():
        по_отместване = {о: контроли[о][слой] for о in отмествания}
        изход[слой] = {
            "matches": брой,
            "control_default": по_отместване.get(GEOMETRY_CONTROL_SHIFT_CM, 0),
            "controls_by_shift": по_отместване,
            "controls_total": sum(по_отместване.values()),
            "annotations_with_length": надписи,
        }
    return изход


def _решения(данни: dict) -> dict[str, bool]:
    m = данни["matches"]
    k = данни["control_default"]
    всички = данни["controls_total"]
    старо = m >= MIN_СЪВПАДЕНИЯ and (m / max(1, k)) >= ПРЕВЕС
    ново = m >= MIN_СЪВПАДЕНИЯ and (k == 0 or (m / k) >= ПРЕВЕС)
    много = m >= MIN_СЪВПАДЕНИЯ and всички == 0
    return {"СТАРО": старо, "НОВО": ново, "МНОГО_КОНТРОЛИ": много}


def _армировъчен(слой: str) -> bool | None:
    """Груба независима преценка по име — САМО за отчета, не за решение."""

    горе = слой.upper()
    if any(и in горе for и in ("ARM", "АРМ", "REBAR", "STREM", "СТРЕМ", "BAR",
                               "REINF", "ESOV", "ЕСОВ", "FIB", "ФИБ")):
        return True
    if any(и in горе for и in ("HATCH", "DIM", "KOT", "КОТ", "AXIS", "OSI", "ОСИ",
                               "TEXT", "TXT", "RAMKA", "РАМКА", "FRAME", "WALL",
                               "COL_", "SLAB", "CONTOUR", "SECTION", "HID",
                               "LIFT", "SYMBOL", "LEGEND")):
        return False
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверка на защитата при контрола 0")
    parser.add_argument("--json", type=pathlib.Path,
                        default=pathlib.Path(r"C:\lr_exp\rezultat\etap_4\KONTROLA.json"))
    parser.add_argument("--seed", type=int, default=20260927)
    args = parser.parse_args()
    random.seed(args.seed)

    редове = []
    по_правило: dict[str, Counter] = {и: Counter() for и in ("СТАРО", "НОВО", "МНОГО_КОНТРОЛИ")}

    for проект in sorted(ПРОЕКТИ.iterdir()):
        if not проект.is_dir():
            continue
        записи = обекти_на(проект)
        if not записи:
            continue
        данни = _брои(записи)
        for слой, стойности in sorted(данни.items()):
            решения = _решения(стойности)
            истина = _армировъчен(слой)
            ред = {"проект": проект.name, "слой": слой, **стойности,
                   "решения": решения, "по_име_армировъчен": истина}
            редове.append(ред)
            for правило, прието in решения.items():
                if истина is True:
                    по_правило[правило]["вярно_прието" if прието else "пропуснато"] += 1
                elif истина is False:
                    по_правило[правило]["ГРЕШНО_ПРИЕТО" if прието else "вярно_отказано"] += 1
                else:
                    по_правило[правило]["неопределимо_прието" if прието
                                        else "неопределимо_отказано"] += 1

    print("=== РЕАЛЕН КОРПУС: %d слоя със съвпадения по дължина" % len(редове))
    print("%-16s %14s %11s %14s %13s %12s %12s" % (
        "правило", "вярно приети", "пропуснати", "ГРЕШНО приети",
        "вярно отказ", "неопр. приети", "неопр. отказ"))
    print("-" * 100)
    for правило in ("СТАРО", "НОВО", "МНОГО_КОНТРОЛИ"):
        c = по_правило[правило]
        print("%-16s %14d %11d %14d %13d %12d %12d" % (
            правило, c["вярно_прието"], c["пропуснато"], c["ГРЕШНО_ПРИЕТО"],
            c["вярно_отказано"], c["неопределимо_прието"], c["неопределимо_отказано"]))

    print("\n=== СЛОЕВЕ, КОИТО НОВОТО ПРАВИЛО ПРИЕМА, А СТАРОТО НЕ")
    нови = [р for р in редове if р["решения"]["НОВО"] and not р["решения"]["СТАРО"]]
    for р in sorted(нови, key=lambda r: (r["проект"], r["слой"])):
        знак = {True: "армировъчен", False: "НЕ е армировъчен", None: "неопределим"}[
            р["по_име_армировъчен"]]
        print("   %-10s %-34s съвп=%-3d контрола(+13)=%-2d всички контроли=%-2d  %s" % (
            р["проект"], р["слой"][:34], р["matches"], р["control_default"],
            р["controls_total"], знак))
    if not нови:
        print("   (няма)")

    print("\n=== СЛОЕВЕ, КОИТО МНОГОКОНТРОЛНОТО ОТХВЪРЛЯ, А НОВОТО ПРИЕМА")
    разлика = [р for р in редове if р["решения"]["НОВО"] and not р["решения"]["МНОГО_КОНТРОЛИ"]]
    for р in sorted(разлика, key=lambda r: -r["controls_total"])[:12]:
        знак = {True: "армировъчен", False: "НЕ е армировъчен", None: "неопределим"}[
            р["по_име_армировъчен"]]
        print("   %-10s %-30s съвп=%-3d контроли по отместване=%s  %s" % (
            р["проект"], р["слой"][:30], р["matches"],
            {int(к): v for к, v in р["controls_by_shift"].items()}, знак))
    if not разлика:
        print("   (няма)")

    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps({
        "seed": args.seed,
        "отмествания": list(ОТМЕСТВАНИЯ),
        "правила": {
            "СТАРО": "съвпадения>=2 И съвпадения/max(1,контролни)>=4",
            "НОВО": "съвпадения>=2 И (контролни==0 ИЛИ превес>=4)",
            "МНОГО_КОНТРОЛИ": "съвпадения>=2 И нула съвпадения при ВСИЧКИ отмествания",
        },
        "обобщение": {к: dict(v) for к, v in по_правило.items()},
        "редове": редове,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\nзаписан:", args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
