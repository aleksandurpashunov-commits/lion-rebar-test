# -*- coding: utf-8 -*-
"""Изключително заключване между ПРОЦЕСИ за четене-промяна-запис.

ЗАЩО. ``files.atomic_write_text`` гарантира, че файлът никога не е наполовина
записан: ``mkstemp`` + ``fsync`` + ``os.replace``. Това НЕ решава загубената
актуализация. Двата процеса правят:

    A чете (N реда) → B чете (N реда) → A записва (N+1) → B записва (N+1)

и решението на A изчезва, при напълно атомарни записи. Атомарността пази
файла; заключването пази решението. Задание от 30.09.2026, § 7.4: иска се
транзакция или заключване И доказателство, че при два процеса няма загубено
решение.

Заключването е върху ОТДЕЛЕН файл ``<име>.lock`` до пазения файл, не върху
самия него: ``os.replace`` в Windows не може да презапише отворен файл, тоест
заключване върху целта би счупило точно атомарния запис, който пазим.

Повторно влизане в СЪЩИЯ процес е разрешено и се брои (``_ДЪЛБОЧИНА``), защото
един метод, който държи заключването, може да извика друг върху същия файл.
Между процесите заключването остава изключително.

Таймаут: по подразбиране 30 секунди, после ИЗКЛЮЧЕНИЕ. Безкрайното чакане
заключва интерфейса, а мълчаливото продължаване без заключване е точно
загубената актуализация, която гоним. Отказът е по-добър от догадката.
"""
from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

#: Разширението на файла-ключалка. ``data_identity.archive_derived_working_state``
#: го пропуска при архивиране, както пропуска и ``.tmp``.
LOCK_SUFFIX = ".lock"

ТАЙМАУТ_СЕК = 30.0
ИНТЕРВАЛ_СЕК = 0.02

#: Брояч на повторните влизания, по канонизиран път — ЗА ВСЯКА НИШКА ОТДЕЛНО.
#:
#: ЗАЩО по нишка. Повторното влизане е разрешено, защото метод, който държи
#: ключалката, може да извика друг върху същия файл. Ако броячът беше общ за
#: процеса, втора НИШКА щеше да види „вече заключено" и да влезе в критичната
#: секция БЕЗ да заключи — тоест заключването щеше да пази от другия процес, но
#: не от собствения ни работник. Байтовият диапазон се заключва за всеки
#: descriptor поотделно и в Windows, затова другата нишка наистина изчаква.
_НИШКОВО = threading.local()


def _дълбочини() -> dict[str, int]:
    стойност = getattr(_НИШКОВО, "дълбочина", None)
    if стойност is None:
        стойност = {}
        _НИШКОВО.дълбочина = стойност
    return стойност


class FileLockTimeout(TimeoutError):
    """Друг процес държи ключалката по-дълго от позволеното."""


def lock_path_for(path: Path | str) -> Path:
    """Пътят на файла-ключалка за пазен файл."""

    target = Path(path)
    return target.parent / (target.name + LOCK_SUFFIX)


def _key(path: Path) -> str:
    """Канонизиран ключ за брояча. Без допир до диска — файлът може да липсва."""

    return os.path.normcase(os.path.abspath(str(lock_path_for(path))))


def _acquire_os_lock(descriptor: int) -> bool:
    """Един опит за изключително заключване. ``False`` значи „зает сега"."""

    try:
        import fcntl
    except ImportError:
        import msvcrt

        try:
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _release_os_lock(descriptor: int) -> None:
    try:
        import fcntl
    except ImportError:
        import msvcrt

        try:
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        return
    try:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    except OSError:
        pass


@contextmanager
def exclusive_file_lock(
    path: Path | str,
    *,
    timeout: float = ТАЙМАУТ_СЕК,
) -> Iterator[None]:
    """Държи изключително заключване върху ``path`` докато блокът работи.

    Пазеният файл може и да не съществува — заключваме бъдещото му съдържание.
    """

    target = Path(path)
    ключ = _key(target)
    дълбочини = _дълбочини()
    дълбочина = дълбочини.get(ключ, 0)
    if дълбочина:
        дълбочини[ключ] = дълбочина + 1
        try:
            yield
        finally:
            дълбочини[ключ] -= 1
            if дълбочини[ключ] <= 0:
                дълбочини.pop(ключ, None)
        return

    lock_file = lock_path_for(target)
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(str(lock_file), os.O_RDWR | os.O_CREAT, 0o644)
    краен_срок = time.monotonic() + max(float(timeout), 0.0)
    try:
        while True:
            if _acquire_os_lock(descriptor):
                break
            if time.monotonic() >= краен_срок:
                raise FileLockTimeout(
                    "Друг процес държи %s повече от %.0f s. Решението НЕ е записано."
                    % (lock_file.name, timeout)
                )
            time.sleep(ИНТЕРВАЛ_СЕК)
        дълбочини[ключ] = 1
        try:
            yield
        finally:
            дълбочини.pop(ключ, None)
            _release_os_lock(descriptor)
    finally:
        os.close(descriptor)


def update_json_under_lock(
    path: Path | str,
    mutate: Callable[[Any], Any],
    *,
    default: Any = None,
    timeout: float = ТАЙМАУТ_СЕК,
) -> Any:
    """Четене-промяна-запис на JSON под заключване; връща записаното.

    ``mutate`` получава прочетеното (или ``default``, когато файлът липсва или
    е нечетим) и връща новото съдържание. Четенето и записът са в ЕДНА
    критична секция, затова едновременно изпълнение от два процеса не губи
    промяна.
    """

    import json

    from lion_rebar.utils.files import atomic_write_text

    target = Path(path)
    with exclusive_file_lock(target, timeout=timeout):
        current = default
        if target.is_file():
            try:
                current = json.loads(target.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                current = default
        updated = mutate(current)
        atomic_write_text(
            target,
            json.dumps(updated, ensure_ascii=False, indent=2) + "\n",
        )
        return updated
