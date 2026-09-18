"""Нова рамка се огъва по вече начертаните съседни (rc44, 2026-09-17).

Собственик, 2026-09-17: „съгласен съм да прилепват към съседни съществуващи,
т.е. да се огъват по нея“.

ЗАЩО. В „Вертикали до +8.90“ („Сграда бета“) чертежът е минал през състояние,
в което две зони стоят на 1,3 единици една от друга — Стб.Ядро B.3 и
„Тип 2c -215x25“. Измерено по петте запазени поколения на source DXF-а:
разстоянието между най-близките зони е 47,9 → **1,3** → 47,9 → 107,1, след
като собственикът ги е разместил в AutoCAD. При 1,3 единици правоъгълник с
два клика не може да раздели двете: операторът или мине в съседната зона,
или остави свои елементи навън.

Досега влизането в съседната зона беше отказ на две места: ``resolve_operator_area``
отказваше „в рамката попадат 2 заглавия“, а ако все пак минеше — ``area_for_point``
връщаше ``None`` при спор между заглавия и позициите оставаха без зона.

Сега новата рамка се чертае щедро и се огъва: не взима нищо, което друго
заглавие вече е взело. Огъването се смята ВЕДНЪЖ, при чертането, и се
записва — не се преизчислява при четене.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lion_rebar.export.operator_areas import (
    AREAS_NAME,
    KIND_BEAM,
    KIND_GROUP,
    OperatorAreaError,
    area_for_point,
    bend_around_existing,
    load_operator_areas,
    record_operator_area,
    withdraw_operator_area,
)
from lion_rebar.export.operator_group_box import (
    AreaTitle,
    OperatorBoxError,
    resolve_operator_area,
)
from lion_rebar.project.data_identity import DataIdentity

#: Двете съседни зони се застъпват по x между 90 и 100 — както в чертежа,
#: където щедрата рамка неизбежно влиза в съседния блок.
_ЛЯВА = (0.0, 0.0, 100.0, 100.0)
_ДЯСНА = (90.0, 0.0, 200.0, 100.0)
#: Точка в застъпването: тя е на лявата зона, защото тя я е взела първа.
_СПОРНА = (95.0, 50.0)


def _identity() -> DataIdentity:
    return DataIdentity(
        project_id="beta",
        source_sha256="SHA",
        source_generation_id="G1",
        analysis_run_id="R1",
    )


def _record(root: Path, box, handle: str, text: str, kind: str = KIND_GROUP):
    return record_operator_area(
        root,
        box=box,
        kind=kind,
        title_handle=handle,
        title_text=text,
        frame_index=1,
        decided_by="Александър",
        note="съседни зони на 1,3 единици",
        identity=_identity(),
    )


class TestОгъване:
    def test_новата_рамка_не_взима_позиция_от_съседната(self, tmp_path: Path) -> None:
        лява = _record(tmp_path, _ЛЯВА, "HA", "Стб.Ядро B.3")

        дясна = _record(tmp_path, _ДЯСНА, "HB", "Тип 2c -215x25")

        assert дясна.bent_around == (_ЛЯВА,)
        assert дясна.contains(*_СПОРНА) is False
        assert дясна.contains(150.0, 50.0) is True
        assert лява.contains(*_СПОРНА) is True

    def test_спорната_точка_вече_има_стопанин(self, tmp_path: Path) -> None:
        """Червен срещу стария код: там ``area_for_point`` връщаше ``None``.

        Две области с РАЗЛИЧНИ заглавия върху една точка бяха отказ — и то
        мълчалив: позицията оставаше без зона и блокираше експорта.
        """

        _record(tmp_path, _ЛЯВА, "HA", "Стб.Ядро B.3")
        _record(tmp_path, _ДЯСНА, "HB", "Тип 2c -215x25")

        areas = load_operator_areas(tmp_path, _identity())
        стопанин = area_for_point(areas, *_СПОРНА, kind=KIND_GROUP)

        assert стопанин is not None
        assert стопанин.title_text == "Стб.Ядро B.3"

    def test_огъването_преживява_записа_и_четенето(self, tmp_path: Path) -> None:
        _record(tmp_path, _ЛЯВА, "HA", "Стб.Ядро B.3")
        _record(tmp_path, _ДЯСНА, "HB", "Тип 2c -215x25")

        дясна = [
            area
            for area in load_operator_areas(tmp_path, _identity())
            if area.title_handle == "HB"
        ][0]

        assert дясна.bent_around == (_ЛЯВА,)
        assert дясна.contains(*_СПОРНА) is False

    def test_оттеглената_съседна_не_разширява_огънатата(self, tmp_path: Path) -> None:
        """Изрезката е решение, а не изчисление — не се връща сама.

        Оттегли ли се лявата, спорната точка остава ничия, докато операторът
        не начертае наново. Тихото разширяване върху освободеното би сменило
        зоната на позиции, без някой да е решавал.
        """

        лява = _record(tmp_path, _ЛЯВА, "HA", "Стб.Ядро B.3")
        _record(tmp_path, _ДЯСНА, "HB", "Тип 2c -215x25")

        assert withdraw_operator_area(
            tmp_path, лява.area_id, withdrawn_by="Александър", note="сгрешена"
        )

        areas = load_operator_areas(tmp_path, _identity())
        assert [area.title_handle for area in areas] == ["HB"]
        assert areas[0].contains(*_СПОРНА) is False
        assert area_for_point(areas, *_СПОРНА, kind=KIND_GROUP) is None


class TestКоеНеЕСъсед:
    def test_пречертаване_със_същото_заглавие_се_слива(self, tmp_path: Path) -> None:
        """Случаят „Гр. B.13 25/50“ от 2026-09-14 остава както си беше.

        Собственикът чертае рамка, тя не обхваща всичко, и той чертае втора
        малко по-широка върху същото заглавие. Това не е съсед, а
        пречертаване: няма какво да се огъва.
        """

        _record(tmp_path, _ЛЯВА, "HA", "Стб.Ядро B.3")

        по_широка = _record(
            tmp_path, (-5.0, -5.0, 105.0, 105.0), "HA", "Стб.Ядро B.3"
        )

        assert по_широка.bent_around == ()
        assert по_широка.contains(*_СПОРНА) is True

    def test_другият_вид_не_е_съсед(self, tmp_path: Path) -> None:
        """``BEAM`` и ``GROUP`` нарочно се застъпват — гредата и групата ѝ."""

        _record(tmp_path, _ЛЯВА, "HA", "Гр. B.19 25/55", kind=KIND_BEAM)

        групова = _record(tmp_path, _ДЯСНА, "HB", "Тип 2c -215x25")

        assert групова.bent_around == ()
        assert групова.contains(*_СПОРНА) is True

    def test_неприпокриваща_се_съседна_не_прави_изрезка(self, tmp_path: Path) -> None:
        _record(tmp_path, _ЛЯВА, "HA", "Стб.Ядро B.3")

        далечна = _record(tmp_path, (300.0, 0.0, 400.0, 100.0), "HB", "Ш.B.5")

        assert далечна.bent_around == ()


class TestОтказите:
    def test_рамка_изцяло_в_чужда_рамка_се_отказва(self, tmp_path: Path) -> None:
        """След огъването не остава нищо — това е отказ, не празна област."""

        _record(tmp_path, _ЛЯВА, "HA", "Стб.Ядро B.3")

        with pytest.raises(OperatorAreaError, match="изцяло"):
            _record(tmp_path, (10.0, 10.0, 50.0, 50.0), "HB", "Тип 2c -215x25")

    def test_заглавие_с_рамка_не_брои_за_конкуренция(self) -> None:
        """Иначе огъването няма как да се случи.

        Щедрата рамка захваща и съседното заглавие, а ``resolve_operator_area``
        отказваше още там — преди огъването да е стигнало до думата.
        """

        titles = [
            AreaTitle("HA", "Стб.Ядро B.3", 50.0, 50.0, 1, KIND_GROUP),
            AreaTitle("HB", "Тип 2c -215x25", 150.0, 50.0, 1, KIND_GROUP),
        ]

        with pytest.raises(OperatorBoxError, match="2 заглавия"):
            resolve_operator_area((0.0, 0.0), (200.0, 100.0), titles=titles)

        решение = resolve_operator_area(
            (0.0, 0.0), (200.0, 100.0), titles=titles, claimed_title_handles=["HA"]
        )

        assert решение.title_handle == "HB"

    def test_всички_заглавия_вече_имат_рамка(self) -> None:
        titles = [AreaTitle("HA", "Стб.Ядро B.3", 50.0, 50.0, 1, KIND_GROUP)]

        with pytest.raises(OperatorBoxError, match="вече има своя рамка"):
            resolve_operator_area(
                (0.0, 0.0), (200.0, 100.0), titles=titles, claimed_title_handles=["HA"]
            )


class TestСтариЗаписи:
    def test_запис_без_изрезки_си_остава_правоъгълник(self, tmp_path: Path) -> None:
        """Областите, записани преди 17.09, нямат ``bent_around``."""

        _record(tmp_path, _ЛЯВА, "HA", "Стб.Ядро B.3")
        path = tmp_path / "working" / AREAS_NAME
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["areas"][0].pop("bent_around", None)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        area = load_operator_areas(tmp_path, _identity())[0]

        assert area.bent_around == ()
        assert area.contains(*_СПОРНА) is True

    def test_повреден_ред_в_изрезките_се_пропуска(self, tmp_path: Path) -> None:
        """Пропусната изрезка би разширила рамката върху чужда територия."""

        _record(tmp_path, _ЛЯВА, "HA", "Стб.Ядро B.3")
        _record(tmp_path, _ДЯСНА, "HB", "Тип 2c -215x25")
        path = tmp_path / "working" / AREAS_NAME
        payload = json.loads(path.read_text(encoding="utf-8"))
        for entry in payload["areas"]:
            if entry["title_handle"] == "HB":
                entry["bent_around"] = [[0.0, 0.0, 100.0], "боклук", [0, 0, 0, 0]]
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        дясна = [
            area
            for area in load_operator_areas(tmp_path, _identity())
            if area.title_handle == "HB"
        ][0]

        assert дясна.bent_around == ()


class TestИзчислението:
    def test_огъване_по_няколко_съседни(self) -> None:
        areas = [
            _ОбластЗаТест(_ЛЯВА, KIND_GROUP, "HA"),
            _ОбластЗаТест((190.0, 0.0, 300.0, 100.0), KIND_GROUP, "HC"),
            _ОбластЗаТест((0.0, 300.0, 100.0, 400.0), KIND_GROUP, "HD"),
        ]

        изрезки = bend_around_existing(
            _ДЯСНА, areas, kind=KIND_GROUP, title_handle="HB"
        )

        assert изрезки == (_ЛЯВА, (190.0, 0.0, 300.0, 100.0))


class _ОбластЗаТест:
    """Само полетата, които ``bend_around_existing`` чете."""

    def __init__(self, box, kind: str, title_handle: str) -> None:
        self.x0, self.y0, self.x1, self.y1 = box
        self.kind = kind
        self.title_handle = title_handle
