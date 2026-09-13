import json
from pathlib import Path

from lion_rebar.engine.legacy_import import import_legacy_html, import_shape_draft_json
from lion_rebar.engine.test_runner import run_engine_tests


def test_import_embedded_default_library(tmp_path: Path):
    library = {
        "ФОРМИ": [
            {
                "КОД_ФОРМА": "F001",
                "ИМЕ_ФОРМА": "ПРАВ ПРЪТ",
                "КАТЕГОРИЯ": "ПРАВ",
                "БРОЙ_СЕГМЕНТИ": 1,
                "БРОЙ_ОГЪВКИ": 0,
                "ЗАТВОРЕНА": "НЕ",
                "СИМЕТРИЧНА": "ДА",
                "ОГЛЕДАЛНА_ДОПУСТИМА": "ДА",
                "СТАТУС": "ОДОБРЕН",
                "ВЕРСИЯ": 1,
            }
        ],
        "СЕГМЕНТИ": [
            {
                "КОД_ФОРМА": "F001",
                "№_СЕГМЕНТ": 1,
                "КОД_ДЪЛЖИНА": "L1",
                "ТИП_СЕГМЕНТ": "ПРАВ",
                "ЗАДЪЛЖИТЕЛЕН": "ДА",
                "МЕТОД_ГЕОМЕТРИЯ": "ДИРЕКТНА_ДЪЛЖИНА",
            }
        ],
        "ИЗЧЕРТАВАНЕ": [
            {"КОД_ФОРМА": "F001", "№_СЕГМЕНТ": 1, "ПОСОКА_ИЗЧЕРТАВАНЕ": "НАДЯСНО"}
        ],
        "РАЗМЕРИ": [],
        "ТЕСТОВЕ": [
            {
                "ID_ТЕСТ": "T-F001-001",
                "КОД_ФОРМА": "F001",
                "ОЧАКВАНИ_РАЗМЕРИ_СМ": "L1=200",
                "ОЧАКВАНА_ОБЩА_ДЪЛЖИНА_СМ": 200,
            }
        ],
        "НАСТРОЙКИ": [],
    }
    html = (
        "<script>const EDITOR_VERSION='0.3.1';"
        "const RESERVED_CODES=new Set(['F001']);"
        f"const DEFAULT_LIBRARY={json.dumps(library, ensure_ascii=False)};"
        "</script>"
    )
    path = tmp_path / "OPEN_ENGINE_v0.3.1_MASTER.html"
    path.write_text(html, encoding="utf-8")
    engine = import_legacy_html(path)
    assert engine.version == "0.3.1"
    assert engine.find_shape("F001").locked
    assert run_engine_tests(engine)[0].passed


def test_import_shape_draft_json(tmp_path: Path):
    path = tmp_path / "F010_DRAFT.json"
    path.write_text(
        json.dumps(
            {
                "schema": "lion-rebar-shape-draft/v1",
                "forms": [
                    {
                        "proposedCode": "F010",
                        "name": "NEW",
                        "segments": [
                            {"length": 100, "angle": 90, "turn": "RIGHT"},
                            {"length": 50, "angle": None, "turn": None},
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    shapes = import_shape_draft_json(path)
    assert shapes[0].code == "F010"
    assert len(shapes[0].segments) == 2
