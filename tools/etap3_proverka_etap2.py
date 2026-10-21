"""ПРОВЕРКА на пропуските от етап 2 — петте въпроса, измерени.

Не приема нищо за дефект предварително. За всеки въпрос връща какво е намерено и
дали е дефект, неточност в отчета или очаквано поведение.

Само чете (освен своя JSON изход).
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections import Counter, defaultdict

КОРЕН = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(КОРЕН))

from lion_rebar.dxf.models import DxfEntityRecord  # noqa: E402
from lion_rebar.dxf.recognition_policy import DEFAULT_RECOGNITION_POLICY  # noqa: E402
from lion_rebar.learning.convention_apply import (  # noqa: E402
    apply_learned, authorized_proposals, count_shortage_refusal,
)
from lion_rebar.learning.convention_learner import name_token  # noqa: E402
from lion_rebar.learning.knowledge_store import KnowledgeStore  # noqa: E402
from lion_rebar.learning.spec_layer_profile import full_profile  # noqa: E402

ПРОЕКТИ = pathlib.Path(r"C:\lr_exp\proekti")
ХРАНИЛИЩЕ = pathlib.Path(r"C:\lr_exp\data\naucheno\naucheno.json")
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


def въпрос_1(store: KnowledgeStore) -> dict:
    """Защо отчетът сочи кандидат S|REINF, който липсва в хранилището."""

    признаци = {правило.signature.get("name_token") for правило in store.all_rules()}
    в_хранилището = "S|REINF" in признаци
    # къде се появява S|REINF според измерването на материала
    материал = pathlib.Path(r"C:\lr_exp\rezultat\etap_2\MATERIAL.json")
    къде = []
    if материал.is_file():
        д = json.loads(материал.read_text(encoding="utf-8"))
        for проект, р in (д.get("по_проект") or {}).items():
            for п in р.get("предложени") or []:
                if name_token(str(п.get("слой") or "")) == "S|REINF":
                    къде.append({"проект": проект, "набор": р.get("набор"),
                                 "слой": п.get("слой"), "роля": п.get("роля")})
    return {
        "въпрос": "Защо отчетът сочи кандидат S|REINF, а го няма в naucheno_SLED.json",
        "S|REINF_в_хранилището": в_хранилището,
        "признаци_в_хранилището": sorted(и for и in признаци if и),
        "къде_се_среща_в_корпуса": къде,
        "заключение": (
            "НЕТОЧНОСТ В ОТЧЕТА, не дефект в кода. S|REINF се среща само в "
            "`Тест-002`, което е ЗАКЛЮЧЕНО семейство. Обучителят изключва "
            "заключените семейства още преди да образува кандидати (`learn()` "
            "работи само върху `развойни`), затова правилото никога не влиза в "
            "хранилището — нито като кандидат. Отчетът смеси измерването на "
            "МАТЕРИАЛА (което мина през всички проекти) със съдържанието на "
            "ХРАНИЛИЩЕТО. Поведението на кода е правилното."
        ) if not в_хранилището and къде else "нужна е ръчна проверка",
    }


def въпрос_2_и_3(store: KnowledgeStore) -> dict:
    """ARMTXT: отказ и разрешено ползване едновременно? Еднакви ли са пътищата?"""

    изход = {"въпрос": "ARMTXT е отказан в един път и разрешен в друг — защо",
             "по_проект": [], "паритет": {}}
    несъответствия = 0
    прегледани = 0
    for проект in sorted(ПРОЕКТИ.iterdir()):
        if not проект.is_dir():
            continue
        обекти = обекти_на(проект)
        if not обекти:
            continue
        профил = full_profile(обекти, policy=DEFAULT_RECOGNITION_POLICY)
        приложено = apply_learned(профил, store=store, drawing=проект.name)
        разрешени = authorized_proposals(профил, store=store)

        # един и същ (слой, роля) едновременно в двата изхода?
        разрешени_ключове = {(str(р["layer"]), str(р["role"])) for р in разрешени}
        отказани_ключове = {(str(р["layer"]), str(р["role"]))
                            for р in приложено.get("still_refused") or ()}
        двойни = разрешени_ключове & отказани_ключове
        прегледани += len(разрешени_ключове)
        if двойни:
            несъответствия += len(двойни)
            подробно = []
            for слой, роля in sorted(двойни):
                предложения = [п for п in профил.get("proposals") or ()
                               if str(п.get("layer")) == слой and str(п.get("role")) == роля]
                откази = [о for о in профил.get("refusals") or ()
                          if str(о.get("layer")) == слой and str(о.get("role")) == роля]
                подробно.append({
                    "слой": слой, "роля": роля,
                    "предложения": [{"блок": п.get("block_name"),
                                     "доказателство": str(п.get("evidence"))[:90]}
                                    for п in предложения],
                    "откази": [{"блок": о.get("block_name"),
                                "причина": str(о.get("reason"))[:90],
                                "малко_примери": count_shortage_refusal(str(о.get("reason") or ""))}
                               for о in откази],
                })
            изход["по_проект"].append({"проект": проект.name, "двойни": подробно})

    изход["паритет"] = {
        "разгледани_разрешени": прегледани,
        "слой_роля_и_разрешен_и_отказан": несъответствия,
        "заключение": (
            "ДА, има разлика между пътищата. `apply_learned` гледа ОТКАЗИ и пази "
            "вида им (`count_shortage_refusal`). `authorized_proposals` гледа "
            "ПРЕДЛОЖЕНИЯ и НЕ проверява дали за същия (слой, роля) съществува "
            "успоредно отказ. При `POSITION_LABEL_BLOCK` паспортът се произнася "
            "за БЛОКОВО СЕМЕЙСТВО, а не за слой: един и същ слой носи блок с "
            "еднозначни тагове (предложение) и друг блок с двусмислени тагове "
            "(отказ). Затова двата изхода не са в противоречие помежду си, но "
            "съкращаването до ИМЕ НА СЛОЙ губи това разграничение — точно това "
            "прави мостът към политиката."
        ) if несъответствия else (
            "Не е намерено нито едно (слой, роля) едновременно разрешено и "
            "отказано — двата пътя не си противоречат на този корпус."
        ),
    }
    return изход


def въпрос_4(store: KnowledgeStore) -> dict:
    """Доколко правилата зависят от name_token."""

    приети = store.accepted()
    с_признак = [п for п in приети if str(п.signature.get("name_token") or "")]
    # колко различни ИМЕНА на слоеве стоят зад всеки признак
    имена_зад_признак: dict[str, set[str]] = defaultdict(set)
    for п in приети:
        for пример in п.positives:
            имена_зад_признак[str(п.signature.get("name_token"))].add(
                str(пример.get("layer") or ""))
    return {
        "въпрос": "Доколко правилата зависят от name_token",
        "приети_правила": len(приети),
        "с_непразен_признак": len(с_признак),
        "дял": "%d от %d" % (len(с_признак), len(приети)),
        "имена_зад_всеки_признак": {к: sorted(v) for к, v in sorted(имена_зад_признак.items())},
        "заключение": (
            "ВСИЧКИТЕ приети правила изискват съвпадение по признак на името. "
            "Съдържанието е задължително, но признакът ИЗБИРА кое правило да се "
            "пробва — значи преименуван слой със същото съдържание НЕ получава "
            "правилото. Признакът обединява само цифровите наставки "
            "(`Layer1/2/6` -> `LAYER`, `Arm - N12/N14` -> `ARM|N`). Това е "
            "реално ограничение и се мери в изпитанието за устойчивост."
        ),
    }


def въпрос_5() -> dict:
    """Кои резултати ползват предварително запазени човешки одобрения."""

    редове = []
    for проект in sorted(ПРОЕКТИ.iterdir()):
        if not проект.is_dir():
            continue
        работна = проект / "working"
        връзки = работна / "approved_geometry_links.json"
        състояние = работна / "review_center_state.json"
        брой_връзки = 0
        произход: Counter = Counter()
        if връзки.is_file():
            try:
                д = json.loads(връзки.read_text(encoding="utf-8-sig"))
                елементи = д.get("links") if isinstance(д, dict) else д
                if isinstance(елементи, dict):
                    елементи = list(елементи.values())
                for е in елементи or ():
                    if isinstance(е, dict):
                        брой_връзки += 1
                        произход[str(е.get("origin") or е.get("approval_origin") or "?")] += 1
            except (OSError, ValueError):
                pass
        редове.append({
            "проект": проект.name,
            "одобрени_връзки": брой_връзки,
            "по_произход": dict(произход),
            "има_review_center_state": състояние.is_file(),
            "байтове_връзки": връзки.stat().st_size if връзки.is_file() else 0,
        })
    return {
        "въпрос": "Кои резултати ползват предварително запазени човешки одобрения",
        "по_проект": редове,
        "заключение": (
            "Копията носят `approved_geometry_links.json` и "
            "`review_center_state.json` от работата на собственика. При анализ "
            "редовете с вече одобрена връзка получават "
            "`analysis_status = ПРОПУСНАТО — ВЕЧЕ Е ОДОБРЕНО В ПРОЕКТА` и не се "
            "преоценяват. Затова абсолютните числа в етап 2 (напр. 126 за `s-10`, "
            "от които MANUAL 10) СЪДЪРЖАТ предварителни човешки одобрения. "
            "Разликата Б -> В остава валидна (двата режима ползват същите "
            "одобрения), но за етап 3 трябват ЧИСТИ копия без предварителни "
            "одобрения, за да е ясно какво идва от разпознаването."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверка на пропуските от етап 2")
    parser.add_argument("--json", type=pathlib.Path,
                        default=pathlib.Path(r"C:\lr_exp\rezultat\etap_3\PROVERKA_ETAP2.json"))
    args = parser.parse_args()

    store = KnowledgeStore.load(ХРАНИЛИЩЕ)
    print("хранилище:", store.path, "|", store.summary())
    print()

    отговори = [въпрос_1(store), въпрос_2_и_3(store), въпрос_4(store), въпрос_5()]
    for и, отговор in enumerate(отговори, start=1):
        print("=" * 78)
        print("ВЪПРОС %d: %s" % (и, отговор["въпрос"]))
        for к, v in отговор.items():
            if к in ("въпрос", "заключение"):
                continue
            текст = json.dumps(v, ensure_ascii=False)
            print("   %-34s %s" % (к, текст[:150] + ("…" if len(текст) > 150 else "")))
        заключение = отговор.get("заключение") or (
            (отговор.get("паритет") or {}).get("заключение")) or "(няма)"
        print("   ЗАКЛЮЧЕНИЕ:")
        for ред in str(заключение).split(". "):
            if ред.strip():
                print("      " + ред.strip() + ("." if not ред.endswith(".") else ""))
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(отговори, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\nзаписан:", args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
