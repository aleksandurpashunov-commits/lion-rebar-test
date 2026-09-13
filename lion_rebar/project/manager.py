from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from lion_rebar.config.constants import PROJECT_FILENAME, REQUIRED_PROJECT_DIRS
from lion_rebar.engine.models import EnginePackage
from lion_rebar.project.isolation import (
    assert_clean_for_new_project,
    audit_project_isolation,
)
from lion_rebar.project.data_identity import current_identity  # noqa: F401
from lion_rebar.project.data_identity import (
    archive_derived_working_state,
    begin_source_generation,
    current_identity,
    ensure_project_identity,
    stamp_payload,
)
from lion_rebar.project.models import ProjectModel, utc_now_iso
from lion_rebar.utils.files import atomic_write_json, ensure_directory, read_json, safe_filename, sha256_file


class ProjectError(RuntimeError):
    pass


class ProjectManager:
    def create_project(
        self,
        parent_directory: Path,
        project_name: str,
        engine: EnginePackage | None = None,
    ) -> ProjectModel:
        if not project_name.strip():
            raise ProjectError("Името на проекта е задължително.")
        root = parent_directory / safe_filename(project_name, "LION_REBAR_PROJECT")
        if root.exists() and any(root.iterdir()):
            raise ProjectError(f"Папката не е празна: {root}")
        for directory in REQUIRED_PROJECT_DIRS:
            ensure_directory(root / directory)
        project = ProjectModel(name=project_name.strip(), root=str(root))
        ensure_project_identity(project)
        if engine is not None:
            self.pin_engine(project, engine)
        self.save(project)
        self._initialize_working_files(project)
        # ВТОРА ЛИНИЯ. На практика е недостижима, защото проверката
        # "папката не е празна" по-горе вече отказва всичко освен празна
        # директория. Открито при Windows приемане: реалният риск от
        # наследяване е при ОТВАРЯНЕ на съществуващ проект, не при създаване
        # (виж open_project).
        inherited = assert_clean_for_new_project(root)
        if inherited:
            raise ProjectError(
                "Новият проект съдържа наследени production артефакти и не може "
                "да бъде създаден:\n  - " + "\n  - ".join(inherited))
        return project

    def open_project(self, root_or_file: Path) -> ProjectModel:
        project_file = root_or_file
        if root_or_file.is_dir():
            project_file = root_or_file / "working" / PROJECT_FILENAME
        if not project_file.exists():
            raise ProjectError(f"Не е намерен project.json: {project_file}")
        project = ProjectModel.from_dict(read_json(project_file))
        root = project_file.parent.parent.resolve()
        project.root = str(root)
        for directory in REQUIRED_PROJECT_DIRS:
            ensure_directory(root / directory)
        created = ensure_project_identity(project)
        if created:
            self.save(project)
        # ТУК е реалният риск от наследяване: съществуващ проект, чиито
        # working артефакти може да идват от друг проект/поколение (копирана
        # папка, разменени файлове). Проверката ПРЕДУПРЕЖДАВА, не отказва —
        # още не знаем дали копирането на проекти е нормална практика, а
        # забраната на нещо ежедневно чупи работата. Резултатът се излага на
        # UI слоя, който решава какво да покаже.
        if not created:
            try:
                self.last_isolation_report = audit_project_isolation(
                    root, current_identity(project))
            except Exception:  # noqa: BLE001 - одитът не бива да блокира отваряне
                self.last_isolation_report = None
        else:
            self.last_isolation_report = None
        return project

    def save(self, project: ProjectModel) -> Path:
        ensure_project_identity(project)
        project.modified_at = utc_now_iso()
        root = Path(project.root)
        for directory in REQUIRED_PROJECT_DIRS:
            ensure_directory(root / directory)
        project_file = root / "working" / PROJECT_FILENAME
        atomic_write_json(project_file, project.to_dict())
        return project_file

    def pin_engine(self, project: ProjectModel, engine: EnginePackage) -> None:
        project.engine_id = engine.engine_id
        project.engine_version = engine.version
        project.engine_sha256 = engine.content_sha256

    def import_dxf_source(self, project: ProjectModel, source: Path) -> Path:
        if source.suffix.lower() != ".dxf":
            raise ProjectError("Може да се импортира само DXF файл.")
        if not source.exists():
            raise ProjectError(f"DXF файлът не съществува: {source}")

        ensure_project_identity(project)
        root = Path(project.root)
        source_dir = ensure_directory(root / "source")
        destination = source_dir / "original.dxf"
        incoming_sha = sha256_file(source)
        existing_sha = sha256_file(destination) if destination.is_file() else ""
        current = current_identity(project)

        # Re-selecting the byte-identical source is not a new source generation.
        # This keeps valid owner decisions while still allowing a fresh analysis run.
        if destination.is_file() and incoming_sha == existing_sha == current.source_sha256:
            project.source_dxf = str(destination.relative_to(root))
            project.original_source_name = source.name
            project.imported_at = utc_now_iso()
            self.save(project)
            return destination

        same_resolved_path = source.resolve() == destination.resolve()
        staged_source: Path | None = None
        if same_resolved_path:
            # The user may have edited source/original.dxf in place.  Stage its
            # current bytes before the previous generation is archived/backed up.
            handle, staged_name = tempfile.mkstemp(
                prefix=".incoming_source_", suffix=".dxf", dir=str(source_dir)
            )
            os.close(handle)
            staged_source = Path(staged_name)
            try:
                shutil.copy2(source, staged_source)
            except Exception:
                staged_source.unlink(missing_ok=True)
                raise

        # A brand-new project contains only empty scaffolding created before any
        # DXF exists.  Do not archive that as a fake ``legacy`` source
        # generation.  Archive only when there is an actual previous source.
        if destination.is_file() or current.source_sha256:
            archive_derived_working_state(
                root,
                generation_id=current.source_generation_id or None,
            )
        if destination.exists():
            backup_index = 1
            while (source_dir / f"original_previous_{backup_index}.dxf").exists():
                backup_index += 1
            destination.replace(source_dir / f"original_previous_{backup_index}.dxf")
        copy_source = staged_source or source
        try:
            shutil.copy2(copy_source, destination)
        finally:
            if staged_source is not None:
                staged_source.unlink(missing_ok=True)

        copied_sha = sha256_file(destination)
        if copied_sha != incoming_sha:
            raise ProjectError("DXF копието не съвпада с избрания източник.")
        project.source_dxf = str(destination.relative_to(root))
        project.original_source_name = source.name
        project.imported_at = utc_now_iso()
        self._clear_source_derived_metadata(project)
        begin_source_generation(project, source_sha256=copied_sha)
        self.save(project)
        self._initialize_working_files(project, overwrite=True)
        return destination

    def source_dxf_path(self, project: ProjectModel) -> Path | None:
        if not project.source_dxf:
            return None
        path = Path(project.root) / project.source_dxf
        return path if path.exists() else None

    def save_extracted_entities(self, project: ProjectModel, payload: dict[str, Any]) -> Path:
        path = Path(project.root) / "working" / "extracted_entities.json"
        stamped = dict(payload)
        stamp_payload(stamped, current_identity(project))
        atomic_write_json(path, stamped)
        return path

    def save_layer_visibility(self, project: ProjectModel, visibility: dict[str, bool]) -> None:
        project.layer_visibility = dict(visibility)
        self.save(project)

    def save_layer_classification(self, project: ProjectModel, payload: dict[str, Any]) -> Path:
        path = Path(project.root) / "working" / "layer_classification.json"
        stamped = dict(payload)
        stamp_payload(stamped, current_identity(project))
        atomic_write_json(path, stamped)
        project.extensions["layer_classification"] = str(path.relative_to(Path(project.root)))
        project.extensions["layer_classification_summary"] = dict(payload.get("summary") or {})
        self.save(project)
        return path

    def save_engine_snapshot(self, project: ProjectModel, engine: EnginePackage) -> Path:
        self.pin_engine(project, engine)
        path = Path(project.root) / "master" / f"engine_{safe_filename(engine.engine_id)}_v{safe_filename(engine.version)}.json"
        atomic_write_json(path, engine.to_dict())
        project.extensions["engine_snapshot"] = str(path.relative_to(Path(project.root)))
        self.save(project)
        return path

    def migrate_known_engine_snapshot(
        self,
        project: ProjectModel,
        engine: EnginePackage,
        *,
        allowed_from_versions: set[str],
        reason: str,
    ) -> int:
        """Move a project across one explicitly allowed semantic Engine fix.

        The old snapshot remains in ``master`` and the prior project.json plus
        approval file are copied to a migration backup folder.  F003/F005
        approvals are returned to review because their old subtype identity is
        exactly what the migration corrects; unrelated approvals are preserved.
        """

        if project.engine_version == engine.version:
            return 0
        if project.engine_version not in allowed_from_versions:
            raise ProjectError(
                f"Проектът е с Engine v{project.engine_version}; разрешена е миграция само от "
                + ", ".join(sorted(allowed_from_versions))
                + "."
            )
        if project.engine_id and project.engine_id != engine.engine_id:
            raise ProjectError("Engine id на проекта не съвпада с коригирания Engine.")

        root = Path(project.root)
        stamp = utc_now_iso().replace(":", "").replace("+", "_")
        backup_dir = ensure_directory(root / "master" / "migration_backups" / stamp)
        project_file = root / "working" / PROJECT_FILENAME
        if project_file.exists():
            shutil.copy2(project_file, backup_dir / PROJECT_FILENAME)

        approval_path = root / "working" / "approved_geometry_links.json"
        returned_to_review = 0
        if approval_path.exists():
            payload = read_json(approval_path)
            links = [dict(link) for link in payload.get("links", []) if isinstance(link, dict)]
            affected = [
                link
                for link in links
                if str(link.get("master_shape_code") or "").strip().upper()
                in {"F003", "F005"}
            ]
            if affected:
                shutil.copy2(approval_path, backup_dir / approval_path.name)
                affected_handles = {
                    str(link.get("annotation_handle") or "") for link in affected
                }
                payload["links"] = [
                    link
                    for link in links
                    if str(link.get("annotation_handle") or "") not in affected_handles
                ]
                payload["engine_migration"] = {
                    "at": utc_now_iso(),
                    "from_version": project.engine_version,
                    "to_version": engine.version,
                    "reason": reason,
                    "returned_to_review_handles": sorted(affected_handles),
                }
                atomic_write_json(approval_path, payload)
                returned_to_review = len(affected_handles)

        previous_version = project.engine_version
        self.save_engine_snapshot(project, engine)
        migrations = list(project.extensions.get("engine_migrations") or [])
        migrations.append(
            {
                "at": utc_now_iso(),
                "from_version": previous_version,
                "to_version": engine.version,
                "reason": reason,
                "returned_to_review": returned_to_review,
                "backup": str(backup_dir.relative_to(root)),
            }
        )
        project.extensions["engine_migrations"] = migrations
        self.save(project)
        return returned_to_review

    @staticmethod
    def _clear_source_derived_metadata(project: ProjectModel) -> None:
        """Remove metadata that is valid only for the previous DXF generation."""

        project.units = None
        project.units_confidence = None
        project.layer_visibility = {}
        project.import_summary = {}
        project.selected_handle = None
        for key in (
            "latest_batch_report_path",
            "latest_batch_report_generated_at",
            "latest_ai_feedback_bundle",
            "layer_classification",
            "layer_classification_summary",
        ):
            project.extensions.pop(key, None)

    def _initialize_working_files(
        self, project: ProjectModel, *, overwrite: bool = False
    ) -> None:
        working = Path(project.root) / "working"
        defaults = {
            "detected_positions.json": {"schema": "lion-rebar-positions/v1", "positions": []},
            "manual_corrections.json": {"schema": "lion-rebar-corrections/v1", "corrections": []},
            "traceability.json": {"schema": "lion-rebar-traceability/v1", "items": []},
            "layer_classification.json": {
                "schema": "lion-rebar-layer-classification/v1",
                "classifier_version": "1.0",
                "summary": {},
                "layers": [],
            },
        }
        identity = current_identity(project)
        for filename, data in defaults.items():
            path = working / filename
            if overwrite or not path.exists():
                payload = dict(data)
                if identity.source_generation_id:
                    stamp_payload(payload, identity)
                atomic_write_json(path, payload)
