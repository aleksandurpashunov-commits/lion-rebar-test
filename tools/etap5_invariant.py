# -*- coding: utf-8 -*-
"""Инвариантът: потвърждението добавя доказателство, но НЕ мени числа.

Сравнява ред по ред двата прогона по производствените полета. Всяка разлика е
провал на инварианта от CLAUDE.md („експортът не променя форми, размери, бройки
и одобрения"), затова се изброява, а не се обобщава.

Отделно показва позициите с РАЗМИНАВАНЕ между котите на проектанта и измерената
геометрия — те са находка за оператора, не дефект.

Само чете.
"""
from __future__ import annotations

import argparse
import json
import pathlib

#: Полетата, които определят какво излиза в Excel и в машинния изход.
ПРОИЗВОДСТВЕНИ = (
    "master_shape_code",
    "production_segments_cm",
    "canonical_segment_lengths_cm",
    "canonical_turn_angles_deg",
    "declared_length_cm",
    "diameter_mm",
    "annotation_count",
    "batch_status",
    "production_export_blocked",
    "f006_production_status",
)


def _отчет(копие: str) -> pathlib.Path | None:
    папка = pathlib.Path(копие) / "output" / "04_REVIEW_REPORTS"
    ако = sorted(папка.rglob("*_geometry_batch.json")) if папка.is_dir() else []
    return ако[-1] if ако else None


def _редове(обобщение: pathlib.Path) -> tuple[str, dict[str, dict]]:
    д = json.loads(обобщение.read_text(encoding="utf-8"))
    проект = д.get("project")
    име = str(проект.get("name") if isinstance(проект, dict) else проект
              or обобщение.stem)
    отчет = _отчет(str(д.get("copy") or "")) if д.get("copy") else None
    редове: dict[str, dict] = {}
    if отчет is not None:
        for ред in json.loads(отчет.read_text(encoding="utf-8")).get("rows") or ():
            ръчка = str(ред.get("annotation_handle") or "")
            if ръчка:
                редове[ръчка] = ред
    return име, редове


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--predi", required=True, type=pathlib.Path)
    parser.add_argument("--sega", required=True, type=pathlib.Path)
    args = parser.parse_args()

    нарушения: list[tuple] = []
    разминавания: list[dict] = []
    проверени = 0
    присъди: dict[str, int] = {}

    for файл in sorted(args.sega.glob("*.json")):
        if файл.name.startswith("_"):
            continue
        предишен = args.predi / файл.name
        if not предишен.is_file():
            continue
        име, сега = _редове(файл)
        _, преди = _редове(предишен)
        if not сега or not преди:
            print("%-10s няма отчет" % име)
            continue
        for ръчка, ред in сега.items():
            стар = преди.get(ръчка)
            if стар is None:
                нарушения.append((име, ръчка, "нов ред", None, None))
                continue
            проверени += 1
            for поле in ПРОИЗВОДСТВЕНИ:
                if ред.get(поле) != стар.get(поле):
                    нарушения.append((име, ръчка, поле,
                                      стар.get(поле), ред.get(поле)))
            стара = str(стар.get("matcher_disposition") or "")
            нова = str(ред.get("matcher_disposition") or "")
            if стара != нова:
                присъди["%s -> %s" % (стара, нова)] = присъди.get(
                    "%s -> %s" % (стара, нова), 0) + 1
            for б in ред.get("candidate_notes") or ():
                if "F006_OWNED_DIMENSIONS_DISAGREE" in str(б):
                    разминавания.append({
                        "проект": име, "надпис": ръчка,
                        "текст": str(ред.get("annotation_text") or "")[:40],
                        "обявена": ред.get("declared_length_cm"),
                        "кандидат": ред.get("candidate_handles"),
                        "бележка": str(б),
                    })
        for ръчка in set(преди) - set(сега):
            нарушения.append((име, ръчка, "изчезнал ред", None, None))

    print("проверени редове:", проверени)
    print("промени в производствените полета:", len(нарушения))
    for н in нарушения[:40]:
        print("   !", н)
    print("\nпромени в присъдата на matcher-а:")
    for к, в in sorted(присъди.items(), key=lambda кв: -кв[1]):
        print("   %-28s %d" % (к, в))
    print("\nразминавания между котите и геометрията:", len(разминавания))
    for р in разминавания:
        print("   %s / %s  %s  обявена=%s  кандидат=%s"
              % (р["проект"], р["надпис"], р["текст"], р["обявена"], р["кандидат"]))
        print("      ", р["бележка"])
    pathlib.Path(r"C:\lr_exp\rezultat\etap_5\INVARIANT.json").write_text(
        json.dumps({"проверени": проверени,
                    "нарушения": [list(н) for н in нарушения],
                    "присъди": присъди,
                    "разминавания": разминавания},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    return 0 if not нарушения else 1


if __name__ == "__main__":
    raise SystemExit(main())
