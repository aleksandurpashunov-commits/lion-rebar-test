"""Извлича правила за прочит от това, което СЪДЪРЖАНИЕТО вече доказва.

Източникът на истина е ``dxf/convention_profile.propose_convention_profile``:
когато то дава ПРЕДЛОЖЕНИЕ, съдържанието на чертежа само е доказало ролята над
прага (семантичен образец в текста; за геометрия — съвпадение по дължина в
радиуса на политиката, с контрола ``length + GEOMETRY_CONTROL_SHIFT_CM``).
Когато дава ОТКАЗ, доказателство има, но примерите в този чертеж са под прага.

Точно тази разлика е материалът: правило, доказано в две различни семейства,
може да реши отказ в трето — стига целевият чертеж сам да го подкрепи.

Критериите за приемане са записани ПРЕДИ обучението в
``C:\\lr_exp\\rezultat\\etap_2\\KRITERII.json`` и са пренесени тук като
константи, за да са проверими от кода.

НЕ се учат: инженерни решения, форми, участъци. Ролята на слоя е правило за
прочит — не одобрява позиция и не пипа размери, бройки и одобрения.
"""
from __future__ import annotations

import hashlib
import os
import re
from collections import defaultdict
from datetime import datetime
from typing import Any, Iterable, Mapping, Sequence

from lion_rebar.learning.knowledge_store import (
    ACCEPTED,
    CANDIDATE,
    REJECTED,
    KnowledgeStore,
    LearnedRule,
)

LEARNER_VERSION = "1.1"

#: Колко РАЗЛИЧНИ НЕЗАВИСИМИ ИЗТОЧНИКА трябва да доказват правилото, за да се
#: приеме. Числото не е променяно — променено е КАКВО се брои.
#:
#: До 30.09.2026 се броеха стойностите на ``family``, тоест ИМЕНА: ``d-1`` и
#: ``s-10`` са един чертеж с различни sha и различни имена и даваха две
#: „семейства", при което се приемаха 6 правила (измерено, ``PREDI.json``).
#: Сега се броят групите на независимост, изчислени по СЪДЪРЖАНИЕ в
#: ``auto_cycle.assign_independence_groups``.
MIN_INDEPENDENT_TO_ACCEPT = 2

#: Старото име. Пази се, за да не се къса външен код, но значението вече е
#: „независими източника", не „семейства".
MIN_FAMILIES_TO_ACCEPT = MIN_INDEPENDENT_TO_ACCEPT

#: Правило БЕЗ признак на име („само по съдържание") се приема по-трудно:
#: измерено на 27.09.2026, всичките 7 приети правила на етап 2 зависеха от името,
#: тоест преименуван слой със същото съдържание не получаваше нищо. Свалянето на
#: тази зависимост е полезно, но носи риск, затова прагът е по-висок И се иска
#: доказателство при РАЗЛИЧНИ имена — иначе правилото е същото, само маскирано.
MIN_INDEPENDENT_CONTENT_ONLY = 3
MIN_FAMILIES_CONTENT_ONLY = MIN_INDEPENDENT_CONTENT_ONLY
MIN_DISTINCT_NAME_TOKENS_CONTENT_ONLY = 2

MATCH_NAME_TOKEN = "NAME_TOKEN"
MATCH_CONTENT_ONLY = "CONTENT_ONLY"

#: Роли, за които режимът „само по съдържание" е ИЗКЛЮЧЕН.
#:
#: ЗАЩО. Измерено на 27.09.2026 върху `d-2`: правилото по съдържание обяви за
#: армировъчен и `…Slab contour` (74 съвпадения по дължина, контрола 1) — контур
#: на плоча, не армировка. Резултатът: портата пада 124 -> 105 от 178, а анализът
#: се забавя 6,3 s -> 128,9 s. По-нататъшното измерване показа, че вредата не е
#: само от контура: обявяването на който и да е допълнителен слой разширява
#: множеството кандидати и мести избора при позиции, които преди са били верни
#: (19 нови блокирани срещу 19 отпаднали).
#:
#: Затова доказателството „надпис намира дължината си наблизо" е ПРЕКАЛЕНО СЛАБО
#: за тази роля без име. Правилата се образуват и остават в паметта като
#: КАНДИДАТИ с доказателствата си, но не се приемат и не се прилагат.
#: Заместникът е ``dxf/rebar_geometry_evidence.py``.
CONTENT_ONLY_DISABLED_ROLES = frozenset({"REBAR_GEOMETRY"})

#: Диагностичен ключ за ВЪЗПРОИЗВЕЖДАНЕ на измерването, не настройка за
#: собственика. ``LION_REBAR_CONTENT_ONLY_GEOMETRY=on`` връща старото
#: поведение, за да може „преди" и „след" да се мерят без редакция на кода.
_КЛЮЧ_СЪДЪРЖАНИЕ = "LION_REBAR_CONTENT_ONLY_GEOMETRY"


def content_only_disabled_roles() -> frozenset[str]:
    """Кои роли НЕ се приемат в режим само по съдържание.

    Чете се при всяко извикване: тестовете и диагностичните прогони сменят
    средата след вноса на модула.
    """

    if os.environ.get(_КЛЮЧ_СЪДЪРЖАНИЕ, "").strip().lower() in ("on", "1", "yes", "да"):
        return frozenset()
    return CONTENT_ONLY_DISABLED_ROLES

#: Под колко примера в ЦЕЛЕВИЯ чертеж наученото не помага. Нула никога.
MIN_TARGET_SUPPORT = 2

#: Роля от именния класификатор с тази увереност е независимо доказателство и
#: противоречието с нея е контрапример.
NAME_CLASSIFIER_TRUST = 0.96

#: Ролите, за които се учи. Нарочно НЕ включват NOISE: падането на обект от
#: анализа е промяна в инженерния резултат.
LEARNABLE_ROLES = ("ZONE_TITLE", "REBAR_GEOMETRY", "POSITION_KEY",
                   "POSITION_ANNOTATION", "POSITION_LABEL_BLOCK",
                   "POSITION_KEY_SPEC")

#: Кое поле на ``counts`` носи „своите примери" за всяка роля. Имената са
#: прочетени от самите правила в ``convention_profile`` (редове 311, 381, 430,
#: 495, 668), а не предположени — три от петте не са това, което изглежда.
SUPPORT_FIELD = {
    "ZONE_TITLE": "zone_titles",
    "REBAR_GEOMETRY": "local_length_matches",
    "POSITION_KEY": "inside_circle",
    "POSITION_ANNOTATION": "rebar_texts",
    "POSITION_LABEL_BLOCK": "dominant_tag_set",
    # Легендата на позиционните ключове — виж ``learning/spec_layer_profile.py``.
    "POSITION_KEY_SPEC": "legend_specs",
}

#: За ``REBAR_GEOMETRY`` превесът над контролата е защитата срещу случайно
#: съвпадение и НЕ се отслабва от наученото. Виж ``KRITERII.json``.
CONTROL_FIELD = "control_matches"
MIN_CONTROL_RATIO = 4.0

_РАЗДЕЛИТЕЛИ = re.compile(r"[^A-ZА-Я0-9]+")


def independent_key(наблюдение: Mapping[str, Any]) -> str:
    """По кой НЕЗАВИСИМ ИЗТОЧНИК се брои наблюдението.

    Ключът е ``independence_group`` — изчислен по съдържание в
    ``auto_cycle.assign_independence_groups``.

    **Празен низ значи „не се брои за свидетелство".** Такъв е случаят с
    ``ГРУПА_НЕДОКАЗАНА``: наблюдение без подпис по съдържание. Без това изключване
    остава дупка — стара памет без подписи образува ЕДНА група, която заедно с
    едно ново наблюдение би дала „две свидетелства". Тоест точно старият дефект,
    върнат през задната врата.

    Ако полето липсва напълно (ръчно построено наблюдение в тест или скрипт), се
    пада назад към ``family`` и правилото получава ``independence_proven: false``
    в ``validation``, за да е видимо, че независимостта не е доказана по
    съдържание.
    """

    from lion_rebar.learning.auto_cycle import ГРУПА_НЕДОКАЗАНА

    група = str(наблюдение.get("independence_group") or "")
    if група == ГРУПА_НЕДОКАЗАНА:
        return ""
    if група:
        return група
    return str(наблюдение.get("family") or наблюдение.get("drawing") or "")


def _семейни_присъди(подписи: Sequence[Mapping[str, Any]]) -> list[str]:
    """Присъдите „едно / различни семейства" между донорите, по диалекта им."""

    from lion_rebar.learning.source_identity import family_relation

    изход: list[str] = []
    for и, а in enumerate(подписи):
        for б in подписи[и + 1:]:
            изход.append(family_relation(а, б)["verdict"])
    return изход


def name_token(layer: str) -> str:
    """Нормализиран признак на името — БЕЗ цифрите на края.

    ``Layer1``, ``Layer2`` и ``Layer6`` дават един признак ``LAYER``: в корпуса
    те са един и същ шаблон на едно CAD, а цифрата е поредност, не смисъл.
    Признакът СТЕСНЯВА правилото (целевият слой трябва да го има), затова по-общ
    признак не прави правилото по-опасно — опасно би било да няма проверка по
    съдържание, а тя е задължителна.
    """

    горе = str(layer or "").strip().upper().replace("$", "")
    части = [ч for ч in _РАЗДЕЛИТЕЛИ.split(горе) if ч]
    изчистени = [re.sub(r"\d+$", "", ч) or ч for ч in части]
    return "|".join(изчистени)


def signature_of(role: str, layer: str, counts: Mapping[str, Any],
                 *, match_mode: str = MATCH_NAME_TOKEN) -> dict[str, Any]:
    """Самоличността на правилото.

    НЕ съдържа променливи полета на анализа (``batch_status``,
    ``problem_codes``, ``candidate_shape_code``) — първият експеримент измери, че
    те се разминават при всяко подобрение на разпознаването.

    ``match_mode``:

    ``NAME_TOKEN``    — правилото важи само за слой със същия признак на име;
    ``CONTENT_ONLY``  — важи за всеки слой, чието СЪДЪРЖАНИЕ дава същия вид
                        доказателство. Признакът на името не участва.
    """

    подпис = {
        "role": role,
        "evidence_kind": SUPPORT_FIELD.get(role, "unknown"),
        "match_mode": match_mode,
    }
    if match_mode == MATCH_NAME_TOKEN:
        подпис["name_token"] = name_token(layer)
    return подпис


def signature_id(signature: Mapping[str, Any]) -> str:
    основа = "|".join(
        "%s=%s" % (к, signature.get(к)) for к in sorted(signature)
    )
    кратко = hashlib.sha256(основа.encode("utf-8")).hexdigest()[:10]
    return "CR-%s-%s" % (str(signature.get("role") or "?"), кратко)


def _support(counts: Mapping[str, Any], role: str) -> int:
    поле = SUPPORT_FIELD.get(role)
    if not поле:
        return 0
    try:
        return int(counts.get(поле) or 0)
    except (TypeError, ValueError):
        return 0


def learn(
    наблюдения: Sequence[Mapping[str, Any]],
    *,
    store: KnowledgeStore,
) -> dict[str, Any]:
    """Извлича, проверява и записва правила.

    ``наблюдения`` е по един запис за чертеж:
      ``{"drawing", "family", "locked", "profile", "layer_roles",
         "independence_group", "content_signature"}``
    където ``profile`` е изходът на ``propose_convention_profile``, а
    ``layer_roles`` е ``{слой: {"role", "confidence"}}`` от именния класификатор.

    Приемането се брои по ``independence_group`` — независимите ИЗТОЧНИЦИ, не по
    имена на семейства или файлове. Обхватът на приетото се избира от
    ``learning/scopes.py``: един източник дава проектна конвенция (кандидат), два
    независими източника в непотвърдено различни семейства дават СЕМЕЙНА
    конвенция (адаптация), и само доказано различни семейства дават общо правило.

    Заключените семейства НЕ участват като източник на обучение.
    """

    развойни = [н for н in наблюдения if not н.get("locked")]

    положителни: dict[str, list[dict[str, Any]]] = defaultdict(list)
    контрапримери: dict[str, list[dict[str, Any]]] = defaultdict(list)
    подписи: dict[str, dict[str, Any]] = {}
    доказателства: dict[str, list[dict[str, Any]]] = defaultdict(list)
    # подпис -> роли, които някой предлага за същия подпис (за т.3 и отхвърляне)
    роли_на_подпис: dict[tuple[str, str], set[str]] = defaultdict(set)

    # Диалектът на всеки независим източник: нужен е и за обхвата (семейна
    # конвенция срещу общо правило), и за прилагането.
    стилове_на_източник: dict[str, dict[str, Any]] = {}
    независимостта_е_доказана = True
    for наблюдение in развойни:
        if not str(наблюдение.get("independence_group") or ""):
            независимостта_е_доказана = False

    for наблюдение in развойни:
        чертеж = str(наблюдение.get("drawing") or "")
        семейство = str(наблюдение.get("family") or чертеж)
        източник = independent_key(наблюдение)
        профил = наблюдение.get("profile") or {}
        имена = наблюдение.get("layer_roles") or {}
        if източник and източник not in стилове_на_източник:
            from lion_rebar.learning.source_identity import style_signature

            подпис = наблюдение.get("content_signature") or {}
            if подпис:
                стилове_на_източник[източник] = style_signature(подпис)

        for предложение in профил.get("proposals") or ():
            роля = str(предложение.get("role") or "")
            if роля not in LEARNABLE_ROLES:
                continue
            слой = str(предложение.get("layer") or "")
            if not слой:
                continue
            counts = предложение.get("counts") or {}
            описание = имена.get(слой) or {}
            чужда = str(описание.get("role") or "")
            увереност = float(описание.get("confidence") or 0.0)
            противоречие = (
                чужда and чужда != роля and чужда != "UNKNOWN"
                and увереност >= NAME_CLASSIFIER_TRUST
                and not _съвместими(роля, чужда)
            )

            # Всяко предложение храни ДВА кандидата: по признак на име и само по
            # съдържание. Вторият е по-полезен (важи и за преименуван слой) и
            # по-рискован, затова се приема по-трудно — виж критериите долу.
            for режим in (MATCH_NAME_TOKEN, MATCH_CONTENT_ONLY):
                подпис = signature_of(роля, слой, counts, match_mode=режим)
                ид = signature_id(подпис)
                подписи[ид] = подпис
                роли_на_подпис[
                    (подпис["evidence_kind"], подпис.get("name_token") or "*")
                ].add(роля)

                положителни[ид].append({
                    "drawing": чертеж,
                    "family": семейство,
                    "independence_group": източник,
                    "layer": слой,
                    "name_token": name_token(слой),
                    "support": _support(counts, роля),
                    "counts": dict(counts),
                })
                доказателства[ид].append({
                    "drawing": чертеж,
                    "layer": слой,
                    "evidence": str(предложение.get("evidence") or "")[:220],
                    "sample_handles": list(предложение.get("sample_handles") or ())[:3],
                })
                if противоречие:
                    контрапримери[ид].append({
                        "drawing": чертеж, "layer": слой,
                        "reason": "именният класификатор дава %s с увереност %.2f"
                                  % (чужда, увереност),
                    })

    отчет = {
        "learner_version": LEARNER_VERSION,
        "learned_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_drawings": [str(н.get("drawing") or "") for н in развойни],
        "excluded_locked": [str(н.get("drawing") or "") for н in наблюдения
                            if н.get("locked")],
        "accepted": [], "candidates": [], "rejected": [],
    }

    from lion_rebar.learning import scopes

    for ид, подпис in sorted(подписи.items()):
        свои = положителни[ид]
        семейства = sorted({str(п["family"]) for п in свои})
        # Празният ключ НЕ се брои: това е наблюдение, чиято независимост не е
        # доказана (виж ``independent_key``).
        източници = sorted({str(п.get("independence_group") or "") for п in свои}
                           - {""})
        донорски_стилове = [стилове_на_източник[и] for и in източници
                            if и in стилове_на_източник]
        обхват = scopes.scope_for(
            independent_sources=len(източници),
            family_verdicts=_семейни_присъди(донорски_стилове),
        )
        против = контрапримери.get(ид) or []
        ключ = (подпис["evidence_kind"], подпис.get("name_token") or "*")
        роли = роли_на_подпис.get(ключ) or set()

        причини_за_отказ = [
            "нула или един свой пример в целевия слой (под %d)" % MIN_TARGET_SUPPORT,
        ]
        if str(подпис.get("match_mode") or MATCH_NAME_TOKEN) == MATCH_NAME_TOKEN:
            причини_за_отказ.append("целевият слой няма признака на името")
        else:
            причини_за_отказ.append(
                "именният класификатор дава на целевия слой друга роля с увереност "
                ">= %.2f" % NAME_CLASSIFIER_TRUST)
        if подпис["role"] == "REBAR_GEOMETRY":
            причини_за_отказ.append(
                "превесът над контролата под 4.0 в целевия чертеж"
            )

        режим = str(подпис.get("match_mode") or MATCH_NAME_TOKEN)
        признаци = sorted({str(п.get("name_token") or "") for п in свои})
        нужни = (MIN_INDEPENDENT_CONTENT_ONLY if режим == MATCH_CONTENT_ONLY
                 else MIN_INDEPENDENT_TO_ACCEPT)

        if (режим == MATCH_CONTENT_ONLY
                and str(подпис.get("role") or "") in content_only_disabled_roles()):
            състояние, бележка = CANDIDATE, (
                "режимът само по съдържание е изключен за %s: доказателството "
                "(надпис намира дължината си наблизо) приема и контури "
                "(измерено на d-2, порта 124 -> 105)" % подпис.get("role"))
        elif против:
            състояние, бележка = REJECTED, "има контрапример"
        elif len(роли) > 1:
            състояние, бележка = REJECTED, (
                "подписът съвпада с повече от една роля: " + ", ".join(sorted(роли))
            )
        elif len(източници) < нужни:
            състояние, бележка = CANDIDATE, (
                "доказано в %d НЕЗАВИСИМИ източника (%s), а за режим %s трябват %d"
                % (len(източници), ", ".join(източници), режим, нужни)
            )
        elif (режим == MATCH_CONTENT_ONLY
              and len(признаци) < MIN_DISTINCT_NAME_TOKENS_CONTENT_ONLY):
            състояние, бележка = CANDIDATE, (
                "само по съдържание, но всички примери са с едно и също име (%s) "
                "— това е правило по име, маскирано като по съдържание"
                % ", ".join(признаци)
            )
        elif not scopes.is_applicable(обхват["scope"]):
            състояние, бележка = CANDIDATE, (
                "обхватът е %s: %s" % (обхват["scope"], обхват["verdict"])
            )
        else:
            състояние, бележка = ACCEPTED, (
                "доказано по съдържание в %d независими източника, обхват %s "
                "(режим %s, имена: %s)"
                % (len(източници), обхват["scope"], режим,
                   ", ".join(признаци) or "—")
            )

        правило = LearnedRule(
            rule_id=ид,
            version=1,
            role=str(подпис["role"]),
            signature=dict(подпис),
            state=състояние,
            origin="convention_profile/%s + learner/%s"
                   % (str((развойни[0].get("profile") or {}).get("proposer_version")
                          if развойни else "?"), LEARNER_VERSION),
            evidence=доказателства.get(ид) or [],
            positives=свои,
            counterexamples=против,
            scope={
                "families": семейства,
                "family_count": len(семейства),
                "independent_sources": len(източници),
                "independence_groups": източници,
                "learning_scope": обхват["scope"],
                "scope_verdict": обхват["verdict"],
                # Диалектът на донорите: с него при прилагане се проверява дали
                # целевият чертеж е в СЪЩОТО семейство (``scopes.target_in_scope``).
                "donor_styles": донорски_стилове,
                "match_mode": режим,
                "name_token": подпис.get("name_token"),
                "name_tokens_seen": признаци,
                "min_target_support": MIN_TARGET_SUPPORT,
                "applies_to": "слой в чертеж, чието съдържание дава поне %d свои примера"
                              % MIN_TARGET_SUPPORT,
            },
            refusal_conditions=причини_за_отказ,
            validation={
                "criteria": "C:/lr_exp/rezultat/etap_2/KRITERII.json",
                "verdict": бележка,
                "min_independent_to_accept": MIN_INDEPENDENT_TO_ACCEPT,
                "independent_sources": len(източници),
                "independence_proven": независимостта_е_доказана,
                "name_classifier_trust": NAME_CLASSIFIER_TRUST,
                "counterexample_count": len(против),
            },
        )
        записано = store.put(правило)
        кратко = {
            "rule_id": записано.rule_id, "role": записано.role,
            "name_token": (подпис.get("name_token") or "*"),
            "families": семейства, "verdict": бележка,
            "independent_sources": len(източници),
            "learning_scope": обхват["scope"],
        }
        if състояние == ACCEPTED:
            отчет["accepted"].append(кратко)
        elif състояние == CANDIDATE:
            отчет["candidates"].append(кратко)
        else:
            отчет["rejected"].append(кратко)

    store.set_meta(
        learner_version=LEARNER_VERSION,
        learned_at=отчет["learned_at"],
        source_drawings=отчет["source_drawings"],
        excluded_locked=отчет["excluded_locked"],
        criteria="C:/lr_exp/rezultat/etap_2/KRITERII.json",
    )
    return отчет


def _съвместими(предложена: str, именна: str) -> bool:
    """Двете роли говорят ли за едно и също нещо с различни имена.

    ``REBAR_TEXT`` на именния класификатор и ``POSITION_ANNOTATION`` на паспорта
    са едно и също — надписите на позициите. Не е контрапример.
    """

    еднакви = (
        {"POSITION_ANNOTATION", "REBAR_TEXT"},
        {"POSITION_LABEL_BLOCK", "REBAR_TEXT"},
        {"REBAR_GEOMETRY", "STIRRUPS"},
        # Слоят с легендата („N18 L=410") Е текст на армировка. Именният
        # класификатор го нарича REBAR_TEXT с 0.99; това е същото нещо с друго
        # име, не противоречие.
        {"POSITION_KEY_SPEC", "REBAR_TEXT"},
        {"POSITION_KEY_SPEC", "LEGEND"},
    )
    двойка = {предложена, именна}
    return any(двойка <= група for група in еднакви)
