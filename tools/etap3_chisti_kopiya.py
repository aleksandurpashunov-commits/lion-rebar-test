"""Прави ЧИСТИ копия на проектите — без предварителни човешки одобрения.

Защо: проверката на етап 2 показа, че всички числа дотук включват
``approved_geometry_links.json`` и ``review_center_state.json`` от работата на
собственика. Редовете с вече одобрена връзка получават
``analysis_status = ПРОПУСНАТО — ВЕЧЕ Е ОДОБРЕНО В ПРОЕКТА`` и не се
преоценяват — тоест част от „разпознатото" всъщност е човешко решение.

Чистото копие пази САМО входа: изходния DXF, ``project.json``, замразената
снимка на engine-а и класификацията на слоевете. Всичко, което носи одобрение,
решение, замразяване или предишен експорт, се маха.

Оригиналните копия в ``C:\\lr_exp\\proekti`` НЕ се променят.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import shutil

ИЗТОЧНИК = pathlib.Path(r"C:\lr_exp\proekti")
ЦЕЛ = pathlib.Path(r"C:\lr_exp\proekti_chisti")

#: Тези файлове носят човешки решения или предишно състояние — махат се.
МАХАТ_СЕ = (
    "approved_geometry_links.json",
    "review_center_state.json",
    "decision_journal.jsonl",
    "decision_events_v2.jsonl",
    "decision_event_outbox.jsonl",
    "manual_corrections.json",
    "final_control_freeze.json",
    "final_control_invalidations.jsonl",
    "final_integrity_report.json",
    "final_integrity_ledger.csv",
    "final_control_export_config.json",
    "convention_profile.json",
    "convention_profile_audit.jsonl",
    "detected_positions.json",
)

#: Цели папки, които не са вход.
ПАПКИ_МАХАТ_СЕ = ("output", "final_control_sources")

#: Полета в ``project.json``, които сочат към махнатото.
РАЗШИРЕНИЯ_МАХАТ_СЕ = (
    "approved_geometry_links", "review_center_state", "final_control_freeze",
    "final_integrity_report", "convention_profile", "detected_positions",
)


def почисти(проект: pathlib.Path, цел: pathlib.Path) -> dict:
    if цел.exists():
        shutil.rmtree(цел)
    shutil.copytree(проект, цел, ignore=shutil.ignore_patterns(
        *ПАПКИ_МАХАТ_СЕ, "*.before_auto_*.json", "__pycache__"))

    махнати = []
    работна = цел / "working"
    if работна.is_dir():
        for име in МАХАТ_СЕ:
            п = работна / име
            if п.exists():
                размер = п.stat().st_size
                п.unlink()
                махнати.append({"файл": име, "байтове": размер})
        # и всички резервни копия на връзките
        for п in list(работна.glob("approved_geometry_links.*.json")):
            махнати.append({"файл": п.name, "байтове": п.stat().st_size})
            п.unlink()
    for име in ПАПКИ_МАХАТ_СЕ:
        п = цел / име
        if п.is_dir():
            shutil.rmtree(п)
            махнати.append({"файл": име + "/", "байтове": 0})

    # project.json вече не бива да сочи към махнатото
    пп = цел / "working" / "project.json"
    if not пп.is_file():
        пп = цел / "project.json"
    почистени_полета = []
    if пп.is_file():
        д = json.loads(пп.read_text(encoding="utf-8-sig"))
        разширения = д.get("extensions") or {}
        for ключ in РАЗШИРЕНИЯ_МАХАТ_СЕ:
            if ключ in разширения:
                разширения.pop(ключ)
                почистени_полета.append(ключ)
        д["extensions"] = разширения
        пп.write_text(json.dumps(д, ensure_ascii=False, indent=1), encoding="utf-8")

    остава = sorted(п.name for п in (цел / "working").iterdir()) \
        if (цел / "working").is_dir() else []
    return {"проект": проект.name, "цел": str(цел),
            "махнати": махнати, "почистени_полета": почистени_полета,
            "остава_в_working": остава}


def main() -> int:
    parser = argparse.ArgumentParser(description="Чисти копия без одобрения")
    parser.add_argument("проекти", nargs="*", help="празно = всички")
    parser.add_argument("--json", type=pathlib.Path,
                        default=pathlib.Path(r"C:\lr_exp\rezultat\etap_3\CHISTI_KOPIYA.json"))
    args = parser.parse_args()

    ЦЕЛ.mkdir(parents=True, exist_ok=True)
    имена = args.проекти or [п.name for п in sorted(ИЗТОЧНИК.iterdir()) if п.is_dir()]
    отчет = []
    for име in имена:
        проект = ИЗТОЧНИК / име
        if not проект.is_dir():
            print("няма такъв проект:", име)
            continue
        ред = почисти(проект, ЦЕЛ / име)
        отчет.append(ред)
        print("%-10s махнати %2d файла/папки; в working остават %d" % (
            име, len(ред["махнати"]), len(ред["остава_в_working"])))
        for м in ред["махнати"][:4]:
            print("      - %s" % м["файл"])

    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(отчет, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\nчисти копия в:", ЦЕЛ)
    print("записан:", args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
