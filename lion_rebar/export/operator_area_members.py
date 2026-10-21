# -*- coding: utf-8 -*-
"""Всеки член на груповото решение за ПЛОЩ минава своя проверка (§ 6.1).

Задание от 30.09.2026, § 6.1, и уговорка на собственика от 01.10.2026:

> всяка позиция в групата да преминава собствена проверка, а несъвместимите
> членове да остават за преглед с конкретна причина. Поименното записване е
> необходимата основа, но само по себе си не прави груповото одобрение
> безопасно.

КАКВО БЕШЕ. Рамката с два клика е групово решение: „всичко в този правоъгълник
е на тази греда". Преценката се правеше, но се ИЗХВЪРЛЯШЕ — операторът виждаше
броя и най-много осем премествания, а записът на областта не казваше нито една
позиция. След записа не можеше да се провери НИТО ЕДИН член: правоъгълникът и
заглавието са единственото, което оставаше.

Освен това два изхода бяха МЪЛЧАЛИВИ:

* обект без точка на вмъкване — не можеше да се реши дали е вътре, и просто се
  пропускаше;
* обект в правоъгълника, който не е позиция (бележка като „стр. 59N8/s=20") —
  пропускаше се, затова броят „позиции в рамката" не се връзваше с видяното.

КАКВО Е СЕГА. Всеки обект в правоъгълника получава присъда с причина, присъдите
се показват ПРЕДИ записа и се записват С областта. Нищо не се пропуска мълчаливо.

КАКВО ТОЗИ МОДУЛ НЕ ПРАВИ. Не измисля инженерно правило за несъвместимост. Член,
когото автоматиката е вързала за ДРУГА греда, се приема — рамката бие
автоматиката по изрично решение на собственика от 2026-09-14 — но се записва
като ПРЕМЕСТЕН, с коя греда е бил и с показателите на автоматиката. Прагът,
отвъд който преместването да спира за преглед, е решение на собственика върху
измерени числа, не догадка на кода.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

#: Четирите състояния са по таблицата на собственика от 02.10.2026.
#:
#: 1. НЯМА УСТАНОВЕНА ПРИНАДЛЕЖНОСТ → предложение, показано преди записа.
ПРИЕТ = "ACCEPTED"

#: 2. ПРИНАДЛЕЖНОСТТА КЪМ ДРУГА ГРЕДА Е САМО АВТОМАТИЧНО ПРЕДПОЛОЖЕНИЕ →
#:    видимо предупреждение и потвърждение от оператора. Рамката печели, но
#:    едва след като операторът е видял какво точно се мести и откъде.
ПРЕМЕСТЕН = "ACCEPTED_MOVED_FROM_OTHER"

#: 3. ПРИНАДЛЕЖНОСТТА КЪМ ДРУГА ГРЕДА Е ВЕЧЕ ПОТВЪРДЕНА ОТ ОПЕРАТОР → БЕЗ
#:    мълчаливо преместване. Позицията НЕ става член на рамката и остава за
#:    изрично решение.
#:
#:    Собственик, 02.10.2026: „Самото очертаване на рамка не е достатъчно
#:    основание да се отмени предишно потвърдено решение."
ПОТВЪРДЕН_ДРУГАДЕ = "CONFIRMED_ELSEWHERE"

#: 4. НЕУСТАНОВЕНА ИЛИ ПРОТИВОРЕЧИВА ПРИНАДЛЕЖНОСТ → остава за преглед с
#:    конкретна причина.
БЕЗ_ТОЧКА = "UNDECIDABLE_NO_ANCHOR"

#: Обект в правоъгълника, който не е позиция (бележка, заглавие, свободен текст).
НЕ_Е_ПОЗИЦИЯ = "NOT_A_POSITION"

#: Присъди, при които членът влиза в групата.
ПРИЕТИ = frozenset({ПРИЕТ, ПРЕМЕСТЕН})

#: Присъди, които искат операторски поглед, преди рамката да се запише.
ЗА_ПРЕГЛЕД = frozenset({БЕЗ_ТОЧКА, ПОТВЪРДЕН_ДРУГАДЕ})


@dataclass(frozen=True)
class ЧленНаОбласт:
    """Една присъда за един обект спрямо една начертана рамка."""

    handle: str
    text: str
    verdict: str
    reason: str
    x: float | None = None
    y: float | None = None
    from_title: str | None = None
    from_handle: str | None = None
    score: float | None = None
    margin: float | None = None

    def to_dict(self) -> dict[str, Any]:
        редът = {
            "handle": self.handle,
            "text": self.text,
            "verdict": self.verdict,
            "reason": self.reason,
        }
        if self.x is not None and self.y is not None:
            редът["x"] = round(float(self.x), 3)
            редът["y"] = round(float(self.y), 3)
        if self.from_title:
            редът["from_title"] = self.from_title
            редът["from_handle"] = self.from_handle
            редът["score"] = self.score
            редът["margin"] = self.margin
        return редът


def ordered_for_review(members: Iterable[ЧленНаОбласт]) -> list[ЧленНаОбласт]:
    """Преместванията, подредени за преглед. БЕЗ праг.

    Собственик, 02.10.2026: „Не бих задавал праг по score/margin, преди да
    знаем как се изчисляват и дали разграничават правилните от грешните
    премествания върху проверени случаи. Число без такава проверка създава
    привидна сигурност. Дотогава тези показатели могат да подреждат случаите
    за преглед."

    Затова показателите само ПОДРЕЖДАТ: пръв е случаят, в който автоматиката е
    била най-уверена, защото точно там рамката противоречи на най-силния
    автоматичен отговор. Нищо не се спира заради число.
    """

    преместени = [член for член in members if член.verdict == ПРЕМЕСТЕН]
    return sorted(
        преместени,
        key=lambda член: (
            -(член.margin if член.margin is not None else -1.0),
            -(член.score if член.score is not None else -1.0),
            член.handle,
        ),
    )


def summarise(members: Iterable[ЧленНаОбласт]) -> dict[str, Any]:
    """Броевете по присъда — за съобщението и за записа."""

    редове = list(members)
    по_присъда: dict[str, int] = {}
    for член in редове:
        по_присъда[член.verdict] = по_присъда.get(член.verdict, 0) + 1
    return {
        "total": len(редове),
        "accepted": sum(1 for член in редове if член.verdict in ПРИЕТИ),
        "moved": по_присъда.get(ПРЕМЕСТЕН, 0),
        "confirmed_elsewhere": по_присъда.get(ПОТВЪРДЕН_ДРУГАДЕ, 0),
        "for_review": sum(1 for член in редове if член.verdict in ЗА_ПРЕГЛЕД),
        "by_verdict": по_присъда,
    }


def area_member_verdicts(
    *,
    entities: Sequence[Any],
    policy: Any,
    box: Sequence[float],
    bent_around: Sequence[Sequence[float]],
    kind: str,
    title_handle: str,
    confirmed_beams: Mapping[str, str] | None = None,
) -> list[ЧленНаОбласт]:
    """Присъда за всеки обект спрямо рамката. Нищо не се пропуска мълчаливо.

    Връща САМО обекти, които рамката засяга: вътре в правоъгълника след
    огъването, плюс онези, за които не може да се реши дали са вътре, защото
    нямат точка на вмъкване.

    ``confirmed_beams`` са ПОТВЪРДЕНИТЕ ОТ ОПЕРАТОР принадлежности: надпис →
    handle на гредата (``export/beam_decisions.load_beam_decisions``). Позиция
    с потвърдена принадлежност към ДРУГА греда не става член на рамката —
    очертаването на правоъгълник не е основание да се отмени предишно
    потвърдено решение.
    """

    from lion_rebar.dxf.batch_geometry_report import _annotation_class
    from lion_rebar.dxf.recognition_policy import RecognitionPolicy
    from lion_rebar.dxf.text_parser import parse_rebar_text
    from lion_rebar.export.excel_export import (
        _assign_beam_label,
        _detect_beam_labels,
        _entity_insert,
    )
    from lion_rebar.export.frame_detection import (
        assign_point_to_frame,
        detect_drawing_frames,
    )
    from lion_rebar.export.operator_areas import KIND_BEAM, region_contains

    решена = (
        policy
        if isinstance(policy, RecognitionPolicy)
        else RecognitionPolicy.from_settings(policy)
    )
    позволени = {"BLOCK_ATTR_TEXT"}
    if решена.include_text_mtext_annotations:
        позволени.update({"TEXT", "MTEXT"})

    записи = list(entities)
    рамки = detect_drawing_frames(записи)
    заглавия = _detect_beam_labels(записи, рамки)

    правоъгълник = tuple(float(стойност) for стойност in box)
    огънато = tuple(tuple(float(с) for с in друго) for друго in bent_around)

    членове: list[ЧленНаОбласт] = []
    for запис in записи:
        if str(запис.entity_type or "").upper() not in позволени:
            continue
        текст = " ".join(str(запис.text or "").split())
        точка = _entity_insert(запис)
        позиция = _annotation_class(parse_rebar_text(запис.text or "")) == "ACTIONABLE"
        if точка is None:
            # Без точка не може да се реши дали е в рамката. Това е отказ с
            # причина, а не мълчаливо пропускане: ако такъв обект е позиция,
            # операторът трябва да го види.
            if позиция:
                членове.append(
                    ЧленНаОбласт(
                        handle=str(запис.handle or ""),
                        text=текст,
                        verdict=БЕЗ_ТОЧКА,
                        reason=(
                            "позицията няма точка на вмъкване — не може да се "
                            "реши дали е в рамката"
                        ),
                    )
                )
            continue
        if not region_contains(правоъгълник, огънато, точка[0], точка[1]):
            continue
        if not позиция:
            # ПОЗИЦИЯ значи същото, което значи и при анализа: надпис с брой,
            # диаметър и дължина. „стр. 59N8/s=20" минава за армировъчен текст,
            # но няма дължина и не е позиция. Записва се, за да се връзва броят.
            членове.append(
                ЧленНаОбласт(
                    handle=str(запис.handle or ""),
                    text=текст,
                    verdict=НЕ_Е_ПОЗИЦИЯ,
                    reason="в рамката, но не е позиция (няма брой, диаметър и дължина)",
                    x=точка[0],
                    y=точка[1],
                )
            )
            continue
        if str(kind) != KIND_BEAM:
            членове.append(
                ЧленНаОбласт(
                    handle=str(запис.handle or ""),
                    text=текст,
                    verdict=ПРИЕТ,
                    reason="позиция в рамката",
                    x=точка[0],
                    y=точка[1],
                )
            )
            continue
        # Състояние 3 от таблицата: потвърдена от оператор принадлежност към
        # ДРУГА греда. Проверява се ПРЕДИ автоматиката, защото потвърденото
        # решение е по-силно от предположението — и от правоъгълника.
        потвърдена = str((confirmed_beams or {}).get(str(запис.handle or "")) or "")
        if потвърдена and потвърдена != str(title_handle):
            по_handle = {str(з.handle): з for з in заглавия}
            чуждо = по_handle.get(потвърдена)
            членове.append(
                ЧленНаОбласт(
                    handle=str(запис.handle or ""),
                    text=текст,
                    verdict=ПОТВЪРДЕН_ДРУГАДЕ,
                    reason=(
                        "операторът вече е потвърдил принадлежност към %s; "
                        "рамката НЕ я мести — иска се изрично решение"
                        % (чуждо.title if чуждо is not None else потвърдена)
                    ),
                    x=точка[0],
                    y=точка[1],
                    from_title=(чуждо.title if чуждо is not None else None),
                    from_handle=потвърдена,
                )
            )
            continue
        индекс = assign_point_to_frame(точка[0], точка[1], list(рамки))
        свързване = (
            _assign_beam_label(точка[0], точка[1], int(индекс), заглавия)
            if индекс is not None
            else None
        )
        ако_друга = (
            свързване is not None
            and str(свързване.label.handle) != str(title_handle)
        )
        if ако_друга:
            членове.append(
                ЧленНаОбласт(
                    handle=str(запис.handle or ""),
                    text=текст,
                    verdict=ПРЕМЕСТЕН,
                    reason=(
                        "автоматиката я е свързала с %s; рамката печели по "
                        "изрично решение, затова преместването е видимо"
                        % свързване.label.title
                    ),
                    x=точка[0],
                    y=точка[1],
                    from_title=свързване.label.title,
                    from_handle=str(свързване.label.handle),
                    score=_число(getattr(свързване, "score", None)),
                    margin=_число(getattr(свързване, "margin", None)),
                )
            )
            continue
        членове.append(
            ЧленНаОбласт(
                handle=str(запис.handle or ""),
                text=текст,
                verdict=ПРИЕТ,
                reason="позиция в рамката, без друго свързване",
                x=точка[0],
                y=точка[1],
            )
        )
    return членове


def _число(стойност: Any) -> float | None:
    try:
        return round(float(стойност), 4)
    except (TypeError, ValueError):
        return None


def members_from_dicts(редове: Iterable[Mapping[str, Any]]) -> list[ЧленНаОбласт]:
    """Обратно от записаното — за проверка на вече записана област."""

    резултат: list[ЧленНаОбласт] = []
    for ред in редове:
        if not isinstance(ред, Mapping):
            continue
        резултат.append(
            ЧленНаОбласт(
                handle=str(ред.get("handle") or ""),
                text=str(ред.get("text") or ""),
                verdict=str(ред.get("verdict") or ""),
                reason=str(ред.get("reason") or ""),
                x=_число(ред.get("x")),
                y=_число(ред.get("y")),
                from_title=str(ред.get("from_title") or "") or None,
                from_handle=str(ред.get("from_handle") or "") or None,
                score=_число(ред.get("score")),
                margin=_число(ред.get("margin")),
            )
        )
    return резултат
