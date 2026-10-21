"""Преизчислява РАЗНОГЛАСИЯТА от запазените редове с ТЕКУЩИЯ код.

Защо: отчетите в прогоните носят разминаванията, изчислени с кода от момента на
прогона. За да се измери ефектът от поправка в ``disagreement_report.py``, не е
нужен нов анализ — достатъчно е да се повикат проверките наново върху същите
редове. Така разликата е само от кода.

Показва СТАРО (записано в отчета) срещу НОВО (преизчислено) за всеки проект.
Само чете; отчетите не се пренаписват.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections import Counter

КОРЕН = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(КОРЕН))

from lion_rebar.dxf.disagreement_report import build_disagreement_report  # noqa: E402

ПРОГОНИ = pathlib.Path(r"C:\lr_exp\data\full_path_runs")
ЛИПСА_НЕ_СПОР = {"УЧАСТЪК_БЕЗ_КОТА", "БЕЗ_КОТИ", "НЯМА_КОТИ"}


ПРОЕКТИ = pathlib.Path(r"C:\lr_exp\proekti")


def _формите(проект: str) -> list:
    """Формите от ЗАМРАЗЕНИЯ engine на самия проект — същите, с които е анализиран.

    Не се ползва активният регистър: изолираната папка с данни тръгва от
    bootstrap 0.0.0 и R5 би мълчала, което щеше да изглежда като полза от
    поправката.
    """

    from lion_rebar.engine.models import EnginePackage

    папка = ПРОЕКТИ / проект / "master"
    файлове = sorted(папка.glob("engine_*.json")) if папка.is_dir() else []
    if not файлове:
        print("ВНИМАНИЕ  %s: няма замразен engine; R5 мълчи" % проект)
        return []
    най = max(файлове, key=lambda f: f.stat().st_mtime)
    engine = EnginePackage.from_dict(json.loads(най.read_text(encoding="utf-8-sig")))
    return list(engine.shapes)


def _най_нови() -> dict[str, pathlib.Path]:
    намерени: dict[str, tuple[float, pathlib.Path]] = {}
    for п in ПРОГОНИ.rglob("*_geometry_batch.json"):
        име = п.parent.parent.parent.parent.name.split("_2026")[0]
        време = п.stat().st_mtime
        ако = намерени.get(име)
        if ако is None or време > ако[0]:
            намерени[име] = (време, п)
    return {и: п for и, (_, п) in намерени.items()}


def _раздели(находки) -> tuple[int, int, Counter]:
    спорове, липси = 0, 0
    видове: Counter = Counter()
    for н in находки:
        вид = н.get("kind") if isinstance(н, dict) else н.kind
        if вид in ЛИПСА_НЕ_СПОР:
            липси += 1
            continue
        спорове += 1
        видове[вид] += 1
    return спорове, липси, видове


def main() -> int:
    parser = argparse.ArgumentParser(description="Преизчислени разногласия")
    parser.add_argument("--json", type=pathlib.Path, default=None)
    args = parser.parse_args()

    отчети = _най_нови()
    print()
    print("%-10s %7s  %-18s  %-18s" % ("проект", "позиц", "СТАРО сп./без кота", "НОВО сп./без кота"))
    print("-" * 66)

    изход = {}
    сбор = Counter()
    for име, път in sorted(отчети.items()):
        д = json.loads(път.read_text(encoding="utf-8-sig"))
        стар = д.get("disagreements") or {}
        стари_спорове, стари_липси, стари_видове = _раздели(стар.get("findings") or [])

        форми = _формите(име)
        нов = build_disagreement_report(д.get("rows") or [], shape_definitions=форми)
        нови_спорове, нови_липси, нови_видове = _раздели(нов.get("findings") or [])

        изход[име] = {
            "позиции": нов.get("scope_positions"),
            "старо": {"спорове": стари_спорове, "без_кота": стари_липси,
                      "видове": dict(стари_видове)},
            "ново": {"спорове": нови_спорове, "без_кота": нови_липси,
                     "видове": dict(нови_видове)},
        }
        сбор["стари_спорове"] += стари_спорове
        сбор["стари_липси"] += стари_липси
        сбор["нови_спорове"] += нови_спорове
        сбор["нови_липси"] += нови_липси
        знак = ""
        if (нови_спорове, нови_липси) != (стари_спорове, стари_липси):
            знак = "   <-- различно"
        print("%-10s %7s  %7d / %-8d  %7d / %-8d  форми=%-3d%s" % (
            име, нов.get("scope_positions"), стари_спорове, стари_липси,
            нови_спорове, нови_липси, len(форми), знак))

    print("\n=== СБОР по 8-те проекта със запазени отчети")
    print("  противоречия      СТАРО %5d  ->  НОВО %5d  (%+d)"
          % (сбор["стари_спорове"], сбор["нови_спорове"],
             сбор["нови_спорове"] - сбор["стари_спорове"]))
    print("  участък без кота  СТАРО %5d  ->  НОВО %5d  (%+d)"
          % (сбор["стари_липси"], сбор["нови_липси"],
             сбор["нови_липси"] - сбор["стари_липси"]))

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps({"по_проект": изход, "сбор": dict(сбор)},
                                        ensure_ascii=False, indent=1), encoding="utf-8")
        print("\nзаписан:", args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
