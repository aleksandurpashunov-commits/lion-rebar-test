# -*- coding: utf-8 -*-
"""Петте липсващи полета в записа на решението и ВИДЪТ на решението.

Задание от 30.09.2026, § 3.2 и § 3.3. Пликът на решението
(``decision_contract``) вече носеше самоличност, стабилен ``event_id``,
``decision_revision``, ``supersedes_event_ids``, ``active`` и ``operation_kind``.
Липсваха точно пет неща, с които записът става проверим без да се чете кодът:

``change``          предишната и новата стойност, по ИМЕНУВАНИ производствени
                    полета, с кой ключ е прочетена всяка;
``actor``           кой е извършил действието — без измисляне на име;
``evidence``        какво е доказало решението, събрано на едно място;
``decision_scope``  обхватът по стълбата от ``learning/scopes.py``;
``code_version``    версията на кода, който е записал решението.

Плюс ``decision_kind`` — разграничението, поискано в § 3.2: наблюдение на
програмата / предложение / потвърдено решение / отхвърляне / поправка.

ПРАВИЛАТА НА ТОЗИ МОДУЛ

1. Нищо не се измисля. Липсващата предишна стойност се записва като липсваща,
   с причина (``NO_PRIOR_RECORDED_VALUE``), а не като равна на новата.
2. Източникът на всяка стойност се записва. ``declared_length_cm`` и
   ``production_length_cm`` НЕ са едно и също нещо: когато предишната дължина е
   прочетена от надписа, това се вижда в ``before_keys``, а не се скрива зад
   общото име.
3. Обхватът е най-тесният доказан. Едно операторско решение върху позиция е
   ``POSITION_DECISION`` и не става правило, колкото и пъти да се повтори в
   същия проект.
4. „Потвърждение" и „поправка" се различават по ДАННИТЕ: решение, което не
   мени нито едно производствено поле, е потвърждение. Операторът, приел
   предложението на програмата без промяна, не е коригирал нищо.
5. Липсата на сравнима предишна стойност НЕ е потвърждение. Без база за
   сравнение няма как да се заключи, че производствените полета не са сменени —
   това би бил извод от липсваща информация. Видът остава ``UNDETERMINED`` с
   причината. Уговорка на собственика от 01.10.2026.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from lion_rebar.config.constants import APP_VERSION
from lion_rebar.session.operator_identity import ЗАПЪЛВАЩИ, is_named_person

#: Петте вида, поискани в § 3.2.
ВИД_НАБЛЮДЕНИЕ = "AUTOMATIC_OBSERVATION"
ВИД_ПРЕДЛОЖЕНИЕ = "SUGGESTION"
ВИД_ПОТВЪРДЕНО = "CONFIRMED_DECISION"
ВИД_ОТХВЪРЛЯНЕ = "REJECTION"
ВИД_ПОПРАВКА = "CORRECTION"

#: ШЕСТИЯТ вид, добавен по уговорка на собственика от 01.10.2026:
#:
#: > Липсваща предишна стойност не означава потвърждение. Ако няма надеждна база
#: > за сравнение, системата не може да заключи, че производствените полета не
#: > са променени. Такъв случай трябва да остане изрично неустановен, вместо да
#: > бъде отчетен като доказано потвърждение.
#:
#: Той НЕ е шести смислов вид решение, а признание, че видът не е доказан.
#: Записът носи причината защо, за да се вижда какво точно липсва.
ВИД_НЕУСТАНОВЕНО = "UNDETERMINED"

ВИДОВЕ = frozenset(
    {
        ВИД_НАБЛЮДЕНИЕ,
        ВИД_ПРЕДЛОЖЕНИЕ,
        ВИД_ПОТВЪРДЕНО,
        ВИД_ОТХВЪРЛЯНЕ,
        ВИД_ПОПРАВКА,
        ВИД_НЕУСТАНОВЕНО,
    }
)

#: Събития, при които програмата КОНСТАТИРА, а операторът не избира стойност.
НАБЛЮДЕНИЯ = frozenset({"NO_VALID_CANDIDATE", "MISSING_MASTER"})

#: Събития, с които позиция СЕ МАХА от производството или се маркира проблем.
ОТХВЪРЛЯНИЯ = frozenset(
    {
        "PROBLEM_RECORDED",
        "EXCLUDED_BY_ENGINEER",
        "NON_PRODUCTION_POSITION",
        "FLAGGED",
    }
)

#: Чернова: записана е, но нищо производствено не зависи от нея.
ПРЕДЛОЖЕНИЯ = frozenset({"MANUAL_SHAPE_DRAFT"})

#: Оттегляне на предходна операция — винаги поправка.
ОТТЕГЛЯНИЯ = frozenset(
    {"GROUP_UNDONE", "SIMILAR_UNDONE", "ENGINEER_DECISION_WITHDRAWN"}
)

#: Производствените полета, по които се сравняват предишната и новата стойност.
#: Първият ключ е каноничното име; следващите са ИМЕНУВАНИТЕ синоними, които
#: различните диалози пишат. Редът е значещ — чете се първият наличен.
СРАВНЯВАНИ_ПОЛЕТА: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("count", ("count", "annotation_count", "resolved_count")),
    ("diameter_mm", ("diameter_mm", "resolved_diameter_mm")),
    (
        "production_length_cm",
        ("production_length_cm", "resolved_length_cm", "declared_length_cm"),
    ),
    (
        "segment_lengths_cm",
        (
            "segment_lengths_cm",
            "production_segment_lengths_cm",
            "canonical_segment_lengths_cm",
        ),
    ),
    ("shape_code", ("shape_code", "master_shape_code")),
    ("geometry_handles", ("geometry_handles", "candidate_handles", "handles")),
)

#: Ключове, от които се чете авторът. Нищо извън тях не става автор.
КЛЮЧОВЕ_ЗА_АВТОР = (
    "actor",
    "approved_by",
    "decided_by",
    "operator",
    "author",
    "resolved_by",
)

#: Стойности, които НЕ посочват човек. Записват се, но не се броят за автор.
#:
#: Определението живее в ``session/operator_identity.py``, за да е ЕДНО: там
#: се проверява и заявеното име, тъй че една и съща стойност не може да мине за
#: име при заявяването и за роля в записа.
#:
#: Кирилските са в списъка по МЕРЕНЕ: до 01.10.2026 единайсет места в
#: ``ui/main_window.py`` и ``ui/review_center.py`` пишеха ``getattr(self,
#: "operator_name", "") or "оператор"``, а ``operator_name`` не се задаваше
#: никъде. Тези стари записи остават с НЕИЗВЕСТЕН автор и не се приписват със
#: задна дата на никого.
БЕЗЛИЧНИ_АВТОРИ = ЗАПЪЛВАЩИ

#: Ключове с доказателства. Събират се както са — присъдата не ги преразказва.
КЛЮЧОВЕ_ЗА_ДОКАЗАТЕЛСТВО = (
    "evidence",
    "diagnostic_problem",
    "decision_suggestion",
    "review_reason_codes",
    "matcher_evidence_band",
    "matcher_disposition",
    "contextual_rule_code",
    "reason_code",
    "reason",
)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _normalized(value: Any) -> Any:
    """Стойност, годна за сравнение. Списъците се сравняват като списъци."""

    if isinstance(value, (list, tuple)):
        return [_normalized(item) for item in value]
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return round(float(value), 6)
    if value is None:
        return None
    return str(value)


def _четене(source: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """Производствените полета от един обект + с кой ключ е прочетено всяко."""

    стойности: dict[str, Any] = {}
    ключове: dict[str, str] = {}
    for каноничен, синоними in СРАВНЯВАНИ_ПОЛЕТА:
        for ключ in синоними:
            if ключ not in source:
                continue
            стойност = source.get(ключ)
            if стойност is None or (isinstance(стойност, (list, tuple)) and not стойност):
                continue
            стойности[каноничен] = _normalized(стойност)
            ключове[каноничен] = ключ
            break
    return стойности, ключове


def change_block(
    payload: Mapping[str, Any],
    *,
    superseded: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Предишната и новата стойност, по именувани производствени полета.

    Предишната стойност се търси в този ред: ``original`` от товара, после
    ``change.after`` на отмененото активно решение. Няма трети път: контекстът
    на реда (``row_context``) описва какво е ВИДЯЛА програмата, не какво е било
    РЕШЕНО, и затова не се чете като предишно решение.
    """

    преди_източник = "NO_PRIOR_RECORDED_VALUE"
    преди, преди_ключове = _четене(_mapping(payload.get("original")))
    if преди:
        преди_източник = "PAYLOAD_ORIGINAL"
    else:
        for предходно in superseded:
            предходна_промяна = _mapping(предходно.get("change"))
            наследено = _mapping(предходна_промяна.get("after"))
            if наследено:
                преди, преди_ключове = _четене(наследено)
                преди_източник = "SUPERSEDED_EVENT:%s" % str(
                    предходно.get("event_id") or ""
                )
                break

    след_източник = "NO_RECORDED_VALUE"
    for ключ, име in (
        ("resolved", "PAYLOAD_RESOLVED"),
        ("decision", "PAYLOAD_DECISION"),
    ):
        след, след_ключове = _четене(_mapping(payload.get(ключ)))
        if след:
            след_източник = име
            break
    else:
        след, след_ключове = _четене(payload)
        if след:
            след_източник = "PAYLOAD_TOP_LEVEL"

    общи = sorted(set(преди) & set(след))
    променени = sorted(име for име in общи if преди[име] != след[име])
    непроменени = sorted(име for име in общи if преди[име] == след[име])
    само_ново = sorted(set(след) - set(преди))

    # Групово решение: стойностите са по членове. Записват се поименно, защото
    # „57 потвърдени" без членовете не позволява проверка на нито един от тях
    # (§ 6.1). Нищо не се отрязва — товарът вече носи същите редове.
    членове: list[dict[str, Any]] = []
    for ред in payload.get("decisions") or []:
        if not isinstance(ред, Mapping):
            continue
        стойности, ключове = _четене(ред)
        членове.append(
            {
                "annotation_handle": str(ред.get("annotation_handle") or "") or None,
                "after": стойности or None,
                "after_keys": ключове,
                "before_source": "NO_PRIOR_RECORDED_VALUE",
            }
        )

    return {
        "members": членове,
        "member_count": len(членове),
        "before": преди or None,
        "after": след or None,
        "before_source": преди_източник,
        "after_source": след_източник,
        "before_keys": преди_ключове,
        "after_keys": след_ключове,
        "changed_fields": променени,
        "unchanged_fields": непроменени,
        "fields_without_prior_value": само_ново,
        "comparable": bool(общи),
    }


def actor_block(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Кой е извършил действието. Непосочен автор се записва като непосочен.

    Акаунтът на машината НЕ се чете: той казва на кой компютър е натиснато, а
    не кой инженер е решил. Предпочитаме видима празнота пред присвоено име —
    записът после е основание за приемане или отказ на цял чертеж.

    Името е ЗАЯВЕНО, не удостоверено: програмата не прави вход в системата и не
    проверява нищо. Затова записът носи ``claimed`` и ``authenticated`` изрично
    — за да не се чете някога като удостоверена самоличност.
    """

    източници = [payload, _mapping(payload.get("canonical_decision")), _mapping(payload.get("decision"))]
    записано: str | None = None
    източник = "UNRECORDED"
    for обект in източници:
        for ключ in КЛЮЧОВЕ_ЗА_АВТОР:
            стойност = str(обект.get(ключ) or "").strip()
            if стойност:
                записано = стойност
                източник = "PAYLOAD:%s" % ключ
                break
        if записано:
            break
    посочен = is_named_person(записано)
    return {
        "recorded_as": записано,
        "source": източник,
        "identifies_person": посочен,
        "claimed": посочен,
        "authenticated": False,
        "role": "OPERATOR" if записано else None,
        "session_id": str(payload.get("session_id") or "") or None,
    }


def evidence_block(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Относимите доказателства, събрани на едно място, без преразказ."""

    елементи: list[dict[str, Any]] = []
    for обект, произход in (
        (payload, "payload"),
        (_mapping(payload.get("canonical_decision")), "canonical_decision"),
    ):
        for ключ in КЛЮЧОВЕ_ЗА_ДОКАЗАТЕЛСТВО:
            if ключ not in обект:
                continue
            стойност = обект.get(ключ)
            if стойност is None or stringly_empty(стойност):
                continue
            елементи.append({"kind": ключ, "origin": произход, "value": стойност})
    return {
        "items": елементи,
        "count": len(елементи),
        "present": bool(елементи),
    }


def stringly_empty(value: Any) -> bool:
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict, set)):
        return not value
    return False


def scope_block(
    payload: Mapping[str, Any],
    *,
    annotation_handles: Iterable[str] = (),
) -> dict[str, Any]:
    """Обхватът на решението по стълбата от ``learning/scopes.py``.

    Решение, вързано за конкретни handle-и, е решение за ПОЗИЦИЯ. Решение без
    handle-и (например отговор за категорията на една площ) важи най-много за
    ТОЗИ проект — един независим източник, тоест ``PROJECT_CONVENTION``. По-широк
    обхват не се извлича от едно операторско действие: той иска независими
    свидетели и минава през ``session/rule_activation.py``.
    """

    from lion_rebar.learning.scopes import scope_for

    handles = [str(стойност) for стойност in annotation_handles if str(стойност or "").strip()]
    присъда = scope_for(
        independent_sources=1,
        family_verdicts=(),
        per_position=bool(handles),
    )
    присъда["positions"] = len(handles)
    присъда["learned"] = False
    присъда["verdict_note"] = (
        "едно операторско действие не е независим свидетел за правило; "
        "разширяването на обхвата минава през rule_activation"
    )
    return присъда


def decision_kind(
    event_type: str,
    *,
    change: Mapping[str, Any],
    supersedes_event_ids: Iterable[str] = (),
    payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Видът на решението — изчислен, с причината за избора.

    Явно подаден ``decision_kind`` в товара се уважава (пътят на паметта знае
    по-добре от типа на събитието, че предлага, а не решава), но се отбелязва
    като подаден, не като изчислен.
    """

    name = str(event_type or "").strip().upper()
    товар = _mapping(payload)
    подаден = str(товар.get("decision_kind") or "").strip().upper()
    if подаден in ВИДОВЕ:
        return {
            "kind": подаден,
            "source": "PAYLOAD",
            "why": "видът е подаден от извикващия път",
        }
    отменя = [str(стойност) for стойност in supersedes_event_ids if str(стойност or "")]
    if name in ОТТЕГЛЯНИЯ:
        return {
            "kind": ВИД_ПОПРАВКА,
            "source": "DERIVED",
            "why": "оттегляне на предходна операция",
        }
    if name in НАБЛЮДЕНИЯ:
        return {
            "kind": ВИД_НАБЛЮДЕНИЕ,
            "source": "DERIVED",
            "why": "програмата констатира липса; операторът не избира стойност",
        }
    if name in ПРЕДЛОЖЕНИЯ:
        return {
            "kind": ВИД_ПРЕДЛОЖЕНИЕ,
            "source": "DERIVED",
            "why": "черновата не влиза в производството без одобрение",
        }
    if name in ОТХВЪРЛЯНИЯ:
        return {
            "kind": ВИД_ОТХВЪРЛЯНЕ,
            "source": "DERIVED",
            "why": "позицията се маха от производството или носи маркиран проблем",
        }
    променени = list(change.get("changed_fields") or [])
    if променени:
        return {
            "kind": ВИД_ПОПРАВКА,
            "source": "DERIVED",
            "why": "новата стойност се различава по: %s" % ", ".join(променени),
            "changed_fields": променени,
        }
    # Редът е важен: ОТМЕНЯНЕТО само по себе си не е поправка. Повторно
    # одобрение със същите стойности отменя предходния запис, но не поправя
    # нищо — то е потвърждение.
    #
    # А липсата на сравнима предишна стойност не е НИТО поправка, нито
    # потвърждение. Без база за сравнение няма как да се заключи, че
    # производствените полета не са сменени; „потвърдено" там би било извод от
    # липсваща информация. Затова видът остава НЕУСТАНОВЕН, с причината.
    if not change.get("comparable"):
        return {
            "kind": ВИД_НЕУСТАНОВЕНО,
            "source": "DERIVED",
            "why": (
                "няма сравнима предишна стойност (%s); отменя %d предходни "
                "решения. Не е доказано нито че нещо се е сменило, нито че не се "
                "е сменило" % (str(change.get("before_source") or "?"), len(отменя))
            ),
            "changed_fields": [],
            "undetermined_because": str(change.get("before_source") or "") or None,
        }
    if отменя:
        return {
            "kind": ВИД_ПОТВЪРДЕНО,
            "source": "DERIVED",
            "why": (
                "отменя %d предходни решения със СЪЩИТЕ производствени стойности"
                % len(отменя)
            ),
            "changed_fields": [],
        }
    return {
        "kind": ВИД_ПОТВЪРДЕНО,
        "source": "DERIVED",
        "why": "нито едно производствено поле не е сменено и няма отменено решение",
        "changed_fields": [],
    }


def decision_fields(
    event_type: str,
    payload: Mapping[str, Any],
    *,
    annotation_handles: Iterable[str] = (),
    supersedes_event_ids: Iterable[str] = (),
    superseded_events: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Петте полета + видът, готови за плика на решението."""

    change = change_block(payload, superseded=superseded_events)
    return {
        "change": change,
        "actor": actor_block(payload),
        "evidence": evidence_block(payload),
        "decision_scope": scope_block(payload, annotation_handles=annotation_handles),
        "code_version": APP_VERSION,
        "decision_kind": decision_kind(
            event_type,
            change=change,
            supersedes_event_ids=supersedes_event_ids,
            payload=payload,
        ),
    }
