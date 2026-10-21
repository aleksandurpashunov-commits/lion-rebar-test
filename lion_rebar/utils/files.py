from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any


def ensure_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


#: Колко дълго се чака целта да се освободи, преди записът да бъде обявен за
#: провален. Виж обяснението в ``atomic_write_text``.
REPLACE_RETRY_SECONDS = 5.0
REPLACE_RETRY_INTERVAL = 0.05


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    """Запис през временен файл и ``os.replace``.

    ЗАЩО ИМА ОПИТИ. В Windows ``os.replace`` пада с ``PermissionError``, когато
    друг държи целта ОТВОРЕНА — втори процес на програмата, редактор на
    собственика, синхронизацията на стриймващия драйв или антивирусна проверка.
    Измерено на 01.10.2026 с два процеса върху един файл: освен загубената
    актуализация се видяха и паднал запис, и паднало четене. Паднал запис на
    ledger-а значи ЗАГУБЕНО решение, затова не се предаваме при първия отказ, а
    изчакваме целта да се освободи. След изтичане на времето грешката се вдига:
    мълчаливото продължаване без запис е точно дефектът, който гоним.

    Опитите НЕ заместват заключването (``utils.process_lock``). Заключването
    пази решението между нашите процеси; опитите пазят записа от чужд читател.
    """

    ensure_directory(path.parent)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        краен_срок = time.monotonic() + REPLACE_RETRY_SECONDS
        while True:
            try:
                os.replace(tmp_path, path)
                break
            except PermissionError:
                if time.monotonic() >= краен_срок:
                    raise
                time.sleep(REPLACE_RETRY_INTERVAL)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def atomic_write_json(path: Path, data: Any) -> None:
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_bytes(data: Any) -> bytes:
    return json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def copy_without_overwrite(source: Path, destination: Path) -> Path:
    ensure_directory(destination.parent)
    if destination.exists():
        stem = destination.stem
        suffix = destination.suffix
        index = 1
        while True:
            candidate = destination.with_name(f"{stem}_{index}{suffix}")
            if not candidate.exists():
                destination = candidate
                break
            index += 1
    shutil.copy2(source, destination)
    return destination


def safe_filename(value: str, fallback: str = "file") -> str:
    cleaned = re.sub(r"[^0-9A-Za-zА-Яа-я._-]+", "_", value.strip())
    cleaned = cleaned.strip("._-")
    return cleaned or fallback


def set_read_only(path: Path) -> None:
    try:
        path.chmod(0o444)
    except OSError:
        pass


def set_writable(path: Path) -> None:
    try:
        path.chmod(0o644)
    except OSError:
        pass
