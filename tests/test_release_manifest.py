"""Еднозначен релийз: версията, сумите и тестовият отчет сочат едно състояние.

ЗАЩО СЪЩЕСТВУВА. Към 24.09.2026 `APP_VERSION` беше `0.1.44.21-rc44`,
`pyproject.toml` пишеше `0.1.44.20-rc43`, а `SHA256_MANIFEST.json` описваше
rc43. Тоест пакетът не можеше да каже кой код носи, и никой тест не забелязваше.

Тук се пази съвпадението на местата, които трябва да се сменят ЗАЕДНО, и
поведението на генератора: отказ при мръсно дърво, манифест без самия себе си,
честен отчет от `--junitxml`.

Файловите суми НЕ се сверяват тук — те остаряват при всяка редакция по
построение. За тях е `tools/manifest.py --check` в момента на релийза.
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

from lion_rebar.config.constants import APP_VERSION

КОРЕН = Path(__file__).resolve().parents[1]


def _инструмент():
    spec = importlib.util.spec_from_file_location("manifest_tool", КОРЕН / "tools" / "manifest.py")
    модул = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(модул)
    return модул


# --- трите места ------------------------------------------------------------


def test_pyproject_nosi_sushtata_versiya() -> None:
    текст = (КОРЕН / "pyproject.toml").read_text(encoding="utf-8")
    намерено = re.search(r'^version = "([^"]+)"', текст, flags=re.MULTILINE)
    assert намерено, "pyproject.toml няма version"
    assert намерено.group(1) == APP_VERSION, (
        "pyproject.toml и APP_VERSION се разминават — точно дефектът, заради "
        "който манифестът остана на rc43"
    )


def test_releasnite_dokumenti_za_tazi_versiya_sushtestvuvat() -> None:
    кратка = APP_VERSION.split("-")[-1]
    assert (КОРЕН / "docs" / f"RELEASE_v{APP_VERSION}_BG.md").is_file()
    assert (КОРЕН / f"WINDOWS_ACCEPTANCE_{кратка}.md").is_file()


# --- генераторът ------------------------------------------------------------


def test_otchetut_ot_junit_broi_minalite_sam() -> None:
    """Числата идват от XML-а, не от паметта на автора."""

    модул = _инструмент()
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<testsuites><testsuite name="pytest" errors="0" failures="0" '
        'skipped="14" tests="1922" time="185.9" timestamp="2026-09-24T12:00:00+03:00">'
        "</testsuite></testsuites>"
    )
    път = КОРЕН / "tests" / "_junit_проба.xml"
    път.write_text(xml, encoding="utf-8")
    try:
        отчет = модул.отчет_от_junit(път)
    finally:
        път.unlink()
    assert отчет["passed"] == 1908
    assert отчет["skipped"] == 14
    assert отчет["failures"] == 0 and отчет["errors"] == 0


def test_generatorut_otkazva_pri_mrusno_durvo(monkeypatch) -> None:
    """Манифест на непроследено състояние не значи нищо."""

    модул = _инструмент()
    monkeypatch.setattr(модул, "дървото_е_чисто", lambda: (False, " M lion_rebar/app.py"))
    with pytest.raises(модул.МанифестError) as грешка:
        модул.построй(version="v0.0.0")
    assert "неприключени промени" in str(грешка.value)


def test_neprosledenite_papki_ne_pravyat_durvoto_mrusno(monkeypatch) -> None:
    """Работните материали на друг агент не влизат в релийза и не му пречат."""

    модул = _инструмент()
    monkeypatch.setattr(
        модул, "_git",
        lambda *args: "?? .agents/\n?? AGENTS.md\n" if args[0] == "status" else "",
    )
    чисто, мръсни = модул.дървото_е_чисто()
    assert чисто and мръсни == ""


def test_spisukut_izklyuchva_samiya_manifest(monkeypatch) -> None:
    модул = _инструмент()
    monkeypatch.setattr(
        модул, "_git",
        lambda *args: "SHA256_MANIFEST.json\nlion_rebar/app.py\nREADME_BG.md\n",
    )
    файлове = модул.проследени_файлове()
    assert файлове == ("README_BG.md", "lion_rebar/app.py")


def test_proverkata_vizhda_razlichen_i_nov_fayl(tmp_path, monkeypatch) -> None:
    модул = _инструмент()
    (tmp_path / "а.txt").write_text("едно", encoding="utf-8")
    (tmp_path / "б.txt").write_text("две", encoding="utf-8")
    манифест = tmp_path / "SHA256_MANIFEST.json"
    манифест.write_text(
        json.dumps({
            "schema": модул.СХЕМА,
            "version": "v0.0.0",
            "commit": "0" * 40,
            "files": {"а.txt": модул.сума(tmp_path / "а.txt"), "в.txt": "0" * 64},
            "evidence_files": [],
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(модул, "КОРЕН", tmp_path)
    monkeypatch.setattr(модул, "МАНИФЕСТ", манифест)
    monkeypatch.setattr(модул, "проследени_файлове", lambda: ("а.txt", "б.txt"))

    разлики = модул.провери()

    assert "липсва: в.txt" in разлики
    assert "нов, извън манифеста: б.txt" in разлики
    assert not any("а.txt" in ред for ред in разлики)
