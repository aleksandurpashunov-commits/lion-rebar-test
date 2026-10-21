# -*- coding: utf-8 -*-
"""Чете вградените в DXF ведомости (OLE2FRAME → CFB → Excel BIFF8).

Защо съществува
---------------
В ``Тест-002`` проектантската ведомост не е начертана с текст, а е **вграден
документ** — обект ``OLE2FRAME``, в който лежи цял файл на Microsoft Excel
97-2003. Разпознаването на форми не го вижда, а той съдържа декларираните от
проектанта позиции, бройки, диаметри и дължини. Този модул го изважда, за да
може ведомостта да се сравни с разпознатото, вместо да се гадае.

Какво точно чете
----------------
1. ``extract_ole2frames`` — минава суровия DXF по двойки (код, стойност) и
   събира всички ``OLE2FRAME``: handle, слой, owner handle, име на типа
   (код 3), двата ъгъла на рамката (кодове 10/20/30 и 11/21/31), типа на
   обекта (код 71), обявената дължина на данните (код 90) и самите данни от
   групите ``310`` — шестнайсетичен текст, който се сглобява и декодира.
2. ``AutoCadOleBlob`` — двоичният блок на AutoCAD започва със собствен
   заглавен блок (координати на рамката в double), а истинският документ
   започва при подписа на CFB ``D0CF11E0A1B11AE1``. Търси се подписът, а не
   се приема фиксирано отместване.
3. ``CompoundFile`` — четец на Compound File Binary (MS-CFB): заглавие, DIFAT,
   FAT, MiniFAT, директория и потоци (включително мини-потоци под 4096 B).
   Проверени са версия 3 (сектор 512 B) и версия 4 (сектор 4096 B) по спец;
   в наличните данни се среща само версия 3.
4. ``read_summary_information`` — свойствата на вградения документ от потока
   ``\\x05SummaryInformation`` ([MS-OLEPS]): автор, програма, кога е създаден и
   кога е записан последно. Това е единственото проследимо доказателство коя
   таблица е по-нова; чете се, а не се предполага.
5. ``read_biff8_workbook`` — четец на Excel BIFF8 (``Workbook`` поток):
   BOF, BOUNDSHEET, CODEPAGE, FORMAT, XF, SST (с ``CONTINUE`` и смяната на
   флага за 8/16-битови знаци по средата на низ), LABELSST, LABEL, RK, MULRK,
   NUMBER, BLANK, MULBLANK, BOOLERR, FORMULA + STRING, DIMENSIONS.

Какво НЕ прави (нарочно)
------------------------
* **Не изпълнява нищо.** Формулите не се смятат — взема се само кешираният
  резултат, записан от Excel в самия запис ``FORMULA``. Макроси, външни
  връзки (``SUPBOOK``/``EXTERNNAME``), DDE и OLE автоматизация не се пипат.
* Не чете ``\\x02OlePres000`` (растерната картинка за показване в AutoCAD) —
  тя не носи числа.
* Не поддържа защитени с парола работни книги: при запис ``FILEPASS`` четенето
  спира с ``OleScheduleError``. Няма опит за разшифроване.
* Не поддържа по-стари BIFF (5/7) и Excel 2007+ (zip/OOXML) — форматът се
  разпознава и се съобщава, но не се чете. За OOXML в проекта вече има
  ``openpyxl``.
* Не тълкува формати на числа отвъд това да запише кода на формата; изведената
  стойност е **суровата** стойност от файла, а не това, което Excel показва
  след закръгляне.
* Не смята обединени клетки (``MERGEDCELLS``) — стойността стои в горната лява
  клетка, останалите излизат празни.

Източници на формата (проверени срещу данните в ``Тест-002``)
-------------------------------------------------------------
* ``[MS-CFB]`` Compound File Binary File Format, Microsoft Open Specifications.
* ``[MS-XLS]`` Excel Binary File Format (.xls) Structure, Microsoft Open
  Specifications (записи BOF/BOUNDSHEET/SST/RK/LABELSST/FORMULA).
* DXF Reference, AutoCAD — обект ``OLE2FRAME``, групи 3/10/11/71/72/90/310.
Точните адреси, датите на четене и лицензът са записани в отчета
``OLE_AGENT.json`` (ключ ``sources``).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Sequence

CFB_SIGNATURE = bytes.fromhex("D0CF11E0A1B11AE1")
ZIP_SIGNATURE = b"PK\x03\x04"

_FREESECT = 0xFFFFFFFF
_ENDOFCHAIN = 0xFFFFFFFE
_FATSECT = 0xFFFFFFFD
_DIFSECT = 0xFFFFFFFC

# CLSID-и на root entry, по които се познава какъв документ е вграден.
_ROOT_CLSIDS = {
    "00020820-0000-0000-c000-000000000046": "Excel.Sheet.8 (Excel 97-2003 работен лист)",
    "00020821-0000-0000-c000-000000000046": "Excel.Chart.8 (Excel 97-2003 диаграма)",
    "00020810-0000-0000-c000-000000000046": "Excel.Sheet.5 (Excel 5.0/95 работен лист)",
    "00020906-0000-0000-c000-000000000046": "Word.Document.8 (Word 97-2003)",
    "00020d0b-0000-0000-c000-000000000046": "MailMessage",
}


class OleScheduleError(RuntimeError):
    """Данните не са в очаквания вид и четенето е спряно нарочно."""


# --------------------------------------------------------------------------
# 1. DXF → OLE2FRAME
# --------------------------------------------------------------------------


@dataclass
class Ole2Frame:
    """Един ``OLE2FRAME`` от чертежа заедно със суровия му двоичен блок."""

    handle: str
    owner_handle: str
    layer: str
    type_name: str
    upper_left: tuple[float, float, float] | None
    lower_right: tuple[float, float, float] | None
    ole_object_type: int | None
    declared_length: int | None
    data: bytes
    #: Разширените данни (кодове ≥ 1000) както стоят в чертежа. При рамка без
    #: групи 310 това е единственото, което казва какво е имало вътре.
    xdata: list[tuple[int, str]] = field(default_factory=list)

    @property
    def width(self) -> float | None:
        if not self.upper_left or not self.lower_right:
            return None
        return abs(self.lower_right[0] - self.upper_left[0])

    @property
    def height(self) -> float | None:
        if not self.upper_left or not self.lower_right:
            return None
        return abs(self.lower_right[1] - self.upper_left[1])

    @property
    def cfb_offset(self) -> int:
        """Отместване на подписа на CFB в блока, или -1."""
        return self.data.find(CFB_SIGNATURE)

    def xdata_block(self, name: str = "OLEBEGIN") -> list[tuple[int, str]]:
        """Двойките между ``name`` и съответния ``…END`` маркер в xdata.

        ВНИМАНИЕ: числата в ``OLEBEGIN`` НЕ са дължина на двоичните данни.
        Измерено върху ``Тест-002``: полетата 3/90/43 и 4/91/44 описват двата
        шрифта на рамката (име, размер, височина) — при празните рамки името е
        празно и размерът 0. Дължината на данните е в група 90 на самия обект,
        а данните — в групите 310.
        """
        out: list[tuple[int, str]] = []
        inside = False
        end = name.replace("BEGIN", "END")
        for code, value in self.xdata:
            if value == name:
                inside = True
                continue
            if inside and value == end:
                break
            if inside:
                out.append((code, value))
        return out

    def container_kind(self) -> str:
        """Какъв контейнер лежи вътре — по подпис, не по име."""
        if not self.data:
            return "празен"
        if self.cfb_offset >= 0:
            return "CFB"
        if ZIP_SIGNATURE in self.data[:4096]:
            return "ZIP (OOXML)"
        return "непознат"

    def compound_bytes(self) -> bytes:
        """Байтовете от подписа на CFB нататък."""
        pos = self.cfb_offset
        if pos < 0:
            raise OleScheduleError(
                f"OLE2FRAME {self.handle}: няма подпис на CFB в {len(self.data)} B"
            )
        return self.data[pos:]


def iter_dxf_tags(raw: bytes) -> Iterator[tuple[int, bytes]]:
    """Минава суровия DXF по двойки (код, стойност) без ezdxf.

    Нарочно работи с байтове: интересната част е шестнайсетичен ASCII текст,
    а имената са UTF-8 (DXF AC1021 и нагоре).
    """
    lines = raw.split(b"\n")
    i = 0
    n = len(lines)
    while i + 1 < n:
        head = lines[i].strip()
        if not head:
            i += 1
            continue
        try:
            code = int(head)
        except ValueError:
            i += 1
            continue
        yield code, lines[i + 1].rstrip(b"\r")
        i += 2


def _decode_dxf_text(value: bytes) -> str:
    for enc in ("utf-8", "cp1251"):
        try:
            return value.decode(enc)
        except UnicodeDecodeError:
            continue
    return value.decode("latin-1")


def extract_ole2frames(source: Path | bytes) -> list[Ole2Frame]:
    """Изважда всички ``OLE2FRAME`` от DXF файл (или вече прочетени байтове)."""
    raw = source if isinstance(source, bytes) else Path(source).read_bytes()
    frames: list[Ole2Frame] = []
    current: list[tuple[int, bytes]] | None = None

    def _flush(tags: list[tuple[int, bytes]]) -> None:
        frames.append(_build_frame(tags))

    for code, value in iter_dxf_tags(raw):
        if code == 0:
            if current is not None:
                _flush(current)
                current = None
            if value.strip() == b"OLE2FRAME":
                current = []
            continue
        if current is not None:
            current.append((code, value))
    if current is not None:
        _flush(current)
    return frames


def _build_frame(tags: Sequence[tuple[int, bytes]]) -> Ole2Frame:
    first: dict[int, bytes] = {}
    chunks: list[bytes] = []
    xdata: list[tuple[int, str]] = []
    for code, value in tags:
        if code == 310:
            chunks.append(value.strip())
            continue
        if code >= 1000:
            xdata.append((code, _decode_dxf_text(value).strip()))
            continue
        if code not in first:
            first[code] = value

    def _txt(code: int, default: str = "") -> str:
        return _decode_dxf_text(first[code]) if code in first else default

    def _int(code: int) -> int | None:
        if code not in first:
            return None
        try:
            return int(first[code].strip())
        except ValueError:
            return None

    def _point(base: int) -> tuple[float, float, float] | None:
        if base not in first:
            return None
        try:
            return (
                float(first[base].strip()),
                float(first[base + 10].strip()) if base + 10 in first else 0.0,
                float(first[base + 20].strip()) if base + 20 in first else 0.0,
            )
        except ValueError:
            return None

    hex_text = b"".join(chunks)
    try:
        data = bytes.fromhex(hex_text.decode("ascii")) if hex_text else b""
    except ValueError as exc:  # pragma: no cover - повреден файл
        raise OleScheduleError(f"група 310 не е шестнайсетичен текст: {exc}") from exc

    return Ole2Frame(
        handle=_txt(5, "?"),
        owner_handle=_txt(330, ""),
        layer=_txt(8, ""),
        type_name=_txt(3, ""),
        upper_left=_point(10),
        lower_right=_point(11),
        ole_object_type=_int(71),
        declared_length=_int(90),
        data=data,
        xdata=xdata,
    )


# --------------------------------------------------------------------------
# 2. CFB (MS-CFB)
# --------------------------------------------------------------------------


@dataclass
class DirEntry:
    index: int
    name: str
    entry_type: int
    clsid: str
    start_sector: int
    size: int

    @property
    def is_stream(self) -> bool:
        return self.entry_type == 2


class CompoundFile:
    """Минимален четец на Compound File Binary (MS-CFB). Само четене."""

    def __init__(self, data: bytes):
        if data[:8] != CFB_SIGNATURE:
            raise OleScheduleError("липсва подпис на CFB")
        self.data = data
        (
            self.minor_version,
            self.major_version,
            byte_order,
            sector_shift,
            mini_sector_shift,
        ) = struct.unpack_from("<5H", data, 24)
        if byte_order != 0xFFFE:
            raise OleScheduleError(f"неочакван ред на байтовете 0x{byte_order:04X}")
        self.sector_size = 1 << sector_shift
        self.mini_sector_size = 1 << mini_sector_shift
        (
            self.num_dir_sectors,
            self.num_fat_sectors,
            self.first_dir_sector,
            _transaction,
            self.mini_stream_cutoff,
            self.first_minifat_sector,
            self.num_minifat_sectors,
            self.first_difat_sector,
            self.num_difat_sectors,
        ) = struct.unpack_from("<9I", data, 40)
        self._fat = self._load_fat()
        self._minifat = self._load_minifat()
        self.entries = self._load_directory()
        self._mini_stream: bytes | None = None

    # -- сектори ----------------------------------------------------------
    def _sector(self, number: int) -> bytes:
        offset = (number + 1) * self.sector_size
        chunk = self.data[offset : offset + self.sector_size]
        if len(chunk) != self.sector_size:
            raise OleScheduleError(f"сектор {number} излиза извън данните")
        return chunk

    def _load_fat(self) -> list[int]:
        per_sector = self.sector_size // 4
        difat = list(struct.unpack_from("<109I", self.data, 76))
        nxt = self.first_difat_sector
        seen: set[int] = set()
        while nxt not in (_ENDOFCHAIN, _FREESECT) and nxt not in seen:
            seen.add(nxt)
            values = struct.unpack("<%dI" % per_sector, self._sector(nxt))
            difat.extend(values[:-1])
            nxt = values[-1]
        fat: list[int] = []
        for sector in difat[: self.num_fat_sectors]:
            if sector in (_FREESECT, _ENDOFCHAIN):
                continue
            fat.extend(struct.unpack("<%dI" % per_sector, self._sector(sector)))
        return fat

    def _chain(self, start: int) -> Iterator[int]:
        cur = start
        seen: set[int] = set()
        while cur not in (_ENDOFCHAIN, _FREESECT, _FATSECT, _DIFSECT):
            if cur in seen:
                raise OleScheduleError("зациклена верига от сектори")
            seen.add(cur)
            yield cur
            if cur >= len(self._fat):
                return
            cur = self._fat[cur]

    def _read_chain(self, start: int, size: int | None = None) -> bytes:
        buf = b"".join(self._sector(s) for s in self._chain(start))
        return buf if size is None else buf[:size]

    def _load_minifat(self) -> list[int]:
        raw = self._read_chain(self.first_minifat_sector)
        if not raw:
            return []
        return list(struct.unpack("<%dI" % (len(raw) // 4), raw))

    def _load_directory(self) -> list[DirEntry]:
        raw = self._read_chain(self.first_dir_sector)
        entries: list[DirEntry] = []
        for i in range(0, len(raw) - 127, 128):
            blob = raw[i : i + 128]
            name_len = struct.unpack_from("<H", blob, 64)[0]
            name = (
                blob[: max(0, name_len - 2)].decode("utf-16-le", "replace")
                if name_len >= 2
                else ""
            )
            entry_type = blob[66]
            clsid_raw = blob[80:96]
            start, size_lo, size_hi = struct.unpack_from("<3I", blob, 116)
            size = size_lo | (size_hi << 32) if self.major_version >= 4 else size_lo
            entries.append(
                DirEntry(
                    index=i // 128,
                    name=name,
                    entry_type=entry_type,
                    clsid=_format_clsid(clsid_raw),
                    start_sector=start,
                    size=size,
                )
            )
        return entries

    # -- потоци -----------------------------------------------------------
    @property
    def root(self) -> DirEntry:
        if not self.entries:
            raise OleScheduleError("празна директория")
        return self.entries[0]

    def root_class(self) -> str:
        """Човешко име на вградения документ по CLSID на root entry."""
        return _ROOT_CLSIDS.get(self.root.clsid, "непознат CLSID")

    def stream_names(self) -> list[str]:
        return [e.name for e in self.entries if e.is_stream]

    def find(self, name: str) -> DirEntry | None:
        for entry in self.entries:
            if entry.is_stream and entry.name == name:
                return entry
        return None

    def read(self, entry: DirEntry | str) -> bytes:
        if isinstance(entry, str):
            found = self.find(entry)
            if found is None:
                raise OleScheduleError(f"няма поток {entry!r}")
            entry = found
        if entry.size < self.mini_stream_cutoff:
            return self._read_mini(entry)
        return self._read_chain(entry.start_sector, entry.size)

    def _read_mini(self, entry: DirEntry) -> bytes:
        if self._mini_stream is None:
            self._mini_stream = self._read_chain(self.root.start_sector, self.root.size)
        out = bytearray()
        cur = entry.start_sector
        seen: set[int] = set()
        while cur not in (_ENDOFCHAIN, _FREESECT) and len(out) < entry.size:
            if cur in seen:
                raise OleScheduleError("зациклена верига в MiniFAT")
            seen.add(cur)
            offset = cur * self.mini_sector_size
            out += self._mini_stream[offset : offset + self.mini_sector_size]
            if cur >= len(self._minifat):
                break
            cur = self._minifat[cur]
        return bytes(out[: entry.size])


def _format_clsid(raw: bytes) -> str:
    if len(raw) != 16 or raw == b"\x00" * 16:
        return "00000000-0000-0000-0000-000000000000"
    d1, d2, d3 = struct.unpack_from("<IHH", raw, 0)
    rest = raw[8:]
    return "%08x-%04x-%04x-%s-%s" % (d1, d2, d3, rest[:2].hex(), rest[2:].hex())


# --------------------------------------------------------------------------
# 2б. Служебните свойства (\x05SummaryInformation) — [MS-OLEPS]
# --------------------------------------------------------------------------

#: Идентификаторите от SummaryInformation, които носят проследимост.
_SUMMARY_PIDS = {
    1: "codepage", 2: "заглавие", 3: "тема", 4: "автор", 5: "ключови_думи",
    6: "бележки", 7: "шаблон", 8: "последно_записал", 9: "редакция",
    10: "време_на_редактиране", 11: "последно_печатано", 12: "създаден",
    13: "последно_записан", 14: "страници", 15: "думи", 16: "знаци",
    18: "програма", 19: "защита",
}

_VT_I2, _VT_I4, _VT_LPSTR, _VT_FILETIME, _VT_LPWSTR = 2, 3, 30, 64, 31
_FILETIME_EPOCH_NS100 = 116444736000000000  # 1601-01-01 → 1970-01-01


def _filetime_to_iso(value: int) -> str | None:
    if value <= 0:
        return None
    import datetime

    seconds = (value - _FILETIME_EPOCH_NS100) / 1e7
    try:
        return datetime.datetime.fromtimestamp(
            seconds, datetime.timezone.utc
        ).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def read_summary_information(stream: bytes) -> dict[str, object]:
    """Чете свойствата на вградения документ (автор, кога е записан, програма).

    Само описателните типове: цели числа, низове и дати. Всичко останало се
    пропуска — тук не се гадае.
    """
    if len(stream) < 48 or struct.unpack_from("<H", stream, 0)[0] != 0xFFFE:
        raise OleScheduleError("не е property set поток")
    count = struct.unpack_from("<I", stream, 24)[0]
    out: dict[str, object] = {}
    for index in range(count):
        base = 28 + index * 20
        if base + 20 > len(stream):
            break
        section = struct.unpack_from("<I", stream, base + 16)[0]
        if section + 8 > len(stream):
            continue
        properties = struct.unpack_from("<I", stream, section + 4)[0]
        raw: dict[int, tuple[int, bytes]] = {}
        for k in range(properties):
            entry = section + 8 + k * 8
            if entry + 8 > len(stream):
                break
            pid, offset = struct.unpack_from("<2I", stream, entry)
            start = section + offset
            if start + 4 > len(stream):
                continue
            vtype = struct.unpack_from("<I", stream, start)[0]
            raw[pid] = (vtype, stream[start + 4 : start + 4 + 1024])
        codepage = 1252
        if 1 in raw and raw[1][0] == _VT_I2:
            codepage = struct.unpack_from("<h", raw[1][1], 0)[0] & 0xFFFF
        for pid, (vtype, payload) in raw.items():
            name = _SUMMARY_PIDS.get(pid, f"pid{pid}")
            if vtype == _VT_I4 and len(payload) >= 4:
                out[name] = struct.unpack_from("<i", payload, 0)[0]
            elif vtype == _VT_I2 and len(payload) >= 2:
                out[name] = struct.unpack_from("<h", payload, 0)[0]
            elif vtype == _VT_FILETIME and len(payload) >= 8:
                out[name] = _filetime_to_iso(struct.unpack_from("<Q", payload, 0)[0])
            elif vtype in (_VT_LPSTR, _VT_LPWSTR) and len(payload) >= 4:
                length = struct.unpack_from("<I", payload, 0)[0]
                if vtype == _VT_LPWSTR:
                    text = payload[4 : 4 + length * 2].decode("utf-16-le", "replace")
                else:
                    enc = "cp%d" % codepage if codepage not in (0, 1200) else "cp1252"
                    try:
                        text = payload[4 : 4 + length].decode(enc, "replace")
                    except LookupError:
                        text = payload[4 : 4 + length].decode("latin-1", "replace")
                out[name] = text.rstrip("\x00").strip()
    return out


# --------------------------------------------------------------------------
# 3. Excel BIFF8
# --------------------------------------------------------------------------

_BOF = 0x0809
_EOF = 0x000A
_FILEPASS = 0x002F
_CONTINUE = 0x003C
_CODEPAGE = 0x0042
_BOUNDSHEET = 0x0085
_MULRK = 0x00BD
_MULBLANK = 0x00BE
_XF = 0x00E0
_SST = 0x00FC
_LABELSST = 0x00FD
_FORMAT = 0x041E
_DIMENSIONS = 0x0200
_BLANK = 0x0201
_NUMBER = 0x0203
_LABEL = 0x0204
_BOOLERR = 0x0205
_STRING = 0x0207
_RK = 0x027E
_FORMULA = 0x0006

_ERROR_NAMES = {
    0x00: "#NULL!", 0x07: "#DIV/0!", 0x0F: "#VALUE!", 0x17: "#REF!",
    0x1D: "#NAME?", 0x24: "#NUM!", 0x2A: "#N/A",
}


@dataclass
class Cell:
    row: int
    col: int
    value: object
    kind: str  # "text" | "number" | "bool" | "error" | "blank"
    source: str  # името на BIFF записа, от който идва — за проследимост
    format_code: str | None = None


@dataclass
class Sheet:
    name: str
    hidden: int
    sheet_type: int
    cells: dict[tuple[int, int], Cell] = field(default_factory=dict)
    dimensions: tuple[int, int, int, int] | None = None

    def rows(self) -> list[int]:
        return sorted({r for r, _ in self.cells})

    def row_values(self, row: int) -> dict[int, Cell]:
        return {c: cell for (r, c), cell in self.cells.items() if r == row}


@dataclass
class Workbook:
    biff_version: int
    codepage: int | None
    sheets: list[Sheet]
    shared_strings: list[str]
    sst_declared_unique: int | None = None


def iter_biff_records(buf: bytes) -> Iterator[tuple[int, bytes]]:
    pos = 0
    total = len(buf)
    while pos + 4 <= total:
        rid, length = struct.unpack_from("<HH", buf, pos)
        pos += 4
        yield rid, buf[pos : pos + length]
        pos += length


def _decode_rk(rk: int) -> float:
    if rk & 0x02:
        value = float((rk >> 2) - 0x40000000 if (rk >> 2) & 0x20000000 else rk >> 2)
    else:
        value = struct.unpack("<d", struct.pack("<Q", (rk & 0xFFFFFFFC) << 32))[0]
    return value / 100.0 if rk & 0x01 else value


def _xl_unicode_string(buf: bytes, pos: int, cch_size: int = 2) -> tuple[str, int]:
    """Чете XLUnicodeString в един запис (без CONTINUE)."""
    if cch_size == 2:
        cch = struct.unpack_from("<H", buf, pos)[0]
        pos += 2
    else:
        cch = buf[pos]
        pos += 1
    flags = buf[pos]
    pos += 1
    rich = bool(flags & 0x08)
    phonetic = bool(flags & 0x04)
    wide = bool(flags & 0x01)
    runs = 0
    ext = 0
    if rich:
        runs = struct.unpack_from("<H", buf, pos)[0]
        pos += 2
    if phonetic:
        ext = struct.unpack_from("<I", buf, pos)[0]
        pos += 4
    width = 2 if wide else 1
    raw = buf[pos : pos + cch * width]
    pos += cch * width
    text = raw.decode("utf-16-le" if wide else "latin-1", "replace")
    pos += runs * 4 + ext
    return text, pos


class _SstReader:
    """Чете SST през границите на ``CONTINUE``.

    Всеки ``CONTINUE`` започва с нов байт-флаг, **само когато** прекъсва низ по
    средата на знаците му ([MS-XLS], 2.4.58). Това е мястото, където наивните
    четци се чупят при кирилица.
    """

    def __init__(self, blocks: Sequence[bytes]):
        self.buf = b"".join(blocks)
        self.bounds: set[int] = set()
        pos = 0
        for block in blocks[:-1]:
            pos += len(block)
            self.bounds.add(pos)
        self.pos = 0

    def _next_bound(self) -> int:
        later = [b for b in self.bounds if b > self.pos]
        return min(later) if later else len(self.buf)

    def read_strings(self, count: int) -> list[str]:
        out: list[str] = []
        for _ in range(count):
            if self.pos + 3 > len(self.buf):
                break
            cch = struct.unpack_from("<H", self.buf, self.pos)[0]
            self.pos += 2
            flags = self.buf[self.pos]
            self.pos += 1
            rich = bool(flags & 0x08)
            phonetic = bool(flags & 0x04)
            wide = bool(flags & 0x01)
            runs = struct.unpack_from("<H", self.buf, self.pos)[0] if rich else 0
            self.pos += 2 if rich else 0
            ext = struct.unpack_from("<I", self.buf, self.pos)[0] if phonetic else 0
            self.pos += 4 if phonetic else 0
            out.append(self._read_chars(cch, wide))
            self.pos += runs * 4 + ext
        return out

    def _read_chars(self, cch: int, wide: bool) -> str:
        parts: list[str] = []
        remaining = cch
        while remaining > 0:
            if self.pos in self.bounds:
                flags = self.buf[self.pos]
                self.pos += 1
                wide = bool(flags & 0x01)
            limit = self._next_bound()
            width = 2 if wide else 1
            take = min(remaining, max(0, (limit - self.pos)) // width)
            if take == 0:
                if self.pos >= len(self.buf):
                    break
                self.pos = limit
                continue
            raw = self.buf[self.pos : self.pos + take * width]
            self.pos += take * width
            parts.append(raw.decode("utf-16-le" if wide else "latin-1", "replace"))
            remaining -= take
        return "".join(parts)


def read_biff8_workbook(stream: bytes) -> Workbook:
    """Чете ``Workbook`` поток на Excel BIFF8. Формулите НЕ се смятат."""
    records = list(iter_biff_records(stream))
    if not records or records[0][0] != _BOF:
        raise OleScheduleError("потокът не започва с BOF")
    version = struct.unpack_from("<H", records[0][1], 0)[0]
    if version != 0x0600:
        raise OleScheduleError(
            f"поддържа се само BIFF8 (0x0600); намерено 0x{version:04X}"
        )

    codepage: int | None = None
    boundsheets: list[tuple[int, int, int, str]] = []
    shared: list[str] = []
    sst_unique: int | None = None
    formats: dict[int, str] = {}
    xf_format: list[int] = []

    idx = 0
    total = len(records)
    while idx < total:
        rid, data = records[idx]
        if rid == _EOF:
            idx += 1
            break
        if rid == _FILEPASS:
            raise OleScheduleError("работната книга е защитена с парола (FILEPASS)")
        if rid == _CODEPAGE and len(data) >= 2:
            codepage = struct.unpack_from("<H", data, 0)[0]
        elif rid == _BOUNDSHEET:
            pos = struct.unpack_from("<I", data, 0)[0]
            hidden, sheet_type = data[4], data[5]
            name, _ = _xl_unicode_string(data, 6, cch_size=1)
            boundsheets.append((pos, hidden, sheet_type, name))
        elif rid == _FORMAT and len(data) >= 4:
            ifmt = struct.unpack_from("<H", data, 0)[0]
            text, _ = _xl_unicode_string(data, 2)
            formats[ifmt] = text
        elif rid == _XF and len(data) >= 4:
            xf_format.append(struct.unpack_from("<H", data, 2)[0])
        elif rid == _SST:
            total_refs, unique = struct.unpack_from("<ii", data, 0)
            sst_unique = unique
            blocks = [data[8:]]
            look = idx + 1
            while look < total and records[look][0] == _CONTINUE:
                blocks.append(records[look][1])
                look += 1
            shared = _SstReader(blocks).read_strings(unique)
            idx = look - 1
        idx += 1

    sheets = [Sheet(name=n, hidden=h, sheet_type=t) for _, h, t, n in boundsheets]
    sheet_iter = iter(sheets)
    current: Sheet | None = None
    pending_formula: tuple[int, int, int] | None = None

    def _fmt(ixfe: int) -> str | None:
        if ixfe < len(xf_format):
            return formats.get(xf_format[ixfe])
        return None

    while idx < total:
        rid, data = records[idx]
        idx += 1
        if rid == _BOF:
            sub = struct.unpack_from("<H", data, 2)[0] if len(data) >= 4 else 0
            current = next(sheet_iter, None) if sub == 0x0010 else None
            continue
        if current is None:
            continue
        if rid == _EOF:
            current = None
            continue
        if rid == _DIMENSIONS and len(data) >= 14:
            r0, r1, c0, c1 = struct.unpack_from("<IIHH", data, 0)
            current.dimensions = (r0, r1, c0, c1)
        elif rid == _LABELSST and len(data) >= 10:
            row, col, ixfe, isst = struct.unpack_from("<HHHI", data, 0)
            text = shared[isst] if 0 <= isst < len(shared) else ""
            current.cells[(row, col)] = Cell(row, col, text, "text", "LABELSST", _fmt(ixfe))
        elif rid == _LABEL and len(data) >= 8:
            row, col, ixfe = struct.unpack_from("<3H", data, 0)
            text, _ = _xl_unicode_string(data, 6)
            current.cells[(row, col)] = Cell(row, col, text, "text", "LABEL", _fmt(ixfe))
        elif rid == _RK and len(data) >= 10:
            row, col, ixfe, rk = struct.unpack_from("<3HI", data, 0)
            current.cells[(row, col)] = Cell(
                row, col, _decode_rk(rk), "number", "RK", _fmt(ixfe)
            )
        elif rid == _MULRK and len(data) >= 6:
            row, col_first = struct.unpack_from("<2H", data, 0)
            count = (len(data) - 6) // 6
            for k in range(count):
                ixfe, rk = struct.unpack_from("<HI", data, 4 + k * 6)
                col = col_first + k
                current.cells[(row, col)] = Cell(
                    row, col, _decode_rk(rk), "number", "MULRK", _fmt(ixfe)
                )
        elif rid == _NUMBER and len(data) >= 14:
            row, col, ixfe = struct.unpack_from("<3H", data, 0)
            value = struct.unpack_from("<d", data, 6)[0]
            current.cells[(row, col)] = Cell(row, col, value, "number", "NUMBER", _fmt(ixfe))
        elif rid == _BLANK and len(data) >= 6:
            row, col, ixfe = struct.unpack_from("<3H", data, 0)
            current.cells[(row, col)] = Cell(row, col, None, "blank", "BLANK", _fmt(ixfe))
        elif rid == _MULBLANK and len(data) >= 6:
            row, col_first = struct.unpack_from("<2H", data, 0)
            count = (len(data) - 6) // 2
            for k in range(count):
                ixfe = struct.unpack_from("<H", data, 4 + k * 2)[0]
                col = col_first + k
                current.cells[(row, col)] = Cell(row, col, None, "blank", "MULBLANK", _fmt(ixfe))
        elif rid == _BOOLERR and len(data) >= 8:
            row, col, ixfe = struct.unpack_from("<3H", data, 0)
            raw, is_error = data[6], data[7]
            if is_error:
                current.cells[(row, col)] = Cell(
                    row, col, _ERROR_NAMES.get(raw, f"#ERR{raw}"), "error", "BOOLERR", _fmt(ixfe)
                )
            else:
                current.cells[(row, col)] = Cell(
                    row, col, bool(raw), "bool", "BOOLERR", _fmt(ixfe)
                )
        elif rid == _FORMULA and len(data) >= 20:
            row, col, ixfe = struct.unpack_from("<3H", data, 0)
            result = data[6:14]
            if result[6:8] == b"\xff\xff":
                marker = result[0]
                if marker == 0:  # низ — идва в следващия STRING запис
                    pending_formula = (row, col, ixfe)
                elif marker == 1:
                    current.cells[(row, col)] = Cell(
                        row, col, bool(result[2]), "bool", "FORMULA(кеш)", _fmt(ixfe)
                    )
                elif marker == 2:
                    current.cells[(row, col)] = Cell(
                        row, col, _ERROR_NAMES.get(result[2], f"#ERR{result[2]}"),
                        "error", "FORMULA(кеш)", _fmt(ixfe),
                    )
                else:
                    current.cells[(row, col)] = Cell(
                        row, col, "", "text", "FORMULA(кеш)", _fmt(ixfe)
                    )
            else:
                value = struct.unpack_from("<d", result, 0)[0]
                current.cells[(row, col)] = Cell(
                    row, col, value, "number", "FORMULA(кеш)", _fmt(ixfe)
                )
        elif rid == _STRING and pending_formula is not None:
            row, col, ixfe = pending_formula
            pending_formula = None
            text, _ = _xl_unicode_string(data, 0)
            current.cells[(row, col)] = Cell(
                row, col, text, "text", "FORMULA(кеш)+STRING", _fmt(ixfe)
            )

    return Workbook(
        biff_version=version,
        codepage=codepage,
        sheets=sheets,
        shared_strings=shared,
        sst_declared_unique=sst_unique,
    )


# --------------------------------------------------------------------------
# 4. Свързване DXF → ведомост
# --------------------------------------------------------------------------


@dataclass
class EmbeddedDocument:
    frame: Ole2Frame
    container: str
    root_clsid: str = ""
    root_class: str = ""
    container_version: str = ""
    streams: list[tuple[str, int]] = field(default_factory=list)
    workbook: Workbook | None = None
    summary: dict[str, object] = field(default_factory=dict)
    error: str = ""


def read_embedded_documents(source: Path | bytes) -> list[EmbeddedDocument]:
    """Чете всички вградени документи от DXF. Никога не хвърля за отделен обект."""
    out: list[EmbeddedDocument] = []
    for frame in extract_ole2frames(source):
        kind = frame.container_kind()
        doc = EmbeddedDocument(frame=frame, container=kind)
        if kind != "CFB":
            doc.error = "" if kind == "празен" else f"контейнерът е {kind} — не се чете тук"
            out.append(doc)
            continue
        try:
            cfb = CompoundFile(frame.compound_bytes())
            doc.root_clsid = cfb.root.clsid
            doc.root_class = cfb.root_class()
            doc.container_version = (
                f"CFB {cfb.major_version}.{cfb.minor_version}, "
                f"сектор {cfb.sector_size} B, мини-сектор {cfb.mini_sector_size} B, "
                f"праг {cfb.mini_stream_cutoff} B"
            )
            doc.streams = [
                (e.name, e.size) for e in cfb.entries if e.is_stream
            ]
            summary_entry = cfb.find("\x05SummaryInformation")
            if summary_entry is not None:
                try:
                    doc.summary = read_summary_information(cfb.read(summary_entry))
                except (OleScheduleError, struct.error, IndexError, ValueError):
                    doc.summary = {}
            wb_entry = cfb.find("Workbook") or cfb.find("Book")
            if wb_entry is None:
                doc.error = "няма поток Workbook/Book"
            else:
                doc.workbook = read_biff8_workbook(cfb.read(wb_entry))
        except OleScheduleError as exc:
            doc.error = str(exc)
        except (struct.error, IndexError, ValueError) as exc:  # pragma: no cover
            doc.error = f"повредени данни: {type(exc).__name__}: {exc}"
        out.append(doc)
    return out


# --------------------------------------------------------------------------
# 5. Таблицата
# --------------------------------------------------------------------------


@dataclass
class ScheduleTable:
    """Прочетена ведомост: ред със заглавия + редове със стойности."""

    sheet_name: str
    header_row: int | None
    columns: dict[int, str]
    rows: list[dict[str, object]]


def _as_text(cell: Cell | None) -> str:
    if cell is None or cell.value is None:
        return ""
    if isinstance(cell.value, float) and cell.value.is_integer():
        return str(int(cell.value))
    return str(cell.value).strip()


def detect_schedule_table(sheet: Sheet, min_filled: int = 3) -> ScheduleTable:
    """Намира реда със заглавия и връща редовете под него.

    Кандидат е всеки ред с поне ``min_filled`` текстови клетки. Печели този,
    чиито колони наистина са пълни под него: оценката е броят непразни клетки
    в същите колони под реда. Това отделя заглавния ред на таблицата от
    заглавието на самия лист, което стои над него и няма данни под себе си.
    Нарочно не се търси конкретна дума — заглавията се четат, не се налагат.
    """
    filled_below: dict[int, list[int]] = {}
    for (row, col), cell in sheet.cells.items():
        if _as_text(cell):
            filled_below.setdefault(col, []).append(row)

    header_row: int | None = None
    best_score = -1
    for row in sheet.rows():
        cells = sheet.row_values(row)
        cols = [c for c, cell in cells.items() if cell.kind == "text" and _as_text(cell)]
        if len(cols) < min_filled:
            continue
        score = sum(
            sum(1 for r in filled_below.get(c, ()) if r > row) for c in cols
        )
        if score > best_score:
            best_score = score
            header_row = row

    columns: dict[int, str] = {}
    if header_row is not None:
        for col, cell in sorted(sheet.row_values(header_row).items()):
            text = _as_text(cell)
            if text:
                columns[col] = text

    rows: list[dict[str, object]] = []
    for row in sheet.rows():
        if header_row is not None and row <= header_row:
            continue
        cells = sheet.row_values(row)
        if not any(_as_text(c) for c in cells.values()):
            continue
        record: dict[str, object] = {"_row": row}
        for col, cell in sorted(cells.items()):
            key = columns.get(col, f"col{col}")
            record[key] = cell.value
            record[f"{key}__source"] = cell.source
        rows.append(record)

    return ScheduleTable(
        sheet_name=sheet.name, header_row=header_row, columns=columns, rows=rows
    )


__all__ = [
    "Cell",
    "CompoundFile",
    "DirEntry",
    "EmbeddedDocument",
    "Ole2Frame",
    "OleScheduleError",
    "ScheduleTable",
    "Sheet",
    "Workbook",
    "detect_schedule_table",
    "extract_ole2frames",
    "iter_biff_records",
    "iter_dxf_tags",
    "read_biff8_workbook",
    "read_embedded_documents",
    "read_summary_information",
]
