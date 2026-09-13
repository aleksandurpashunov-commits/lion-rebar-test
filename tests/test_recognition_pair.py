"""Recognition pair: matcher 05210e71 + Engine 0.3.13 са неразделима двойка.

Този тест ДОКУМЕНТИРА несъвместимостта, установена от 2x2 isolation matrix:
Engine 0.3.13 + старият proven matcher (fe46be28) произвежда 47 MASTER
двусмислия върху заключения holdout. Не е production вариант.
"""
from pathlib import Path

from lion_rebar.config.constants import RECOGNITION_MATCHER_SHA256
from lion_rebar.utils.files import sha256_file

EXPECTED_MATCHER_SHA = (
    "05210e71ba6dc68c4d98665194cece4a11b3ce15ea684680e070e1f9ac2f024b"
)


def test_matcher_engine_pair_is_documented() -> None:
    assert RECOGNITION_MATCHER_SHA256 == EXPECTED_MATCHER_SHA
    matcher = Path(__file__).resolve().parents[1] / "lion_rebar" / "dxf" / "shape_recognition.py"
    actual = sha256_file(matcher)
    assert actual == EXPECTED_MATCHER_SHA, (
        "shape_recognition.py е променен без обновяване на recognition pair "
        f"документацията: {actual}. Matcher и Engine 0.3.13 се издават само "
        "заедно; вж. CHANGELOG (2x2 isolation matrix: старият matcher + "
        "Engine 0.3.13 = 47 MASTER двусмислия)."
    )


def test_changelog_has_no_false_byte_identical_claim() -> None:
    changelog = (
        Path(__file__).resolve().parents[1] / "CHANGELOG.md"
    ).read_text(encoding="utf-8")
    assert "remains byte-identical" not in changelog
    assert EXPECTED_MATCHER_SHA in changelog
