# -*- coding: utf-8 -*-
"""Шаблонът на ``viewer.html``. Държи се отделно, за да не се губи в кода.

Две неща са нарочни и решават оплакването „при приближаване се размазва“:

* ``vector-effect:non-scaling-stroke`` — дебелината на линията е в ПИКСЕЛИ на
  екрана, не в единици на чертежа, затова при увеличение линията не надебелява;
* размерът на надписите се преизчислява при всяко мащабиране, за да остане
  постоянен на екрана.
"""
from __future__ import annotations

ШАБЛОН = """<!doctype html>
<html lang="bg"><head><meta charset="utf-8">
<title>Разпознаване — {име}</title>
<style>
 html,body { margin:0; height:100%; font-family:"Segoe UI",system-ui,sans-serif;
   background:#0f172a; color:#e2e8f0; overflow:hidden; }
 #лента { position:fixed; top:0; left:0; right:0; height:44px; display:flex;
   align-items:center; gap:12px; padding:0 14px; background:#111827;
   border-bottom:1px solid #1f2937; z-index:10; font-size:13px; }
 .значка { display:inline-flex; align-items:center; gap:6px; cursor:pointer;
   padding:3px 7px; border-radius:6px; user-select:none; white-space:nowrap; }
 .значка:hover { background:#1f2937; }
 .значка.скрит { opacity:.35; text-decoration:line-through; }
 .точка { width:11px; height:11px; border-radius:2px; display:inline-block; }
 #платно { position:fixed; top:44px; left:0; right:0; bottom:0; background:#fff;
   cursor:grab; }
 #платно.влачи { cursor:grabbing; }
 svg { width:100%; height:100%; display:block; }
 .сл path { vector-effect:non-scaling-stroke; }
 .несвързано { stroke-width:0.7px; }
 .не-позиция { stroke-width:1.4px; }
 .зелено { stroke-width:2.2px; }
 .жълто { stroke-width:3px; }
 .червено { stroke-width:3.4px; }
 .скрито { display:none; }
 #етикети text { font-family:Arial,sans-serif; pointer-events:none; }
 #етикети .ореол { fill:none; stroke:#fff; stroke-linejoin:round;
   vector-effect:non-scaling-stroke; stroke-width:3.5px; }
 #панел { position:fixed; right:14px; top:58px; width:340px; max-height:calc(100% - 76px);
   overflow:auto; background:#111827f2; border:1px solid #334155; border-radius:10px;
   padding:12px 14px; font-size:12.5px; line-height:1.45; display:none; z-index:9; }
 #панел h3 { margin:0 0 8px; font-size:15px; }
 #панел table { width:100%; border-collapse:collapse; }
 #панел td { padding:1px 0; vertical-align:top; }
 #панел td:first-child { color:#94a3b8; width:42%; }
 .бел { color:#fbbf24; }
 #помощ { position:fixed; left:14px; bottom:10px; font-size:11.5px; color:#94a3b8; }
 #часовник { margin-left:auto; color:#64748b; font-size:11.5px; white-space:nowrap; }
 #търсене { background:#0b1220; border:1px solid #334155; color:#e2e8f0;
   border-radius:6px; padding:4px 8px; font-size:12.5px; width:140px; }
</style></head><body>
<div id="лента">
  <b>{име}</b>
  <span class="значка" data-к="зелено"><i class="точка" style="background:#16a34a"></i>зелени {зелено}</span>
  <span class="значка" data-к="жълто"><i class="точка" style="background:#d97706"></i>жълти {жълто}</span>
  <span class="значка" data-к="червено"><i class="точка" style="background:#dc2626"></i>червени {червено}</span>
  <span class="значка" data-к="не-позиция"><i class="точка" style="background:#0284c7"></i>не са позиции {не_позиция}</span>
  <span class="значка" data-к="несвързано"><i class="точка" style="background:#c7cbd1"></i>несвързани {несвързано}</span>
  <span class="значка" data-к="етикети">надписи</span>
  <input id="търсене" placeholder="търси надпис…">
  <span id="часовник">{време}</span>
</div>
<div id="платно">{svg}</div>
<div id="панел"></div>
<div id="помощ">колелце = мащаб · влачене = местене · щракване = подробности · 0 = начален изглед · Enter в търсенето = приближи до позицията</div>
<script>
const ИНФО = {инфо};
const платно = document.getElementById('платно');
const свг = платно.querySelector('svg');
const етикети = document.getElementById('етикети');
let vb = свг.getAttribute('viewBox').split(/\\s+/).map(Number);
const начално = vb.slice();
function приложи() {
  свг.setAttribute('viewBox', vb.join(' '));
  if (етикети) {
    const к = vb[2] / Math.max(1, свг.clientWidth);
    етикети.setAttribute('font-size', (13 * к).toFixed(4));
  }
}
приложи();
платно.addEventListener('wheel', function (e) {
  e.preventDefault();
  const r = свг.getBoundingClientRect();
  const fx = (e.clientX - r.left) / r.width, fy = (e.clientY - r.top) / r.height;
  const k = e.deltaY < 0 ? 0.82 : 1.22;
  vb = [vb[0] + vb[2] * fx * (1 - k), vb[1] + vb[3] * fy * (1 - k), vb[2] * k, vb[3] * k];
  приложи();
}, {passive:false});
let влачи = null;
платно.addEventListener('mousedown', function (e) {
  влачи = {x:e.clientX, y:e.clientY, vb:vb.slice()}; платно.classList.add('влачи');
});
window.addEventListener('mouseup', function () { влачи = null; платно.classList.remove('влачи'); });
window.addEventListener('mousemove', function (e) {
  if (!влачи) return;
  const r = свг.getBoundingClientRect();
  vb[0] = влачи.vb[0] - (e.clientX - влачи.x) * влачи.vb[2] / r.width;
  vb[1] = влачи.vb[1] - (e.clientY - влачи.y) * влачи.vb[3] / r.height;
  приложи();
});
window.addEventListener('keydown', function (e) { if (e.key === '0') { vb = начално.slice(); приложи(); } });
window.addEventListener('resize', приложи);
document.querySelectorAll('.значка[data-к]').forEach(function (з) {
  з.addEventListener('click', function () {
    const к = з.dataset.к;
    з.classList.toggle('скрит');
    const цел = (к === 'етикети') ? етикети : свг.querySelector('.' + к);
    if (цел) цел.classList.toggle('скрито');
  });
});
const панел = document.getElementById('панел');
function ред(к, с) {
  if (с === undefined || с === null || с === '' || (Array.isArray(с) && !с.length)) return '';
  return '<tr><td>' + к + '</td><td>' + (Array.isArray(с) ? с.join(', ') : с) + '</td></tr>';
}
function покажи(h) {
  const п = ИНФО[h];
  if (!п) { панел.style.display = 'none'; return; }
  let t = '<h3>' + (п.text || п.handle) + '</h3><table>';
  t += ред('състояние', п.вид) + ред('handle', п.handle);
  t += ред('брой', п.count) + ред('Ø mm', п.diameter) + ред('надписана L cm', п.declared);
  t += ред('форма', [п.shape_code, п.shape_name].filter(Boolean).join(' · '));
  t += ред('двусмислие', п.ambiguous);
  t += ред('участъци cm', п.segments) + ред('завои °', п.turns);
  t += ред('измерена L cm', п.geometry_length) + ред('разлика cm', п.numeric_difference);
  t += ред('слой на кандидата', п.candidate_layer) + ред('score', п.candidate_score);
  t += ред('статус', п.batch_status) + ред('анализ', п.analysis_status) + ред('лист', п.frame);
  t += ред('причини', п.reasons) + ред('сведения', п.infos) + ред('спряна от портата', п.gate_reasons);
  t += '</table>';
  if (п.top && п.top.length) {
    t += '<h3 style="margin-top:10px">следващи кандидати</h3><table>';
    п.top.forEach(function (k) {
      const s = (k.score === null || k.score === undefined) ? '—' : Number(k.score).toFixed(3);
      t += ред('#' + k.rank + ' ' + (k.layer || ''), 'score ' + s + ' · ' + (k.confidence || ''));
    });
    t += '</table>';
  }
  if (п.notes && п.notes.length) {
    t += '<div class="бел" style="margin-top:8px">' + п.notes.map(function (n) { return '• ' + n; }).join('<br>') + '</div>';
  }
  панел.innerHTML = t; панел.style.display = 'block';
}
платно.addEventListener('click', function (e) {
  const цел = e.target.closest('[data-h]');
  if (!цел || !цел.dataset.h) { панел.style.display = 'none'; return; }
  покажи(цел.dataset.h);
});
function приближи(h) {
  // Обхваща ВСИЧКИ обекти на позицията — надписа и геометрията му. Само
  // първият би ни закарал до кръстчето на надписа, а прътът остава извън кадър.
  const части = свг.querySelectorAll('.сл [data-h="' + h + '"]');
  if (!части.length) return;
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  части.forEach(function (ч) {
    const b = ч.getBBox();
    if (b.width === 0 && b.height === 0) return;
    x0 = Math.min(x0, b.x); y0 = Math.min(y0, b.y);
    x1 = Math.max(x1, b.x + b.width); y1 = Math.max(y1, b.y + b.height);
  });
  if (!isFinite(x0)) return;
  const поле = Math.max(x1 - x0, y1 - y0, начално[2] / 400) * 0.9;
  const ш = (x1 - x0) + поле * 2;
  const в = Math.max((y1 - y0) + поле * 2, ш * (свг.clientHeight / Math.max(1, свг.clientWidth)));
  vb = [(x0 + x1) / 2 - ш / 2, (y0 + y1) / 2 - в / 2, ш, в];
  приложи();
}
document.getElementById('търсене').addEventListener('keydown', function (e) {
  if (e.key !== 'Enter') return;
  const дума = e.target.value.trim().toLowerCase();
  if (!дума) return;
  const h = Object.keys(ИНФО).find(function (k) {
    return (ИНФО[k].text || '').toLowerCase().indexOf(дума) >= 0;
  });
  if (!h) return;
  покажи(h); приближи(h);
});
let печат = "{време}";
setInterval(async function () {
  try {
    const о = await fetch('stamp.txt', {cache:'no-store'});
    if (!о.ok) return;
    const нов = (await о.text()).trim();
    if (нов && нов !== печат) location.reload();
  } catch (e) { }
}, 2000);
</script></body></html>
"""
