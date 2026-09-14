"""Позицията влиза при гредата си, не при рамката, в която е начертана (rc44).

Собственик, 2026-09-14, като видя описа: „виж в лист Детайли — има позиции от
гредите."

В ``s-9`` шест греди — B.1, B.7, B.13, B.18, B.24, B.29 — са начертани в
чертожната рамка ДЕТАЙЛИ. Листът се избираше по рамката, затова армировката
им излизаше в лист „детайли" **анонимно**: Гр. B.7 и Гр. B.24 нямаха НИТО
ЕДНА позиция в листа с гредите, макар цялата им армировка да е в чертежа.

Арматуристът реже по греди, не по чертожни рамки. Затова позиция с доказана
греда отива при нея.

Мярка върху реалния проект: лист „детайли" 18 → 2 позиции, лист „греди"
149 → 165, и всичките 32 греди вече присъстват. Двете, които остават в
„детайли", са на ~10 800 единици от всяко заглавие — те наистина не са на
греда.

ВТОРИЯТ ДЕФЕКТ, ОТКРИТ В СЪЩИЯ ЧЕРТЕЖ
--------------------------------------
Собственикът беше начертал ДВЕ припокриващи се рамки за Гр. B.13 (първата не
обхващаше всичко, втората — по-широка). Правилото „две области върху една
точка = отказ" ги отменяше взаимно и шест позиции оставаха без греда, което
блокираше експорта. Но двете сочеха ЕДНА И СЪЩА греда — спор няма и
отговорът е един. Отказът важи само когато заглавията са различни.
"""
from __future__ import annotations

from pathlib import Path

from lion_rebar.export.operator_areas import (
    KIND_BEAM,
    area_for_point,
    load_operator_areas,
    record_operator_area,
)
from lion_rebar.project.data_identity import DataIdentity

_BOX_A = (112855.0, 2731.0, 114832.0, 3172.0)   # първата рамка за Гр. B.13
_BOX_B = (112888.0, 2739.0, 114842.0, 3229.0)   # втората, по-широка
_ВЪТРЕ = (113400.0, 2800.0)


def _identity() -> DataIdentity:
    return DataIdentity(
        project_id="s-9", source_sha256="SHA",
        source_generation_id="G1", analysis_run_id="R1",
    )


def _area(root: Path, box, handle: str, title: str) -> None:
    record_operator_area(
        root,
        box=box,
        kind=KIND_BEAM,
        title_handle=handle,
        title_text=title,
        frame_index=5,
        decided_by="Александър",
        note="вярно",
        identity=_identity(),
    )


class TestПрипокриващиСеРамки:
    def test_две_рамки_за_същата_греда_не_са_спор(self, tmp_path: Path) -> None:
        """Реалният случай от ``s-9``: втора, по-широка рамка върху същата."""

        _area(tmp_path, _BOX_A, "BL13", "Гр. B.13 25/50")
        _area(tmp_path, _BOX_B, "BL13", "Гр. B.13 25/50")
        areas = load_operator_areas(tmp_path, _identity())
        assert len(areas) == 2

        found = area_for_point(areas, _ВЪТРЕ[0], _ВЪТРЕ[1], kind=KIND_BEAM)

        assert found is not None
        assert found.title_text == "Гр. B.13 25/50"

    def test_две_рамки_за_различни_греди_остават_отказ(self, tmp_path: Path) -> None:
        """Тук наистина не се знае чия е позицията — и това не се гадае."""

        _area(tmp_path, _BOX_A, "BL13", "Гр. B.13 25/50")
        _area(tmp_path, _BOX_B, "BL14", "Гр. B.14 25/55")
        areas = load_operator_areas(tmp_path, _identity())

        found = area_for_point(areas, _ВЪТРЕ[0], _ВЪТРЕ[1], kind=KIND_BEAM)

        assert found is None

    def test_три_рамки_за_същата_греда_също_минават(self, tmp_path: Path) -> None:
        """Броят не е мерилото — еднаквостта на отговора е."""

        _area(tmp_path, _BOX_A, "BL13", "Гр. B.13 25/50")
        _area(tmp_path, _BOX_B, "BL13", "Гр. B.13 25/50")
        _area(
            tmp_path,
            (112800.0, 2700.0, 115000.0, 3300.0),
            "BL13",
            "Гр. B.13 25/50",
        )
        areas = load_operator_areas(tmp_path, _identity())

        found = area_for_point(areas, _ВЪТРЕ[0], _ВЪТРЕ[1], kind=KIND_BEAM)

        assert found is not None

    def test_едната_различна_разваля_съгласието(self, tmp_path: Path) -> None:
        _area(tmp_path, _BOX_A, "BL13", "Гр. B.13 25/50")
        _area(tmp_path, _BOX_B, "BL13", "Гр. B.13 25/50")
        _area(
            tmp_path,
            (112800.0, 2700.0, 115000.0, 3300.0),
            "BL14",
            "Гр. B.14 25/55",
        )
        areas = load_operator_areas(tmp_path, _identity())

        found = area_for_point(areas, _ВЪТРЕ[0], _ВЪТРЕ[1], kind=KIND_BEAM)

        assert found is None

    def test_точка_извън_всички_си_остава_без_греда(self, tmp_path: Path) -> None:
        _area(tmp_path, _BOX_A, "BL13", "Гр. B.13 25/50")
        _area(tmp_path, _BOX_B, "BL13", "Гр. B.13 25/50")
        areas = load_operator_areas(tmp_path, _identity())

        assert area_for_point(areas, 1.0, 1.0, kind=KIND_BEAM) is None
