"""ЕКСПЕРИМЕНТ: помага ли повторното ползване на решения МЕЖДУ чертежи.

ВЪПРОСЪТ. ``review/decision_suggestions.py`` вече умее да предлага предишно
човешко решение със същия строг контекст и дори различава „същия DXF" от
„напълно съвпадащ друг контекст". В производството обаче индексът се пълни
само от текущия проект — решенията не се наследяват между проекти (data
identity gate, CLAUDE.md).

Тук индексът се пълни от ДРУГИ чертежи и се мери какво би предложил.

ПРОТОКОЛ. Изключване на цяло семейство (leave-one-family-out): за целеви
проект T индексът се строи от всички проекти ИЗВЪН семейството на T. После за
всяка позиция, която човекът е решил в T, се пита индексът и отговорът му се
сверява с решението на човека.

ЕТАЛОН. Решението на инженера, доставено в дневника на проекта. Не се ползват
автоматични връзки: те са изходът на самата програма.

Мерят се четири изхода:
  ВЯРНО     — предложението съвпада с човешкото решение (форма, брой, Ø, L, сегменти)
  ГРЕШНО    — предложи друго; това е опасният изход
  КОНФЛИКТ  — намери различни решения и отказа (безопасно)
  БЕЗ       — няма съвпадащ контекст или липсва доказателство (безопасно)

Нищо не се записва в проектите: само чете и отчита.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections import Counter, defaultdict

КОРЕН = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(КОРЕН))

from lion_rebar.review.decision_suggestions import (  # noqa: E402
    CONFLICT, INELIGIBLE, NO_MATCH, READY, DecisionSuggestionIndex,
)

ПРОЕКТИ = pathlib.Path(r"C:\lr_exp\proekti")

#: Кой проект към кое чертожно семейство принадлежи (виж NABORI.json).
СЕМЕЙСТВО = {
    "т-1": "т-1", "s-9": "s-9", "s-11": "s-11", "s-8": "s-8", "s-10": "s-10",
    "s-7": "s-7", "s-1": "s-1..s-6/а-1", "d-1": "d-1", "d-2": "d-2",
    "Тест-003": "тест-001/Тест-003", "Тест-002": "Тест-002",
    "тест-004": "тест-004/005", "тест-006": "тест-006",
}


def _решения(проект: pathlib.Path) -> list[dict]:
    """Човешките решения в проекта — еталонът."""

    изход = []
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
            каноничен = payload.get("canonical_decision") or {}
            дръжка = str(payload.get("annotation_handle") or "")
            if дръжка and каноничен:
                изход.append({"дръжка": дръжка, "решение": каноничен,
                              "надпис": payload.get("annotation_text")})
    # една дръжка -> последното решение
    последни = {}
    for р in изход:
        последни[р["дръжка"]] = р
    return list(последни.values())


ПРОГОНИ = pathlib.Path(r"C:\lr_exp\data\full_path_runs")


def _копие_на_прогона(име: str) -> pathlib.Path | None:
    """Най-новото ЗАПАЗЕНО копие от ``full_path`` за този проект."""

    кандидати = [p for p in ПРОГОНИ.iterdir()
                 if p.is_dir() and p.name.startswith(f"{име}_")]
    return max(кандидати, key=lambda p: p.stat().st_mtime) if кандидати else None


def _отчет(проект: pathlib.Path) -> dict | None:
    корен = _копие_на_прогона(проект.name) or проект
    папка = корен / "output" / "04_REVIEW_REPORTS"
    папки = sorted(папка.glob("*/*_geometry_batch.json")) if папка.is_dir() else []
    if not папки:
        return None
    най = max(папки, key=lambda p: p.stat().st_mtime)
    return json.loads(най.read_text(encoding="utf-8-sig"))


def _подпис(решение: dict) -> tuple:
    """Как се сверява предложение срещу човешко решение."""

    сегменти = решение.get("production_segment_lengths_cm") or []
    return (
        str(решение.get("shape_code") or "").upper(),
        int(решение.get("count") or 0),
        round(float(решение.get("diameter_mm") or 0), 3),
        round(float(решение.get("production_length_cm") or 0), 2),
        tuple(round(float(s), 2) for s in сегменти),
    )


def _подпис_предложение(предложение) -> tuple:
    return (
        str(предложение.shape_code or "").upper(),
        int(предложение.count or 0),
        round(float(предложение.diameter_mm or 0), 3),
        round(float(предложение.production_length_cm or 0), 2),
        tuple(round(float(s), 2) for s in (предложение.production_segment_lengths_cm or ())),
    )


def оцени(само_свои: bool) -> dict:
    """``само_свои`` възпроизвежда производственото поведение (само този проект)."""

    всички = [p for p in sorted(ПРОЕКТИ.iterdir()) if p.is_dir()]
    резултат = {}
    for цел in всички:
        еталон = _решения(цел)
        if not еталон:
            continue
        отчет = _отчет(цел)
        if отчет is None:
            резултат[цел.name] = {"пропуснат": "няма пакетен отчет"}
            continue
        редове = {str(r.get("annotation_handle") or ""): r for r in отчет.get("rows") or []}
        самоличност = (отчет.get("data_identity") or
                       отчет.get("lion-rebar-data-identity") or {})
        if not самоличност:
            for r in отчет.get("rows") or []:
                if r.get("data_identity"):
                    самоличност = r["data_identity"]
                    break
        версия = str((отчет.get("analysis_engine") or {}).get("engine_version") or "")

        ако = СЕМЕЙСТВО.get(цел.name, цел.name)
        източници = [цел] if само_свои else [
            p for p in всички if СЕМЕЙСТВО.get(p.name, p.name) != ако
        ]
        индекс = DecisionSuggestionIndex(())
        събрани = []
        for и in източници:
            ч = DecisionSuggestionIndex.from_sources(project_root=и)
            събрани.append(ч)
        # сливане на кофите
        обединени = {}
        for ч in събрани:
            for ключ, стойности in ч._buckets.items():  # noqa: SLF001 — експеримент
                обединени.setdefault(ключ, []).extend(стойности)
        индекс._buckets = {к: tuple(v) for к, v in обединени.items()}  # noqa: SLF001

        броячи = Counter()
        грешни = []
        for случай in еталон:
            ред = редове.get(случай["дръжка"])
            if ред is None:
                броячи["без ред в отчета"] += 1
                continue
            предложение = индекс.lookup(ред, identity=самоличност, engine_version=версия)
            ако_статус = предложение.status
            if ако_статус == READY and предложение.proposal is not None:
                if _подпис_предложение(предложение.proposal) == _подпис(случай["решение"]):
                    броячи["ВЯРНО"] += 1
                else:
                    броячи["ГРЕШНО"] += 1
                    грешни.append({
                        "надпис": случай["надпис"],
                        "човек": _подпис(случай["решение"]),
                        "предложено": _подпис_предложение(предложение.proposal),
                    })
            elif ако_статус == CONFLICT:
                броячи["КОНФЛИКТ"] += 1
            elif ако_статус == NO_MATCH:
                броячи["БЕЗ СЪВПАДЕНИЕ"] += 1
            elif ако_статус == INELIGIBLE:
                броячи["БЕЗ ДОКАЗАТЕЛСТВО"] += 1
        резултат[цел.name] = {
            "човешки решения": len(еталон),
            "източници": len(източници),
            **речник_броячи(броячи),
            "грешни примери": грешни[:5],
        }
    return резултат


def речник_броячи(броячи: Counter) -> dict:
    return {к: броячи[к] for к in sorted(броячи)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Повторно ползване между чертежи")
    parser.add_argument("--json", type=pathlib.Path, default=None)
    args = parser.parse_args()

    изход = {}
    for етикет, само_свои in (("ПРОИЗВОДСТВО (само своя проект)", True),
                              ("ЕКСПЕРИМЕНТ (други чертежи)", False)):
        print(f"\n=== {етикет}")
        данни = оцени(само_свои)
        изход[етикет] = данни
        сбор = Counter()
        for име, р in sorted(данни.items()):
            if "пропуснат" in р:
                print(f"   {име:12s} {р['пропуснат']}")
                continue
            части = " ".join(f"{к}={v}" for к, v in р.items()
                             if к not in ("грешни примери", "източници"))
            print(f"   {име:12s} {части}")
            for к, v in р.items():
                if isinstance(v, int) and к != "източници":
                    сбор[к] += v
        print(f"   {'ОБЩО':12s} {dict(сбор)}")
    if args.json:
        args.json.write_text(json.dumps(изход, ensure_ascii=False, indent=1), encoding="utf-8")
        print("\nзаписан:", args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
