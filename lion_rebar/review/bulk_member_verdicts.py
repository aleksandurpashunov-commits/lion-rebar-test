# -*- coding: utf-8 -*-
"""Присъда за ВСЕКИ член на масовото приемане — една и съща преди и при записа.

Задание от 30.09.2026, § 6.1 и § 6.3, и уговорка на собственика от 01.10.2026:
„всяка позиция в групата да преминава собствена проверка, а несъвместимите
членове да остават за преглед с конкретна причина."

КАКВО БЕШЕ. Проверката по редове СЪЩЕСТВУВАШЕ и беше права: проблемната позиция
се пропуска с причина, а групата продължава. Но тя живееше вътре в писача, тоест
се случваше СЛЕД като операторът вече е натиснал „Да". Диалогът обещаваше „Ще
бъдат записани 460 връзки", а след записа излизаше предупреждение „Одобрени 439,
пропуснати 21". Операторът научаваше какво точно се е променило едва след като
се е променило — точно обратното на § 6.3.

КАКВО Е СЕГА. Присъдите се смятат ТУК, с чиста функция без интерфейс, и се
ползват на двете места: в потвърждението (преди) и в писача (при записа). Едно
изчисление, затова двете числа не могат да се разминат.

ЗАЩО НЕ ДВЕ РЕАЛИЗАЦИИ. Отделна „бърза преценка" за диалога и отделна истинска
проверка в писача се разминават при първата промяна в едната. Същият урок вече е
платен другаде в този проект: сборът не е доказателство за участъците.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

ПРИЕТ = "ACCEPTED"
ОТКАЗ = "REFUSED"

#: Причините, дума по дума. Не се преформулират мимоходом: операторът ги чете,
#: а тестовете ги пазят.
БЕЗ_КАНДИДАТ = "липсва кандидат"
НЕДОПУСТИМ = "недопустим ред за масово приемане"


@dataclass
class БулковЧлен:
    """Присъдата за един ред от масовото приемане."""

    annotation_handle: str
    label: str
    verdict: str
    reason: str
    geometry_key: tuple[str, ...] = ()
    shared_legend_reference: bool = False
    #: Непрозрачни за записа: товарът, анализът и избраният кандидат.
    row: Mapping[str, Any] | None = field(default=None, repr=False)
    analysis: Any = field(default=None, repr=False)
    candidate: Any = field(default=None, repr=False)

    @property
    def accepted(self) -> bool:
        return self.verdict == ПРИЕТ

    def to_dict(self) -> dict[str, Any]:
        return {
            "annotation_handle": self.annotation_handle,
            "label": self.label,
            "verdict": self.verdict,
            "reason": self.reason,
            "geometry_handles": list(self.geometry_key),
        }


def geometry_owners(links: Iterable[Mapping[str, Any]]) -> dict[tuple[str, ...], str]:
    """Коя геометрия на кой надпис вече е одобрена."""

    from lion_rebar.review_center_model import is_position_key_legend_reference

    собственици: dict[tuple[str, ...], str] = {}
    for link in links:
        if not isinstance(link, Mapping):
            continue
        if is_position_key_legend_reference(link):
            continue
        ключ = tuple(
            sorted(
                str(стойност)
                for стойност in link.get("geometry_handles", [])
                if str(стойност)
            )
        )
        надпис = str(link.get("annotation_handle") or "")
        if ключ and надпис:
            собственици[ключ] = надпис
    return собственици


def _валидатор(approval_mode: str):
    from lion_rebar.review_center_model import (
        bulk_eligible,
        f006_bulk_eligible,
        final_confirmation_eligible,
        geometry_bulk_eligible,
    )

    return {
        "GEOMETRY_STRONG": geometry_bulk_eligible,
        "F006_STRONG": f006_bulk_eligible,
        "FINAL_USER_CONFIRMATION": final_confirmation_eligible,
    }.get(str(approval_mode), bulk_eligible)


def bulk_member_verdicts(
    rows: Sequence[Mapping[str, Any]],
    *,
    approval_mode: str = "STRONG",
    existing_links: Iterable[Mapping[str, Any]] = (),
) -> list[БулковЧлен]:
    """Присъда за всеки подаден ред, в реда на подаването.

    Редът е значещ: две позиции, които искат ЕДНА и съща геометрия, не могат и
    двете да я получат. Втората получава отказ с името на първата — така, както
    беше и дотук.
    """

    from lion_rebar.review_center_model import (
        geometry_analysis_from_report_row,
        is_position_key_legend_reference,
    )

    собственици = geometry_owners(existing_links)
    в_пакета: dict[tuple[str, ...], str] = {}
    валидатор = _валидатор(approval_mode)

    присъди: list[БулковЧлен] = []
    for row in rows:
        анализ = geometry_analysis_from_report_row(row)
        надпис = str(анализ.annotation_handle)
        име = анализ.label or анализ.annotation_handle
        if not анализ.candidates:
            присъди.append(
                БулковЧлен(
                    annotation_handle=надпис,
                    label=име,
                    verdict=ОТКАЗ,
                    reason=БЕЗ_КАНДИДАТ,
                    row=row,
                    analysis=анализ,
                )
            )
            continue
        кандидат = анализ.candidates[0]
        ключ = tuple(sorted(str(стойност) for стойност in кандидат.handles))
        if not валидатор(row):
            присъди.append(
                БулковЧлен(
                    annotation_handle=надпис,
                    label=анализ.label,
                    verdict=ОТКАЗ,
                    reason=НЕДОПУСТИМ,
                    geometry_key=ключ,
                    row=row,
                    analysis=анализ,
                    candidate=кандидат,
                )
            )
            continue
        легенда = is_position_key_legend_reference(row)
        собственик = собственици.get(ключ)
        if собственик and собственик != надпис and not легенда:
            присъди.append(
                БулковЧлен(
                    annotation_handle=надпис,
                    label=анализ.label,
                    verdict=ОТКАЗ,
                    reason="геометрията вече е одобрена за друг надпис (%s)" % собственик,
                    geometry_key=ключ,
                    row=row,
                    analysis=анализ,
                    candidate=кандидат,
                )
            )
            continue
        от_пакета = в_пакета.get(ключ)
        if от_пакета and от_пакета != надпис and not легенда:
            присъди.append(
                БулковЧлен(
                    annotation_handle=надпис,
                    label=анализ.label,
                    verdict=ОТКАЗ,
                    reason="същата геометрия е подадена и от %s" % от_пакета,
                    geometry_key=ключ,
                    row=row,
                    analysis=анализ,
                    candidate=кандидат,
                )
            )
            continue
        if not легенда:
            # САМО в пакета. Вече одобреното в ledger-а и подаденото в този
            # пакет са различни положения и носят различни причини: „вече е
            # одобрена за друг надпис" сочи минало решение, а „същата геометрия
            # е подадена и от" сочи спор вътре в самото групово действие.
            в_пакета[ключ] = надпис
        присъди.append(
            БулковЧлен(
                annotation_handle=надпис,
                label=анализ.label,
                verdict=ПРИЕТ,
                reason="минава проверката за масово приемане",
                geometry_key=ключ,
                shared_legend_reference=легенда,
                row=row,
                analysis=анализ,
                candidate=кандидат,
            )
        )
    return присъди


def summarise(members: Iterable[БулковЧлен]) -> dict[str, Any]:
    """Броевете и причините за отказ — за потвърждението и за записа."""

    редове = list(members)
    по_причина: dict[str, int] = {}
    for член in редове:
        if член.accepted:
            continue
        по_причина[член.reason] = по_причина.get(член.reason, 0) + 1
    приети = sum(1 for член in редове if член.accepted)
    return {
        "total": len(редове),
        "accepted": приети,
        "refused": len(редове) - приети,
        "by_reason": по_причина,
    }


def refusal_lines(members: Iterable[БулковЧлен], *, limit: int = 10) -> list[str]:
    """Редовете за съобщението: поименно, с причина, без отрязване наужким."""

    откази = [член for член in members if not член.accepted]
    редове = [
        "  • %s: %s" % (член.label, член.reason) for член in откази[:limit]
    ]
    if len(откази) > limit:
        редове.append("  • ... и още %d" % (len(откази) - limit))
    return редове
