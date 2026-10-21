# -*- coding: utf-8 -*-
r"""Проверява, че ``08_CHANGES.patch`` наистина възпроизвежда доставеното дърво.

Без тази проверка patch-ът е обещание. Тук се прави следното:

1. клонира се хранилището в отделна папка (споделени обекти, без копиране);
2. изважда се БАЗАТА — комитът, срещу който patch-ът е направен;
3. patch-ът се прилага;
4. всеки файл от ``SHA256_MANIFEST.json`` се сверява байт за байт с
   полученото дърво.

Разминаване се изброява поименно. Нищо в работното дърво не се пипа.

Пускане::

    .venv/Scripts/python.exe tools/opit11_proveri_patch.py ^
        --patch <път до 08_CHANGES.patch> --izhod <отчет.json>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import shutil
import subprocess
import tempfile

КОРЕН = pathlib.Path(__file__).resolve().parents[1]
МАНИФЕСТ = КОРЕН / "SHA256_MANIFEST.json"


def _git(папка: pathlib.Path, *аргументи: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *аргументи], cwd=str(папка), capture_output=True,
    )


def _сума(път: pathlib.Path) -> str:
    смес = hashlib.sha256()
    with път.open("rb") as поток:
        for парче in iter(lambda: поток.read(1 << 20), b""):
            смес.update(парче)
    return смес.hexdigest()


def главно() -> int:
    парсер = argparse.ArgumentParser(description=__doc__)
    парсер.add_argument("--patch", required=True)
    парсер.add_argument("--izhod", required=True)
    парсер.add_argument("--zapazi", action="store_true",
                        help="не трий временното дърво")
    аргументи = парсер.parse_args()
    патч = pathlib.Path(аргументи.patch).resolve()
    манифест = json.loads(МАНИФЕСТ.read_text(encoding="utf-8"))
    база = str(манифест.get("commit") or "")
    отчет: dict = {
        "схема": "opit11-proverka-na-patcha/v1",
        "patch": str(патч),
        "база": база,
        "файлове в манифеста": len(манифест.get("files") or {}),
    }
    временна = pathlib.Path(tempfile.mkdtemp(prefix="opit11_patch_"))
    дърво = временна / "proverka"
    try:
        клон = _git(КОРЕН, "clone", "--shared", "--no-checkout",
                    str(КОРЕН), str(дърво))
        отчет["клониране"] = клон.returncode
        if клон.returncode:
            отчет["ГРЕШКА"] = клон.stderr.decode("utf-8", "replace")[-800:]
            return _запиши(отчет, аргументи.izhod, временна, аргументи.zapazi)
        # Свеж клон взима ГЛОБАЛНИЯ ``core.autocrlf``, който на Windows често
        # е ``true`` — изваждането тогава сменя краищата на редовете и patch-ът
        # не се прилага, макар да е верен. Работното дърво е с ``false``.
        _git(дърво, "config", "core.autocrlf", "false")
        _git(дърво, "config", "core.eol", "lf")
        изваждане = _git(дърво, "checkout", "--force", база)
        отчет["изваждане на базата"] = изваждане.returncode
        if изваждане.returncode:
            отчет["ГРЕШКА"] = изваждане.stderr.decode("utf-8", "replace")[-800:]
            return _запиши(отчет, аргументи.izhod, временна, аргументи.zapazi)
        прилагане = _git(
            дърво, "apply", "--binary", "--whitespace=nowarn", str(патч)
        )
        отчет["прилагане"] = прилагане.returncode
        отчет["прилагане stderr"] = прилагане.stderr.decode(
            "utf-8", "replace")[-2000:]
        if прилагане.returncode:
            отчет["ГРЕШКА"] = "patch-ът НЕ се прилага върху базата"
            return _запиши(отчет, аргументи.izhod, временна, аргументи.zapazi)

        разлики: list[str] = []
        проверени = 0
        for име, очаквана in sorted((манифест.get("files") or {}).items()):
            път = дърво / име
            if not път.is_file():
                разлики.append("липсва след прилагането: %s" % име)
                continue
            проверени += 1
            if _сума(път) != очаквана:
                разлики.append("различен след прилагането: %s" % име)
        отчет["проверени файла"] = проверени
        отчет["разминавания"] = len(разлики)
        отчет["първите разминавания"] = разлики[:40]
        отчет["patch-ът възпроизвежда доставеното"] = not разлики
        return _запиши(отчет, аргументи.izhod, временна, аргументи.zapazi)
    finally:
        pass


def _запиши(отчет: dict, изход: str, временна: pathlib.Path,
            запази: bool) -> int:
    път = pathlib.Path(изход)
    път.parent.mkdir(parents=True, exist_ok=True)
    отчет["временно дърво"] = str(временна) if запази else "изтрито"
    път.write_text(
        json.dumps(отчет, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(json.dumps(
        {
            ключ: стойност
            for ключ, стойност in отчет.items()
            if ключ != "първите разминавания"
        },
        ensure_ascii=False, indent=1,
    ))
    if not запази:
        shutil.rmtree(временна, ignore_errors=True)
    return 0 if отчет.get("patch-ът възпроизвежда доставеното") else 1


if __name__ == "__main__":
    raise SystemExit(главно())
