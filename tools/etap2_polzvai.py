"""ПОЛЗВАНЕ: зарежда наученото след рестарт и го прилага върху чертеж.

Това е ОТДЕЛЕН процес. Не учи нищо и не пипа хранилището — само го чете. Така
се доказва, че знанието преживява затварянето на програмата.

Команда:

    LION_REBAR_DATA_ROOT=C:\\lr_exp\\data python tools/etap2_polzvai.py тест-004

``LION_REBAR_LEARNING=off`` изключва ползването (режим Б) при абсолютно същия
код — така разликата Б → В е само от хранилището.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time

КОРЕН = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(КОРЕН))

from lion_rebar.learning.spec_layer_profile import full_profile  # noqa: E402
from lion_rebar.dxf.models import DxfEntityRecord  # noqa: E402
from lion_rebar.dxf.recognition_policy import DEFAULT_RECOGNITION_POLICY  # noqa: E402
from lion_rebar.learning.convention_apply import apply_learned, merged_roles  # noqa: E402
from lion_rebar.learning.knowledge_store import KnowledgeStore, store_path  # noqa: E402
from lion_rebar.learning.policy_bridge import (  # noqa: E402
    learning_enabled, policy_with_learned_knowledge, store_fingerprint,
)
from lion_rebar.utils.app_paths import AppPaths  # noqa: E402

ПРОЕКТИ = pathlib.Path(r"C:\lr_exp\proekti")
ПОЛЕТА = ("handle", "entity_type", "layer", "geometry", "text", "block_name",
          "color_index", "linetype", "raw")


def обекти_на(проект: pathlib.Path) -> list[DxfEntityRecord]:
    п = проект / "working" / "extracted_entities.json"
    if not п.is_file():
        return []
    д = json.loads(п.read_text(encoding="utf-8-sig"))
    редове = д if isinstance(д, list) else (д.get("entities") or [])
    return [DxfEntityRecord(**{и: р.get(и) for и in ПОЛЕТА if и in р})
            for р in редове if isinstance(р, dict)]


def main() -> int:
    parser = argparse.ArgumentParser(description="Ползване на наученото")
    parser.add_argument("проект")
    parser.add_argument("--hranilishte", type=pathlib.Path, default=None)
    parser.add_argument("--json", type=pathlib.Path, default=None)
    args = parser.parse_args()

    начало = time.time()
    път = args.hranilishte or store_path(AppPaths.default())
    store = KnowledgeStore.load(път)
    включено = learning_enabled()

    print("=== РЕСТАРТ: нов процес (pid %d)" % os.getpid())
    print("  LION_REBAR_DATA_ROOT = %s" % os.environ.get("LION_REBAR_DATA_ROOT", "(няма)"))
    print("  LION_REBAR_LEARNING  = %s  ->  ползване: %s"
          % (os.environ.get("LION_REBAR_LEARNING", "(няма)"),
             "ВКЛЮЧЕНО" if включено else "ИЗКЛЮЧЕНО"))
    отпечатък = store_fingerprint(store)
    print("  хранилище: %s (%d байта)" % (отпечатък["path"], отпечатък["bytes"]))
    print("  заредени правила: %d, от тях приети: %d"
          % (len(store), len(store.accepted())))
    for правило in store.accepted():
        print("     %-26s %-22s име=%-14s от %s" % (
            правило.rule_id, правило.role,
            правило.signature.get("name_token"), ", ".join(правило.families)))

    проект = ПРОЕКТИ / args.проект
    обекти = обекти_на(проект)
    if not обекти:
        print("няма извлечени обекти за", args.проект)
        return 1
    print("\n=== ЧЕРТЕЖ %s (%d обекта)" % (args.проект, len(обекти)))

    профил = full_profile(обекти, policy=DEFAULT_RECOGNITION_POLICY)
    приложено = apply_learned(профил, store=store, drawing=args.проект,
                              enabled=включено)
    политика, мост = policy_with_learned_knowledge(
        DEFAULT_RECOGNITION_POLICY, обекти, store=store,
        drawing=args.проект, enabled=включено,
    )

    print("  паспортът дава: %d предложения, %d отказа"
          % (len(профил.get("proposals") or ()), len(профил.get("refusals") or ())))
    print("  наученото РЕШИ: %d" % len(приложено.get("resolved") or ()))
    for р in приложено.get("resolved") or ():
        print("     + %-30s %-20s свои примери=%-3d правило=%s (от %s)" % (
            str(р.get("layer"))[:30], р.get("role"), р.get("own_support"),
            р.get("rule_id"), ", ".join(р.get("learned_from") or ())))
        print("       %s" % р.get("why"))
    print("  остават откази: %d" % len(приложено.get("still_refused") or ()))
    for р in (приложено.get("still_refused") or [])[:8]:
        print("     - %-30s %-20s свои=%-3d %s" % (
            str(р.get("layer"))[:30], р.get("role"), р.get("own_support"),
            str(р.get("learned_refusal"))[:60]))

    print("\n  declared_rebar_geometry_layers: %s -> %s"
          % (list(DEFAULT_RECOGNITION_POLICY.declared_rebar_geometry_layers),
             list(политика.declared_rebar_geometry_layers)))
    print("  политиката промени ли се: %s" % мост.get("policy_changed"))

    изход = {
        "проект": args.проект,
        "pid": os.getpid(),
        "learning_enabled": включено,
        "store": отпечатък,
        "profile_summary": профил.get("summary"),
        "applied": приложено,
        "policy_bridge": {к: v for к, v in мост.items() if к != "resolved"},
        "roles_after": merged_roles(профил, приложено),
        "seconds": round(time.time() - начало, 2),
    }
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(изход, ensure_ascii=False, indent=1),
                             encoding="utf-8")
        print("  записан:", args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
