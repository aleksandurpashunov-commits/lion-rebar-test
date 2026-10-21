# -*- coding: utf-8 -*-
"""Авторът на решението: ЗАЯВЕНО име, не удостоверена самоличност.

Решение на собственика от 01.10.2026 по § 3.3, точка по точка:

1. при първото записано операторско решение се въвежда име и то се запомня;
2. текущият оператор се вижда в интерфейса и може да се сменя;
3. всяко решение пази автора КЪМ МОМЕНТА НА ЗАПИСА — смяната не пренаписва
   историята;
4. старите записи „оператор" остават с НЕИЗВЕСТЕН автор и не се приписват със
   задна дата;
5. въведеното име е заявен автор, а не удостоверена самоличност.

ДЕЛТАТА, която този файл пази. Дотук единайсет места записваха буквалния низ
„оператор", защото `MainWindow.operator_name` не се задаваше никъде. Нито едно
място вече не може да запише този низ: незаявеното име е `НЕЗАЯВЕН` и е
различимо от старите записи.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lion_rebar.session.decision_contract import append_project_decision_event
from lion_rebar.session.operator_identity import (
    НЕЗАЯВЕН,
    OperatorIdentityError,
    claimed_operator,
    declare_operator,
    is_named_person,
    operator_history,
)

ENGINE = "0.3.15"


def _проект(tmp_path: Path) -> Path:
    корен = tmp_path / "проект"
    (корен / "working").mkdir(parents=True)
    (корен / "working" / "project.json").write_text(
        json.dumps(
            {
                "project_id": "p-op",
                "name": "АВТОР",
                "engine_id": "lion-rebar-master",
                "engine_version": ENGINE,
                "engine_sha256": "e" * 64,
                "extensions": {"source_sha256": "a" * 64},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return корен


# --- 1 и 2: въвежда се, запомня се, сменя се -------------------------------


def test_imeto_se_zapomnya_i_se_chete(tmp_path):
    настройки = tmp_path / "settings.json"
    assert claimed_operator(настройки) == ""

    declare_operator(настройки, "  Александър   Пашунов ")

    assert claimed_operator(настройки) == "Александър Пашунов"


def test_smyanata_dopisva_istoriyata(tmp_path):
    настройки = tmp_path / "settings.json"
    declare_operator(настройки, "Александър")
    declare_operator(настройки, "Мария")

    история = operator_history(настройки)
    assert [ред["name"] for ред in история] == ["Александър", "Мария"]
    # Вижда се КОЕ е заместено, тоест историята не е пренаписана, а дописана.
    assert история[0]["replaced"] is None
    assert история[1]["replaced"] == "Александър"
    assert claimed_operator(настройки) == "Мария"


def test_chuzhdite_nastroyki_se_zapazvat(tmp_path):
    """Записът на името не изтрива останалите настройки."""

    настройки = tmp_path / "settings.json"
    настройки.write_text(
        json.dumps({"нещо_друго": 42}, ensure_ascii=False), encoding="utf-8"
    )

    declare_operator(настройки, "Александър")

    данни = json.loads(настройки.read_text(encoding="utf-8"))
    assert данни["нещо_друго"] == 42
    assert данни["operator_name"] == "Александър"


# --- 4 и 5: ролята не е име, а името не е удостоверено ---------------------


@pytest.mark.parametrize(
    "стойност", ["", "   ", "А", "оператор", "ОПЕРАТОР", "USER", "инженер", НЕЗАЯВЕН]
)
def test_rolyata_i_prazното_ne_sa_ime(tmp_path, стойност):
    настройки = tmp_path / "settings.json"
    with pytest.raises(OperatorIdentityError):
        declare_operator(настройки, стойност)
    assert claimed_operator(настройки) == ""


def test_is_named_person_otkazva_zapalvashtite():
    assert is_named_person("Александър Пашунов")
    assert not is_named_person("оператор")
    assert not is_named_person("USER")
    assert not is_named_person(НЕЗАЯВЕН)
    assert not is_named_person("")
    assert not is_named_person(None)


def test_imeto_e_zayaveno_a_ne_udostovereno(tmp_path):
    настройки = tmp_path / "settings.json"
    данни = declare_operator(настройки, "Александър Пашунов")
    assert данни["operator_claimed"] is True
    assert данни["operator_authenticated"] is False

    корен = _проект(tmp_path)
    плик = append_project_decision_event(
        корен,
        "APPROVED",
        {"annotation_handle": "A1", "actor": "Александър Пашунов",
         "master_shape_code": "F001"},
    )
    автор = плик["actor"]
    assert автор["recorded_as"] == "Александър Пашунов"
    assert автор["identifies_person"] is True
    assert автор["claimed"] is True
    # Нищо в програмата не проверява самоличност и записът не твърди обратното.
    assert автор["authenticated"] is False


def test_stariyat_zapis_operator_ostava_s_neizvesten_avtor(tmp_path):
    """Записите от преди въвеждането не се приписват със задна дата."""

    корен = _проект(tmp_path)
    плик = append_project_decision_event(
        корен,
        "APPROVED",
        {"annotation_handle": "B2", "approved_by": "оператор",
         "master_shape_code": "F001"},
    )
    автор = плик["actor"]
    assert автор["recorded_as"] == "оператор"
    assert автор["identifies_person"] is False
    assert автор["claimed"] is False


def test_nezayaveniyat_avtor_e_razlichim_ot_stariya(tmp_path):
    корен = _проект(tmp_path)
    плик = append_project_decision_event(
        корен,
        "APPROVED",
        {"annotation_handle": "C3", "actor": НЕЗАЯВЕН, "master_shape_code": "F001"},
    )
    assert плик["actor"]["recorded_as"] == НЕЗАЯВЕН
    assert плик["actor"]["identifies_person"] is False


# --- 3: смяната не пренаписва историята -----------------------------------


def test_smyanata_na_imeto_ne_menya_veche_zapisanite_resheniya(tmp_path):
    настройки = tmp_path / "settings.json"
    корен = _проект(tmp_path)

    declare_operator(настройки, "Александър")
    първо = append_project_decision_event(
        корен,
        "APPROVED",
        {"annotation_handle": "D4", "actor": claimed_operator(настройки),
         "master_shape_code": "F001"},
    )

    declare_operator(настройки, "Мария")
    второ = append_project_decision_event(
        корен,
        "APPROVED",
        {"annotation_handle": "E5", "actor": claimed_operator(настройки),
         "master_shape_code": "F001"},
    )

    assert първо["actor"]["recorded_as"] == "Александър"
    assert второ["actor"]["recorded_as"] == "Мария"

    # И на диска: първият ред не е пипан.
    редове = [
        json.loads(ред)
        for ред in (корен / "working" / "decision_events_v2.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if ред.strip()
    ]
    по_handle = {ред["annotation_handles"][0]: ред for ред in редове}
    assert по_handle["D4"]["actor"]["recorded_as"] == "Александър"
    assert по_handle["E5"]["actor"]["recorded_as"] == "Мария"


def test_nechetimi_nastroyki_ne_davat_ime_i_ne_padat(tmp_path):
    """§ 7.6: повредените настройки не дават име, но и не спират програмата."""

    настройки = tmp_path / "settings.json"
    настройки.write_text("{непълен", encoding="utf-8")

    assert claimed_operator(настройки) == ""
    assert operator_history(настройки) == []
    # Явното заявяване работи и върху повредения файл.
    declare_operator(настройки, "Александър")
    assert claimed_operator(настройки) == "Александър"


def test_nito_edno_mesto_ne_pishe_zapalvashtiya_niz():
    """Регресия: буквалният низ „оператор" не остава като автор в кода."""

    from pathlib import Path as _P

    import lion_rebar

    корен = _P(lion_rebar.__file__).resolve().parent
    намерени = []
    for файл in корен.rglob("*.py"):
        текст = файл.read_text(encoding="utf-8", errors="replace")
        for ред in текст.splitlines():
            if "#" in ред and ред.strip().startswith("#"):
                continue
            if 'or "оператор"' in ред or "or 'оператор'" in ред:
                намерени.append("%s: %s" % (файл.name, ред.strip()))
    assert намерени == []
