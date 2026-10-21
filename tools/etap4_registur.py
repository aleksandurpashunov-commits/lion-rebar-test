"""ЕДИНЕН РЕГИСТЪР на непреминалите позиции — по проект, ревизия и позиция.

Защо: етап 3 изброи групи (51 блокирани, 318 без MASTER форма, 234 за ръчен
преглед, 131 F006 с кука), но те НЕ са независими — една позиция може да е в
няколко. Тук всяка позиция е ЕДИН ред с една ОСНОВНА причина и списък от
съпътстващи флагове, за да не се събират припокриващи се причини.

Източници:
  * пакетните отчети (пълните редове) от запазените прогони;
  * обобщенията на прогоните (кои позиции е спряла портата);
  * копията на проектите (има ли предварително човешко решение).

Само чете.
"""
from __future__ import annotations

import argparse
import json
import pathlib
from collections import Counter, defaultdict

ПРОГОНИ = pathlib.Path(r"C:\lr_exp\data\full_path_runs")
ПРОЕКТИ = pathlib.Path(r"C:\lr_exp\proekti")
ЧИСТИ = pathlib.Path(r"C:\lr_exp\proekti_chisti")

СЕМЕЙСТВО = {
    "т-1": "т-1", "s-9": "s-9", "s-11": "s-11", "s-8": "s-8", "s-10": "s-10",
    "s-7": "s-7", "s-1": "s-1..s-6/а-1", "d-1": "d-1", "d-2": "d-2",
    "Тест-003": "тест-001/Тест-003", "Тест-002": "Тест-002",
    "тест-004": "тест-004/005", "тест-006": "тест-006",
}
ЗАКЛЮЧЕНИ = {"т-1", "Тест-002", "s-7", "s-1..s-6/а-1"}

#: Редът определя ОСНОВНАТА причина: първата, която важи, печели. Подредбата е
#: от „няма какво да се разпознае" към „има кандидат, но нещо не достига".
ПРИОРИТЕТ = (
    ("NO_GEOMETRY_AT_ALL", "няма нито един кандидат-обект"),
    ("NO_MASTER_SHAPE", "има геометрия, но никоя активна форма не пасва"),
    ("F006_HOOKED_CLOSURE", "стреме с отворени куки — иска инженерно решение"),
    ("OWNERSHIP_CONFLICT", "една геометрия, поискана от няколко надписа"),
    ("DIMENSION_EVIDENCE_INVALID", "надписаните коти не потвърждават участъците"),
    ("WEAK_SEPARATION", "първият кандидат няма достатъчно преднина"),
    ("FAR_ASSOCIATION", "надписът е извън основния радиус"),
    ("DATA_QUALITY", "проблем с качеството на данните"),
    ("OTHER_MANUAL", "за ръчен преглед по друга причина"),
)


ЧИСТИ_ПРОГОНИ = pathlib.Path(r"C:\lr_exp\rezultat\etap_4\chisti_progoni")


def _най_нови_отчети(само_чисти: bool = True) -> dict[str, pathlib.Path]:
    """Отчетите от ЧИСТИТЕ копия (без предварителни одобрения) по подразбиране.

    Отчетите от копията С одобрения дават друга картина: там част от
    „разпознатото" всъщност е човешко решение. За регистъра важи чистото.
    """

    if само_чисти and ЧИСТИ_ПРОГОНИ.is_dir():
        изход = {}
        for п in sorted(ЧИСТИ_ПРОГОНИ.glob("*.json")):
            if п.name.startswith("_"):
                continue
            try:
                д = json.loads(п.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            копие = str(д.get("copy") or "")
            if not копие:
                continue
            папка = pathlib.Path(копие) / "output" / "04_REVIEW_REPORTS"
            файлове = sorted(папка.glob("*/*_geometry_batch.json")) if папка.is_dir() else []
            if файлове:
                изход[п.stem] = max(файлове, key=lambda f: f.stat().st_mtime)
        if изход:
            return изход
    намерени: dict[str, tuple[float, pathlib.Path]] = {}
    for п in ПРОГОНИ.rglob("*_geometry_batch.json"):
        име = п.parent.parent.parent.parent.name.split("_2026")[0]
        т = п.stat().st_mtime
        ако = намерени.get(име)
        if ако is None or т > ако[0]:
            намерени[име] = (т, п)
    return {и: п for и, (_, п) in намерени.items()}


def poz_handle(поз: dict) -> str:
    return str(поз.get("handle") or "")


def _човешки_решения(проект: str) -> set[str]:
    """Дръжките с предварително човешко решение в копието с одобрения."""

    дръжки: set[str] = set()
    работна = ПРОЕКТИ / проект / "working"
    връзки = работна / "approved_geometry_links.json"
    if връзки.is_file():
        try:
            д = json.loads(връзки.read_text(encoding="utf-8-sig"))
            елементи = д.get("links") if isinstance(д, dict) else д
            if isinstance(елементи, dict):
                елементи = list(елементи.values())
            for е in елементи or ():
                if isinstance(е, dict):
                    за = str(е.get("annotation_handle") or е.get("handle") or "")
                    if за:
                        дръжки.add(за.upper())
        except (OSError, ValueError):
            pass
    return дръжки


def _основна_причина(ред: dict) -> tuple[str, list[str]]:
    """Една основна причина и всички съпътстващи флагове."""

    блокери = {str(к).upper() for к in (ред.get("matcher_blocker_codes") or ())}
    прегледи = {str(к).upper() for к in (ред.get("review_reason_codes") or ())}
    флагове = sorted(блокери | прегледи)
    статус = str(ред.get("batch_status") or "").upper()
    има_кандидат = bool(ред.get("candidate_handles"))
    има_подпис = bool(str(ред.get("canonical_signature") or "").strip())
    има_форма = bool(str(ред.get("master_shape_code") or "").strip())

    if not има_кандидат and not има_подпис:
        основна = "NO_GEOMETRY_AT_ALL"
    elif not има_форма or "MISSING_ACTIVE_MASTER" in блокери:
        основна = "NO_MASTER_SHAPE"
    elif "HOOKED_CLOSURE" in флагове or "СТРЕМЕ F006" in статус:
        основна = "F006_HOOKED_CLOSURE"
    elif "GEOMETRY_OWNERSHIP_CONFLICT" in флагове or ред.get("geometry_owner_conflict") \
            or ред.get("global_conflict"):
        основна = "OWNERSHIP_CONFLICT"
    elif "MATCHER_DIMENSION_EVIDENCE_INVALID" in флагове:
        основна = "DIMENSION_EVIDENCE_INVALID"
    elif "WEAK_CANDIDATE_SEPARATION" in флагове:
        основна = "WEAK_SEPARATION"
    elif "FAR_ASSOCIATION" in флагове:
        основна = "FAR_ASSOCIATION"
    elif any(к.startswith("DATA_QUALITY") for к in флагове) or (ред.get("data_quality_flags")):
        основна = "DATA_QUALITY"
    else:
        основна = "OTHER_MANUAL"
    return основна, флагове


def main() -> int:
    parser = argparse.ArgumentParser(description="Единен регистър на непреминалите позиции")
    parser.add_argument("--json", type=pathlib.Path,
                        default=pathlib.Path(r"C:\lr_exp\rezultat\etap_4\REGISTUR.json"))
    args = parser.parse_args()

    отчети = _най_нови_отчети()
    редове: list[dict] = []
    по_проект: dict[str, Counter] = defaultdict(Counter)
    групи_етап3 = Counter()

    for име, п in sorted(отчети.items()):
        д = json.loads(п.read_text(encoding="utf-8-sig"))
        самоличност = д.get("data_identity") or {}
        ревизия = str(самоличност.get("source_generation_id") or "")[:8]
        сума = str(самоличност.get("source_sha256") or "")[:12]
        решени_от_човек = _човешки_решения(име)
        семейство = СЕМЕЙСТВО.get(име, име)
        # Кои позиции НАИСТИНА спира портата. ``matcher_disposition == MANUAL``
        # значи само „за визуална проверка" — такава позиция често минава
        # портата и излиза в спецификацията. Смесването на двете преувеличава.
        спрени = set()
        обобщение = ЧИСТИ_ПРОГОНИ / ("%s.json" % име)
        if обобщение.is_file():
            try:
                о = json.loads(обобщение.read_text(encoding="utf-8"))
                for поз in (о.get("gate") or {}).get("blocked_positions") or ():
                    спрени.add(str(poz_handle(поз)).upper())
            except (OSError, ValueError):
                pass

        for ред in д.get("rows") or []:
            if str(ред.get("annotation_class") or "") != "ACTIONABLE":
                continue
            диспозиция = str(ред.get("matcher_disposition") or "").upper()
            статус = str(ред.get("batch_status") or "")
            одобрена = bool(ред.get("approved_in_project"))
            # интересуват ни само позициите, които НЕ минават сами
            за_преглед = диспозиция in ("MANUAL", "UNKNOWN") or not str(
                ред.get("master_shape_code") or "").strip()
            if not за_преглед:
                continue

            основна, флагове = _основна_причина(ред)
            дръжка = str(ред.get("annotation_handle") or "")
            запис = {
                "проект": име, "семейство": семейство,
                "заключен": семейство in ЗАКЛЮЧЕНИ,
                "ревизия": ревизия, "source_sha12": сума,
                "handle": дръжка,
                "надпис": ред.get("annotation_text"),
                "основна_причина": основна,
                "съпътстващи_флагове": флагове,
                "batch_status": статус,
                "matcher_disposition": диспозиция,
                "има_кандидат": bool(ред.get("candidate_handles")),
                "кандидат_дръжки": list(ред.get("candidate_handles") or ())[:4],
                "има_каноничен_подпис": bool(str(ред.get("canonical_signature") or "").strip()),
                "master_shape_code": ред.get("master_shape_code"),
                "спряна_от_портата": дръжка.upper() in спрени,
                "предварително_човешко_решение": (дръжка.upper() in решени_от_човек)
                                                  or одобрена,
                "approved_in_project": одобрена,
                "коти_наблизо": list(ред.get("nearby_dimensions_cm") or ())[:8],
                "производствени_участъци": list(ред.get("production_segments_cm") or ())[:8],
                "одобрени_участъци": list(ред.get("production_segment_lengths_cm") or ())[:8],
            }
            редове.append(запис)
            по_проект[име][основна] += 1

            # принадлежност към групите, цитирани в етап 3
            if not запис["има_каноничен_подпис"] and not запис["master_shape_code"]:
                групи_етап3["без_активна_MASTER_форма"] += 1
            if диспозиция == "MANUAL":
                групи_етап3["за_ръчен_преглед"] += 1
            if "HOOKED_CLOSURE" in флагове:
                групи_етап3["F006_HOOKED_CLOSURE"] += 1

    общо = Counter(р["основна_причина"] for р in редове)
    развойни = Counter(р["основна_причина"] for р in редове if not р["заключен"])
    спрени_общо = Counter(р["основна_причина"] for р in редове if р["спряна_от_портата"])
    спрени_развой = Counter(р["основна_причина"] for р in редове
                            if р["спряна_от_портата"] and not р["заключен"])
    с_решение = sum(1 for р in редове if р["предварително_човешко_решение"])

    print("=== ЕДИНЕН РЕГИСТЪР: %d позиции за преглед в %d проекта" % (
        len(редове), len(по_проект)))
    print("   от тях с ПРЕДВАРИТЕЛНО човешко решение: %d" % с_решение)
    print()
    print("%-28s %6s %6s %8s %8s  %s" % ("основна причина", "общо", "развой",
                                         "СПРЕНИ", "спр.разв", "описание"))
    print("-" * 96)
    описания = dict(ПРИОРИТЕТ)
    for код, _ in ПРИОРИТЕТ:
        if общо.get(код):
            print("%-28s %6d %6d %8d %8d  %s" % (
                код, общо[код], развойни.get(код, 0), спрени_общо.get(код, 0),
                спрени_развой.get(код, 0), описания[код]))
    print("%-28s %6d %6d %8d %8d" % ("ВСИЧКО", sum(общо.values()),
          sum(развойни.values()), sum(спрени_общо.values()), sum(спрени_развой.values())))

    print("\n=== ГРУПИТЕ ОТ ЕТАП 3 се ПРИПОКРИВАТ")
    for к, v in групи_етап3.most_common():
        print("   %-32s %d" % (к, v))
    print("   (сборът им е %d при само %d различни позиции — затова не се събират)"
          % (sum(групи_етап3.values()), len(редове)))

    print("\n=== по проект (основна причина)")
    for име in sorted(по_проект):
        c = по_проект[име]
        print("   %-10s %s" % (име, ", ".join("%s=%d" % (к, v) for к, v in c.most_common())))

    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps({
        "schema": "lion-rebar-etap4-registry/v1",
        "позиции": редове,
        "по_основна_причина": dict(общо),
        "по_основна_причина_развойни": dict(развойни),
        "по_проект": {к: dict(v) for к, v in по_проект.items()},
        "групи_от_етап_3_припокриващи_се": dict(групи_етап3),
        "с_предварително_човешко_решение": с_решение,
        "спрени_от_портата_по_причина": dict(спрени_общо),
        "спрени_от_портата_развойни": dict(спрени_развой),
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\nзаписан:", args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
