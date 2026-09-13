from __future__ import annotations

import logging
import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QGraphicsItem, QWidget

from lion_rebar.dxf.models import (
    DxfEntityRecord,
    DxfImportResult,
    DxfImportSummary,
)
from lion_rebar.engine.package_io import inspect_engine_bundle
from lion_rebar.engine.repository import EngineRepositoryError
from lion_rebar.manual_assignment import (
    ManualGeometryError,
    SUPPORTED_MANUAL_GEOMETRY,
)
from lion_rebar.project.data_identity import (
    begin_source_generation,
    current_identity,
)
from lion_rebar.project.models import ProjectModel
from lion_rebar.ui.drawing_view import DrawingView, build_scene
import lion_rebar.ui.main_window as main_window_module
from lion_rebar.ui.main_window import MainWindow


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _engine():
    path = (
        Path(__file__).resolve().parents[1]
        / "engine_updates"
        / "lion-rebar-master_v0.3.14_F005_INDEPENDENT_ARMS_PRODUCTION_DRAFT.lionengine"
    )
    return inspect_engine_bundle(path)[0]


class _RepositoryStub:
    def __init__(self):
        self.engine = _engine()

    def load_current(self):
        return self.engine


class _FailingRepositoryStub:
    def load_current(self):
        raise EngineRepositoryError("engine unavailable")


class _RecoveringRepositoryStub:
    def __init__(self):
        self.engine = _engine()
        self.available = False
        self.calls = 0

    def load_current(self):
        self.calls += 1
        if not self.available:
            raise EngineRepositoryError("engine temporarily unavailable")
        return self.engine


class _SessionStub:
    has_active_session = False

    def active_summary(self):
        return None

    def record_system_event(self, *_args, **_kwargs) -> None:
        return None


class _ReviewStub(QWidget):
    def __init__(self):
        super().__init__()
        self.errors: list[str] = []
        self.geometry_selection_states: list[bool] = []

    def mark_error(self, message: str) -> None:
        self.errors.append(str(message))

    def set_geometry_selection_active(self, active: bool, *_args) -> None:
        self.geometry_selection_states.append(bool(active))


def _line(handle: str, start, end, *, layer: str = "ARM-GORNA"):
    return DxfEntityRecord(
        handle=handle,
        entity_type="LINE",
        layer=layer,
        geometry={"start": [*start, 0.0], "end": [*end, 0.0]},
    )


def _guard_entities() -> list[DxfEntityRecord]:
    return [
        # One exact F004 global legend geometry, using the real H-3 handles.
        _line("706D", (0.0, 0.0), (360.0, 0.0)),
        _line("7070", (360.0, 0.0), (360.0, 20.0)),
        DxfEntityRecord(
            handle="LEGEND-SPEC",
            entity_type="TEXT",
            layer="ARMTXT1",
            text="N20 - L=380",
            geometry={"insert": [0.0, -7.0, 0.0]},
        ),
        DxfEntityRecord(
            handle="LEGEND-KEY",
            entity_type="TEXT",
            layer="0.4",
            text="3b*",
            geometry={"insert": [-20.0, -20.0, 0.0]},
        ),
        # The schematic outline from the H-3 screenshot is a supported Qt
        # entity type, but not an authoritative rebar primitive.
        DxfEntityRecord(
            handle="6FD7",
            entity_type="LWPOLYLINE",
            layer="Shema Blockove",
            geometry={
                "points": [
                    [4085.576, 1253.007, 0.0],
                    [5719.375, 1253.007, 0.0],
                    [5719.375, 1908.265, 0.0],
                ],
                "closed": False,
            },
        ),
        _line("VALID-REBAR", (1000.0, 0.0), (1100.0, 0.0)),
        DxfEntityRecord(
            handle="ANN",
            entity_type="BLOCK_ATTR_TEXT",
            layer="ARMTXT",
            text="1N20x380",
            geometry={"insert": [1500.0, 0.0, 0.0]},
        ),
    ]


def _import_result(records: list[DxfEntityRecord], filename: str) -> DxfImportResult:
    layers = sorted({record.layer for record in records})
    return DxfImportResult(
        summary=DxfImportSummary(
            filename=filename,
            dxf_version="AC1032",
            units_code=6,
            units_name="Meters",
            layers=layers,
            entity_counts={},
            total_entities=len(records),
            text_entities=sum(
                record.entity_type in {"TEXT", "MTEXT", "BLOCK_ATTR_TEXT"}
                for record in records
            ),
            potential_rebar_texts=1,
            block_count=0,
        ),
        entities=records,
    )


class _ImporterStub:
    result: DxfImportResult | None = None

    def import_file(self, _path: Path) -> DxfImportResult:
        assert self.result is not None
        return self.result


def _is_selectable(item) -> bool:
    return bool(item.flags() & QGraphicsItem.ItemIsSelectable)


def test_drawing_view_handle_filter_deselects_immediately_and_reset_restores() -> None:
    _app()
    records = _guard_entities()
    built = build_scene(records)
    view = DrawingView()
    view.setScene(built.scene)
    for handle in ("6FD7", "706D", "VALID-REBAR"):
        built.handle_to_item[handle].setSelected(True)

    view.set_selection_entity_filter(
        SUPPORTED_MANUAL_GEOMETRY,
        allowed_handles={"VALID-REBAR"},
    )

    assert not _is_selectable(built.handle_to_item["6FD7"])
    assert not _is_selectable(built.handle_to_item["706D"])
    assert _is_selectable(built.handle_to_item["VALID-REBAR"])
    assert not built.handle_to_item["6FD7"].isSelected()
    assert not built.handle_to_item["706D"].isSelected()
    assert built.handle_to_item["VALID-REBAR"].isSelected()
    assert view._selection_allowed_handles == frozenset({"VALID-REBAR"})

    view.set_selection_entity_filter(None)

    assert view._selection_entity_filter is None
    assert view._selection_allowed_handles is None
    assert _is_selectable(built.handle_to_item["6FD7"])
    assert _is_selectable(built.handle_to_item["706D"])
    assert _is_selectable(built.handle_to_item["VALID-REBAR"])
    view.setScene(None)
    view.close()


def test_main_window_blocks_schematic_and_shared_legend_but_allows_rebar(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app = _app()
    records = _guard_entities()
    built = build_scene(records)
    window = MainWindow(
        _RepositoryStub(),
        logging.getLogger("rc42-manual-geometry-guard"),
        session_service=_SessionStub(),
    )
    window.project = ProjectModel(name="GUARD", root=str(tmp_path))
    window.import_result = SimpleNamespace(entities=records)
    window.scene_result = built
    window.drawing_view.setScene(built.scene)
    window._invalidate_manual_geometry_guard_cache()

    allowed, protected = window._manual_geometry_guard_handles()
    assert {"706D", "7070", "VALID-REBAR"} <= allowed
    assert {"706D", "7070"} <= protected
    assert "6FD7" not in allowed

    for handle in ("6FD7", "706D", "7070", "VALID-REBAR"):
        built.handle_to_item[handle].setSelected(True)
    window._begin_manual_geometry_selection(
        {"annotation_handle": "ANN", "annotation_text": "1N20x380"}
    )
    app.processEvents()

    assert window.drawing_view._selection_allowed_handles == frozenset(
        {"VALID-REBAR"}
    )
    for handle in ("6FD7", "706D", "7070"):
        assert not _is_selectable(built.handle_to_item[handle])
        assert not built.handle_to_item[handle].isSelected()
    assert _is_selectable(built.handle_to_item["VALID-REBAR"])
    assert built.handle_to_item["VALID-REBAR"].isSelected()

    with pytest.raises(ManualGeometryError, match="6FD7"):
        window._validate_manual_geometry_records(
            [built.handle_to_record["6FD7"]]
        )
    with pytest.raises(ManualGeometryError, match="обща геометрия на легендата"):
        window._validate_manual_geometry_records(
            [built.handle_to_record["706D"], built.handle_to_record["7070"]]
        )
    assert window._validate_manual_geometry_records(
        [built.handle_to_record["VALID-REBAR"]]
    ) == (built.handle_to_record["VALID-REBAR"],)

    # Simulate a caller bypassing the Qt flags and passing the raw schematic
    # object into the normal save path.  Backend validation must run before the
    # geometry summarizer/editor.
    window._leave_manual_geometry_selection(
        restore_review=False,
        clear_selection=True,
    )
    built.handle_to_item["6FD7"].setSelected(True)
    review = _ReviewStub()
    window._review_center = review
    summarize_called = False

    def forbidden_summary(*_args, **_kwargs):
        nonlocal summarize_called
        summarize_called = True
        raise AssertionError("summarizer must not receive forbidden geometry")

    monkeypatch.setattr(
        main_window_module,
        "summarize_selected_geometry",
        forbidden_summary,
    )
    window._review_assign_form(
        {"annotation_handle": "ANN", "annotation_text": "1N20x380"}
    )

    assert summarize_called is False
    assert review.errors and "6FD7" in review.errors[-1]

    window._review_center = None
    review.close()
    window.scene_result = None
    window.import_result = None
    window.project = None
    window.close()


def test_real_project_engine_failure_disables_all_manual_selection(
    tmp_path: Path,
) -> None:
    _app()
    records = _guard_entities()
    built = build_scene(records)
    window = MainWindow(
        _FailingRepositoryStub(),
        logging.getLogger("rc42-manual-geometry-fail-closed"),
        session_service=_SessionStub(),
    )
    window.project = ProjectModel(name="GUARD-FAIL", root=str(tmp_path))
    window.import_result = SimpleNamespace(entities=records)
    window.scene_result = built
    window.drawing_view.setScene(built.scene)
    window._invalidate_manual_geometry_guard_cache()

    allowed, protected = window._manual_geometry_guard_handles()

    assert allowed == frozenset()
    assert protected == frozenset()
    assert window._manual_geometry_guard_error is not None
    window._begin_manual_geometry_selection(
        {"annotation_handle": "ANN", "annotation_text": "1N20x380"}
    )
    assert window.drawing_view._selection_allowed_handles == frozenset()
    assert not _is_selectable(built.handle_to_item["VALID-REBAR"])
    assert "блокиран" in window.statusBar().currentMessage()

    window._leave_manual_geometry_selection(
        restore_review=False,
        clear_selection=True,
    )
    window.scene_result = None
    window.import_result = None
    window.project = None
    window.close()


def test_dxf_reload_closes_old_manual_review_and_rejects_stale_row(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app = _app()
    old_records = _guard_entities()
    old_built = build_scene(old_records)
    window = MainWindow(
        _RepositoryStub(),
        logging.getLogger("rc42-manual-reload-lifecycle"),
        session_service=_SessionStub(),
    )
    window.project = ProjectModel(name="RELOAD", root=str(tmp_path))
    window.import_result = _import_result(old_records, "old.dxf")
    window.scene_result = old_built
    window.drawing_view.setScene(old_built.scene)
    old_review = _ReviewStub()
    window._review_center = old_review
    window._begin_manual_geometry_selection(
        {"annotation_handle": "ANN", "annotation_text": "1N20x380"}
    )
    stale_row = dict(window._manual_selection_row or {})

    new_records = [
        _line("NEW-REBAR", (0.0, 0.0), (100.0, 0.0)),
        DxfEntityRecord(
            handle="NEW-ANN",
            entity_type="BLOCK_ATTR_TEXT",
            layer="ARMTXT",
            text="1N20x100",
            geometry={"insert": [0.0, 10.0, 0.0]},
        ),
    ]
    _ImporterStub.result = _import_result(new_records, "new.dxf")
    monkeypatch.setattr(main_window_module, "DxfImporter", _ImporterStub)
    monkeypatch.setattr(
        window.project_manager,
        "save_extracted_entities",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        window.project_manager,
        "save_layer_classification",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        window.project_manager,
        "save",
        lambda *_args, **_kwargs: tmp_path / "project.json",
    )
    monkeypatch.setattr(window, "_session_project_event", lambda *_args: None)

    window._load_dxf_from_project(tmp_path / "new.dxf", show_report=False)
    app.processEvents()

    assert window._review_center is None
    assert window._manual_selection_row is None
    assert window._manual_selection_scene is None
    assert window.scene_result is not old_built
    assert set(window.scene_result.handle_to_record) == {"NEW-REBAR", "NEW-ANN"}
    assert window.drawing_view._selection_entity_filter is None

    # Simulate a delayed signal from the already closed old Review Center.
    current_review = _ReviewStub()
    window._review_center = current_review
    window.scene_result.handle_to_item["NEW-REBAR"].setSelected(True)
    summarize_called = False

    def forbidden_summary(*_args, **_kwargs):
        nonlocal summarize_called
        summarize_called = True
        raise AssertionError("stale row must be rejected before summarization")

    monkeypatch.setattr(
        main_window_module,
        "summarize_selected_geometry",
        forbidden_summary,
    )
    window._review_assign_form(stale_row)

    assert summarize_called is False
    assert current_review.errors
    assert "предишен DXF import" in current_review.errors[-1]

    window._review_center = None
    current_review.close()
    old_review.close()
    window.scene_result = None
    window.import_result = None
    window.project = None
    window.close()


def test_dxf_load_eagerly_builds_guard_once_before_first_manual_click(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _app()
    records = _guard_entities()
    _ImporterStub.result = _import_result(records, "guard.dxf")
    monkeypatch.setattr(main_window_module, "DxfImporter", _ImporterStub)
    real_context_builder = main_window_module.build_geometry_candidate_context
    real_legend_builder = main_window_module.build_position_key_legend_index
    calls = {"context": 0, "legend": 0}

    def counting_context(*args, **kwargs):
        calls["context"] += 1
        return real_context_builder(*args, **kwargs)

    def counting_legend(*args, **kwargs):
        calls["legend"] += 1
        return real_legend_builder(*args, **kwargs)

    monkeypatch.setattr(
        main_window_module,
        "build_geometry_candidate_context",
        counting_context,
    )
    monkeypatch.setattr(
        main_window_module,
        "build_position_key_legend_index",
        counting_legend,
    )
    window = MainWindow(
        _RepositoryStub(),
        logging.getLogger("rc42-manual-eager-cache"),
        session_service=_SessionStub(),
    )
    window.project = ProjectModel(name="EAGER", root=str(tmp_path))
    monkeypatch.setattr(
        window.project_manager,
        "save_extracted_entities",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        window.project_manager,
        "save_layer_classification",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        window.project_manager,
        "save",
        lambda *_args, **_kwargs: tmp_path / "project.json",
    )
    monkeypatch.setattr(window, "_session_project_event", lambda *_args: None)

    window._load_dxf_from_project(tmp_path / "guard.dxf", show_report=False)

    assert calls == {"context": 1, "legend": 1}
    assert window._manual_geometry_guard_cache_ready is True
    assert {"706D", "7070"} <= window._manual_protected_legend_handles

    window._begin_manual_geometry_selection(
        {"annotation_handle": "ANN", "annotation_text": "1N20x380"}
    )

    assert calls == {"context": 1, "legend": 1}
    assert window.drawing_view._selection_allowed_handles == frozenset(
        {"VALID-REBAR"}
    )

    window._leave_manual_geometry_selection(
        restore_review=False,
        clear_selection=True,
    )
    window.scene_result = None
    window.import_result = None
    window.project = None
    window.close()


def test_project_switch_to_project_without_dxf_clears_previous_scene(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _app()
    records = _guard_entities()
    built = build_scene(records)
    window = MainWindow(
        _RepositoryStub(),
        logging.getLogger("rc42-project-switch-teardown"),
        session_service=_SessionStub(),
    )
    project_a = ProjectModel(name="A", root=str(tmp_path / "A"))
    window.project = project_a
    window.import_result = _import_result(records, "a.dxf")
    window.scene_result = built
    window.drawing_view.setScene(built.scene)
    window.layer_panel.set_layers(["ARM-GORNA", "ARMTXT"])
    window._begin_manual_geometry_selection(
        {"annotation_handle": "ANN", "annotation_text": "1N20x380"}
    )
    monkeypatch.setattr(window, "_flush_project_decision_outbox", lambda: None)

    project_b = ProjectModel(name="B", root=str(tmp_path / "B"))
    window._set_project(project_b)

    assert window.project is project_b
    assert window.import_result is None
    assert window.scene_result is None
    assert window.drawing_view.scene() is None
    assert window._manual_selection_row is None
    assert window._manual_selection_scene is None
    assert window._manual_geometry_guard_cache_ready is False
    assert window.drawing_view._selection_allowed_handles is None
    assert window.layer_panel.list_widget.count() == 0

    window.project = None
    window.close()


def test_failed_guard_build_retries_and_recovers_without_dxf_reload(
    tmp_path: Path,
) -> None:
    _app()
    records = _guard_entities()
    built = build_scene(records)
    repository = _RecoveringRepositoryStub()
    window = MainWindow(
        repository,
        logging.getLogger("rc42-manual-guard-recovery"),
        session_service=_SessionStub(),
    )
    window.project = ProjectModel(name="RECOVER", root=str(tmp_path))
    window.import_result = _import_result(records, "recover.dxf")
    window.scene_result = built
    window.drawing_view.setScene(built.scene)
    window._invalidate_manual_geometry_guard_cache()
    calls_before_attempts = repository.calls

    first_allowed, first_protected = window._manual_geometry_guard_handles()

    assert first_allowed == frozenset()
    assert first_protected == frozenset()
    assert window._manual_geometry_guard_error is not None
    assert window._manual_geometry_guard_cache_ready is False

    repository.available = True
    allowed, protected = window._manual_geometry_guard_handles()

    assert repository.calls == calls_before_attempts + 2
    assert {"706D", "7070", "VALID-REBAR"} <= allowed
    assert {"706D", "7070"} <= protected
    assert window._manual_geometry_guard_error is None
    assert window._manual_geometry_guard_cache_ready is True

    window.scene_result = None
    window.import_result = None
    window.project = None
    window.close()


def test_dxf_stage_failure_keeps_previous_import_and_scene_pair(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _app()
    old_records = _guard_entities()
    old_result = _import_result(old_records, "old.dxf")
    old_built = build_scene(old_records)
    window = MainWindow(
        _RepositoryStub(),
        logging.getLogger("rc42-atomic-dxf-stage"),
        session_service=_SessionStub(),
    )
    window.project = ProjectModel(
        name="ATOMIC",
        root=str(tmp_path),
        units="Meters",
        layer_visibility={"ARM-GORNA": True},
        import_summary={"filename": "old.dxf"},
    )
    window.import_result = old_result
    window.scene_result = old_built
    window.drawing_view.setScene(old_built.scene)
    window._loaded_source_generation_id = current_identity(
        window.project
    ).source_generation_id

    # Reuse a handle deliberately: a torn import/scene pair would make the new
    # record appear authorized against the old graphics item.
    new_records = [
        _line("VALID-REBAR", (9000.0, 0.0), (9200.0, 0.0)),
        DxfEntityRecord(
            handle="ANN",
            entity_type="BLOCK_ATTR_TEXT",
            layer="ARMTXT",
            text="1N20x200",
            geometry={"insert": [9000.0, 10.0, 0.0]},
        ),
    ]
    _ImporterStub.result = _import_result(new_records, "new.dxf")
    monkeypatch.setattr(main_window_module, "DxfImporter", _ImporterStub)
    real_build_scene = main_window_module.build_scene

    def injected_scene_failure(records, _display_entities=None):
        if records is new_records:
            raise RuntimeError("fault between import and scene commit")
        return real_build_scene(records)

    monkeypatch.setattr(main_window_module, "build_scene", injected_scene_failure)
    original_project_state = (
        dict(window.project.import_summary),
        window.project.units,
        dict(window.project.layer_visibility),
    )

    with pytest.raises(RuntimeError, match="fault between import and scene commit"):
        window._load_dxf_from_project(tmp_path / "new.dxf", show_report=False)

    assert window.import_result is old_result
    assert window.scene_result is old_built
    assert window.drawing_view.scene() is old_built.scene
    assert window.scene_result.handle_to_record["VALID-REBAR"] is old_records[-2]
    assert (
        window.project.import_summary,
        window.project.units,
        window.project.layer_visibility,
    ) == original_project_state

    window.scene_result = None
    window.import_result = None
    window.project = None
    window.close()


def test_changed_source_generation_stage_failure_clears_old_drawing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _app()
    old_records = _guard_entities()
    old_result = _import_result(old_records, "old.dxf")
    old_built = build_scene(old_records)
    project = ProjectModel(name="GENERATION", root=str(tmp_path))
    old_identity = begin_source_generation(project, source_sha256="a" * 64)
    window = MainWindow(
        _RepositoryStub(),
        logging.getLogger("rc42-source-generation-fail-closed"),
        session_service=_SessionStub(),
    )
    window.project = project
    window.import_result = old_result
    window.scene_result = old_built
    window.drawing_view.setScene(old_built.scene)
    window._loaded_source_generation_id = old_identity.source_generation_id

    # Simulate the real import_dxf_source() transaction, which advances the
    # generation before _load_dxf_from_project parses/builds the new drawing.
    new_identity = begin_source_generation(project, source_sha256="b" * 64)
    assert new_identity.source_generation_id != old_identity.source_generation_id
    new_records = [
        _line("VALID-REBAR", (9000.0, 0.0), (9200.0, 0.0)),
    ]
    _ImporterStub.result = _import_result(new_records, "new.dxf")
    monkeypatch.setattr(main_window_module, "DxfImporter", _ImporterStub)

    def injected_scene_failure(_records, _display_entities=None):
        raise RuntimeError("new generation scene failure")

    monkeypatch.setattr(main_window_module, "build_scene", injected_scene_failure)

    with pytest.raises(RuntimeError, match="new generation scene failure"):
        window._load_dxf_from_project(tmp_path / "new.dxf", show_report=False)

    assert current_identity(project).source_generation_id == new_identity.source_generation_id
    assert window.import_result is None
    assert window.scene_result is None
    assert window.drawing_view.scene() is None
    assert window.layer_panel.list_widget.count() == 0
    assert window._loaded_source_generation_id is None

    window.project = None
    window.close()


def test_ui_failure_after_project_writes_clears_view_instead_of_rollback(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _app()
    old_records = _guard_entities()
    old_result = _import_result(old_records, "old.dxf")
    old_built = build_scene(old_records)
    project = ProjectModel(
        name="DISK-COMMIT",
        root=str(tmp_path),
        import_summary={"filename": "old.dxf"},
    )
    generation = begin_source_generation(project, source_sha256="c" * 64)
    window = MainWindow(
        _RepositoryStub(),
        logging.getLogger("rc42-post-write-fail-closed"),
        session_service=_SessionStub(),
    )
    window.project = project
    window.import_result = old_result
    window.scene_result = old_built
    window.drawing_view.setScene(old_built.scene)
    window._loaded_source_generation_id = generation.source_generation_id

    new_records = [
        _line("VALID-REBAR", (9000.0, 0.0), (9200.0, 0.0)),
    ]
    _ImporterStub.result = _import_result(new_records, "new.dxf")
    monkeypatch.setattr(main_window_module, "DxfImporter", _ImporterStub)
    save_calls: list[str] = []
    monkeypatch.setattr(
        window.project_manager,
        "save_extracted_entities",
        lambda *_args, **_kwargs: save_calls.append("entities"),
    )
    monkeypatch.setattr(
        window.project_manager,
        "save_layer_classification",
        lambda *_args, **_kwargs: save_calls.append("layers"),
    )
    monkeypatch.setattr(
        window.project_manager,
        "save",
        lambda *_args, **_kwargs: save_calls.append("project"),
    )

    def injected_fit_failure():
        raise RuntimeError("UI failure after project writes")

    monkeypatch.setattr(window.drawing_view, "fit_all", injected_fit_failure)

    with pytest.raises(RuntimeError, match="UI failure after project writes"):
        window._load_dxf_from_project(tmp_path / "new.dxf", show_report=False)

    assert save_calls == ["entities", "layers", "project"]
    assert project.import_summary["filename"] == "new.dxf"
    assert window.import_result is None
    assert window.scene_result is None
    assert window.drawing_view.scene() is None
    assert window.layer_panel.list_widget.count() == 0
    assert window._loaded_source_generation_id is None

    window.project = None
    window.close()
