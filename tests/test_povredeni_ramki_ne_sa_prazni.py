# -*- coding: utf-8 -*-
"""Нечетимият файл с начертани рамки е ОТКАЗ, не празен списък (§ 7.6).

НОВООТКРИТ ДЕФЕКТ, 01.10.2026 (докладва се отделно по § 10.3). Намерен при
работата по § 7.4 — четенето на областите от работника на анализа.

Старото поведение: ``operator_areas._read`` връщаше ``{"areas": []}`` и когато
файлът липсва, И когато съществува, но не се чете. Двете последствия бяха
мълчаливи:

1. ЗАПИСЪТ ТРИЕШЕ ВСИЧКО. ``record_operator_area`` чете, добавя и записва
   прочетеното. Един неуспешен прочит — заето от синхронизацията на драйва
   ``I:``, антивирусна проверка, прекъснат предишен запис — и новият файл
   съдържа САМО новата рамка. Всички по-рано начертани рамки изчезват.
2. АНАЛИЗЪТ РАБОТЕШЕ С НУЛА РАМКИ. ``batch_geometry_worker`` прихващаше всяко
   изключение и връщаше ``()``. Оградените от оператора позиции пак излизаха
   без греда, а вината изглеждаше като на разпознаването.

Новото: липсващият файл е празно (нищо не е чертано — законно състояние), а
нечетимият вдига ``OperatorAreaError`` и по двата пътя.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lion_rebar.export.operator_areas import (
    AREAS_NAME,
    KIND_BEAM,
    OperatorAreaError,
    load_operator_areas,
    record_operator_area,
)
from lion_rebar.project.data_identity import DataIdentity

_КУТИЯ = (113317.0, 2638.0, 113897.0, 2938.0)


def _самоличност() -> DataIdentity:
    return DataIdentity(
        project_id="s-9",
        source_sha256="SHA",
        source_generation_id="G1",
        analysis_run_id="R1",
    )


def _запиши(корен: Path, **промени):
    параметри = {
        "box": _КУТИЯ,
        "kind": KIND_BEAM,
        "title_handle": "BL19",
        "title_text": "Гр. B.19 25/55",
        "frame_index": 6,
        "decided_by": "Александър",
        "note": "детайлите са над заглавието на Гр. B.19",
        "identity": _самоличност(),
    }
    параметри.update(промени)
    return record_operator_area(корен, **параметри)


def test_lipsvashtiyat_fayl_e_prazno(tmp_path):
    """Нищо начертано е законно състояние и остава празен списък."""

    assert load_operator_areas(tmp_path, _самоличност()) == ()


def test_nechetimiyat_fayl_ne_se_chete_kato_prazen(tmp_path):
    _запиши(tmp_path)
    път = tmp_path / "working" / AREAS_NAME
    път.write_text("{непълен", encoding="utf-8")

    with pytest.raises(OperatorAreaError):
        load_operator_areas(tmp_path, _самоличност())


def test_fayl_s_neochakvana_struktura_e_otkaz(tmp_path):
    _запиши(tmp_path)
    път = tmp_path / "working" / AREAS_NAME
    път.write_text(json.dumps({"areas": "не е списък"}), encoding="utf-8")

    with pytest.raises(OperatorAreaError):
        load_operator_areas(tmp_path, _самоличност())


def test_zapisat_ne_trie_ramkite_pri_nechetim_fayl(tmp_path):
    """ЧЕРВЕНИЯТ случай: дотук новата рамка оставаше сама във файла."""

    _запиши(tmp_path)
    _запиши(tmp_path, title_handle="BL20", title_text="Гр. B.20 25/55",
            box=(120000.0, 2638.0, 120580.0, 2938.0))
    път = tmp_path / "working" / AREAS_NAME
    преди = път.read_text(encoding="utf-8")
    assert len(json.loads(преди)["areas"]) == 2

    път.write_text("{непълен", encoding="utf-8")
    with pytest.raises(OperatorAreaError):
        _запиши(tmp_path, title_handle="BL21", title_text="Гр. B.21 25/55",
                box=(130000.0, 2638.0, 130580.0, 2938.0))

    # Повреденият файл НЕ е презаписан с една рамка.
    assert път.read_text(encoding="utf-8") == "{непълен"


def test_rabotnikat_na_analiza_ne_padda_mallchalivo_s_nula_ramki(tmp_path):
    """Работникът вдига отказа вместо да върне нула области."""

    from lion_rebar.ui.batch_geometry_worker import BatchGeometryWorker

    _запиши(tmp_path)
    (tmp_path / "working" / AREAS_NAME).write_text("{непълен", encoding="utf-8")

    class _Проект:
        root = tmp_path

    работник = BatchGeometryWorker.__new__(BatchGeometryWorker)
    работник._project = _Проект()
    работник._data_identity = _самоличност()

    with pytest.raises(OperatorAreaError):
        работник._load_operator_areas()


def test_rabotnikat_prodalzhava_kogato_naistina_nyama_ramki(tmp_path):
    """Липсата си остава липса — тя не спира анализа."""

    from lion_rebar.ui.batch_geometry_worker import BatchGeometryWorker

    class _Проект:
        root = tmp_path

    работник = BatchGeometryWorker.__new__(BatchGeometryWorker)
    работник._project = _Проект()
    работник._data_identity = _самоличност()

    assert работник._load_operator_areas() == ()
