"""ЕКСПЕРИМЕНТ: кои отслабвания на строгия контекст дават полза без грешки.

ОТКРИТОТО ПРЕДИ ТОВА (CENZUS_DOKAZATELSTVA.json, 83 доставени решения):
  56 (67%) са ПОТВЪРЖДЕНИЯ — инженерът е казал „вярно е"; не носят ново решение
  13 (16%) падат, защото няма кандидат-форма (``candidate_shape_code``)
  13 (16%) са годни
   1 е оттеглено

Затова тук се мерят ТРИ независими отслабвания, всяко със своя риск:

  К (кандидат)  — допуска ред БЕЗ кандидат-форма, с изричен признак
                  ``NO_CANDIDATE_SHAPE``. Печели точно 13-те загубени.
  Б (бройка)    — маха надписа и броя от ключа. Причина: „468N18x410" и
                  „40N18x410" са същото желязо в различен брой, а броят се
                  чете от надписа, не от решението.
  Ч (чертежи)   — индексът вижда други СЕМЕЙСТВА, не само своя проект.

ЧЕСТНОСТТА НА МЕРКАТА
  * Еталон е решението на инженера от дневника. Автоматичните връзки не се
    ползват: те са изходът на самата програма.
  * Решение не може да съвпадне със себе си (кодът изключва своята дръжка).
  * В рамките на един проект мярката показва дали инженерът е бил ПОСЛЕДОВАТЕЛЕН,
    не дали правилото предсказва. Предсказване се мери само в режим Ч.
  * Режим Б се брои за верен само ако броят в решението на инженера съвпада с
    броя от надписа — иначе „взимаме броя от надписа" е само по себе си грешно.
  * ГРЕШНО е опасният изход и се отчита пълно, не се укрива.

Заключените семейства (виж NABORI.json) не влизат без ``--zaklyucheni``.
Нищо не се записва в проектите.
"""
from __future__ import annotations

import argparse
import itertools
import json
import pathlib
import sys
from collections import Counter

КОРЕН = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(КОРЕН))

from lion_rebar.review import decision_suggestions as ds  # noqa: E402
from lion_rebar.review.decision_suggestions import (  # noqa: E402
    CONFLICT, INELIGIBLE, NO_MATCH, READY, DecisionSuggestionIndex,
)

ПРОЕКТИ = pathlib.Path(r"C:\lr_exp\proekti")
ПРОГОНИ = pathlib.Path(r"C:\lr_exp\data\full_path_runs")

СЕМЕЙСТВО = {
    "т-1": "т-1", "s-9": "s-9", "s-11": "s-11", "s-8": "s-8", "s-10": "s-10",
    "s-7": "s-7", "s-1": "s-1..s-6/а-1", "d-1": "d-1", "d-2": "d-2",
    "Тест-003": "тест-001/Тест-003", "Тест-002": "Тест-002",
    "тест-004": "тест-004/005", "тест-006": "тест-006",
}
ЗАКЛЮЧЕНИ = {"т-1", "Тест-002", "s-7", "s-1..s-6/а-1"}

#: Полетата на ключа без тези, които носят броя. Целият надпис („468N18x410")
#: съдържа броя, затова пада заедно с полето ``count``.
БЕЗ_БРОЙКА = tuple(
    име for име in ds._CONTEXT_FIELDS if име not in ("annotation_text", "count")
)
ВСИЧКИ_ПОЛЕТА = tuple(ds._CONTEXT_FIELDS)
ПРИЗНАК_БЕЗ_КАНДИДАТ = "NO_CANDIDATE_SHAPE"

_ОРИГИНАЛЕН_КОНТЕКСТ = ds.strict_context_from_review_row


def _контекст_без_кандидат(row, *, identity, engine_version):
    """Допуска ред без кандидат-форма, като обявява липсата изрично в ключа.

    Не е мълчаливо отслабване: признакът влиза в ключа, затова ред без кандидат
    може да съвпадне само с друг ред без кандидат.
    """

    контекст, липсва = _ОРИГИНАЛЕН_КОНТЕКСТ(
        row, identity=identity, engine_version=engine_version)
    if контекст is not None or set(липсва) != {"candidate_shape_code"}:
        return контекст, липсва
    подменен = dict(row)
    подменен["master_shape_code"] = ПРИЗНАК_БЕЗ_КАНДИДАТ
    подменен["suggested_shape_code"] = ПРИЗНАК_БЕЗ_КАНДИДАТ
    return _ОРИГИНАЛЕН_КОНТЕКСТ(
        подменен, identity=identity, engine_version=engine_version)


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
    последни = {}
    for р in изход:
        последни[р["дръжка"]] = р
    return list(последни.values())


def _отчет(проект: pathlib.Path) -> dict | None:
    """Пакетният отчет от запазеното копие на прогона, ако има такова."""

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


def _подпис_човек(решение: dict, *, с_бройка: bool) -> tuple:
    сегменти = решение.get("production_segment_lengths_cm") or []
    основа = (
        str(решение.get("shape_code") or "").upper(),
        round(float(решение.get("diameter_mm") or 0), 3),
        round(float(решение.get("production_length_cm") or 0), 2),
        tuple(round(float(s), 2) for s in сегменти),
    )
    return (int(решение.get("count") or 0),) + основа if с_бройка else основа


def _подпис_предложение(п, *, с_бройка: bool) -> tuple:
    основа = (
        str(п.shape_code or "").upper(),
        round(float(п.diameter_mm or 0), 3),
        round(float(п.production_length_cm or 0), 2),
        tuple(round(float(s), 2) for s in (п.segment_lengths_cm or ())),
    )
    return (int(п.count or 0),) + основа if с_бройка else основа


def _индекс(източници: list[pathlib.Path]) -> DecisionSuggestionIndex:
    """Един индекс от няколко проекта — сливане на кофите без нов запис."""

    обединени: dict[str, list] = {}
    for и in източници:
        частичен = DecisionSuggestionIndex.from_sources(project_root=и)
        for ключ, стойности in частичен._buckets.items():  # noqa: SLF001 — експеримент
            обединени.setdefault(ключ, []).extend(стойности)
    индекс = DecisionSuggestionIndex(())
    индекс._buckets = {к: tuple(v) for к, v in обединени.items()}  # noqa: SLF001
    return индекс


def оцени(*, кандидат: bool, бройка: bool, чертежи: bool,
          със_заключени: bool) -> dict:
    """Всеки от трите ключа включва едно отслабване; изходът е по проекти."""

    всички = [p for p in sorted(ПРОЕКТИ.iterdir()) if p.is_dir()]
    цели = [p for p in всички
            if със_заключени or СЕМЕЙСТВО.get(p.name, p.name) not in ЗАКЛЮЧЕНИ]

    if кандидат:
        ds.strict_context_from_review_row = _контекст_без_кандидат
    if бройка:
        ds._CONTEXT_FIELDS = БЕЗ_БРОЙКА
    try:
        резултат = {}
        for цел in цели:
            еталон = _решения(цел)
            if not еталон:
                continue
            отчет = _отчет(цел)
            if отчет is None:
                резултат[цел.name] = {"пропуснат": "няма отчет"}
                continue
            редове = {str(r.get("annotation_handle") or ""): r
                      for r in отчет.get("rows") or []}
            самоличност = отчет.get("data_identity") or {}
            if not самоличност:
                for r in отчет.get("rows") or []:
                    if r.get("data_identity"):
                        самоличност = r["data_identity"]
                        break
            версия = str((отчет.get("analysis_engine") or {}).get("engine_version") or "")
            своето = СЕМЕЙСТВО.get(цел.name, цел.name)
            източници = [p for p in всички
                         if СЕМЕЙСТВО.get(p.name, p.name) != своето] \
                if чертежи else [цел]
            индекс = _индекс(източници)

            броячи: Counter = Counter()
            грешни: list = []
            броят_не_пасва: list = []
            for случай in еталон:
                ред = редове.get(случай["дръжка"])
                if ред is None:
                    броячи["без ред"] += 1
                    continue
                предложение = индекс.lookup(
                    ред, identity=самоличност, engine_version=версия)
                if предложение.status == READY and предложение.proposal is not None:
                    еднакво = (
                        _подпис_предложение(предложение.proposal, с_бройка=not бройка)
                        == _подпис_човек(случай["решение"], с_бройка=not бройка))
                    # При режим Б броят идва от надписа на целевия ред: ако
                    # инженерът е решил с ДРУГ брой, ключът без бройка е негоден.
                    if еднакво and бройка:
                        от_надписа = ds._positive_integer_text(
                            ред.get("annotation_count") or ред.get("original_count")
                            or ред.get("production_count"))
                        решен = str(int(случай["решение"].get("count") or 0))
                        if от_надписа and от_надписа != решен:
                            еднакво = False
                            броят_не_пасва.append({
                                "надпис": случай["надпис"],
                                "брой_от_надписа": от_надписа,
                                "решен_брой": решен,
                            })
                    броячи["ВЯРНО" if еднакво else "ГРЕШНО"] += 1
                    if not еднакво and len(грешни) < 5:
                        грешни.append({
                            "надпис": случай["надпис"],
                            "човек": _подпис_човек(
                                случай["решение"], с_бройка=not бройка),
                            "предложено": _подпис_предложение(
                                предложение.proposal, с_бройка=not бройка),
                        })
                elif предложение.status == CONFLICT:
                    броячи["КОНФЛИКТ"] += 1
                elif предложение.status == NO_MATCH:
                    броячи["БЕЗ СЪВПАДЕНИЕ"] += 1
                elif предложение.status == INELIGIBLE:
                    броячи["БЕЗ ДОКАЗАТЕЛСТВО"] += 1
            резултат[цел.name] = {
                "решения": len(еталон),
                **{к: броячи[к] for к in sorted(броячи)},
                "грешни": грешни,
                "броят_не_пасва": броят_не_пасва[:5],
            }
        return резултат
    finally:
        ds.strict_context_from_review_row = _ОРИГИНАЛЕН_КОНТЕКСТ
        ds._CONTEXT_FIELDS = ВСИЧКИ_ПОЛЕТА


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Кои отслабвания на ключа дават полза без грешки")
    parser.add_argument("--json", type=pathlib.Path, default=None)
    parser.add_argument("--zaklyucheni", action="store_true",
                        help="включва и заключените семейства (финално измерване)")
    args = parser.parse_args()

    изход = {}
    for кандидат, бройка, чертежи in itertools.product((False, True), repeat=3):
        етикет = ("К" if кандидат else "-") + ("Б" if бройка else "-") + \
                 ("Ч" if чертежи else "-")
        данни = оцени(кандидат=кандидат, бройка=бройка, чертежи=чертежи,
                      със_заключени=args.zaklyucheni)
        изход[етикет] = данни
        сбор: Counter = Counter()
        for р in данни.values():
            if "пропуснат" in р:
                continue
            for к, v in р.items():
                if isinstance(v, int):
                    сбор[к] += v
        print("{0}  решения={1:3d}  ВЯРНО={2:3d}  ГРЕШНО={3:3d}  КОНФЛИКТ={4:3d}"
              "  БЕЗ_СЪВП={5:3d}  БЕЗ_ДОК={6:3d}".format(
                  етикет, сбор["решения"], сбор["ВЯРНО"], сбор["ГРЕШНО"],
                  сбор["КОНФЛИКТ"], сбор["БЕЗ СЪВПАДЕНИЕ"],
                  сбор["БЕЗ ДОКАЗАТЕЛСТВО"]))

    print("\nК=допуска ред без кандидат-форма  Б=ключ без бройка  "
          "Ч=индекс от други чертежи")
    print("Набор:", "РАЗВОЕН + ЗАКЛЮЧЕН" if args.zaklyucheni else "само РАЗВОЕН")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(изход, ensure_ascii=False, indent=1),
                             encoding="utf-8")
        print("записан:", args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
