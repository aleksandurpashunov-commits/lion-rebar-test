"""Отчет „Разногласия“ — къде два независими източника в чертежа не се съгласяват.

ЗАЩО. Собственик, 23.09.2026: „по-скоро се питам дали няма нещо градивно,
което да ни помогне за в бъдеще“.

Всичко открито тази седмица дойде от сверка с нещо, което чертежът ВЕЧЕ казва —
добавката за огъване (разрез през 219 позиции), надписаните коти срещу
измерените сегменти (16,97 срещу 17), ведомостта на проектанта (235 kg), двата
еднакви кандидата. Нито едно не дойде от измислено правило. Всяко отне по ден
ръчна работа.

Този скрипт прави същото машинно. НЕ мени поведение и НЕ пипа проект: само чете
запазените пакетни отчети и брои къде източниците се разминават.

КАК СЕ ЧЕТЕ РЕЗУЛТАТЪТ
----------------------
* Постоянно разногласие = ПРАВИЛО, което не знаем. Така се доказа
  закръглянето до 5: 177 прави пръта с недостиг нула срещу 42 огънати,
  всичките точно ``ceil(сбор/5)*5``.
* Разпиляно разногласие = ДЕФЕКТ. Няма едно обяснение.
* Малък обхват = шум; не се заключава нищо.

Затова всяка проверка се мери с ТРИ числа: обхват (за колко позиции говори),
съгласуваност (какъв дял се обяснява с една хипотеза) и най-честото обяснение.

Пускане::

    .venv/Scripts/python.exe tools/razminavaniya.py [--json <път>] [--max-mb 60]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ОБЛАК = Path(r"I:\Моят диск\LION_REBAR_CLOUD\04_REVIEW_REPORTS")
ПРОЕКТНИ_КОРЕНИ = (
    Path(r"I:\Моят диск\Нови проектанти"),
    Path(r"I:\Моят диск\Тест-желязо"),
)

#: Допускът, с който програмата сверява сбор срещу обявена дължина.
ДОПУСК = 0.51
#: Стъпката на закръгляне, доказана на 21.09.2026.
СТЪПКА = 5.0

# ПРОВЕРКИТЕ МЕРЯТ СРЕЩУ ПРАВИЛАТА НА ПРОГРАМАТА, НЕ СРЕЩУ ПЛОСЪК ДОПУСК.
#
# Първата редакция сверяваше с ±0,51 и закръгляне до 5, и обяви 2547 „находки“
# с недостиг точно 5,00 cm. Всичките се оказаха F006 стремета, които програмата
# ОБЯСНЯВА с документираната си добавка от 8 cm — тоест мярката беше по-груба от
# самата програма и преоткриваше вече взети решения.
#
# Разногласие е това, което програмата НЕ МОЖЕ да обясни. Затова тук се вика
# нейният собствен ``segments_match_total``.
from lion_rebar.engine.bend_allowance import segments_match_total  # noqa: E402


def _число(value: Any) -> float | None:
    try:
        число = float(value)
    except (TypeError, ValueError):
        return None
    return число if math.isfinite(число) else None


def _вектор(value: Any) -> list[float]:
    изход: list[float] = []
    for item in value or ():
        число = _число(item)
        if число is not None:
            изход.append(число)
    return изход


# --- проверките ------------------------------------------------------------
#
# Всяка връща ``(ключ, обяснение)`` или ``None``. ``ключ`` е групата, по която
# се мери съгласуваността; ``обяснение`` е текстът за отчета.


def r1_nadpis_sreshtu_nachertano(row: Mapping[str, Any]) -> tuple[str, str] | None:
    """Надписаната дължина срещу измерената по геометрията."""

    обявена = _число(row.get("declared_length_cm"))
    сурова = _число(row.get("geometry_length_raw"))
    if обявена is None or сурова is None or обявена <= 0 or сурова <= 0:
        return None
    if not row.get("candidate_handles"):
        return None
    код = str(row.get("master_shape_code") or "").strip().upper() or None
    if segments_match_total(код, сурова, обявена):
        return None
    разлика = сурова - обявена
    if разлика > 0:
        return ("НАЧЕРТАНОТО_НАДВИШАВА",
                f"начертаното е с {разлика:.2f} cm повече от надписа")
    return ("НАЧЕРТАНОТО_НЕ_СТИГА",
            f"начертаното е с {-разлика:.2f} cm по-малко, необяснено")


def r2_koti_sreshtu_izmereno(row: Mapping[str, Any]) -> tuple[str, str] | None:
    """Всеки производствен участък съвпада ли с НАДПИСАНА кота."""

    сегменти = _вектор(row.get("production_segments_cm"))
    коти = _вектор(row.get("nearby_dimensions_cm"))
    if len(сегменти) < 2 or not коти:
        return None
    без_кота = 0
    шум = 0
    for стойност in сегменти:
        най = min(коти, key=lambda k: abs(k - стойност))
        разлика = abs(най - стойност)
        if разлика <= 0.011:
            continue
        if разлика <= 0.5:
            шум += 1
        else:
            без_кота += 1
    if шум and not без_кота:
        return ("ИЗМЕРЕНО_ВМЕСТО_НАДПИСАНО",
                f"{шум} участъка се разминават с котата под 0,5 cm")
    if без_кота:
        return ("УЧАСТЪК_БЕЗ_КОТА", f"{без_кота} участъка нямат своя кота")
    return None


def r3_sbor_sreshtu_nadpis(row: Mapping[str, Any]) -> tuple[str, str] | None:
    """Сборът на производствените участъци срещу надписаната дължина."""

    сегменти = _вектор(row.get("production_segments_cm"))
    обявена = _число(row.get("production_length_cm")) or _число(row.get("declared_length_cm"))
    if len(сегменти) < 2 or обявена is None or обявена <= 0:
        return None
    код = str(row.get("master_shape_code") or "").strip().upper() or None
    сбор = math.fsum(сегменти)
    if segments_match_total(код, сбор, обявена):
        return None
    разлика = сбор - обявена
    if разлика > 0:
        return ("СБОРЪТ_НАДВИШАВА", f"сборът е с {разлика:.2f} cm повече")
    return ("СБОРЪТ_НЕ_ЗАТВАРЯ", f"недостиг {-разлика:.2f} cm, необяснен")


def r4_edna_geometriya_dva_nadpisa(row: Mapping[str, Any]) -> tuple[str, str] | None:
    """Една и съща геометрия, поискана от няколко надписа."""

    if not row.get("global_conflict"):
        return None
    брой = len(_вектор_текст(row.get("candidate_handles")))
    return ("СПОР_ЗА_ГЕОМЕТРИЯ", f"глобален конфликт при {брой or 1} кандидата")


def r5_broy_sreshtu_forma(row: Mapping[str, Any]) -> tuple[str, str] | None:
    """Броят участъци срещу това, което MASTER формата очаква."""

    код = str(row.get("master_shape_code") or "").strip().upper()
    сегменти = _вектор(row.get("production_segments_cm"))
    if not код or not сегменти:
        return None
    очаквани = {"F001": 1, "F004": 2, "F003": 3, "F005": 3, "F007": 3,
                "F002": 3, "F008": 5, "F009": 5, "F010": 5, "F006": 6}
    искан = очаквани.get(код)
    if искан is None or len(сегменти) == искан:
        return None
    return ("БРОЙ_УЧАСТЪЦИ", f"{код} очаква {искан}, а са {len(сегменти)}")


def r6_dulzhina_bez_geometriya(row: Mapping[str, Any]) -> tuple[str, str] | None:
    """Позиция с обявена дължина, но без нито един кандидат."""

    ако = str(row.get("batch_status") or "")
    if ако != "НЯМА КАНДИДАТ":
        return None
    отказани = row.get("rejection_counts") or {}
    if isinstance(отказани, Mapping) and отказани:
        причина = max(отказани.items(), key=lambda kv: kv[1])
        return ("ВСИЧКИ_КАНДИДАТИ_ОТКАЗАНИ", f"{причина[0]} x{причина[1]}")
    return ("НИТО_ЕДИН_КАНДИДАТ", "нито един обект не е разгледан")


def _вектор_текст(value: Any) -> list[str]:
    return [str(item) for item in (value or ()) if str(item)]


ПРОВЕРКИ = {
    "R1 надпис↔начертано": r1_nadpis_sreshtu_nachertano,
    "R2 коти↔измерено": r2_koti_sreshtu_izmereno,
    "R3 сбор↔надпис": r3_sbor_sreshtu_nadpis,
    "R4 спор за геометрия": r4_edna_geometriya_dva_nadpisa,
    "R5 брой участъци": r5_broy_sreshtu_forma,
    "R6 без кандидат": r6_dulzhina_bez_geometriya,
}


# --- корпусът --------------------------------------------------------------


def naj_novi_otcheti(max_mb: float) -> dict[str, Path]:
    """Най-новият отчет за всеки проект — както при разреза от 21.09."""

    корени = [ОБЛАК]
    for корен in ПРОЕКТНИ_КОРЕНИ:
        if корен.is_dir():
            for проект in корен.iterdir():
                папка = проект / "output" / "04_REVIEW_REPORTS"
                if папка.is_dir():
                    корени.append(папка)
    най: dict[str, tuple[float, Path]] = {}
    for корен in корени:
        for път in корен.rglob("*geometry_batch.json"):
            име = път.name.replace("_geometry_batch.json", "")
            if път.stat().st_size > max_mb * 1e6:
                continue
            време = път.stat().st_mtime
            ако = най.get(име)
            if ако is None or време > ако[0]:
                най[име] = (време, път)
    return {име: път for име, (_време, път) in най.items()}


def redove(път: Path) -> list[dict]:
    try:
        payload = json.loads(път.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return []
    редове = payload.get("rows")
    if not isinstance(редове, list):
        return []
    return [
        r for r in редове
        if isinstance(r, dict) and str(r.get("annotation_class") or "") == "ACTIONABLE"
    ]


# --- измерването -----------------------------------------------------------


def izmeri(
    корпус: Mapping[str, Path],
    подредба: Iterable[str],
    *,
    exclusive: bool,
) -> dict[str, Any]:
    """Брои находките по проверка.

    ``exclusive`` — първата проверка, която се обади за позиция, я ЗАЕМА;
    останалите мълчат за нея. Така се мери как подредбата мени приписването.
    """

    имена = list(подредба)
    находки: dict[str, Counter] = {име: Counter() for име in имена}
    позиции: dict[str, int] = {име: 0 for име in имена}
    примери: dict[str, dict[str, list[str]]] = {име: defaultdict(list) for име in имена}
    общо = 0
    засегнати = 0
    за_проект: dict[str, Counter] = defaultdict(Counter)

    for проект, път in корпус.items():
        for ред in redove(път):
            общо += 1
            обадили = False
            for име in имена:
                изход = ПРОВЕРКИ[име](ред)
                if изход is None:
                    continue
                ключ, обяснение = изход
                находки[име][ключ] += 1
                позиции[име] += 1
                за_проект[проект][име] += 1
                ако = примери[име][ключ]
                if len(ако) < 3:
                    ако.append(
                        f"{проект}: {str(ред.get('annotation_text') or '')[:20]} — {обяснение}"
                    )
                обадили = True
                if exclusive:
                    break
            if обадили:
                засегнати += 1
    return {
        "общо_позиции": общо,
        "засегнати": засегнати,
        "находки": находки,
        "позиции": позиции,
        "примери": примери,
        "за_проект": за_проект,
    }


def saglasuvanost(находки: Counter) -> tuple[float, str, int]:
    """Какъв дял от находките се обяснява с ЕДНА хипотеза."""

    сбор = sum(находки.values())
    if not сбор:
        return 0.0, "—", 0
    ключ, брой = находки.most_common(1)[0]
    return брой / сбор, ключ, сбор


def main() -> int:
    parser = argparse.ArgumentParser(description="Отчет „Разногласия“")
    parser.add_argument("--json", type=Path, default=None)
    parser.add_argument("--max-mb", type=float, default=60.0)
    args = parser.parse_args()

    корпус = naj_novi_otcheti(args.max_mb)
    print(f"КОРПУС  {len(корпус)} проекта (най-новият отчет на всеки, до {args.max_mb:g} MB)")

    самостоятелно = izmeri(корпус, ПРОВЕРКИ, exclusive=False)
    print(f"ПОЗИЦИИ {самостоятелно['общо_позиции']} за действие; "
          f"с поне едно разногласие {самостоятелно['засегнати']}")
    print()

    print("=== ВСЯКА ПРОВЕРКА САМОСТОЯТЕЛНО ===")
    print("%-24s %8s %7s %14s  %s" % ("проверка", "обхват", "дял%", "съгласуваност", "най-често"))
    редове_отчет = []
    for име in ПРОВЕРКИ:
        дял, ключ, сбор = saglasuvanost(самостоятелно["находки"][име])
        обхват = самостоятелно["позиции"][име]
        процент = 100.0 * обхват / max(самостоятелно["общо_позиции"], 1)
        print("%-24s %8d %6.1f%% %13.0f%%  %s" % (име, обхват, процент, дял * 100, ключ))
        редове_отчет.append({
            "проверка": име, "обхват": обхват, "дял_позиции": процент,
            "съгласуваност": дял, "най_често": ключ,
            "разбивка": dict(самостоятелно["находки"][име]),
        })
    print()

    for име in ПРОВЕРКИ:
        находки = самостоятелно["находки"][име]
        if not находки:
            continue
        print(f"--- {име} ---")
        for ключ, брой in находки.most_common():
            print("   %-30s %6d" % (ключ, брой))
            for пример in самостоятелно["примери"][име][ключ][:2]:
                print("        · %s" % пример[:100])
        print()

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps({
                "корпус": len(корпус),
                "общо_позиции": самостоятелно["общо_позиции"],
                "засегнати": самостоятелно["засегнати"],
                "проверки": редове_отчет,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print("записан:", args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
