"""ОБУЧЕНИЕ: извлича правила за прочит и ги записва в постоянното хранилище.

Команда:

    LION_REBAR_DATA_ROOT=C:\\lr_exp\\data python tools/etap2_obuchi.py

Източник: копията на проектите в ``C:\\lr_exp\\proekti``. Заключените семейства
НЕ участват като източник. С ``--izklyuchi-semeystvo`` се изключва още едно
семейство — така се прави честна проверка „научи без него, приложи върху него".

Нищо не се пипа в проектите: чете се само ``working/extracted_entities.json`` и
``working/layer_classification.json``.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

КОРЕН = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(КОРЕН))

from lion_rebar.learning.spec_layer_profile import full_profile  # noqa: E402
from lion_rebar.dxf.models import DxfEntityRecord  # noqa: E402
from lion_rebar.learning.convention_learner import learn  # noqa: E402
from lion_rebar.learning.knowledge_store import KnowledgeStore, store_path  # noqa: E402
from lion_rebar.utils.app_paths import AppPaths  # noqa: E402

ПРОЕКТИ = pathlib.Path(r"C:\lr_exp\proekti")

СЕМЕЙСТВО = {
    "т-1": "т-1", "s-9": "s-9", "s-11": "s-11", "s-8": "s-8", "s-10": "s-10",
    "s-7": "s-7", "s-1": "s-1..s-6/а-1", "d-1": "d-1", "d-2": "d-2",
    "Тест-003": "тест-001/Тест-003", "Тест-002": "Тест-002",
    "тест-004": "тест-004/005", "тест-006": "тест-006",
}
ЗАКЛЮЧЕНИ = {"т-1", "Тест-002", "s-7", "s-1..s-6/а-1"}

ПОЛЕТА = ("handle", "entity_type", "layer", "geometry", "text", "block_name",
          "color_index", "linetype", "raw")


def обекти_на(проект: pathlib.Path) -> list[DxfEntityRecord]:
    п = проект / "working" / "extracted_entities.json"
    if not п.is_file():
        return []
    д = json.loads(п.read_text(encoding="utf-8-sig"))
    редове = д if isinstance(д, list) else (д.get("entities") or [])
    изход = []
    for р in редове:
        if isinstance(р, dict):
            изход.append(DxfEntityRecord(**{и: р.get(и) for и in ПОЛЕТА if и in р}))
    return изход


def роли_по_име(проект: pathlib.Path) -> dict[str, dict]:
    п = проект / "working" / "layer_classification.json"
    if not п.is_file():
        return {}
    д = json.loads(п.read_text(encoding="utf-8-sig"))
    return {
        str(р.get("layer") or ""): {"role": str(р.get("role") or ""),
                                    "confidence": float(р.get("confidence") or 0.0)}
        for р in д.get("layers") or () if isinstance(р, dict)
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Обучение на правила за прочит")
    parser.add_argument("--izklyuchi-semeystvo", action="append", default=[],
                        help="семейство, което да НЕ участва в обучението")
    parser.add_argument("--json", type=pathlib.Path, default=None)
    parser.add_argument("--hranilishte", type=pathlib.Path, default=None,
                        help="друг файл за хранилището (по подразбиране: папката с данни)")
    parser.add_argument("--otnovo", action="store_true",
                        help="започва на чисто; иначе се добавя към съществуващото")
    args = parser.parse_args()

    изключени = set(args.izklyuchi_semeystvo or ())
    път = args.hranilishte or store_path(AppPaths.default())
    if args.otnovo and pathlib.Path(път).is_file():
        # Без това старите правила от предишна схема на подписа остават и могат
        # да се прилагат, макар подписът им вече да се образува различно.
        pathlib.Path(път).unlink()
        print("хранилището е изчистено (--otnovo)")
    store = KnowledgeStore.load(път)
    print("хранилище:", store.path)
    print("преди обучението:", store.summary())
    if изключени:
        print("изключени семейства:", ", ".join(sorted(изключени)))

    наблюдения = []
    начало = time.time()
    for проект in sorted(ПРОЕКТИ.iterdir()):
        if not проект.is_dir():
            continue
        обекти = обекти_на(проект)
        if not обекти:
            continue
        семейство = СЕМЕЙСТВО.get(проект.name, проект.name)
        заключено = семейство in ЗАКЛЮЧЕНИ or семейство in изключени
        т0 = time.time()
        профил = full_profile(обекти)
        наблюдения.append({
            "drawing": проект.name,
            "family": семейство,
            "locked": заключено,
            "profile": профил,
            "layer_roles": роли_по_име(проект),
        })
        print("  %-10s %-18s обекти=%-6d предл=%-3d откази=%-3d %.1f s%s" % (
            проект.name, семейство, len(обекти),
            len(профил.get("proposals") or ()), len(профил.get("refusals") or ()),
            time.time() - т0, "   (ИЗКЛЮЧЕН)" if заключено else ""))

    отчет = learn(наблюдения, store=store)
    записан = store.save()
    отчет["seconds"] = round(time.time() - начало, 1)
    отчет["store_path"] = str(записан)
    отчет["store_bytes"] = записан.stat().st_size
    отчет["excluded_families"] = sorted(изключени)

    print("\n=== НАУЧЕНО")
    print("  източници:", ", ".join(отчет["source_drawings"]))
    print("  изключени (заключени и по избор):", ", ".join(отчет["excluded_locked"]))
    for етикет, ключ in (("ПРИЕТИ", "accepted"), ("КАНДИДАТИ", "candidates"),
                         ("ОТХВЪРЛЕНИ", "rejected")):
        редове = отчет[ключ]
        print("\n  %s (%d):" % (етикет, len(редове)))
        for р in редове:
            print("    %-26s %-22s име=%-12s семейства=%-22s %s" % (
                р["rule_id"], р["role"], р["name_token"][:12],
                ",".join(р["families"])[:22], р["verdict"][:46]))

    print("\n  хранилище след обучението:", store.summary())
    print("  записан:", записан, "(%d байта, %.1f s)" % (отчет["store_bytes"], отчет["seconds"]))

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(отчет, ensure_ascii=False, indent=1),
                             encoding="utf-8")
        print("  отчет:", args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
