"""ДИАГНОЗА: кое поле в ключа разделя решения, които изглеждат еднакви.

Повод: в „тест-004" инженерът е решил 11 позиции, много от които са едно и
също желязо в различен брой („468N18x410", „40N18x410" ...). Нито две не
попадат в една кофа — дори когато броят и надписът се махнат от ключа.

Тук за избран проект се вади ПЪЛНИЯТ ключ на всяко решение и се показва кои
полета се различават. Само чете.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections import Counter, defaultdict

КОРЕН = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(КОРЕН))

from lion_rebar.review import decision_suggestions as ds  # noqa: E402

ПРОЕКТИ = pathlib.Path(r"C:\lr_exp\proekti")
ПРОГОНИ = pathlib.Path(r"C:\lr_exp\data\full_path_runs")
ПРИЗНАК = "NO_CANDIDATE_SHAPE"


def _отчет(проект: pathlib.Path) -> dict | None:
    кандидати = [p for p in ПРОГОНИ.iterdir()
                 if p.is_dir() and p.name.startswith(проект.name + "_")] \
        if ПРОГОНИ.is_dir() else []
    корен = max(кандидати, key=lambda p: p.stat().st_mtime) if кандидати else проект
    папка = корен / "output" / "04_REVIEW_REPORTS"
    файлове = sorted(папка.glob("*/*_geometry_batch.json")) if папка.is_dir() else []
    if not файлове:
        return None
    най = max(файлове, key=lambda p: p.stat().st_mtime)
    return json.loads(най.read_text(encoding="utf-8-sig"))


def _дръжки_на_решенията(проект: pathlib.Path) -> list[str]:
    дръжки = []
    for име in ("decision_journal.jsonl", "decision_events_v2.jsonl"):
        п = проект / "working" / име
        if not п.is_file():
            continue
        for ред in п.read_text(encoding="utf-8").splitlines():
            ред = ред.strip()
            if not ред:
                continue
            try:
                запис = json.loads(ред)
            except ValueError:
                continue
            if not isinstance(запис, dict) or запис.get("event") != "ENGINEER_RESOLVED":
                continue
            payload = запис.get("payload") or {}
            дръжка = str(payload.get("annotation_handle") or "")
            if дръжка and (payload.get("canonical_decision") or {}):
                дръжки.append(дръжка)
    return list(dict.fromkeys(дръжки))


def main() -> int:
    parser = argparse.ArgumentParser(description="Кое поле разделя еднаквите решения")
    parser.add_argument("проект")
    args = parser.parse_args()

    проект = ПРОЕКТИ / args.проект
    отчет = _отчет(проект)
    if отчет is None:
        print("няма отчет за", проект.name)
        return 1
    редове = {str(r.get("annotation_handle") or ""): r for r in отчет.get("rows") or []}
    самоличност = отчет.get("data_identity") or {}
    if not самоличност:
        for r in отчет.get("rows") or []:
            if r.get("data_identity"):
                самоличност = r["data_identity"]
                break
    версия = str((отчет.get("analysis_engine") or {}).get("engine_version") or "")

    ключове = []
    for дръжка in _дръжки_на_решенията(проект):
        ред = редове.get(дръжка)
        if ред is None:
            continue
        подменен = dict(ред)
        if not ds._upper(ред.get("master_shape_code") or ред.get("suggested_shape_code")):
            подменен["master_shape_code"] = ПРИЗНАК
            подменен["suggested_shape_code"] = ПРИЗНАК
        контекст, липсва = ds.strict_context_from_review_row(
            подменен, identity=самоличност, engine_version=версия)
        if контекст is None:
            print("  %-14s НЕПЪЛЕН КЛЮЧ: %s" % (дръжка, ", ".join(липсва)))
            continue
        ключове.append((дръжка, dict(контекст.fields)))

    if not ключове:
        print("няма ключове")
        return 0

    print("\n%d решения с пълен ключ в %s\n" % (len(ключове), проект.name))
    имена = list(ключове[0][1])
    различни = {}
    for име in имена:
        стойности = Counter(к[име] for _, к in ключове)
        if len(стойности) > 1:
            различни[име] = стойности

    print("=== ПОЛЕТА, КОИТО СЕ РАЗЛИЧАВАТ (те разделят кофите)")
    for име, стойности in sorted(различни.items(), key=lambda kv: -len(kv[1])):
        print("  %-28s %d различни стойности" % (име, len(стойности)))
        for стойност, брой in стойности.most_common(6):
            показана = (стойност[:70] + "...") if len(стойност) > 73 else стойност
            print("      x%-3d %s" % (брой, показана or "(празно)"))

    еднакви = [и for и in имена if и not in различни]
    print("\n=== ЕДНАКВИ ЗА ВСИЧКИ (%d полета)" % len(еднакви))
    print("  " + ", ".join(еднакви))

    # Колко кофи щяха да станат, ако едно поле се махне от ключа
    print("\n=== КОЛКО КОФИ ОСТАВАТ, АКО СЕ МАХНЕ ЕДНО ПОЛЕ")
    основа = len({tuple(sorted(к.items())) for _, к in ключове})
    print("  %-28s %d кофи за %d решения (сега)" % ("(нищо не се маха)", основа, len(ключове)))
    for име in sorted(различни):
        кофи = {tuple(sorted((и, v) for и, v in к.items() if и != име))
                for _, к in ключове}
        печалба = len(ключове) - len(кофи)
        знак = "  <-- сливане" if len(кофи) < основа else ""
        print("  без %-24s %d кофи%s" % (име, len(кофи), знак))

    # И ако се махнат ВСИЧКИ различаващи се полета без инженерния смисъл
    незначещи = [и for и in различни if и in (
        "annotation_text", "count", "numeric_difference",
        "alternative_candidate_count", "canonical_signature")]
    if незначещи:
        кофи = defaultdict(list)
        for дръжка, к in ключове:
            кофи[tuple(sorted((и, v) for и, v in к.items() if и not in незначещи))].append(дръжка)
        print("\n=== БЕЗ %s" % ", ".join(sorted(незначещи)))
        print("  %d кофи за %d решения" % (len(кофи), len(ключове)))
        for _, дръжки in sorted(кофи.items(), key=lambda kv: -len(kv[1]))[:5]:
            print("      %d: %s" % (len(дръжки), ", ".join(дръжки[:6])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
