from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


# Стъпка 3 от Фаза 0 (независим одит на rc43): ``parents[3]`` излизаше ИЗВЪН
# самия release пакет (tests/ -> release/ -> release-ти родител -> още едно
# ниво нагоре), докато ``verification_artifacts/`` реално живее вътре в
# release/ (виж parents[1] по-долу). Дори с поправения път файлът
# ``rc37_r4_acceptance.py`` не е бил опакован в нито един доставен release —
# затова целият сбор от тестове спираше с грешка още при COLLECTION, преди
# `pytest` изобщо да опита да изпълни каквото и да е (заобикаляно досега
# само с `--ignore=tests/test_rc37_r4_acceptance_oracle.py`, което означава,
# че обявените "1128 passed" в CHANGELOG никога не са били измерени върху
# доставения на клиента пакет).
#
# Скриптът трябва да се възстанови от историческия rc37 verification архив
# и да се опакова трайно в tests/fixtures, за да могат тези три теста да
# продължат да проверяват реалния acceptance oracle. Дотогава модулът се
# прескача чисто (не гърми) — а ако файлът все пак присъства (например при
# бъдещо опаковане), тестовете вървят нормално без промяна.
SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "verification_artifacts"
    / "rc37_r4_acceptance.py"
)
if not SCRIPT.is_file():
    pytest.skip(
        "rc37_r4_acceptance.py липсва от доставения пакет "
        "(verification_artifacts/) — историческият rc37 acceptance oracle "
        "никога не е бил опакован за клиента. Нужно е инженерно решение: "
        "възстанови и опакова оригиналния скрипт, или официално оттегли "
        "тези три теста.",
        allow_module_level=True,
    )
SPEC = importlib.util.spec_from_file_location("rc37_r4_acceptance", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
ACCEPTANCE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ACCEPTANCE)


def _dxf_trace(*, unique_position_count: int = 158) -> dict:
    markers = [
        {
            "annotation_handle": f"HANDLE-{index:03d}",
            "position_number": index % 158 + 1,
        }
        for index in range(583)
    ]
    return {
        "marker_count": len(markers),
        "unique_position_count": unique_position_count,
        "markers": markers,
    }


def test_dxf_oracle_distinguishes_markers_from_unique_position_numbers() -> None:
    markers_by_handle = ACCEPTANCE._assert_dxf_marker_cardinality(_dxf_trace())

    assert len(markers_by_handle) == 583
    assert len({row["position_number"] for row in markers_by_handle.values()}) == 158


def test_dxf_oracle_rejects_583_as_unique_position_number_count() -> None:
    with pytest.raises(
        ACCEPTANCE.AcceptanceError,
        match="DXF unique_position_count != 158",
    ):
        ACCEPTANCE._assert_dxf_marker_cardinality(
            _dxf_trace(unique_position_count=583)
        )


def test_corrected_f005_position_numbers_are_frozen() -> None:
    assert ACCEPTANCE.POSITION_EXPECTATIONS["CD20::BJ-FI-L"]["position_number"] == 8
    assert ACCEPTANCE.POSITION_EXPECTATIONS["DB78::BJ-FI-L"]["position_number"] == 56
