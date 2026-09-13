"""Recovery.2 smoke tests for the standalone PyInstaller dist.

Run explicitly against a built dist:

    set LION_REBAR_DIST=<path to dist\\LION_REBAR_DESKTOP>
    python -m pytest tests/test_dist_smoke.py

Without the environment variable every test is skipped, so the ordinary
suite stays independent of a build artefact.

Coverage (headless-възможното):
* задължителните data файлове са в пакета: engine_updates/*.lionengine,
  lion_rebar/resources/export_template_v1.xlsx,
  lion_rebar/resources/OPEN_ENGINE_v0.3.1_MASTER_F009_LOCKED.html;
* заключеният HTML има точно одобрения SHA-256;
* Excel шаблонът се отваря (openpyxl) и Engine bundle-ът се чете;
* EXE-то стартира offscreen и остава живо (не крашва веднага).

Извън обхвата на headless smoke: интерактивният импорт на Engine през UI
и пълният Excel export през прозореца — те изискват оператор.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

DIST = os.environ.get("LION_REBAR_DIST", "")

pytestmark = pytest.mark.skipif(
    not DIST or not Path(DIST).is_dir(),
    reason="LION_REBAR_DIST не сочи към изграден dist пакет",
)


def _dist() -> Path:
    return Path(DIST)


def _internal() -> Path:
    inner = _dist() / "_internal"
    return inner if inner.is_dir() else _dist()


def test_dist_contains_engine_bundles():
    bundles = sorted((_internal() / "engine_updates").glob("*.lionengine"))
    assert bundles, "dist пакетът няма engine_updates/*.lionengine"


def test_dist_contains_excel_template_and_it_opens():
    template = _internal() / "lion_rebar" / "resources" / "export_template_v1.xlsx"
    assert template.is_file(), "export_template_v1.xlsx липсва в dist"
    openpyxl = pytest.importorskip("openpyxl")
    workbook = openpyxl.load_workbook(template)
    assert workbook.sheetnames


def test_dist_contains_locked_master_html_with_exact_sha():
    from lion_rebar.export.master_engine_v031 import MASTER_ENGINE_SHA256

    import hashlib

    html = (
        _internal()
        / "lion_rebar"
        / "resources"
        / "OPEN_ENGINE_v0.3.1_MASTER_F009_LOCKED.html"
    )
    assert html.is_file(), "заключеният MASTER HTML липсва в dist"
    digest = hashlib.sha256(html.read_bytes()).hexdigest()
    assert digest == MASTER_ENGINE_SHA256


def test_dist_engine_bundle_is_readable():
    from lion_rebar.engine.package_io import inspect_engine_bundle

    bundles = sorted((_internal() / "engine_updates").glob("*.lionengine"))
    assert bundles
    engine, _manifest, _names = inspect_engine_bundle(bundles[-1])
    assert engine.shapes


def test_exe_starts_offscreen_and_stays_alive(tmp_path: Path):
    exe = _dist() / "LION_REBAR_DESKTOP.exe"
    assert exe.is_file(), "LION_REBAR_DESKTOP.exe липсва в dist"
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    # The build smoke must never create/resume the operator's real work session.
    env["LION_REBAR_DATA_ROOT"] = str(tmp_path / "isolated_appdata")
    process = subprocess.Popen(
        [str(exe)],
        cwd=str(_dist()),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        time.sleep(8.0)
        assert process.poll() is None, (
            f"EXE-то завърши преждевременно с код {process.returncode}"
        )
    finally:
        process.kill()
        process.wait(timeout=15)
