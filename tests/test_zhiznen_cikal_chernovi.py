# -*- coding: utf-8 -*-
"""Жизненият цикъл на черновата за форма: две редакции, два проекта, два процеса.

Точка 4 от `PRODALZHENIE.md` иска проектната форма да минава през НОРМАЛНИЯ път
и жизненият цикъл да е проверен: прекъснат анализ, смяна на проект, едновременен
запис. Нормалният път е `MainWindow._write_manual_shape_draft` → черновата в
`working/manual_shape_drafts.json` + кандидатът в `working/shape_candidates/`.

Какво е измерено преди тези тестове (29.09.2026):

* черновата **не влиза в разпознаването** — `manual_shape_drafts.json` се чете
  само от своя записвач и от диагностичния пакет
  (`grep -rn manual_shape_drafts lion_rebar/` дава `ui/main_window.py` и
  `diagnostics/bundle.py`). Затова застояла чернова не може да смени инженерен
  резултат; тя е запис на операторско намерение.
* шестте записа в `main_window` ползваха **едно и също име** на временния файл
  (`<име>.json.tmp`). При два процеса върху един проект — собственикът държи
  приложението отворено, а втори екземпляр пише в същия проект — единият
  презаписва временния файл на другия и `replace` може да качи частично
  съдържание. Сега минават през `utils.files.atomic_write_text`
  (`tempfile.mkstemp` + `fsync` + `os.replace`).
* черновата не носеше самоличността на данните, затова не можеше да се различи
  върху коя редакция на чертежа е направена.
"""
from __future__ import annotations

import json
import logging
import types
from pathlib import Path

import pytest

from lion_rebar.project.data_identity import begin_source_generation, current_identity
from lion_rebar.project.models import ProjectModel
from lion_rebar.ui.main_window import MainWindow


def _payload(handle: str = "9C5", *, точки=None) -> dict:
    """Същият вид товар, който Review Center подава на записа."""

    точки = точки or ((10.0, 10.0), (110.0, 10.0), (110.0, -40.0))
    return {
        "shape_name": "Проектна форма",
        "reason": "Липсва MASTER форма",
        "note": "Проверена избрана геометрия.",
        "save_training_example": False,
        "geometry": {
            "handles": ["9C6", "9C7"],
            "entity_types": ["LINE"],
            "layers": ["ARM-GORNA"],
            "points": [list(т) for т in точки],
            "closed": False,
            "connected": True,
            "physical_connected": True,
            "branched": False,
            "segment_count": 2,
            "segment_lengths_cm": [100.0, 50.0],
            "turn_angles_deg": [-90.0],
            "total_length_cm": 150.0,
            "canonical_signature": "O|n=2|r=0.33333,0.66667|a=-90.00",
            "component_count": 1,
            "inferred_composite": False,
            "near_gap_healed": False,
            "training_eligible": True,
            "warnings": [],
        },
        "dimensions": {
            "source": "ANNOTATION",
            "production_length_cm": 150.0,
            "production_segment_lengths_cm": [],
            "measured_geometry_length_cm": 150.0,
        },
        "annotation": {
            "handle": handle,
            "text": "8N10x150",
            "count": 8,
            "diameter_mm": 10,
            "declared_length_cm": 150.0,
        },
    }


class _Прозорец:
    """Само това, което записът пипа от `self` — без Qt и без engine.

    Методите се вземат от истинския `MainWindow`, за да се проверява НОРМАЛНИЯТ
    път, а не копие на логиката му.
    """

    def __init__(self, проект: ProjectModel) -> None:
        self.project = проект
        self.logger = logging.getLogger("zhiznen-cikal")

    _write_manual_shape_draft = MainWindow._write_manual_shape_draft
    _append_manual_training_example = MainWindow._append_manual_training_example


def _проект(корен: Path, име: str, *, sha: str) -> ProjectModel:
    корен.mkdir(parents=True, exist_ok=True)
    проект = ProjectModel(name=име, root=str(корен))
    begin_source_generation(проект, source_sha256=sha)
    return проект


def _чернови(корен: Path) -> dict:
    път = корен / "working" / "manual_shape_drafts.json"
    return json.loads(път.read_text(encoding="utf-8-sig"))


def test_chernovata_nosi_samolichnostta_na_redakciyata(tmp_path: Path):
    """Прекъснат анализ и нов внос: чия е черновата се вижда, не се гадае.

    Черновата се прави върху една редакция на чертежа. Ако чертежът бъде внесен
    наново, старата чернова остава на диска (тя е операторско намерение, не
    резултат) — но трябва да е ясно за коя редакция е. Без печат на
    самоличността същият handle в новата редакция може да е друг прът.
    """

    проект = _проект(tmp_path / "проект", "ЕДИН", sha="a" * 64)
    прозорец = _Прозорец(проект)
    първа = current_identity(проект)

    прозорец._write_manual_shape_draft(_payload("9C5"))
    записано = _чернови(Path(проект.root))
    assert [д["data_identity"]["source_generation_id"] for д in записано["drafts"]] == [
        първа.source_generation_id
    ]

    # Чертежът е внесен наново — нова редакция, същият handle.
    втора = begin_source_generation(проект, source_sha256="b" * 64)
    assert втора.source_generation_id != първа.source_generation_id
    прозорец._write_manual_shape_draft(_payload("9C5"))

    записано = _чернови(Path(проект.root))
    assert len(записано["drafts"]) == 1
    assert записано["drafts"][0]["data_identity"]["source_generation_id"] == (
        втора.source_generation_id
    )
    # Старата отива в историята СЪС своята самоличност — проследимо, не изтрито.
    assert len(записано["history"]) == 1
    assert записано["history"][0]["data_identity"]["source_generation_id"] == (
        първа.source_generation_id
    )
    assert записано["history"][0]["replaced_at"]


def test_dve_chernovi_v_dva_proekta_ne_se_smesvat(tmp_path: Path):
    """Смяна на проект: решенията не преминават между проекти."""

    едно = _проект(tmp_path / "едно", "ЕДНО", sha="c" * 64)
    друго = _проект(tmp_path / "друго", "ДРУГО", sha="d" * 64)

    _Прозорец(едно)._write_manual_shape_draft(_payload("9C5"))
    _Прозорец(друго)._write_manual_shape_draft(
        _payload("9C5", точки=((0.0, 0.0), (200.0, 0.0), (200.0, -60.0)))
    )

    първо = _чернови(Path(едно.root))
    второ = _чернови(Path(друго.root))
    assert първо["project_name"] == "ЕДНО"
    assert второ["project_name"] == "ДРУГО"
    assert len(първо["drafts"]) == len(второ["drafts"]) == 1
    assert (първо["drafts"][0]["data_identity"]["project_id"]
            != второ["drafts"][0]["data_identity"]["project_id"])
    # Геометрията на единия не се вижда в другия.
    assert първо["drafts"][0]["geometry"]["total_length_cm"] == 150.0
    assert второ["drafts"][0]["geometry"]["total_length_cm"] == 150.0
    assert (първо["drafts"][0]["geometry"]["points"]
            != второ["drafts"][0]["geometry"]["points"])
    # Всеки кандидат е в своята папка.
    for проект in (едно, друго):
        кандидати = list((Path(проект.root) / "working" / "shape_candidates").glob("*.json"))
        assert len(кандидати) == 1


def test_zapisat_e_atomaren_bez_obshto_ime_na_vremenen_fayl(tmp_path: Path, monkeypatch):
    """Едновременен запис: временният файл е уникален, нищо не остава след него.

    Червен срещу предишния код: той правеше `output_path.with_suffix(".json.tmp")`
    — едно и също име за всички процеси — и никога не викаше общия безопасен
    запис. Тук се проверява, че записът минава през него и че след него в
    папката няма нито един остатъчен временен файл.
    """

    проект = _проект(tmp_path / "проект", "ЕДИН", sha="e" * 64)
    прозорец = _Прозорец(проект)

    извикани: list[str] = []
    истински = MainWindow.__module__
    модул = __import__(истински, fromlist=["atomic_write_text"])
    оригинал = модул.atomic_write_text

    def следящ(path, text, *args, **kwargs):
        извикани.append(Path(path).name)
        return оригинал(path, text, *args, **kwargs)

    monkeypatch.setattr(модул, "atomic_write_text", следящ)
    прозорец._write_manual_shape_draft(_payload("9C5"))

    assert извикани == ["manual_shape_drafts.json"]
    работна = Path(проект.root) / "working"
    assert not list(работна.glob("*.tmp"))
    assert not list(работна.glob(".manual_shape_drafts.json.*"))
    assert not list((работна / "shape_candidates").glob("*.tmp"))
    # И записаното е цял, четим JSON.
    assert _чернови(Path(проект.root))["drafts"][0]["annotation"]["handle"] == "9C5"


def test_primerat_za_obuchenie_sashto_nosi_samolichnost(tmp_path: Path):
    """Примерът за обучение е данна за учене — без самоличност е безполезен."""

    проект = _проект(tmp_path / "проект", "ЕДИН", sha="f" * 64)
    прозорец = _Прозорец(проект)
    товар = _payload("9C5")
    товар["save_training_example"] = True
    прозорец._write_manual_shape_draft(товар)

    път = Path(проект.root) / "working" / "manual_shape_training_examples.json"
    данни = json.loads(път.read_text(encoding="utf-8-sig"))
    assert len(данни["examples"]) == 1
    assert данни["examples"][0]["data_identity"]["source_generation_id"] == (
        current_identity(проект).source_generation_id
    )
    assert данни["examples"][0]["status"] == "MISSING_MASTER_DRAFT"
