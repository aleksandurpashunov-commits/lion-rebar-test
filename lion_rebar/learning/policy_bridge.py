"""Наученото влиза в разпознаването през СЪЩИЯ шев като потвърдения паспорт.

Днес операторът потвърждава паспорта веднъж за чертеж и
``project/convention_profile_decisions.policy_with_confirmed_profile`` слага
обявените слоеве в ``declared_rebar_geometry_layers``. Причината е измерена и
записана в ``dxf/recognition_policy.py``: в два чертежа прътите стоят на слой
``s-Reinf`` — дума, която речникът не познава — и затова **786 от 805 и 299 от
329 позиции остават без нито един кандидат**.

Тук същото се случва БЕЗ операторско потвърждение, когато:

* има ПРИЕТО правило за ``REBAR_GEOMETRY``, доказано по съдържание в поне две
  различни семейства, с признак на име, който пасва на слоя; и
* самият целеви чертеж дава поне два свои примера (надпис намира точната си
  дължина на този слой) с превес над контролата >= 4.0.

Тоест наученото носи само прага за КОЛИЧЕСТВО. Доказателството идва от чертежа
пред нас. При нула или един свой пример — отказ.

Изключването е през ``LION_REBAR_LEARNING``: ``off`` дава политиката точно
както без този модул, за да е сравним режим Б с режим А.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterable, Mapping

from lion_rebar.learning.spec_layer_profile import full_profile
from lion_rebar.session.layer_classifier import classify_layers
from lion_rebar.dxf.recognition_policy import RecognitionPolicy
from lion_rebar.learning.convention_apply import apply_learned, authorized_proposals
from lion_rebar.learning.knowledge_store import KnowledgeStore, store_path
from lion_rebar.learning.source_identity import content_signature, style_signature

#: Стойности на ``LION_REBAR_LEARNING``, които ИЗКЛЮЧВАТ ползването.
ИЗКЛЮЧЕНО = {"0", "off", "false", "no", "изкл"}

#: Коя роля на слой в кое поле на политиката влиза. Точно тези три полета днес
#: съдържат ЛИТЕРАЛНИ имена на слоеве от конкретни офиси:
#:
#:   position_key_circle_layer_aliases = ("0.4",)
#:   position_key_table_layer_aliases  = ("ARMTXT", "ARM_TXT")
#:   declared_rebar_geometry_layers    = ()   # пълни се от оператора
#:
#: Първите две са изброени в кода и работят за офисите, чиито имена са в списъка.
#: Третото иска операторът да потвърди паспорта за всеки чертеж. Наученото пълни
#: и трите от ДОКАЗАТЕЛСТВО, затова важи и за име, което никой не е изброил.
РОЛЯ_В_ПОЛЕ = {
    "REBAR_GEOMETRY": "declared_rebar_geometry_layers",
    "POSITION_KEY": "position_key_circle_layer_aliases",
    "POSITION_LABEL_BLOCK": "position_key_table_layer_aliases",
    "POSITION_KEY_SPEC": "position_key_spec_layer_aliases",
}

#: Полетата, които ``LION_REBAR_HARDCODED_ALIASES=off`` изпразва. Само за
#: експеримента: така се мери колко носи изброеното в кода и може ли наученото
#: да го замени. НЕ е предложение за основната програма.
ИЗБРОЕНИ_В_КОДА = (
    "position_key_circle_layer_aliases",
    "position_key_table_layer_aliases",
    "position_key_spec_layer_aliases",
)


def learning_enabled() -> bool:
    стойност = str(os.environ.get("LION_REBAR_LEARNING") or "").strip().lower()
    if not стойност:
        return True
    return стойност not in ИЗКЛЮЧЕНО


def hardcoded_aliases_enabled() -> bool:
    """Дали изброените в кода имена на слоеве важат (по подразбиране: да)."""

    стойност = str(os.environ.get("LION_REBAR_HARDCODED_ALIASES") or "").strip().lower()
    if not стойност:
        return True
    return стойност not in ИЗКЛЮЧЕНО


def without_hardcoded_aliases(policy: RecognitionPolicy) -> RecognitionPolicy:
    """Политика без изброените в кода имена на слоеве — за режимите на опита."""

    import dataclasses

    празни = {поле: () for поле in ИЗБРОЕНИ_В_КОДА
              if hasattr(policy, поле)}
    return dataclasses.replace(policy, **празни) if празни else policy


def load_store() -> KnowledgeStore:
    """Хранилището от папката с данни — следва ``LION_REBAR_DATA_ROOT``.

    ``LION_REBAR_NAUCHENO`` пренасочва към друг файл. Нужно е за честното
    измерване: при „изключи едно семейство и приложи върху него" всеки целеви
    проект иска свое хранилище, обучено без него. В нормална работа не се задава.
    """

    from lion_rebar.utils.app_paths import AppPaths

    изрично = str(os.environ.get("LION_REBAR_NAUCHENO") or "").strip()
    if изрично:
        път = Path(изрично)
        # Изрично зададеното хранилище значи СРАВНИТЕЛНО ИЗМЕРВАНЕ. Липсващ файл
        # там е грешка в подготовката, не „празна памет": мълчаливото празно
        # хранилище обезсмисля измерването, без да личи. Измерено на етап 4 —
        # цяла таблица „с памет" беше направена с празно хранилище заради едно
        # тире в името на файла.
        разрешено = str(
            os.environ.get("LION_REBAR_NAUCHENO_ALLOW_MISSING") or ""
        ).strip().lower() in {"1", "on", "yes", "true", "да"}
        if not път.is_file() and not разрешено:
            raise FileNotFoundError(
                "LION_REBAR_NAUCHENO сочи към несъществуващо хранилище: %s. "
                "Сравнителното измерване се прекратява, за да не се подмени "
                "тихо с празна памет. За нарочно празна памет задайте "
                "LION_REBAR_NAUCHENO_ALLOW_MISSING=1." % път
            )
        return KnowledgeStore.load(път)
    return KnowledgeStore.load(store_path(AppPaths.default()))


def learned_declarations(
    entities: Iterable[Any],
    *,
    policy: RecognitionPolicy,
    store: KnowledgeStore | None = None,
    drawing: str = "",
    enabled: bool | None = None,
) -> dict[str, Any]:
    """Кои слоеве наученото обявява за армировъчна геометрия — и защо."""

    включено = learning_enabled() if enabled is None else bool(enabled)
    хранилище = store if store is not None else load_store()
    if not включено or not хранилище.accepted():
        return {
            "learning_enabled": включено,
            "accepted_rule_count": len(хранилище.accepted()),
            "declared_layers": (),
            "resolved": [],
            "still_refused": [],
        }
    записи = list(entities)
    профил = full_profile(записи, policy=policy)
    # Ролите по ИМЕ са независимото насрещно мнение: правило „само по съдържание"
    # не се прилага върху слой, за който именният класификатор твърди уверено
    # нещо друго. Ползва се функцията на самата програма, не нова.
    роли_по_име = {
        str(ред.get("layer") or ""): {
            "role": str(ред.get("role") or ""),
            "confidence": float(ред.get("confidence") or 0.0),
        }
        for ред in (classify_layers(записи).get("layers") or ())
        if isinstance(ред, dict)
    }
    # Диалектът на ЦЕЛЕВИЯ чертеж. Нужен е, защото правило със СЕМЕЕН обхват важи
    # само вътре в семейството на донорите си (``learning/scopes.py``). Смята се
    # от самите обекти и не пази нито един надпис.
    целеви_стил = style_signature(content_signature(записи))
    приложено = apply_learned(
        профил, store=хранилище, drawing=drawing, enabled=True,
        layer_roles=роли_по_име, target_style=целеви_стил,
    )
    разрешени = authorized_proposals(
        профил, store=хранилище, enabled=True, layer_roles=роли_по_име,
        target_style=целеви_стил,
    )

    # Две пътеки, нарочно различими в отчета:
    #   РАЗРЕШЕНО  — чертежът сам доказва ролята; наученото дава правото да се
    #                ползва без операторско потвърждение;
    #   ДОРЕШЕНО   — чертежът има доказателство под прага; наученото сваля
    #                само прага за количество.
    по_поле: dict[str, list[str]] = {}
    произход: list[dict[str, Any]] = []
    for източник, редове in (("РАЗРЕШЕНО", разрешени),
                             ("ДОРЕШЕНО", приложено.get("resolved") or [])):
        for ред in редове:
            роля = str(ред.get("role") or "")
            поле = РОЛЯ_В_ПОЛЕ.get(роля)
            слой = str(ред.get("layer") or "")
            if not поле or not слой:
                continue
            по_поле.setdefault(поле, [])
            if слой not in по_поле[поле]:
                по_поле[поле].append(слой)
            произход.append({**ред, "path": източник, "policy_field": поле})

    return {
        "learning_enabled": True,
        "accepted_rule_count": приложено.get("accepted_rule_count"),
        "by_policy_field": {к: tuple(v) for к, v in по_поле.items()},
        "declared_layers": tuple(по_поле.get("declared_rebar_geometry_layers") or ()),
        "authorized": разрешени,
        "resolved": приложено.get("resolved") or [],
        "provenance": произход,
        "still_refused": приложено.get("still_refused") or [],
        "profile_summary": профил.get("summary"),
        "name_classifier_layers": len(роли_по_име),
        # Диалектът на целевия чертеж се връща, за да не се смята втори път и за
        # да е видимо в отчета срещу какво е мерен обхватът.
        "target_style": целеви_стил,
    }


def policy_with_learned_knowledge(
    base_policy: RecognitionPolicy,
    entities: Iterable[Any],
    *,
    store: KnowledgeStore | None = None,
    drawing: str = "",
    enabled: bool | None = None,
) -> tuple[RecognitionPolicy, dict[str, Any]]:
    """Политиката, разширена с наученото. Връща и отчет за проследимост.

    Обявеното се ДОБАВЯ, не замества: речникът на engine-а продължава да важи.
    Без прието правило или без подкрепа в чертежа политиката се връща
    НЕПРОМЕНЕНА — затова режим Б е точно като режим А.
    """

    import dataclasses

    отчет = learned_declarations(
        entities, policy=base_policy, store=store, drawing=drawing, enabled=enabled
    )
    по_поле = отчет.get("by_policy_field") or {}
    промени: dict[str, tuple[str, ...]] = {}
    преди: dict[str, list[str]] = {}
    for поле, слоеве in по_поле.items():
        текущи = tuple(getattr(base_policy, поле, ()) or ())
        обединени = tuple(dict.fromkeys((*текущи, *слоеве)))
        преди[поле] = list(текущи)
        if обединени != текущи:
            промени[поле] = обединени

    # Втората научена роля: размерният слой на проектанта. Тя не обявява нови
    # геометрични слоеве — само стеснява търсенето на отпечатаните размери във
    # верижното правило, и то СЛЕД като неограниченият прочит вече е отказал.
    from lion_rebar.learning.dimension_style import learned_styles

    хранилище = store if store is not None else load_store()
    стилове = learned_styles(хранилище.accepted(),
                             target_style=отчет.get("target_style"))
    if стилове:
        текущи = {т[0]: т for т in
                  (getattr(base_policy, "learned_segment_dimension_styles", ()) or ())}
        нови = {слой: (слой, str(с.get("dimension_layer") or ""),
                       float(с.get("parallel_deg", -1.0)))
                for слой, с in стилове.items()}
        обединени = tuple(sorted({**текущи, **нови}.values()))
        if обединени != tuple(sorted(текущи.values())):
            промени["learned_segment_dimension_styles"] = обединени
        отчет["segment_dimension_styles"] = {к: dict(v) for к, v in стилове.items()}

    отчет["policy_changed"] = bool(промени)
    отчет["policy_before"] = преди
    отчет["policy_after"] = {к: list(v) for к, v in промени.items()}
    if not промени:
        return base_policy, отчет
    return dataclasses.replace(base_policy, **промени), отчет


def store_fingerprint(store: KnowledgeStore) -> Mapping[str, Any]:
    """Кратък отпечатък на хранилището за манифеста на прогона."""

    път = Path(store.path)
    return {
        "path": str(път),
        "exists": път.is_file(),
        "bytes": път.stat().st_size if път.is_file() else 0,
        "rule_count": len(store),
        "accepted": [правило.rule_id for правило in store.accepted()],
        "summary": store.summary(),
    }
