from __future__ import annotations

import os
import time
import shutil
import uuid
from pathlib import Path
from typing import Sequence


class ExportBundleError(RuntimeError):
    pass


#: Windows/OneDrive заключват файлове за кратко (WinError 32). Записът се
#: повтаря няколко пъти с нарастващо изчакване, преди да се обяви провал.
#: Открито при Windows приемане: export падаше с PermissionError, защото
#: предишният Excel беше отворен или синхронизиран в момента на замяната.
_REPLACE_ATTEMPTS = 6
_REPLACE_DELAY_SECONDS = 0.25


def _replace_with_retry(source: Path, target: Path) -> None:
    """``os.replace`` с изчакване при временно заключен файл.

    Повдига последното изключение, ако файлът остане заключен — тихо
    пропускане не е допустимо.
    """

    last: OSError | None = None
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            os.replace(source, target)
            return
        except PermissionError as exc:  # Windows: файлът е зает
            last = exc
        except OSError as exc:
            if getattr(exc, "winerror", None) not in (32, 33):
                raise
            last = exc
        time.sleep(_REPLACE_DELAY_SECONDS * (attempt + 1))
    assert last is not None
    raise PermissionError(
        f"Файлът е зает от друга програма и не може да бъде заменен: {target}\n"
        f"Затвори Excel/AutoCAD (или изчакай OneDrive да приключи) и опитай пак.\n"
        f"Първопричина: {last}"
    ) from last


def replace_export_bundle(pairs: Sequence[tuple[Path, Path]]) -> None:
    """Replace a group of final export files with rollback on any failure.

    All temporary and final files must be in the same parent directory. This
    keeps every ``os.replace`` operation on one filesystem and allows the
    previous complete bundle to be restored if one replacement fails.
    """

    normalized = [(Path(temporary), Path(final)) for temporary, final in pairs]
    if not normalized:
        raise ExportBundleError("Няма файлове за записване.")

    parents = {path.parent.resolve() for pair in normalized for path in pair}
    if len(parents) != 1:
        raise ExportBundleError(
            "Временните и крайните export файлове трябва да са в една папка."
        )
    parent = normalized[0][1].parent
    parent.mkdir(parents=True, exist_ok=True)

    finals = [final.resolve() for _, final in normalized]
    if len(finals) != len(set(finals)):
        raise ExportBundleError("Дублиран краен файл в export пакета.")
    missing = [str(path) for path, _ in normalized if not path.is_file()]
    if missing:
        raise ExportBundleError("Липсва временен export файл: " + ", ".join(missing))

    backup_dir = parent / f".lion_rebar_export_backup_{uuid.uuid4().hex}"
    backup_dir.mkdir()
    moved_old: list[tuple[Path, Path]] = []
    installed_new: list[Path] = []
    try:
        for index, (_, final) in enumerate(normalized):
            if final.exists():
                backup = backup_dir / f"{index:02d}_{final.name}"
                _replace_with_retry(final, backup)
                moved_old.append((backup, final))

        for temporary, final in normalized:
            _replace_with_retry(temporary, final)
            installed_new.append(final)
    except Exception:
        for final in reversed(installed_new):
            try:
                final.unlink(missing_ok=True)
            except OSError:
                pass
        restore_errors: list[str] = []
        for backup, final in reversed(moved_old):
            try:
                _replace_with_retry(backup, final)
            except OSError as exc:
                restore_errors.append(f"{final}: {exc}")
        if restore_errors:
            raise ExportBundleError(
                "Export записът се провали и част от предишния пакет не можа да бъде "
                "възстановена: " + "; ".join(restore_errors)
            )
        raise
    finally:
        shutil.rmtree(backup_dir, ignore_errors=True)
