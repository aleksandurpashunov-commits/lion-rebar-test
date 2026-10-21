# -*- coding: utf-8 -*-
"""Сравнението А / Б / В по раздел 8 — позиция по позиция, не само по бройки.

* **А** — замразеният етап 4: старият код, наученото изключено.
* **Б** — новият код, наученото изключено.
* **В** — същият нов код с валидираната памет след рестарт (замразена).

За всяка ПРОМЕНЕНА позиция се дава: старо състояние, ново състояние, причина,
доказателство и дали е проверена. Непроверените НЕ се броят за успех, затова
колоната „проверка" носи или източника на независимото доказателство, или
изрично „непроверена".

Само чете.
"""
from __future__ import annotations

import argparse
import json
import pathlib

#: Как се установява, че позицията е одобрена: авторитетният списък в копието.
ВРЪЗКИ = pathlib.Path("working") / "approved_geometry_links.json"


def _отчет(копие: str) -> pathlib.Path | None:
    папка = pathlib.Path(копие) / "output" / "04_REVIEW_REPORTS"
    ако = sorted(папка.rglob("*_geometry_batch.json")) if папка.is_dir() else []
    return ако[-1] if ако else None


def _одобрени(копие: str) -> set[str]:
    файл = pathlib.Path(копие) / ВРЪЗКИ
    if not файл.is_file():
        return set()
    д = json.loads(файл.read_text(encoding="utf-8"))
    записи = д.get("links") if isinstance(д, dict) and "links" in д else д
    if isinstance(записи, dict):
        return {str(к) for к in записи}
    return {str(з.get("annotation_handle") or "") for з in записи or ()}


def _прогон(файл: pathlib.Path) -> dict:
    д = json.loads(файл.read_text(encoding="utf-8"))
    копие = str(д.get("copy") or "")
    отчет = _отчет(копие) if копие else None
    редове = {}
    if отчет is not None:
        for ред in json.loads(отчет.read_text(encoding="utf-8")).get("rows") or ():
            ръчка = str(ред.get("annotation_handle") or "")
            if ръчка:
                редове[ръчка] = ред
    порта = д.get("gate") or {}
    return {
        "редове": редове,
        "одобрени": _одобрени(копие) if копие else set(),
        "порта": порта.get("approved"),
        "блокирани": len(порта.get("blocked_positions") or ()),
        "финал": (д.get("final_control") or {}).get("approved_after"),
        "статус": д.get("status"), "стоп": д.get("stop_code"),
        "учене": д.get("learning") or {},
        "секунди": (d := д.get("analysis_seconds")) and d or
                   (д.get("seconds") or {}).get("analysis"),
    }


def _причина(стар: dict | None, нов: dict | None) -> tuple[str, str, str]:
    """Причина, доказателство, проверка."""

    if стар is None:
        return ("нов ред в отчета", "-", "непроверена")
    слой_с = str((стар or {}).get("candidate_layer") or "")
    слой_н = str((нов or {}).get("candidate_layer") or "")
    бележки = [str(б) for б in ((нов or {}).get("candidate_notes") or ())]
    ако_кука = any("F006_OWNED_DIMENSIONS_CONFIRM" in б for б in бележки)
    разлика_с = abs(float((стар or {}).get("numeric_difference") or 0.0))
    разлика_н = abs(float((нов or {}).get("numeric_difference") or 0.0))
    if not слой_с and слой_н:
        return (
            "слоят %s е намерен по доказателство: позицията беше БЕЗ кандидат"
            % слой_н,
            "разлика надпис-геометрия %.3f cm" % разлика_н,
            "вътрешна: дължина + един обект на надпис "
            "(tools/etap5_proveri_test002.py)")
    if слой_с and слой_н and слой_с != слой_н:
        стари_коди = ",".join(str(к) for к
                              in ((стар or {}).get("review_reason_codes") or ()))
        return (
            "кандидатът се смени от %s на намерения %s" % (слой_с, слой_н),
            "разлика надпис-геометрия %.3f -> %.3f cm; преднина на първия "
            "%.3f -> %.3f; стари флагове: %s"
            % (разлика_с, разлика_н,
               float((стар or {}).get("candidate_score_gap") or 0.0),
               float((нов or {}).get("candidate_score_gap") or 0.0),
               стари_коди or "-"),
            "вътрешна: точно съвпадение на дължината срещу предишна разлика "
            "%.1f cm" % разлика_с if разлика_н < разлика_с
            else "непроверена: новата разлика НЕ е по-малка")
    if ако_кука:
        return ("отворените куки са потвърдени от собствените коти на контура",
                "котите лежат върху върховете на кандидата",
                "инвариант: нито едно производствено число не е сменено "
                "(tools/etap5_invariant.py)")
    стара = str((стар or {}).get("matcher_disposition") or "")
    нова = str((нов or {}).get("matcher_disposition") or "")
    if стара != нова:
        return ("присъдата на matcher-а: %s -> %s" % (стара, нова), "-",
                "непроверена")
    return ("състоянието се смени без разпозната причина", "-", "непроверена")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--a", required=True, type=pathlib.Path)
    parser.add_argument("--b", required=True, type=pathlib.Path)
    parser.add_argument("--v", type=pathlib.Path, default=None)
    parser.add_argument("--izhod", type=pathlib.Path,
                        default=pathlib.Path(r"C:\lr_exp\rezultat\etap_5\ABV.json"))
    args = parser.parse_args()

    таблица: list[dict] = []
    редове_промени: list[dict] = []
    for файл in sorted(args.b.glob("*.json")):
        if файл.name.startswith("_"):
            continue
        име = файл.stem
        а = _прогон(args.a / файл.name) if (args.a / файл.name).is_file() else None
        б = _прогон(файл)
        в = (_прогон(args.v / файл.name)
             if args.v and (args.v / файл.name).is_file() else None)
        ред = {"проект": име,
               "А": (а or {}).get("порта"), "Б": б.get("порта"),
               "В": (в or {}).get("порта"),
               "А_финал": (а or {}).get("финал"), "Б_финал": б.get("финал"),
               "В_финал": (в or {}).get("финал"),
               "В_намерени": list((в or {}).get("учене", {}).get(
                   "discovered_layers") or ()),
               "Б_намерени": list(б.get("учене", {}).get(
                   "discovered_layers") or ()),
               "В_приложени": len((в or {}).get("учене", {}).get("applied") or ()),
               }
        таблица.append(ред)

        if а is None:
            continue
        for ръчка in sorted(set(а["редове"]) | set(б["редове"])):
            беше = ръчка in а["одобрени"]
            стана = ръчка in б["одобрени"]
            if беше == стана:
                continue
            стар, нов = а["редове"].get(ръчка), б["редове"].get(ръчка)
            причина, доказателство, проверка = _причина(стар, нов)
            редове_промени.append({
                "проект": име, "надпис": ръчка,
                "текст": str((нов or стар or {}).get("annotation_text") or "")[:32],
                "старо": "одобрена" if беше else "НЕ е одобрена",
                "ново": "одобрена" if стана else "НЕ е одобрена",
                "причина": причина, "доказателство": доказателство,
                "проверка": проверка,
            })

    print("%-10s %-14s %-14s %-14s %s" % ("проект", "А порта", "Б порта", "В порта",
                                          "намерени слоеве (Б / В)"))
    for р in таблица:
        print("%-10s %-14s %-14s %-14s %s / %s"
              % (р["проект"], р["А"], р["Б"], р["В"],
                 ",".join(р["Б_намерени"]) or "-",
                 ",".join(р["В_намерени"]) or "-"))

    print("\nпозиции със СМЕНЕНО одобрение (А -> Б): %d" % len(редове_промени))
    по_причина: dict[str, int] = {}
    for р in редове_промени:
        по_причина[р["причина"][:60]] = по_причина.get(р["причина"][:60], 0) + 1
    for к, в in sorted(по_причина.items(), key=lambda кв: -кв[1]):
        print("   %-62s %d" % (к, в))
    непроверени = [р for р in редове_промени if р["проверка"] == "непроверена"]
    print("\nОТ ТЯХ НЕПРОВЕРЕНИ: %d" % len(непроверени))
    for р in непроверени[:15]:
        print("   %s / %s %s: %s" % (р["проект"], р["надпис"], р["текст"],
                                     р["причина"][:60]))

    args.izhod.write_text(json.dumps(
        {"таблица": таблица, "промени": редове_промени}, ensure_ascii=False,
        indent=1), encoding="utf-8")
    print("\nзаписано:", args.izhod)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
