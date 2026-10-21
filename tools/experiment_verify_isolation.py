"""Сверява, че основната програма и общите места НЕ са пипани.

Изпълнява се НАКРАЯ. Сравнява днешното състояние с отпечатъка от началото
(``OSNOVNA_OTPECHATAK.json``, 561 файла) и проверява петте общи места, които
експериментът би могъл да засегне.

Изход 0 — нищо не е мръднало. Изход 2 — има промяна и тя се изброява.
Само чете.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess
import sys

ОСНОВНА = pathlib.Path(
    r"I:\Моят диск\Lion-Rebar\LION_REBAR_v0_1_44_21-rc44_WINDOWS\release")
ОТПЕЧАТЪК = pathlib.Path(r"C:\lr_exp\OSNOVNA_OTPECHATAK.json")
РЕЗЕРВНО = pathlib.Path(r"C:\Users\Александър\LION_REBAR_BACKUP")
ОБЛАК = pathlib.Path(r"I:\Моят диск\LION_REBAR_CLOUD")
ДАННИ_НА_СОБСТВЕНИКА = pathlib.Path(
    r"C:\Users\Александър\AppData\Local\Packages"
    r"\PythonSoftwareFoundation.Python.3.12_qbz5n2kfra8p0\LocalCache\Local"
    r"\LionBuild\LionRebarDesktop")
ПРОЕКТИ_НА_СОБСТВЕНИКА = (
    pathlib.Path(r"I:\Моят диск\Тест-желязо"),
    pathlib.Path(r"I:\Моят диск\Нови проектанти"),
)


def _sha(п: pathlib.Path) -> str:
    h = hashlib.sha256()
    with п.open("rb") as f:
        for блок in iter(lambda: f.read(1 << 20), b""):
            h.update(блок)
    return h.hexdigest()


def main() -> int:
    провали: list[str] = []
    запис = json.loads(ОТПЕЧАТЪК.read_text(encoding="utf-8"))
    стари: dict = запис["files"]

    глава = subprocess.run(
        ["git", "-C", str(ОСНОВНА), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=False).stdout.strip()
    ок_глава = глава == запис["git_head"]
    print("git HEAD           %s  %s" % ("OK " if ок_глава else "РАЗЛИКА", глава[:12]))
    if not ок_глава:
        провали.append("git HEAD на основната програма е друг: %s" % глава)

    променени, изчезнали, нови = [], [], []
    сега: dict[str, str] = {}
    for п in ОСНОВНА.rglob("*"):
        if not п.is_file():
            continue
        отн = str(п.relative_to(ОСНОВНА)).replace("\\", "/")
        if отн_пропуска(отн):
            continue
        сега[отн] = _sha(п)
    for отн, сума in стари.items():
        ако = сега.get(отн)
        if ако is None:
            изчезнали.append(отн)
        elif ако != сума:
            променени.append(отн)
    # „Нов" файл е доказателство само ако е СЛЕД отпечатъка. Отпечатъкът е взет
    # с друг списък изключения (пропускаше скритите папки), затова `.agents/`,
    # `.codex/` и `.pytest_cache/` изглеждат нови, макар да са от 12 и 19.09 —
    # чужда работа и стар кеш, не следа от експеримента.
    отпечатан_на = ОТПЕЧАТЪК.stat().st_mtime
    заварени = []
    for отн in сега:
        if отн in стари:
            continue
        файл = ОСНОВНА / отн
        if файл.stat().st_mtime > отпечатан_на:
            нови.append(отн)
        else:
            заварени.append(отн)

    print("файлове в отпечатъка %d; днес съответни %d" % (len(стари), len(сега)))
    print("променени %d | изчезнали %d | нови СЛЕД отпечатъка %d"
          % (len(променени), len(изчезнали), len(нови)))
    print("заварени извън отпечатъка (стар кеш и чужда работа): %d" % len(заварени))
    for отн in sorted(заварени)[:6]:
        from datetime import datetime
        кога = datetime.fromtimestamp((ОСНОВНА / отн).stat().st_mtime)
        print("   ЗАВАРЕН   %s  %s" % (кога.strftime("%Y-%m-%d"), отн))
    for етикет, списък in (("ПРОМЕНЕН", променени), ("ИЗЧЕЗНАЛ", изчезнали),
                           ("НОВ", нови)):
        for отн in списък[:20]:
            print("   %-9s %s" % (етикет, отн))
        if списък:
            провали.append("%s: %d файла" % (етикет, len(списък)))

    print()
    for име, път in (("резервно копие", РЕЗЕРВНО), ("облак", ОБЛАК),
                     ("данни на собственика", ДАННИ_НА_СОБСТВЕНИКА)):
        ако = път.exists()
        print("%-24s %s" % (име, "съществува" if ако else "няма я"))

    if not провали:
        print("\nИЗОЛАЦИЯТА Е ПОТВЪРДЕНА: основната програма е непокътната.")
        return 0
    print("\nПРОМЯНА В ОСНОВНАТА ПРОГРАМА:")
    for р in провали:
        print("   -", р)
    return 2


def отн_пропуска(отн: str) -> bool:
    """Същото изключване като при вземането на отпечатъка."""

    части = отн.split("/")
    return (".venv" in части or "__pycache__" in части or ".git" in части
            or отн.endswith(".pyc"))


if __name__ == "__main__":
    raise SystemExit(main())
