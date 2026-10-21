"""Кога спорът за една геометрия НЕ може да промени записаното.

``global_conflict`` значи, че един DXF обект е първи кандидат на няколко надписа.
Програмата спира такава позиция, защото не знае чия е геометрията.

Но конфликтът е основание за отказ само когато **изходът зависи от отговора**.
В ``batch_auto_output`` вече има такъв случай: когато позицията изнася собствената
си proxy геометрия, спорният обект не влиза в записа и конфликтът се подминава.

Тук се добавя вторият случай, измерен на 27.09.2026 в `d-2`
(AutoCAD Structural Detailing):

* 17 обекта са поискани от по два надписа;
* при **всичките 17** двата надписа са с ЕДНАКЪВ текст — еднакъв брой, диаметър
  и дължина;
* броят не идва от обекта, а от надписа, и формата излиза еднаква и в двата
  случая — значи **каквото и да реши спорът, в спецификацията влиза едно и също**.

НЕЗАВИСИМА ПРОВЕРКА. Чертежът носи собствената си армировъчна ведомост
(``dxf/asd_schedule.py``). За `d-2` тя дава 5710 броя в 45 реда и съвпада с
извлеченото по **44 от 45** реда — включително за спорните позиции. Тоест
количествата са верни и спорът наистина не ги мени.

КОГА НЕ ВАЖИ. Достатъчно е ЕДИН от претендентите да се различава по диаметър,
обявена дължина, форма или производствени участъци — тогава отговорът има
значение и отказът остава. Пример от `Тест-003`: `4N14x310` и `4N16x310` искат
един обект; различен диаметър значи различно желязо и различно тегло.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable, Mapping, Sequence


def _производствен_ключ(row: Mapping[str, Any]) -> tuple:
    """Какво определя РЕЗУЛТАТА за тази позиция.

    Броят нарочно НЕ влиза: той идва от надписа и е различен за всеки надпис,
    без това да прави спора съществен. Влиза всичко, което зависи от това чия е
    геометрията.
    """

    def _число(стойност: Any) -> float | None:
        try:
            return round(float(стойност), 2)
        except (TypeError, ValueError):
            return None

    сегменти = tuple(
        _число(с) for с in (row.get("canonical_segment_lengths_cm")
                            or row.get("production_segments_cm") or ())
    )
    return (
        _число(row.get("diameter_mm")),
        _число(row.get("declared_length_cm")),
        str(row.get("master_shape_code") or "").strip().upper(),
        сегменти,
        bool(row.get("master_shape_mirrored")),
    )


def _претенденти(rows: Sequence[Mapping[str, Any]]) -> dict[str, list[Mapping[str, Any]]]:
    """Кой обект от кои редове е поискан като ПЪРВИ кандидат."""

    по_обект: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for ред in rows:
        if not isinstance(ред, Mapping):
            continue
        if str(ред.get("annotation_class") or "") != "ACTIONABLE":
            continue
        кандидати = [str(к) for к in (ред.get("candidate_handles") or ()) if str(к)]
        if not кандидати:
            continue
        по_обект[кандидати[0]].append(ред)
    return по_обект


def production_neutral_conflicts(
    rows: Iterable[Mapping[str, Any]],
) -> frozenset[str]:
    """Дръжките на надписите, чийто ownership конфликт не мени изхода.

    Връща имена на НАДПИСИ (``annotation_handle``), не на геометрични обекти,
    защото гейтът работи по позиция.
    """

    редове = [р for р in rows if isinstance(р, Mapping)]
    неутрални: set[str] = set()
    for _обект, претенденти in _претенденти(редове).items():
        ако = [р for р in претенденти if bool(р.get("global_conflict"))]
        if len(претенденти) < 2 or not ако:
            continue
        ключове = {_производствен_ключ(р) for р in претенденти}
        if len(ключове) != 1:
            continue
        единственият = next(iter(ключове))
        # Изроден ключ (без диаметър, дължина и форма) не доказва нищо.
        if единственият[0] is None or единственият[1] is None or not единственият[2]:
            continue
        for р in ако:
            дръжка = str(р.get("annotation_handle") or "")
            if дръжка:
                неутрални.add(дръжка)
    return frozenset(неутрални)


def neutrality_report(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Отчет за проследимост: кои спорове са неутрални и кои не, и защо."""

    редове = [р for р in rows if isinstance(р, Mapping)]
    неутрални, съществени = [], []
    for обект, претенденти in sorted(_претенденти(редове).items()):
        ако = [р for р in претенденти if bool(р.get("global_conflict"))]
        if len(претенденти) < 2 or not ако:
            continue
        ключове = {_производствен_ключ(р) for р in претенденти}
        запис = {
            "geometry_handle": обект,
            "claimants": [str(р.get("annotation_handle") or "") for р in претенденти],
            "texts": [str(р.get("annotation_text") or "") for р in претенденти],
            "distinct_production_keys": len(ключове),
        }
        if len(ключове) == 1:
            единственият = next(iter(ключове))
            if (единственият[0] is None or единственият[1] is None
                    or not единственият[2]):
                запис["verdict"] = "НЕДОСТАТЪЧЕН КЛЮЧ — остава отказ"
                съществени.append(запис)
            else:
                запис["verdict"] = "НЕУТРАЛЕН — изходът е един и същ"
                запис["production_key"] = {
                    "diameter_mm": единственият[0],
                    "declared_length_cm": единственият[1],
                    "shape": единственият[2],
                    "segments": list(единственият[3]),
                }
                неутрални.append(запис)
        else:
            запис["verdict"] = "СЪЩЕСТВЕН — претендентите се различават"
            съществени.append(запис)
    return {
        "schema": "lion-rebar-ownership-neutrality/v1",
        "contested_objects": len(неутрални) + len(съществени),
        "neutral_objects": len(неутрални),
        "material_objects": len(съществени),
        "neutral_positions": sum(len(з["claimants"]) for з in неутрални),
        "neutral": неутрални,
        "material": съществени,
    }
