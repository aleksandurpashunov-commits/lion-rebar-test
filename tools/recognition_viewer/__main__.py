# -*- coding: utf-8 -*-
r"""Визуален преглед на разпознаването.

    python -m tools.recognition_viewer <чертеж.dxf> --report <batch.json>
                                       [--summary <summary.json>] [--out <папка>]
                                       [--serve] [--port N]

Генерира ``viewer.html`` (жив, със zoom/pan — линиите и надписите са с ПОСТОЯНЕН
размер на екрана, затова приближаването не размазва) и ``viewer.png`` (същата
картина, за четене от Claude).

Инструментът е САМО ЗА ЧЕТЕНЕ — не пипа Engine, Matcher, MASTER, праговете,
правилата или експорта.
"""
from __future__ import annotations

import argparse
import html as _html
import json
import sys
from datetime import datetime
from pathlib import Path

КОРЕН = Path(__file__).resolve().parents[2]
if str(КОРЕН) not in sys.path:
    sys.path.insert(0, str(КОРЕН))

from tools.recognition_viewer import viewer as вю  # noqa: E402
from tools.recognition_viewer.html_template import ШАБЛОН  # noqa: E402

ШРИФТОВЕ = (
    r"C:\Windows\Fonts\arial.ttf",
    r"C:\Windows\Fonts\segoeui.ttf",
    r"C:\Windows\Fonts\consola.ttf",
)


def png_от_svg(svg: str, път: Path, ширина: int) -> bool:
    """Растеризиране през PySide6. Шрифтовете се зареждат ИЗРИЧНО.

    Измерено: в offscreen режим Qt има НУЛА шрифтови семейства и текстът излиза
    като празни кутийки. Зареждането на файла с шрифта го решава.
    """

    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QByteArray, Qt
    from PySide6.QtGui import QFontDatabase, QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    for шрифт in ШРИФТОВЕ:
        if Path(шрифт).is_file():
            QFontDatabase.addApplicationFont(шрифт)

    рисувач = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    if not рисувач.isValid():
        return False
    размер = рисувач.defaultSize()
    височина = max(1, int(ширина * размер.height() / max(1, размер.width())))
    изображение = QImage(ширина, височина, QImage.Format.Format_RGB32)
    изображение.fill(Qt.GlobalColor.white)
    художник = QPainter(изображение)
    рисувач.render(художник)
    художник.end()
    return bool(изображение.save(str(път)))


def _сглоби_html(картина: вю.Картина, име: str, време: str) -> str:
    """Заместване, НЕ ``format``: шаблонът е пълен с фигурни скоби от CSS и JS."""

    с = картина.summary
    замени = {
        "{име}": _html.escape(име),
        "{svg}": картина.svg,
        "{инфо}": json.dumps(картина.инфо, ensure_ascii=False),
        "{зелено}": str(с.get("зелено", 0)),
        "{жълто}": str(с.get("жълто", 0)),
        "{червено}": str(с.get("червено", 0)),
        "{не_позиция}": str(с.get("не-позиция", 0)),
        "{несвързано}": str(с.get("несвързано", 0)),
        "{време}": време,
    }
    текст = ШАБЛОН
    for ключ, стойност in замени.items():
        текст = текст.replace(ключ, стойност)
    return текст


def main(argv: list[str] | None = None) -> int:
    парсер = argparse.ArgumentParser(prog="python -m tools.recognition_viewer")
    парсер.add_argument("dxf", type=Path)
    парсер.add_argument("--report", type=Path, required=True)
    парсер.add_argument("--summary", type=Path)
    парсер.add_argument("--out", type=Path, default=Path("output/viewer"))
    парсер.add_argument("--width", type=int, default=2400)
    парсер.add_argument("--png-width", type=int, default=2600)
    парсер.add_argument("--serve", action="store_true")
    парсер.add_argument("--port", type=int, default=8777)
    парсер.add_argument("--no-png", action="store_true")
    парсер.add_argument(
        "--ignore-identity", action="store_true",
        help="пусни въпреки разминаване между чертежа и отчета (НЕ се препоръчва)",
    )
    а = парсер.parse_args(argv)

    отчет = вю.прочети_отчет(а.report)
    самоличност = (отчет.get("data_identity") or {}).get("source_sha256") or ""
    на_чертежа = вю.sha256_на(а.dxf)
    if самоличност and самоличност != на_чертежа and not а.ignore_identity:
        print("ОТКАЗ: отчетът е за ДРУГ чертеж.", file=sys.stderr)
        print(f"  чертеж: {на_чертежа}", file=sys.stderr)
        print(f"  отчет : {самоличност}", file=sys.stderr)
        return 2

    from lion_rebar.dxf.importer import DxfImporter

    записи = list(DxfImporter().import_file(а.dxf).entities)
    спрени = вю.спрени_от_портата(а.summary)
    позиции = вю.позиции_от(отчет, спрени)
    картина = вю.построй(записи, позиции, ширина=а.width)

    папка = Path(а.out)
    папка.mkdir(parents=True, exist_ok=True)
    време = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    име = str(отчет.get("project_name") or а.dxf.stem)
    (папка / "viewer.html").write_text(_сглоби_html(картина, име, време), encoding="utf-8")
    (папка / "stamp.txt").write_text(време, encoding="utf-8")
    ок = True if а.no_png else png_от_svg(картина.svg, папка / "viewer.png", а.png_width)

    с = картина.summary
    общо = с.get("зелено", 0) + с.get("жълто", 0) + с.get("червено", 0)
    print(f"обекти в чертежа : {len(записи)}")
    print(f"позиции          : {общо}")
    print(f"  зелени         : {с.get('зелено', 0)}")
    print(f"  жълти          : {с.get('жълто', 0)}")
    print(f"  червени        : {с.get('червено', 0)}")
    print(f"не са позиции    : {с.get('не-позиция', 0)}")
    print(f"несвързани обекти: {с.get('несвързано', 0)}")
    print(f"HTML : {папка / 'viewer.html'}")
    if not а.no_png:
        print(f"PNG  : {папка / 'viewer.png'} ({'записан' if ок else 'НЕ Е ЗАПИСАН'})")

    if а.serve:
        import http.server
        import socketserver

        class Тих(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=str(папка), **kwargs)

            def log_message(self, *args):
                pass

        socketserver.TCPServer.allow_reuse_address = True
        with socketserver.TCPServer(("127.0.0.1", а.port), Тих) as сървър:
            порт = сървър.server_address[1]
            print(f"ОТВОРИ: http://127.0.0.1:{порт}/viewer.html", flush=True)
            сървър.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
