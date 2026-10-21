# -*- coding: utf-8 -*-
"""Самоличност на един прогон — за да сочи всеки резултат към конкретно изпълнение.

Без това сравненията не са възпроизводими: етап 4 направи цяла таблица „с памет"
с празно хранилище, защото едно тире в името на файла не се забеляза, а нищо в
отчета не показваше коя памет всъщност е била заредена.

Обхватът е нарочно тесен — само ЧЕТЕ състояние и връща речник:

* ``run_id`` — уникален за изпълнението;
* ``code`` — комит и отпечатък на работното дърво (включително неверсионираното);
* ``settings`` — стойностите на ключовете от средата, които менят поведението;
* ``store`` — път, отпечатък и брой правила на паметта, ПРЕДИ и СЛЕД анализа;
* ``inputs`` — отпечатък на всеки входен файл.

Модулът не пише никъде и не пипа Qt.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

#: Ключовете от средата, които менят поведението на разпознаването или паметта.
#: Записват се ВИНАГИ, включително когато не са зададени — липсата е също
#: настройка и трябва да личи в отчета.
СЛЕДЕНИ_КЛЮЧОВЕ = (
    "LION_REBAR_DATA_ROOT",
    "LION_REBAR_NAUCHENO",
    "LION_REBAR_NAUCHENO_ALLOW_MISSING",
    "LION_REBAR_LEARNING",
    "LION_REBAR_AUTOCYCLE",
    "LION_REBAR_HARDCODED_ALIASES",
    "LION_REBAR_LOCKED_FAMILIES",
    "LION_REBAR_CONTENT_ONLY_GEOMETRY",
    "LION_REBAR_GEOMETRY_DISCOVERY",
    "LION_REBAR_INFERRED_TOLERANCE_CM",
)

СХЕМА = "lion-rebar-run-registry/v1"


def _отпечатък_на_файл(път: Path) -> dict[str, Any]:
    try:
        данни = път.read_bytes()
    except OSError as грешка:
        return {"path": str(път), "error": str(грешка)}
    return {"path": str(път), "sha256": hashlib.sha256(данни).hexdigest(),
            "bytes": len(данни)}


def _git(корен: Path, *аргументи: str) -> str:
    try:
        готово = subprocess.run(
            ["git", *аргументи], cwd=str(корен), capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ""
    return готово.stdout.strip() if готово.returncode == 0 else ""


def code_fingerprint(корен: Path | str) -> dict[str, Any]:
    """Комитът плюс отпечатък на РЕАЛНОТО дърво.

    Само комитът не стига: експериментът работи върху променено дърво. Затова
    се смята и отпечатък на самия `git diff` заедно с новите файлове, за да е
    различима всяка редакция.
    """

    корен = Path(корен)
    комит = _git(корен, "rev-parse", "HEAD")
    diff = _git(корен, "diff")
    нови = _git(корен, "ls-files", "--others", "--exclude-standard")
    подпис = hashlib.sha256(
        ("%s\n%s\n%s" % (комит, diff, нови)).encode("utf-8")).hexdigest()
    return {
        "commit": комит or "неизвестен",
        "dirty": bool(diff),
        "worktree_sha256": подпис,
        "diff_bytes": len(diff.encode("utf-8")),
        "untracked_count": len([р for р in нови.splitlines() if р.strip()]),
    }


def store_fingerprint() -> dict[str, Any]:
    """Пътят, отпечатъкът и съставът на паметта — такава, каквато е СЕГА.

    Не вдига изключение: при изрично зададено, но липсващо хранилище връща
    описание на грешката, за да личи в отчета. Прекратяването на измерването е
    работа на ``policy_bridge.load_store``, не на регистъра.
    """

    изрично = str(os.environ.get("LION_REBAR_NAUCHENO") or "").strip()
    try:
        from lion_rebar.learning.knowledge_store import KnowledgeStore, store_path
        from lion_rebar.utils.app_paths import AppPaths

        път = Path(изрично) if изрично else Path(store_path(AppPaths.default()))
    except Exception as грешка:  # noqa: BLE001 — регистърът никога не вали прогон
        return {"error": "%s: %s" % (type(грешка).__name__, грешка)}

    изход: dict[str, Any] = {"path": str(път), "explicit": bool(изрично),
                             "exists": път.is_file()}
    if not път.is_file():
        return изход
    изход.update(_отпечатък_на_файл(път))
    try:
        from lion_rebar.learning.knowledge_store import KnowledgeStore

        store = KnowledgeStore.load(път)
        по_състояние: dict[str, int] = {}
        for правило in store.all_rules():
            по_състояние[правило.state] = по_състояние.get(правило.state, 0) + 1
        изход["rules_total"] = len(store)
        изход["by_state"] = по_състояние
        изход["accepted_rule_ids"] = sorted(п.rule_id for п in store.accepted())
    except Exception as грешка:  # noqa: BLE001
        изход["error"] = "%s: %s" % (type(грешка).__name__, грешка)
    return изход


def settings_snapshot() -> dict[str, Any]:
    """Стойностите на следените ключове; ``None`` значи „не е зададен"."""

    return {ключ: os.environ.get(ключ) for ключ in СЛЕДЕНИ_КЛЮЧОВЕ}


def begin_run(
    *,
    code_root: Path | str,
    inputs: Iterable[Path | str] = (),
    label: str = "",
) -> dict[str, Any]:
    """Началният запис на прогона. Връща речник, който се допълва накрая."""

    return {
        "schema": СХЕМА,
        "run_id": uuid.uuid4().hex,
        "label": str(label or ""),
        "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "code": code_fingerprint(code_root),
        "settings": settings_snapshot(),
        "store_before": store_fingerprint(),
        "inputs": [_отпечатък_на_файл(Path(п)) for п in inputs],
    }


def finish_run(запис: dict[str, Any], *, outcome: str = "") -> dict[str, Any]:
    """Затваря записа: памет СЛЕД анализа и изход."""

    запис["finished_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    запис["store_after"] = store_fingerprint()
    запис["outcome"] = str(outcome or "")
    преди = (запис.get("store_before") or {}).get("sha256")
    след = (запис.get("store_after") or {}).get("sha256")
    запис["store_changed"] = bool(преди != след)
    return запис


def write_run(запис: Mapping[str, Any], папка: Path | str) -> Path:
    """Записва прогона като отделен файл, кръстен на своя run_id."""

    папка = Path(папка)
    папка.mkdir(parents=True, exist_ok=True)
    път = папка / ("run_%s.json" % запис.get("run_id", "bez_id"))
    път.write_text(json.dumps(запис, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    return път
