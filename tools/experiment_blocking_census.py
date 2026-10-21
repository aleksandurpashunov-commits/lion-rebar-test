"""ЕКСПЕРИМЕНТ 2: кое спира позициите — по позиции, не по кодове.

Чете ЗАПИСАНИТЕ отчети на ``full_path`` (нищо не се пуска наново) и подрежда
причините, по които позиция не минава. Взима се НАЙ-НОВИЯТ прогон за всеки
проект.

Три различни мерки, защото дават различни числа и смесването им е измамно:

  ПОРТА          — ``gate.approved`` (``build_batch_export_context``); мярката
                   на CLAUDE.md;
  ФИНАЛЕН КОНТРОЛ— ``final_control.approved_after``; с него излиза Excel;
  ЕКСПОРТ        — стигнал ли е до PRODUCTION VERIFIED.

Знаменателят е ВСИЧКИ ACTIONABLE позиции. Нищо не се изключва от него.
Една позиция се брои ЕДИН път, дори да има няколко причини; затова сборът по
причини е по-голям от броя блокирани и двата се показват отделно.
Само чете.
"""
from __future__ import annotations

import argparse
import json
import pathlib
from collections import Counter

ПРОГОНИ = pathlib.Path(r"C:\lr_exp\data\full_path_runs")

СЕМЕЙСТВО = {
    "т-1": "т-1", "s-9": "s-9", "s-11": "s-11", "s-8": "s-8", "s-10": "s-10",
    "s-7": "s-7", "s-1": "s-1..s-6/а-1", "d-1": "d-1", "d-2": "d-2",
    "Тест-003": "тест-001/Тест-003", "Тест-002": "Тест-002",
    "тест-004": "тест-004/005", "тест-006": "тест-006",
}
ЗАКЛЮЧЕНИ = {"т-1", "Тест-002", "s-7", "s-1..s-6/а-1"}


def _най_нови() -> dict[str, tuple[pathlib.Path, dict]]:
    """Най-новото обобщение за всеки проект, от всички папки с прогони."""

    намерени: dict[str, tuple[float, pathlib.Path, dict]] = {}
    for п in ПРОГОНИ.rglob("*.summary.json"):
        try:
            д = json.loads(п.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        име = str((д.get("project") or {}).get("name") or "")
        if not име:
            име = п.name.split("_2026")[0]
        време = п.stat().st_mtime
        ако = намерени.get(име)
        if ако is None or време > ако[0]:
            намерени[име] = (време, п, д)
    return {и: (п, д) for и, (_, п, д) in намерени.items()}


def main() -> int:
    parser = argparse.ArgumentParser(description="Кое спира позициите, по позиции")
    parser.add_argument("--json", type=pathlib.Path, default=None)
    args = parser.parse_args()

    обобщения = _най_нови()
    print("%-10s %-4s %7s %7s %7s %7s  %-8s %s" % (
        "проект", "набор", "ACTION", "ПОРТА", "ФИН.К", "ЕКСПОРТ", "спиране", "прогон"))
    print("-" * 104)

    по_позиции: Counter = Counter()
    по_позиции_р: Counter = Counter()
    примери: dict[str, list] = {}
    сбор = Counter()
    сбор_р = Counter()
    редове = {}
    минали, минали_р = [], []

    for име, (път, д) in sorted(обобщения.items()):
        порта = д.get("gate") or {}
        фин = д.get("final_control") or {}
        експорт = д.get("export") or {}
        анализ = д.get("analysis") or {}
        заключен = СЕМЕЙСТВО.get(име, име) in ЗАКЛЮЧЕНИ

        actionable = int(порта.get("actionable") or анализ.get("actionable") or 0)
        одобрени = int(порта.get("approved") or 0)
        след_контрол = int(фин.get("approved_after") or 0)
        статус_експорт = str(експорт.get("status") or "")
        спиране = str(д.get("stop_code") or "")

        блокирани = порта.get("blocked_positions") or []
        for поз in блокирани:
            причини = tuple(sorted(set(поз.get("reasons") or ())))
            ключ = " + ".join(причини) or "(без код)"
            по_позиции[ключ] += 1
            if not заключен:
                по_позиции_р[ключ] += 1
            примери.setdefault(ключ, [])
            if len(примери[ключ]) < 5:
                примери[ключ].append({"проект": име,
                                      "надпис": поз.get("text"),
                                      "handle": поз.get("handle")})

        редове[име] = {
            "набор": "ЗАКЛ" if заключен else "РАЗВ",
            "actionable": actionable, "порта": одобрени,
            "финален_контрол": след_контрол, "експорт": статус_експорт,
            "спиране": спиране, "блокирани_позиции": len(блокирани),
            "прогон": път.name,
        }
        for ключ, стойност in (("actionable", actionable), ("порта", одобрени),
                               ("контрол", след_контрол),
                               ("блокирани", len(блокирани))):
            сбор[ключ] += стойност
            if not заключен:
                сбор_р[ключ] += стойност
        (минали if експорт.get("production_verified") else []).append(име)
        if not заключен and експорт.get("production_verified"):
            минали_р.append(име)

        print("%-10s %-4s %7d %7d %7d %7s  %-8s %s" % (
            име, "ЗАКЛ" if заключен else "РАЗВ", actionable, одобрени,
            след_контрол, "ДА" if експорт.get("production_verified") else "не",
            спиране or "-", път.name.split(".")[0]))

    def процент(част: int, цяло: int) -> str:
        return "%.1f%%" % (част * 100.0 / цяло) if цяло else "n/a"

    for етикет, с, п in (("ЦЯЛ КОРПУС", сбор, минали),
                         ("РАЗВОЕН НАБОР", сбор_р, минали_р)):
        print("\n=== %s" % етикет)
        print("  ACTIONABLE позиции (знаменател)   %6d" % с["actionable"])
        print("  минават ПОРТАТА                   %6d  %s"
              % (с["порта"], процент(с["порта"], с["actionable"])))
        print("  след ФИНАЛЕН КОНТРОЛ              %6d  %s"
              % (с["контрол"], процент(с["контрол"], с["actionable"])))
        print("  блокирани позиции на портата      %6d" % с["блокирани"])
        print("  проекти до PRODUCTION VERIFIED    %6d: %s" % (len(п), ", ".join(п)))

    for етикет, брояч in (("ЦЯЛ КОРПУС", по_позиции), ("РАЗВОЕН НАБОР", по_позиции_р)):
        print("\n=== БЛОКИРАНИ ПОЗИЦИИ ПО СЪЧЕТАНИЕ ОТ ПРИЧИНИ — %s" % етикет)
        всички = sum(брояч.values()) or 1
        for ключ, брой in брояч.most_common(12):
            print("  %5d  %5.1f%%  %s" % (брой, брой * 100.0 / всички, ключ))
        print("  %5d  100.0%%  ВСИЧКИ блокирани позиции" % sum(брояч.values()))

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps({
            "по_проект": редове,
            "сборове_цял_корпус": dict(сбор),
            "сборове_развоен": dict(сбор_р),
            "съчетания_цял_корпус": dict(по_позиции),
            "съчетания_развоен": dict(по_позиции_р),
            "примери": примери,
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        print("\nзаписан:", args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
