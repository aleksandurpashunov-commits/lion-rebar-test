"""P0-3 — Review Center current-project recovery.

Проблем: при активен проект с валиден последен пакетен анализ бутонът
``Review Center`` показваше файлов диалог за произволен batch JSON.
Ръчният избор на вътрешен JSON не е нормален операторски workflow.

Модулът е чист (без Qt) и решава САМО какво трябва да се случи. UI слоят
изпълнява решението. Така поведението е тестваемо без Windows среда.

Правило:
  * има валиден последен Review за текущите project_id / source_generation_id
    / analysis_run_id  ->  ``OPEN_LATEST``;
  * няма валиден  ->  ``ASK_OPERATOR`` с изрични опции
    (стартирай анализ / избери исторически / откажи);
  * няма проект или няма импортиран DXF  ->  ``BLOCKED``.

Историческият избор остава възможен, но е СЪЗНАТЕЛНО действие на
оператора, а не подразбиращ се път.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from lion_rebar.project.data_identity import DataIdentity, mismatch_reason

ACTION_OPEN_LATEST = "OPEN_LATEST"
ACTION_ASK_OPERATOR = "ASK_OPERATOR"
ACTION_BLOCKED = "BLOCKED"

OPTION_RUN_ANALYSIS = "RUN_ANALYSIS"
OPTION_CHOOSE_HISTORICAL = "CHOOSE_HISTORICAL"
OPTION_CANCEL = "CANCEL"


@dataclass(frozen=True)
class ReviewRecoveryDecision:
    action: str
    report_path: Path | None = None
    reason: str = ""
    message: str = ""
    options: tuple[str, ...] = field(default_factory=tuple)

    @property
    def opens_directly(self) -> bool:
        return self.action == ACTION_OPEN_LATEST


def evaluate_report_candidate(
    payload: Mapping[str, Any] | None,
    current: DataIdentity,
    *,
    project_name: str = "",
) -> str:
    """Връща причина за отхвърляне; празен низ = редът е валиден за проекта."""

    if payload is None:
        return "REPORT_UNREADABLE"
    report_project = str(payload.get("project_name") or "").strip()
    if project_name and report_project and report_project != project_name:
        return "PROJECT_NAME_MISMATCH"
    return mismatch_reason(payload, current, require_run=True)


def decide_review_entry(
    *,
    has_project: bool,
    has_import: bool,
    current: DataIdentity | None,
    candidates: Sequence[tuple[Path, Mapping[str, Any] | None]],
    project_name: str = "",
) -> ReviewRecoveryDecision:
    """Решава как да се отвори Review Center.

    ``candidates`` са (път, зареден payload), подредени от най-нов към
    най-стар. Payload=None означава нечетим отчет.
    """

    if not has_project:
        return ReviewRecoveryDecision(
            action=ACTION_BLOCKED, reason="NO_PROJECT",
            message="Първо отвори проект.")
    if not has_import:
        return ReviewRecoveryDecision(
            action=ACTION_BLOCKED, reason="NO_IMPORT",
            message="Първо импортирай DXF в проекта.")
    if current is None:
        return ReviewRecoveryDecision(
            action=ACTION_BLOCKED, reason="NO_DATA_IDENTITY",
            message="Проектът няма валидна data identity.")

    rejections: list[str] = []
    for path, payload in candidates:
        reason = evaluate_report_candidate(payload, current, project_name=project_name)
        if not reason:
            return ReviewRecoveryDecision(
                action=ACTION_OPEN_LATEST, report_path=Path(path),
                reason="CURRENT_RUN_MATCH",
                message="Отваря се последният валиден анализ на текущия проект.")
        rejections.append(f"{Path(path).name}: {reason}")

    if not candidates:
        reason = "NO_REVIEW_FOR_CURRENT_RUN"
        message = ("За текущия анализ няма пакетен Review отчет.")
    else:
        reason = "ONLY_STALE_REVIEWS"
        message = (
            "Намерените отчети не принадлежат на текущия анализ:\n  - "
            + "\n  - ".join(rejections[:5]))
    return ReviewRecoveryDecision(
        action=ACTION_ASK_OPERATOR, reason=reason, message=message,
        options=(OPTION_RUN_ANALYSIS, OPTION_CHOOSE_HISTORICAL, OPTION_CANCEL))
