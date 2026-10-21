"""Обновяване на знанието от РАЗРЕШЕНИ външни източници — с ограничения.

Какво прави и какво НЕ прави:

* тегли само от изричен списък в ``izvori.json`` — няма свободно обхождане;
* има таван за време, обем, брой опити и общ брой заявки за едно пускане;
* пази отпечатък на изтегленото и пропуска непроменените (без дублиране);
* **третира изтегленото като ДАННИ** — нищо не се изпълнява, не се внася като
  код, не се разархивира;
* записва знанието като ``reference`` кандидат с източник, лиценз и дата —
  **никога като прието правило**. Външното знание не е доказателство за
  конкретния чертеж; то може само да ПРЕДЛОЖИ, а потвърждението идва от
  местните данни (изискване 6).

Зависимости, казано точно: нужен е обикновен HTTP достъп (``urllib`` от
стандартната библиотека). НЕ се ползва външен езиков модел, платен API, ключ или
чужди идентификационни данни. Без интернет функцията се връща спокойно и
разпознаването продължава с вече валидираната памет.

Разделяне на видовете знание (изискване 6) — стандартът НЕ се смесва:

``geometry``    — описание на форма (участъци, ъгли, топология);
``convention``  — проектантска условност (как се изписва нещо);
``dimension``   — правило за размери;
``production``  — производствено правило (куки, радиуси, добавки);
``standard``    — зависи от стандарт/версия/държава и носи изрично име на
                  стандарта; числата от различни стандарти не се смесват.
"""
from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

REGISTRY_NAME = "izvori.json"
EXTERNAL_NAME = "vunshno_znanie.json"
EXTERNAL_SCHEMA = "lion-rebar-external-knowledge/v1"

#: Ограничения за едно пускане. Целта е механизмът да не може да „избяга".
MAX_SOURCES_PER_RUN = 12
MAX_BYTES_PER_SOURCE = 4 * 1024 * 1024
MAX_TOTAL_SECONDS = 120.0
MAX_ATTEMPTS = 2
REQUEST_TIMEOUT = 15.0
MIN_SECONDS_BETWEEN_REQUESTS = 0.5

ВИДОВЕ = ("geometry", "convention", "dimension", "production", "standard")


@dataclass
class FetchOutcome:
    source_id: str
    url: str
    status: str            # OK | UNCHANGED | SKIPPED | ERROR | LIMIT
    detail: str = ""
    bytes: int = 0
    sha256: str = ""
    seconds: float = 0.0


@dataclass
class ExternalEntry:
    """Едно външно знание — винаги кандидат, никога прието само заради източника."""

    entry_id: str
    kind: str
    title: str
    statement: str
    source_id: str
    url: str
    licence: str
    accessed: str
    standard: str = ""
    version: str = ""
    limitations: str = ""
    state: str = "UNCONFIRMED"
    local_confirmation: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id, "kind": self.kind, "title": self.title,
            "statement": self.statement, "source_id": self.source_id,
            "url": self.url, "licence": self.licence, "accessed": self.accessed,
            "standard": self.standard, "version": self.version,
            "limitations": self.limitations, "state": self.state,
            "local_confirmation": dict(self.local_confirmation),
        }


def registry_path(root: Path) -> Path:
    return Path(root) / REGISTRY_NAME


def external_path(root: Path) -> Path:
    return Path(root) / EXTERNAL_NAME


def load_registry(root: Path) -> list[dict[str, Any]]:
    п = registry_path(root)
    if not п.is_file():
        return []
    д = json.loads(п.read_text(encoding="utf-8-sig"))
    редове = д.get("sources") if isinstance(д, dict) else д
    return [р for р in редове or () if isinstance(р, dict) and р.get("url")]


def _разрешен(url: str, allow_hosts: Iterable[str]) -> bool:
    from urllib.parse import urlparse

    разбор = urlparse(url)
    if разбор.scheme not in ("http", "https"):
        return False
    хост = (разбор.hostname or "").lower()
    позволени = {str(х).lower() for х in allow_hosts}
    return any(хост == п or хост.endswith("." + п) for п in позволени)


def fetch_sources(
    root: Path,
    *,
    offline: bool = False,
    max_sources: int = MAX_SOURCES_PER_RUN,
) -> list[FetchOutcome]:
    """Тегли разрешените източници с таваните по-горе. Нищо не се изпълнява."""

    изход: list[FetchOutcome] = []
    източници = load_registry(root)[:max_sources]
    ако_кеш = Path(root) / "izvori_cache"
    ако_кеш.mkdir(parents=True, exist_ok=True)
    позволени_хостове = {и.get("host") or "" for и in източници}
    позволени_хостове.discard("")
    начало = time.time()
    последна = 0.0

    for източник in източници:
        ид = str(източник.get("id") or източник.get("url"))
        url = str(източник.get("url"))
        if time.time() - начало > MAX_TOTAL_SECONDS:
            изход.append(FetchOutcome(ид, url, "LIMIT", "изчерпано общо време"))
            continue
        if offline:
            изход.append(FetchOutcome(ид, url, "SKIPPED", "офлайн режим"))
            continue
        if not _разрешен(url, позволени_хостове):
            изход.append(FetchOutcome(ид, url, "SKIPPED", "хостът не е в списъка"))
            continue

        т0 = time.time()
        грешка = ""
        for опит in range(1, MAX_ATTEMPTS + 1):
            пауза = MIN_SECONDS_BETWEEN_REQUESTS - (time.time() - последна)
            if пауза > 0:
                time.sleep(пауза)
            try:
                заявка = urllib.request.Request(
                    url, headers={"User-Agent": "LION-REBAR-learning/1.0"})
                with urllib.request.urlopen(заявка, timeout=REQUEST_TIMEOUT) as отговор:
                    данни = отговор.read(MAX_BYTES_PER_SOURCE + 1)
                последна = time.time()
                if len(данни) > MAX_BYTES_PER_SOURCE:
                    изход.append(FetchOutcome(ид, url, "LIMIT", "над тавана за обем",
                                              bytes=len(данни),
                                              seconds=time.time() - т0))
                    break
                отпечатък = hashlib.sha256(данни).hexdigest()
                файл = ако_кеш / (hashlib.sha256(url.encode()).hexdigest()[:16] + ".bin")
                старо = (hashlib.sha256(файл.read_bytes()).hexdigest()
                         if файл.is_file() else "")
                if старо == отпечатък:
                    изход.append(FetchOutcome(ид, url, "UNCHANGED", "същото съдържание",
                                              bytes=len(данни), sha256=отпечатък,
                                              seconds=time.time() - т0))
                    break
                файл.write_bytes(данни)
                изход.append(FetchOutcome(ид, url, "OK", "изтеглено",
                                          bytes=len(данни), sha256=отпечатък,
                                          seconds=time.time() - т0))
                break
            except (urllib.error.URLError, urllib.error.HTTPError, OSError,
                    ValueError) as изкл:
                грешка = "%s: %s" % (type(изкл).__name__, изкл)
                последна = time.time()
                if опит >= MAX_ATTEMPTS:
                    изход.append(FetchOutcome(ид, url, "ERROR", грешка,
                                              seconds=time.time() - т0))
    return изход


def load_external(root: Path) -> list[ExternalEntry]:
    п = external_path(root)
    if not п.is_file():
        return []
    д = json.loads(п.read_text(encoding="utf-8-sig"))
    if str(д.get("schema") or "") != EXTERNAL_SCHEMA:
        raise ValueError("непозната схема на външното знание: %r" % д.get("schema"))
    известни = set(ExternalEntry.__dataclass_fields__)  # noqa: SLF001
    return [ExternalEntry(**{к: v for к, v in р.items() if к in известни})
            for р in д.get("entries") or () if isinstance(р, dict)]


def save_external(root: Path, entries: Iterable[ExternalEntry]) -> Path:
    п = external_path(root)
    п.parent.mkdir(parents=True, exist_ok=True)
    редове = [е.to_dict() for е in entries]
    for ред in редове:
        if ред["kind"] not in ВИДОВЕ:
            raise ValueError("непознат вид знание: %r" % ред["kind"])
        if ред["kind"] in ("production", "standard") and not ред["standard"]:
            raise ValueError(
                "производствено правило без име на стандарт: %r" % ред["entry_id"])
    п.write_text(json.dumps({
        "schema": EXTERNAL_SCHEMA,
        "written_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "entries": редове,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    return п


def update(root: Path, *, offline: bool = False) -> dict[str, Any]:
    """Едно пускане на обновяването. Връща отчет; нищо не се приема автоматично."""

    начало = time.time()
    резултати = fetch_sources(root, offline=offline)
    записи = load_external(root)
    по_състояние: dict[str, int] = {}
    for е in записи:
        по_състояние[е.state] = по_състояние.get(е.state, 0) + 1
    return {
        "ran_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "offline": bool(offline),
        "registry": str(registry_path(root)),
        "sources_seen": len(load_registry(root)),
        "fetch": [
            {"source_id": р.source_id, "url": р.url, "status": р.status,
             "detail": р.detail, "bytes": р.bytes, "sha256": р.sha256[:16],
             "seconds": round(р.seconds, 2)}
            for р in резултати
        ],
        "external_entries": len(записи),
        "by_state": по_състояние,
        "limits": {
            "max_sources_per_run": MAX_SOURCES_PER_RUN,
            "max_bytes_per_source": MAX_BYTES_PER_SOURCE,
            "max_total_seconds": MAX_TOTAL_SECONDS,
            "max_attempts": MAX_ATTEMPTS,
            "min_seconds_between_requests": MIN_SECONDS_BETWEEN_REQUESTS,
        },
        "seconds": round(time.time() - начало, 2),
        "note": ("Външното знание е винаги UNCONFIRMED, докато местни данни не го "
                 "потвърдят. Нищо изтеглено не се изпълнява."),
    }
