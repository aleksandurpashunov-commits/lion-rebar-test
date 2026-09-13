"""Отчет след проект: какво е научила програмата и къде се е отказала.

ЗАЩО НЕ Е "САМООБУЧЕНИЕ"
------------------------
Отчетът СЪБИРА доказателства; той не променя нито едно правило. Решението
кое наблюдение да стане правило е инженерно и се взима от човек.

Разликата има значение. Безопасно е системата да запомни, че при даден
проектант куката е 10 cm, а добавката за огъване 5 -- това са конвенции с
проверка: уравнението или се затваря, или не. Опасно е да запомни, че
"позиция с L=135 обикновено е 35x20" -- това е статистика върху решения, а
решенията понякога са грешни. Едно сгрешено решение би станало правило,
правилото би произвело още грешки, и никой не би забелязал, защото
системата вече е "уверена".

Проектът стои на принципа „отказва вместо да гадае“. Този модул го пази.
"""
from __future__ import annotations

import collections
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

REPORT_NAME = "PROJECT_LEARNING_REPORT.json"
SCHEMA = "lion-rebar-learning-report/v1"


def _text(value: Any) -> str:
    return " ".join(str(value if value is not None else "").split()).strip()


@dataclass
class ConventionObservation:
    """Наблюдавана конвенция на проектанта, с доказателството зад нея."""

    name: str
    value: Any
    occurrences: int
    total: int
    evidence: str = ""

    @property
    def consistency(self) -> float:
        return round(self.occurrences / self.total, 3) if self.total else 0.0

    @property
    def is_stable(self) -> bool:
        """Стабилна = среща се в поне 80% от случаите и поне 5 пъти."""

        return self.occurrences >= 5 and self.consistency >= 0.8

    def to_dict(self) -> dict:
        return {
            "name": self.name, "value": self.value,
            "occurrences": self.occurrences, "total": self.total,
            "consistency": self.consistency, "stable": self.is_stable,
            "evidence": self.evidence,
        }


@dataclass
class LearningReport:
    project_name: str
    conventions: list[ConventionObservation] = field(default_factory=list)
    refusals: dict[str, int] = field(default_factory=dict)
    operator_decisions: dict[str, int] = field(default_factory=dict)
    disagreements: list[dict] = field(default_factory=list)
    totals: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "schema": SCHEMA,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "project_name": self.project_name,
            "totals": dict(self.totals),
            "conventions": [item.to_dict() for item in self.conventions],
            "refusals": dict(self.refusals),
            "operator_decisions": dict(self.operator_decisions),
            "disagreements": list(self.disagreements),
            "note": (
                "Този отчет е доказателствен. Нито едно правило не се променя "
                "автоматично. Решението кое наблюдение да стане правило се "
                "взима от инженер."
            ),
        }


def _observe_hooks(rows: Sequence[Mapping[str, Any]]) -> ConventionObservation | None:
    hooks = []
    for row in rows:
        segments = row.get("production_segments_cm") or []
        if len(segments) == 6:
            hooks.append(float(segments[0]))
        elif len(segments) == 3 and _text(row.get("master_shape_code")) == "F006":
            hooks.append(float(segments[2]))
    if not hooks:
        return None
    counter = collections.Counter(hooks)
    value, occurrences = counter.most_common(1)[0]
    return ConventionObservation(
        name="Дължина на куката при стремена",
        value=value, occurrences=occurrences, total=len(hooks),
        evidence="Изведено от производствените сегменти на разпознатите стремета.")


def _observe_bend_allowance(rows: Sequence[Mapping[str, Any]]) -> ConventionObservation | None:
    gaps = []
    for row in rows:
        if _text(row.get("master_shape_code")) != "F006":
            continue
        segments = row.get("production_segments_cm") or []
        try:
            declared = float(row.get("declared_length_cm"))
        except (TypeError, ValueError):
            continue
        if len(segments) == 3:
            width, height, hook = (float(value) for value in segments)
        elif len(segments) == 6:
            hook, height, width = (float(segments[0]), float(segments[1]),
                                   float(segments[2]))
        else:
            continue
        gaps.append(round(declared - (2 * (width + height) + 2 * hook), 1))
    if not gaps:
        return None
    counter = collections.Counter(gaps)
    value, occurrences = counter.most_common(1)[0]
    return ConventionObservation(
        name="Добавка за огъване (обща дължина минус сбор на страните)",
        value=value, occurrences=occurrences, total=len(gaps),
        evidence="Разликата е отговорност на проектанта; тук само се измерва.")


def _observe_annotation_source(rows: Sequence[Mapping[str, Any]]) -> ConventionObservation | None:
    if not rows:
        return None
    from_blocks = sum(
        1 for row in rows
        if "::BJ-FI-L" in _text(row.get("annotation_handle")))
    return ConventionObservation(
        name="Източник на надписите",
        value="БЛОКОВЕ С АТРИБУТИ" if from_blocks * 2 > len(rows) else "СВОБОДЕН ТЕКСТ",
        occurrences=max(from_blocks, len(rows) - from_blocks), total=len(rows),
        evidence="Определя кой път на разпознаване е водещ за този проектант.")


def build_learning_report(
    project_name: str,
    rows: Sequence[Mapping[str, Any]],
    *,
    blockers: Iterable[Mapping[str, Any]] = (),
    decisions: Iterable[Mapping[str, Any]] = (),
) -> LearningReport:
    """Сглобява отчета от вече наличните следи в проекта."""

    report = LearningReport(project_name=_text(project_name))

    for observer in (_observe_annotation_source, _observe_hooks,
                     _observe_bend_allowance):
        observation = observer(rows)
        if observation is not None:
            report.conventions.append(observation)

    refusals: collections.Counter = collections.Counter()
    for blocker in blockers:
        for code in (blocker.get("reason_codes") or []):
            refusals[_text(code)] += 1
    report.refusals = dict(refusals.most_common())

    kinds: collections.Counter = collections.Counter()
    disagreements: list[dict] = []
    for decision in decisions:
        payload = decision.get("payload") if isinstance(decision, Mapping) else {}
        payload = payload if isinstance(payload, Mapping) else {}
        kinds[_text(payload.get("action_class") or decision.get("event") or "?")] += 1
        proposed = _text(payload.get("rejected_master_shape_code"))
        chosen = _text(payload.get("shape_code"))
        if proposed and chosen and proposed != chosen:
            disagreements.append({
                "annotation_handle": _text(payload.get("annotation_handle")),
                "proposed": proposed, "chosen": chosen,
                "note": _text(payload.get("note"))[:120],
            })
    report.operator_decisions = dict(kinds.most_common())
    report.disagreements = disagreements

    strong = sum(
        1 for row in rows
        if _text(row.get("batch_status")) in (
            "СИЛЕН", "СИЛЕН ПО ГЕОМЕТРИЯ", "СИЛНО СТРЕМЕ F006"))
    report.totals = {
        "positions": len(rows),
        "strong": strong,
        "blocked": sum(1 for _ in report.refusals) and sum(report.refusals.values()),
        "operator_decisions": sum(kinds.values()),
        "disagreements": len(disagreements),
    }
    return report


def write_learning_report(project_root: Path | str, report: LearningReport) -> Path:
    directory = Path(project_root) / "working"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / REPORT_NAME
    temporary = target.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8")
    temporary.replace(target)
    return target


def stable_conventions(report: LearningReport) -> list[ConventionObservation]:
    """Наблюденията, узрели за инженерно решение — но НЕ приложени."""

    return [item for item in report.conventions if item.is_stable]
