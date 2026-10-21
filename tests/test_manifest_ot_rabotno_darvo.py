# -*- coding: utf-8 -*-
"""Манифест на пакет от РАБОТНО дърво — честен, а не премълчан.

ЗАЩО СЪЩЕСТВУВА. Тестовата версия за приемателния опит се доставя от работно
дърво, което НЕ е комитвано: базата е комит, но кодът е по-нов. Дотук
генераторът отказваше в такъв случай — правилно за релийз, но тогава пакетът
изобщо няма контролни суми, което е по-лошо.

Режимът ``--dirty-ok`` НЕ разхлабва нищо: хешира и НЕПРОСЛЕДЕНИТЕ продуктови
файлове (иначе манифестът би описал стар код и би премълчал новите модули) и
записва изрично, че комитът е само БАЗА.

Git не се вика наистина: ``_git`` се подменя, за да е тестът детерминиран и да
не зависи от състоянието на дървото, в което се пуска.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

КОРЕН = Path(__file__).resolve().parents[1]


def _инструмент():
    spec = importlib.util.spec_from_file_location(
        "manifest_tool_dirty", КОРЕН / "tools" / "manifest.py"
    )
    модул = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(модул)
    return модул


#: Два истински файла: единият „проследен", другият „непроследен продуктов".
ПРОСЛЕДЕН = "pyproject.toml"
НЕПРОСЛЕДЕН = "lion_rebar/review/decision_reuse.py"


def _подмени(модул, monkeypatch, *, порцелан: str) -> None:
    def _git(*аргументи: str) -> str:
        if аргументи[:2] == ("status", "--porcelain"):
            return порцелан
        if аргументи[:2] == ("rev-parse", "HEAD"):
            return "f" * 40 + "\n"
        if аргументи[:1] == ("ls-files",):
            return ПРОСЛЕДЕН + "\n"
        raise AssertionError("неочакван git: %s" % (аргументи,))

    monkeypatch.setattr(модул, "_git", _git)


def test_chisto_darvo_ne_otbelyazva_nishto(monkeypatch):
    модул = _инструмент()
    _подмени(модул, monkeypatch, порцелан="")
    манифест = модул.построй(version="v-проба")
    assert манифест["commit"] == "f" * 40
    assert "uncommitted_changes" not in манифест
    assert set(манифест["files"]) == {ПРОСЛЕДЕН}


def test_marsno_darvo_bez_razreshenie_se_otkazva(monkeypatch):
    модул = _инструмент()
    _подмени(модул, monkeypatch, порцелан=" M %s\n" % ПРОСЛЕДЕН)
    with pytest.raises(модул.МанифестError, match="неприключени промени"):
        модул.построй(version="v-проба")


def test_marsno_darvo_s_razreshenie_opisva_istinskiya_kod(monkeypatch):
    модул = _инструмент()
    _подмени(
        модул,
        monkeypatch,
        порцелан=" M %s\n?? %s\n?? .agents/\n" % (ПРОСЛЕДЕН, НЕПРОСЛЕДЕН),
    )
    манифест = модул.построй(version="v-проба", dirty_ok=True)
    assert манифест["uncommitted_changes"] is True
    assert манифест["commit_is_base_only"] is True
    assert манифест["dirty_tracked_files"] == [ПРОСЛЕДЕН]
    # НОВИЯТ модул влиза в сумите — иначе манифестът премълчава кода.
    assert НЕПРОСЛЕДЕН in манифест["files"]
    assert ПРОСЛЕДЕН in манифест["files"]
    # Работните материали на друг агент НЕ влизат.
    assert not [име for име in манифест["files"] if име.startswith(".agents")]
    # Сумата е истинската сума на файла на диска.
    assert манифест["files"][НЕПРОСЛЕДЕН] == модул.сума(КОРЕН / НЕПРОСЛЕДЕН)


def test_manifestut_ne_hashira_sebe_si_i_v_marsen_rezhim(monkeypatch):
    модул = _инструмент()
    _подмени(
        модул,
        monkeypatch,
        порцелан=" M %s\n?? SHA256_MANIFEST.json\n" % ПРОСЛЕДЕН,
    )
    манифест = модул.построй(version="v-проба", dirty_ok=True)
    assert "SHA256_MANIFEST.json" not in манифест["files"]


def test_dostaveniyat_manifest_e_v_marsen_rezhim_i_nosi_versiyata():
    """Манифестът В ПАКЕТА казва, че комитът е само база."""

    from lion_rebar.config.constants import APP_VERSION

    данни = json.loads(
        (КОРЕН / "SHA256_MANIFEST.json").read_text(encoding="utf-8")
    )
    assert данни["version"] == f"v{APP_VERSION}"
    # Тази версия се доставя от работно дърво; ако някога бъде комитвана,
    # полетата отпадат и тогава този тест трябва да се обнови СЪЗНАТЕЛНО.
    assert данни.get("uncommitted_changes") is True
    assert данни.get("commit_is_base_only") is True
    assert "lion_rebar/review/decision_reuse.py" in данни["files"]
