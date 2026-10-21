# -*- coding: utf-8 -*-
"""Атомарният запис не се предава, когато друг държи целта отворена.

НОВООТКРИТ ДЕФЕКТ, 01.10.2026 (докладва се отделно по § 10.3). Намерен е при
мярката за § 7.4: два процеса върху един файл.

В Windows ``os.replace`` пада с ``PermissionError``, ако целта е отворена от
някой друг. Това е всекидневна ситуация в средата на собственика: вторият
процес на програмата, редактор, антивирусна проверка и синхронизацията на
стриймващия драйв ``I:``. Паднал запис на ``approved_geometry_links.json`` или
на журнала с решенията значи ЗАГУБЕНО решение — не повредено, а липсващо.

Старото поведение: един опит и изключение. Новото: изчаква целта да се
освободи до ``REPLACE_RETRY_SECONDS``, после вдига грешката. Мълчаливо
продължаване без запис няма.

Тестът е доказано червен срещу стария код: с един опит ``os.replace`` пада
веднага, защото в момента на извикването чуждият читател още държи файла.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import lion_rebar
from lion_rebar.utils import files as модул
from lion_rebar.utils.files import atomic_write_text

КОРЕН_НА_КОДА = str(Path(lion_rebar.__file__).resolve().parents[1])

ДРЪЖ = '''
import sys, time
from pathlib import Path

цел, знак, задържане = Path(sys.argv[1]), Path(sys.argv[2]), float(sys.argv[3])
with цел.open("r", encoding="utf-8"):
    знак.write_text("1", encoding="utf-8")
    time.sleep(задържане)
'''


def _дръж_отворен(tmp_path: Path, цел: Path, задържане: float) -> subprocess.Popen:
    скрипт = tmp_path / "дръж.py"
    скрипт.write_text(ДРЪЖ, encoding="utf-8")
    знак = tmp_path / "взето"
    среда = dict(os.environ)
    среда["PYTHONPATH"] = КОРЕН_НА_КОДА + os.pathsep + среда.get("PYTHONPATH", "")
    процес = subprocess.Popen(
        [sys.executable, str(скрипт), str(цел), str(знак), str(задържане)],
        env=среда,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    краен = time.monotonic() + 30
    while not знак.is_file():
        if time.monotonic() >= краен or процес.poll() is not None:
            процес.kill()
            процес.communicate(timeout=60)
            pytest.fail("Чуждият читател не отвори целта.")
        time.sleep(0.01)
    return процес


@pytest.mark.skipif(os.name != "nt", reason="Поведението е на Windows.")
def test_zapisat_izchakva_chuzhdiya_chitatel(tmp_path):
    цел = tmp_path / "ledger.json"
    цел.write_text(json.dumps({"links": []}), encoding="utf-8")
    процес = _дръж_отворен(tmp_path, цел, 1.5)
    try:
        atomic_write_text(цел, json.dumps({"links": ["ново"]}))
    finally:
        процес.communicate(timeout=60)
    assert json.loads(цел.read_text(encoding="utf-8"))["links"] == ["ново"]


@pytest.mark.skipif(os.name != "nt", reason="Поведението е на Windows.")
def test_s_edin_opit_zapisat_bi_padnal(tmp_path, monkeypatch):
    """ЧЕРВЕНИЯТ случай: старото поведение е нулево изчакване."""

    monkeypatch.setattr(модул, "REPLACE_RETRY_SECONDS", 0.0)
    цел = tmp_path / "ledger.json"
    цел.write_text(json.dumps({"links": []}), encoding="utf-8")
    процес = _дръж_отворен(tmp_path, цел, 1.5)
    try:
        with pytest.raises(PermissionError):
            atomic_write_text(цел, json.dumps({"links": ["ново"]}))
    finally:
        процес.communicate(timeout=60)
    # Старото съдържание е непокътнато: отказът не поврежда файла.
    assert json.loads(цел.read_text(encoding="utf-8"))["links"] == []


@pytest.mark.skipif(os.name != "nt", reason="Поведението е на Windows.")
def test_sled_iztichane_greshkata_se_vdiga(tmp_path, monkeypatch):
    """Отказът е по-добър от мълчаливо продължаване без запис."""

    monkeypatch.setattr(модул, "REPLACE_RETRY_SECONDS", 0.3)
    цел = tmp_path / "ledger.json"
    цел.write_text(json.dumps({"links": []}), encoding="utf-8")
    процес = _дръж_отворен(tmp_path, цел, 5.0)
    try:
        with pytest.raises(PermissionError):
            atomic_write_text(цел, json.dumps({"links": ["ново"]}))
    finally:
        процес.kill()
        процес.communicate(timeout=60)
    # Нито един временен файл не остава след провалилия се запис.
    assert [име.name for име in tmp_path.glob(".ledger.json.*")] == []
