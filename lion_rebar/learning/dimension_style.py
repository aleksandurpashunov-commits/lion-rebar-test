# -*- coding: utf-8 -*-
"""Научаване на ЕДНА способност: къде проектантът пише размерите на участъците.

## Каква е способността

Верижното правило (``dxf/chain_printed_dimensions.py``) чете производствения
вектор от собствените коти на пръта. То ОТКАЗВА, когато в обхвата на рамената
попаднат ПОВЕЧЕ числови надписи, отколкото са рамената — изборът кой от тях е
размер би бил догадка.

Много чертежи обаче имат постоянен навик: размерите на участъците стоят на ЕДИН
определен слой, а другите числа наоколо (коти на конструкцията, номера на оси,
височини) са на други слоеве. Ако програмата сама забележи този навик, тя може
да ограничи търсенето до този слой и да прочете пръта, който иначе би отказала.

## Защо е безопасно

* прилага се **само след като неограниченият прочит вече е отказал** — тоест
  може само да ДОБАВИ прочит, никога да отнеме вече получен;
* ограничава КОИ текстове се гледат; не създава и не променя нито едно число;
* всички останали проверки остават: еднозначно присвояване със запас, по един
  размер на рамо и **сборът трябва да е точно обявената дължина**. Обявената
  дължина идва от надписа на позицията, не от слоя — затова научената роля не
  може да се самопотвърди.

## Цикълът, изцяло вътре в програмата

1. **Наблюдение** — от САМИТЕ обекти на чертежа, без участие на анализа и без
   решенията му: за всяка верига от свързани отсечки с 2–6 рамена се гледа дали
   числовите надписи в обхвата ѝ са точно колкото рамената и дали идват от един
   слой. Така паметта не се учи от собствените си решения.
2. **Кандидат** — двойка (слой на геометрията → слой на размерите) с достатъчно
   опора и без контрапример в същия чертеж.
3. **Проверка** — срещу НАТРУПАНИТЕ наблюдения от другите чертежи.
4. **Приемане** — само при опора в поне ``НУЖНИ_НЕЗАВИСИМИ`` РАЗЛИЧНИ НЕЗАВИСИМИ
   ИЗТОЧНИКА и нула контрапримера.
5. **Запис** — в ``knowledge_store`` с произход, обхват, доказателства, условия
   за отказ и възможност за оттегляне.
6. **Ползване** — от СЛЕДВАЩИЯ анализ нататък, включително след рестарт.

Контрапример е чертеж, в който същият геометричен слой носи размери от ДРУГ
слой. Тогава правилото отива в ``REJECTED`` и не се преоткрива.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Iterable, Mapping, Sequence

#: Ролята за СЛОЯ: „размерите на участъците стоят на слой X".
РОЛЯ = "SEGMENT_DIMENSION_TEXT"

#: Ролята за НАВИКА: „размерът се пише УСПОРЕДНО на своето рамо, до T градуса".
#: Тя е независима от имената на слоевете и затова се пренася между офиси —
#: точно затова се учи отделно от слоя.
РОЛЯ_УСПОРЕДНОСТ = "SEGMENT_DIMENSION_PARALLEL"

#: Колко вериги в ЕДИН чертеж трябва да подкрепят двойката, за да стане кандидат.
#: Под това число съвпадението е случайност: две вериги с по три рамена се
#: покриват от шест произволни числа твърде лесно.
НУЖНА_ОПОРА = 4

#: Колко РАЗЛИЧНИ НЕЗАВИСИМИ ИЗТОЧНИКА трябват за приемане.
#:
#: ПОПРАВЕНО на 30.09.2026. Дотогава тук стоеше: „Копие на същия чертеж не е
#: второ доказателство — ``auto_cycle.record_observation`` пази по sha256." Това
#: беше НЕВЯРНО: пазенето беше по ``family`` ИЛИ ``source_sha256``, тоест по
#: имена. Измерено (``rezultat/closure_final/agent_nezavisimost/PREDI.json``):
#: ``d-1`` и ``s-10`` са ЕДИН чертеж (вложеност на надписите 0,9469) с различни
#: sha и различни имена и даваха две „семейства" — 6 приети правила от един
#: чертеж, подаден двукратно; ``s-8`` ⊂ ``s-9`` (вложеност 1,0000) даваха 3.
#: Сега се броят групите по СЪДЪРЖАНИЕ (``auto_cycle.assign_independence_groups``).
НУЖНИ_НЕЗАВИСИМИ = 2

#: Старото име. Пази се за външен код; значението вече е „независими източника".
НУЖНИ_СЕМЕЙСТВА = НУЖНИ_НЕЗАВИСИМИ

#: Дял на веригите на слоя, които трябва да сочат СЪЩИЯ размерен слой. Под 1,0
#: означава смесен навик и правилото не се предлага.
НУЖНА_ЧИСТОТА = 1.0

#: Праговете за успоредност, които се пробват. Научава се НАЙ-ТЕСНИЯТ, който
#: покрива поне ``НУЖЕН_ДЯЛ_УСПОРЕДНИ`` от наблюдаваните собствени коти.
ПРАГОВЕ_УСПОРЕДНОСТ_DEG = (5.0, 10.0, 15.0, 30.0)

#: Какъв дял от собствените коти трябва да са успоредни, за да се научи стилът.
#: Под 1,0 нарочно: филтърът се пуска САМО след отказ и сборът пак решава, тоест
#: изтърван собственик води до същия отказ, не до грешно число.
НУЖЕН_ДЯЛ_УСПОРЕДНИ = 0.90

УСЛОВИЯ_ЗА_ОТКАЗ = (
    "прилага се САМО след като неограниченият прочит вече е отказал",
    "сборът на отпечатаните числа трябва да е точно обявената дължина",
    "не се прилага, ако ограничаването остави по-малко надписи от рамена",
    "оттегля се при чертеж, в който същият геометричен слой носи размери от друг слой",
)


def _отклонение_deg(ротация: float, посока: float) -> float:
    """Ъгловото отклонение текст ↔ рамо, по модул 180°."""

    а, б = float(ротация) % 180.0, float(посока) % 180.0
    return min(abs(а - б), 180.0 - abs(а - б))


def _вериги_на_чертежа(entities: Sequence[Any]) -> list[tuple[str, list, list]]:
    """Всички прости отворени вериги с 2–6 рамена, по слоеве."""

    from lion_rebar.dxf import chain_printed_dimensions as вер

    указател = вер.ВериженУказател(entities)
    видени: set[int] = set()
    изход: list[tuple[str, list, list]] = []
    for handle in указател.по_handle:
        части = указател.по_handle[handle]
        if not части or id(части[0]) in видени:
            continue
        събрано = вер.събери_веригата([handle], указател)
        if събрано is None:
            видени.add(id(части[0]))
            continue
        отсечки, слой = събрано
        for отсечка in отсечки:
            видени.add(id(отсечка))
        подредени = вер.подреди_веригата(отсечки)
        if подредени is None:
            continue
        рамена = вер.рамена_на_веригата(подредени)
        if рамена is None or not (2 <= len(рамена) <= вер.МАКС_РАМЕНА):
            continue
        кандидати = вер.надписи_на_рамената(рамена, указател)
        изход.append((слой, рамена, кандидати))
    return изход


def observe(entities: Sequence[Any]) -> dict[str, Any]:
    """Наблюдението на ЕДИН чертеж — само от обектите, без анализа.

    За всяка верига се брои двойката (слой на геометрията → слой на размерите)
    само когато надписите в обхвата са ТОЧНО колкото рамената и идват от ЕДИН
    слой. Смесените вериги се броят като „смесени" и правят слоя нечист.
    """

    from lion_rebar.dxf import chain_printed_dimensions as вер

    опора: dict[str, Counter] = defaultdict(Counter)
    общо: Counter = Counter()
    смесени: Counter = Counter()
    обхват: dict[str, float] = {}
    успоредност: dict[str, Counter] = {}
    for слой, рамена, кандидати in _вериги_на_чертежа(entities):
        if not кандидати:
            continue
        общо[слой] += 1
        if len(кандидати) != len(рамена):
            continue
        слоеве = {надпис.слой for надпис, _ in кандидати}
        if len(слоеве) != 1:
            смесени[слой] += 1
            continue
        избор = вер._най_добро_присвояване(рамена, кандидати)
        if избор is None or избор[2] < вер.ЗАПАС_НА_ПРИСВОЯВАНЕТО:
            continue
        размерен = слоеве.pop()
        опора[слой][размерен] += 1
        най_далеч = max(
            вер._разстояние_до_рамото(надпис.точка, рамена[и]) / надпис.височина
            for и, надпис in enumerate(избор[0])
        )
        ключ = "%s→%s" % (слой, размерен)
        обхват[ключ] = max(обхват.get(ключ, 0.0), най_далеч)
        # Успоредността е ВТОРИЯТ признак на стила: пише ли проектантът размера
        # успоредно на рамото му. Мери се същото максимално отклонение.
        отклонение = max(
            _отклонение_deg(надпис.ротация, рамена[и].посока_deg)
            for и, надпис in enumerate(избор[0])
        )
        for праг in ПРАГОВЕ_УСПОРЕДНОСТ_DEG:
            if отклонение <= праг:
                успоредност.setdefault(ключ, Counter())[праг] += 1
        успоредност.setdefault(ключ, Counter())["общо"] += 1

    предложения = []
    for слой, броеве in опора.items():
        подкрепа = sum(броеве.values())
        най, брой = броеве.most_common(1)[0]
        чистота = брой / подкрепа if подкрепа else 0.0
        предложения.append({
            "geometry_layer": слой,
            "dimension_layer": най,
            "support": брой,
            "chains_with_texts": общо.get(слой, 0),
            "purity": round(чистота, 4),
            "mixed": смесени.get(слой, 0),
            "reach_heights": round(обхват.get("%s→%s" % (слой, най), 0.0), 3),
            "parallel_counts": dict(успоредност.get("%s→%s" % (слой, най), Counter())),
            "other_layers": {к: v for к, v in броеве.items() if к != най},
        })
    предложения.sort(key=lambda п: (-п["support"], п["geometry_layer"]))
    return {"role": РОЛЯ, "proposals": предложения}


def _годно(предложение: Mapping[str, Any]) -> bool:
    return (
        int(предложение.get("support") or 0) >= НУЖНА_ОПОРА
        and float(предложение.get("purity") or 0.0) >= НУЖНА_ЧИСТОТА
        and int(предложение.get("mixed") or 0) == 0
    )


def независим_източник(наблюдение: Mapping[str, Any]) -> str:
    """Ключът, по който наблюдението се брои за ЕДНО свидетелство.

    Същата функция като в ``convention_learner`` — едно определение за двата
    обучителя, за да не се разминат.
    """

    from lion_rebar.learning.convention_learner import independent_key

    return independent_key(наблюдение)


def candidates_from(
    наблюдения: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Кандидати и присъди от НАТРУПАНИТЕ наблюдения.

    Всяка двойка (геометричен слой → размерен слой) носи НЕЗАВИСИМИТЕ ИЗТОЧНИЦИ,
    в които е видяна, и контрапримерите: източници, в които СЪЩИЯТ геометричен
    слой сочи друг размерен слой.
    """

    from lion_rebar.learning import scopes
    from lion_rebar.learning.source_identity import style_signature

    по_слой: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    доказателства: dict[str, list[dict[str, Any]]] = defaultdict(list)
    стилове: dict[str, dict[str, Any]] = {}
    имена_на_източник: dict[str, set[str]] = defaultdict(set)
    for наблюдение in наблюдения:
        източник = независим_източник(наблюдение)
        семейство = str(наблюдение.get("family") or наблюдение.get("drawing") or "")
        # Празен ключ значи „независимостта не е доказана" — наблюдението дава
        # доказателствен ред, но НЕ се брои за свидетелство.
        if източник:
            имена_на_източник[източник].add(семейство)
        подпис = наблюдение.get("content_signature") or {}
        if подпис and източник and източник not in стилове:
            стилове[източник] = style_signature(подпис)
        стил = (наблюдение.get("profile") or {}).get("dimension_style") or {}
        for предложение in (стил.get("proposals") or ()):
            if not _годно(предложение):
                continue
            геом = str(предложение.get("geometry_layer") or "")
            разм = str(предложение.get("dimension_layer") or "")
            if not геом or not разм:
                continue
            if източник:
                по_слой[геом][разм].add(източник)
            else:
                по_слой[геом].setdefault(разм, set())
            доказателства["%s→%s" % (геом, разм)].append({
                "family": семейство,
                "independence_group": източник,
                "support": предложение.get("support"),
                "purity": предложение.get("purity"),
                "reach_heights": предложение.get("reach_heights"),
            })

    изход: list[dict[str, Any]] = []
    for геом, варианти in по_слой.items():
        for разм, източници in варианти.items():
            контра = sorted(
                с for друг, други in варианти.items() if друг != разм
                for с in други
            )
            ключ = "%s→%s" % (геом, разм)
            донорски = [стилове[и] for и in sorted(източници) if и in стилове]
            обхват = scopes.scope_for(
                independent_sources=len(източници),
                family_verdicts=_семейни_присъди(донорски),
            )
            семейства = sorted({
                с for и in източници for с in (имена_на_източник.get(и) or ())})
            изход.append({
                "role": РОЛЯ,
                "geometry_layer": геом,
                "dimension_layer": разм,
                "independence_groups": sorted(източници),
                "families": семейства,
                "donor_styles": донорски,
                "scope": обхват,
                "counterexample_families": контра,
                "evidence": доказателства[ключ],
                "accept": (len(източници) >= НУЖНИ_НЕЗАВИСИМИ and not контра
                           and scopes.is_applicable(обхват["scope"])),
                "reject": bool(контра),
            })
    изход.sort(key=lambda п: (-len(п["independence_groups"]), п["geometry_layer"]))
    return изход


def _семейни_присъди(подписи: Sequence[Mapping[str, Any]]) -> list[str]:
    from lion_rebar.learning.convention_learner import _семейни_присъди as общо

    return общо(подписи)


def rule_id_for(геометричен: str, размерен: str) -> str:
    import hashlib

    ключ = "%s|%s→%s" % (РОЛЯ, геометричен.strip().upper(), размерен.strip().upper())
    return "%s-%s" % (РОЛЯ.lower().replace("_", "-"),
                      hashlib.sha256(ключ.encode("utf-8")).hexdigest()[:12])


def learn(
    наблюдения: Iterable[Mapping[str, Any]],
    *,
    store: Any,
) -> dict[str, list[dict[str, Any]]]:
    """Създава, приема или отхвърля правилата за размерния слой.

    Заключените семейства (``locked``) НЕ участват в обучението — те са
    изпитът. Правило с контрапример отива в ``REJECTED`` и остава там, за да не
    се преоткрива.
    """

    from lion_rebar.learning.knowledge_store import (
        ACCEPTED, CANDIDATE, LearnedRule, REJECTED,
    )

    за_учене = [н for н in наблюдения if not bool(н.get("locked"))]
    отчет: dict[str, list[dict[str, Any]]] = {
        "accepted": [], "candidates": [], "rejected": []}
    for присъда in candidates_from(за_учене):
        геом = присъда["geometry_layer"]
        разм = присъда["dimension_layer"]
        ид = rule_id_for(геом, разм)
        старо = store.get(ид)
        if присъда["reject"]:
            състояние = REJECTED
        elif присъда["accept"]:
            състояние = ACCEPTED
        else:
            # Под прага за независими източници или извън приложим обхват —
            # правилото се пази с доказателствата си, но не се прилага.
            състояние = CANDIDATE
        if старо is not None and старо.state == REJECTED and състояние != REJECTED:
            # Веднъж оборено правило не се връща от само себе си.
            continue
        правило = LearnedRule(
            rule_id=ид,
            version=(старо.version if старо else 1),
            role=РОЛЯ,
            signature={"role": РОЛЯ, "geometry_layer": геом, "dimension_layer": разм},
            state=състояние,
            origin=(
                "наблюдение на самите обекти: вериги от свързани отсечки на слой "
                "%r, чиито числови надписи са точно колкото рамената и идват от "
                "слой %r" % (геом, разм)
            ),
            evidence=list(присъда["evidence"]),
            positives=[{"family": с} for с in присъда["families"]],
            counterexamples=[{"family": с, "reason": "същият геометричен слой "
                              "сочи друг размерен слой"}
                             for с in присъда["counterexample_families"]],
            scope={
                "geometry_layer": геом,
                "dimension_layer": разм,
                "applies_to": "chain_printed_dimensions — само след отказ на "
                              "неограничения прочит",
                "families": list(присъда["families"]),
                "independence_groups": list(присъда["independence_groups"]),
                "independent_sources": len(присъда["independence_groups"]),
                "learning_scope": присъда["scope"]["scope"],
                "scope_verdict": присъда["scope"]["verdict"],
                "donor_styles": list(присъда["donor_styles"]),
            },
            refusal_conditions=list(УСЛОВИЯ_ЗА_ОТКАЗ),
            validation={
                "required_independent": НУЖНИ_НЕЗАВИСИМИ,
                "required_support": НУЖНА_ОПОРА,
                "required_purity": НУЖНА_ЧИСТОТА,
                "independent_sources": len(присъда["independence_groups"]),
                "families_seen": len(присъда["families"]),
            },
        )
        store.put(правило)
        ключ = ("accepted" if състояние == ACCEPTED
                else "rejected" if състояние == REJECTED else "candidates")
        отчет[ключ].append({"rule_id": ид, "role": РОЛЯ,
                            "geometry_layer": геом, "dimension_layer": разм})
    return отчет


def learned_mapping(правила: Iterable[Any],
                    *, target_style: Mapping[str, Any] | None = None,
                    ) -> dict[str, str]:
    """Приетите правила като {геометричен слой (главни букви): размерен слой}.

    ``target_style`` работи като в ``learned_styles``: правило със СЕМЕЕН обхват
    важи само в своето семейство. Проверката е тук, а не само в ``learned_styles``,
    за да няма достъп до приетите правила БЕЗ проверка на обхвата.
    """

    from lion_rebar.learning import scopes

    карта: dict[str, str] = {}
    for правило in правила:
        if str(getattr(правило, "role", "")) != РОЛЯ:
            continue
        в_обхват, _ = scopes.target_in_scope(правило, target_style=target_style)
        if not в_обхват:
            continue
        подпис = getattr(правило, "signature", None) or {}
        геом = str(подпис.get("geometry_layer") or "").strip()
        разм = str(подпис.get("dimension_layer") or "").strip()
        if геом and разм:
            карта[геом.upper()] = разм
    return карта


# --------------------------------------------------------------------------
# Вторият, ПРЕНОСИМ навик: размерът се пише успоредно на рамото си
# --------------------------------------------------------------------------

def parallel_candidate(наблюдения: Iterable[Mapping[str, Any]]) -> dict[str, Any] | None:
    """Най-тесният праг за успоредност, който всеки НЕЗАВИСИМ ИЗТОЧНИК подкрепя.

    Признакът НЕ съдържа име на слой, файл или handle — само ъгъл. Затова се
    пренася между офиси, докато правилото за слоя важи само за своя офис.

    Броенето е по ``independence_group``: два файла на един чертеж подкрепят един
    праг, не два. Заедно с това се пази и обхватът — навик, видян в един
    независим източник, остава проектна конвенция.
    """

    from lion_rebar.learning import scopes
    from lion_rebar.learning.source_identity import style_signature

    по_семейство: dict[str, dict[float, tuple[int, int]]] = {}
    стилове: dict[str, dict[str, Any]] = {}
    for наблюдение in наблюдения:
        семейство = независим_източник(наблюдение)
        if not семейство:
            # Независимостта не е доказана — наблюдението не подкрепя праг.
            continue
        подпис = наблюдение.get("content_signature") or {}
        if подпис and семейство not in стилове:
            стилове[семейство] = style_signature(подпис)
        стил = (наблюдение.get("profile") or {}).get("dimension_style") or {}
        сбор: dict[float, list[int]] = {п: [0, 0] for п in ПРАГОВЕ_УСПОРЕДНОСТ_DEG}
        общо = 0
        for предложение in (стил.get("proposals") or ()):
            if not _годно(предложение):
                continue
            броеве = предложение.get("parallel_counts") or {}
            цял = int(броеве.get("общо") or броеве.get(u"общо") or 0)
            if not цял:
                continue
            общо += цял
            for праг in ПРАГОВЕ_УСПОРЕДНОСТ_DEG:
                сбор[праг][0] += int(броеве.get(str(праг)) or броеве.get(праг) or 0)
        if общо:
            по_семейство[семейство] = {п: (сбор[п][0], общо)
                                       for п in ПРАГОВЕ_УСПОРЕДНОСТ_DEG}
    if len(по_семейство) < НУЖНИ_НЕЗАВИСИМИ:
        return None
    донорски = [стилове[и] for и in sorted(по_семейство) if и in стилове]
    обхват = scopes.scope_for(
        independent_sources=len(по_семейство),
        family_verdicts=_семейни_присъди(донорски),
    )
    if not scopes.is_applicable(обхват["scope"]):
        return None
    for праг in ПРАГОВЕ_УСПОРЕДНОСТ_DEG:
        дялове = {с: (в[праг][0] / в[праг][1] if в[праг][1] else 0.0)
                  for с, в in по_семейство.items()}
        if all(д >= НУЖЕН_ДЯЛ_УСПОРЕДНИ for д in дялове.values()):
            return {
                "role": РОЛЯ_УСПОРЕДНОСТ,
                "parallel_deg": праг,
                "families": sorted(по_семейство),
                "independence_groups": sorted(по_семейство),
                "donor_styles": донорски,
                "scope": обхват,
                "shares": {с: round(д, 4) for с, д in дялове.items()},
                "evidence": [
                    {"family": с, "within": в[праг][0], "total": в[праг][1],
                     "share": round(в[праг][0] / в[праг][1], 4) if в[праг][1] else 0.0}
                    for с, в in sorted(по_семейство.items())
                ],
            }
    return None


def learn_parallel(наблюдения: Iterable[Mapping[str, Any]], *, store: Any) -> dict:
    """Приема или отхвърля преносимия навик за успоредност."""

    from lion_rebar.learning.knowledge_store import ACCEPTED, LearnedRule, REJECTED

    за_учене = [н for н in наблюдения if not bool(н.get("locked"))]
    присъда = parallel_candidate(за_учене)
    ид = "segment-dimension-parallel"
    старо = store.get(ид)
    if присъда is None:
        if старо is not None and старо.state == ACCEPTED:
            store.put(LearnedRule(**{**старо.to_dict(), "state": REJECTED,
                                     "counterexamples": [{"reason":
                                      "семействата вече не подкрепят общ праг"}]}))
            return {"accepted": [], "candidates": [], "rejected": [{"rule_id": ид}]}
        return {"accepted": [], "candidates": [], "rejected": []}
    правило = LearnedRule(
        rule_id=ид,
        version=(старо.version if старо else 1),
        role=РОЛЯ_УСПОРЕДНОСТ,
        signature={"role": РОЛЯ_УСПОРЕДНОСТ, "parallel_deg": присъда["parallel_deg"]},
        state=ACCEPTED,
        origin=("наблюдение на самите обекти: собствените коти на веригите се "
                "пишат успоредно на рамото си до %g°" % присъда["parallel_deg"]),
        evidence=list(присъда["evidence"]),
        positives=[{"family": с} for с in присъда["families"]],
        counterexamples=[],
        scope={"applies_to": "chain_printed_dimensions — само след отказ на "
                             "неограничения прочит",
               "independent_of_layer_names": True,
               "families": list(присъда["families"]),
               "independence_groups": list(присъда["independence_groups"]),
               "independent_sources": len(присъда["independence_groups"]),
               "learning_scope": присъда["scope"]["scope"],
               "scope_verdict": присъда["scope"]["verdict"],
               "donor_styles": list(присъда["donor_styles"])},
        refusal_conditions=list(УСЛОВИЯ_ЗА_ОТКАЗ),
        validation={"required_independent": НУЖНИ_НЕЗАВИСИМИ,
                    "independent_sources": len(присъда["independence_groups"]),
                    "required_share": НУЖЕН_ДЯЛ_УСПОРЕДНИ,
                    "shares": присъда["shares"]},
    )
    store.put(правило)
    return {"accepted": [{"rule_id": ид, "role": РОЛЯ_УСПОРЕДНОСТ,
                          "parallel_deg": присъда["parallel_deg"]}],
            "candidates": [], "rejected": []}


def learned_styles(правила: Iterable[Any],
                   *, target_style: Mapping[str, Any] | None = None,
                   ) -> dict[str, dict[str, Any]]:
    """Приетите правила като {геометричен слой: {dimension_layer, parallel_deg}}.

    Навикът за успоредност няма слой — той важи за ВСЕКИ слой, затова се слага
    под ключа ``"*"`` и се слива в потребителя.

    ``target_style`` е диалектът на ЦЕЛЕВИЯ чертеж. Правило със СЕМЕЕН обхват се
    прилага само когато целевият чертеж е в същото семейство
    (``scopes.target_in_scope``); без подаден диалект такова правило се отказва.
    """

    from lion_rebar.learning import scopes

    карта: dict[str, dict[str, Any]] = {}
    общ: dict[str, Any] = {}
    for правило in правила:
        роля = str(getattr(правило, "role", ""))
        подпис = getattr(правило, "signature", None) or {}
        в_обхват, _ = scopes.target_in_scope(правило, target_style=target_style)
        if not в_обхват:
            continue
        if роля == РОЛЯ:
            геом = str(подпис.get("geometry_layer") or "").strip()
            разм = str(подпис.get("dimension_layer") or "").strip()
            if геом and разм:
                карта.setdefault(геом.upper(), {})["dimension_layer"] = разм
        elif роля == РОЛЯ_УСПОРЕДНОСТ:
            try:
                общ["parallel_deg"] = float(подпис.get("parallel_deg"))
            except (TypeError, ValueError):
                pass
    if общ:
        карта.setdefault("*", {}).update(общ)
    return карта
