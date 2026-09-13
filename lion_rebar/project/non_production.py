from __future__ import annotations

"""Shared proof contract for terminal non-production decisions."""

from collections.abc import Mapping


def is_valid_non_production_decision(value: object) -> bool:
    """Return whether an owner exclusion may remove one production row.

    A decision code alone is deliberately insufficient.  Review Center,
    Final Control and the audit ledger use this one predicate so a row cannot
    look resolved in one place and remain blocked in another.
    """

    if not isinstance(value, Mapping):
        return False
    evidence = value.get("evidence")
    return (
        str(value.get("decision") or "").strip().upper()
        == "NON_PRODUCTION_POSITION"
        and str(value.get("action_class") or "").strip().upper() == "EXCLUSION"
        and str(value.get("reason_code") or "").strip().upper()
        == "NON_PRODUCTION_POSITION"
        and bool(str(value.get("note") or "").strip())
        and isinstance(evidence, Mapping)
        and bool(evidence)
    )
