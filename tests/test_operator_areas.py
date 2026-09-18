"""Рамката се пази като ОБЛАСТ и преживява анализа (rc44).

Собственик, 2026-09-13: „да направим рамките преди теста на чертежа, за да
може програмата да го прочете."

Дотогава рамката с два клика се превръщаше веднага в решения за отделни
позиции — тоест можеше да се чертае едва СЛЕД като анализът е произвел
позиции и Финалният контрол ги е блокирал. Тук се проверява обратният ред:
областта се записва преди анализа и той я чете.

Най-важното твърдение е последното: област, записана преди анализа, НЕ бива
да се обезсилва от самия анализ. Решенията за позиции сверяват
``analysis_run_id`` и с право — те се вземат върху резултат от точно този
прогон. Областта се чертае преди прогона; ако и тя сверяваше run id, щеше да
умира точно в мига, в който за пръв път трябва да свърши работа.
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
    areas_summary,
    audit_trail,
    load_operator_areas,
    normalise_box,
    record_operator_area,
    withdraw_operator_area,
)
from lion_rebar.project.data_identity import DataIdentity

# Истинската геометрия на s-9: заглавието „Гр. B.19 25/55" стои на
# (113837, 2678), а четирите неразпознати детайла — над и вляво от него.
_BEAM_BOX = (113317.0, 2638.0, 113897.0, 2938.0)


def _identity(run: str = "R1", generation: str = "G1") -> DataIdentity:
    return DataIdentity(
        project_id="s-9",
        source_sha256="SHA",
        source_generation_id=generation,
        analysis_run_id=run,
    )


def _record(root: Path, **kw):
    params = {
        "box": _BEAM_BOX,
        "kind": KIND_BEAM,
        "title_handle": "BL19",
        "title_text": "Гр. B.19 25/55",
        "frame_index": 6,
        "decided_by": "Александър",
        "note": "детайлите са над заглавието на Гр. B.19",
        "identity": _identity(),
    }
    params.update(kw)
    return record_operator_area(root, **params)


class TestЧертане:
    def test_посоката_на_влаченето_няма_значение(self) -> None:
        assert normalise_box((10.0, 20.0), (0.0, 5.0)) == (0.0, 5.0, 10.0, 20.0)

    def test_областта_се_записва_и_прочита(self, tmp_path: Path) -> None:
        area = _record(tmp_path)

        loaded = load_operator_areas(tmp_path, _identity())

        assert [a.area_id for a in loaded] == [area.area_id]
        assert loaded[0].title_text == "Гр. B.19 25/55"
        assert loaded[0].kind == KIND_BEAM
        assert areas_summary(loaded) == {KIND_BEAM: 1, KIND_GROUP: 0}

    def test_записът_оставя_одитна_следа(self, tmp_path: Path) -> None:
        _record(tmp_path)

        trail = audit_trail(tmp_path)

        assert [e["event"] for e in trail] == ["AREA_RECORDED"]
        assert trail[0]["decided_by"] == "Александър"

    @pytest.mark.parametrize(
        "kw, съобщение",
        [
            ({"box": (10.0, 10.0, 10.0, 20.0)}, "нулева"),
            ({"note": "х"}, "Обосновката"),
            ({"decided_by": "  "}, "автор"),
            ({"title_handle": ""}, "заглавие"),
            ({"kind": "НЕЩО"}, "Непознат вид"),
        ],
    )
    def test_невалидна_област_се_отказва(self, tmp_path: Path, kw, съобщение) -> None:
        with pytest.raises(OperatorAreaError, match=съобщение):
            _record(tmp_path, **kw)

        assert load_operator_areas(tmp_path, _identity()) == ()


class TestЧетене:
    def test_точка_вътре_намира_областта(self, tmp_path: Path) -> None:
        _record(tmp_path)
        areas = load_operator_areas(tmp_path, _identity())

        found = area_for_point(areas, 113382.0, 2762.0, kind=KIND_BEAM)

        assert found is not None
        assert found.title_text == "Гр. B.19 25/55"

    def test_точка_вън_не_намира_нищо(self, tmp_path: Path) -> None:
        _record(tmp_path)
        areas = load_operator_areas(tmp_path, _identity())

        assert area_for_point(areas, 999999.0, 0.0, kind=KIND_BEAM) is None

    def test_друг_вид_не_се_бърка(self, tmp_path: Path) -> None:
        """Област за греда не бива да раздава групови етикети и обратно."""

        _record(tmp_path)
        areas = load_operator_areas(tmp_path, _identity())

        assert area_for_point(areas, 113382.0, 2762.0, kind=KIND_GROUP) is None

    def test_записана_без_изрезка_припокриваща_се_област_е_отказ(
        self, tmp_path: Path
    ) -> None:
        """Защитата от 2026-09-14 остава за записите, които нямат изрезка.

        До 17.09.2026 две области с различни заглавия върху една точка се
        отменяха взаимно — двусмислието спираше, вместо да се гадае. От
        18.09 новата рамка се огъва още при чертането и такова застъпване
        вече не се създава (``bend_around_existing``).

        Но област, записана ПРЕДИ това, няма изрезка. За нея отговорът си
        остава отказ: там наистина никой не е решавал чия е точката. Тук
        изрезката се маха от записа, за да се провери точно този случай.
        """

        _record(tmp_path)
        _record(
            tmp_path,
            box=(113300.0, 2600.0, 113900.0, 2999.0),
            title_handle="BL8",
            title_text="Гр. B.8 25/55",
        )
        path = tmp_path / "working" / AREAS_NAME
        payload = json.loads(path.read_text(encoding="utf-8"))
        for entry in payload["areas"]:
            entry["bent_around"] = []
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        areas = load_operator_areas(tmp_path, _identity())
        assert len(areas) == 2

        assert area_for_point(areas, 113382.0, 2762.0, kind=KIND_BEAM) is None

    def test_новата_област_се_огъва_и_точката_има_стопанин(
        self, tmp_path: Path
    ) -> None:
        """Собственик, 2026-09-18: огъването важи и за рамките на греди.

        Втората рамка не взима нищо от първата, затова спорът изчезва и
        позицията остава при гредата, която я е обхванала първа.
        """

        _record(tmp_path)
        _record(
            tmp_path,
            box=(113300.0, 2600.0, 113900.0, 2999.0),
            title_handle="BL8",
            title_text="Гр. B.8 25/55",
        )
        areas = load_operator_areas(tmp_path, _identity())

        стопанин = area_for_point(areas, 113382.0, 2762.0, kind=KIND_BEAM)

        assert стопанин is not None
        assert стопанин.title_text == "Гр. B.19 25/55"

    def test_чужда_рамка_не_се_чете(self, tmp_path: Path) -> None:
        _record(tmp_path)
        areas = load_operator_areas(tmp_path, _identity())

        assert area_for_point(
            areas, 113382.0, 2762.0, kind=KIND_BEAM, frame_index=1
        ) is None


class TestIdentity:
    def test_област_от_друг_проект_не_се_наследява(self, tmp_path: Path) -> None:
        _record(tmp_path)

        друг = DataIdentity(
            project_id="s-7", source_sha256="SHA",
            source_generation_id="G1", analysis_run_id="R1",
        )

        assert load_operator_areas(tmp_path, друг) == ()

    def test_променен_чертеж_обезсилва_областта(self, tmp_path: Path) -> None:
        _record(tmp_path)

        assert load_operator_areas(tmp_path, _identity(generation="G2")) == ()

    def test_областта_преживява_нов_анализ(self, tmp_path: Path) -> None:
        """Сърцевината на цялата функция.

        Операторът чертае рамката, ПОСЛЕ пуска анализа. Анализът започва с
        ``begin_analysis_run``, който дава нов ``analysis_run_id``. Ако
        областта се сверяваше по анализ, тя щеше да е мъртва още при първото
        си ползване — и рамката пак щеше да върши работа само СЛЕД анализа,
        тоест точно това, което собственикът поиска да се смени.
        """

        _record(tmp_path, identity=_identity(run="ПРЕДИ"))

        loaded = load_operator_areas(tmp_path, _identity(run="СЛЕД"))

        assert [a.title_text for a in loaded] == ["Гр. B.19 25/55"]


class TestОттегляне:
    def test_оттеглената_област_изчезва_и_остава_в_одита(self, tmp_path: Path) -> None:
        area = _record(tmp_path)

        assert withdraw_operator_area(
            tmp_path, area.area_id, withdrawn_by="Александър", note="сгрешена рамка"
        )

        assert load_operator_areas(tmp_path, _identity()) == ()
        assert [e["event"] for e in audit_trail(tmp_path)] == [
            "AREA_RECORDED", "AREA_WITHDRAWN",
        ]

    def test_оттегляне_на_несъществуваща_област(self, tmp_path: Path) -> None:
        assert not withdraw_operator_area(
            tmp_path, "няма-такава", withdrawn_by="А", note="проба"
        )

    def test_оттеглянето_иска_мотив(self, tmp_path: Path) -> None:
        area = _record(tmp_path)

        with pytest.raises(OperatorAreaError, match="Обосновката"):
            withdraw_operator_area(
                tmp_path, area.area_id, withdrawn_by="А", note=""
            )

        assert len(load_operator_areas(tmp_path, _identity())) == 1
