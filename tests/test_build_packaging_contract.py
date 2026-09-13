from __future__ import annotations

from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[1]


def test_build_exe_explicitly_bundles_lion_rebar_resources() -> None:
    text = (ROOT / "BUILD_EXE.bat").read_text(encoding="utf-8")
    normalized = text.replace("/", "\\").lower()
    assert '--add-data "lion_rebar\\resources;lion_rebar\\resources"' in normalized


def test_build_exe_runs_mandatory_dist_smoke() -> None:
    text = (ROOT / "BUILD_EXE.bat").read_text(encoding="utf-8").lower()
    assert "lion_rebar_dist" in text
    assert "tests\\test_dist_smoke.py" in text.replace("/", "\\")
    assert "if errorlevel 1" in text


def test_package_data_includes_all_resource_types() -> None:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    patterns = data["tool"]["setuptools"]["package-data"]["lion_rebar"]
    assert "resources/*" in patterns
