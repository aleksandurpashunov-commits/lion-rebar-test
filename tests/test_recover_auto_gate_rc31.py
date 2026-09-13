from __future__ import annotations

import json
from pathlib import Path

from tools.recover_auto_gate_rc31 import _decision_fingerprint


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_decision_fingerprint_ignores_integrity_metadata_updates(
    tmp_path: Path,
) -> None:
    state = tmp_path / "review_center_state.json"
    decisions = {
        "A1": {"decision": "ENGINEER_RESOLVED", "production_length_cm": 105.0}
    }
    _write(state, {"decisions": decisions, "updated_at": "before"})
    before = _decision_fingerprint(state)

    _write(
        state,
        {
            "decisions": decisions,
            "updated_at": "after",
            "data_identity": {"analysis_run_id": "RUN"},
        },
    )

    assert _decision_fingerprint(state) == before


def test_decision_fingerprint_detects_changed_user_decision(tmp_path: Path) -> None:
    state = tmp_path / "review_center_state.json"
    _write(state, {"decisions": {"A1": {"decision": "ENGINEER_RESOLVED"}}})
    before = _decision_fingerprint(state)
    _write(state, {"decisions": {"A1": {"decision": "EXCLUDED_BY_ENGINEER"}}})

    assert _decision_fingerprint(state) != before
