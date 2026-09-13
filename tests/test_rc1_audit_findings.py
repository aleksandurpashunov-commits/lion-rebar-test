"""Регресионни тестове по находките от независимия red-team одит на RC1.

Всеки тест възпроизвежда конкретен reproducer от одита и го заключва.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from lion_rebar.project.data_identity import DataIdentity
from lion_rebar.project.final_control_lifecycle import (
    STATUS_INVALID,
    STATUS_VALID,
    assert_export_allowed,
    freeze_status,
)
from lion_rebar.review.apply_to_group import (
    apply_signature,
    build_group_plan,
    segment_shape,
    signature_fields,
)

IDENT = DataIdentity("P1", "a" * 64, "G1", "R1")


def _row(handle, **over):
    row = {
        "annotation_handle": handle, "annotation_text": "25N8x128",
        "annotation_count": "25", "diameter_mm": "8", "declared_length_cm": "128",
        "master_shape_code": "F006", "candidate_handles": f"G{handle}",
        "canonical_segment_lengths_cm": "34,30,34,30",
        "matcher_blocker_codes": "HOOKED_CLOSURE",
        "batch_status": "ЗА РЪЧЕН ПРЕГЛЕД", "review_group_key": "K1",
        "project_id": "P1", "source_sha256": "a" * 64,
        "source_generation_id": "G1", "analysis_run_id": "R1",
    }
    row.update(over)
    return row


def _freeze(payload):
    root = Path(tempfile.mkdtemp())
    (root / "working").mkdir()
    (root / "working" / "final_control_freeze.json").write_text(
        payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
    return root


# ---------------------------------------------- находка 1

def test_audit_f006_same_diameter_and_length_but_different_stirrup_is_not_equivalent():
    """34x30 и 44x20: еднакви Ø, L, MASTER и проблем — но РАЗЛИЧНИ стремена."""

    a = _row("A1", canonical_segment_lengths_cm="34,30,34,30")
    b = _row("A2", canonical_segment_lengths_cm="44,20,44,20")
    assert sum(float(x) for x in "34,30,34,30".split(",")) == \
           sum(float(x) for x in "44,20,44,20".split(","))
    assert apply_signature(a, IDENT) != apply_signature(b, IDENT)
    plan = build_group_plan(a, [a, b], IDENT, changes={"master_shape_code": "F013"})
    assert plan.affected_count == 0
    assert any("segment_shape" in m.reason for m in plan.excluded)


def test_audit_identical_stirrups_still_group_together():
    """Поправката не бива да убива легитимното групиране."""

    a = _row("A1")
    b = _row("A2")
    plan = build_group_plan(a, [a, b], IDENT, changes={"master_shape_code": "F013"})
    assert [m.annotation_handle for m in plan.eligible] == ["A2"]


def test_audit_turn_angles_are_part_of_the_signature():
    a = _row("A1", canonical_turn_angles_deg="90,90,90")
    b = _row("A2", canonical_turn_angles_deg="90,135,90")
    assert apply_signature(a, IDENT) != apply_signature(b, IDENT)


def test_audit_segment_shape_prefers_production_over_canonical():
    row = _row("A1", production_segments_cm="10,20",
               canonical_segment_lengths_cm="34,30,34,30")
    assert segment_shape(row) == "10|20"


def test_audit_signature_exposes_twelve_named_components():
    fields = signature_fields(_row("A1"), IDENT)
    assert "segment_shape" in fields and "turn_angles" in fields
    assert len(fields) == 12


# ---------------------------------------------- находка 2

def test_audit_resolved_positions_are_never_touched():
    representative = _row("A1")
    for status in ("ОДОБРЕНА ВРЪЗКА", "NON_PRODUCTION_POSITION",
                   "ОДОБРЕНО АВТОМАТИЧНО (ПАКЕТНА ПОРТА)"):
        member = _row("B1", batch_status=status)
        plan = build_group_plan(representative, [representative, member], IDENT,
                                changes={"master_shape_code": "F013"})
        assert plan.affected_count == 0, status
        assert plan.excluded[-1].reason == "ALREADY_RESOLVED", status


def test_audit_ownership_conflict_is_an_explicit_guard_not_a_side_effect():
    """И двамата с конфликт: подписите съвпадат, но пак не се прилага."""

    representative = _row("A1", global_conflict=True)
    member = _row("A2", global_conflict=True)
    plan = build_group_plan(representative, [representative, member], IDENT,
                            changes={"master_shape_code": "F013"})
    assert plan.affected_count == 0
    assert plan.excluded[-1].reason == "OWNERSHIP_CONFLICT"


def test_audit_geometry_owner_conflict_is_also_guarded():
    representative = _row("A1")
    member = _row("A2", geometry_owner_conflict=True)
    plan = build_group_plan(representative, [representative, member], IDENT,
                            changes={"master_shape_code": "F013"})
    assert plan.excluded[-1].reason == "OWNERSHIP_CONFLICT"


def test_audit_foreign_representative_refuses_the_whole_operation():
    foreign = _row("X1", project_id="OTHER")
    plan = build_group_plan(foreign, [foreign, _row("A2")], IDENT,
                            changes={"master_shape_code": "F013"})
    assert not plan.can_apply
    assert plan.refusal.startswith("REPRESENTATIVE_IDENTITY")
    assert plan.affected_count == 0


def test_audit_foreign_representative_generation_is_refused():
    foreign = _row("X1", source_generation_id="G9")
    plan = build_group_plan(foreign, [foreign, _row("A2")], IDENT,
                            changes={"master_shape_code": "F013"})
    assert not plan.can_apply
    assert "SOURCE_GENERATION_MISMATCH" in plan.refusal


# ---------------------------------------------- находка 11

def test_audit_export_allowed_only_for_exact_valid():
    assert assert_export_allowed(_freeze({"status": STATUS_VALID})) == []


def test_audit_unknown_status_blocks_export():
    problems = assert_export_allowed(_freeze({"status": "BANANA"}))
    assert problems and "FINAL_CONTROL_UNKNOWN_STATUS" in problems[0]


def test_audit_missing_status_is_invalid_not_valid():
    root = _freeze({"artifacts": []})
    assert freeze_status(root) == STATUS_INVALID
    assert assert_export_allowed(root)


def test_audit_empty_status_is_invalid():
    root = _freeze({"status": ""})
    assert freeze_status(root) == STATUS_INVALID
    assert assert_export_allowed(root)


def test_audit_non_object_freeze_is_invalid():
    root = _freeze("[]")
    assert freeze_status(root) == STATUS_INVALID
    assert assert_export_allowed(root)


def test_audit_corrupt_freeze_still_blocks():
    assert assert_export_allowed(_freeze("{broken"))


def test_audit_missing_freeze_reports_no_final_control():
    problems = assert_export_allowed(Path(tempfile.mkdtemp()))
    assert problems and "NO_FINAL_CONTROL" in problems[0]


# ------------------- Windows приемане rc2: freeze без status

def test_windows_rc2_freeze_manifest_writes_explicit_status():
    """Финален контрол ТРЯБВА да записва status, иначе export блокира веднага.

    Открито при реалния Windows пробег: freeze манифестът съдържаше само
    schema/generated_at/data_identity/artifacts. С новото fail-closed
    правило липсващият статус е INVALID, тоест успешен Финален контрол
    произвеждаше неизползваем freeze.
    """

    source = Path("lion_rebar/project/integrity.py").read_text(encoding="utf-8")
    start = source.index("def _build_freeze_manifest")
    end = source.index("def ", source.index("_write_json_atomic(manifest_path", start))
    body = source[start:end]
    assert '"status": FREEZE_STATUS_VALID' in body


def test_windows_rc2_fresh_freeze_allows_export():
    """Кръгът се затваря: нов freeze -> export разрешен."""

    from lion_rebar.project.final_control_lifecycle import STATUS_VALID

    root = Path(tempfile.mkdtemp())
    (root / "working").mkdir()
    (root / "working" / "final_control_freeze.json").write_text(
        json.dumps({
            "schema": "lion-rebar-final-control-freeze/v1",
            "status": STATUS_VALID,
            "generated_at": "2026-08-24T00:00:00+00:00",
            "data_identity": {"project_id": "P1"},
            "artifacts": [],
        }), encoding="utf-8")
    assert assert_export_allowed(root) == []


def test_windows_rc2_legacy_freeze_without_status_blocks_export():
    """Стар freeze (6.2) без status: export е блокиран, не мълчаливо разрешен."""

    root = Path(tempfile.mkdtemp())
    (root / "working").mkdir()
    (root / "working" / "final_control_freeze.json").write_text(
        json.dumps({
            "schema": "lion-rebar-final-control-freeze/v1",
            "generated_at": "2026-08-15T12:02:17+03:00",
            "data_identity": {"project_id": "P1"},
            "artifacts": [{"role": "SOURCE_DXF", "exists": True}],
        }), encoding="utf-8")
    problems = assert_export_allowed(root)
    # rc4: съобщението вече казва РЕАЛНАТА причина — непълен запис, а не
    # "обезсилен след production промяна".
    assert problems and "FINAL_CONTROL_INCOMPLETE" in problems[0]


# ------------------- rc4: съобщението казва РЕАЛНАТА причина

def _state(payload):
    from lion_rebar.project.final_control_lifecycle import freeze_state

    return freeze_state(_freeze(payload))


def test_rc4_legacy_freeze_is_incomplete_not_invalidated():
    """Стар freeze без status НЕ е 'обезсилен след production промяна'."""

    from lion_rebar.project.final_control_lifecycle import STATUS_MISSING

    status, reason = _state({"schema": "x", "generated_at": "t", "artifacts": []})
    assert status == STATUS_MISSING
    assert reason == "LEGACY_FREEZE_WITHOUT_STATUS"
    problems = assert_export_allowed(_freeze({"schema": "x", "artifacts": []}))
    assert problems and "FINAL_CONTROL_INCOMPLETE" in problems[0]
    assert "production промяна" not in problems[0]


def test_rc4_real_invalidation_still_says_production_change():
    problems = assert_export_allowed(
        _freeze({"status": "INVALID", "invalidated_reason": "PRODUCTION_CHANGE"}))
    assert problems and "FINAL_CONTROL_INVALID" in problems[0]
    assert "production промяна" in problems[0]


def test_rc4_corrupt_freeze_says_unreadable():
    problems = assert_export_allowed(_freeze("{broken"))
    assert problems and "FINAL_CONTROL_UNREADABLE" in problems[0]


def test_rc4_unknown_status_still_blocks_with_its_own_message():
    problems = assert_export_allowed(_freeze({"status": "BANANA"}))
    assert problems and "FINAL_CONTROL_UNKNOWN_STATUS" in problems[0]


def test_rc4_every_failure_mode_has_a_distinct_message():
    """Четирите режима на отказ имат четири РАЗЛИЧНИ кода."""

    codes = []
    for payload in ({"schema": "x", "artifacts": []},
                    {"status": "INVALID"},
                    "{broken",
                    {"status": "BANANA"}):
        problems = assert_export_allowed(_freeze(payload))
        assert problems, payload
        codes.append(problems[0].split(":")[0])
    assert codes == ["FINAL_CONTROL_INCOMPLETE", "FINAL_CONTROL_INVALID",
                     "FINAL_CONTROL_UNREADABLE", "FINAL_CONTROL_UNKNOWN_STATUS"]
    assert len(set(codes)) == 4


# ------------------- rc6: заключен файл на Windows/OneDrive

def test_rc6_replace_retries_on_windows_lock():
    """WinError 32 не бива да проваля export от първия опит."""

    import lion_rebar.export.bundle as bundle

    calls = {"n": 0}
    real = bundle.os.replace

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] < 3:
            error = PermissionError("locked")
            error.winerror = 32
            raise error
        return real(src, dst)

    bundle.os.replace = flaky
    try:
        root = Path(tempfile.mkdtemp())
        source = root / "tmp.txt"; source.write_text("нов", encoding="utf-8")
        target = root / "final.txt"; target.write_text("стар", encoding="utf-8")
        bundle._replace_with_retry(source, target)
        assert target.read_text(encoding="utf-8") == "нов"
        assert calls["n"] == 3
    finally:
        bundle.os.replace = real


def test_rc6_permanent_lock_raises_with_operator_guidance():
    import lion_rebar.export.bundle as bundle

    real = bundle.os.replace

    def always_locked(src, dst):
        error = PermissionError("locked")
        error.winerror = 32
        raise error

    bundle.os.replace = always_locked
    bundle._REPLACE_DELAY_SECONDS = 0.0
    try:
        root = Path(tempfile.mkdtemp())
        source = root / "tmp.txt"; source.write_text("x", encoding="utf-8")
        target = root / "final.txt"
        try:
            bundle._replace_with_retry(source, target)
        except PermissionError as exc:
            assert "Затвори Excel" in str(exc)
            return
        raise AssertionError("очаква се PermissionError след изчерпани опити")
    finally:
        bundle.os.replace = real
        bundle._REPLACE_DELAY_SECONDS = 0.25


def test_rc6_non_lock_oserror_is_not_retried():
    """Само заключване се повтаря; други OSError излизат веднага."""

    import lion_rebar.export.bundle as bundle

    real = bundle.os.replace

    def disk_full(src, dst):
        error = OSError("no space")
        error.winerror = 112
        raise error

    bundle.os.replace = disk_full
    try:
        root = Path(tempfile.mkdtemp())
        source = root / "t.txt"; source.write_text("x", encoding="utf-8")
        try:
            bundle._replace_with_retry(source, root / "f.txt")
        except OSError as exc:
            assert getattr(exc, "winerror", None) == 112
            return
        raise AssertionError("не-заключващ OSError трябва да излезе веднага")
    finally:
        bundle.os.replace = real

