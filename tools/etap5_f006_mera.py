# -*- coding: utf-8 -*-
"""Измерва потвърждението на стремената по собствени коти — трите категории.

Заданието на етап 5, раздел 4, иска РАЗДЕЛНО:

1. **новоразрешени** — позиции, които преди НЕ минаваха портата, а сега минават;
2. **вече излизаха, но вече не искат тази ръчна проверка** — присъдата на
   matcher-а пада от MANUAL, а портата вече ги пускаше;
3. **останали по други, независими причини** — с изброените причини.

Сравняват се пакетните отчети (`*_geometry_batch.json`) на ДВА прогона върху
същите чисти копия: „преди" и „сега". Ключът е handle-ът на надписа.

Само чете.
"""
from __future__ import annotations

import argparse
import json
import pathlib
from collections import Counter

ПРОГОНИ = pathlib.Path(r"C:\lr_exp\data\full_path_runs")


def _отчет_за_копие(копие: str) -> pathlib.Path | None:
    """Пакетният отчет в дадено копие на проекта."""

    папка = pathlib.Path(копие) / "output" / "04_REVIEW_REPORTS"
    ако = sorted(папка.rglob("*_geometry_batch.json")) if папка.is_dir() else []
    return ако[-1] if ако else None


def _редове(обобщение: pathlib.Path) -> tuple[str, dict[str, dict], dict]:
    """Проект, редовете му по handle на надписа и обобщението на портата."""

    д = json.loads(обобщение.read_text(encoding="utf-8"))
    име = str(д.get("project") or обобщение.stem)
    копие = str(д.get("copy") or "")
    отчет = _отчет_за_копие(копие) if копие else None
    редове: dict[str, dict] = {}
    if отчет is not None:
        пакет = json.loads(отчет.read_text(encoding="utf-8"))
        for ред in пакет.get("rows") or ():
            ръчка = str(ред.get("annotation_handle") or "")
            if ръчка:
                редове[ръчка] = ред
    порта = д.get("gate") or {}
    return име, редове, порта


def _кука(ред: dict) -> bool:
    коди = {str(к).upper() for к in (ред.get("review_reason_codes") or ())}
    return "HOOKED_CLOSURE" in коди


def _потвърден(ред: dict) -> bool:
    """Личи от бележката, която проходът добавя. Самото поле не влиза в отчета."""

    return any("F006_OWNED_DIMENSIONS_CONFIRM" in str(б)
               for б in (ред.get("candidate_notes") or ()))


def _разминаване(ред: dict) -> bool:
    return any("F006_OWNED_DIMENSIONS_DISAGREE" in str(б)
               for б in (ред.get("candidate_notes") or ()))


def _спрени(порта: dict) -> set[str]:
    return {str(р.get("handle") or "")
            for р in порта.get("blocked_positions") or ()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--predi", required=True, type=pathlib.Path,
                        help="папка с обобщенията на прогона ПРЕДИ")
    parser.add_argument("--sega", required=True, type=pathlib.Path,
                        help="папка с обобщенията на прогона СЕГА")
    parser.add_argument("--izhod", type=pathlib.Path,
                        default=pathlib.Path(r"C:\lr_exp\rezultat\etap_5\F006_MERA.json"))
    args = parser.parse_args()

    общо = {"новоразрешени": [], "без_тази_проверка": [], "останали": [],
            "разминавания": [], "нови_спирания": []}
    по_проекти: dict[str, dict] = {}

    for файл in sorted(args.sega.glob("*.json")):
        if файл.name.startswith("_"):
            continue
        предишен = args.predi / файл.name
        if not предишен.is_file():
            print("%-10s няма прогон ПРЕДИ" % файл.stem)
            continue
        име, сега_редове, сега_порта = _редове(файл)
        _, преди_редове, преди_порта = _редове(предишен)
        if not сега_редове or not преди_редове:
            print("%-10s няма пакетен отчет (копието изтрито?)" % име)
            continue

        преди_спрени = _спрени(преди_порта)
        сега_спрени = _спрени(сега_порта)
        свои = {"куки": 0, "потвърдени": 0, "новоразрешени": 0,
                "без_тази_проверка": 0, "останали": 0, "разминавания": 0,
                "причини_на_останалите": Counter()}

        for ръчка, ред in сега_редове.items():
            стар = преди_редове.get(ръчка)
            if стар is None or not _кука(стар):
                continue
            свои["куки"] += 1
            if _разминаване(ред):
                свои["разминавания"] += 1
                общо["разминавания"].append((име, ръчка))
            ако_потвърден = _потвърден(ред)
            if ако_потвърден:
                свои["потвърдени"] += 1
            беше_спряна = ръчка in преди_спрени
            е_спряна = ръчка in сега_спрени
            стара_присъда = str(стар.get("matcher_disposition") or "")
            нова_присъда = str(ред.get("matcher_disposition") or "")

            if беше_спряна and not е_спряна:
                свои["новоразрешени"] += 1
                общо["новоразрешени"].append(
                    (име, ръчка, str(ред.get("annotation_text") or "")[:40]))
            elif (not беше_спряна and ако_потвърден
                  and стара_присъда == "MANUAL" and нова_присъда != "MANUAL"):
                свои["без_тази_проверка"] += 1
                общо["без_тази_проверка"].append((име, ръчка, нова_присъда))
            elif нова_присъда == "MANUAL":
                свои["останали"] += 1
                причини = [к for к in (ред.get("matcher_blocker_codes") or ())]
                свои["причини_на_останалите"][
                    ",".join(sorted(str(к) for к in причини)) or "?"] += 1
                общо["останали"].append((име, ръчка))
            if not беше_спряна and е_спряна:
                общо["нови_спирания"].append((име, ръчка))

        свои["причини_на_останалите"] = dict(свои["причини_на_останалите"])
        по_проекти[име] = свои
        print("%-10s куки=%-4d потвърдени=%-4d новоразрешени=%-3d "
              "без_тази_проверка=%-4d останали=%-4d разминавания=%d"
              % (име, свои["куки"], свои["потвърдени"], свои["новоразрешени"],
                 свои["без_тази_проверка"], свои["останали"],
                 свои["разминавания"]))

    print()
    for име, списък in общо.items():
        print("%-20s %d" % (име, len(списък)))
    args.izhod.write_text(
        json.dumps({"по_проекти": по_проекти,
                    "общо": {к: len(в) for к, в in общо.items()},
                    "подробно": {к: в[:200] for к, в in общо.items()}},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    print("записано:", args.izhod)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
