"""Кога нов engine е САМО добавка спрямо стария.

ЗАЩО. Собственик, 22.09.2026: „искам така да стане, че след въвеждане на форма,
като дам пакетен анализ, след това директно да си я разпознава“.

Проектът нарочно е закотвен за своя engine snapshot — така стар резултат се
възпроизвежда и промяна в СМИСЪЛА на форма не мени мълчаливо минали одобрения
(``docs/ENGINE_UPGRADE_AND_APPROVAL_BG.md``). Затова днес новата форма стига до
проекта само през нов проект или през изрично вписана поправка.

Но има случай, в който закотвянето не пази нищо: когато новият engine **не
променя нищо**, а само ДОБАВЯ. Тогава нито едно старо одобрение не може да
смени смисъла си, защото формата, върху която е взето, е буква по буква
същата.

Това не е преценка, а ПРОВЕРКА. Тук се сравнява всичко, което влияе на
разпознаването и на производствените размери:

* всяка форма от стария engine съществува в новия и е ИДЕНТИЧНА;
* правилата, категориите, статусите, настройките и тестовете на engine-а са
  идентични;
* ``engine_id`` съвпада.

Различие в който и да е от тях -> НЕ е добавка и проектът си остава на своя
snapshot. Разликите във версия, подпис, история и време на публикуване се
пренебрегват: те се менят при всяко публикуване.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from lion_rebar.engine.models import EnginePackage

#: Полета на пакета, които се менят при всяко публикуване и нямат отношение
#: към разпознаването.
_VOLATILE = frozenset({
    "version", "state", "created_at", "updated_at", "published_at",
    "approved_by", "approval_note", "parent_version", "content_sha256",
    "production_ready", "change_log", "shapes", "source_assets",
})


@dataclass(frozen=True)
class AdditiveUpgrade:
    """Резултатът от сравнението на два engine пакета."""

    added_shape_codes: tuple[str, ...]
    blocked_reason: str = ""

    def __bool__(self) -> bool:
        return not self.blocked_reason

    @property
    def is_additive(self) -> bool:
        return not self.blocked_reason and bool(self.added_shape_codes)


def _shape_fingerprint(shape) -> dict[str, Any]:
    """Формата така, както влияе на разпознаването и на размерите.

    Нарочно НЕ включва ``notes``: одобрението дописва там текста на оператора
    и това не мени нито една стойност.
    """

    data = dict(shape.to_dict())
    data.pop("notes", None)
    return data


def compare_for_additive_upgrade(
    current: EnginePackage,
    candidate: EnginePackage,
) -> AdditiveUpgrade:
    """Само добавка ли е ``candidate`` спрямо ``current``.

    Връща добавените кодове, или причината, поради която не е добавка.
    """

    if str(current.engine_id) != str(candidate.engine_id):
        return AdditiveUpgrade((), "engine_id не съвпада")

    старите = {str(s.code).upper(): _shape_fingerprint(s) for s in current.shapes}
    новите = {str(s.code).upper(): _shape_fingerprint(s) for s in candidate.shapes}

    изчезнали = sorted(set(старите) - set(новите))
    if изчезнали:
        return AdditiveUpgrade((), "липсват форми: " + ", ".join(изчезнали))

    променени = sorted(код for код, данни in старите.items() if новите[код] != данни)
    if променени:
        return AdditiveUpgrade((), "променени форми: " + ", ".join(променени))

    стар = current.to_dict()
    нов = candidate.to_dict()
    for ключ in sorted(set(стар) | set(нов)):
        if ключ in _VOLATILE:
            continue
        if стар.get(ключ) != нов.get(ключ):
            return AdditiveUpgrade((), f"променено поле на engine-а: {ключ}")

    добавени = tuple(sorted(set(новите) - set(старите)))
    if not добавени:
        return AdditiveUpgrade((), "няма добавени форми")
    return AdditiveUpgrade(добавени)
