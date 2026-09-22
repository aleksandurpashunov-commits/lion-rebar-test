from __future__ import annotations

import json
import shutil
import uuid
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lion_rebar.config.constants import (
    ACTIVATION_PHRASE,
    APPROVAL_PHRASE,
    CONTROLLED_REVISION_PHRASE,
)
from lion_rebar.engine.bootstrap import create_bootstrap_engine
from lion_rebar.engine.models import EnginePackage, ShapeDefinition, SourceAsset, utc_now_iso
from lion_rebar.engine.package_io import (
    export_engine_bundle,
    extract_engine_bundle_assets,
    inspect_engine_bundle,
)
from lion_rebar.engine.test_runner import run_engine_tests
from lion_rebar.engine.validation import ValidationIssue, has_errors, validate_engine
from lion_rebar.utils.files import (
    atomic_write_json,
    canonical_json_bytes,
    copy_without_overwrite,
    ensure_directory,
    read_json,
    safe_filename,
    set_read_only,
    set_writable,
    sha256_bytes,
    sha256_file,
)


class EngineRepositoryError(RuntimeError):
    pass


class EngineValidationError(EngineRepositoryError):
    def __init__(self, issues: list[ValidationIssue]):
        self.issues = issues
        message = "\n".join(f"[{issue.severity}] {issue.code}: {issue.message}" for issue in issues)
        super().__init__(message)


@dataclass(frozen=True)
class EngineVersionInfo:
    engine_id: str
    version: str
    title: str
    state: str
    path: Path
    published_at: str | None
    content_sha256: str | None
    production_ready: bool
    current: bool = False


class EngineRepository:
    def __init__(self, root: Path):
        self.root = root
        self.drafts_dir = root / "drafts"
        self.published_dir = root / "published"
        self.assets_dir = root / "assets"
        self.backups_dir = root / "backups"
        self.imports_dir = root / "imports"
        self.audit_file = root / "audit.jsonl"
        self.current_file = root / "current.json"
        self.initialize()

    def initialize(self) -> None:
        for directory in (
            self.root,
            self.drafts_dir,
            self.published_dir,
            self.assets_dir,
            self.backups_dir,
            self.imports_dir,
        ):
            ensure_directory(directory)
        if not self.current_file.exists():
            self._install_bootstrap()

    def _install_bootstrap(self) -> None:
        engine = create_bootstrap_engine()
        engine.published_at = utc_now_iso()
        engine.updated_at = engine.published_at
        engine.content_sha256 = self._calculate_engine_sha(engine)
        filename = self._published_filename(engine)
        path = self.published_dir / filename
        atomic_write_json(path, engine.to_dict())
        set_read_only(path)
        self._write_current_pointer(engine, path, reason="INITIAL_BOOTSTRAP", by="SYSTEM")
        self._audit("INSTALL_BOOTSTRAP", engine_id=engine.engine_id, version=engine.version)

    def current_pointer(self) -> dict[str, Any]:
        return read_json(self.current_file)

    def load_current(self) -> EnginePackage:
        pointer = self.current_pointer()
        path = self.root / str(pointer["relative_path"])
        if not path.exists():
            raise EngineRepositoryError(f"Активният engine липсва: {path}")
        source_data = read_json(path)
        engine = EnginePackage.from_dict(source_data)
        self._verify_engine_checksum(engine, source_data=source_data)
        return engine

    def current_path(self) -> Path:
        pointer = self.current_pointer()
        return self.root / str(pointer["relative_path"])

    def save_draft(self, engine: EnginePackage) -> Path:
        engine.state = "DRAFT"
        engine.updated_at = utc_now_iso()
        engine.content_sha256 = None
        issues = validate_engine(engine, parent=self._try_load_parent(engine), for_publish=False)
        if has_errors(issues):
            raise EngineValidationError(issues)
        path = self.drafts_dir / self._draft_filename(engine)
        atomic_write_json(path, engine.to_dict())
        self._audit("SAVE_DRAFT", engine_id=engine.engine_id, version=engine.version, path=str(path))
        return path

    def load_draft(self, path_or_name: Path | str) -> EnginePackage:
        path = Path(path_or_name)
        if not path.is_absolute():
            path = self.drafts_dir / path
        return EnginePackage.from_dict(read_json(path))

    def list_drafts(self) -> list[EngineVersionInfo]:
        """Черновите, подредени по ВЕРСИЯ — последната е най-новата.

        Дотук се сортираше по име на файл, тоест като низ. При десет чернови
        подредбата ставаше 0.3.1, 0.3.12, 0.3.15, 0.3.16, 0.3.2 … 0.3.7,
        защото знакът „7“ е след „1“. ``EngineStudio`` отваря последната
        (``drafts[-1]``) и затова показваше **0.3.7** вместо най-скорошната.

        Намерено 22.09.2026: собственикът роди форма от случай, черновата
        0.3.16 се записа вярно, а Studio отвори 0.3.7 и формата „я нямаше“.
        """

        def по_версия(path: Path) -> tuple:
            стъбло = path.name[: -len(".draft.json")]
            версия = стъбло.rsplit("_v", 1)[-1] if "_v" in стъбло else стъбло
            части = []
            for къс in версия.split("."):
                части.append((0, int(къс)) if къс.isdigit() else (1, къс))
            return (tuple(части), path.name)

        result: list[EngineVersionInfo] = []
        for path in sorted(self.drafts_dir.glob("*.draft.json"), key=по_версия):
            try:
                engine = EnginePackage.from_dict(read_json(path))
            except Exception:
                continue
            result.append(
                EngineVersionInfo(
                    engine_id=engine.engine_id,
                    version=engine.version,
                    title=engine.title,
                    state=engine.state,
                    path=path,
                    published_at=engine.published_at,
                    content_sha256=engine.content_sha256,
                    production_ready=engine.production_ready,
                )
            )
        return result

    def list_published(self) -> list[EngineVersionInfo]:
        pointer = self.current_pointer()
        current_relative = str(pointer.get("relative_path", ""))
        result: list[EngineVersionInfo] = []
        for path in sorted(self.published_dir.glob("*.json")):
            try:
                engine = EnginePackage.from_dict(read_json(path))
            except Exception:
                continue
            result.append(
                EngineVersionInfo(
                    engine_id=engine.engine_id,
                    version=engine.version,
                    title=engine.title,
                    state=engine.state,
                    path=path,
                    published_at=engine.published_at,
                    content_sha256=engine.content_sha256,
                    production_ready=engine.production_ready,
                    current=str(path.relative_to(self.root)) == current_relative,
                )
            )
        return result

    def load_published(self, engine_id: str, version: str) -> EnginePackage:
        candidates = [
            info for info in self.list_published()
            if info.engine_id == engine_id and info.version == version
        ]
        if not candidates:
            raise EngineRepositoryError(f"Не е намерена публикувана версия {engine_id} {version}.")
        source_data = read_json(candidates[0].path)
        engine = EnginePackage.from_dict(source_data)
        self._verify_engine_checksum(engine, source_data=source_data)
        return engine

    def clone_current_to_draft(self, new_version: str, created_by: str) -> EnginePackage:
        current = self.load_current()
        draft = current.clone_as_draft(new_version, created_by)
        self.save_draft(draft)
        return draft

    def create_empty_draft(self, engine_id: str, title: str, version: str) -> EnginePackage:
        current = self.load_current()
        draft = EnginePackage(
            engine_id=engine_id,
            title=title,
            version=version,
            state="DRAFT",
            parent_version=None,
            production_ready=False,
            categories=deepcopy(current.categories),
            position_statuses=list(current.position_statuses),
            settings=deepcopy(current.settings),
        )
        self.save_draft(draft)
        return draft

    def create_controlled_shape_revision(
        self,
        engine: EnginePackage,
        shape_code: str,
        *,
        authorized_by: str,
        reason: str,
        phrase: str,
    ) -> ShapeDefinition:
        if phrase != CONTROLLED_REVISION_PHRASE:
            raise EngineRepositoryError("Невалидна фраза за контролирана ревизия.")
        shape = engine.find_shape(shape_code)
        if shape is None:
            raise EngineRepositoryError(f"Форма {shape_code} не е намерена.")
        if not shape.locked:
            return shape
        if not authorized_by.strip() or not reason.strip():
            raise EngineRepositoryError("Изискват се автор и причина за ревизията.")
        shape.revision += 1
        shape.locked = False
        shape.state = "DRAFT"
        shape.controlled_revision = {
            "authorized_by": authorized_by.strip(),
            "reason": reason.strip(),
            "authorized_at": utc_now_iso(),
            "base_fingerprint": self._shape_fingerprint_from_current(shape.code),
            "original_code": shape.code,
        }
        engine.change_log.append(
            {
                "at": utc_now_iso(),
                "action": "CONTROLLED_SHAPE_REVISION",
                "shape": shape.code,
                "revision": shape.revision,
                "by": authorized_by.strip(),
                "reason": reason.strip(),
            }
        )
        return shape

    def approve_shape(self, engine: EnginePackage, shape_code: str, *, approved_by: str, note: str) -> None:
        shape = engine.find_shape(shape_code)
        if shape is None:
            raise EngineRepositoryError(f"Форма {shape_code} не е намерена.")
        if not approved_by.strip():
            raise EngineRepositoryError("Липсва одобрил потребител.")
        shape.state = "APPROVED"
        shape.notes = (shape.notes + "\n" if shape.notes else "") + f"Одобрено от {approved_by}: {note}".strip()
        engine.change_log.append(
            {
                "at": utc_now_iso(),
                "action": "APPROVE_SHAPE",
                "shape": shape.code,
                "revision": shape.revision,
                "by": approved_by,
                "note": note,
            }
        )

    def publish(
        self,
        engine: EnginePackage,
        *,
        approved_by: str,
        approval_note: str,
        phrase: str,
        activate: bool = True,
    ) -> Path:
        if phrase != APPROVAL_PHRASE:
            raise EngineRepositoryError("Невалидна фраза за публикуване.")
        if not approved_by.strip() or not approval_note.strip():
            raise EngineRepositoryError("Изискват се име на одобряващия и бележка за промяната.")
        parent = self._try_load_parent(engine)
        issues = validate_engine(engine, parent=parent, for_publish=True)
        if has_errors(issues):
            raise EngineValidationError(issues)

        published = EnginePackage.from_dict(engine.to_dict())
        published.state = "PUBLISHED"
        published.published_at = utc_now_iso()
        published.updated_at = published.published_at
        published.approved_by = approved_by.strip()
        published.approval_note = approval_note.strip()
        for shape in published.shapes:
            if shape.active and shape.state in {"APPROVED", "LOCKED"}:
                shape.state = "LOCKED"
                shape.locked = True
        published.change_log.append(
            {
                "at": published.published_at,
                "action": "PUBLISH_ENGINE",
                "by": approved_by.strip(),
                "note": approval_note.strip(),
            }
        )
        published.content_sha256 = self._calculate_engine_sha(published)

        path = self.published_dir / self._published_filename(published)
        if path.exists():
            raise EngineRepositoryError(
                f"Версия {published.engine_id} {published.version} вече е публикувана. Използвай нов номер."
            )
        atomic_write_json(path, published.to_dict())
        set_read_only(path)
        self._audit(
            "PUBLISH_ENGINE",
            engine_id=published.engine_id,
            version=published.version,
            sha256=published.content_sha256,
            by=approved_by,
        )
        if activate:
            self._backup_current_pointer()
            self._write_current_pointer(
                published,
                path,
                reason=approval_note,
                by=approved_by,
            )
        return path

    def activate_version(
        self,
        engine_id: str,
        version: str,
        *,
        by: str,
        reason: str,
        phrase: str,
    ) -> None:
        if phrase != ACTIVATION_PHRASE:
            raise EngineRepositoryError("Невалидна фраза за активиране.")
        if not by.strip() or not reason.strip():
            raise EngineRepositoryError("Изискват се име и причина за активиране/връщане назад.")
        candidates = [
            info for info in self.list_published()
            if info.engine_id == engine_id and info.version == version
        ]
        if not candidates:
            raise EngineRepositoryError(f"Няма публикувана версия {engine_id} {version}.")
        path = candidates[0].path
        source_data = read_json(path)
        engine = EnginePackage.from_dict(source_data)
        self._verify_engine_checksum(engine, source_data=source_data)
        self._backup_current_pointer()
        self._write_current_pointer(engine, path, reason=reason, by=by)
        self._audit("ACTIVATE_ENGINE", engine_id=engine_id, version=version, by=by, reason=reason)

    def attach_asset(self, source: Path, role: str, notes: str = "") -> SourceAsset:
        destination = copy_without_overwrite(source, self.assets_dir / safe_filename(source.name))
        asset = SourceAsset(
            role=role,
            filename=source.name,
            sha256=sha256_file(destination),
            relative_path=str(destination.relative_to(self.root)),
            notes=notes,
        )
        self._audit("ATTACH_ASSET", role=role, filename=source.name, sha256=asset.sha256)
        return asset

    def export_bundle(self, engine: EnginePackage, destination: Path) -> Path:
        asset_paths: list[Path] = []
        for asset in engine.source_assets:
            if asset.relative_path:
                candidate = self.root / asset.relative_path
                if candidate.exists():
                    asset_paths.append(candidate)
        path = export_engine_bundle(engine, destination, asset_paths)
        self._audit("EXPORT_ENGINE_BUNDLE", engine_id=engine.engine_id, version=engine.version, path=str(path))
        return path

    def import_bundle_as_draft(self, bundle_path: Path) -> EnginePackage:
        engine, manifest, _ = inspect_engine_bundle(bundle_path)
        import_dir = ensure_directory(self.imports_dir / f"{safe_filename(engine.engine_id)}_{safe_filename(engine.version)}_{uuid.uuid4().hex[:8]}")
        shutil.copy2(bundle_path, import_dir / bundle_path.name)
        extracted = extract_engine_bundle_assets(bundle_path, import_dir / "assets")
        engine.state = "DRAFT"
        engine.published_at = None
        engine.approved_by = None
        engine.approval_note = ""
        engine.content_sha256 = None
        engine.updated_at = utc_now_iso()
        engine.change_log.append(
            {
                "at": utc_now_iso(),
                "action": "IMPORT_ENGINE_BUNDLE_AS_DRAFT",
                "source": bundle_path.name,
                "manifest": manifest,
            }
        )
        for extracted_path in extracted:
            registered = self.attach_asset(extracted_path, role="IMPORTED_BUNDLE_ASSET")
            engine.source_assets.append(registered)
        self.save_draft(engine)
        return engine

    def install_trusted_bundled_engine(
        self,
        bundle_path: Path,
        *,
        allowed_current_versions: set[str],
        expected_target_version: str,
    ) -> bool:
        """Install one Engine carried by a verified managed application release.

        This narrow path is not a replacement for user-driven MASTER Studio
        publishing.  It is used only for a named, regression-tested Engine
        correction shipped inside the application update.  The current pointer
        is backed up and no activation occurs unless the installed Engine is on
        the caller's explicit predecessor allow-list.
        """

        current = self.load_current()
        if current.version == expected_target_version:
            return False
        if current.version not in allowed_current_versions:
            return False

        bootstrap_current = (
            current.engine_id == "lion-rebar-bootstrap"
            and current.version == "0.0.0"
            and not current.production_ready
        )

        candidate, manifest, names = inspect_engine_bundle(Path(bundle_path))
        if candidate.version != expected_target_version:
            raise EngineRepositoryError(
                f"Bundled Engine version is {candidate.version}, expected {expected_target_version}."
            )
        if candidate.engine_id != current.engine_id and not bootstrap_current:
            raise EngineRepositoryError("Bundled Engine id does not match the active Engine.")
        if sorted(names) != ["engine.json", "manifest.json"]:
            raise EngineRepositoryError("Bundled Engine contains unexpected files.")
        if str(manifest.get("engine_version") or "") != candidate.version:
            raise EngineRepositoryError("Bundled Engine manifest version does not match.")
        if not candidate.production_ready:
            raise EngineRepositoryError("Bundled Engine is not marked production-ready.")

        parent = (
            current
            if (
                candidate.engine_id == current.engine_id
                and candidate.parent_version == current.version
            )
            else None
        )
        issues = validate_engine(candidate, parent=parent, for_publish=True)
        if has_errors(issues):
            raise EngineValidationError(issues)
        failed = [result for result in run_engine_tests(candidate) if not result.passed]
        if failed:
            raise EngineRepositoryError(
                "Bundled Engine control tests failed: "
                + "; ".join(result.message for result in failed)
            )

        published = EnginePackage.from_dict(candidate.to_dict())
        published.state = "PUBLISHED"
        published.published_at = utc_now_iso()
        published.updated_at = published.published_at
        published.approved_by = "LION REBAR MANAGED RELEASE"
        published.approval_note = (
            "Trusted bundled Engine correction installed by the managed application update."
        )
        for shape in published.shapes:
            if shape.active and shape.state in {"APPROVED", "LOCKED"}:
                shape.state = "LOCKED"
                shape.locked = True
        published.change_log.append(
            {
                "at": published.published_at,
                "action": "INSTALL_TRUSTED_BUNDLED_ENGINE",
                "from_version": current.version,
                "to_version": published.version,
                "source": Path(bundle_path).name,
            }
        )
        published.content_sha256 = self._calculate_engine_sha(published)

        destination = self.published_dir / self._published_filename(published)
        if destination.exists():
            existing_data = read_json(destination)
            existing = EnginePackage.from_dict(existing_data)
            self._verify_engine_checksum(existing, source_data=existing_data)
            existing_f005 = existing.find_shape("F005")
            if (
                existing.engine_id != candidate.engine_id
                or existing.version != candidate.version
                or existing_f005 is None
                or not existing_f005.active
                or existing_f005.recognition.get("canonical_replacement")
            ):
                raise EngineRepositoryError(
                    f"Published Engine {published.version} already exists with different content."
                )
            published = existing
        else:
            atomic_write_json(destination, published.to_dict())
            set_read_only(destination)

        self._backup_current_pointer()
        self._write_current_pointer(
            published,
            destination,
            reason="TRUSTED_MANAGED_ENGINE_CORRECTION",
            by="LION REBAR MANAGED RELEASE",
        )
        self._audit(
            "INSTALL_TRUSTED_BUNDLED_ENGINE",
            engine_id=published.engine_id,
            from_version=current.version,
            version=published.version,
            sha256=published.content_sha256,
            source=Path(bundle_path).name,
        )
        return True

    def _try_load_parent(self, engine: EnginePackage) -> EnginePackage | None:
        if not engine.parent_version:
            return None
        try:
            return self.load_published(engine.engine_id, engine.parent_version)
        except EngineRepositoryError:
            # Older imported engines may have a parent from a different id. Current is safer than guessing.
            current = self.load_current()
            if current.version == engine.parent_version:
                return current
            return None

    def _shape_fingerprint_from_current(self, shape_code: str) -> str | None:
        from lion_rebar.engine.validation import shape_fingerprint

        current = self.load_current()
        shape = current.find_shape(shape_code)
        return shape_fingerprint(shape) if shape else None

    def _calculate_engine_sha(self, engine: EnginePackage) -> str:
        return sha256_bytes(canonical_json_bytes(engine.to_dict(include_checksum=False)))

    def _verify_engine_checksum(
        self,
        engine: EnginePackage,
        *,
        source_data: dict[str, Any] | None = None,
    ) -> None:
        expected = (
            source_data.get("content_sha256")
            if source_data is not None
            else engine.content_sha256
        )
        if not expected:
            return
        if source_data is None:
            actual = self._calculate_engine_sha(engine)
        else:
            payload = deepcopy(source_data)
            payload.pop("content_sha256", None)
            actual = sha256_bytes(canonical_json_bytes(payload))
        if actual != expected:
            raise EngineRepositoryError(
                f"Checksum на engine {engine.engine_id} {engine.version} не съвпада. Файлът може да е редактиран ръчно."
            )

    def _write_current_pointer(self, engine: EnginePackage, path: Path, *, reason: str, by: str) -> None:
        pointer = {
            "engine_id": engine.engine_id,
            "version": engine.version,
            "relative_path": str(path.relative_to(self.root)),
            "content_sha256": engine.content_sha256,
            "activated_at": utc_now_iso(),
            "activated_by": by,
            "reason": reason,
        }
        atomic_write_json(self.current_file, pointer)

    def _backup_current_pointer(self) -> None:
        if not self.current_file.exists():
            return
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = self.backups_dir / f"current_{timestamp}_{uuid.uuid4().hex[:6]}.json"
        shutil.copy2(self.current_file, backup)

    def _published_filename(self, engine: EnginePackage) -> str:
        return f"{safe_filename(engine.engine_id)}_v{safe_filename(engine.version)}.json"

    def _draft_filename(self, engine: EnginePackage) -> str:
        return f"{safe_filename(engine.engine_id)}_v{safe_filename(engine.version)}.draft.json"

    def _audit(self, action: str, **details: Any) -> None:
        ensure_directory(self.audit_file.parent)
        record = {"at": utc_now_iso(), "action": action, **details}
        with self.audit_file.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
