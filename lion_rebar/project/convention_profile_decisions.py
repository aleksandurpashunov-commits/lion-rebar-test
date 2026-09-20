"""Операторско потвърждение на паспорта на чертежа (профил на конвенцията).

ЗАЩО СЪЩЕСТВУВА
---------------
``dxf/convention_profile.py`` предлага ролите по съдържание — кой слой носи
позиционните ключове, заглавията на зоните, надписите и армировъчната
геометрия — с доказателство и с контрола. Предложение без операторски път е
половин функция: някой трябва да го потвърди, а потвърждението трябва да е
проследимо.

Правила, същите като при решението за рамка (``export/frame_decisions.py``):

* роля се приема само от ``PROPOSABLE_ROLES`` — „не знам“ не е решение и не се
  записва;
* изисква се автор;
* **мотив се изисква само когато операторът се разминава с предложеното** —
  приемането на предложение носи своето доказателство в себе си, а отхвърлянето
  или смяната на ролята е решение срещу измереното и иска обяснение;
* всяко решение се стъпва със самоличността на данните и влиза в одитна следа;
* профил от друг DXF или друга генерация **не се наследява** — ``load`` отказва
  при разминаване, точно както решенията за рамки.

КАКВО ТОЗИ МОДУЛ НАРОЧНО НЕ ПРАВИ
---------------------------------
Не обезсилва Финалния контрол. Днес потвърденият профил **не влиза в
разпознаването** — нито един продуктов модул не го чете — затова записът му не
мени нито едно инженерно число и обезсилването би било празна пречка. Това е
пиннато с тест (``test_потвърждаването_днес_не_пипа_замразяването``).

**Когато профилът влезе в разпознаването (стъпка 5 от Етап 2 в
``docs/ROADMAP_PRODUCT_BG.md``), обезсилването става задължително** — тогава
профилът мени резултата и стар Финален контрол не бива да остава валиден.
Смяната да е нарочна, не случайна: тестът по-горе ще падне и ще принуди
решение.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from lion_rebar.dxf.convention_profile import PROPOSABLE_ROLES
from lion_rebar.project.data_identity import DataIdentity, mismatch_reason

PROFILE_NAME = "convention_profile.json"
AUDIT_NAME = "convention_profile_audit.jsonl"
SCHEMA = "lion-rebar-convention-profile-decisions/v1"

#: Какво е направил операторът с предложението.
ACCEPTED = "ACCEPTED"
CHANGED = "CHANGED"
REJECTED = "REJECTED"
VERDICTS = (ACCEPTED, CHANGED, REJECTED)

MIN_NOTE_LENGTH = 3


class ConventionProfileError(ValueError):
    """Невалидно решение по паспорта — отказва се, не се записва."""


@dataclass(frozen=True)
class ProfileDecision:
    """Едно операторско решение за един слой или блоково семейство."""

    target_kind: str
    layer: str
    block_name: str
    role: str
    verdict: str
    proposed_role: str
    evidence: str
    note: str
    decided_by: str
    at: str
    project_id: str
    source_sha256: str
    source_generation_id: str
    analysis_run_id: str

    @property
    def target_key(self) -> str:
        return f"{self.target_kind}:{self.layer}:{self.block_name}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_kind": self.target_kind,
            "layer": self.layer,
            "block_name": self.block_name,
            "role": self.role,
            "verdict": self.verdict,
            "proposed_role": self.proposed_role,
            "evidence": self.evidence,
            "note": self.note,
            "decided_by": self.decided_by,
            "at": self.at,
            "project_id": self.project_id,
            "source_sha256": self.source_sha256,
            "source_generation_id": self.source_generation_id,
            "analysis_run_id": self.analysis_run_id,
        }


def validate_decision(
    *,
    role: str,
    verdict: str,
    proposed_role: str,
    decided_by: str,
    note: str,
) -> None:
    """Проверява едно решение, преди да бъде записано."""

    if verdict not in VERDICTS:
        raise ConventionProfileError(f"Непознато решение: {verdict!r}")
    if verdict == REJECTED:
        if role:
            raise ConventionProfileError(
                "Отхвърлено предложение не носи роля."
            )
    else:
        if role not in PROPOSABLE_ROLES:
            raise ConventionProfileError(f"Непозната роля: {role!r}")
    if not str(decided_by or "").strip():
        raise ConventionProfileError("Липсва автор на решението.")
    разминаване = verdict != ACCEPTED or (proposed_role and role != proposed_role)
    if разминаване and len(str(note or "").strip()) < MIN_NOTE_LENGTH:
        raise ConventionProfileError(
            "Решение, различно от предложеното, изисква мотив — измереното "
            "доказателство се отменя от човек, не от мълчание."
        )


def record_convention_profile(
    project_root: Path | str,
    *,
    decisions: Iterable[Mapping[str, Any]],
    confirmed_by: str,
    identity: DataIdentity,
    proposer_version: str = "",
) -> list[ProfileDecision]:
    """Записва потвърдения профил и одитната следа.

    ``decisions`` е списък от речници с ключове ``target_kind``, ``layer``,
    ``block_name``, ``role``, ``verdict``, ``proposed_role``, ``evidence`` и
    ``note``. Празен списък е отказ — профил без нито едно решение не се пише.
    """

    подготвени: list[ProfileDecision] = []
    сега = _now()
    for сурово in decisions:
        роля = str(сурово.get("role") or "")
        решение = str(сурово.get("verdict") or "")
        предложена = str(сурово.get("proposed_role") or "")
        бележка = str(сурово.get("note") or "")
        validate_decision(
            role=роля,
            verdict=решение,
            proposed_role=предложена,
            decided_by=confirmed_by,
            note=бележка,
        )
        слой = str(сурово.get("layer") or "")
        блок = str(сурово.get("block_name") or "")
        if not (слой or блок):
            raise ConventionProfileError("Решение без слой и без блок.")
        подготвени.append(
            ProfileDecision(
                target_kind=str(сурово.get("target_kind") or "LAYER"),
                layer=слой,
                block_name=блок,
                role=роля,
                verdict=решение,
                proposed_role=предложена,
                evidence=str(сурово.get("evidence") or ""),
                note=бележка.strip(),
                decided_by=str(confirmed_by).strip(),
                at=сега,
                project_id=identity.project_id,
                source_sha256=identity.source_sha256,
                source_generation_id=identity.source_generation_id,
                analysis_run_id=identity.analysis_run_id,
            )
        )
    if not подготвени:
        raise ConventionProfileError("Няма нито едно решение — профил не се записва.")

    ключове = [решение.target_key for решение in подготвени]
    if len(set(ключове)) != len(ключове):
        raise ConventionProfileError("Един и същ слой или блок има две решения.")

    път = _profile_path(project_root)
    payload = {
        "schema": SCHEMA,
        "confirmed_by": str(confirmed_by).strip(),
        "confirmed_at": сега,
        "proposer_version": str(proposer_version or ""),
        "project_id": identity.project_id,
        "source_sha256": identity.source_sha256,
        "source_generation_id": identity.source_generation_id,
        "analysis_run_id": identity.analysis_run_id,
        "decisions": [решение.to_dict() for решение in подготвени],
    }
    _write(път, payload)
    for решение in подготвени:
        _audit(project_root, {"event": "CONVENTION_PROFILE_DECISION", **решение.to_dict()})
    return подготвени


def load_convention_profile(
    project_root: Path | str,
    *,
    identity: DataIdentity | None = None,
) -> dict[str, Any] | None:
    """Чете потвърдения профил; отказва профил от друг чертеж или генерация."""

    път = _profile_path(project_root)
    if not път.is_file():
        return None
    try:
        payload = json.loads(път.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("decisions"), list):
        return None
    if identity is not None:
        причина = mismatch_reason(payload, identity)
        if причина:
            payload = dict(payload)
            payload["identity_mismatch"] = причина
            payload["decisions"] = []
    return payload


def confirmed_roles(payload: Mapping[str, Any] | None) -> dict[str, str]:
    """Кой слой или блок с каква роля е потвърден. Отхвърлените не влизат."""

    изход: dict[str, str] = {}
    for ред in (payload or {}).get("decisions", []) or []:
        if not isinstance(ред, dict):
            continue
        if str(ред.get("verdict") or "") == REJECTED:
            continue
        роля = str(ред.get("role") or "")
        if роля not in PROPOSABLE_ROLES:
            continue
        ключ = str(ред.get("block_name") or "") or str(ред.get("layer") or "")
        if ключ:
            изход[ключ] = роля
    return изход


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _profile_path(project_root: Path | str) -> Path:
    return Path(project_root) / "working" / PROFILE_NAME


def _write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def _audit(project_root: Path | str, entry: Mapping[str, Any]) -> None:
    път = Path(project_root) / "working" / AUDIT_NAME
    път.parent.mkdir(parents=True, exist_ok=True)
    with open(път, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(entry), ensure_ascii=False) + "\n")
