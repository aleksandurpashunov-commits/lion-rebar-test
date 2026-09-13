"""Нормализация на AutoCAD текст преди семантичен анализ.

Root cause (Belgravia, 2026-08-23): 4495 текстови записа в реален DXF са
записани като ``\\U+043E\\U+0433...`` вместо кирилица. Търсенето на рамки
работи върху суровия текст, затова ``markers RAW = 0``; след декодиране
същият файл дава 14 маркера.

Модулът е **общ**, не е настроен по конкретния файл: покрива валидните
AutoCAD escape последователности, а не конкретни низове.

Обхват:
  ``\\U+XXXX``  -- Unicode code point (основният случай)
  ``\\M+NXXXX`` -- multibyte code point (стар AutoCAD)
  ``%%u %%o``   -- подчертаване/надчертаване (премахват се)
  ``%%d %%c %%p`` -- градус / диаметър / плюс-минус
  ``%%NNN``     -- символ по код
  ``\\P``       -- нов ред в MTEXT
  ``{\\f...;}`` -- MTEXT форматиращи кодове

Нормализацията НЕ променя семантиката: тя само превръща записа в
човешкия текст, който проектантът вижда в CAD.
"""
from __future__ import annotations

import re

_UNICODE_RE = re.compile(r"\\U\+([0-9A-Fa-f]{4})")
_MBYTE_RE = re.compile(r"\\M\+[0-9A-Fa-f]([0-9A-Fa-f]{4})")
_MTEXT_FORMAT_RE = re.compile(r"\\[A-Za-z][^;\\]*;")
_PCT_CODE_RE = re.compile(r"%%(\d{3})")
_WS_RE = re.compile(r"\s+")

_PCT_MAP = {
    "%%d": "°",
    "%%c": "Ø",
    "%%p": "±",
    "%%%": "%",
}


def decode_dxf_text(value: object) -> str:
    """Превръща суров DXF текст в човешкия текст.

    Идемпотентна: повторно извикване връща същия резултат.
    Никога не вдига изключение — при повреден вход връща каквото може.
    """

    text = str(value if value is not None else "")
    if not text:
        return ""
    text = text.replace("\ufeff", "")

    def _u(match: re.Match) -> str:
        try:
            return chr(int(match.group(1), 16))
        except (ValueError, OverflowError):
            return match.group(0)

    text = _UNICODE_RE.sub(_u, text)
    text = _MBYTE_RE.sub(_u, text)

    # %%NNN -> символ по код (преди буквените кодове, за да не се яде %%1)
    def _pct(match: re.Match) -> str:
        try:
            return chr(int(match.group(1)))
        except (ValueError, OverflowError):
            return match.group(0)

    text = _PCT_CODE_RE.sub(_pct, text)
    lowered_pairs = (("%%u", ""), ("%%o", ""), ("%%U", ""), ("%%O", ""))
    for token, repl in lowered_pairs:
        text = text.replace(token, repl)
    for token, repl in _PCT_MAP.items():
        text = text.replace(token, repl).replace(token.upper(), repl)

    # MTEXT: параграф и форматиращи кодове
    text = text.replace("\\P", "\n").replace("\\p", "\n")
    text = _MTEXT_FORMAT_RE.sub("", text)
    text = text.replace("\\~", " ")
    text = text.replace("{", "").replace("}", "")
    text = text.replace("\\\\", "\\")
    return text


def normalize_for_match(value: object) -> str:
    """Декодира + подготвя за търсене по ключова дума (главни, без Ё)."""

    decoded = decode_dxf_text(value)
    return _WS_RE.sub(" ", decoded.upper().replace("Ё", "Е")).strip()


def contains_encoded_text(value: object) -> bool:
    """True, ако суровият текст съдържа AutoCAD escape, нуждаещ се от decode."""

    text = str(value if value is not None else "")
    return bool(_UNICODE_RE.search(text) or _MBYTE_RE.search(text) or "%%" in text)
