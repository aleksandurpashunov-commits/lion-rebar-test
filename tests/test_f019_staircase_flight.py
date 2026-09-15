"""Z-образният прът е СТЪЛБИЩНО РАМО, не снаждане (rc44).

Собственик, 2026-09-13, за реалната позиция „4N16x323" в чертежа на
шайбите — [60, 203, 60] cm под 42°: „Като тази за стълбищен манш."

Историята на този ред е показателна и заслужава да остане записана:

  1. Първо минаваше като **F002 (прав прът с редукция)** без преглед. Грешно
     и опасно: F002 е снаждане, тоест средният участък е късата наклонена
     връзка между два дълги пръта. Тук средният участък е 203 cm — самият
     марш.
  2. След поправката от същия ден вече не минаваше НИКАК и оставаше без
     форма, с предложение F003 [90, 143, 90] от случайна котировка.
  3. Сега се разпознава като **F019 СТЪЛБИЩНО РАМО 5**.

Разграничението между F002 и F019 не е ъгълът, а кой участък е дълъг:

    F002 РЕДУКЦИЯ   два дълги пръта, КРАТКО наклонено снаждане  [150, 21, 310]
    F019 СТЪЛБИЩНО  късо рамо, ДЪЛЪГ наклонен марш, късо рамо    [60, 203, 60]

Ъгълът нарочно не участва: в ENGINE F019 е с ``angle_mode='AUTO'`` и без
``min/max_angle_deg``, защото стълбищата се проектират с различен наклон.
Контролната позиция в ENGINE е 8N10x406 [50, 286, 70] при около 29°;
реалната тук е под 42°.
"""
from __future__ import annotations

import pytest

from lion_rebar.dxf.proxy_adapter import three_leg_shape_from_directions
from lion_rebar.export.master_engine_v031 import (
    MasterDimensionError,
    parameters_from_segments,
    total_for,
)

# Реалната геометрия, измерена от чертежа: 60.00 @ 0°, 202.62 @ −42.3°,
# 60.00 @ 0°, плюс две истински дъги за огъвките. Сборът 322.62 отговаря на
# декларираните 323 cm.
_Z_DIRECTIONS = (0.0, -42.3, 0.0)
_Z_SEGMENTS = (60.0, 203.0, 60.0)
_Z_TOTAL = 323.0

# Контролната позиция, записана в самата ENGINE дефиниция на F019.
_ENGINE_DIRECTIONS = (0.0, -29.0, 0.0)
_ENGINE_SEGMENTS = (50.0, 286.0, 70.0)
_ENGINE_TOTAL = 406.0


class TestРазпознаване:
    def test_дългият_среден_участък_е_стълбищно_рамо(self) -> None:
        assert three_leg_shape_from_directions(_Z_DIRECTIONS, _Z_SEGMENTS) == "F019"

    def test_контролната_позиция_от_engine_също(self) -> None:
        """Друг наклон, същата форма — ъгълът не е част от определението."""

        assert (
            three_leg_shape_from_directions(_ENGINE_DIRECTIONS, _ENGINE_SEGMENTS)
            == "F019"
        )

    def test_късият_среден_участък_остава_снаждане(self) -> None:
        """Истинското F002 от чертежа с колоните не бива да се премести."""

        assert (
            three_leg_shape_from_directions((0.0, 30.0, 0.0), (150.0, 21.0, 310.0))
            == "F002"
        )

    def test_правите_ъгли_остават_U_форма(self) -> None:
        assert (
            three_leg_shape_from_directions((90.0, 0.0, 90.0), (20.0, 200.0, 20.0))
            == "F003"
        )

    @pytest.mark.parametrize(
        "directions, segments, защо",
        [
            ((0.0, -42.0, 0.0), (60.0, 60.0, 203.0), "средният е равен на крайния"),
            ((0.0, -42.0, 50.0), (60.0, 203.0, 60.0), "външните рамена не са паралелни"),
            ((0.0, 0.0, 0.0), (60.0, 203.0, 60.0), "няма чупка изобщо"),
        ],
    )
    def test_неясното_отива_при_оператора(self, directions, segments, защо) -> None:
        """FAIL-CLOSED: съмнението не се превръща в най-близката форма."""

        assert three_leg_shape_from_directions(directions, segments) is None, защо

    def test_наклонът_не_променя_формата(self) -> None:
        """Един и същи прът при всеки наклон си остава стълбищно рамо."""

        for наклон in (-15.0, -29.0, -42.3, -60.0, -80.0):
            assert (
                three_leg_shape_from_directions((0.0, наклон, 0.0), _Z_SEGMENTS)
                == "F019"
            ), f"наклон {наклон}°"


class TestПроизводствениРазмери:
    def test_трите_сегмента_са_трите_размера(self) -> None:
        """Наклонът не скъсява нито един от тях, затова няма проекции."""

        parameters = parameters_from_segments(
            "F019",
            _Z_SEGMENTS,
            production_length_cm=_Z_TOTAL,
            turn_angles_deg=(42.3, 42.3),
        )

        assert parameters.values == {"L1": 60.0, "L2": 203.0, "L3": 60.0}
        assert parameters.expanded_segments_cm == _Z_SEGMENTS
        assert parameters.total_length_cm == _Z_TOTAL

    def test_същите_размери_при_друг_наклон(self) -> None:
        at_29 = parameters_from_segments(
            "F019", _Z_SEGMENTS, production_length_cm=_Z_TOTAL,
            turn_angles_deg=(29.0, 29.0),
        )
        at_42 = parameters_from_segments(
            "F019", _Z_SEGMENTS, production_length_cm=_Z_TOTAL,
            turn_angles_deg=(42.3, 42.3),
        )

        assert at_29.values == at_42.values

    def test_контролната_позиция_от_engine_се_смята(self) -> None:
        parameters = parameters_from_segments(
            "F019", _ENGINE_SEGMENTS, production_length_cm=_ENGINE_TOTAL,
            turn_angles_deg=(29.0, 29.0),
        )

        assert parameters.values == {"L1": 50.0, "L2": 286.0, "L3": 70.0}

    def test_общата_дължина_е_сборът(self) -> None:
        assert total_for("F019", {"L1": 60.0, "L2": 203.0, "L3": 60.0}) == 323.0

    def test_грешен_брой_сегменти_се_отказва(self) -> None:
        with pytest.raises(MasterDimensionError, match="F019 очаква 3 сегмента"):
            parameters_from_segments(
                "F019", (60.0, 203.0), production_length_cm=263.0,
                turn_angles_deg=(42.3,),
            )

    def test_сегменти_които_не_затварят_се_отказват(self) -> None:
        """Декларация, която не дава обявената дължина, не е доказателство."""

        with pytest.raises(MasterDimensionError):
            parameters_from_segments(
                "F019", (60.0, 203.0, 60.0), production_length_cm=400.0,
                turn_angles_deg=(42.3, 42.3),
            )


class TestРисунка:
    """Операторът трябва да ВИДИ формата, не само кода ѝ."""

    def test_стълбищното_рамо_се_рисува(self) -> None:
        from lion_rebar.export.master_engine_v031 import render_master_svg

        svg = render_master_svg("F019", {"L1": 60.0, "L2": 203.0, "L3": 60.0})

        assert "<svg" in svg
        for размер in (">60<", ">203<"):
            assert размер in svg, f"липсва {размер}"

    def test_рисунката_има_два_завоя(self) -> None:
        """Силуетът е Z: две къси рамена и дълъг наклонен марш между тях."""

        import re

        from lion_rebar.export.master_engine_v031 import render_master_svg

        svg = render_master_svg("F019", {"L1": 60.0, "L2": 203.0, "L3": 60.0})
        points = re.search(r'class="rebar"[^>]*points="([^"]+)"', svg)
        if points is None:
            points = re.search(r'points="([^"]+)"', svg)
        assert points is not None, "рисунката няма начупена линия"
        vertices = [
            tuple(float(v) for v in pair.split(","))
            for pair in points.group(1).split()
        ]
        assert len(vertices) == 4, "стълбищното рамо е три сегмента, тоест 4 върха"
        # Първото и последното рамо са хоризонтални, маршът между тях -- не.
        assert vertices[0][1] == vertices[1][1]
        assert vertices[2][1] == vertices[3][1]
        assert vertices[1][1] != vertices[2][1]
