"""Пакетираният engine и пътят към него са в съгласие с релийза.

Днешният най-скъп дефект тръгна от разминаване между това, което е активно, и
това, което е вярно (F010 срещу F009). Тази проверка пази другото възможно
разминаване: между engine-а, който приложението НОСИ, и версията, която очаква
да инсталира. Всяко от двете се чете от истинския код и от истинския пакет, не
от константа в теста.

Проверява се и че пакетът е годен за инсталация при старт — точно каквото
``install_trusted_bundled_engine`` изисква, преди да пипне регистъра:
production-ready, точно два файла, съвпадащ manifest, чиста структурна проверка
и всички контролни тестове.
"""
from __future__ import annotations

import re
from pathlib import Path

from lion_rebar.engine.package_io import inspect_engine_bundle
from lion_rebar.engine.test_runner import run_engine_tests
from lion_rebar.engine.validation import has_errors, validate_engine
from lion_rebar.ui.main_window import _KNOWN_ENGINE_CORRECTIONS

_ROOT = Path(__file__).resolve().parents[1]
_APP = _ROOT / "lion_rebar" / "app.py"


def _shipped() -> tuple[Path, str, set[str]]:
    """Име на пакета, очакваната версия и допуснатите предшественици."""

    source = _APP.read_text(encoding="utf-8")
    name = re.search(r'"(lion-rebar-master_v[^"]+\.lionengine)"', source)
    version = re.search(r'expected_target_version="([^"]+)"', source)
    allowed = re.search(r"allowed_current_versions=\{([^}]+)\}", source)
    assert name and version and allowed, "app.py не декларира пакетиран engine"
    versions = {
        item.strip().strip('"')
        for item in allowed.group(1).split(",")
        if item.strip()
    }
    return _ROOT / "engine_updates" / name.group(1), version.group(1), versions


def test_приложението_носи_engine_а_който_очаква() -> None:
    bundle, expected_version, _allowed = _shipped()

    assert bundle.is_file(), f"липсва пакетът {bundle.name}"
    engine, manifest, names = inspect_engine_bundle(bundle)
    assert engine.version == expected_version
    assert str(manifest.get("engine_version") or "") == expected_version
    assert sorted(names) == ["engine.json", "manifest.json"]
    assert engine.production_ready is True


def test_пакетираният_engine_минава_проверките_за_инсталация() -> None:
    bundle, _version, _allowed = _shipped()
    engine, _manifest, _names = inspect_engine_bundle(bundle)

    issues = validate_engine(engine, parent=None, for_publish=True)
    assert not has_errors(issues), [
        issue.message for issue in issues if issue.severity == "ERROR"
    ]
    failed = [result for result in run_engine_tests(engine) if not result.passed]
    assert not failed, [result.message for result in failed]


def test_всеки_допуснат_предшественик_има_път_за_миграция() -> None:
    """Инсталира ли се engine от дадена версия, проектът също трябва да може.

    Иначе машината минава напред, а проектът остава на своя snapshot без
    никакво обяснение освен „различна engine версия".
    """

    _bundle, expected_version, allowed = _shipped()
    covered: set[str] = set()
    for correction in _KNOWN_ENGINE_CORRECTIONS:
        if correction.to_version == expected_version:
            covered |= set(correction.from_versions)
    # ``0.0.0`` е bootstrap: там проект още няма.
    assert allowed - {"0.0.0"} <= covered


def test_целевата_версия_не_е_сред_предшествениците() -> None:
    _bundle, expected_version, allowed = _shipped()
    assert expected_version not in allowed
