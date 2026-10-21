# -*- coding: utf-8 -*-
"""Кой е операторът — ЗАЯВЕНО име, не удостоверена самоличност.

Решение на собственика от 01.10.2026, § 3.3:

> При първото записване на операторско решение се въвежда име и то се запомня.
> Текущият оператор се вижда в интерфейса и може да се сменя. Всяко решение
> пази автора към момента на записа; последващата смяна не пренаписва
> историята. Старите записи „оператор" остават с неизвестен автор. Не се
> приписват със задна дата. Въведеното име е заявен автор, а не удостоверена
> самоличност.

Затова този модул НЕ прави вход в системата и не проверява нищо. Той пази едно
заявено име до останалите данни на приложението (``settings.json``) и история
на заявяванията. Полето в записа на решението носи `claimed: true` и
`authenticated: false`, за да не се чете някога като удостоверена самоличност.

ЗАЩО името е на ниво ПРИЛОЖЕНИЕ, а не на проект: операторът е същият при смяна
на проекта, а решение, записано в десет проекта в един следобед, е от същия
човек. Проектът пази КОЙ е решил всяко нещо в самото решение; настройката пази
само кой седи пред програмата сега.

ЗАЩО незаявеното име не става „оператор": дотук единайсет места записваха
буквалния низ „оператор", защото нямаше откъде да дойде име. Тези записи остават
с НЕИЗВЕСТЕН автор и не се приписват със задна дата на никого. Новите записи без
заявено име носят ``НЕЗАЯВЕН`` — различим от старите и от истинско име.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from lion_rebar.utils.process_lock import exclusive_file_lock

СХЕМА = "lion-rebar-operator-identity/v1"

#: Записва се като автор, когато операторът още не е заявил име.
НЕЗАЯВЕН = "НЕЗАЯВЕН"

#: Най-късото допустимо заявено име.
МИН_ДЪЛЖИНА = 2

#: Стойности, които НЕ посочват човек. Включва и историческото „оператор",
#: за да не може запълващият низ да бъде заявен като име.
ЗАПЪЛВАЩИ = frozenset(
    {
        "USER",
        "OPERATOR",
        "ENGINEER",
        "SYSTEM",
        "MAIN_WINDOW",
        "ОПЕРАТОР",
        "ИНЖЕНЕР",
        "ПОТРЕБИТЕЛ",
        НЕЗАЯВЕН,
    }
)


class OperatorIdentityError(ValueError):
    """Неприемливо заявено име — отказва се, не се записва."""


def is_named_person(value: Any) -> bool:
    """Посочва ли тази стойност ЧОВЕК, или е роля и запълващ низ."""

    име = str(value or "").strip()
    return bool(име) and len(име) >= МИН_ДЪЛЖИНА and име.upper() not in ЗАПЪЛВАЩИ


def normalise_name(value: Any) -> str:
    """Проверява и нормализира заявено име. Отказва запълващите низове."""

    име = " ".join(str(value or "").split())
    if not име:
        raise OperatorIdentityError("Името на оператора не може да е празно.")
    if len(име) < МИН_ДЪЛЖИНА:
        raise OperatorIdentityError(
            "Името на оператора е поне %d знака." % МИН_ДЪЛЖИНА
        )
    if име.upper() in ЗАПЪЛВАЩИ:
        raise OperatorIdentityError(
            "%s е роля, а не име. Напиши кой работи: записът на решението "
            "посочва автор, не длъжност." % име
        )
    return име


def claimed_operator(settings_file: Path | str) -> str:
    """Заявеното име или празен низ. Нечетими настройки НЕ дават име."""

    данни = _прочети(Path(settings_file))
    име = str(данни.get("operator_name") or "").strip()
    return име if is_named_person(име) else ""


def operator_history(settings_file: Path | str) -> list[dict[str, Any]]:
    """Историята на заявяванията, най-старото първо."""

    данни = _прочети(Path(settings_file))
    редове = данни.get("operator_history")
    if not isinstance(редове, list):
        return []
    return [dict(ред) for ред in редове if isinstance(ред, dict)]


def declare_operator(settings_file: Path | str, name: Any) -> dict[str, Any]:
    """Заявява текущия оператор. Връща записаното.

    Историята само се ДОПИСВА. Смяната на името не пренаписва нито едно вече
    записано решение — авторът в решението е към момента на записа.
    """

    име = normalise_name(name)
    път = Path(settings_file)
    with exclusive_file_lock(път):
        данни = _прочети(път)
        сурова = данни.get("operator_history")
        история = (
            [dict(ред) for ред in сурова if isinstance(ред, dict)]
            if isinstance(сурова, list)
            else []
        )
        предишно = str(данни.get("operator_name") or "").strip()
        история.append(
            {
                "name": име,
                "declared_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "replaced": предишно or None,
            }
        )
        данни.update(
            {
                "schema": СХЕМА,
                "operator_name": име,
                "operator_claimed": True,
                "operator_authenticated": False,
                "operator_history": история,
            }
        )
        _запиши(път, данни)
        return dict(данни)


def _прочети(път: Path) -> dict[str, Any]:
    if not път.is_file():
        return {}
    try:
        стойност = json.loads(път.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        # Нечетимите настройки НЕ дават име и НЕ се презаписват тук: записът
        # минава през ``declare_operator``, където операторът явно заявява.
        return {}
    return стойност if isinstance(стойност, dict) else {}


def _запиши(път: Path, данни: dict[str, Any]) -> None:
    from lion_rebar.utils.files import atomic_write_text

    atomic_write_text(път, json.dumps(данни, ensure_ascii=False, indent=2) + "\n")
