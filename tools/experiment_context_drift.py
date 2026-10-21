"""ДИАГНОЗА: съвпада ли ключът на ДОКАЗАТЕЛСТВОТО с ключа на ТЪРСЕНЕТО.

Индексът строи ключ два пъти по различни пътища:

  ДОКАЗАТЕЛСТВО — от ``_historical_context(row, payload)``, тоест от записаното
                  решение (какъв е бил редът, когато инженерът е решавал);
  ТЪРСЕНЕ       — от днешния ред на пакетния отчет.

Ако двата ключа се различават дори в едно поле, предложение НЕ МОЖЕ да се яви,
колкото и решения да има. Затова тук за един и същ handle се вадят и двата
ключа и се сравняват поле по поле. Само чете.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections import Counter

КОРЕН = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(КОРЕН))

from lion_rebar.review import decision_suggestions as ds  # noqa: E402

ПРОЕКТИ = pathlib.Path(r"C:\lr_exp\proekti")
ПРОГОНИ = pathlib.Path(r"C:\lr_exp\data\full_path_runs")


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


def _годни_доказателства(проект: pathlib.Path):
    """Същият път като ``_eligible_evidence``, но връща и междинните части."""

    доставени = ds._delivered_project_events(проект)
    слети, _ = ds._merge_events(доставени)
    жизнен, кандидати = [], {}
    for стойност in слети.values():
        плик = ds._mapping(ds._mapping(стойност.event.get("payload")).get("human_decision"))
        стабилен = ds._text(стойност.event.get("stable_event_id"))
        if (not ds.is_supported_decision_contract(плик.get("schema"))
                or ds._text(плик.get("event_id")) != стабилен
                or ds._upper(плик.get("event_type")) not in ds.DECISION_EVENT_TYPES):
            continue
        жизнен.append(стойност)
        if (ds._upper(плик.get("event_type")) == ds._EVENT_TYPE
                and bool(плик.get("active", True))
                and стойност.delivery_sources & {"SESSION_DB_ENDED", "PROJECT_OUTBOX_DELIVERED"}):
            кандидати[стабилен] = стойност
    редове = ds._decision_rows([и.event for и in жизнен])
    изход = []
    for ред in редове:
        доставено = кандидати.get(ds._text(ред.get("event_id")))
        if доставено is None or not ds._row_is_eligible(ред):
            continue
        плик = ds._mapping(ds._mapping(доставено.event.get("payload")).get("human_decision"))
        payload = ds._mapping(плик.get("payload"))
        самоличност = ds._mapping(плик.get("source_identity"))
        контекст, липсва = ds.strict_context_from_review_row(
            ds._historical_context(ред, payload),
            identity=самоличност,
            engine_version=(ds._text(доставено.event.get("engine_version"))
                            or ds._text(самоличност.get("engine_version"))),
        )
        изход.append({
            "handle": ds._upper(payload.get("annotation_handle")
                                or ds._mapping(payload.get("row_context")).get("annotation_handle")),
            "надпис": ds._text(payload.get("annotation_text"))
                      or ds._text(ds._mapping(payload.get("row_context")).get("annotation_text")),
            "контекст": контекст,
            "липсва": липсва,
            "самоличност": самоличност,
        })
    return изход


def main() -> int:
    parser = argparse.ArgumentParser(description="Разминава ли се ключът на двата пътя")
    parser.add_argument("проекти", nargs="+")
    args = parser.parse_args()

    общо_разминавания: Counter = Counter()
    for име in args.проекти:
        проект = ПРОЕКТИ / име
        отчет = _отчет(проект)
        if отчет is None:
            print(име, "— няма отчет")
            continue
        редове = {ds._upper(r.get("annotation_handle")): r for r in отчет.get("rows") or []}
        самоличност_днес = отчет.get("data_identity") or {}
        if not самоличност_днес:
            for r in отчет.get("rows") or []:
                if r.get("data_identity"):
                    самоличност_днес = r["data_identity"]
                    break
        версия = str((отчет.get("analysis_engine") or {}).get("engine_version") or "")

        print("\n" + "=" * 70)
        print(име)
        for случай in _годни_доказателства(проект):
            ред = редове.get(случай["handle"])
            if ред is None:
                print("  %-16s няма ред в днешния отчет" % случай["handle"])
                continue
            днес, липсва_днес = ds.strict_context_from_review_row(
                ред, identity=самоличност_днес, engine_version=версия)
            ист = случай["контекст"]
            if ист is None:
                print("  %-16s %-18s ДОКАЗАТЕЛСТВОТО е без ключ: %s"
                      % (случай["handle"], случай["надпис"][:18], ", ".join(случай["липсва"])))
                continue
            if днес is None:
                print("  %-16s %-18s ТЪРСЕНЕТО е без ключ: %s"
                      % (случай["handle"], случай["надпис"][:18], ", ".join(липсва_днес)))
                continue
            а, б = dict(ист.fields), dict(днес.fields)
            разлики = [(к, а.get(к), б.get(к)) for к in б if а.get(к) != б.get(к)]
            if not разлики:
                print("  %-16s %-18s КЛЮЧОВЕТЕ СЪВПАДАТ" % (случай["handle"], случай["надпис"][:18]))
                continue
            print("  %-16s %-18s %d разлики:" % (случай["handle"], случай["надпис"][:18], len(разлики)))
            for к, стар, нов in разлики:
                общо_разминавания[к] += 1
                print("        %-26s доказателство=%r" % (к, (стар or "")[:38]))
                print("        %-26s днес         =%r" % ("", (нов or "")[:38]))

    if общо_разминавания:
        print("\n=== КОИ ПОЛЕТА СЕ РАЗМИНАВАТ НАЙ-ЧЕСТО")
        for к, брой in общо_разминавания.most_common():
            print("  %4d  %s" % (брой, к))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
