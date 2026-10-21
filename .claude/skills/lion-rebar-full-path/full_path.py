r"""Целият път без интерфейс: импорт → анализ → Финален контрол → експорт.

Взима КОПИЕ на проект (оригиналът не се пипа), минава веригата с кода на
програмата в реда на главния прозорец и записва числата на всеки етап.
Броят одобрени е този от ``build_batch_export_context`` — мярката, която
съвпада с Review Center (CLAUDE.md, раздел 4).

Защо не ``MainWindow``: прозорецът сам търси ``LION_REBAR_CLOUD`` по всички
дискове и пише отчета там, а копие с името на истинския проект би
презаписало ``03_TEST_DXF\<име>_AUTO.dxf``. Тук отчетът отива в
``<копие>\output\04_REVIEW_REPORTS`` — резервният път на самия прозорец
(``main_window.py``, ``run_batch_geometry_analysis``).

Спира там, където спира и програмата, и казва защо. Не мигрира engine, не
решава рамки и греди, не въвежда решения — това са решения на инженера.

Пускане от release/:
    .venv\Scripts\python.exe .claude\skills\lion-rebar-full-path\full_path.py "I:\Моят диск\Тест-желязо\s-9"
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import string
import subprocess
import sys
import time
import traceback
from collections import Counter
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

RELEASE_ROOT = Path(__file__).resolve().parents[3]

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_SAFETY = 2
EXIT_STOPPED = 3

# Заключващи файлове на AutoCAD: не са част от проекта и се сменят сами.
_VOLATILE_SUFFIXES = {".dwl", ".dwl2"}


class Stop(Exception):
    """Верига, спряна от порта на самата програма — резултат, не авария."""

    def __init__(self, code: str, detail: Any = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class SafetyViolation(Exception):
    """Запис извън копието — прогонът не може да се приеме."""


def _jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}


def _read_links(path: Path) -> dict[str, dict]:
    # Същото като MainWindow._read_all_approved_geometry_links.
    payload = _read_json(path)
    links = payload.get("links", []) if isinstance(payload, dict) else []
    result: dict[str, dict] = {}
    for link in links if isinstance(links, list) else []:
        if isinstance(link, dict) and str(link.get("annotation_handle", "")):
            result[str(link["annotation_handle"])] = dict(link)
    return result


def _tree_state(root: Path) -> dict[str, tuple[int, int]]:
    """Размер и mtime на всеки файл.

    Стига, за да хване запис, и е бързо върху стриймващия драйв — хеш на
    120 MB там отнема минути.
    """

    state: dict[str, tuple[int, int]] = {}
    if not root.is_dir():
        return state
    for path in root.rglob("*"):
        try:
            if path.is_file():
                info = path.stat()
                state[path.relative_to(root).as_posix()] = (info.st_size, info.st_mtime_ns)
        except OSError:
            continue
    return state


def _delta(before: dict, after: dict) -> list[str]:
    changed = sorted(set(before) ^ set(after))
    changed += sorted(key for key in set(before) & set(after) if before[key] != after[key])
    return changed


def _cloud_folders() -> list[Path]:
    """Облачните папки, които главният прозорец би намерил.

    Същите места като ``MainWindow._detect_cloud_review_folder`` — за да се
    докаже, че прогонът не ги е пипнал.
    """

    relatives = (
        Path("My Drive") / "LION_REBAR_CLOUD",
        Path("Моят диск") / "LION_REBAR_CLOUD",
        Path("LION_REBAR_CLOUD"),
    )
    bases = [Path.home(), Path.home() / "Google Drive"]
    for variable in ("GOOGLE_DRIVE", "GOOGLEDRIVE", "GDRIVE"):
        if os.environ.get(variable):
            bases.append(Path(os.environ[variable]))
    if os.name == "nt":
        bases.extend(
            Path(f"{letter}:\\")
            for letter in string.ascii_uppercase
            if os.path.exists(f"{letter}:\\")
        )
    found: list[Path] = []
    seen: set[str] = set()
    for base in bases:
        for relative in relatives:
            for sub in ("04_REVIEW_REPORTS", "03_TEST_DXF"):
                candidate = base / relative / sub
                key = str(candidate).casefold()
                if key in seen:
                    continue
                seen.add(key)
                try:
                    if candidate.is_dir():
                        found.append(candidate)
                except OSError:
                    continue
    return found


def _top_level_state(folder: Path) -> dict[str, tuple[int, int]]:
    # Нов отчет е нова папка, презаписан AUTO.dxf сменя mtime — горното ниво стига.
    state: dict[str, tuple[int, int]] = {}
    try:
        for entry in os.scandir(folder):
            info = entry.stat()
            state[entry.name] = (info.st_size if entry.is_file() else -1, info.st_mtime_ns)
    except OSError:
        pass
    return state


def _owner_data_folder() -> Path | None:
    # От Store Python LOCALAPPDATA се вижда пренасочено — точно папката,
    # която ползва програмата, пусната през .venv.
    base = os.environ.get("LOCALAPPDATA")
    return Path(base) / "LionBuild" / "LionRebarDesktop" if base else None


def _copy_ignore(source: Path) -> Callable[[str, list[str]], set[str]]:
    source_key = str(source).casefold()

    def ignore(directory: str, names: list[str]) -> set[str]:
        skipped = {name for name in names if Path(name).suffix.casefold() in _VOLATILE_SUFFIXES}
        # Старите експорти и диагностики не са вход за веригата, а са
        # най-голямата част от проекта.
        if str(Path(directory).resolve()).casefold() == source_key and "output" in names:
            skipped.add("output")
        return skipped

    return ignore


def _inside(path: Path | str | None, root: Path) -> bool:
    if not path:
        return False
    try:
        Path(path).resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _rmtree(path: Path) -> None:
    def make_writable(function, target, _info) -> None:
        os.chmod(target, stat.S_IWRITE)
        function(target)

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=make_writable)
    else:  # pragma: no cover - .venv е 3.12
        shutil.rmtree(path, onerror=make_writable)


def _git_head() -> str:
    try:
        head = subprocess.run(
            ["git", "-C", str(RELEASE_ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", str(RELEASE_ROOT), "status", "--porcelain", "--", "lion_rebar"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        return head + ("+промени в lion_rebar" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return ""


def _политика_за_анализ(settings, target, *, identity, entities, drawing, summary):
    """Политиката на анализа — СЪЩАТА обща логика, която ползва и прозорецът.

    Виж ``lion_rebar/learning/integration.py``. Тук няма отделно поведение:
    разликата между двата пътя е само коя нишка вика цикъла.

    Средата решава режима:
    ``LION_REBAR_LEARNING=off`` не ползва паметта (режим Б);
    ``LION_REBAR_AUTOCYCLE=off`` спира ученето след анализа;
    ``LION_REBAR_NAUCHENO`` сочи към друго хранилище (за честен пренос);
    ``LION_REBAR_HARDCODED_ALIASES=off`` изпразва изброените в кода имена.
    """

    import os

    from lion_rebar.learning.integration import (
        prepare_policy, run_cycle_after_analysis,
    )

    policy, състояние, мост = prepare_policy(
        settings, target, identity=identity, entities=entities, drawing=drawing)
    # Наблюдението НЕ се записва тук: това място е ПРЕДИ анализа. Цикълът се
    # затваря в `_zatvori_ucheben_cikal` след успешния анализ — както прави
    # прозорецът в `_batch_finished`. Версията на паметта, с която анализът
    # работи, е фиксирана точно сега.
    summary["learning"] = {
        **състояние.to_dict(),
        "bridge": {к: v for к, v in (мост or {}).items() if к != "profile_summary"},
        "policy_in_use": {
            поле: list(getattr(policy, поле, ()) or ())
            for поле in ("position_key_circle_layer_aliases",
                         "position_key_table_layer_aliases",
                         "position_key_spec_layer_aliases",
                         "declared_rebar_geometry_layers")
        },
    }
    return policy, състояние, мост


def _zatvori_ucheben_cikal(състояние, мост, *, entities, drawing, summary,
                           source_sha256=""):
    """Записва наблюдението СЛЕД успешния анализ — редът на прозореца.

    Раздел 6 на заданието: наблюдението се записва след успешно ТЕХНИЧЕСКО
    завършване на анализа, а новоприетото важи от СЛЕДВАЩИЯ анализ. Затова
    тази стъпка е тук, а не при подготовката на политиката.
    """

    import os

    from lion_rebar.learning.integration import run_cycle_after_analysis

    ново = run_cycle_after_analysis(
        status=състояние, bridge_report=мост, entities=entities,
        drawing=drawing, family=drawing, source_sha256=source_sha256,
        locked_families=tuple(
            с for с in str(os.environ.get("LION_REBAR_LOCKED_FAMILIES") or "").split(";")
            if с.strip()),
    )
    предишно = dict(summary.get("learning") or {})
    summary["learning"] = {**предишно, **ново.to_dict(),
                           "cycle_closed_after_analysis": True}
    return ново


def run_chain(source: Path, target: Path, summary: dict[str, Any]) -> None:
    # Никакви прозорци на екрана на собственика.
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    sys.path.insert(0, str(RELEASE_ROOT))

    from PySide6.QtWidgets import QApplication

    from lion_rebar.config.constants import APP_VERSION
    from lion_rebar.dxf.batch_geometry_report import count_batch_annotations
    from lion_rebar.dxf.importer import DxfImporter
    from lion_rebar.project.convention_profile_decisions import (
        policy_with_confirmed_profile,
    )
    from lion_rebar.export.batch_auto_output import (
        BatchAutoOutputError,
        build_batch_export_context,
    )
    from lion_rebar.export.excel_export import (
        ExportFrameConfig,
        load_export_context,
        load_pinned_engine,
    )
    from lion_rebar.export.frame_decisions import load_frame_decisions
    from lion_rebar.export.frame_detection import sanitize_sheet_name
    from lion_rebar.project.approval_origin import approval_origin, is_manual_approval
    from lion_rebar.project.data_identity import (
        begin_analysis_run,
        current_identity,
        mismatch_reason,
    )
    from lion_rebar.project.final_control_lifecycle import (
        assert_export_allowed,
        freeze_state,
    )
    from lion_rebar.project.integrity import reconcile_project_integrity
    from lion_rebar.project.manager import ProjectManager
    from lion_rebar.review_center_model import ReviewReportError, load_review_report
    from lion_rebar.session.layer_classifier import (
        classification_by_layer,
        classify_layers,
        filter_analysis_entities,
        high_confidence_noise_layers,
    )
    from lion_rebar.ui.batch_geometry_worker import BatchGeometryWorker
    from lion_rebar.ui.export_worker import ExportWorker

    summary["app_version"] = APP_VERSION
    seconds = summary["seconds"]
    app = QApplication.instance() or QApplication([])

    # --- копие ---------------------------------------------------------
    started = time.perf_counter()
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target, ignore=_copy_ignore(source))
    seconds["copy"] = round(time.perf_counter() - started, 1)

    # --- отваряне (MainWindow.open_project) ------------------------------
    manager = ProjectManager()
    project = manager.open_project(target)
    if Path(project.root).resolve() != target.resolve():
        raise SafetyViolation(f"Проектът се отвори извън копието: {project.root}")
    summary["project"] = {
        "name": project.name,
        "engine_id": project.engine_id,
        "engine_version": project.engine_version,
        "engine_sha256": project.engine_sha256,
        "isolation_report": _jsonable(getattr(manager, "last_isolation_report", None)),
    }
    if project.engine_version in {"0.3.12", "0.3.13"}:
        # Прозорецът мигрира такъв проект при отваряне и връща F003/F005
        # одобрения за нов анализ. Това е инженерна промяна — тук не се прави.
        raise Stop("MIGRATION_REQUIRED", {"engine_version": project.engine_version})
    if not str(project.extensions.get("engine_snapshot") or "").strip():
        # Без закачен snapshot прозорецът взима текущия engine от registry-то.
        # Тук това би било тиха подмяна на engine-а — отказ.
        raise Stop("NO_ENGINE_SNAPSHOT")
    engine = load_pinned_engine(project, None)
    summary["project"]["engine_snapshot_sha256"] = engine.content_sha256
    source_dxf = manager.source_dxf_path(project)
    if source_dxf is None or not source_dxf.is_file():
        raise Stop("NO_SOURCE_DXF", {"source_dxf": project.source_dxf})

    # --- импорт (MainWindow._load_dxf_from_project) ----------------------
    started = time.perf_counter()
    imported = DxfImporter().import_file(source_dxf)
    source_sha256 = str(project.extensions.get("source_sha256") or "") or None
    layer_report = classify_layers(
        imported.entities,
        declared_layers=imported.summary.layers,
        source_sha256=source_sha256,
    )
    layer_roles = classification_by_layer(layer_report)
    previous_visibility = dict(project.layer_visibility or {})
    project.import_summary = imported.summary.to_dict()
    project.units = imported.summary.units_name
    project.layer_visibility = {
        layer: (
            previous_visibility[layer]
            if layer in previous_visibility
            else bool((layer_roles.get(layer) or {}).get("default_visible", True))
        )
        for layer in imported.summary.layers
    }
    manager.save_extracted_entities(project, imported.to_dict())
    manager.save_layer_classification(project, layer_report)
    manager.save(project)

    # MainWindow._analysis_entities: чете записаната класификация от диска.
    layer_payload = _read_json(target / "working" / "layer_classification.json")
    noise_layers = high_confidence_noise_layers(layer_payload)
    analysis_entities = (
        filter_analysis_entities(imported.entities, layer_payload)
        if noise_layers
        else list(imported.entities)
    )
    annotations = count_batch_annotations(analysis_entities, policy=engine.settings)
    summary["import"] = {
        "entities": len(imported.entities),
        "layers": len(imported.summary.layers),
        "units": imported.summary.units_name,
        "noise_layers": sorted(noise_layers),
        "analysis_entities": len(analysis_entities),
        "annotations": annotations,
    }
    seconds["import"] = round(time.perf_counter() - started, 1)
    if annotations <= 0:
        raise Stop("NO_ANNOTATIONS")

    # --- анализ и автоматично одобрение (run_batch_geometry_analysis) -----
    started = time.perf_counter()
    output_parent = target / "output" / "04_REVIEW_REPORTS"
    output_parent.mkdir(parents=True, exist_ok=True)
    approved_links = _read_links(target / "working" / "approved_geometry_links.json")
    # Самоличността на прогона: без нея резултатът не сочи към конкретно
    # изпълнение и сравненията не са възпроизводими (етап 4 направи цяла
    # таблица „с памет" с празно хранилище, без това да личи никъде).
    from lion_rebar.diagnostics.run_registry import begin_run, finish_run

    _източен_dxf = manager.source_dxf_path(project)
    summary["run"] = begin_run(
        code_root=RELEASE_ROOT,
        inputs=[_източен_dxf] if _източен_dxf else (),
        label=project.name,
    )

    try:
        identity = begin_analysis_run(project)
    except ValueError as exc:
        raise Stop("NO_ANALYSIS_RUN", str(exc)) from exc
    manager.save(project)
    summary["project"]["identity"] = identity.to_dict()

    # Политиката се готви ПРЕДИ анализа: версията на паметта, с която анализът
    # работи, се фиксира сега и не се мени по време на прогона.
    политика_за_анализа, учебно_състояние, учебен_мост = _политика_за_анализ(
        engine.settings, target, identity=identity,
        entities=analysis_entities, drawing=project.name, summary=summary,
    )
    worker = BatchGeometryWorker(
        entities=analysis_entities,
        output_parent=output_parent,
        project_name=project.name,
        approved_links=approved_links,
        shape_definitions=engine.shapes,
        control_tests=engine.tests,
        # Както в прозореца: политиката на engine-а, наслоена с
        # потвърдения профил на чертежа (обявени слоеве).
        policy=политика_за_анализа,
        engine_metadata={
            "engine_id": engine.engine_id,
            "engine_version": engine.version,
            "content_sha256": engine.content_sha256,
        },
        project=project,
        engine=engine,
        source_dxf_path=manager.source_dxf_path(project),
        data_identity=identity,
    )
    finished: list[Any] = []
    failed: list[tuple[str, str]] = []
    worker.finished.connect(finished.append)
    worker.failed.connect(lambda message, trace: failed.append((message, trace)))
    worker.run()
    app.processEvents()
    seconds["analysis"] = round(time.perf_counter() - started, 1)
    if failed or not finished:
        message, trace = failed[0] if failed else ("worker не върна резултат", "")
        raise Stop("BATCH_FAILED", {"message": message, "traceback": trace})
    batch = finished[0]
    report_path = Path(batch.json_path)
    if not _inside(report_path, target):
        raise SafetyViolation(f"Отчетът е извън копието: {report_path}")
    # MainWindow._batch_finished
    project.extensions["latest_batch_report_path"] = str(report_path)
    project.extensions["latest_batch_report_generated_at"] = (
        datetime.now().astimezone().isoformat(timespec="seconds")
    )
    manager.save(project)
    summary["analysis"] = {
        "report": report_path.relative_to(target).as_posix(),
        "total_annotations": batch.total_annotations,
        "processed_annotations": batch.processed_annotations,
        "actionable": batch.actionable_count,
        "strong": batch.strong_count,
        "review": batch.review_count,
        "risk": batch.risk_count,
        "no_candidate": batch.no_candidate_count,
        "errors": batch.error_count,
        "cancelled": batch.cancelled,
    }
    auto = batch.auto_approval
    summary["auto_approval"] = _jsonable(auto) if auto is not None else None
    summary["auto_approval_error"] = str(batch.auto_approval_error or "")
    if batch.cancelled:
        finish_run(summary["run"], outcome="BATCH_CANCELLED")
        raise Stop("BATCH_CANCELLED")

    # Анализът завърши технически успешно -> едва сега се записва наблюдението.
    # Новоприетото важи от СЛЕДВАЩИЯ анализ, не от този.
    _zatvori_ucheben_cikal(
        учебно_състояние, учебен_мост,
        entities=analysis_entities, drawing=project.name, summary=summary,
        # Самоличността на ИЗХОДНИЯ DXF, не името на проекта: копие под друго
        # име не бива да се брои като второ доказателство.
        source_sha256=str(getattr(identity, "source_sha256", "") or ""),
    )

    # --- авторитетният брой (auto_approval.py, auto_approve_gate_positions) -
    started = time.perf_counter()
    links_after = _read_links(target / "working" / "approved_geometry_links.json")
    manual_links = {handle: link for handle, link in links_after.items() if is_manual_approval(link)}
    try:
        context = build_batch_export_context(
            report_path,
            entities=analysis_entities,
            engine=engine,
            approved_links=manual_links,
            frame_decisions=load_frame_decisions(target, identity),
        )
        # Една блокирана позиция носи няколко причини — броят се причините,
        # а позициите се изброяват поименно.
        reasons = Counter(code for blocker in context.blockers for code in blocker.reason_codes)
        summary["gate"] = {
            "actionable": context.actionable_count,
            "approved": len(context.positions),
            "blocked": len(context.blockers),
            "complete": context.complete,
            "blocker_reasons": dict(reasons),
            "blocked_positions": [
                {
                    "handle": blocker.annotation_handle,
                    "text": blocker.annotation_text,
                    "reasons": list(blocker.reason_codes),
                }
                # Отрязването беше на 50 и точно то направи невъзможна поименната
                # база: `Тест-002` има 98 спрени, `d-2` — 54, затова 52 позиции
                # изобщо не се явяваха в отчета. Полето `blocked` носи верния
                # брой, но списъкът — не, и всяко броене по списъка се насищаше
                # (обобщението на прогоните даваше 197 вместо 249).
                # 500 е с голям запас над най-лошия измерен чертеж, а целият
                # списък е по-малък от един ред геометрия.
                for blocker in context.blockers[:500]
            ],
            "manual_links": len(manual_links),
            "all_links": len(links_after),
        }
        if auto is not None and auto.gate_passed_count != len(context.positions):
            summary["notes"].append(
                "Разминаване: auto_approval.gate_passed_count="
                f"{auto.gate_passed_count}, а build_batch_export_context дава "
                f"{len(context.positions)}."
            )
    except BatchAutoOutputError as exc:
        # „Няма брой", не нула — функцията отказва при UNKNOWN рамка и др.
        summary["gate"] = {"error": str(exc), "manual_links": len(manual_links), "all_links": len(links_after)}
    seconds["gate"] = round(time.perf_counter() - started, 1)

    review_state = _read_json(target / "working" / "review_center_state.json")
    decisions = review_state.get("decisions") if isinstance(review_state, dict) else None
    if isinstance(decisions, dict) and decisions:
        carried = Counter(
            str((item or {}).get("status") or (item or {}).get("decision") or "?")
            for item in decisions.values()
            if isinstance(item, dict) or item is None
        )
        summary["carried_review_decisions"] = dict(carried)
        summary["notes"].append(
            f"Пренесени решения от review_center_state.json: {sum(carried.values())}. "
            "В прозореца отварянето на Review Center с нов отчет може да ги "
            "нулира (по код, непроверено) — тук остават."
        )

    # --- Финален контрол (MainWindow._run_final_integrity_for_export) -------
    started = time.perf_counter()
    if not _report_matches_project(
        report_path, project, load_review_report, ReviewReportError,
        current_identity, mismatch_reason,
    ):
        raise Stop("REPORT_IDENTITY_MISMATCH")
    integrity = reconcile_project_integrity(target, report_path=report_path, apply=True)
    lifecycle_blockers = (
        assert_export_allowed(target) if integrity.full_specification_complete else []
    )
    error_codes = Counter(
        issue.code for issue in integrity.issues if str(issue.severity).upper() == "ERROR"
    )
    summary["final_control"] = {
        "integrity_passed": integrity.integrity_passed,
        "full_specification_complete": integrity.full_specification_complete,
        "actionable_positions": integrity.actionable_positions,
        "approved_after": integrity.approved_after,
        "non_production_positions": integrity.non_production_positions,
        "unresolved_positions": integrity.unresolved_positions,
        "safe_confirmation_pending": integrity.safe_confirmation_pending,
        "engineering_decisions_pending": integrity.engineering_decisions_pending,
        "explicit_exclusions": integrity.explicit_exclusions,
        "identity_failures": integrity.identity_failures,
        "duplicate_groups_remaining": integrity.duplicate_groups_remaining,
        "beam_assignments_frozen": integrity.beam_assignments_frozen,
        "vertical_assignments_frozen": integrity.vertical_assignments_frozen,
        "vertical_sections_frozen": integrity.vertical_sections_frozen,
        "export_preflight_error": integrity.export_preflight_error,
        "error_issue_codes": dict(error_codes),
        "lifecycle_blockers": list(lifecycle_blockers),
        "freeze_state": list(freeze_state(target)),
        # Портата и Финалният контрол броят различно: решение на инженера може
        # да мине контрола, макар портата да блокира позицията. Произходът на
        # връзките показва откъде идва разликата, вместо да се гадае.
        "links_by_origin": dict(
            Counter(
                approval_origin(link)
                for link in _read_links(target / "working" / "approved_geometry_links.json").values()
            )
        ),
    }
    seconds["final_control"] = round(time.perf_counter() - started, 1)
    if not (integrity.full_specification_complete and not lifecycle_blockers):
        raise Stop("FINAL_CONTROL_BLOCKED", {"error_issue_codes": dict(error_codes)})

    # --- експорт (MainWindow.run_excel_export) ----------------------------
    started = time.perf_counter()
    frames, positions = load_export_context(project, imported.entities, engine)
    # Имената на листовете се вземат от замразената конфигурация — нея
    # generate_excel_export(require_frozen_config=True) сверява. Отделно се
    # проверява дали подразбиращите се стойности на диалога съвпадат с нея:
    # ако не, операторът би трябвало да ги напише на ръка.
    frozen = _read_json(target / "working" / "final_control_export_config.json")
    frozen_rows = [row for row in frozen.get("frames", []) if isinstance(row, dict)] if isinstance(frozen, dict) else []
    configs = [
        ExportFrameConfig(
            frame_index=int(row["frame_index"]),
            category_code=str(row["category_code"]),
            sheet_name=str(row["sheet_name"]),
            title_text=str(row["title_text"]),
            confirmed=True,
        )
        for row in frozen_rows
    ]
    dialog_defaults = [
        (frame.index, str(frame.category_code), sanitize_sheet_name(frame.suggested_sheet_name, fallback=f"лист {frame.index}"))
        for frame in frames
    ]
    frozen_keys = [
        (int(row["frame_index"]), str(row["category_code"]), str(row["sheet_name"]))
        for row in frozen_rows
    ]
    export_dir = target / "output" / "headless_export"
    excel_path = export_dir / f"{project.name}_СПЕЦИФИКАЦИЯ.xlsx"
    export_worker = ExportWorker(
        project=project,
        engine=engine,
        entities=imported.entities,
        frames=frames,
        positions=positions,
        configs=configs,
        excel_path=excel_path,
    )
    done: list[Any] = []
    export_failed: list[tuple[str, str, str]] = []
    export_worker.finished.connect(done.append)
    export_worker.failed.connect(lambda kind, message, trace: export_failed.append((kind, message, trace)))
    export_worker.run()
    app.processEvents()
    seconds["export"] = round(time.perf_counter() - started, 1)
    summary["export"] = {
        "frames": len(frames),
        "positions": len(positions),
        "dialog_defaults_match_frozen_config": dialog_defaults == frozen_keys,
    }
    if export_failed or not done:
        kind, message, trace = export_failed[0] if export_failed else ("", "worker не върна резултат", "")
        raise Stop("EXPORT_FAILED", {"kind": kind, "message": message, "traceback": trace})
    result = done[0]
    files = {
        "excel": result.excel_path,
        "excel_trace": result.excel_trace_path,
        "dxf": result.dxf_path,
        "dxf_trace": result.dxf_trace_path,
        "manifest": result.manifest_path,
    }
    outside = [role for role, path in files.items() if path and not _inside(path, target)]
    if outside:
        raise SafetyViolation(f"Експорт извън копието: {outside}")
    summary["export"].update(
        {
            "status": result.status,
            "production_verified": result.production_verified,
            "verification_problems": list(result.verification_problems),
            "excel_frames": result.excel_result.frame_count,
            "excel_positions": result.excel_result.safe_position_count,
            "excel_grouped_rows": result.excel_result.grouped_row_count,
            "dxf_markers": result.dxf_result.marker_count,
            "dxf_unique_positions": result.dxf_result.unique_position_count,
            "files": {role: Path(path).relative_to(target).as_posix() for role, path in files.items() if path},
        }
    )
    if not result.production_verified:
        raise Stop("EXPORT_NOT_VERIFIED", list(result.verification_problems))


def _report_matches_project(
    path: Path, project: Any, load_review_report: Any, report_error: Any,
    current_identity: Any, mismatch_reason: Any,
) -> bool:
    # Същото като MainWindow._review_report_matches_current_project.
    try:
        report = load_review_report(path)
    except report_error:
        return False
    if report.project_name and report.project_name != project.name:
        return False
    identity = current_identity(project)
    if not identity.source_generation_id:
        return True
    if not identity.analysis_run_id:
        return False
    payload = _read_json(path)
    if not isinstance(payload, dict) or mismatch_reason(payload, identity, require_run=True):
        return False
    rows = payload.get("rows")
    if not isinstance(rows, list):
        return False
    return all(
        isinstance(row, dict) and not mismatch_reason(row, identity, require_run=True)
        for row in rows
    )


def _print_summary(summary: dict[str, Any], summary_path: Path) -> None:
    def get(section: str, key: str, default: Any = "—") -> Any:
        value = (summary.get(section) or {}).get(key, default)
        return default if value is None else value

    project = summary.get("project") or {}
    lines = [
        f"ПРОЕКТ       {project.get('name', '—')}  ({summary['source_project']})",
        f"КОПИЕ        {summary['copy']}",
        f"ENGINE       {project.get('engine_id', '—')} {project.get('engine_version', '—')} "
        f"({str(project.get('engine_sha256') or '')[:12]})",
    ]
    if summary.get("import"):
        lines.append(
            f"ИМПОРТ       {get('import', 'entities')} обекта, {get('import', 'layers')} слоя, "
            f"{len(get('import', 'noise_layers', []))} шумови → {get('import', 'analysis_entities')} за анализ, "
            f"{get('import', 'annotations')} надписа"
        )
    if summary.get("analysis"):
        lines.append(
            f"АНАЛИЗ       {get('analysis', 'total_annotations')} надписа, "
            f"{get('analysis', 'actionable')} за действие, {get('analysis', 'errors')} грешки"
        )
    auto = summary.get("auto_approval")
    if auto:
        lines.append(
            f"ОДОБРЕНИЕ    минали портата {auto.get('gate_passed_count')}, нови AUTO {auto.get('newly_approved_count')}, "
            f"ръчни {auto.get('kept_manual_count')}, блокирани {auto.get('blocked_count')} {auto.get('blocked_reason_counts') or ''}"
        )
    elif summary.get("auto_approval_error"):
        lines.append(f"ОДОБРЕНИЕ    грешка: {summary['auto_approval_error']}")
    gate = summary.get("gate") or {}
    if gate.get("error"):
        lines.append(f"ОДОБРЕНИ     няма брой: {gate['error']}")
    elif gate:
        lines.append(
            f"ПОРТА        {gate.get('approved')} от {gate.get('actionable')} минават "
            f"(build_batch_export_context), блокирани {gate.get('blocked')} {gate.get('blocker_reasons') or ''}"
        )
    final = summary.get("final_control") or {}
    if final:
        lines.append(
            f"ФИНАЛЕН      пълна спецификация: {'да' if final.get('full_specification_complete') else 'НЕ'}; "
            f"{final.get('approved_after')} производствени {final.get('links_by_origin') or ''} + "
            f"{final.get('non_production_positions')} непроизводствени от {final.get('actionable_positions')}; "
            f"нерешени {final.get('unresolved_positions')}"
            + (f"; грешки {final.get('error_issue_codes')}" if final.get("error_issue_codes") else "")
        )
    export = summary.get("export") or {}
    if export.get("status"):
        lines.append(
            f"ЕКСПОРТ      {export['status']}; {export.get('excel_positions')} позиции в Excel, "
            f"{export.get('dxf_markers')} маркера в DXF"
            + ("" if export.get("dialog_defaults_match_frozen_config") else "; диалогът НЕ съвпада със замразените листове")
        )
    safety = summary.get("safety") or {}
    if safety:
        lines.append(
            "БЕЗОПАСНОСТ  оригинал: " + ("непокътнат" if safety.get("source_unchanged") else "ПРОМЕНЕН")
            + "; облак: " + ("непокътнат" if safety.get("cloud_unchanged") else "ПРОМЕНЕН")
            + "; данни на собственика: " + ("без промяна" if safety.get("owner_data_unchanged") else "ПРОМЕНЕНИ")
        )
    status = summary["status"] + (f" {summary['stop_code']}" if summary.get("stop_code") else "")
    lines.append(f"СТАТУС       {status} за {summary['seconds'].get('total', '—')} s {summary['seconds']}")
    for note in summary.get("notes") or []:
        lines.append(f"БЕЛЕЖКА      {note}")
    lines.append(f"ОТЧЕТ        {summary_path}")
    print("\n".join(lines))


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Целият път без интерфейс върху копие на проект.")
    parser.add_argument("project", type=Path, help="папка на проекта с working\\project.json; не се променя")
    parser.add_argument("--work-root", type=Path, help="къде да е копието (по подразбиране <LION_REBAR_DATA_ROOT>\\full_path_runs)")
    parser.add_argument("--json", type=Path, help="допълнително копие на отчета")
    parser.add_argument("--discard-copy", action="store_true", help="изтрий копието, след като отчетът е записан")
    args = parser.parse_args(argv)

    data_root = os.environ.get("LION_REBAR_DATA_ROOT")
    if not data_root:
        print("LION_REBAR_DATA_ROOT не е зададен — отказ (виж .claude/settings.json).", file=sys.stderr)
        return EXIT_SAFETY
    source = args.project.resolve()
    if not (source / "working" / "project.json").is_file():
        print(f"Няма working\\project.json в {source}", file=sys.stderr)
        return EXIT_ERROR
    work_root = (args.work_root or Path(data_root).expanduser() / "full_path_runs").resolve()
    if _inside(work_root, source):
        print("Копието не може да е вътре в оригинала.", file=sys.stderr)
        return EXIT_SAFETY
    target = work_root / f"{source.name}_{datetime.now():%Y%m%d_%H%M%S}"
    summary_path = work_root / f"{target.name}.summary.json"

    summary: dict[str, Any] = {
        "schema": "lion-rebar-full-path/v1",
        "release_root": str(RELEASE_ROOT),
        "git_head": _git_head(),
        "source_project": str(source),
        "copy": str(target),
        "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "status": "ERROR",
        "stop_code": "",
        "stop_detail": None,
        "notes": [],
        "seconds": {},
    }
    cloud = _cloud_folders()
    owner = _owner_data_folder()
    before_source = _tree_state(source)
    before_cloud = {str(folder): _top_level_state(folder) for folder in cloud}
    before_owner = _tree_state(owner) if owner else {}
    total_started = time.perf_counter()
    exit_code = EXIT_ERROR
    try:
        run_chain(source, target, summary)
        summary["status"] = "OK"
        exit_code = EXIT_OK
    except Stop as stop:
        summary["status"] = "STOPPED"
        summary["stop_code"] = stop.code
        summary["stop_detail"] = _jsonable(stop.detail)
        exit_code = EXIT_STOPPED
    except SafetyViolation as violation:
        summary["status"] = "SAFETY_VIOLATION"
        summary["stop_detail"] = str(violation)
        exit_code = EXIT_SAFETY
    except Exception as exc:  # noqa: BLE001 - отчетът трябва да се запише винаги
        summary["status"] = "ERROR"
        summary["stop_detail"] = {"error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()}
        exit_code = EXIT_ERROR
    finally:
        summary["seconds"]["total"] = round(time.perf_counter() - total_started, 1)
        source_changed = _delta(before_source, _tree_state(source))
        cloud_changed = [
            f"{folder}\\{name}"
            for folder in cloud
            for name in _delta(before_cloud[str(folder)], _top_level_state(folder))
        ]
        owner_changed = _delta(before_owner, _tree_state(owner)) if owner else []
        summary["safety"] = {
            "source_unchanged": not source_changed,
            "source_changed": source_changed[:50],
            "cloud_unchanged": not cloud_changed,
            "cloud_changed": cloud_changed[:50],
            "owner_data_unchanged": not owner_changed,
            "owner_data_changed": owner_changed[:50],
        }
        if source_changed or cloud_changed:
            # Скриптът пише само в копието. Промяна отвън значи или грешка тук,
            # или отворената програма на собственика — и двете трябва да се видят.
            summary["notes"].append(
                "Промяна извън копието по време на прогона. Ако програмата на "
                "собственика е била отворена, провери дали не е тя."
            )
            if exit_code != EXIT_ERROR:
                exit_code = EXIT_SAFETY
                summary["status"] = "SAFETY_VIOLATION"
        summary["finished_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        summary["exit_code"] = exit_code
        # Затваря самоличността на прогона: памет СЛЕД анализа и изход. Прави се
        # тук, за да важи и когато прогонът е спрян от порта.
        if isinstance(summary.get("run"), dict):
            try:
                from lion_rebar.diagnostics.run_registry import finish_run

                finish_run(summary["run"],
                           outcome=str(summary.get("status") or ""))
            except Exception as грешка:   # noqa: BLE001 — регистърът не вали прогон
                summary["run"]["error"] = "%s: %s" % (type(грешка).__name__, грешка)
        work_root.mkdir(parents=True, exist_ok=True)
        text = json.dumps(_jsonable(summary), ensure_ascii=False, indent=2)
        summary_path.write_text(text, encoding="utf-8")
        if args.json:
            args.json.parent.mkdir(parents=True, exist_ok=True)
            args.json.write_text(text, encoding="utf-8")
        if args.discard_copy and target.exists():
            _rmtree(target)
            summary["notes"].append("Копието е изтрито след отчета (--discard-copy).")
        _print_summary(summary, summary_path)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
