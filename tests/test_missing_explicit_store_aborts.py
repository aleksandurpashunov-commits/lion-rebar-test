# -*- coding: utf-8 -*-
"""Изрично зададено, но липсващо хранилище прекратява сравнителното измерване.

Дефект, намерен при преглед на собственото измерване от етап 4: шаблонът
``store_bez_{}.json`` даваше ``store_bez_d-2.json`` за проект ``d-2``, а файлът
е ``store_bez_d2.json``. Липсващият файл се зареждаше като ПРАЗНА памет и цялата
таблица „с памет" мереше празно хранилище — без нито едно предупреждение.
"""
from __future__ import annotations

import pytest

from lion_rebar.learning.knowledge_store import KnowledgeStore
from lion_rebar.learning.policy_bridge import load_store


def test_lipsvashto_izrichno_hranilishte_prekratyava(tmp_path, monkeypatch) -> None:
    липсващ = tmp_path / "store_bez_d-2.json"
    monkeypatch.setenv("LION_REBAR_NAUCHENO", str(липсващ))
    with pytest.raises(FileNotFoundError) as грешка:
        load_store()
    съобщение = str(грешка.value)
    assert "store_bez_d-2.json" in съобщение
    assert "празна памет" in съобщение


def test_nalichno_izrichno_hranilishte_se_zarezhda(tmp_path, monkeypatch) -> None:
    път = tmp_path / "naucheno.json"
    KnowledgeStore.load(път).save()
    monkeypatch.setenv("LION_REBAR_NAUCHENO", str(път))
    store = load_store()
    assert str(store.path) == str(път)


def test_narochno_prazna_pamet_se_razreshava_izrichno(tmp_path, monkeypatch) -> None:
    """Ключът остава, за да може да се мери и с нарочно празна памет."""

    monkeypatch.setenv("LION_REBAR_NAUCHENO", str(tmp_path / "nyama.json"))
    monkeypatch.setenv("LION_REBAR_NAUCHENO_ALLOW_MISSING", "1")
    assert len(load_store()) == 0


def test_bez_izrichno_zadadeno_povedenieto_e_nepromeneno(tmp_path, monkeypatch) -> None:
    """В нормална работа липсващият файл значи „още нищо не е научено"."""

    monkeypatch.delenv("LION_REBAR_NAUCHENO", raising=False)
    monkeypatch.setenv("LION_REBAR_DATA_ROOT", str(tmp_path))
    assert len(load_store()) == 0
