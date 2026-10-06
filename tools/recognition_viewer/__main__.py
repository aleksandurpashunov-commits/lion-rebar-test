# -*- coding: utf-8 -*-
r"""Визуален преглед на разпознаването.

    python -m tools.recognition_viewer <чертеж.dxf> --report <batch.json>
                                       [--summary <summary.json>] [--out <папка>]
                                       [--serve]

Генерира ``viewer.html`` (самостоятелен, със zoom/pan) и ``viewer.png`` (същата
картина, за четене). Инструментът е САМО ЗА ЧЕТЕНЕ — не пипа Engine, Matcher,
MASTER, праговете, правилата или експорта.
"""
from __future__ import annotations

import argparse
import html as _html
import json
import sys
from pathlib import Path

КОРЕН = Path(__file__).resolve().parents[2]
if str(КОРЕН) not in sys.path:
    sys.path.insert(0, str(КОРЕН))

from tools.recognition_viewer import viewer as вю  # noqa: E402

ШРИФТОВЕ = (
    r"C:\Windows\Fonts\arial.ttf",
    r"C:\Windows\Fonts\segoeui.ttf",
    r"C:\Windows\Fonts\consola.ttf",
)

ШАБЛОН = """<!doctype html>
<html lang="bg"><head><meta charset="utf-8">
<title>Разпознаване — {име}</title>
<style>
 html,body {{ margin:0; height:100%; font-family:"Segoe UI",system-ui,sans-serif;
   background:#0f172a; color:#e2e8f0; overflow:hidden; }}
 #лента {{ position:fixed; top:0; left:0; right:0; height:46px; display:flex;
   align-items:center; gap:14px; padding:0 14px; background:#111827;
   border-bottom:1px solid #1f2937; z-index:10; font-size:13px; }}
 .значка {{ display:inline-flex; align-items:center; gap:6px; }}
 .точка {{ width:11px; height:11px; border-radius:2px; display:inline-block; }}
 #платно {{ position:fixed; top:46px; left:0; right:0; bottom:0; background:#fff;
   cursor:grab; }}
 #платно.влачи {{ cursor:grabbing; }}
 svg {{ width:100%; height:100%; display:block; }}
 #панел {{ position:fixed; right:14px; top:60px; width:330px; max-height:calc(100% - 80px);
   overflow:auto; background:#111827ee; border:1px solid #334155; border-radius:10px;
   padding:12px 14px; font-size:12.5px; line-height:1.45; display:none; z-index:9; }}
 #панел h3 {{ margin:0 0 8px; font-size:15px; }}
 #панел table {{ width:100%; border-collapse:collapse; }}
 #панел td {{ padding:1px 0; vertical-align:top; }}
 #панел td:first-child {{ color:#94a3b8; width:40%; }}
 .бел {{ color:#fbbf24; }}
 #помощ {{ position:fixed; left:14px; bottom:12px; font-size:11.5px; color:#94a3b8; }}
 #часовник {{ margin-left:auto; color:#64748b; font-size:11.5px; }}
</style></head><body>
<div id="лента">
  <b>{име}</b>
  <span class="значка"><i class="точка" style="background:#16a34a"></i>зелени {зелено}</span>
  <span class="значка"><i class="точка" style="background:#d97706"></i>жълти {жълто}</span>
  <span class="значка"><i class="точка" style="background:#dc2626"></i>червени {червено}</span>
  <span class="значка"><i class="точка" style="background:#0284c7"></i>не са позиции {не_позиция}</span>
  <span class="значка"><i class="точка" style="background:#c7cbd1"></i>несвързани обекти {несвързано}</span>
  <span>общо позиции <b>{общо}</b></span>
  <span id="часовник">{време}</span>
</div>
<div id="платно">{svg}</div>
<div id="панел"></div>
<div id="помощ">колелце = мащаб · влачене = местене · щракване върху позиция = подробности</div>
<script>
const ИНФО = {инфо};
const сцена = document.getElementById('сцена');
const платно = document.getElementById('платно');
const свг = платно.querySelector('svg');
let vb = свг.getAttribute('viewBox').split(/\\s+/).map(Number);
const начално = vb.slice();
function приложи() {{ свг.setAttribute('viewBox', vb.join(' ')); }}
платно.addEventListener('wheel', e => {{
  e.preventDefault();
  const r = свг.getBoundingClientRect();
  const fx = (e.clientX - r.left) / r.width, fy = (e.clientY - r.top) / r.height;
  const k = e.deltaY < 0 ? 0.84 : 1.19;
  const nx = vb[0] + vb[2] * fx * (1 - k), ny = vb[1] + vb[3] * fy * (1 - k);
  vb = [nx, ny, vb[2] * k, vb[3] * k]; приложи();
}}, {{passive:false}});
let влачи = null;
платно.addEventListener('mousedown', e => {{ влачи = {{x:e.clientX, y:e.clientY, vb:vb.slice()}}; платно.classList.add('влачи'); }});
window.addEventListener('mouseup', () => {{ влачи = null; платно.classList.remove('влачи'); }});
window.addEventListener('mousemove', e => {{
  if (!влачи) return;
  const r = свг.getBoundingClientRect();
  vb[0] = влачи.vb[0] - (e.clientX - влачи.x) * влачи.vb[2] / r.width;
  vb[1] = влачи.vb[1] - (e.clientY - влачи.y) * влачи.vb[3] / r.height;
  приложи();
}});
window.addEventListener('keydown', e => {{ if (e.key === '0') {{ vb = начално.slice(); приложи(); }} }});
const панел = document.getElementById('панел');
function ред(к, с) {{ return с === undefined || с === null || с === '' || (Array.isArray(с) && !с.length)
  ? '' : `<tr><td>${{к}}</td><td>${{Array.isArray(с) ? с.join(', ') : с}}</td></tr>`; }}
платно.addEventListener('click', e => {{
  const цел = e.target.closest('path[data-h]');
  if (!цел || !цел.dataset.h) {{ панел.style.display = 'none'; return; }}
  const п = ИНФО[цел.dataset.h];
  if (!п) {{ панел.style.display = 'none'; return; }}
  let t = `<h3>${{п.text || п.handle}}</h3><table>`;
  t += ред('състояние', п.вид);
  t += ред('брой', п.count) + ред('Ø mm', п.diameter) + ред('надписана L cm', п.declared);
  t += ред('форма', [п.shape_code, п.shape_name].filter(Boolean).join(' · '));
  t += ред('двусмислие', п.ambiguous);
  t += ред('участъци cm', п.segments) + ред('завои °', п.turns);
  t += ред('измерена L cm', п.geometry_length) + ред('разлика cm', п.numeric_difference);
  t += ред('слой на кандидата', п.candidate_layer) + ред('score', п.candidate_score);
  t += ред('статус', п.batch_status) + ред('анализ', п.analysis_status);
  t += ред('лист', п.frame);
  t += ред('причини', п.reasons) + ред('сведения', п.infos);
  t += ред('спряна от портата', п.gate_reasons);
  t += '</table>';
  if (п.top && п.top.length) {{
    t += '<h3 style="margin-top:10px">следващи кандидати</h3><table>';
    п.top.forEach(k => {{ t += ред('#' + k.rank + ' ' + (k.layer||''),
      'score ' + (k.score===null?'—':Number(k.score).toFixed(3)) + ' · ' + (k.confidence||'')); }});
    t += '</table>';
  }}
  if (п.notes && п.notes.length) {{
    t += '<div class="бел" style="margin-top:8px">' + п.notes.map(n => '• ' + n).join('<br>') + '</div>';
  }}
  панел.innerHTML = t; панел.style.display = 'block';
}});
// авто-обновяване: при сменен файл страницата се презарежда сама
let печат = "{време}";
setInterval(async () => {{
  try {{
    const о = await fetch('stamp.txt', {{cache:'no-store'}});
    if (!о.ok) return;
    const нов = (await о.text()).trim();
    if (нов && нов !== печат) location.reload();
  }} catch (e) {{ /* file:// — без сървър няма проверка */ }}
}}, 2000);
</script></body></html>
"""


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


def main(argv: list[str] | None = None) -> int:
    парсер = argparse.ArgumentParser(prog="python -m tools.recognition_viewer")
    парсер.add_argument("dxf", type=Path)
    парсер.add_argument("--report", type=Path, required=True)
    парсер.add_argument("--summary", type=Path)
    парсер.add_argument("--out", type=Path, default=Path("output/viewer"))
    парсер.add_argument("--width", type=int, default=2400)
    парсер.add_argument("--png-width", type=int, default=2600)
    парсер.add_argument("--serve", action="store_true")
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

    резултат = DxfImporter().import_file(а.dxf)
    записи = list(резултат.entities)
    спрени = вю.спрени_от_портата(а.summary)
    позиции = вю.позиции_от(отчет, спрени)
    картина = вю.построй(записи, позиции, ширина=а.width)

    папка = Path(а.out)
    папка.mkdir(parents=True, exist_ok=True)
    from datetime import datetime

    време = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    с = картина.summary
    html_текст = ШАБЛОН.format(
        име=_html.escape(str(отчет.get("project_name") or а.dxf.stem)),
        svg=картина.svg,
        инфо=json.dumps(картина.инфо, ensure_ascii=False),
        зелено=с.get("зелено", 0), жълто=с.get("жълто", 0), червено=с.get("червено", 0),
        не_позиция=с.get("не-позиция", 0), несвързано=с.get("несвързано", 0),
        общо=с.get("зелено", 0) + с.get("жълто", 0) + с.get("червено", 0),
        време=време,
    )
    (папка / "viewer.html").write_text(html_текст, encoding="utf-8")
    (папка / "stamp.txt").write_text(време, encoding="utf-8")
    ок = png_от_svg(картина.svg, папка / "viewer.png", а.png_width)

    print(f"обекти в чертежа : {len(записи)}")
    print(f"позиции          : {с.get('зелено',0)+с.get('жълто',0)+с.get('червено',0)}")
    print(f"  зелени         : {с.get('зелено',0)}")
    print(f"  жълти          : {с.get('жълто',0)}")
    print(f"  червени        : {с.get('червено',0)}")
    print(f"не са позиции    : {с.get('не-позиция',0)}")
    print(f"несвързани обекти: {с.get('несвързано',0)}")
    print(f"HTML : {папка / 'viewer.html'}")
    print(f"PNG  : {папка / 'viewer.png'} ({'записан' if ок else 'НЕ Е ЗАПИСАН'})")

    if а.serve:
        import http.server
        import socketserver

        class Тих(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=str(папка), **kwargs)

            def log_message(self, *args):
                pass

        with socketserver.TCPServer(("127.0.0.1", 0), Тих) as сървър:
            порт = сървър.server_address[1]
            print(f"сървър: http://127.0.0.1:{порт}/viewer.html  (Ctrl+C спира)")
            сървър.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
