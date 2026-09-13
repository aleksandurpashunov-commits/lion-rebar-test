from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

ApprovalOrigin = Literal["NONE", "AUTO", "MANUAL", "UNKNOWN"]

AUTO_APPROVED_BY = "AUTO_BATCH_GATE"


def approval_origin(link: Mapping[str, Any] | None) -> ApprovalOrigin:
    """Classify a persisted geometry approval without guessing.

    AUTO wins over every other marker because an automatic conclusion must
    never acquire the privileges of an owner decision.  Only explicit USER or
    OWNER markers (or the legacy Bulgarian manual status) are manual.  Unknown
    records are intentionally recomputed and do not bypass the Matcher.
    """

    if not link:
        return "NONE"

    approved_by = str(link.get("approved_by") or "").strip().upper()
    approval_status = str(link.get("approval_status") or "").strip().upper()

    if (
        approved_by == AUTO_APPROVED_BY
        or "ОДОБРЕНО АВТОМАТИЧНО" in approval_status
        or bool(link.get("auto_gate"))
    ):
        return "AUTO"

    if (
        approved_by == "USER"
        or approved_by.startswith("USER_")
        or approved_by.startswith("OWNER_")
        or "ОДОБРЕНО РЪЧНО" in approval_status
    ):
        return "MANUAL"

    return "UNKNOWN"


def is_auto_approval(link: Mapping[str, Any] | None) -> bool:
    return approval_origin(link) == "AUTO"


def is_manual_approval(link: Mapping[str, Any] | None) -> bool:
    return approval_origin(link) == "MANUAL"
