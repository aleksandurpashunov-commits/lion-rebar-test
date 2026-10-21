# -*- coding: utf-8 -*-
"""Тестове на четеца за вградени ведомости (OLE2FRAME → CFB → BIFF8).

Всички образци тук са **синтетични** и се сглобяват на място: истинският
чертеж не участва. Така тестът проверява четенето на формата, а не числата на
конкретен проект, и не зависи от това дали чертежът е под ръка.

Червени са срещу липсата на четеца: без ``lion_rebar/dxf/ole_schedule.py``
целият модул не се импортира.
"""
from __future__ import annotations

import struct

import pytest

from lion_rebar.dxf.ole_schedule import (
    CompoundFile,
    OleScheduleError,
    detect_schedule_table,
    extract_ole2frames,
    read_biff8_workbook,
    read_embedded_documents,
    read_summary_information,
)

CFB_SIGNATURE = bytes.fromhex("D0CF11E0A1B11AE1")
_SECTOR = 512
_MINI = 64
_ENDOFCHAIN = 0xFFFFFFFE
_FREESECT = 0xFFFFFFFF
_FATSECT = 0xFFFFFFFD
_NOSTREAM = 0xFFFFFFFF
EXCEL8_CLSID = bytes.fromhex("2008020000000000c000000000000046")


# --------------------------------------------------------------------------
# Сглобяване на синтетичен CFB (минимален писач — само за тестовете)
# --------------------------------------------------------------------------


def _blocks(data: bytes, size: int) -> list[bytes]:
    if not data:
        return []
    return [data[i : i + size].ljust(size, b"\0") for i in range(0, len(data), size)]


def build_cfb(streams: dict[str, bytes], root_clsid: bytes = EXCEL8_CLSID) -> bytes:
    """Прави валиден CFB v3 с подадените потоци.

    Нарочно е минимален: директорията е дясна върволица, не балансирано дърво.
    Това стига за четец, който минава записите поред, и се проверява точно той.
    """
    names = list(streams)
    mini_names = [n for n in names if 0 < len(streams[n]) < 4096]

    ministream = b""
    minifat: list[int] = []
    mini_start: dict[str, int] = {}
    for name in mini_names:
        chunks = _blocks(streams[name], _MINI)
        first = len(minifat)
        mini_start[name] = first
        for i in range(len(chunks)):
            minifat.append(first + i + 1)
        minifat[-1] = _ENDOFCHAIN
        ministream += b"".join(chunks)

    minifat_raw = b"".join(struct.pack("<I", v) for v in minifat)
    if minifat_raw:
        pad = (-len(minifat_raw)) % _SECTOR
        minifat_raw += struct.pack("<I", _FREESECT) * (pad // 4)

    # 1) директория (попълва се след разпределението на секторите)
    entry_count = 1 + len(names)
    dir_sectors = max(1, -(-entry_count * 128 // _SECTOR))

    chains: list[tuple[str, int]] = [("dir", dir_sectors)]
    minifat_sectors = len(minifat_raw) // _SECTOR
    if minifat_sectors:
        chains.append(("minifat", minifat_sectors))
    mini_sectors = len(_blocks(ministream, _SECTOR))
    if mini_sectors:
        chains.append(("ministream", mini_sectors))
    big = [n for n in names if len(streams[n]) >= 4096]
    for name in big:
        chains.append((f"stream:{name}", len(_blocks(streams[name], _SECTOR))))

    start: dict[str, int] = {}
    next_sector = 0
    for key, count in chains:
        start[key] = next_sector
        next_sector += count
    n_data = next_sector

    n_fat = 1
    while True:
        need = -(-(n_data + n_fat) // (_SECTOR // 4))
        if need <= n_fat:
            break
        n_fat = need
    fat_first = n_data

    fat = [_FREESECT] * (n_fat * (_SECTOR // 4))
    for key, count in chains:
        base = start[key]
        for i in range(count):
            fat[base + i] = base + i + 1
        fat[base + count - 1] = _ENDOFCHAIN
    for i in range(n_fat):
        fat[fat_first + i] = _FATSECT

    # директорията
    directory = bytearray()

    def _entry(name: str, etype: int, clsid: bytes, start_sector: int, size: int,
               left: int, right: int, child: int) -> bytes:
        raw = bytearray(128)
        encoded = name.encode("utf-16-le") + b"\0\0"
        raw[: len(encoded)] = encoded
        struct.pack_into("<H", raw, 64, len(encoded))
        raw[66] = etype
        raw[67] = 1  # черен
        struct.pack_into("<3I", raw, 68, left, right, child)
        raw[80:96] = clsid
        struct.pack_into("<3I", raw, 116, start_sector, size, 0)
        return bytes(raw)

    directory += _entry(
        "Root Entry", 5, root_clsid,
        start.get("ministream", _ENDOFCHAIN), len(ministream),
        _NOSTREAM, _NOSTREAM, 1 if names else _NOSTREAM,
    )
    for idx, name in enumerate(names):
        data = streams[name]
        if not data:
            s, size = _ENDOFCHAIN, 0
        elif len(data) < 4096:
            s, size = mini_start[name], len(data)
        else:
            s, size = start[f"stream:{name}"], len(data)
        right = idx + 2 if idx + 1 < len(names) else _NOSTREAM
        directory += _entry(name, 2, b"\0" * 16, s, size, _NOSTREAM, right, _NOSTREAM)
    directory = bytes(directory).ljust(dir_sectors * _SECTOR, b"\0")

    header = bytearray(_SECTOR)
    header[0:8] = CFB_SIGNATURE
    struct.pack_into("<5H", header, 24, 0x003E, 3, 0xFFFE, 9, 6)
    struct.pack_into(
        "<9I", header, 40,
        0,                                       # брой сектори с директория (v3: 0)
        n_fat,
        start["dir"],
        0,
        4096,
        start.get("minifat", _ENDOFCHAIN),
        minifat_sectors,
        _ENDOFCHAIN,
        0,
    )
    difat = [fat_first + i for i in range(n_fat)] + [_FREESECT] * (109 - n_fat)
    struct.pack_into("<109I", header, 76, *difat)

    body = bytearray(n_data * _SECTOR)

    def _put(key: str, data: bytes) -> None:
        offset = start[key] * _SECTOR
        body[offset : offset + len(data)] = data

    _put("dir", directory)
    if minifat_sectors:
        _put("minifat", minifat_raw)
    if mini_sectors:
        _put("ministream", b"".join(_blocks(ministream, _SECTOR)))
    for name in big:
        _put(f"stream:{name}", b"".join(_blocks(streams[name], _SECTOR)))

    fat_raw = b"".join(struct.pack("<I", v) for v in fat)
    return bytes(header) + bytes(body) + fat_raw


# --------------------------------------------------------------------------
# Сглобяване на синтетичен BIFF8
# --------------------------------------------------------------------------


def rec(rid: int, data: bytes) -> bytes:
    return struct.pack("<HH", rid, len(data)) + data


def bof(substream: int) -> bytes:
    return rec(0x0809, struct.pack("<HHHHII", 0x0600, substream, 0, 0, 0, 0))


def boundsheet(name: str, position: int = 0) -> bytes:
    encoded = name.encode("utf-16-le")
    return rec(
        0x0085,
        struct.pack("<IBBBB", position, 0, 0, len(name), 0x01) + encoded,
    )


def xl_string(text: str) -> bytes:
    return struct.pack("<HB", len(text), 0x01) + text.encode("utf-16-le")


def sst_payload(strings: list[str]) -> tuple[bytes, list[int]]:
    """Полето на SST плюс отместванията, където започват знаците на всеки низ."""
    out = bytearray(struct.pack("<ii", len(strings), len(strings)))
    starts = []
    for text in strings:
        wide = any(ord(ch) > 0xFF for ch in text)
        out += struct.pack("<HB", len(text), 0x01 if wide else 0x00)
        starts.append(len(out))
        out += text.encode("utf-16-le" if wide else "latin-1")
    return bytes(out), starts


def sst_records(strings: list[str], split_in: int | None = None,
                split_chars: int = 2) -> bytes:
    """SST, по избор разкъсан на CONTINUE по средата на знаците на даден низ."""
    payload, starts = sst_payload(strings)
    if split_in is None:
        return rec(0x00FC, payload)
    wide = any(ord(ch) > 0xFF for ch in strings[split_in])
    cut = starts[split_in] + split_chars * (2 if wide else 1)
    flags = bytes([0x01 if wide else 0x00])
    return rec(0x00FC, payload[:cut]) + rec(0x003C, flags + payload[cut:])


def rk_int(value: int, hundredths: bool = False) -> int:
    raw = ((value & 0x3FFFFFFF) << 2) | 0x02
    return raw | 0x01 if hundredths else raw


def rk_double(value: float) -> int:
    bits = struct.unpack("<Q", struct.pack("<d", value))[0]
    assert bits & 0xFFFFFFFF == 0, "числото не се събира в RK без загуба"
    return (bits >> 32) & 0xFFFFFFFC


def cell_rk(row: int, col: int, rk: int, xf: int = 0) -> bytes:
    return rec(0x027E, struct.pack("<3HI", row, col, xf, rk))


def cell_labelsst(row: int, col: int, isst: int, xf: int = 0) -> bytes:
    return rec(0x00FD, struct.pack("<3HI", row, col, xf, isst))


def cell_label(row: int, col: int, text: str, xf: int = 0) -> bytes:
    return rec(0x0204, struct.pack("<3H", row, col, xf) + xl_string(text))


def cell_number(row: int, col: int, value: float, xf: int = 0) -> bytes:
    return rec(0x0203, struct.pack("<3Hd", row, col, xf, value))


def cell_formula_number(row: int, col: int, value: float, xf: int = 0) -> bytes:
    body = struct.pack("<3H", row, col, xf) + struct.pack("<d", value)
    return rec(0x0006, body + struct.pack("<HIH", 0, 0, 0))


def cell_formula_string(row: int, col: int, text: str, xf: int = 0) -> bytes:
    cached = bytes([0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0xFF, 0xFF])
    body = struct.pack("<3H", row, col, xf) + cached
    return rec(0x0006, body + struct.pack("<HIH", 0, 0, 0)) + rec(
        0x0207, xl_string(text)
    )


def cell_mulrk(row: int, col_first: int, values: list[int], xf: int = 0) -> bytes:
    body = struct.pack("<2H", row, col_first)
    for rk in values:
        body += struct.pack("<HI", xf, rk)
    return rec(0x00BD, body + struct.pack("<H", col_first + len(values) - 1))


def cell_blank(row: int, col: int, xf: int = 0) -> bytes:
    return rec(0x0201, struct.pack("<3H", row, col, xf))


def dimensions(r0: int, r1: int, c0: int, c1: int) -> bytes:
    return rec(0x0200, struct.pack("<IIHHH", r0, r1, c0, c1, 0))


EOF_REC = rec(0x000A, b"")


def sample_workbook() -> bytes:
    """Малка ведомост, построена като истинските: заглавие на листа отгоре
    (с още надписи отстрани), ред със заглавия на колоните, две позиции и
    обобщение отдолу. И заглавието, и обобщението са кандидати за заглавен ред
    — печели този, под чиито колони наистина има данни."""
    strings = [
        "Поз.", "Брой", "Ø", "ТИП", "Дължина, сm", "СПЕЦИФИКАЦИЯ — образец",
        "N8", "N10", "N12", "Дълж., m",
    ]
    globals_part = (
        bof(0x0005)
        + rec(0x0042, struct.pack("<H", 1200))
        + boundsheet("Лист1")
        + boundsheet("Празен")
        + sst_records(strings, split_in=5, split_chars=4)
        + EOF_REC
    )
    sheet = (
        bof(0x0010)
        + dimensions(0, 6, 0, 11)
        # ред 0 — заглавие на листа плюс справката с единичните тегла отстрани
        + cell_labelsst(0, 0, 5)
        + cell_labelsst(0, 8, 6)
        + cell_labelsst(0, 9, 7)
        + cell_labelsst(0, 10, 8)
        # ред 1 — истинските заглавия на колоните (+ единичните тегла)
        + cell_labelsst(1, 0, 0)
        + cell_labelsst(1, 1, 1)
        + cell_labelsst(1, 2, 2)
        + cell_labelsst(1, 3, 3)
        + cell_labelsst(1, 4, 4)
        + cell_number(1, 8, 0.395)
        + cell_number(1, 9, 0.617)
        + cell_number(1, 10, 0.888)
        # ред 2 — позиция с цели числа и кеширана формула
        + cell_mulrk(2, 0, [rk_int(8), rk_int(3)])
        + cell_formula_string(2, 2, "N10")
        + cell_blank(2, 3)
        + cell_rk(2, 4, rk_int(440))
        + cell_formula_number(2, 5, 13.2)
        # ред 3 — стотни и double
        + cell_rk(3, 0, rk_int(12))
        + cell_rk(3, 1, rk_int(440, hundredths=True))
        + cell_label(3, 2, "N12")
        + cell_number(3, 3, 17.6)
        + cell_rk(3, 4, rk_double(165.5))
        + cell_rk(3, 5, rk_int(-5))
        # редове 4–5 — обобщение: също кандидат за заглавен ред, но с почти
        # нищо под себе си
        + cell_labelsst(4, 0, 2)
        + cell_labelsst(4, 1, 6)
        + cell_labelsst(4, 2, 7)
        + cell_labelsst(4, 3, 8)
        + cell_labelsst(5, 0, 9)
        + cell_number(5, 1, 734.7)
        + cell_number(5, 2, 56.5)
        + cell_number(5, 3, 230.2)
        + EOF_REC
    )
    empty_sheet = bof(0x0010) + dimensions(0, 0, 0, 0) + EOF_REC
    return globals_part + sheet + empty_sheet


# --------------------------------------------------------------------------
# Сглобяване на синтетичен property set (\x05SummaryInformation)
# --------------------------------------------------------------------------

_FILETIME_EPOCH = 116444736000000000


def filetime(iso: str) -> int:
    import datetime

    moment = datetime.datetime.fromisoformat(iso)
    return int(moment.timestamp() * 10_000_000) + _FILETIME_EPOCH


def build_summary(properties: list[tuple[int, int, bytes]]) -> bytes:
    """Property set с една секция. ``properties`` е (pid, VT, вече опакована стойност)."""
    values = bytearray()
    offsets = []
    table_size = 8 + len(properties) * 8
    for _pid, vtype, payload in properties:
        offsets.append(table_size + len(values))
        block = struct.pack("<I", vtype) + payload
        block += b"\0" * ((-len(block)) % 4)
        values += block
    section = bytearray(struct.pack("<II", table_size + len(values), len(properties)))
    for (pid, _vt, _p), offset in zip(properties, offsets):
        section += struct.pack("<2I", pid, offset)
    section += values
    head = struct.pack("<HHI", 0xFFFE, 0, 0) + b"\0" * 16 + struct.pack("<I", 1)
    head += b"\xe0\x85\x9f\xf2\xf9\x4f\x68\x10\xab\x91\x08\x00\x2b\x27\xb3\xd9"
    head += struct.pack("<I", 48)
    return head + bytes(section)


def lpstr(text: str, codepage: str = "cp1251") -> bytes:
    raw = text.encode(codepage) + b"\0"
    return struct.pack("<I", len(raw)) + raw


# --------------------------------------------------------------------------
# Сглобяване на синтетичен DXF
# --------------------------------------------------------------------------


def dxf_with_ole(payloads: list[bytes], prefix: bytes = b"\xAB" * 128) -> bytes:
    """DXF с по един OLE2FRAME на подаден блок плюс една празна рамка."""
    out = [b"  0\nSECTION\n  2\nENTITIES\n"]
    out.append(
        b"  0\nOLE2FRAME\n  5\nAAA1\n330\nOWN1\n100\nAcDbEntity\n  8\nSTANDARD\n"
        b"100\nAcDbOle2Frame\n 70\n     2\n  1\nOLE\n"
        b"1001\nACAD\n1000\nOLEBEGIN\n1070\n    70\n1070\n     1\n"
        b"1070\n    90\n1071\n        0\n1000\nOLEEND\n"
    )
    for index, payload in enumerate(payloads):
        blob = prefix + payload
        chunks = [
            blob[i : i + 127].hex().upper().encode("ascii")
            for i in range(0, len(blob), 127)
        ]
        head = (
            b"  0\nOLE2FRAME\n  5\nB00%d\n330\nOWN1\n100\nAcDbEntity\n"
            b"  8\nOtvori\n100\nAcDbOle2Frame\n 70\n     2\n"
            b"  3\n\xd0\xa0\xd0\xb0\xd0\xb1\xd0\xbe\xd1\x82\xd0\xb5\xd0\xbd \xd0\xbb\xd0\xb8\xd1\x81\xd1\x82\n"
            b" 10\n100.5\n 20\n900.25\n 30\n0.0\n"
            b" 11\n600.5\n 21\n400.25\n 31\n0.0\n"
            b" 71\n     2\n 72\n     0\n 73\n     2\n"
        ) % index
        head += b" 90\n%d\n" % len(blob)
        out.append(head + b"".join(b"310\n" + c + b"\n" for c in chunks))
    out.append(b"  0\nENDSEC\n  0\nEOF\n")
    return b"".join(out)


# --------------------------------------------------------------------------
# Тестове — DXF слой
# --------------------------------------------------------------------------


def test_празна_рамка_се_разпознава_като_празна():
    frames = extract_ole2frames(dxf_with_ole([]))
    assert len(frames) == 1
    empty = frames[0]
    assert empty.handle == "AAA1"
    assert empty.layer == "STANDARD"
    assert empty.data == b""
    assert empty.container_kind() == "празен"
    assert empty.cfb_offset == -1
    # няма група 90 и няма 310 — точно това значи „няма OLE съдържание във файла"
    assert empty.declared_length is None
    assert empty.type_name == ""
    assert empty.upper_left is None
    # xdata-та се запазва сурова; полетата в OLEBEGIN НЕ са дължина на данните
    assert empty.xdata[:2] == [(1001, "ACAD"), (1000, "OLEBEGIN")]
    assert empty.xdata_block() == [
        (1070, "70"), (1070, "1"), (1070, "90"), (1071, "0"),
    ]


def test_групите_310_се_сглобяват_и_декодират():
    payload = build_cfb({"Workbook": sample_workbook()})
    frames = extract_ole2frames(dxf_with_ole([payload]))
    assert len(frames) == 2
    frame = frames[1]
    assert frame.handle == "B000"
    assert frame.layer == "Otvori"
    assert frame.owner_handle == "OWN1"
    assert frame.type_name == "Работен лист"
    assert frame.ole_object_type == 2
    # обявената дължина (код 90) трябва да съвпада с реално сглобената
    assert frame.declared_length == len(frame.data)
    assert frame.width == pytest.approx(500.0)
    assert frame.height == pytest.approx(500.0)
    assert frame.container_kind() == "CFB"
    # подписът НЕ е на нула — търси се, а не се приема фиксирано отместване
    assert frame.cfb_offset == 128
    assert frame.compound_bytes()[:8] == CFB_SIGNATURE


def test_подпис_без_cfb_не_се_обявява_за_четим():
    frames = extract_ole2frames(dxf_with_ole([b"PK\x03\x04" + b"\0" * 50]))
    assert frames[1].container_kind() == "ZIP (OOXML)"
    with pytest.raises(OleScheduleError):
        frames[1].compound_bytes()


# --------------------------------------------------------------------------
# Тестове — CFB слой
# --------------------------------------------------------------------------


def test_cfb_чете_директория_и_малък_поток():
    raw = build_cfb({"Workbook": sample_workbook(), "\x01Ole": b"malko"})
    cfb = CompoundFile(raw)
    assert cfb.major_version == 3
    assert cfb.sector_size == 512
    assert set(cfb.stream_names()) == {"Workbook", "\x01Ole"}
    assert cfb.read("\x01Ole") == b"malko"
    assert cfb.root_class().startswith("Excel.Sheet.8")


def test_cfb_чете_голям_поток_през_fat():
    big = bytes(range(256)) * 40  # 10240 B > прага от 4096
    raw = build_cfb({"Big": big, "Small": b"x" * 10})
    cfb = CompoundFile(raw)
    assert cfb.read("Big") == big
    assert cfb.read("Small") == b"x" * 10


def test_cfb_без_подпис_отказва():
    with pytest.raises(OleScheduleError):
        CompoundFile(b"\x00" * 1024)


# --------------------------------------------------------------------------
# Тестове — BIFF8 слой
# --------------------------------------------------------------------------


def test_biff8_чете_листовете_и_клетките():
    wb = read_biff8_workbook(sample_workbook())
    assert wb.biff_version == 0x0600
    assert wb.codepage == 1200
    assert [s.name for s in wb.sheets] == ["Лист1", "Празен"]
    sheet = wb.sheets[0]
    assert sheet.dimensions == (0, 6, 0, 11)
    assert wb.sheets[1].cells == {}

    стойности = {к: c.value for к, c in sheet.cells.items()}
    assert стойности[(1, 0)] == "Поз."
    assert стойности[(1, 2)] == "Ø"
    assert стойности[(2, 0)] == 8.0 and sheet.cells[(2, 0)].source == "MULRK"
    assert стойности[(2, 1)] == 3.0
    assert стойности[(2, 2)] == "N10"
    assert sheet.cells[(2, 2)].source == "FORMULA(кеш)+STRING"
    assert стойности[(2, 3)] is None and sheet.cells[(2, 3)].kind == "blank"
    assert стойности[(2, 4)] == 440.0
    assert стойности[(2, 5)] == pytest.approx(13.2)
    assert sheet.cells[(2, 5)].source == "FORMULA(кеш)"


def test_biff8_декодира_всички_разновидности_на_rk():
    sheet = read_biff8_workbook(sample_workbook()).sheets[0]
    assert sheet.cells[(3, 1)].value == pytest.approx(4.40)   # цяло / 100
    assert sheet.cells[(3, 3)].value == pytest.approx(17.6)   # NUMBER
    assert sheet.cells[(3, 4)].value == pytest.approx(165.5)  # double в RK
    assert sheet.cells[(3, 5)].value == pytest.approx(-5.0)   # отрицателно цяло


def test_sst_прескача_границата_на_continue_без_да_чупи_кирилицата():
    wb = read_biff8_workbook(sample_workbook())
    # низът е разкъсан на CONTINUE точно по средата на знаците си
    assert wb.shared_strings == [
        "Поз.", "Брой", "Ø", "ТИП", "Дължина, сm", "СПЕЦИФИКАЦИЯ — образец",
        "N8", "N10", "N12", "Дълж., m",
    ]
    assert wb.sst_declared_unique == len(wb.shared_strings)


def test_sst_без_continue_дава_същото():
    strings = ["Поз.", "Брой", "Ø"]
    stream = (
        bof(0x0005) + boundsheet("Л") + sst_records(strings) + EOF_REC
        + bof(0x0010) + EOF_REC
    )
    assert read_biff8_workbook(stream).shared_strings == strings


def test_парола_спира_четенето_вместо_да_гадае():
    stream = bof(0x0005) + rec(0x002F, b"\x01\x00") + EOF_REC
    with pytest.raises(OleScheduleError, match="парола"):
        read_biff8_workbook(stream)


def test_по_стар_biff_се_отказва_явно():
    stream = rec(0x0809, struct.pack("<HH", 0x0500, 0x0005)) + EOF_REC
    with pytest.raises(OleScheduleError, match="BIFF8"):
        read_biff8_workbook(stream)


# --------------------------------------------------------------------------
# Тестове — свойствата на документа
# --------------------------------------------------------------------------


SAMPLE_SUMMARY = [
    (1, 2, struct.pack("<h", 1251)),                     # codepage
    (4, 30, lpstr("ПРОЕКТАНТ")),                         # автор
    (18, 30, lpstr("Microsoft Excel")),                  # програма
    (12, 64, struct.pack("<Q", filetime("2004-09-09T18:39:01+00:00"))),
    (13, 64, struct.pack("<Q", filetime("2023-08-22T14:09:35+00:00"))),
]


def test_свойствата_дават_кога_е_записан_документът():
    свойства = read_summary_information(build_summary(SAMPLE_SUMMARY))
    assert свойства["codepage"] == 1251
    assert свойства["автор"] == "ПРОЕКТАНТ"
    assert свойства["програма"] == "Microsoft Excel"
    assert свойства["създаден"].startswith("2004-09-09T18:39:01")
    assert свойства["последно_записан"].startswith("2023-08-22T14:09:35")


def test_свойствата_се_четат_през_целия_път():
    payload = build_cfb({
        "Workbook": sample_workbook(),
        "\x05SummaryInformation": build_summary(SAMPLE_SUMMARY),
    })
    doc = read_embedded_documents(dxf_with_ole([payload]))[1]
    assert doc.summary["автор"] == "ПРОЕКТАНТ"
    assert doc.summary["последно_записан"].startswith("2023-08-22")


def test_негоден_property_set_не_сваля_четенето_на_таблицата():
    payload = build_cfb({
        "Workbook": sample_workbook(),
        "\x05SummaryInformation": b"\x00" * 64,
    })
    doc = read_embedded_documents(dxf_with_ole([payload]))[1]
    assert doc.summary == {}
    assert doc.error == ""
    assert doc.workbook is not None


# --------------------------------------------------------------------------
# Тестове — таблицата
# --------------------------------------------------------------------------


def test_заглавният_ред_е_този_с_данни_под_себе_си():
    sheet = read_biff8_workbook(sample_workbook()).sheets[0]
    table = detect_schedule_table(sheet)
    # ред 0 (заглавие на листа) и ред 4 (обобщение) също имат по 4 текстови
    # клетки; печели ред 1, защото под неговите колони има данни
    assert table.header_row == 1
    # заглавията се четат както са — включително числата на справката отстрани;
    # ролите им се решават от този, който тълкува ведомостта, не от четеца
    assert table.columns == {
        0: "Поз.", 1: "Брой", 2: "Ø", 3: "ТИП", 4: "Дължина, сm",
        8: "0.395", 9: "0.617", 10: "0.888",
    }
    # модулът дава ВСИЧКИ редове под заглавния — обобщението не се изхвърля
    # мълчаливо, отделянето му е работа на този, който тълкува ведомостта
    assert [r["_row"] for r in table.rows] == [2, 3, 4, 5]
    first = table.rows[0]
    assert first["Поз."] == 8.0
    assert first["Ø"] == "N10"
    assert first["Дължина, сm"] == 440.0
    # проследимост: за всяка стойност се помни от кой BIFF запис идва
    assert first["Ø__source"] == "FORMULA(кеш)+STRING"
    assert first["Поз.__source"] == "MULRK"


# --------------------------------------------------------------------------
# Целият път
# --------------------------------------------------------------------------


def test_целият_път_от_dxf_до_редовете():
    payload = build_cfb({"Workbook": sample_workbook(), "\x01Ole": b"ole"})
    docs = read_embedded_documents(dxf_with_ole([payload]))
    assert len(docs) == 2
    assert docs[0].container == "празен" and docs[0].workbook is None

    doc = docs[1]
    assert doc.container == "CFB"
    assert doc.error == ""
    assert doc.root_clsid == "00020820-0000-0000-c000-000000000046"
    assert doc.container_version.startswith("CFB 3.62, сектор 512 B")
    assert dict(doc.streams)["Workbook"] > 0
    assert doc.workbook is not None
    table = detect_schedule_table(doc.workbook.sheets[0])
    assert len(table.rows) == 4
    assert table.rows[1]["Ø"] == "N12"


def test_повреден_вграден_документ_не_сваля_останалите():
    добър = build_cfb({"Workbook": sample_workbook()})
    лош = CFB_SIGNATURE + b"\x00" * 600  # подпис има, съдържание — не
    docs = read_embedded_documents(dxf_with_ole([лош, добър]))
    assert docs[1].container == "CFB" and docs[1].error
    assert docs[1].workbook is None
    assert docs[2].workbook is not None
