"""Една обща логика за обучението — ползва се и от прозореца, и от ``full_path``.

Етап 3 закачи цикъла само в безинтерфейсния път. Тук същото се изнася на едно
място, за да няма две различни поведения:

``prepare_policy``          преди анализа: потвърденият профил + наученото;
``run_cycle_after_analysis`` след анализа: наблюдение, кандидати, приемане.

Разделението е нарочно. Прилагането става с паметта ОТПРЕДИ прогона, а ученето —
след него, за да не може чертежът сам да си издаде разрешение.

Модулът НЕ внася Qt: прозорецът сам решава в коя нишка да вика втората функция,
за да не блокира интерфейса.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from lion_rebar.dxf.recognition_policy import RecognitionPolicy


@dataclass
class LearningStatus:
    """Малкото видимо състояние, което интерфейсът показва."""

    enabled: bool = True
    store_path: str = ""
    rules_total: int = 0
    rules_accepted: int = 0
    applied: list[dict[str, Any]] = field(default_factory=list)
    policy_changed: bool = False
    declared_layers: list[str] = field(default_factory=list)
    #: Слоеве, НАМЕРЕНИ от геометричния различител — отделно от обявените от
    #: човек, за да е ясен произходът на решението.
    discovered_layers: list[str] = field(default_factory=list)
    unresolved: int = 0
    new_candidates: list[str] = field(default_factory=list)
    newly_accepted: list[str] = field(default_factory=list)
    observations: int = 0
    #: Колко от наблюденията са ДОКАЗАНО различни източника. „Наблюдения" и
    #: „независими източника" са различни числа и се отчитат отделно.
    independent_sources: int = 0
    error: str = ""

    def short_line(self) -> str:
        """Един ред за лентата на състоянието."""

        if not self.enabled:
            return "Обучение: изключено"
        if self.error:
            return "Обучение: ГРЕШКА — %s" % self.error[:90]
        части = ["Обучение: %d правила (%d приети)" % (self.rules_total,
                                                       self.rules_accepted)]
        if self.observations:
            части.append("наблюдения %d / независими източника %d"
                         % (self.observations, self.independent_sources))
        if self.applied:
            части.append("приложени %d" % len(self.applied))
        if self.declared_layers:
            части.append("обявени слоеве: %s" % ", ".join(self.declared_layers[:3]))
        if self.discovered_layers:
            части.append("намерени слоеве: %s"
                         % ", ".join(self.discovered_layers[:3]))
        if self.newly_accepted:
            части.append("НОВО приети %d" % len(self.newly_accepted))
        if self.new_candidates:
            части.append("нови кандидати %d" % len(self.new_candidates))
        if self.unresolved:
            части.append("неразрешени %d" % self.unresolved)
        return " | ".join(части)

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled, "store_path": self.store_path,
            "rules_total": self.rules_total, "rules_accepted": self.rules_accepted,
            "applied": list(self.applied), "policy_changed": self.policy_changed,
            "declared_layers": list(self.declared_layers),
            "discovered_layers": list(self.discovered_layers),
            "unresolved": self.unresolved,
            "new_candidates": list(self.new_candidates),
            "newly_accepted": list(self.newly_accepted),
            "observations": self.observations,
            "independent_sources": self.independent_sources,
            "error": self.error,
        }


def learning_is_on() -> bool:
    from lion_rebar.learning.policy_bridge import learning_enabled

    return learning_enabled()


def _намери_armirovachni_sloeve(policy: RecognitionPolicy,
                                entities: Sequence[Any]) -> tuple[RecognitionPolicy,
                                                                  list[str]]:
    """Отваря слой, който речникът пропуска, но различителят доказва.

    Речникът от корени (``ARM``, ``REBAR``, ``BAR``, ``STEEL``, ``АРМ`` ...) не
    познава ``s-Reinf`` и в `Тест-002` 299 от 329 позиции остават без нито един
    кандидат. Добавянето на корен ``REINF`` обаче би отворило и слоевете
    ``…Surface reinforcement…`` на `d-2`, а точно това е ИЗМЕРЕНО като вредно
    (порта 124 -> 105). Затова решението е по измерими признаци, не по име.

    Разширеното търсене е ОТДЕЛЕНО от приемането: тук слоят само става видим за
    търсенето на кандидати. Дали позицията се приема минава през целия обичаен
    път — форма, matcher, порта — непроменен.

    Изключва се с ``LION_REBAR_GEOMETRY_DISCOVERY=off``.
    """

    import dataclasses

    if str(os.environ.get("LION_REBAR_GEOMETRY_DISCOVERY") or "").strip().lower() in {
        "0", "off", "false", "no"
    }:
        return policy, []
    try:
        from lion_rebar.dxf.geometry_candidates import _rebar_geometry_layer_role
        from lion_rebar.dxf.rebar_geometry_evidence import (
            VERDICT_REBAR, evaluate_rebar_geometry,
        )

        доказателство = evaluate_rebar_geometry(list(entities), policy=policy)
        намерени = sorted(
            слой for слой, ред in (доказателство.layers or {}).items()
            # Само слой, който речникът НЕ познава: намирането допълва, не
            # заменя вече работещото разпознаване.
            if _rebar_geometry_layer_role(слой, policy) is None
            and str(getattr(ред, "verdict", "")) == VERDICT_REBAR
        )
        if not намерени:
            return policy, []
        вече = tuple(getattr(policy, "discovered_rebar_geometry_layers", ()) or ())
        policy = dataclasses.replace(
            policy,
            discovered_rebar_geometry_layers=tuple(dict.fromkeys(вече + tuple(намерени))),
        )
        return policy, намерени
    except Exception:  # noqa: BLE001 — намирането не вали анализа
        return policy, []


def prepare_policy(
    settings: Any,
    project_root: Path | str,
    *,
    identity: Any,
    entities: Sequence[Any],
    drawing: str,
) -> tuple[RecognitionPolicy, LearningStatus, dict[str, Any]]:
    """Политиката за анализа и какво е приложило наученото.

    Връща и суровия отчет на моста, за да може цикълът след анализа да ползва
    същото изчисление, без да го прави втори път.
    """

    from lion_rebar.project.convention_profile_decisions import (
        policy_with_confirmed_profile,
    )
    from lion_rebar.learning.policy_bridge import (
        hardcoded_aliases_enabled, load_store, policy_with_learned_knowledge,
        without_hardcoded_aliases,
    )

    policy = policy_with_confirmed_profile(settings, project_root, identity=identity)
    if not hardcoded_aliases_enabled():
        policy = without_hardcoded_aliases(policy)

    # Геометричният различител НЕ е памет: той мери ТОЗИ чертеж. Затова работи
    # и когато наученото е изключено (режим Б) — това е подобрение в
    # разпознаването, не приложено знание от други чертежи.
    policy, намерени = _намери_armirovachni_sloeve(policy, entities)

    състояние = LearningStatus(enabled=learning_is_on())
    състояние.discovered_layers = list(намерени)
    if not състояние.enabled:
        return policy, състояние, {}

    try:
        store = load_store()
        състояние.store_path = str(store.path)
        състояние.rules_total = len(store)
        състояние.rules_accepted = len(store.accepted())
        policy, мост = policy_with_learned_knowledge(
            policy, entities, store=store, drawing=drawing)
        състояние.policy_changed = bool(мост.get("policy_changed"))
        състояние.declared_layers = list(
            (мост.get("by_policy_field") or {}).get(
                "declared_rebar_geometry_layers") or ())
        състояние.applied = [
            {"rule_id": р.get("rule_id"), "role": р.get("role"),
             "layer": р.get("layer"), "path": р.get("path"),
             "learned_from": р.get("learned_from")}
            for р in (мост.get("provenance") or ())
        ]
        състояние.unresolved = len(мост.get("still_refused") or ())
        return policy, състояние, мост
    except Exception as грешка:  # noqa: BLE001 — обучението не вали анализа
        състояние.error = "%s: %s" % (type(грешка).__name__, грешка)
        return policy, състояние, {}


def run_cycle_after_analysis(
    *,
    status: LearningStatus,
    bridge_report: Mapping[str, Any],
    entities: Sequence[Any],
    drawing: str,
    family: str = "",
    source_sha256: str = "",
    locked_families: Iterable[str] = (),
) -> LearningStatus:
    """Наблюдение, извличане, проверка и приемане — СЛЕД анализа.

    Безопасна е да се вика в отделна нишка: не пипа Qt и не чете глобално
    състояние освен папката с данни.
    """

    if not status.enabled:
        return status
    if str(os.environ.get("LION_REBAR_AUTOCYCLE") or "").strip().lower() in {
        "0", "off", "false", "no"
    }:
        return status
    try:
        from lion_rebar.learning.auto_cycle import run_cycle
        from lion_rebar.learning.policy_bridge import load_store
        from lion_rebar.learning.source_identity import content_signature
        from lion_rebar.learning.spec_layer_profile import full_profile
        from lion_rebar.session.layer_classifier import classify_layers

        from lion_rebar.learning import dimension_style

        записи = list(entities)
        профил = full_profile(записи)
        # Наблюдението за размерния слой се мери от САМИТЕ обекти, не от
        # решенията на анализа: паметта не се учи от собствените си резултати.
        профил = {**профил, "dimension_style": dimension_style.observe(записи)}
        роли = {
            str(ред.get("layer") or ""): {
                "role": str(ред.get("role") or ""),
                "confidence": float(ред.get("confidence") or 0.0)}
            for ред in (classify_layers(записи).get("layers") or ())
            if isinstance(ред, dict)
        }
        приложено = {
            "resolved": (bridge_report or {}).get("resolved") or [],
            "authorized": (bridge_report or {}).get("authorized") or [],
            "still_refused": (bridge_report or {}).get("still_refused") or [],
        }
        store = load_store()
        преди = {п.rule_id for п in store.accepted()}
        резултат = run_cycle(
            store=store, profile=профил, layer_roles=роли, applied=приложено,
            drawing=drawing, family=family or drawing,
            source_sha256=source_sha256, locked_families=locked_families,
            # Подписът по съдържание е ЗАДЪЛЖИТЕЛЕН тук: без него наблюдението не
            # се брои за независимо свидетелство. Смята се от същите обекти, от
            # които се смята и профилът — нищо ново не се чете.
            content_signature=content_signature(записи),
        )
        # „Новоприети" значи ПРЕМИНАЛИ В прието състояние в ТОЗИ прогон, а не
        # всички вече приети. Етап 3 ги отчиташе погрешно като всички приети.
        сега = {п.rule_id for п in store.accepted()}
        status.newly_accepted = sorted(сега - преди)
        status.new_candidates = sorted(резултат.candidates_now)
        status.observations = резултат.observations_total
        status.independent_sources = резултат.independent_sources
        status.rules_total = len(store)
        status.rules_accepted = len(сега)
        status.unresolved = max(status.unresolved, len(резултат.unrecognised))
        return status
    except Exception as грешка:  # noqa: BLE001
        status.error = "цикъл: %s: %s" % (type(грешка).__name__, грешка)
        return status


def rollback_memory(to_backup: Path | str) -> Path:
    """Връща паметта към по-ранно копие. Текущото се запазва до него."""

    import shutil
    from datetime import datetime

    from lion_rebar.learning.policy_bridge import load_store

    текущо = Path(load_store().path)
    източник = Path(to_backup)
    if not източник.is_file():
        raise FileNotFoundError("няма такова копие: %s" % източник)
    if текущо.is_file():
        печат = datetime.now().strftime("%Y%m%d_%H%M%S")
        shutil.copy2(текущо, текущо.with_name("%s.before_rollback_%s.json"
                                              % (текущо.stem, печат)))
    shutil.copy2(източник, текущо)
    return текущо
