"""ДИАГНОЗА: къде се губят човешките решения по пътя до „доказателство".

Повтаря ВСИЧКИ прагове на ``_eligible_evidence`` като водопад — всяко решение
се приписва на ПЪРВОТО условие, което го отсича. Само чете; нищо не се пипа.

Защо е нужно: ако обучението няма доказателства, безсмислено е да се подобрява
съвпадането. Първо се мери откъде идва загубата и дали е нарочна.
"""
from __future__ import annotations

import json
import pathlib
import sys
from collections import Counter

КОРЕН = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(КОРЕН))

from lion_rebar.review import decision_suggestions as ds  # noqa: E402

ПРОЕКТИ = pathlib.Path(r"C:\lr_exp\proekti")


def _стъпка(ред: dict, плик: dict, payload: dict) -> str:
    """Първото условие, което отсича това решение — по реда на кода."""

    # --- _row_is_eligible, разбито на съставките си
    пълнота = ds._mapping(ред.get("field_completeness"))
    има_доказана_геометрия = bool(
        пълнота.get("geometry")
        or (ds._upper(ред.get("reason_code")) in ds._GEOMETRYLESS_REASONS
            and ds._text(ред.get("note")))
    )
    if ds._upper(ред.get("event_type")) != ds._EVENT_TYPE:
        return "A1. не е ENGINEER_RESOLVED"
    if not ред.get("active", True):
        return "A2. решението е оттеглено (active=false)"
    if ред.get("superseded"):
        return "A3. НАДЖИВЯНО от по-късно решение"
    if ред.get("invalidated_by_undo"):
        return "A4. отменено с Undo"
    if ds._upper(ред.get("action_class")) not in ds._ELIGIBLE_ACTIONS:
        return f"A5. действието не носи знание: {ds._upper(ред.get('action_class')) or '(празно)'}"
    if ds._upper(ред.get("reason_code")) == "CONFIRM_CORRECT":
        return "A6. само потвърждение, че е вярно"
    if not ред.get("complete_production_resolution"):
        return "A7. НЕПЪЛНО производствено решение"
    if not има_доказана_геометрия:
        return "A8. няма геометрия и няма годна бележка"

    # --- повторната проверка върху непокътнатото събитие
    действие = ds._upper(payload.get("action_class"))
    геометрия = ds._mapping(payload.get("geometry"))
    причина = ds._upper(payload.get("reason_code"))
    дръжки = ds._string_values(геометрия.get("handles"))
    оригинал = ds._mapping(payload.get("original"))
    контекст_ред = ds._mapping(payload.get("row_context"))
    стари_дръжки = ds._string_values(
        оригинал.get("geometry_handles") or контекст_ред.get("candidate_handles"))
    бележка = ds._text(payload.get("note") or payload.get("operator_note"))
    без_геометрия_ок = bool(причина in ds._GEOMETRYLESS_REASONS and бележка)

    if действие not in ds._ELIGIBLE_ACTIONS:
        return f"Б1. суровото действие не носи знание: {действие or '(празно)'}"
    if not (дръжки or без_геометрия_ок):
        return f"Б2. суровото събитие е без геометрия, причина {причина or '(празна)'}"
    if причина == "CONFIRM_CORRECT":
        return "Б3. суровото е потвърждение"
    if причина == "NO_GEOMETRY" and дръжки:
        return "Б4. NO_GEOMETRY, но има дръжки — противоречие"
    if причина in ds._GEOMETRYLESS_REASONS and дръжки and дръжки != стари_дръжки:
        return "Б5. инженерът е избрал ДРУГА геометрия — дръжките не се пренасят"

    # --- контекст и решение
    самоличност = ds._mapping(плик.get("source_identity"))
    контекст, липсва = ds.strict_context_from_review_row(
        ds._historical_context(ред, payload),
        identity=самоличност,
        engine_version=(ds._text(плик.get("engine_version"))
                        or ds._text(самоличност.get("engine_version"))),
    )
    if контекст is None:
        return "В1. строгият контекст е непълен: " + ", ".join(липсва)
    if ds._resolution(ред, payload) is None:
        return "В2. производственото решение не се възстановява"
    return "ГОДНО"


def _събери(проект: pathlib.Path):
    try:
        доставени = ds._delivered_project_events(проект)
    except (OSError, ValueError):
        return None
    слети, _ = ds._merge_events(доставени)
    жизнен, кандидати = [], {}
    for стойност in слети.values():
        плик = ds._mapping(ds._mapping(стойност.event.get("payload")).get("human_decision"))
        стабилен = ds._text(стойност.event.get("stable_event_id"))
        if (not ds.is_supported_decision_contract(плик.get("schema"))
                or ds._text(плик.get("event_id")) != стабилен
                or ds._upper(плик.get("event_type")) not in ds.DECISION_EVENT_TYPES):
            continue
        жизнен.append(стойност)
        if (ds._upper(плик.get("event_type")) == ds._EVENT_TYPE
                and bool(плик.get("active", True))
                and стойност.delivery_sources & {"SESSION_DB_ENDED", "PROJECT_OUTBOX_DELIVERED"}):
            кандидати[стабилен] = стойност
    редове = ds._decision_rows([и.event for и in жизнен])
    return редове, кандидати


def main() -> int:
    общо = Counter()
    по_проект = {}
    примери: dict[str, list] = {}
    for проект in sorted(ПРОЕКТИ.iterdir()):
        if not проект.is_dir():
            continue
        данни = _събери(проект)
        if данни is None:
            continue
        редове, кандидати = данни
        if not кандидати:
            continue
        броячи = Counter()
        for ред in редове:
            доставено = кандидати.get(ds._text(ред.get("event_id")))
            if доставено is None:
                continue
            плик = ds._mapping(ds._mapping(доставено.event.get("payload")).get("human_decision"))
            payload = ds._mapping(плик.get("payload"))
            стъпка = _стъпка(ред, плик, payload)
            броячи[стъпка] += 1
            общо[стъпка] += 1
            примери.setdefault(стъпка, [])
            if len(примери[стъпка]) < 3:
                примери[стъпка].append({
                    "проект": проект.name,
                    "надпис": ds._text(payload.get("annotation_text"))
                              or ds._text(ds._mapping(payload.get("row_context")).get("annotation_text")),
                    "действие": ds._upper(payload.get("action_class")),
                    "причина": ds._upper(payload.get("reason_code")),
                    "бележка": ds._text(payload.get("note") or payload.get("operator_note"))[:60],
                })
        по_проект[проект.name] = dict(броячи)
        print(f"{проект.name:10s} " + "  ".join(
            f"{к.split('.')[0]}={v}" for к, v in sorted(броячи.items())))

    print("\n=== КЪДЕ СЕ ГУБЯТ (всички проекти, първото отсичащо условие)")
    за_всички = sum(общо.values())
    for стъпка, брой in sorted(общо.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"  {брой:4d}  {брой*100.0/за_всички:5.1f}%  {стъпка}")
    print(f"  {за_всички:4d}  100.0%  ВСИЧКИ доставени и живи")

    изход = pathlib.Path(r"C:\lr_exp\rezultat\CENZUS_DOKAZATELSTVA.json")
    изход.parent.mkdir(parents=True, exist_ok=True)
    изход.write_text(json.dumps(
        {"общо": dict(общо), "по_проект": по_проект, "примери": примери},
        ensure_ascii=False, indent=1), encoding="utf-8")
    print("\nзаписан:", изход)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
