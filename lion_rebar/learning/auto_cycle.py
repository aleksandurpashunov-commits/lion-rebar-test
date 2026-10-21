"""Автоматичният цикъл: прилага, записва неразпознатото, учи, приема, ползва.

Това е стъпка 8 от заданието — вътре в програмата, без ръчна команда:

1. прилага приетите знания върху новия чертеж (през ``policy_bridge``);
2. записва наблюдението на чертежа и неразпознатите случаи с причините;
3. извлича нови кандидати от това наблюдение;
4. проверява ги срещу НАТРУПАНИТЕ наблюдения и контрапримерите;
5. приема покриващите критериите;
6. приетото важи от СЛЕДВАЩИЯ анализ нататък.

ЗАЩО от следващия. Ако чертежът, който точно сега се анализира, може сам да си
издаде разрешение, това е самолицензиране: правилото щеше да се обоснове с
данните, върху които се прилага. Затова приложението чете хранилището отпреди
цикъла, а наученото влиза в сила след него. Така „обучаващият" и „изпитваният"
не са един и същ чертеж в един и същ прогон.

Наблюдението НЕ съдържа чертожни данни — само профила по съдържание (роли,
броеве, причини за отказ) и самоличността. Нищо не излиза извън папката с данни.

ПОРТАТА ЗА НЕЗАВИСИМОСТ. До 30.09.2026 дублирането се пазеше по ``family``
**или** ``source_sha256`` — две ИМЕНА. Измерено (``PREDI.json``): ``d-1`` и
``s-10`` са ЕДИН чертеж с различни sha и различни имена и минаваха за две
доказателства, при което се приемаха 6 правила; ``s-8`` ⊂ ``s-9`` минаваха и
приемаха 3. Сега независимостта се доказва по СЪДЪРЖАНИЕ
(``learning/source_identity.py``): всеки запис получава ``independence_group`` и
обучителите броят ГРУПИ, не файлове.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from lion_rebar.learning.convention_learner import learn
from lion_rebar.learning.knowledge_store import KnowledgeStore

OBSERVATIONS_NAME = "nablyudeniya.jsonl"
CYCLE_SCHEMA = "lion-rebar-auto-cycle/v1"

#: Колко наблюдения се пазят. Без таван файлът расте безкрайно.
MAX_OBSERVATIONS = 500


#: Името на групата за записите БЕЗ подпис по съдържание. Всички те са ЕДНА
#: група: липсващият подпис не доказва независимост, а непроверимото не се брои
#: за второ свидетелство. Така стара памет (отпреди подписа) не се губи, но и не
#: се брои за повече от едно свидетелство.
ГРУПА_НЕДОКАЗАНА = "НЕДОКАЗАНА"


@dataclass(frozen=True)
class CycleResult:
    drawing: str
    family: str
    applied_rules: tuple[str, ...]
    unrecognised: tuple[dict[str, Any], ...]
    observations_total: int
    accepted_now: tuple[str, ...]
    candidates_now: tuple[str, ...]
    rejected_now: tuple[str, ...]
    store_path: str
    seconds: float
    #: Колко РАЗЛИЧНИ независими източника стоят зад наблюденията. Това, а не
    #: ``observations_total``, е числото, с което се приема правило.
    independent_sources: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": CYCLE_SCHEMA,
            "drawing": self.drawing, "family": self.family,
            "applied_rules": list(self.applied_rules),
            "unrecognised": [dict(р) for р in self.unrecognised],
            "observations_total": self.observations_total,
            "independent_sources": self.independent_sources,
            "accepted_now": list(self.accepted_now),
            "candidates_now": list(self.candidates_now),
            "rejected_now": list(self.rejected_now),
            "store_path": self.store_path,
            "seconds": round(self.seconds, 2),
        }


def observations_path(store: KnowledgeStore) -> Path:
    return Path(store.path).parent / OBSERVATIONS_NAME


def load_observations(път: Path) -> list[dict[str, Any]]:
    if not път.is_file():
        return []
    изход: list[dict[str, Any]] = []
    for ред in път.read_text(encoding="utf-8").splitlines():
        ред = ред.strip()
        if not ред:
            continue
        try:
            запис = json.loads(ред)
        except ValueError:
            continue
        if isinstance(запис, dict) and запис.get("drawing"):
            изход.append(запис)
    return изход


def assign_independence_groups(записи: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Слага на всеки запис ``independence_group`` по СЪДЪРЖАНИЕТО му.

    Групите се преизчисляват НАНОВО при всяко добавяне, защото подписът стои в
    самия запис. Така поправката лекува и файлове, записани от по-стар код, без
    отделна миграция.

    Правилото: два записа са в ЕДНА група, когато
    ``source_identity.independence`` НЕ ги обявява за независими — тоест при
    копие, ревизия, поднабор и при всяко неразрешено съмнение. Свързването е
    преходно (обединяване на групи), защото „а е същият като б" и „б е същият
    като в" не може да остави „а" и „в" за две свидетелства.

    Записите БЕЗ подпис отиват всички в ``ГРУПА_НЕДОКАЗАНА``: непроверимото не е
    второ свидетелство, но и не заразява проверимите.
    """

    from lion_rebar.learning.source_identity import (
        has_content, independence, independent_sets, labels_as_set,
    )

    с_подпис = [(и, н) for и, н in enumerate(записи)
                if has_content(н.get("content_signature"))]
    родител = {и: и for и, _ in с_подпис}
    # Множествата се строят ВЕДНЪЖ: при тавана от 500 наблюдения двойките са
    # 124 750 и построяването им наново струваше 7,2 s на анализ (измерено).
    множества = {и: labels_as_set(н.get("content_signature")) for и, н in с_подпис}
    отрязани = {и: bool((н.get("content_signature") or {}).get("labels_truncated"))
                for и, н in с_подпис}

    def корен(х: int) -> int:
        while родител[х] != х:
            х = родител[х]
        return х

    причини: dict[int, list[dict[str, Any]]] = {и: [] for и, _ in с_подпис}
    for позиция, (и, а) in enumerate(с_подпис):
        for б_и, б in с_подпис[позиция + 1:]:
            # Вече в една група: свързването е преходно, втора причина не мени
            # групите. Мерено: при 500 еднакви наблюдения това спестява 2,1 s.
            if корен(и) == корен(б_и):
                continue
            if not (отрязани[и] or отрязани[б_и]):
                независими, _, _ = independent_sets(множества[и], множества[б_и])
                if независими:
                    continue
            # Присъдата с числата и причината се строи само за двойките, които НЕ
            # са независими — те са малкото, а редът влиза в отчета.
            присъда = independence(а.get("content_signature"),
                                   б.get("content_signature"))
            причини[и].append({"с": str(б.get("drawing") or ""), **присъда})
            причини[б_и].append({"с": str(а.get("drawing") or ""), **присъда})
            ка, кб = корен(и), корен(б_и)
            if ка != кб:
                родител[кб] = ка

    имена: dict[int, str] = {}
    изход: list[dict[str, Any]] = []
    for и, н in enumerate(записи):
        нов = dict(н)
        if и not in родител:
            нов["independence_group"] = ГРУПА_НЕДОКАЗАНА
            нов["independence_note"] = ("няма подпис по съдържание — записът не се "
                                        "брои за отделно свидетелство")
            изход.append(нов)
            continue
        к = корен(и)
        if к not in имена:
            имена[к] = "НЕЗ-%02d" % (len(имена) + 1)
        нов["independence_group"] = имена[к]
        свързани = причини.get(и) or []
        нов["independence_note"] = (
            "" if not свързани else
            "същият източник като %s (%s)" % (
                ", ".join(sorted({str(р["с"]) for р in свързани})[:4]),
                свързани[0]["reason"])
        )
        изход.append(нов)
    return изход


def independence_summary(записи: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Колко НЕЗАВИСИМИ източника има зад наблюденията — и кой с кого е слепен."""

    редове = list(записи)
    групи: dict[str, list[str]] = {}
    for н in редове:
        група = str(н.get("independence_group") or ГРУПА_НЕДОКАЗАНА)
        групи.setdefault(група, []).append(str(н.get("drawing") or ""))
    доказани = {к: v for к, v in групи.items() if к != ГРУПА_НЕДОКАЗАНА}
    return {
        "observations": len(редове),
        "independent_sources": len(доказани),
        "groups": {к: sorted(v) for к, v in sorted(групи.items())},
        "unproven": sorted(групи.get(ГРУПА_НЕДОКАЗАНА) or ()),
        "merged": {к: sorted(v) for к, v in sorted(доказани.items()) if len(v) > 1},
    }


def record_observation(
    път: Path,
    *,
    drawing: str,
    family: str,
    profile: Mapping[str, Any],
    layer_roles: Mapping[str, Any],
    source_sha256: str = "",
    content_signature: Mapping[str, Any] | None = None,
) -> int:
    """Добавя наблюдение и преизчислява ГРУПИТЕ на независимост.

    Изхвърля по-ранен запис при същото ``family`` (ревизия на същия чертеж) или
    при същия ``source_sha256`` (побайтово копие). Това е НЕОБХОДИМО, но не е
    достатъчно: и двете са имена. Затова всеки запис получава
    ``independence_group`` по съдържанието си, а обучителите броят групи.

    Измереното, което прави това нужно (``PREDI.json``, 30.09.2026): ``d-1`` и
    ``s-10`` са един чертеж с различни sha и различни имена; преди поправката
    двата давали 2 наблюдения и **6 приети правила**. ``s-8`` е поднабор на
    ``s-9`` (вложеност 1,0000) и двата давали 2 наблюдения и **3 приети правила**.
    """

    запис = {
        "recorded_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "drawing": drawing,
        "family": family or drawing,
        "source_sha256": source_sha256,
        "profile": {
            "proposals": list(profile.get("proposals") or ()),
            "refusals": list(profile.get("refusals") or ()),
            "summary": dict(profile.get("summary") or {}),
            # Втората наблюдавана роля: къде проектантът пише размерите на
            # участъците. Пак само профил по съдържание — нула чертожни данни.
            "dimension_style": dict(profile.get("dimension_style") or {}),
        },
        "layer_roles": dict(layer_roles or {}),
        # Подписът по съдържание: отпечатъци на надписите и на диалекта, без нито
        # един надпис, handle или координата (``source_identity``).
        "content_signature": dict(content_signature or {}),
    }
    наличните = load_observations(път)
    # Двете НЕОБХОДИМИ причини да се изхвърли по-ранен запис:
    #   * СЪЩОТО СЕМЕЙСТВО -> това е ревизия на същия чертеж и я заменя;
    #   * СЪЩОТО СЪДЪРЖАНИЕ (sha) под друго име -> побайтово копие.
    # Те не стигат: различен sha и различно име НЕ доказват различен източник.
    # Останалото се решава по съдържание в ``assign_independence_groups``.
    сха = str(запис.get("source_sha256") or "")
    запазени = [
        н for н in наличните
        if str(н.get("family") or "") != запис["family"]
        and not (сха and str(н.get("source_sha256") or "") == сха)
    ]
    запазени.append(запис)
    запазени = запазени[-MAX_OBSERVATIONS:]
    запазени = assign_independence_groups(запазени)
    път.parent.mkdir(parents=True, exist_ok=True)
    временен = път.with_suffix(път.suffix + ".tmp")
    временен.write_text(
        "\n".join(json.dumps(н, ensure_ascii=False) for н in запазени) + "\n",
        encoding="utf-8")
    os.replace(временен, път)
    return len(запазени)


def unrecognised_from(profile: Mapping[str, Any],
                      applied: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Неразпознатите случаи и причините им — за отчета и за следващия цикъл."""

    решени = {(str(р.get("layer") or ""), str(р.get("role") or ""))
              for р in (applied.get("resolved") or ())}
    изход = []
    for отказ in profile.get("refusals") or ():
        ключ = (str(отказ.get("layer") or ""), str(отказ.get("role") or ""))
        if ключ in решени:
            continue
        изход.append({
            "layer": отказ.get("layer"),
            "block_name": отказ.get("block_name") or "",
            "role": отказ.get("role"),
            "profile_reason": str(отказ.get("reason") or ""),
            "counts": dict(отказ.get("counts") or {}),
        })
    for останал in applied.get("still_refused") or ():
        изход.append({
            "layer": останал.get("layer"), "role": останал.get("role"),
            "profile_reason": останал.get("profile_reason"),
            "learned_refusal": останал.get("learned_refusal"),
            "counts": {},
        })
    # един ред за (слой, роля, причина)
    видени = set()
    единствени = []
    for р in изход:
        ключ = (str(р.get("layer")), str(р.get("role")), str(р.get("profile_reason")))
        if ключ in видени:
            continue
        видени.add(ключ)
        единствени.append(р)
    return единствени


def run_cycle(
    *,
    store: KnowledgeStore,
    profile: Mapping[str, Any],
    layer_roles: Mapping[str, Any],
    applied: Mapping[str, Any],
    drawing: str,
    family: str = "",
    source_sha256: str = "",
    locked_families: Iterable[str] = (),
    content_signature: Mapping[str, Any] | None = None,
) -> CycleResult:
    """Изпълнява целия цикъл след анализа на един чертеж.

    ``applied`` е това, което ВЕЧЕ е приложено върху чертежа (от моста). То идва
    от хранилището ОТПРЕДИ този цикъл — затова новото знание не се прилага само
    върху себе си.

    ``content_signature`` е подписът по съдържание на чертежа
    (``source_identity.content_signature``). Без него наблюдението се записва, но
    НЕ се брои за отделно свидетелство — независимостта му не е доказана.
    """

    import time

    т0 = time.time()
    приложени = tuple(
        str(р.get("rule_id") or "") for р in (applied.get("resolved") or ())
    ) + tuple(
        str(р.get("rule_id") or "") for р in (applied.get("authorized") or ())
    )
    неразпознати = tuple(unrecognised_from(profile, applied))

    път = observations_path(store)
    общо = record_observation(
        път, drawing=drawing, family=family or drawing,
        profile=profile, layer_roles=layer_roles, source_sha256=source_sha256,
        content_signature=content_signature,
    )

    заключени = {str(с) for с in locked_families}
    записани = load_observations(път)
    наблюдения = [
        {"drawing": н.get("drawing"), "family": н.get("family"),
         "locked": str(н.get("family") or "") in заключени,
         "profile": н.get("profile") or {},
         "layer_roles": н.get("layer_roles") or {},
         # Двете нови полета: по кой независим източник се брои записът и с какъв
         # диалект. Обучителите ползват тях, не имената.
         "independence_group": н.get("independence_group") or "",
         "content_signature": н.get("content_signature") or {}}
        for н in записани
    ]
    независимост = independence_summary(записани)
    отчет = learn(наблюдения, store=store)
    # Втората роля минава през СЪЩИТЕ наблюдения и същото хранилище, със свои
    # критерии за приемане (``learning/dimension_style.py``).
    from lion_rebar.learning.dimension_style import (
        learn as учи_стил, learn_parallel as учи_успоредност,
    )

    for учител in (учи_стил, учи_успоредност):
        частен = учител(наблюдения, store=store)
        for ключ in ("accepted", "candidates", "rejected"):
            отчет[ключ] = [*отчет.get(ключ, []), *частен.get(ключ, [])]
    store.set_meta(auto_cycle_at=datetime.now().astimezone().isoformat(timespec="seconds"),
                   auto_cycle_drawing=drawing,
                   observations=общо,
                   independence=независимост)
    store.save()

    return CycleResult(
        drawing=drawing, family=family or drawing,
        applied_rules=tuple(и for и in приложени if и),
        unrecognised=неразпознати,
        observations_total=общо,
        independent_sources=int(независимост["independent_sources"]),
        accepted_now=tuple(р["rule_id"] for р in отчет["accepted"]),
        candidates_now=tuple(р["rule_id"] for р in отчет["candidates"]),
        rejected_now=tuple(р["rule_id"] for р in отчет["rejected"]),
        store_path=str(store.path),
        seconds=time.time() - т0,
    )
