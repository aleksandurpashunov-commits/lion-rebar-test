from pathlib import Path

from lion_rebar.engine.bootstrap import create_bootstrap_engine
from lion_rebar.project.manager import ProjectManager


def test_create_save_open_project_and_copy_dxf(tmp_path: Path):
    manager = ProjectManager()
    engine = create_bootstrap_engine()
    project = manager.create_project(tmp_path, "Alfa Test", engine)
    assert (Path(project.root) / "working" / "project.json").exists()
    manager.save_engine_snapshot(project, engine)

    source = tmp_path / "sample.dxf"
    source.write_text("0\nSECTION\n0\nEOF\n", encoding="ascii")
    copied = manager.import_dxf_source(project, source)
    assert copied.name == "original.dxf"
    assert copied.read_text(encoding="ascii") == source.read_text(encoding="ascii")

    opened = manager.open_project(Path(project.root))
    assert opened.name == "Alfa Test"
    assert opened.original_source_name == "sample.dxf"
