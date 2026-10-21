"""Постоянно хранилище за научени правила за прочит.

Всяко правило носи всичко, което го прави проверимо: откъде е дошло, върху
какво е доказано, кога отказва и какво е заменило. Записът е един JSON файл в
папката с данни (`AppPaths.learned_knowledge`), тоест следва
``LION_REBAR_DATA_ROOT`` и НЕ пише в програмата.

Защо отделен файл, а не в проекта: правилото за прочит важи за СТИЛ на чертане,
не за конкретен проект. Решенията остават в проекта — те не се наследяват
(data identity gate).

Състояния:

``CANDIDATE``   — доказано в ЕДИН независим източник; НЕ се прилага;
``ACCEPTED``    — минало критериите; прилага се, ако целевият чертеж го подкрепи
                  И е в обхвата на правилото;
``REJECTED``    — има контрапример; пази се, за да не се преоткрива;
``WITHDRAWN``   — заменено от по-ново правило (``supersedes``).

Състоянието НЕ е достатъчно: всяко правило носи и ОБХВАТ в ``scope``
(``learning_scope``, ``learning/scopes.py``) — решение за позиция, проектна
конвенция, семейна конвенция или общо правило. Приемането казва „доказано е",
обхватът казва „ДОКЪДЕ важи". Прилагането минава и през двете.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

KNOWLEDGE_SCHEMA = "lion-rebar-learned-knowledge/v1"
STORE_NAME = "naucheno.json"

CANDIDATE = "CANDIDATE"
ACCEPTED = "ACCEPTED"
REJECTED = "REJECTED"
WITHDRAWN = "WITHDRAWN"
СЪСТОЯНИЯ = (CANDIDATE, ACCEPTED, REJECTED, WITHDRAWN)


@dataclass(frozen=True)
class LearnedRule:
    """Едно научено правило за прочит.

    ``signature`` е самоличността: тя НЕ съдържа променливи полета на анализа
    (``batch_status``, ``problem_codes``, ``candidate_shape_code``). Съдържа само
    вида доказателство и, ако е бил еднакъв при всички примери, нормализиран
    признак на името на слоя — който СТЕСНЯВА правилото, никога не го разширява.
    """

    rule_id: str
    version: int
    role: str
    signature: dict[str, Any]
    state: str
    origin: str
    evidence: list[dict[str, Any]] = field(default_factory=list)
    positives: list[dict[str, Any]] = field(default_factory=list)
    counterexamples: list[dict[str, Any]] = field(default_factory=list)
    scope: dict[str, Any] = field(default_factory=dict)
    refusal_conditions: list[str] = field(default_factory=list)
    validation: dict[str, Any] = field(default_factory=dict)
    supersedes: str | None = None
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LearnedRule":
        известни = {поле for поле in cls.__dataclass_fields__}  # noqa: SLF001
        return cls(**{к: v for к, v in data.items() if к in известни})

    @property
    def families(self) -> tuple[str, ...]:
        """Различните ИМЕНА на семейства в доказателствата.

        ВНИМАНИЕ: това са имена, подадени от викащия (на практика — имената на
        чертежите). Броят им НЕ е брой независими свидетелства; за него виж
        ``independent_sources``. Двете се разминават точно в случая ``d-1`` /
        ``s-10``: две имена, един чертеж.
        """

        return tuple(sorted({
            str(п.get("family") or "") for п in self.positives
            if str(п.get("family") or "")
        }))

    @property
    def independent_sources(self) -> int:
        """Колко ДОКАЗАНО различни източника стоят зад правилото.

        Броят се групите на независимост, изчислени по съдържание
        (``auto_cycle.assign_independence_groups``). Липсва ли записана група,
        връща 0 — непроверимото не се брои.
        """

        групи = {str(п.get("independence_group") or "") for п in self.positives}
        групи.discard("")
        if групи:
            return len(групи)
        try:
            return int((self.scope or {}).get("independent_sources") or 0)
        except (TypeError, ValueError):
            return 0

    @property
    def learning_scope(self) -> str:
        """Докъде важи правилото — стъпалото от ``learning/scopes.py``."""

        from lion_rebar.learning.scopes import rule_scope

        return rule_scope(self)


class KnowledgeStore:
    """Чете и записва научените правила. Записът е атомарен."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._rules: dict[str, LearnedRule] = {}
        self._meta: dict[str, Any] = {}

    # --- четене и запис -------------------------------------------------- #

    @classmethod
    def load(cls, path: Path | str) -> "KnowledgeStore":
        store = cls(path)
        файл = store.path / STORE_NAME if store.path.is_dir() else store.path
        store.path = файл
        if not файл.is_file():
            return store
        данни = json.loads(файл.read_text(encoding="utf-8-sig"))
        if str(данни.get("schema") or "") != KNOWLEDGE_SCHEMA:
            raise ValueError(
                "непозната схема на хранилището: %r" % данни.get("schema")
            )
        store._meta = dict(данни.get("meta") or {})
        for ред in данни.get("rules") or ():
            if not isinstance(ред, dict):
                continue
            правило = LearnedRule.from_dict(ред)
            store._rules[правило.rule_id] = правило
        return store

    def save(self) -> Path:
        """Записва наведнъж през временен файл, за да няма половин хранилище."""

        self.path.parent.mkdir(parents=True, exist_ok=True)
        данни = {
            "schema": KNOWLEDGE_SCHEMA,
            "written_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "meta": dict(self._meta),
            "rules": [
                правило.to_dict()
                for правило in sorted(self._rules.values(),
                                      key=lambda п: (п.role, п.rule_id))
            ],
        }
        временен = self.path.with_suffix(self.path.suffix + ".tmp")
        временен.write_text(
            json.dumps(данни, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        os.replace(временен, self.path)
        return self.path

    # --- достъп ---------------------------------------------------------- #

    def __len__(self) -> int:
        return len(self._rules)

    @property
    def meta(self) -> dict[str, Any]:
        return self._meta

    def set_meta(self, **стойности: Any) -> None:
        self._meta.update(стойности)

    def all_rules(self) -> tuple[LearnedRule, ...]:
        return tuple(sorted(self._rules.values(), key=lambda п: (п.role, п.rule_id)))

    def accepted(self) -> tuple[LearnedRule, ...]:
        return tuple(п for п in self.all_rules() if п.state == ACCEPTED)

    def by_state(self, state: str) -> tuple[LearnedRule, ...]:
        return tuple(п for п in self.all_rules() if п.state == state)

    def get(self, rule_id: str) -> LearnedRule | None:
        return self._rules.get(rule_id)

    def put(self, правило: LearnedRule) -> LearnedRule:
        """Добавя или обновява правило, като вдига версията при промяна."""

        if правило.state not in СЪСТОЯНИЯ:
            raise ValueError("непознато състояние: %r" % правило.state)
        сега = datetime.now().astimezone().isoformat(timespec="seconds")
        старо = self._rules.get(правило.rule_id)
        if старо is None:
            ново = LearnedRule(
                **{**правило.to_dict(),
                   "version": max(1, int(правило.version or 1)),
                   "created_at": правило.created_at or сега,
                   "updated_at": сега}
            )
        else:
            промяна = (старо.state != правило.state
                       or старо.signature != правило.signature
                       or старо.positives != правило.positives
                       or старо.counterexamples != правило.counterexamples)
            ново = LearnedRule(
                **{**правило.to_dict(),
                   "version": старо.version + 1 if промяна else старо.version,
                   "created_at": старо.created_at or сега,
                   "updated_at": сега}
            )
        self._rules[ново.rule_id] = ново
        return ново

    def withdraw(self, rule_id: str, *, replaced_by: str) -> LearnedRule | None:
        """Оттегля правило, защото е заменено — не го изтрива."""

        старо = self._rules.get(rule_id)
        if старо is None:
            return None
        оттеглено = LearnedRule(
            **{**старо.to_dict(),
               "state": WITHDRAWN,
               "version": старо.version + 1,
               "validation": {**старо.validation, "replaced_by": replaced_by},
               "updated_at": datetime.now().astimezone().isoformat(timespec="seconds")}
        )
        self._rules[rule_id] = оттеглено
        return оттеглено

    def summary(self) -> dict[str, Any]:
        по_състояние: dict[str, int] = {}
        по_роля: dict[str, int] = {}
        for правило in self._rules.values():
            по_състояние[правило.state] = по_състояние.get(правило.state, 0) + 1
            if правило.state == ACCEPTED:
                по_роля[правило.role] = по_роля.get(правило.role, 0) + 1
        return {
            "rule_count": len(self._rules),
            "by_state": dict(sorted(по_състояние.items())),
            "accepted_by_role": dict(sorted(по_роля.items())),
            "path": str(self.path),
        }


def store_path(paths: Any) -> Path:
    """Пътят на хранилището от ``AppPaths`` — с падане назад за стар обект."""

    директория = getattr(paths, "learned_knowledge", None)
    if директория is None:
        директория = Path(getattr(paths, "root")) / "naucheno"
    return Path(директория) / STORE_NAME


def rules_for_role(правила: Iterable[LearnedRule], role: str) -> tuple[LearnedRule, ...]:
    return tuple(п for п in правила if п.role == role and п.state == ACCEPTED)
