"""Контролните суми на релийза, обвързани с ТОЧНИЯ код.

ЗАЩО. `SHA256_MANIFEST.json` дотук се правеше на ръка и затова остаряваше:
към 24.09.2026 той описваше rc43, а `APP_VERSION` беше rc44 — тоест пакетът не
можеше да каже кой код носи. „Еднозначен релийз“ значи точно това: сумите, кодът
и тестовият отчет да сочат едно и също състояние.

КАКВО ГАРАНТИРА. Манифестът се прави само от ЧИСТО работно дърво и записва
комита, от който е снет. Ако дървото има неприключени промени, генераторът
отказва — манифест на непроследено състояние не значи нищо.

Хешират се всички проследени от git файлове, БЕЗ самия манифест: той се ражда
след тях и не може да съдържа собствената си сума. Затова и `--check` го
пропуска, и проверката минава точно на комита, в който манифестът е добавен.

Пускане::

    .venv/Scripts/python.exe tools/manifest.py --write
    .venv/Scripts/python.exe tools/manifest.py --check
    .venv/Scripts/python.exe tools/manifest.py --write --tests отчет.xml \
        --evidence docs/RELEASE_v0.1.44.21-rc44_BG.md WINDOWS_ACCEPTANCE_rc44.md
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

КОРЕН = Path(__file__).resolve().parents[1]
МАНИФЕСТ = КОРЕН / "SHA256_MANIFEST.json"
СХЕМА = "lion-rebar-manifest/v1"
#: Манифестът не хешира себе си: ражда се след файловете, които описва.
СОБСТВЕНОТО_ИМЕ = "SHA256_MANIFEST.json"


class МанифестError(RuntimeError):
    pass


def _git(*аргументи: str) -> str:
    готово = subprocess.run(
        ["git", *аргументи],
        cwd=КОРЕН,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if готово.returncode:
        raise МанифестError(f"git {' '.join(аргументи)}: {готово.stderr.strip()}")
    return готово.stdout


def проследени_файлове() -> tuple[str, ...]:
    редове = [ред.strip() for ред in _git("ls-files").splitlines()]
    return tuple(sorted(ред for ред in редове if ред and ред != СОБСТВЕНОТО_ИМЕ))


def дървото_е_чисто() -> tuple[bool, str]:
    """Чисто ли е дървото по ПРОСЛЕДЕНИТЕ файлове.

    Непроследените папки не пречат: в дървото на собственика стоят работни
    материали на друг агент, които не влизат в релийза. Манифестът описва това,
    което git носи.
    """

    мръсни = [
        ред for ред in _git("status", "--porcelain").splitlines()
        if ред and not ред.startswith("??")
    ]
    return (not мръсни), "\n".join(мръсни)


def сума(път: Path) -> str:
    смилане = hashlib.sha256()
    with път.open("rb") as поток:
        for блок in iter(lambda: поток.read(1024 * 1024), b""):
            смилане.update(блок)
    return смилане.hexdigest()


def сумите(файлове: Iterable[str]) -> dict[str, str]:
    изход: dict[str, str] = {}
    for име in файлове:
        път = КОРЕН / име
        if not път.is_file():
            raise МанифестError(f"проследен файл липсва на диска: {име}")
        изход[име] = сума(път)
    return изход


def отчет_от_junit(път: Path) -> dict[str, Any]:
    """Числата на пакета от `--junitxml`, а не от паметта на автора."""

    import xml.etree.ElementTree as ET

    корен = ET.parse(път).getroot()
    възел = корен[0] if корен.tag == "testsuites" else корен
    данни = възел.attrib
    цели = {
        ключ: int(данни.get(ключ, 0))
        for ключ in ("tests", "errors", "failures", "skipped")
    }
    цели["passed"] = цели["tests"] - цели["errors"] - цели["failures"] - цели["skipped"]
    цели["timestamp"] = данни.get("timestamp", "")
    цели["seconds"] = float(данни.get("time", 0.0))
    return цели


def построй(
    *,
    version: str,
    evidence: Iterable[str] = (),
    tests: Path | None = None,
) -> dict[str, Any]:
    чисто, мръсни = дървото_е_чисто()
    if not чисто:
        raise МанифестError(
            "работното дърво има неприключени промени — манифест на непроследено "
            "състояние не значи нищо:\n" + мръсни
        )
    файлове = проследени_файлове()
    манифест: dict[str, Any] = {
        "schema": СХЕМА,
        "version": version,
        "commit": _git("rev-parse", "HEAD").strip(),
        "files": сумите(файлове),
        "evidence_files": list(evidence),
    }
    if tests is not None:
        манифест["tests"] = отчет_от_junit(tests)
    return манифест


def провери() -> list[str]:
    """Кои файлове се разминават с манифеста. Празен списък значи съвпадение."""

    if not МАНИФЕСТ.is_file():
        raise МанифестError(f"няма манифест: {МАНИФЕСТ}")
    записано = json.loads(МАНИФЕСТ.read_text(encoding="utf-8"))
    очаквани = записано.get("files") or {}
    разлики: list[str] = []
    for име, очаквана in sorted(очаквани.items()):
        път = КОРЕН / име
        if not път.is_file():
            разлики.append(f"липсва: {име}")
        elif сума(път) != очаквана:
            разлики.append(f"различен: {име}")
    налични = set(проследени_файлове())
    for име in sorted(налични - set(очаквани)):
        разлики.append(f"нов, извън манифеста: {име}")
    return разлики


def main() -> int:
    from lion_rebar.config.constants import APP_VERSION

    parser = argparse.ArgumentParser(description="Контролни суми на релийза")
    parser.add_argument("--write", action="store_true", help="записва манифеста")
    parser.add_argument("--check", action="store_true", help="сверява дървото с манифеста")
    parser.add_argument("--version", default=f"v{APP_VERSION}")
    parser.add_argument("--tests", type=Path, default=None, help="junitxml от пакета")
    parser.add_argument("--evidence", nargs="*", default=[])
    args = parser.parse_args()

    try:
        if args.check:
            разлики = провери()
            if разлики:
                print(f"РАЗМИНАВАНЕ  {len(разлики)} файла")
                for ред in разлики[:40]:
                    print("  ", ред)
                return 1
            записано = json.loads(МАНИФЕСТ.read_text(encoding="utf-8"))
            print(f"СЪВПАДА  {len(записано.get('files') or {})} файла, "
                  f"версия {записано.get('version')}, комит {записано.get('commit', '—')[:7]}")
            return 0

        манифест = построй(
            version=args.version,
            evidence=args.evidence,
            tests=args.tests,
        )
        if args.write:
            МАНИФЕСТ.write_text(
                json.dumps(манифест, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
                encoding="utf-8",
            )
            print(f"ЗАПИСАН  {МАНИФЕСТ.name}")
        print(f"версия {манифест['version']}  комит {манифест['commit'][:7]}  "
              f"файлове {len(манифест['files'])}")
        if "tests" in манифест:
            отчет = манифест["tests"]
            print(f"тестове  {отчет['passed']} минали, {отчет['skipped']} пропуснати, "
                  f"{отчет['failures'] + отчет['errors']} паднали")
        return 0
    except МанифестError as exc:
        print(f"ОТКАЗ  {exc}")
        return 2


if __name__ == "__main__":
    sys.path.insert(0, str(КОРЕН))
    raise SystemExit(main())
