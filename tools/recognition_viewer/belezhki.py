# -*- coding: utf-8 -*-
"""Бележките на оператора от прегледа: MASTER форма или пояснение за позиция.

Собственик, 07.10.2026: „когато маркирам даден текст и позиция с десен бутон
да мога да му избера мастър форма или пояснение за позицията, ако не е
намерена“.

Бележките НЕ са решение в проекта. Записват се в отделен файл до
``viewer.html`` и не стигат до одобренията, портата или експорта — прегледът
остава инструмент само за четене спрямо програмата. Те са вход за разговора:
собственикът казва какво вижда, Claude го чете от файла, а промяната в
разпознаването минава по обичайния път (предложение → тестове → решение).

Бележката е вързана за чертежа по ``source_sha256``. Бележки за друг чертеж
или друга редакция не се зареждат — решенията не се наследяват между чертежи
(CLAUDE.md, раздел 1).
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

ФАЙЛ = "belezhki_operator.json"
СХЕМА = "lion-rebar-viewer-notes/v1"
НАЙ_ДЪЛГО_ПОЯСНЕНИЕ = 2000


class НевалиднаБележка(ValueError):
    """Бележката не може да се запише: липсва позиция, форма или текст."""


def форми_от_engine(отчет_път: Path, отчет: Mapping[str, Any]) -> list[dict[str, Any]]:
    """MASTER формите на engine-а, с който е правен АНАЛИЗЪТ.

    Търси snapshot-а в ``<проект>/master`` (отчетът е в
    ``<проект>/output/04_REVIEW_REPORTS/<прогон>/``) и го приема САМО ако
    ``content_sha256`` съвпада с ``analysis_engine`` на отчета. Друг engine би
    предложил форми, които анализът не е виждал — тогава списъкът е празен и
    менюто казва, че формите не са заредени, вместо да предложи чужди.
    """

    очакван = (отчет.get("analysis_engine") or {}).get("content_sha256") or ""
    if not очакван:
        return []
    try:
        папка = Path(отчет_път).resolve().parents[3] / "master"
    except IndexError:
        return []
    if not папка.is_dir():
        return []
    for файл in sorted(папка.glob("engine_*.json")):
        try:
            данни = json.loads(файл.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if данни.get("content_sha256") != очакван:
            continue
        форми = []
        for форма in данни.get("shapes") or ():
            код = str(форма.get("code") or "").strip()
            if not код:
                continue
            форми.append(
                {
                    "code": код,
                    "name": str(форма.get("name") or ""),
                    "active": bool(форма.get("active")),
                }
            )
        return форми
    return []


def празни(самоличност: str, проект: str) -> dict[str, Any]:
    return {"schema": СХЕМА, "source_sha256": самоличност, "project": проект, "notes": {}}


def зареди(папка: Path, самоличност: str, проект: str) -> dict[str, Any]:
    """Бележките за ТОЗИ чертеж. Файл за друг чертеж не се чете, а се отмества.

    Отместването (не изтриването) пази чуждата работа: собственикът може да
    пусне прегледа върху друга редакция и пак да намери старите си бележки.
    """

    път = Path(папка) / ФАЙЛ
    if not път.is_file():
        return празни(самоличност, проект)
    try:
        данни = json.loads(път.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        данни = None
    if (
        not isinstance(данни, dict)
        or данни.get("schema") != СХЕМА
        or данни.get("source_sha256") != самоличност
        or not isinstance(данни.get("notes"), dict)
    ):
        печат = datetime.now().strftime("%Y%m%d_%H%M%S")
        път.replace(път.with_name(f"belezhki_operator.drug_chertezh_{печат}.json"))
        return празни(самоличност, проект)
    return данни


def _запиши_атомарно(път: Path, данни: Mapping[str, Any]) -> None:
    път.parent.mkdir(parents=True, exist_ok=True)
    fd, временен = tempfile.mkstemp(prefix=".belezhki_", suffix=".json", dir=str(път.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as поток:
            json.dump(данни, поток, ensure_ascii=False, indent=2)
            поток.flush()
            os.fsync(поток.fileno())
        os.replace(временен, път)
    except BaseException:
        Path(временен).unlink(missing_ok=True)
        raise


def приложи(
    папка: Path,
    самоличност: str,
    проект: str,
    заявка: Mapping[str, Any],
    *,
    известни: Mapping[str, str],
    форми: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Записва или изтрива една бележка и връща всички.

    ``известни`` е ключ → надпис за всичко, върху което може да се щракне:
    позициите от отчета и надписите по чертежа. Ключ извън тях е отказ —
    бележка без връзка с обект в чертежа не може да се проследи.
    """

    ключ = str(заявка.get("key") or "").strip()
    if not ключ or ключ not in известни:
        raise НевалиднаБележка(f"Няма такъв обект в чертежа: {ключ or '—'}")
    данни = зареди(папка, самоличност, проект)
    if заявка.get("delete"):
        данни["notes"].pop(ключ, None)
        _запиши_атомарно(Path(папка) / ФАЙЛ, данни)
        return данни

    код = str(заявка.get("master_code") or "").strip()
    пояснение = str(заявка.get("note") or "").strip()
    if not код and not пояснение:
        raise НевалиднаБележка("Избери MASTER форма или напиши пояснение.")
    if len(пояснение) > НАЙ_ДЪЛГО_ПОЯСНЕНИЕ:
        raise НевалиднаБележка(f"Пояснението е над {НАЙ_ДЪЛГО_ПОЯСНЕНИЕ} знака.")
    име = ""
    if код:
        форма = next((ф for ф in форми if ф.get("code") == код), None)
        if форма is None:
            # Код, който engine-ът на анализа не познава, е догадка — отказ.
            raise НевалиднаБележка(f"Формата {код} я няма в engine-а на анализа.")
        име = str(форма.get("name") or "")
    данни["notes"][ключ] = {
        "key": ключ,
        "kind": str(заявка.get("kind") or ""),
        "text": известни[ключ],
        "master_code": код,
        "master_name": име,
        "note": пояснение,
        "at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    _запиши_атомарно(Path(папка) / ФАЙЛ, данни)
    return данни
