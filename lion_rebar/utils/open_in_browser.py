"""Отваряне на локален HTML файл В БРАУЗЪР, а не в каквото е асоциирано.

ЗАЩО. Собственик, 25.09.2026: ``MASTER → Редактор на форми…`` му отвори
редактора в Notepad — 207 571 знака суров HTML вместо страницата.

Причината е в Windows, не в редактора. Асоциацията за ``.html`` на тази машина
сочи Notepad::

    AssocQueryString(".html", "open")
    -> C:\\Program Files\\WindowsApps\\Microsoft.WindowsNotepad_...\\Notepad.exe

а изборът на потребителя за ПРОТОКОЛА ``http`` сочи браузъра::

    HKCU\\...\\UrlAssociations\\http\\UserChoice -> MSEdgeHTM
    HKCR\\MSEdgeHTM\\shell\\open\\command
    -> "...\\msedge.exe" --single-argument %1

``QDesktopServices.openUrl`` минава по асоциацията на ФАЙЛА и затова попада на
Notepad. Затова тук се пита асоциацията на ПРОТОКОЛА: отваряме с програмата,
която операторът вече е избрал за уеб страници.

ОТКАЗЪТ Е СЪОБЩЕНИЕ, НЕ АВАРИЯ. Когато нито един път не сработи, връща се
текст, който извикващият показва заедно с пътя — операторът отваря файла сам.
"""
from __future__ import annotations

import shlex
import subprocess
import sys
import webbrowser
from pathlib import Path

#: Къде Windows пази избора на потребителя за протокола ``http``.
_HTTP_USER_CHOICE = (
    r"Software\Microsoft\Windows\Shell\Associations"
    r"\UrlAssociations\http\UserChoice"
)


def default_browser_command() -> str | None:
    """Командният ред на браузъра по подразбиране, или ``None``."""

    if sys.platform != "win32":
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _HTTP_USER_CHOICE) as key:
            prog_id = str(winreg.QueryValueEx(key, "ProgId")[0] or "").strip()
    except OSError:
        return None
    if not prog_id:
        return None
    try:
        with winreg.OpenKey(
            winreg.HKEY_CLASSES_ROOT, rf"{prog_id}\shell\open\command"
        ) as key:
            command = str(winreg.QueryValueEx(key, "")[0] or "").strip()
    except OSError:
        return None
    return command or None


def browser_argv(command: str, target: str) -> list[str]:
    """Командата на браузъра с подменен ``%1``.

    Windows пише командата с кавички около пътя и с ``%1`` на мястото на
    адреса. Липсва ли ``%1``, адресът се долепя накрая.
    """

    parts = [item.strip('"') for item in shlex.split(command, posix=False)]
    parts = [item for item in parts if item]
    if not parts:
        return []
    argv = [target if item == "%1" else item for item in parts]
    if not any(item == "%1" for item in parts):
        argv.append(target)
    return argv


def open_html_in_browser(path: Path) -> str | None:
    """Отваря ``path`` в браузъра. Връща съобщение при неуспех, иначе ``None``.

    Пътят се подава като ``file:///`` адрес: браузърът го очаква така, а и
    кирилицата в пътя минава кодирана.
    """

    path = Path(path)
    if not path.is_file():
        return f"Липсва файлът: {path}"
    uri = path.resolve().as_uri()

    command = default_browser_command()
    if command:
        argv = browser_argv(command, uri)
        if argv:
            try:
                subprocess.Popen(argv, close_fds=True)
            except OSError:
                pass
            else:
                return None

    try:
        if webbrowser.open(uri):
            return None
    except Exception:  # noqa: BLE001 — резервният път не бива да вдига
        pass
    return "Браузърът не можа да бъде стартиран."
