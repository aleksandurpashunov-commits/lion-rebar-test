# -*- coding: utf-8 -*-
"""Операторски път за СПОРНО ВЛАДЕНИЕ на геометрия (§ 6.2).

ЗАЩО СЪЩЕСТВУВА
---------------
Анализът маркира ``global_conflict`` / ``geometry_owner_conflict``, когато ЕДИН
DXF обект е първи кандидат на няколко надписа, и спира позициите за ръчен
преглед. Това е вярно и нарочно: еднаквият надпис НЕ доказва еднакво решение,
нито отделно количество.

Липсваше изходът. Измерено на замразената база от 29.09.2026: **52 позиции в 21
клъстера** (`d-2` 34, `Тест-002` 8, `s-9` 4, `s-7` 2, `s-8` 2, `тест-006` 2)
стояха спрени само защото нищо в програмата не позволяваше на оператора да каже
чия е геометрията. Решение за греда (``beam_decisions``) и за рамка
(``frame_decisions``) имаше; за владението — не.

Собственик, 02.10.2026: „§6 още не е затворен: липсващ операторски път за 52
позиции над 23 надписа. Той трябва да стане използваем през нормалния
интерфейс."

ДВАТА ВИДА РЕШЕНИЕ
------------------
``OWNER``   геометрията е на ЕДИН от спорещите надписи. Останалите не я
            изнасят и остават блокирани с ясна причина — те не са решени, а
            отделени, и всеки от тях иска свое решение.
``SHARED``  геометрията е обща ПО ЗАМИСЪЛ: един начертан обект е доказателство
            за няколко реда от описа, всеки със своето количество. Същото,
            което автоматиката вече разпознава при легендните референции
            (``is_position_key_legend_reference``) — тук то се заявява от
            оператора, когато автоматиката не го е разпознала.

ПРАВИЛАТА СА НА ``beam_decisions``
----------------------------------
* решава се САМО действителен конфликт — измислен спор не се приема;
* иска се автор и мотив;
* ВСЕКИ член на спора се изброява поименно в решението (§ 6.2: групово решение
  с проверка на всеки член). Член, който не е в списъка, не е решен;
* при ``OWNER`` собственикът е длъжен да е сред членовете;
* записва се с identity на проекта и с одитна следа;
* решение от друг DXF НЕ се наследява; нов анализ на същата редакция го
  запазва, но то важи само за същия спор със същия състав;
* решението се оттегля, като оттеглянето също се записва.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from lion_rebar.project.data_identity import DataIdentity, mismatch_reason
from lion_rebar.utils.files import atomic_write_text
from lion_rebar.utils.process_lock import exclusive_file_lock

DECISIONS_NAME = "geometry_ownership_decisions.json"
AUDIT_NAME = "geometry_ownership_audit.jsonl"
SCHEMA = "lion-rebar-geometry-ownership-decisions/v1"

#: Методът, записан в ledger-а, за да личи произходът на връзката.
OPERATOR_METHOD = "OPERATOR_OWNERSHIP_DECISION"

#: Геометрията е на един от спорещите надписи.
ВИД_СОБСТВЕНИК = "OWNER"

#: Геометрията е обща по замисъл; всеки член си пази количеството.
ВИД_СПОДЕЛЕНА = "SHARED"

ВИДОВЕ = (ВИД_СОБСТВЕНИК, ВИД_СПОДЕЛЕНА)

MIN_NOTE_LENGTH = 3


class OwnershipDecisionError(ValueError):
    """Невалидно решение за владение — отказва се, не се записва."""


@dataclass(frozen=True)
class OwnershipDecision:
    """Едно операторско решение за една спорна геометрия."""

    geometry_key: tuple[str, ...]
    kind: str
    owner_annotation_handle: str
    member_handles: tuple[str, ...]
    decided_by: str
    note: str
    at: str
    project_id: str
    source_sha256: str
    source_generation_id: str
    analysis_run_id: str

    def owns(self, annotation_handle: str) -> bool:
        """Изнася ли ТОЗИ надпис спорната геометрия според решението."""

        handle = str(annotation_handle)
        if handle not in self.member_handles:
            return False
        if self.kind == ВИД_СПОДЕЛЕНА:
            return True
        return handle == self.owner_annotation_handle

    def to_dict(self) -> dict:
        return {
            "geometry_key": list(self.geometry_key),
            "kind": self.kind,
            "owner_annotation_handle": self.owner_annotation_handle,
            "member_handles": list(self.member_handles),
            "decided_by": self.decided_by,
            "note": self.note,
            "at": self.at,
            "project_id": self.project_id,
            "source_sha256": self.source_sha256,
            "source_generation_id": self.source_generation_id,
            "analysis_run_id": self.analysis_run_id,
        }


def geometry_key(handles: Iterable[Any]) -> tuple[str, ...]:
    """Каноничният ключ на една геометрия: сортирани, непразни handle-и."""

    return tuple(sorted({str(стойност) for стойност in handles if str(стойност or "").strip()}))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _paths(project_root: Path | str) -> tuple[Path, Path]:
    working = Path(project_root) / "working"
    return working / DECISIONS_NAME, working / AUDIT_NAME


def _read(path: Path) -> dict:
    """Записаните решения. ЛИПСВАЩ файл е празно; НЕЧЕТИМ е ОТКАЗ (§ 7.6)."""

    if not path.is_file():
        return {"schema": SCHEMA, "decisions": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as грешка:
        raise OwnershipDecisionError(
            "Файлът с решенията за владение (%s) съществува, но не се чете: %s. "
            "Нищо не се записва върху него." % (path.name, грешка)
        ) from грешка
    if not isinstance(payload, dict) or not isinstance(payload.get("decisions"), list):
        raise OwnershipDecisionError(
            "Файлът с решенията за владение (%s) е с неочаквана структура."
            % path.name
        )
    return payload


def _write(path: Path, payload: dict) -> None:
    atomic_write_text(
        path, json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    )


def _audit(project_root: Path | str, entry: Mapping[str, Any]) -> None:
    _, audit = _paths(project_root)
    audit.parent.mkdir(parents=True, exist_ok=True)
    with open(audit, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(entry), ensure_ascii=False) + "\n")


def validate_decision(
    *,
    kind: str,
    key: Sequence[str],
    owner_annotation_handle: str,
    member_handles: Sequence[str],
    decided_by: str,
    note: str,
    conflict_members: Mapping[str, Sequence[str]],
) -> None:
    """Отказва всичко, което не е добросъвестно решение на реален спор.

    ``conflict_members`` е действителното състояние от анализа: ключ на
    геометрия → надписите, които се борят за нея. Решение за геометрия, която
    не е в спор, не се приема — иначе пътят би станал начин да се заобиколи
    проверката, вместо да я реши.
    """

    if str(kind) not in ВИДОВЕ:
        raise OwnershipDecisionError("Непознат вид решение: %r" % (kind,))
    ключ = geometry_key(key)
    if not ключ:
        raise OwnershipDecisionError("Решението няма геометрия.")
    действителни = {
        str(handle)
        for handle in conflict_members.get(ключ, ())
        if str(handle or "").strip()
    }
    if not действителни:
        raise OwnershipDecisionError(
            "Тази геометрия не е в спор според анализа; операторско решение за "
            "владение не се приема по този път."
        )
    подадени = {str(handle) for handle in member_handles if str(handle or "").strip()}
    if not подадени:
        raise OwnershipDecisionError("Решението не изброява нито един член.")
    if подадени != действителни:
        липсват = sorted(действителни - подадени)
        излишни = sorted(подадени - действителни)
        raise OwnershipDecisionError(
            "Решението трябва да изброи ТОЧНО членовете на спора. "
            "Непокрити: %s. Непринадлежащи: %s."
            % (", ".join(липсват) or "няма", ", ".join(излишни) or "няма")
        )
    if str(kind) == ВИД_СОБСТВЕНИК:
        if str(owner_annotation_handle or "") not in подадени:
            raise OwnershipDecisionError(
                "Собственикът трябва да е един от спорещите надписи."
            )
    if not str(decided_by or "").strip():
        raise OwnershipDecisionError("Липсва автор на решението.")
    if len(str(note or "").strip()) < MIN_NOTE_LENGTH:
        raise OwnershipDecisionError("Изисква се инженерна бележка (мотив).")


def record_ownership_decision(
    project_root: Path | str,
    *,
    kind: str,
    key: Sequence[str],
    owner_annotation_handle: str = "",
    member_handles: Sequence[str],
    decided_by: str,
    note: str,
    identity: DataIdentity,
    conflict_members: Mapping[tuple[str, ...], Sequence[str]],
) -> OwnershipDecision:
    """Записва едно решение за владение и одитния му ред."""

    validate_decision(
        kind=kind,
        key=key,
        owner_annotation_handle=owner_annotation_handle,
        member_handles=member_handles,
        decided_by=decided_by,
        note=note,
        conflict_members=conflict_members,
    )
    ключ = geometry_key(key)
    решение = OwnershipDecision(
        geometry_key=ключ,
        kind=str(kind),
        owner_annotation_handle=str(owner_annotation_handle or ""),
        member_handles=tuple(sorted({str(handle) for handle in member_handles})),
        decided_by=str(decided_by).strip(),
        note=str(note).strip(),
        at=_now(),
        project_id=identity.project_id,
        source_sha256=identity.source_sha256,
        source_generation_id=identity.source_generation_id,
        analysis_run_id=identity.analysis_run_id,
    )
    path, _audit_path = _paths(project_root)
    with exclusive_file_lock(path):
        payload = _read(path)
        останали = [
            ред
            for ред in payload.get("decisions", [])
            if isinstance(ред, dict)
            and geometry_key(ред.get("geometry_key") or ()) != ключ
        ]
        останали.append(решение.to_dict())
        payload["schema"] = SCHEMA
        payload["decisions"] = останали
        _write(path, payload)
    _audit(project_root, {"event": "OWNERSHIP_RECORDED", **решение.to_dict()})
    return решение


def withdraw_ownership_decision(
    project_root: Path | str,
    *,
    key: Sequence[str],
    withdrawn_by: str,
    note: str,
) -> bool:
    """Оттегля решение. Връща ``False``, ако такова няма."""

    if not str(withdrawn_by or "").strip():
        raise OwnershipDecisionError("Липсва автор на оттеглянето.")
    if len(str(note or "").strip()) < MIN_NOTE_LENGTH:
        raise OwnershipDecisionError("Изисква се обосновка за оттеглянето.")
    ключ = geometry_key(key)
    path, _audit_path = _paths(project_root)
    with exclusive_file_lock(path):
        payload = _read(path)
        всички = [ред for ред in payload.get("decisions", []) if isinstance(ред, dict)]
        останали = [
            ред for ред in всички if geometry_key(ред.get("geometry_key") or ()) != ключ
        ]
        if len(останали) == len(всички):
            return False
        payload["schema"] = SCHEMA
        payload["decisions"] = останали
        _write(path, payload)
    _audit(
        project_root,
        {
            "event": "OWNERSHIP_WITHDRAWN",
            "geometry_key": list(ключ),
            "withdrawn_by": str(withdrawn_by).strip(),
            "note": str(note).strip(),
            "at": _now(),
        },
    )
    return True


def load_ownership_decisions(
    project_root: Path | str, identity: DataIdentity | None
) -> dict[tuple[str, ...], OwnershipDecision]:
    """Решенията, валидни за ТАЗИ редакция на чертежа.

    Решение от друг проект, друга генерация или друго съдържание на чертежа се
    пропуска — същата защита срещу наследяване както при гредите и рамките.

    ``analysis_run_id`` НЕ се сверява, както при начертаните рамки
    (``load_operator_areas``). Дотук се сверяваше и решението умираше от
    собствената си инструкция: диалогът казва „Пусни пакетния анализ наново“,
    а новият анализ започва с нов ``analysis_run_id`` (на ``d-2``: 17
    прочетени преди, 0 след). Собственик, 22.10.2026: решението да преживява
    нов анализ на същата редакция.

    Опасението, заради което се сверяваше — нов прогон може да даде други
    спорове — се пази там, където решението се ПРИЛАГА:
    ``_apply_global_geometry_conflicts`` го приема само когато новият анализ
    дава същата геометрия с ТОЧНО същите спорещи надписи.
    """

    path, _audit_path = _paths(project_root)
    payload = _read(path)
    резултат: dict[tuple[str, ...], OwnershipDecision] = {}
    for ред in payload.get("decisions", []):
        if not isinstance(ред, dict):
            continue
        if identity is not None and mismatch_reason(ред, identity):
            continue
        ключ = geometry_key(ред.get("geometry_key") or ())
        членове = tuple(
            sorted({str(handle) for handle in (ред.get("member_handles") or []) if str(handle)})
        )
        вид = str(ред.get("kind") or "")
        if not ключ or not членове or вид not in ВИДОВЕ:
            continue
        собственик = str(ред.get("owner_annotation_handle") or "")
        if вид == ВИД_СОБСТВЕНИК and собственик not in членове:
            # Половин решение не е решение: без валиден собственик редът пада,
            # вместо да освободи геометрията на произволен член.
            continue
        резултат[ключ] = OwnershipDecision(
            geometry_key=ключ,
            kind=вид,
            owner_annotation_handle=собственик,
            member_handles=членове,
            decided_by=str(ред.get("decided_by") or ""),
            note=str(ред.get("note") or ""),
            at=str(ред.get("at") or ""),
            project_id=str(ред.get("project_id") or ""),
            source_sha256=str(ред.get("source_sha256") or ""),
            source_generation_id=str(ред.get("source_generation_id") or ""),
            analysis_run_id=str(ред.get("analysis_run_id") or ""),
        )
    return резултат


def audit_trail(project_root: Path | str) -> list[dict]:
    """Одитната следа, най-старото първо."""

    _path, audit = _paths(project_root)
    if not audit.is_file():
        return []
    редове: list[dict] = []
    for ред in audit.read_text(encoding="utf-8-sig").splitlines():
        try:
            стойност = json.loads(ред)
        except (TypeError, ValueError):
            continue
        if isinstance(стойност, dict):
            редове.append(стойност)
    return редове
