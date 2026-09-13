"""Замразена конфигурация на export-а — категория и лист за всяка рамка.

ПРОБЛЕМЪТ
---------
Финалният контрол замразява DXF, batch отчет и Ledger, но НЕ и решението
коя рамка в кой лист отива. Между замразяването и export-а операторът
може да смени категорията в диалога — Excel-ът излиза по новото, а
манифестът твърди PRODUCTION VERIFIED срещу стария freeze.

Реален случай (независим одит на rc21): рамка „вертикали“ се
експортираше като „долна“, а манифестът записваше VERTICALS.

РЕШЕНИЕТО
---------
Конфигурацията се замразява ЗАЕДНО с останалите артефакти и се проверява
на три места:

1. при замразяване -- записва се в freeze манифеста;
2. при генериране на Excel -- разминаване е ОТКАЗ, не предупреждение;
3. при верификация на манифеста -- реалният trace се сравнява с
   замразеното съответствие.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

CONFIG_NAME = "final_control_export_config.json"
SCHEMA = "lion-rebar-export-config/v1"


class ExportConfigError(RuntimeError):
    """Конфигурацията на export-а не съответства на замразената."""


def _text(value: Any) -> str:
    return " ".join(str(value if value is not None else "").split()).strip()


@dataclass(frozen=True)
class FrozenFrameMapping:
    """Замразено съответствие рамка -> категория и лист."""

    frame_index: int
    marker_handle: str
    title_text: str
    category_code: str
    sheet_name: str

    def to_dict(self) -> dict:
        return {
            "frame_index": self.frame_index,
            "marker_handle": self.marker_handle,
            "title_text": self.title_text,
            "category_code": self.category_code,
            "sheet_name": self.sheet_name,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "FrozenFrameMapping":
        return cls(
            frame_index=int(payload.get("frame_index") or 0),
            marker_handle=_text(payload.get("marker_handle")),
            title_text=_text(payload.get("title_text")),
            category_code=_text(payload.get("category_code")).upper(),
            sheet_name=_text(payload.get("sheet_name")),
        )


def mapping_from_frames(frames: Iterable[Any]) -> list[FrozenFrameMapping]:
    """Съответствието, каквото е в момента на замразяване."""

    result: list[FrozenFrameMapping] = []
    for frame in frames:
        result.append(FrozenFrameMapping(
            frame_index=int(getattr(frame, "index", 0) or 0),
            marker_handle=_text(getattr(frame, "marker_handle", "")),
            title_text=_text(getattr(frame, "title_text", "")),
            category_code=_text(getattr(frame, "category_code", "")).upper(),
            sheet_name=_text(getattr(frame, "suggested_sheet_name", "")),
        ))
    return sorted(result, key=lambda item: item.frame_index)


def mapping_from_configs(configs: Iterable[Any],
                         frames: Iterable[Any] = ()) -> list[FrozenFrameMapping]:
    """Съответствието, каквото операторът е избрал в диалога."""

    by_index = {
        int(getattr(frame, "index", 0) or 0): frame for frame in frames
    }
    result: list[FrozenFrameMapping] = []
    for config in configs:
        index = int(getattr(config, "frame_index", 0) or 0)
        frame = by_index.get(index)
        result.append(FrozenFrameMapping(
            frame_index=index,
            marker_handle=_text(getattr(frame, "marker_handle", "")) if frame else "",
            title_text=_text(getattr(config, "title_text", "")),
            category_code=_text(getattr(config, "category_code", "")).upper(),
            sheet_name=_text(getattr(config, "sheet_name", "")),
        ))
    return sorted(result, key=lambda item: item.frame_index)


def config_fingerprint(mapping: Sequence[FrozenFrameMapping]) -> str:
    """Стабилен хеш на съответствието — влиза в манифеста."""

    payload = json.dumps([item.to_dict() for item in mapping],
                         ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def write_frozen_config(project_root: Path | str,
                        mapping: Sequence[FrozenFrameMapping]) -> Path:
    """Записва замразената конфигурация до freeze манифеста."""

    directory = Path(project_root) / "working"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / CONFIG_NAME
    payload = {
        "schema": SCHEMA,
        "fingerprint": config_fingerprint(mapping),
        "frames": [item.to_dict() for item in mapping],
    }
    temporary = target.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8")
    temporary.replace(target)
    return target


def read_frozen_config(project_root: Path | str) -> list[FrozenFrameMapping]:
    """Чете и криптографски проверява замразената конфигурация.

    Липсващ файл се представя като празен списък, за да могат диагностичните
    екрани да различат стар проект от повреден файл. Невалидна схема,
    структура или fingerprint обаче е грешка, а не „няма конфигурация“.
    """

    path = Path(project_root) / "working" / CONFIG_NAME
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ExportConfigError(
            f"Замразената export конфигурация не може да бъде прочетена: {exc}"
        ) from exc
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
        raise ExportConfigError(
            f"Неподдържана схема на замразената export конфигурация: "
            f"{payload.get('schema') if isinstance(payload, dict) else type(payload).__name__!r}."
        )
    frames = payload.get("frames") if isinstance(payload, dict) else None
    if not isinstance(frames, list):
        raise ExportConfigError("Замразената export конфигурация няма списък frames.")
    if any(not isinstance(item, Mapping) for item in frames):
        raise ExportConfigError("Замразената export конфигурация съдържа невалидна рамка.")
    mapping = [FrozenFrameMapping.from_dict(item) for item in frames]
    expected = _text(payload.get("fingerprint")).lower()
    actual = config_fingerprint(mapping)
    if not expected or expected != actual:
        raise ExportConfigError(
            "EXPORT_CONFIG_FINGERPRINT_MISMATCH: замразената export "
            "конфигурация е променена или повредена."
        )
    return mapping


def compare_mappings(frozen: Sequence[FrozenFrameMapping],
                     current: Sequence[FrozenFrameMapping]) -> list[str]:
    """Разлики между замразеното и текущото. Празен списък = съвпадат."""

    problems: list[str] = []
    frozen_by_index = {item.frame_index: item for item in frozen}
    current_by_index = {item.frame_index: item for item in current}

    for index in sorted(set(frozen_by_index) - set(current_by_index)):
        problems.append(
            f"Рамка {index} е замразена, но липсва в текущата конфигурация.")
    for index in sorted(set(current_by_index) - set(frozen_by_index)):
        problems.append(
            f"Рамка {index} присъства в конфигурацията, но не е замразена.")
    for index in sorted(set(frozen_by_index) & set(current_by_index)):
        before, after = frozen_by_index[index], current_by_index[index]
        if before.category_code != after.category_code:
            problems.append(
                f"Рамка {index} „{after.title_text or before.title_text}“: "
                f"замразена категория {before.category_code}, "
                f"конфигурацията иска {after.category_code}.")
        if before.sheet_name != after.sheet_name:
            problems.append(
                f"Рамка {index}: замразен лист „{before.sheet_name}“, "
                f"конфигурацията иска „{after.sheet_name}“.")
    return problems


def assert_config_matches_freeze(project_root: Path | str,
                                 configs: Iterable[Any],
                                 frames: Iterable[Any] = ()) -> None:
    """Отказва export при липса, повреда или разминаване с freeze-а.

    Стар проект без този артефакт трябва да пусне Финален контрол наново.
    Това е по-безопасно от compatibility fail-open в production пътя.
    """

    config_path = Path(project_root) / "working" / CONFIG_NAME
    if not config_path.is_file():
        raise ExportConfigError(
            "Липсва замразена export конфигурация. Пусни Финален контрол наново."
        )
    frozen = read_frozen_config(project_root)
    problems = compare_mappings(frozen, mapping_from_configs(configs, frames))
    if problems:
        raise ExportConfigError(
            "Конфигурацията на export-а се различава от замразената при "
            "Финалния контрол:\n  - " + "\n  - ".join(problems) +
            "\n\nПусни Финален контрол наново или върни конфигурацията.")


def verify_trace_against_freeze(project_root: Path | str,
                                trace: Mapping[str, Any]) -> list[str]:
    """Сравнява РЕАЛНИЯ Excel trace със замразеното съответствие.

    Проверява това, което е излязло, а не това, което е поискано.
    """

    config_path = Path(project_root) / "working" / CONFIG_NAME
    if not config_path.is_file():
        return ["EXPORT_CONFIG_MISSING: липсва замразена export конфигурация."]
    try:
        frozen = read_frozen_config(project_root)
    except ExportConfigError as exc:
        return [f"EXPORT_CONFIG_INVALID: {exc}"]
    if not isinstance(trace, Mapping):
        return ["EXCEL_TRACE_INVALID: trace файлът не е JSON обект."]
    if trace.get("schema") not in {
        "lion-rebar-excel-traceability/v2",
        "lion-rebar-excel-traceability/v3",
    }:
        return [f"EXCEL_TRACE_UNSUPPORTED_SCHEMA: {trace.get('schema')!r}"]
    expected = {item.frame_index: item for item in frozen}
    problems: list[str] = []
    sheets = trace.get("frames")
    if not isinstance(sheets, list):
        return ["EXCEL_TRACE_FRAMES_MISSING: trace файлът няма списък frames."]
    actual_indices: set[int] = set()
    for sheet in sheets:
        if not isinstance(sheet, Mapping):
            problems.append("EXCEL_TRACE_FRAME_INVALID: невалиден запис за рамка.")
            continue
        index = int(sheet.get("frame_index") or 0)
        if index <= 0:
            problems.append("EXCEL_TRACE_FRAME_INVALID: липсва валиден frame_index.")
            continue
        if index in actual_indices:
            problems.append(f"EXCEL_TRACE_FRAME_DUPLICATE: рамка {index} е записана повторно.")
            continue
        actual_indices.add(index)
        item = expected.get(index)
        if item is None:
            problems.append(
                f"EXCEL_TRACE_UNFROZEN_FRAME: рамка {index} не присъства във freeze-а."
            )
            continue
        actual_name = _text(sheet.get("sheet_name") or sheet.get("title"))
        actual_code = _text(sheet.get("category_code")).upper()
        if actual_name and actual_name != item.sheet_name:
            problems.append(
                f"EXPORT_SHEET_MISMATCH: рамка {index} е изнесена в лист "
                f"„{actual_name}“, а замразеното съответствие е "
                f"„{item.sheet_name}“.")
        if actual_code and actual_code != item.category_code:
            problems.append(
                f"EXPORT_CATEGORY_MISMATCH: рамка {index} е изнесена като "
                f"{actual_code}, а замразената категория е "
                f"{item.category_code}.")
        if not actual_name:
            problems.append(f"EXCEL_TRACE_SHEET_MISSING: рамка {index} няма име на лист.")
        if not actual_code:
            problems.append(f"EXCEL_TRACE_CATEGORY_MISSING: рамка {index} няма категория.")
    for index in sorted(set(expected) - actual_indices):
        problems.append(f"EXCEL_TRACE_FRAME_MISSING: замразената рамка {index} липсва в trace-а.")
    return problems
