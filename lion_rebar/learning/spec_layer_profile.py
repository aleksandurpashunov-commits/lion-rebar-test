"""Още едно правило за прочит: кой слой носи ЛЕГЕНДАТА на позиционните ключове.

Защо е нужно. ``dxf/recognition_policy.py`` изброява три ЛИТЕРАЛНИ имена на
слоеве за този механизъм:

    position_key_circle_layer_aliases = ("0.4",)
    position_key_table_layer_aliases  = ("ARMTXT", "ARM_TXT")
    position_key_spec_layer_aliases   = ("ARMTXT1",)

Паспортът вече предлага първите два по съдържание (``POSITION_KEY`` и
``POSITION_LABEL_BLOCK``). За третия няма роля — и точно той липсваше, когато
измерих режимите върху ``s-10``: без изброените имена проектът пада от 126 на
114 позиции, а наученото връщаше само кръгчетата и таблицата.

Признакът НЕ е измислен тук. Ползва се ``_parse_legend_spec`` — същата функция,
с която ``position_key_resolver`` чете легендата. Така „легенден запис" значи
точно това, което програмата смята за легенден запис, а не нещо близко.

Измерено 27.09.2026 върху осемте проекта с извлечени обекти: легендни записи дава
**само по един слой в чертеж** и това е един и същ шаблон на име —
``ARMTXT1`` в ``d-1`` (2), ``s-10`` (4), ``s-11`` (14), ``тест-004`` (5). Нито
един друг слой в нито един проект не дава дори един. Затова прагът за
самостоятелно предложение е 3, а под него се отказва с броя — както прави
паспортът.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable, Mapping, Sequence

from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.dxf.position_key_resolver import _parse_legend_spec

SPEC_ROLE = "POSITION_KEY_SPEC"

#: Под колко легендни записа слоят не се предлага сам. Измерено: `d-1` има 2 и
#: затова остава отказ, който наученото после вдига — точно случаят, за който
#: съществува пренос на знание.
MIN_SPEC_ENTRIES = 3

#: Кое поле на ``counts`` носи своите примери за тази роля.
SPEC_SUPPORT_FIELD = "legend_specs"


def _текстове(records: Sequence[DxfEntityRecord]) -> list[tuple[DxfEntityRecord, str]]:
    изход = []
    for запис in records:
        вид = str(запис.entity_type or "").upper()
        текст = str(запис.text or "").strip()
        if вид in {"TEXT", "MTEXT"} and текст:
            изход.append((запис, текст))
    return изход


def propose_spec_layers(entities: Iterable[Any]) -> dict[str, list[dict[str, Any]]]:
    """Предлага слоя с легендата по съдържание; отказва при малко записи.

    Изходът е в същата форма като на паспорта (``proposals`` / ``refusals``), за
    да влиза в същия обучител и в същото хранилище — без паралелна система.
    """

    по_слой: dict[str, list[DxfEntityRecord]] = defaultdict(list)
    for запис in entities:
        по_слой[str(getattr(запис, "layer", "") or "0")].append(запис)

    предложения: list[dict[str, Any]] = []
    откази: list[dict[str, Any]] = []

    for слой in sorted(по_слой, key=lambda стойност: стойност.casefold()):
        текстове = _текстове(по_слой[слой])
        ако_легенда = [
            (запис, текст) for запис, текст in текстове
            if _parse_legend_spec(текст) is not None
        ]
        if not ако_легенда:
            continue
        counts = {"texts": len(текстове), SPEC_SUPPORT_FIELD: len(ако_легенда)}
        if len(ако_легенда) < MIN_SPEC_ENTRIES:
            откази.append({
                "target_kind": "LAYER", "layer": слой, "role": SPEC_ROLE,
                "reason": (
                    "само %d легендни записа на този слой — под %d не се предлага"
                    % (len(ако_легенда), MIN_SPEC_ENTRIES)
                ),
                "counts": counts,
            })
            continue
        пример = ако_легенда[0][1]
        предложения.append({
            "target_kind": "LAYER", "layer": слой, "role": SPEC_ROLE,
            "confidence": 0.9,
            "evidence": (
                "%d записа се четат от `_parse_legend_spec` като легенда "
                "(диаметър и дължина), например „%s“"
                % (len(ако_легенда), пример[:40])
            ),
            "sample_handles": [str(запис.handle or "")
                               for запис, _ in ако_легенда[:3]],
            "counts": counts, "details": {},
        })
    return {"proposals": предложения, "refusals": откази}


def full_profile(
    entities: Iterable[Any],
    *,
    policy: Any = None,
) -> dict[str, Any]:
    """Паспортът ПЛЮС правилото за легендата — един профил за обучение и ползване.

    Едно място, за да не се разминат обучителят и мостът към политиката.
    """

    from lion_rebar.dxf.convention_profile import propose_convention_profile
    from lion_rebar.dxf.recognition_policy import DEFAULT_RECOGNITION_POLICY

    записи = list(entities)
    профил = propose_convention_profile(
        записи, policy=policy or DEFAULT_RECOGNITION_POLICY
    )
    допълнение = propose_spec_layers(записи)
    обединен = dict(профил)
    обединен["proposals"] = list(профил.get("proposals") or ()) + допълнение["proposals"]
    обединен["refusals"] = list(профил.get("refusals") or ()) + допълнение["refusals"]
    обобщение = dict(профил.get("summary") or {})
    обобщение["proposal_count"] = len(обединен["proposals"])
    обобщение["refusal_count"] = len(обединен["refusals"])
    обобщение["spec_layer_proposals"] = len(допълнение["proposals"])
    обобщение["spec_layer_refusals"] = len(допълнение["refusals"])
    обединен["summary"] = обобщение
    return обединен
