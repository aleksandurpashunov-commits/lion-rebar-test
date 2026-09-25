"""Форма, начертана в редактора, влиза в ENGINE като чернова.

ЗАЩО. Собственик, 23.09.2026, за OPEN_ENGINE редактора: „много добре ги рисува
и много лесно“ — и веднага след това: „можем ли да направим такъв HTML, който
аз да запазя и от него да вкарваме формите“.

Не трябва нов формат. Бутонът „Свали текущата .json“ вече изнася всичко, което
ENGINE иска, и то ляга едно към едно върху ``ShapeDefinition``::

    {"schema": "lion-rebar-shape-draft/v1",
     "forms": [{"proposed_code": "F022", "name": "Кобилица-2",
                "initial_direction": "НАДЯСНО",
                "segments": [{"index": 1, "code": "L1", "length_cm": 264,
                              "bend_after_deg": 29, "bend_direction": "НАДЯСНО",
                              "dimension_position": "АВТО",
                              "dimension_offset": 6}],
                "total_length_cm": 375,
                "calculation": "264 + 96 + 15 = 375 cm",
                "geometry_key": "E|264@29R|96@90R|15"}]}

КАКВО НЕ ПРАВИ ТОЗИ МОДУЛ. Не публикува и не активира. Ражда ЧЕРНОВА с
контролен тест от размерите, които операторът е начертал; пътят чернова →
тестове → одобрение → публикуване остава непроменен
(``docs/ENGINE_UPGRADE_AND_APPROVAL_BG.md``).

ОТКАЗЪТ Е ПО-ДОБЪР ОТ ДОГАДКАТА. Файл, чийто сбор не отговаря на обявената
дължина, чийто код посяга на заключена форма или чийто участък няма положителна
дължина, се отказва цял — не се внася „каквото става“.
"""
from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from lion_rebar.engine.models import SegmentDefinition, ShapeDefinition

#: Схемата, която редакторът пише в пакета си (``packageFor``).
DRAFT_SCHEMA = "lion-rebar-shape-draft/v1"

#: Редакторът, с който се чертаят формите. Доставя се с продукта и влиза в
#: EXE сборката (``BUILD_EXE.bat``, ``--add-data lion_rebar\resources``).
FORM_EDITOR_FILENAME = "OPEN_ENGINE_v0.3.3_FORM_EDITOR_RADIUS.html"


def form_editor_path() -> Path:
    """Пътят до доставения редактор на форми.

    Досега файлът се доставяше, но нищо не го отваряше — операторът трябваше
    сам да го намери на диска. Пътят се смята като при заключения MASTER
    (``master_engine_v031.master_engine_path``), за да важи и в EXE сборката.
    """

    return Path(__file__).resolve().parent.parent / "resources" / FORM_EDITOR_FILENAME


#: Заключените форми на MASTER v0.3.1 — редакторът ги изброява в ``locked_forms``
#: и сам не ги пипа. Внасяне върху тях е отказ.
LOCKED_FORM_CODES = frozenset(f"F{index:03d}" for index in range(1, 10))

#: Колко може да се разминат сборът на участъците и обявената дължина.
#: Редакторът смята и двете от едни и същи числа, затова допускът е само за
#: закръглянето при записа.
TOTAL_TOLERANCE_CM = 0.011

_CODE_RE = re.compile(r"^F\d{3}$")

_DIRECTIONS = {
    "НАДЯСНО": "RIGHT", "RIGHT": "RIGHT",
    "НАГОРЕ": "UP", "UP": "UP",
    "НАЛЯВО": "LEFT", "LEFT": "LEFT",
    "НАДОЛУ": "DOWN", "DOWN": "DOWN",
}

_TURNS = {
    "НАЛЯВО": "LEFT", "LEFT": "LEFT",
    "НАДЯСНО": "RIGHT", "RIGHT": "RIGHT",
    "НЯМА": "NONE", "NONE": "NONE", "": "NONE",
}

_POSITIONS = {
    "АВТО": "AUTO", "AUTO": "AUTO",
    "ОТГОРЕ": "TOP", "TOP": "TOP",
    "ОТДОЛУ": "BOTTOM", "BOTTOM": "BOTTOM",
    "ВЛЯВО": "LEFT", "LEFT": "LEFT",
    "ВДЯСНО": "RIGHT", "RIGHT": "RIGHT",
    "СКРИЙ": "HIDDEN", "HIDDEN": "HIDDEN",
}


class DraftImportError(RuntimeError):
    """Файлът от редактора не може да стане форма."""


def read_draft_package(payload: Any) -> tuple[dict[str, Any], ...]:
    """Формите във файла, независимо дали е пакет, списък или една чернова.

    ``importDraftPackage`` в самия редактор приема същите три вида, затова тук
    не се въвежда по-тясно правило.
    """

    if isinstance(payload, list):
        кандидати = payload
    elif isinstance(payload, Mapping):
        forms = payload.get("forms")
        кандидати = forms if isinstance(forms, list) else [payload]
    else:
        raise DraftImportError("Файлът не е нито пакет, нито чернова.")
    форми = [item for item in кандидати if isinstance(item, Mapping)]
    if not форми:
        raise DraftImportError("Файлът не съдържа нито една чернова.")
    return tuple(dict(item) for item in форми)


def _positive(value: Any, какво: str) -> float:
    try:
        число = float(value)
    except (TypeError, ValueError) as exc:
        raise DraftImportError(f"{какво}: {value!r} не е число.") from exc
    if not math.isfinite(число) or число <= 0:
        raise DraftImportError(f"{какво} трябва да е положително число, а е {value!r}.")
    return число


def _word(value: Any, таблица: Mapping[str, str], какво: str) -> str:
    дума = str(value or "").strip().upper()
    ако = таблица.get(дума)
    if ако is None:
        raise DraftImportError(f"{какво}: непозната стойност {value!r}.")
    return ако


def editor_segments(form: Mapping[str, Any]) -> tuple[SegmentDefinition, ...]:
    """Участъците на формата, проверени един по един."""

    сурови = form.get("segments")
    if not isinstance(сурови, list) or not сурови:
        raise DraftImportError("Черновата няма нито един участък.")
    последен = len(сурови) - 1
    участъци: list[SegmentDefinition] = []
    for индекс, сегмент in enumerate(сурови):
        if not isinstance(сегмент, Mapping):
            raise DraftImportError(f"Участък {индекс + 1} не е запис.")
        име = f"Участък {индекс + 1}"
        дължина = _positive(сегмент.get("length_cm"), f"{име}: дължината")
        посока = _word(
            сегмент.get("bend_direction") or "НЯМА", _TURNS, f"{име}: посоката на завоя"
        )
        ъгъл = сегмент.get("bend_after_deg")
        if индекс == последен:
            if ъгъл not in (None, "") or посока != "NONE":
                raise DraftImportError(
                    f"{име} е последен и не бива да носи завой след себе си."
                )
            ъгъл, посока = None, "NONE"
        else:
            if ъгъл in (None, ""):
                raise DraftImportError(f"{име}: липсва ъгъл на завоя.")
            ъгъл = float(ъгъл)
            if not math.isfinite(ъгъл) or not 0.0 <= ъгъл <= 180.0:
                raise DraftImportError(f"{име}: ъгълът {ъгъл!r} е извън 0..180°.")
            if посока == "NONE":
                raise DraftImportError(f"{име}: завой без посока.")
        участъци.append(
            SegmentDefinition(
                index=индекс + 1,
                length_code=str(сегмент.get("code") or f"L{индекс + 1}").strip().upper(),
                segment_type="LINE",
                fixed_length_cm=None,
                bend_after_deg=ъгъл,
                bend_direction=посока,
                geometry_method="DIRECT_LENGTH",
                dimension_position=_word(
                    сегмент.get("dimension_position") or "АВТО",
                    _POSITIONS,
                    f"{име}: страната на размера",
                ),
                dimension_offset=float(сегмент.get("dimension_offset") or 6.0),
                radius_cm=_optional_radius(сегмент.get("bend_radius_cm"), име),
                angle_mode="FIXED" if ъгъл is not None else "AUTO",
            )
        )
    return tuple(участъци)


def _optional_radius(value: Any, какво: str) -> float | None:
    """Радиусът на огъвката — днес незадължителен.

    Полето ``РАДИУС_ОГЪВКА_СМ`` съществува в заключените данни, но навсякъде е
    празно и редакторът няма поле за него. Приема се, ако дойде, за да не се
    губи при внасяне; рисунката още не го ползва.
    """

    if value in (None, ""):
        return None
    число = _positive(value, f"{какво}: радиусът")
    return число


def shape_from_editor_form(
    form: Mapping[str, Any],
    *,
    code: str | None = None,
    name: str | None = None,
) -> ShapeDefinition:
    """Чернова форма от една чернова на редактора."""

    код = str(code or form.get("proposed_code") or "").strip().upper()
    if not _CODE_RE.match(код):
        raise DraftImportError(f"Кодът {код or '—'} не е във вида F0NN.")
    if код in LOCKED_FORM_CODES:
        raise DraftImportError(
            f"{код} е заключена MASTER форма и не се пренаписва от редактора."
        )
    име = " ".join(str(name or form.get("name") or "").split())
    if not име:
        raise DraftImportError(f"{код}: формата иска име.")

    участъци = editor_segments(form)
    дължини = [
        float(сегмент.get("length_cm"))
        for сегмент in form["segments"]
    ]
    сбор = math.fsum(дължини)
    обявена = form.get("total_length_cm")
    if обявена not in (None, ""):
        if abs(float(обявена) - сбор) > TOTAL_TOLERANCE_CM:
            raise DraftImportError(
                f"{код}: участъците дават {сбор:g} cm, а обявената дължина е "
                f"{float(обявена):g} cm."
            )

    начало = _word(
        form.get("initial_direction") or "НАДЯСНО",
        _DIRECTIONS,
        f"{код}: началната посока",
    )

    recognition: dict[str, Any] = {
        "imported_from_editor": True,
        "segment_count": len(участъци),
        "bend_count": sum(1 for s in участъци if s.bend_after_deg is not None),
    }
    ключ = str(form.get("geometry_key") or "").strip()
    if ключ:
        recognition["geometry_key"] = ключ
    if bool(form.get("separate_overlapping_ends")):
        recognition["separate_overlapping_ends"] = True

    произход = [f"Внесена от редактора на форми ({DRAFT_SCHEMA})."]
    if ключ:
        произход.append(f"Геометричен ключ: {ключ}.")
    сметка = str(form.get("calculation") or "").strip()
    if сметка:
        произход.append(f"Контролно изчисление: {сметка}.")
    собствена = " ".join(str(form.get("notes") or "").split())
    if собствена:
        произход.append(собствена)

    форма = ShapeDefinition(
        code=код,
        name=име,
        state="DRAFT",
        revision=1,
        locked=False,
        # ``active`` НЕ е лостът за одобрение — лостът е ``state``. Черновият
        # engine не е активният; това е гейтът. Същото е и при раждането на
        # форма от случай (``shape_from_case.build_draft_shape``).
        active=True,
        initial_direction=начало,
        mirror_allowed=True,
        notes=" ".join(произход),
        segments=list(участъци),
        recognition=recognition,
    )

    from lion_rebar.engine.test_runner import build_control_test

    размери: dict[str, float] = {}
    for сегмент, дължина in zip(участъци, дължини):
        размери.setdefault(сегмент.length_code, round(float(дължина), 3))
    try:
        тест = build_control_test(форма, размери, test_id=f"{код}-EDITOR-01")
    except (ValueError, KeyError) as exc:
        raise DraftImportError(
            f"{код}: контролният тест не се построява: {exc}"
        ) from exc
    тест.update({
        "source": "IMPORTED_FROM_EDITOR",
        "segment_lengths_cm": [round(float(v), 3) for v in дължини],
        "production_length_cm": round(сбор, 3),
        "geometry_key": ключ,
    })
    форма.tests = [тест]
    return форма


def shapes_from_editor_package(
    payload: Any,
    *,
    taken_codes: Iterable[str] = (),
) -> tuple[ShapeDefinition, ...]:
    """Всички форми във файла, с проверка за сблъсък на кодовете."""

    заети = {str(code).strip().upper() for code in taken_codes}
    форми: list[ShapeDefinition] = []
    видени: set[str] = set()
    for сурова in read_draft_package(payload):
        форма = shape_from_editor_form(сурова)
        if форма.code in видени:
            raise DraftImportError(f"{форма.code} се среща два пъти в един файл.")
        if форма.code in заети:
            raise DraftImportError(
                f"{форма.code} вече съществува в engine-а. Смени кода в редактора."
            )
        видени.add(форма.code)
        форми.append(форма)
    return tuple(форми)
