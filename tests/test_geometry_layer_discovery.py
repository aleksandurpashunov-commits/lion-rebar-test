# -*- coding: utf-8 -*-
"""Намиране на пропуснат армировъчен слой — по доказателство, не по име.

Защо съществува. Речникът в ``_rebar_geometry_layer_role`` познава осем корена
(``ARM``, ``REBAR``, ``BAR``, ``STEEL``, ``АРМ``, ``АРМАТ``, ``ЖЕЛЯЗ``). Слой
``s-Reinf`` (от Reinforcement) не съдържа нито един и пада: в `Тест-002` 299 от
329 позиции остават без нито един кандидат.

Защо НЕ се добавя корен ``REINF``. Тогава биха минали и слоевете
``AutoCAD_Structural_Detailing_…Surface reinforcement…`` на `d-2`, а обявяването
им е ИЗМЕРЕНО като вредно: порта 124 -> 105 от 178, анализ 6,1 -> 128,8 s.
Речникът не може да различи двата случая — и двата са „reinforcement" по име.

Числата в тестовете идват от тези два случая. Имената на слоевете тук са
синтетични: в кода няма нито едно име на файл, handle или конкретен размер.
"""
from __future__ import annotations

import dataclasses

import pytest

from lion_rebar.dxf.geometry_candidates import _rebar_geometry_layer_role
from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.dxf.recognition_policy import DEFAULT_RECOGNITION_POLICY
from lion_rebar.learning.integration import _намери_armirovachni_sloeve


# --- помощни ---------------------------------------------------------------


def _прът(handle: str, слой: str, начало, край) -> DxfEntityRecord:
    return DxfEntityRecord(handle=handle, entity_type="LWPOLYLINE", layer=слой,
                           geometry={"points": [list(начало), list(край)]})


def _надпис(handle: str, текст: str, точка) -> DxfEntityRecord:
    return DxfEntityRecord(handle=handle, entity_type="TEXT", layer="ARMTXT",
                           text=текст, geometry={"insert": list(точка)})


def _армировъчен_чертеж(слой: str, брой: int = 8, дължина: float = 300.0):
    """Прътове със СВОБОДНИ краища — всеки с надпис до себе си."""

    записи = []
    for и in range(брой):
        x = и * 500.0
        записи.append(_прът("G%d" % и, слой, (x, 0.0), (x, дължина)))
        записи.append(_надпис("T%d" % и, "%dN12x%d" % (4 + и, int(дължина)),
                              (x + 5.0, 5.0)))
    return записи


def _контур(слой: str, страна: float = 300.0, брой: int = 8):
    """ЗАТВОРЕНА верига от отсечки — всяка свързана с двете си съседки."""

    записи = []
    върхове = [(0.0, 0.0), (страна, 0.0), (страна, страна), (0.0, страна)]
    for и in range(4):
        записи.append(_прът("C%d" % и, слой, върхове[и], върхове[(и + 1) % 4]))
    for и in range(брой):
        записи.append(_надпис("TC%d" % и, "%dN12x%d" % (4 + и, int(страна)),
                              (5.0 + и, 5.0)))
    return записи


# --- намирането ------------------------------------------------------------


def test_sloy_bez_dumа_ot_rechnika_se_namira_po_dokazatelstvo() -> None:
    """Слой без нито един корен от речника, но с армировъчно доказателство."""

    слой = "s-Reinf"
    assert _rebar_geometry_layer_role(слой) is None, (
        "случаят има смисъл само ако речникът наистина пропуска слоя")

    политика, намерени = _намери_armirovachni_sloeve(
        DEFAULT_RECOGNITION_POLICY, _армировъчен_чертеж(слой))

    assert намерени == [слой]
    assert _rebar_geometry_layer_role(слой, политика) == "DISCOVERED_REBAR_LAYER"


def test_zatvoren_kontur_NE_se_namira() -> None:
    """Затворена верига не е армировка, макар надписите да съвпадат по дължина."""

    политика, намерени = _намери_armirovachni_sloeve(
        DEFAULT_RECOGNITION_POLICY, _контур("Slab contour"))

    assert намерени == []
    assert _rebar_geometry_layer_role("Slab contour", политика) is None


def test_poznat_na_rechnika_sloy_NE_se_dublira() -> None:
    """Намирането ДОПЪЛВА речника, не го заменя."""

    политика, намерени = _намери_armirovachni_sloeve(
        DEFAULT_RECOGNITION_POLICY, _армировъчен_чертеж("ARM_Bars"))

    assert намерени == [], "слоят вече минава по речника"
    assert _rebar_geometry_layer_role("ARM_Bars", политика) == "REBAR_LAYER"


def test_obyaveniyat_ot_chovek_sloy_ostava_s_negoviya_proizhod() -> None:
    """Думата на човека бие: обявеният слой не се преименува на „намерен"."""

    слой = "s-Reinf"
    обявена = dataclasses.replace(
        DEFAULT_RECOGNITION_POLICY, declared_rebar_geometry_layers=(слой,))
    политика, намерени = _намери_armirovachni_sloeve(
        обявена, _армировъчен_чертеж(слой))

    assert намерени == [], "вече обявеният слой не се намира втори път"
    assert _rebar_geometry_layer_role(слой, политика) == "DECLARED_REBAR_LAYER"


# --- ключът и безопасността -------------------------------------------------


def test_klyuchut_izklyuchva_namiraneto(monkeypatch) -> None:
    monkeypatch.setenv("LION_REBAR_GEOMETRY_DISCOVERY", "off")
    _политика, намерени = _намери_armirovachni_sloeve(
        DEFAULT_RECOGNITION_POLICY, _армировъчен_чертеж("s-Reinf"))
    assert намерени == []


@pytest.mark.parametrize("записи", [[], None])
def test_bez_obekti_ne_chupi(записи) -> None:
    политика, намерени = _намери_armirovachni_sloeve(
        DEFAULT_RECOGNITION_POLICY, записи or [])
    assert намерени == []
    assert политика is DEFAULT_RECOGNITION_POLICY


def test_gradezhka_v_razlichitelya_ne_vali_analiza(monkeypatch) -> None:
    """Намирането е допълнение: грешка в него не спира анализа."""

    import lion_rebar.dxf.rebar_geometry_evidence as различител

    def _гърми(*_args, **_kwargs):
        raise RuntimeError("нарочна грешка")

    monkeypatch.setattr(различител, "evaluate_rebar_geometry", _гърми)
    политика, намерени = _намери_armirovachni_sloeve(
        DEFAULT_RECOGNITION_POLICY, _армировъчен_чертеж("s-Reinf"))
    assert намерени == []
    assert политика is DEFAULT_RECOGNITION_POLICY
