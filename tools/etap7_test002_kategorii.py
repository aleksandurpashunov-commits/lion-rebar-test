# -*- coding: utf-8 -*-
"""Етап 7 — категории на 232-те одобрени позиции в Тест-002.

Само чете и мери. Не пипа нито ``lion_rebar``, нито тестове, нито проекта.

Вход (фиксиран от възлагането):
  * прогон  C:\\lr_exp\\rezultat\\etap_6_dovurshvane\\progoni_B\\Тест-002.json
  * копието, което прогонът сочи (batch отчет + одобрени връзки)
  * чертеж   C:\\lr_exp\\proekti_chisti\\Тест-002\\source\\original.dxf
  * обекти   …\\working\\extracted_entities.json

Изход: C:\\lr_exp\\rezultat\\etap_7\\TEST002_KATEGORII.json

Какво установява скриптът (всичко е измерено, нищо не е прието наготово):

1. Чертежът държи ТРИ листа — начертани правоъгълници, не догадка:
   A 71D0BC (КОФРАЖЕН ПЛАН, без надписи), B 6FEC2F (ГОРНА АРМИРОВКА +27.82),
   C 47ACE8 (ДОЛНА АРМИРОВКА ПЛОЧА И ГРЕДИ +27.82).
2. Всяка от петте ведомости носи заглавие в ред 1 НАД реда със заглавия на
   колоните — оттам се чете коя зона покрива (етап 1 / етап 2 / греди).
3. Рамката на програмата (``frame_index``) се пада по най-близък щемпел и НЕ
   съвпада с начертания лист: 59 надписа на лист B отиват в рамка 1, а 88
   надписа на лист C — в рамка 2.

Защо кеш на ведомостите: четенето на петте вградени Excel 2003 от 10 MB DXF
отнема секунди и е чиста функция на файла — кешът е само ускорение, ключът е
sha256 на чертежа.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

RELEASE = Path(r"C:\lr_exp\release")
if str(RELEASE) not in sys.path:
    sys.path.insert(0, str(RELEASE))

PROGON = Path(r"C:\lr_exp\rezultat\etap_6_dovurshvane\progoni_B\Тест-002.json")
DXF = Path(r"C:\lr_exp\proekti_chisti\Тест-002\source\original.dxf")
ENTITIES = Path(r"C:\lr_exp\proekti_chisti\Тест-002\working\extracted_entities.json")
OUT = Path(r"C:\lr_exp\rezultat\etap_7\TEST002_KATEGORII.json")
CACHE = Path(
    r"C:\Users\BFAF~1\AppData\Local\Temp\claude"
    r"\I------------Lion-Rebar-LION-REBAR-v0-1-44-21-rc44-WINDOWS-release"
    r"\cd57f30b-b6e8-45a5-a305-30546d1bd1b2\scratchpad\vedomosti_cache.json"
)

# Колоните идват с ИМЕНАТА от чертежа; „с“ в „Дължина, сm“ е кирилска U+0441.
COL_POZ = "Поз."
COL_BROI = "Брой"
COL_DIAM = "Ø"
COL_TIP = "ТИП"
COL_DALZHINA = "Дължина, \u0441m"

#: Начертаните външни рамки на листовете: handle на LWPOLYLINE и границите.
#: Измерено: единствените контури с h≈2970 в целия чертеж.
LISTOVE = {
    "A": {"handle": "71D0BC", "x0": 42771, "x1": 50887, "y0": 65502, "y1": 68472,
          "naslov": "КОФРАЖЕН ПЛАН НА ПЛОЧА НА КОТА +27.82",
          "naslov_handle": "71D0A8"},
    "B": {"handle": "6FEC2F", "x0": 43017, "x1": 51057, "y0": 61524, "y1": 64494,
          "naslov": "ГОРНА АРМИРОВКА ПЛОЧА НА КОТА +27.82",
          "naslov_handle": "6FEC1C"},
    "C": {"handle": "47ACE8", "x0": 42956, "x1": 51351, "y0": 58030, "y1": 61000,
          "naslov": "ДОЛНА АРМИРОВКА ПЛОЧА И ГРЕДИ НА КОТА +27.82",
          "naslov_handle": "47ACD3"},
}

#: Границата между 1-ви и 2-ри етап в плановете. Изведена от три независими
#: ключа на лист B, всеки от които се затваря ТОЧНО при нея:
#: Ø12×300 (90|310), Ø14×300 (62|33 срещу 62|30), Ø10×300 (120|133).
#: Етап 2 е отляво, етап 1 отдясно — потвърдено и от надписите
#: „в Борд 1, етап 2“ (X=43301) срещу „в Борд 1, етап 1“ (X=47872, 49687).
ETAP_X = 46000.0

#: Лентата с разрезите на гредите долу на лист C.
GREDI_Y_MAX = 58700.0


# ---------------------------------------------------------------- вход

def load_progon() -> dict:
    return json.loads(PROGON.read_text(encoding="utf-8"))


def load_batch(progon: dict):
    path = Path(progon["copy"]) / progon["analysis"]["report"].replace("/", "\\")
    return json.loads(path.read_text(encoding="utf-8")), path


def load_links(progon: dict):
    path = Path(progon["copy"]) / "working" / "approved_geometry_links.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["links"], path


def load_entities() -> dict[str, dict]:
    data = json.loads(ENTITIES.read_text(encoding="utf-8"))
    return {e["handle"]: e for e in data["entities"]}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def load_vedomosti() -> list[dict]:
    """Петте вградени ведомости — от кеша, ако чертежът не е сменен."""
    digest = sha256(DXF)
    if CACHE.exists():
        cached = json.loads(CACHE.read_text(encoding="utf-8"))
        if cached.get("source_sha256") == digest:
            return cached["vedomosti"]

    from lion_rebar.dxf.ole_schedule import (
        detect_schedule_table,
        read_embedded_documents,
    )

    out: list[dict] = []
    for doc in read_embedded_documents(DXF):
        if doc.workbook is None:
            continue
        frame = doc.frame
        sheets = []
        for sheet in doc.workbook.sheets:
            table = detect_schedule_table(sheet)
            if not table.rows:
                continue
            # Ред 1 стои НАД реда със заглавия и носи името на ведомостта.
            naslov = ""
            if table.header_row:
                for row in sheet.rows():
                    if row >= table.header_row:
                        break
                    for _c, cell in sorted(sheet.row_values(row).items()):
                        if isinstance(cell.value, str) and len(cell.value.strip()) > 6:
                            naslov = cell.value.strip()
                            break
                    if naslov:
                        break
            sheets.append(
                {
                    "sheet_name": sheet.name,
                    "naslov": naslov,
                    "header_row": table.header_row,
                    "columns": {str(k): v for k, v in table.columns.items()},
                    "rows": table.rows,
                }
            )
        if not sheets:
            continue
        out.append(
            {
                "frame_handle": frame.handle,
                "layer": frame.layer,
                "upper_left": list(frame.upper_left) if frame.upper_left else None,
                "lower_right": list(frame.lower_right) if frame.lower_right else None,
                "sheets": sheets,
            }
        )
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(
        json.dumps({"source_sha256": digest, "vedomosti": out},
                   ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    return out


# ---------------------------------------------------- редовете на ведомостта

_NUM = re.compile(r"(\d+(?:[.,]\d+)?)")


def num(value: object) -> float | None:
    """„N10“ → 10.0. Само числото в текста; нищо не се домисля."""
    if isinstance(value, (int, float)):
        return float(value)
    if value is None:
        return None
    m = _NUM.search(str(value))
    return float(m.group(1).replace(",", ".")) if m else None


def schedule_positions(vedomosti: list[dict]) -> list[dict]:
    """Редовете-позиции: номер на позиция + брой + Ø + дължина.

    Редовете с обобщения („Ø“, „Дълж., m“, „Тегло, кг“, „ОБЩО“) и рисунките
    под таблицата нямат номер на позиция и отпадат тук.
    """
    out: list[dict] = []
    for ved in vedomosti:
        for sheet in ved["sheets"]:
            for row in sheet["rows"]:
                poz = row.get(COL_POZ)
                if not isinstance(poz, (int, float)):
                    continue
                d = num(row.get(COL_DIAM))
                length = num(row.get(COL_DALZHINA))
                count = num(row.get(COL_BROI))
                out.append(
                    {
                        "ved": ved["frame_handle"],
                        "row": row.get("_row"),
                        "poz": int(poz),
                        "diam_mm": d,
                        "length_cm": length,
                        "count": count,
                        "tip": row.get(COL_TIP),
                        "nepalen": d is None or length is None or count is None,
                    }
                )
    return out


# ---------------------------------------------------------------- геометрия

def xy(entity: dict | None) -> tuple[float, float] | None:
    if not entity:
        return None
    g = entity.get("geometry") or {}
    for key in ("insert", "align_point", "visual_anchor"):
        p = g.get(key)
        if isinstance(p, (list, tuple)) and len(p) >= 2:
            return float(p[0]), float(p[1])
    return None


def sheet_of(x: float, y: float) -> str | None:
    for name, b in LISTOVE.items():
        if b["x0"] <= x <= b["x1"] and b["y0"] <= y <= b["y1"]:
            return name
    return None


def zona_of(x: float, y: float) -> str | None:
    """Зона = (лист, етап) или лентата с гредите долу на лист C."""
    s = sheet_of(x, y)
    if s is None:
        return None
    if s == "C" and y < GREDI_Y_MAX:
        return "C/ГРЕДИ"
    return f"{s}/етап{'1' if x >= ETAP_X else '2'}"


def detect_frames():
    from lion_rebar.dxf.models import DxfEntityRecord
    from lion_rebar.export.frame_detection import detect_drawing_frames

    data = json.loads(ENTITIES.read_text(encoding="utf-8"))
    return detect_drawing_frames([DxfEntityRecord(**e) for e in data["entities"]])


# ---------------------------------------------------------------- анализ

def main() -> None:
    progon = load_progon()
    batch, batch_path = load_batch(progon)
    links, links_path = load_links(progon)
    ents = load_entities()
    vedomosti = load_vedomosti()
    poz_rows = schedule_positions(vedomosti)

    rows = {r["annotation_handle"]: r for r in batch["rows"]}
    appr = {l["annotation_handle"]: l for l in links}
    pos = {h: xy(ents.get(h)) for h in rows}
    sh = {h: (sheet_of(*p) if p else None) for h, p in pos.items()}
    zn = {h: (zona_of(*p) if p else None) for h, p in pos.items()}

    ved_sheet, ved_naslov = {}, {}
    for v in vedomosti:
        ul, lr = v["upper_left"], v["lower_right"]
        ved_sheet[v["frame_handle"]] = sheet_of((ul[0] + lr[0]) / 2, (ul[1] + lr[1]) / 2)
        ved_naslov[v["frame_handle"]] = v["sheets"][0].get("naslov", "")

    print("=" * 78)
    print("ЕТАП 7 — Тест-002: категории на 232-те одобрени позиции")
    print("=" * 78)
    print(f"прогон  : {PROGON}")
    print(f"копие   : {progon['copy']}")
    print(f"чертеж  : {DXF}  (sha256 {progon['project']['identity']['source_sha256'][:16]}…)")
    print(f"редове в batch: {len(rows)}   одобрени връзки: {len(appr)}")

    print("\n--- ЛИСТОВЕ (начертани правоъгълници) ---")
    for name, b in LISTOVE.items():
        n = sum(1 for h in rows if sh[h] == name)
        print(f"  {name} {b['handle']}  X[{b['x0']}..{b['x1']}] Y[{b['y0']}..{b['y1']}]"
              f"  надписи={n:>3}  {b['naslov']}")

    print("\n--- ВЕДОМОСТИ (заглавие от ред 1 на вградения Excel) ---")
    for v in vedomosti:
        fh = v["frame_handle"]
        n = sum(1 for r in poz_rows if r["ved"] == fh)
        print(f"  {fh}  лист {ved_sheet[fh]}  позиции={n:>3}  {ved_naslov[fh]!r}")
    print(f"  общо редове-позиции: {len(poz_rows)}"
          f"  (без пълен ключ: {sum(1 for r in poz_rows if r['nepalen'])})")

    # ---- 0. рамката на програмата срещу листа --------------------------
    cross = defaultdict(int)
    for h, r in rows.items():
        cross[(r.get("frame_index"), sh[h])] += 1
    print("\n--- рамка на програмата × начертан лист ---")
    for k in sorted(cross, key=lambda k: (k[0] is None, k[0], str(k[1]))):
        print(f"  frame_index={k[0]!r:<5} лист={k[1]!r:<5} -> {cross[k]}")

    # ---- 1. глобалната сверка (възпроизвеждане) ------------------------
    vd_glob, vd_glob_rows = defaultdict(int), defaultdict(list)
    for r in poz_rows:
        if r["nepalen"]:
            continue
        vd_glob[(r["diam_mm"], r["length_cm"])] += int(r["count"])
        vd_glob_rows[(r["diam_mm"], r["length_cm"])].append(r)
    ap_glob, ap_glob_pos = defaultdict(int), defaultdict(list)
    for l in links:
        k = (float(l["production_diameter_mm"]), float(l["production_length_cm"]))
        ap_glob[k] += int(l["production_count"])
        ap_glob_pos[k].append(l["annotation_handle"])

    globalna = {"POTVARDENO": [], "PO_MALKO": [], "POVECHE": [], "BEZ_RED": []}
    for k in sorted(ap_glob):
        p, v = ap_glob[k], vd_glob.get(k, 0)
        name = ("BEZ_RED" if k not in vd_glob else
                "POTVARDENO" if p == v else "PO_MALKO" if p < v else "POVECHE")
        globalna[name].append(k)
    print("\n--- 1. ГЛОБАЛНА СВЕРКА по (Ø, обявена дължина), всички 5 ведомости ---")
    tot = 0
    for name in ("POTVARDENO", "PO_MALKO", "POVECHE", "BEZ_RED"):
        n = sum(len(ap_glob_pos[k]) for k in globalna[name])
        tot += n
        print(f"  {name:<12} ключове={len(globalna[name]):>3}  позиции={n:>3}")
    print(f"  {'СБОР':<12} {'':>12}  позиции={tot:>3}")

    # ---- 2. категории по ЛИСТ, с неодобрените надписи -------------------
    D, Drows = defaultdict(int), defaultdict(list)     # ведомост на листа
    for r in poz_rows:
        if r["nepalen"]:
            continue
        D[(ved_sheet[r["ved"]], r["diam_mm"], r["length_cm"])] += int(r["count"])
        Drows[(ved_sheet[r["ved"]], r["diam_mm"], r["length_cm"])].append(r)
    A, Apos = defaultdict(int), defaultdict(list)      # одобрени на листа
    for l in links:
        h = l["annotation_handle"]
        k = (sh[h], float(l["production_diameter_mm"]), float(l["production_length_cm"]))
        A[k] += int(l["production_count"])
        Apos[k].append(h)
    N, Npos = defaultdict(int), defaultdict(list)      # НЕодобрени на листа
    bez_kluch = []
    for h, r in rows.items():
        if h in appr:
            continue
        d, L, c = r["diameter_mm"], r["declared_length_cm"], r["annotation_count"]
        if d is None or L is None or c is None:
            bez_kluch.append(h)
            continue
        k = (sh[h], float(d), float(L))
        N[k] += int(c)
        Npos[k].append(h)

    KAT = {
        "K1_BEZ_RED": "няма НИТО ЕДИН ред с този (Ø, дължина) в ведомостите на своя лист",
        "K2_PREVISHENIE": "одобреното на листа е ПОВЕЧЕ от ведомостите на листа",
        "K3_TOCHNO": "одобреното = ведомостите на листа и в чертежа няма други надписи с този ключ",
        "K4_TOCHNO_S_IZLISHNI": "одобреното = ведомостите, но на листа има и неодобрени надписи със същия ключ",
        "K5_NEDOSTIG_ZATVOREN": "одобрено < ведомост, но одобрени + неодобрени надписи на листа дават ТОЧНО ведомостта",
        "K6_NEDOSTIG_OSTATUK": "дори с всички надписи на листа остава по-малко от ведомостта",
    }
    kat_of: dict[str, str] = {}
    kluch_kat: dict[tuple, str] = {}
    for k in sorted(A):
        d, a, n = D.get(k, 0), A[k], N.get(k, 0)
        t = a + n
        if d == 0:
            name = "K1_BEZ_RED"
        elif a > d:
            name = "K2_PREVISHENIE"
        elif a == d:
            name = "K3_TOCHNO" if t == d else "K4_TOCHNO_S_IZLISHNI"
        else:
            name = "K5_NEDOSTIG_ZATVOREN" if t == d else "K6_NEDOSTIG_OSTATUK"
        kluch_kat[k] = name
        for h in Apos[k]:
            kat_of[h] = name

    print("\n--- 2. КАТЕГОРИИ по (лист, Ø, дължина) + неодобрените надписи ---")
    tot = 0
    kat_tab = []
    for name, opis in KAT.items():
        ks = [k for k in kluch_kat if kluch_kat[k] == name]
        n = sum(1 for h in kat_of if kat_of[h] == name)
        tot += n
        kat_tab.append({"kod": name, "opisanie": opis, "klyuchove": len(ks),
                        "pozicii": n})
        print(f"  {name:<22} ключове={len(ks):>3}  позиции={n:>3}   {opis}")
    print(f"  {'СБОР':<22} {'':>12}  позиции={tot:>3}")
    assert tot == len(links), (tot, len(links))

    # ---- 3. превишенията ------------------------------------------------
    print("\n--- 3. ПРЕВИШЕНИЯТА (глобален ключ, 4 ключа / 15 надписа) ---")
    prevish = []
    for k in [(10.0, 1000.0), (12.0, 320.0), (14.0, 300.0), (14.0, 760.0)]:
        print(f"\n  Ø{k[0]:g}×{k[1]:g}: програма={ap_glob[k]} ведомост={vd_glob[k]}"
              f" (+{ap_glob[k] - vd_glob[k]})")
        for r in vd_glob_rows[k]:
            print(f"     ВЕД {r['ved']} лист {ved_sheet[r['ved']]} Поз.{r['poz']:<3}"
                  f" брой={int(r['count']):<4} {ved_naslov[r['ved']]!r}")
        por = defaultdict(int)
        for h in ap_glob_pos[k]:
            por[zn[h]] += int(appr[h]["production_count"])
        for h in sorted(ap_glob_pos[k],
                        key=lambda h: -int(appr[h]["production_count"])):
            r, l = rows[h], appr[h]
            print(f"     {h:<9} {r['annotation_text']!r:<36} брой={int(l['production_count']):>4}"
                  f" ({pos[h][0]:.0f},{pos[h][1]:.0f}) зона={zn[h]}"
                  f" форма={l['master_shape_code']} DXF={r['geometry_length_raw']}")
            prevish.append({
                "handle": h, "text": r["annotation_text"],
                "sloy": ents[h]["layer"], "x": round(pos[h][0], 1),
                "y": round(pos[h][1], 1), "list": sh[h], "zona": zn[h],
                "broy": int(l["production_count"]),
                "diam_mm": float(l["production_diameter_mm"]),
                "dalzhina_cm": float(l["production_length_cm"]),
                "geometriya": r["candidate_handles"],
                "dxf_dalzhina_cm": r["geometry_length_raw"],
                "forma": l["master_shape_code"],
                "frame_index": r.get("frame_index"),
                "frame_title": r.get("frame_title"),
            })
        zav = defaultdict(int)
        for r in vd_glob_rows[k]:
            zav[ved_naslov[r["ved"]]] += int(r["count"])
        print(f"     по зони в чертежа: {dict(por)}")

    # ---- 4. проверка на обяснението за 82-те ----------------------------
    print("\n--- 4. 82-ТЕ „ПО-МАЛКО“: къде е недостигът ---")
    print(f"{'ключ':<12}{'прог':>5}{'вед':>5}{'нед':>5}  "
          f"{'по лист: вед|одобр|неодобр':<44}присъда")
    proverka = []
    for k in sorted(globalna["PO_MALKO"]):
        parts, ost, izvun = [], 0, 0
        for s in ("B", "C"):
            key = (s, *k)
            if key not in D and key not in A and key not in N:
                continue
            d_, a_, n_ = D.get(key, 0), A.get(key, 0), N.get(key, 0)
            parts.append(f"{s}:{d_}|{a_}|{n_}")
            if d_ and a_ == 0 and n_ == 0:
                izvun += d_
            if d_ > a_ + n_:
                ost += d_ - (a_ + n_)
        prisada = ("ЗАТВОРЕН ОТ НЕОДОБРЕНИ НАДПИСИ НА ЛИСТА" if ost == 0
                   else f"ОСТАТЪК {ost}")
        ved_kade = ", ".join(
            f"{r['ved']}({ved_sheet[r['ved']]},Поз.{r['poz']},{int(r['count'])})"
            for r in vd_glob_rows[k])
        print(f"Ø{k[0]:g}×{k[1]:g}".ljust(12)
              + f"{ap_glob[k]:>5}{vd_glob[k]:>5}{vd_glob[k] - ap_glob[k]:>5}  "
              + "; ".join(parts).ljust(44) + prisada)
        proverka.append({
            "klyuch": f"Ø{k[0]:g}×{k[1]:g}", "programa": ap_glob[k],
            "vedomost": vd_glob[k], "nedostig": vd_glob[k] - ap_glob[k],
            "pozicii": len(ap_glob_pos[k]),
            "redove_vav_vedomostite": ved_kade,
            "broy_redove": len(vd_glob_rows[k]),
            "po_list": parts, "ostatuk": ost,
            "zona_izvun_lista": izvun,
            "prisada": prisada,
        })
    n_zatvoreni = sum(1 for p in proverka if p["ostatuk"] == 0)
    poz_zatvoreni = sum(p["pozicii"] for p in proverka if p["ostatuk"] == 0)
    print(f"\n  ключове със затворен недостиг: {n_zatvoreni}/{len(proverka)}"
          f"  ({poz_zatvoreni}/{sum(p['pozicii'] for p in proverka)} позиции)")
    print(f"  ключове с ведомост на лист БЕЗ нито един надпис: "
          f"{sum(1 for p in proverka if p['zona_izvun_lista'])}")

    # ---- 4б. остатъците: има ли надпис в същата зона, който ги обяснява --
    print("\n--- 4б. ОСТАТЪЦИТЕ: надпис в същата зона със съвпадаща НАЧЕРТАНА дължина ---")
    ostatuci = []
    for p in proverka:
        if not p["ostatuk"]:
            continue
        d, L = (float(x) for x in p["klyuch"][1:].split("×"))
        for s in ("B", "C"):
            key = (s, d, L)
            manjak = D.get(key, 0) - A.get(key, 0) - N.get(key, 0)
            if manjak <= 0:
                continue
            print(f"  {p['klyuch']} на лист {s}: недостиг {manjak}")
            kandidati = []
            for h, r in rows.items():
                if sh[h] != s or r["diameter_mm"] != d:
                    continue
                if r["declared_length_cm"] == L:
                    continue          # вече е броен в ключа
                g = r["geometry_length_raw"]
                c = r["annotation_count"]
                # (а) начертаната дължина съвпада с реда на ведомостта
                # (б) бройката съвпада точно с недостига
                if (g is not None and abs(float(g) - L) <= 1.0) or c == manjak:
                    kandidati.append(h)
                    print(f"     {h:<9} {r['annotation_text']!r:<34} "
                          f"надпис L={r['declared_length_cm']} DXF="
                          f"{float(g):.2f} брой={c} зона={zn[h]} "
                          f"{'одобрен' if h in appr else 'НЕОДОБРЕН: ' + r['batch_status']}")
            if not kandidati:
                print("     няма кандидат — НЕУСТАНОВЕНО")
            ostatuci.append({"klyuch": p["klyuch"], "list": s, "manjak": manjak,
                             "kandidati": kandidati})

    # ---- 5. липсващият случай -------------------------------------------
    bez = [k for k in globalna["BEZ_RED"]]
    print("\n--- 5. ЛИПСВАЩИЯТ СЛУЧАЙ ---")
    lipsvasht = None
    for k in bez:
        for h in ap_glob_pos[k]:
            r, l = rows[h], appr[h]
            print(f"  {h}  {r['annotation_text']!r}  слой={ents[h]['layer']}"
                  f"  ({pos[h][0]:.0f},{pos[h][1]:.0f})  лист={sh[h]}  зона={zn[h]}")
            print(f"     Ø{l['production_diameter_mm']:g} × "
                  f"{l['production_length_cm']:g} cm × {int(l['production_count'])} бр."
                  f"  форма={l['master_shape_code']} сегменти="
                  f"{l['production_segment_lengths_cm']}")
            lipsvasht = {
                "handle": h, "text": r["annotation_text"],
                "sloy": ents[h]["layer"], "x": round(pos[h][0], 1),
                "y": round(pos[h][1], 1), "list": sh[h], "zona": zn[h],
                "diam_mm": l["production_diameter_mm"],
                "dalzhina_cm": l["production_length_cm"],
                "broy": int(l["production_count"]),
                "forma": l["master_shape_code"],
                "segmenti_cm": l["production_segment_lengths_cm"],
            }
    print("  редове с дължина 260 cm в петте ведомости:")
    for r in poz_rows:
        if r["length_cm"] == 260.0:
            print(f"     ВЕД {r['ved']} ({ved_sheet[r['ved']]}) Поз.{r['poz']}"
                  f" Ø{r['diam_mm']:g} брой={int(r['count'])} ТИП={r['tip']!r}")
    print("  близнаците със същата форма и сегменти:")
    for h in ("77A4AB", "77A4D1"):
        l, r = appr.get(h), rows.get(h)
        if l:
            print(f"     {h} {r['annotation_text']!r} Ø{l['production_diameter_mm']:g}"
                  f" брой={int(l['production_count'])} зона={zn[h]}"
                  f" сегменти={l['production_segment_lengths_cm']}")

    # ---- запис ----------------------------------------------------------
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "schema": "etap7-test002-kategorii/v1",
        "vhod": {
            "progon": str(PROGON), "kopie": progon["copy"],
            "batch": str(batch_path), "links": str(links_path),
            "dxf": str(DXF), "entities": str(ENTITIES),
            "source_sha256": progon["project"]["identity"]["source_sha256"],
            "app_version": progon["app_version"],
            "engine_version": progon["project"]["engine_version"],
        },
        "listove": LISTOVE,
        "vedomosti": [
            {"handle": v["frame_handle"], "list": ved_sheet[v["frame_handle"]],
             "naslov": ved_naslov[v["frame_handle"]],
             "upper_left": v["upper_left"], "lower_right": v["lower_right"],
             "pozicii": sum(1 for r in poz_rows if r["ved"] == v["frame_handle"])}
            for v in vedomosti],
        "globalna_sverka": {
            name: {"klyuchove": len(globalna[name]),
                   "pozicii": sum(len(ap_glob_pos[k]) for k in globalna[name])}
            for name in globalna},
        "kategorii": kat_tab,
        "kategoriya_na_poziciya": kat_of,
        "previsheniya": prevish,
        "proverka_na_82": proverka,
        "ostatuci": ostatuci,
        "lipsvasht_sluchay": lipsvasht,
        "neodobreni_nadpisi": len(rows) - len(appr),
        "neodobreni_bez_klyuch": bez_kluch,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nзаписано: {OUT}")


if __name__ == "__main__":
    main()
