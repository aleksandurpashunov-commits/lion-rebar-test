# -*- coding: utf-8 -*-
"""Бележките на оператора във визуалния преглед (собственик, 07.10.2026).

Десен бутон върху надпис или позиция → MASTER форма или пояснение. Бележките
са отделен файл до прегледа и НЕ стигат до проекта — проверява се, че
отказват всичко, което не може да се проследи: непознат обект, форма извън
engine-а на анализа, бележки от друг чертеж.
"""
from __future__ import annotations

import json
import socketserver
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from tools.recognition_viewer import belezhki as бел
from tools.recognition_viewer.__main__ import _сглоби_html, обработчик
from tools.recognition_viewer.viewer import Картина

SHA = "a" * 64
ФОРМИ = [
    {"code": "F004", "name": "ПРЪТ С ЕДИН ВИНКЕЛ", "active": True},
    {"code": "F006", "name": "СТРЕМЕ (БИГЛА)", "active": True},
]
ИЗВЕСТНИ = {"3E12": "33N8x228", "9A01": "ГОРНА АРМИРОВКА"}


def _engine_проект(tmp_path: Path, sha_на_engine: str) -> tuple[Path, dict]:
    отчет_път = tmp_path / "proekt" / "output" / "04_REVIEW_REPORTS" / "run" / "x_geometry_batch.json"
    отчет_път.parent.mkdir(parents=True)
    отчет = {"analysis_engine": {"content_sha256": "e" * 64}, "rows": []}
    отчет_път.write_text(json.dumps(отчет), encoding="utf-8")
    master = tmp_path / "proekt" / "master"
    master.mkdir()
    (master / "engine_lion-rebar-master_v0.3.15.json").write_text(
        json.dumps({"content_sha256": sha_на_engine, "shapes": [
            {"code": "F004", "name": "ПРЪТ С ЕДИН ВИНКЕЛ", "active": True},
            {"code": "F015", "name": "СТЪЛБИЩНО РАМО 4", "active": False},
        ]}),
        encoding="utf-8",
    )
    return отчет_път, отчет


def test_формите_идват_от_engine_а_на_анализа(tmp_path):
    път, отчет = _engine_проект(tmp_path, "e" * 64)
    форми = бел.форми_от_engine(път, отчет)
    assert [ф["code"] for ф in форми] == ["F004", "F015"]
    assert форми[1]["active"] is False


def test_чужд_engine_не_дава_форми(tmp_path):
    # Положителната контрола е горният тест: същата папка с верния sha дава 2.
    път, отчет = _engine_проект(tmp_path, "d" * 64)
    assert бел.форми_от_engine(път, отчет) == []


def test_записва_форма_и_пояснение(tmp_path):
    данни = бел.приложи(
        tmp_path, SHA, "h01", {"key": "3E12", "kind": "позиция", "master_code": "F006",
                               "note": "стреме с куки 135°"},
        известни=ИЗВЕСТНИ, форми=ФОРМИ,
    )
    бележка = данни["notes"]["3E12"]
    assert (бележка["master_code"], бележка["master_name"]) == ("F006", "СТРЕМЕ (БИГЛА)")
    assert бележка["text"] == "33N8x228"
    на_диска = json.loads((tmp_path / бел.ФАЙЛ).read_text(encoding="utf-8"))
    assert на_диска["source_sha256"] == SHA
    assert на_диска["notes"]["3E12"]["note"] == "стреме с куки 135°"


def test_само_пояснение_за_ненамерен_надпис(tmp_path):
    данни = бел.приложи(
        tmp_path, SHA, "h01", {"key": "9A01", "kind": "надпис", "note": "това е позиция 12"},
        известни=ИЗВЕСТНИ, форми=ФОРМИ,
    )
    assert данни["notes"]["9A01"]["master_code"] == ""


@pytest.mark.parametrize(
    "заявка",
    [
        {"key": "FFFF", "note": "няма го в чертежа"},
        {"key": "3E12", "master_code": "F099"},
        {"key": "3E12", "master_code": "", "note": "   "},
        {"key": "3E12", "note": "x" * (бел.НАЙ_ДЪЛГО_ПОЯСНЕНИЕ + 1)},
    ],
)
def test_отказва_непроследимото(tmp_path, заявка):
    with pytest.raises(бел.НевалиднаБележка):
        бел.приложи(tmp_path, SHA, "h01", заявка, известни=ИЗВЕСТНИ, форми=ФОРМИ)
    assert not (tmp_path / бел.ФАЙЛ).exists()


def test_изтриване(tmp_path):
    бел.приложи(tmp_path, SHA, "h01", {"key": "3E12", "note": "а"}, известни=ИЗВЕСТНИ, форми=ФОРМИ)
    данни = бел.приложи(tmp_path, SHA, "h01", {"key": "3E12", "delete": True},
                        известни=ИЗВЕСТНИ, форми=ФОРМИ)
    assert данни["notes"] == {}


def test_бележки_от_друг_чертеж_не_се_зареждат_а_се_отместват(tmp_path):
    бел.приложи(tmp_path, SHA, "h01", {"key": "3E12", "note": "а"}, известни=ИЗВЕСТНИ, форми=ФОРМИ)
    assert бел.зареди(tmp_path, SHA, "h01")["notes"]  # същият чертеж — четат се
    данни = бел.зареди(tmp_path, "b" * 64, "h01")
    assert данни["notes"] == {}
    assert not (tmp_path / бел.ФАЙЛ).exists()
    assert len(list(tmp_path.glob("belezhki_operator.drug_chertezh_*.json"))) == 1


def _картина() -> Картина:
    return Картина(
        svg='<svg viewBox="0 0 1 1"><g class="сл червено"><path d="M0 0" data-h="3E12"/></g></svg>',
        width=1, height=1, summary={}, инфо={"3E12": {"handle": "3E12", "text": "33N8x228"}},
        известни=ИЗВЕСТНИ,
    )


def test_html_вгражда_формите_и_бележките_без_да_затваря_скрипта():
    бележки = бел.празни(SHA, "h01")
    бележки["notes"]["3E12"] = {"key": "3E12", "note": "</script><b>x</b>", "master_code": ""}
    текст = _сглоби_html(_картина(), "h01", "t", форми=ФОРМИ, бележки=бележки, самоличност=SHA)
    assert "const ФОРМИ = " in текст and '"F006"' in текст
    assert "contextmenu" in текст and 'id="меню"' in текст
    # Единственото ``</script>`` е затварящото на самия скрипт.
    assert текст.count("</script>") == 1
    assert "{форми}" not in текст and "{бележки}" not in текст and "{известни}" not in текст


def test_сървърът_записва_бележката_през_post(tmp_path):
    клас = обработчик(tmp_path, самоличност=SHA, проект="h01", известни=ИЗВЕСТНИ, форми=ФОРМИ)
    with socketserver.TCPServer(("127.0.0.1", 0), клас) as сървър:
        нишка = threading.Thread(target=сървър.serve_forever, daemon=True)
        нишка.start()
        адрес = f"http://127.0.0.1:{сървър.server_address[1]}/belezhki"
        try:
            def прати(тяло):
                заявка = urllib.request.Request(
                    адрес, data=json.dumps(тяло).encode("utf-8"),
                    headers={"Content-Type": "application/json"}, method="POST",
                )
                with urllib.request.urlopen(заявка, timeout=5) as отговор:
                    return отговор.status, json.loads(отговор.read().decode("utf-8"))

            статус, данни = прати({"key": "3E12", "master_code": "F006"})
            assert статус == 200 and данни["notes"]["3E12"]["master_code"] == "F006"
            with pytest.raises(urllib.error.HTTPError) as грешка:
                прати({"key": "3E12", "master_code": "F099"})
            assert грешка.value.code == 400
        finally:
            сървър.shutdown()
    на_диска = json.loads((tmp_path / бел.ФАЙЛ).read_text(encoding="utf-8"))
    assert на_диска["notes"]["3E12"]["master_code"] == "F006"
