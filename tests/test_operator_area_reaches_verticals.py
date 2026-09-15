"""Рамката стига до вертикалите по целия път (rc44).

Собственик, 2026-09-15: „искам да знам дали всичко работи."

Два дефекта, които никой модулен тест не можеше да види, защото и двете
страни поотделно са верни — липсваше връзката между тях. Намерени чак
когато целият път беше изминат върху копие на ``s-10``: рамка → анализ →
финален контрол → експорт.

ПЪРВИ: операторът не можеше да огради вертикален елемент
--------------------------------------------------------
``collect_area_titles`` събираше само греди, шайби и колонни групи. В
``s-10`` то връщаше НУЛА заглавия, тоест всяка рамка се отказваше с „в
рамката няма заглавие". Вертикалните секции се разпознават от
``_detect_vertical_section_labels``, но рамката не ги питаше.

ВТОРИ: мъртъв възел между контрола и експорта
---------------------------------------------
``_freeze_beam_assignments`` замразява вертикалната секция през същата
``_assign_vertical_section``, но БЕЗ да ѝ подаде областите. Контролът
замразяваше по геометрия, а експортът четеше по рамката:

    Export:          „Frozen vertical section for 25N8x395 no longer
                      matches the DXF. Run Final Control again."
    Финален контрол: блокиран от същата проверка.

Двете съобщения се пращаха едно към друго и проектът засядаше.
"""
from __future__ import annotations

import inspect
import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class _Секция:
    frame_index: int = 1
    title_handle: str = "552C"
    divider_handle: str = "D1"
    title: str = "K.B.11,11a - 88x25 - 2 бр."
    kind: str = "COLUMN"
    order: int = 1
    xmin: float = 0.0
    xmax: float = 100.0
    divider_y: float = 50.0


@dataclass
class _Обект:
    handle: str = "552C"
    entity_type: str = "TEXT"
    layer: str = "ARMTXT"
    text: str = "K.B.11,11a - 88x25 - 2 бр."
    geometry: dict = field(default_factory=lambda: {"insert": (10.0, 20.0, 0.0)})


class TestРамкатаВижаВертикалнитеСекции:
    """Иначе „Рамка с два клика" се отказва във всеки чертеж с вертикали."""

    def test_заглавието_на_секция_може_да_се_огради(self, monkeypatch) -> None:
        import lion_rebar.export.excel_export as ee
        import lion_rebar.export.operator_group_box as ogb

        monkeypatch.setattr(
            ee, "_detect_vertical_section_labels", lambda *a, **k: [_Секция()]
        )
        monkeypatch.setattr(ee, "_detect_beam_labels", lambda *a, **k: [])

        заглавия = ogb.collect_area_titles([_Обект()], [])
        assert [t.handle for t in заглавия] == ["552C"]
        assert заглавия[0].kind == "GROUP"
        assert заглавия[0].text == "K.B.11,11a - 88x25 - 2 бр."

    def test_handle_ът_е_същият_по_който_после_се_разпознава(self, monkeypatch) -> None:
        """Различен handle = мълчаливо безполезна рамка."""

        import lion_rebar.export.excel_export as ee
        import lion_rebar.export.operator_group_box as ogb

        monkeypatch.setattr(
            ee, "_detect_vertical_section_labels", lambda *a, **k: [_Секция()]
        )
        monkeypatch.setattr(ee, "_detect_beam_labels", lambda *a, **k: [])

        заглавие = ogb.collect_area_titles([_Обект()], [])[0]
        assert заглавие.handle == _Секция().title_handle


class TestКонтролътЗамразяваПоСъщотоПравило:
    """Контролът и експортът трябва да питат едно и също, иначе заседват."""

    def test_областите_стигат_до_замразяването(self) -> None:
        from lion_rebar.project import integrity

        # По изходния код: сглобяването на цял Финален контрол иска проект,
        # engine и замразен DXF, а питаното е едно — че извикването носи
        # областите.
        код = inspect.getsource(integrity._freeze_beam_assignments)
        начало = код.index("vertical_assignment = _assign_vertical_section(")
        край = код.index("if vertical_assignment is None", начало)
        assert "operator_areas=operator_areas" in код[начало:край], (
            "Финалният контрол замразява вертикалната секция без областите: "
            "той решава едно, експортът друго, и проектът засяда"
        )


class TestВсичкиПътищаЧетатОбластите:
    """Всяко място, което пита за вертикална секция, трябва да ги подава."""

    def test_няма_извикване_без_области(self) -> None:
        import lion_rebar.export.excel_export as ee
        from lion_rebar.project import integrity

        пропуснати: list[str] = []
        for модул in (ee, integrity):
            код = inspect.getsource(модул)
            for m in re.finditer(
                r"_assign_vertical_section\(\n(.*?)\n\s*\)", код, re.S
            ):
                if "operator_areas" not in m.group(1):
                    пропуснати.append(модул.__name__)
        assert not пропуснати, (
            f"извикване без операторски области в: {sorted(set(пропуснати))}"
        )
