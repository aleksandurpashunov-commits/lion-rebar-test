# -*- coding: utf-8 -*-
r"""Корпусна регресия за Опит 11 — 11-те чертежа върху ЗАМРАЗЕНИЯ краен код.

Пуска целия път върху ЧИСТИТЕ копия в ИЗОЛИРАНА папка с данни и сравнява
ИНЖЕНЕРНИТЕ ПОЛЕТА ПО ПОЗИЦИЯ срещу базовия прогон (``progoni_pamet``,
02.10.2026) — не само общите числа.

Паметта за решения е ВКЛЮЧЕНА (подразбирането в производството), а чистите
копия не носят нито едно решение. Така се проверява точно това, което трябва:
че самото съществуване на механизма не мени нито едно инженерно поле.

Изход: ``KORPUS/<проект>.json``, ``KORPUS_SVODKA.json``,
``KORPUS_POSITION_DIFF.csv``.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
from datetime import datetime

КОРЕН = pathlib.Path(__file__).resolve().parents[1]
FULL_PATH = КОРЕН / ".claude" / "skills" / "lion-rebar-full-path" / "full_path.py"
ЧИСТИ = pathlib.Path(r"C:\lr_exp\proekti_chisti")
БАЗА = pathlib.Path(r"C:\lr_exp\rezultat\closure_final\progoni_pamet")

#: Полетата, по които се сравнява ПОЗИЦИЯ срещу ПОЗИЦИЯ. Показващите полета на
#: преизползването (``decision_reuse``, ``decision_memory``,
#: ``candidate_notes``) нарочно НЕ са тук: те са нови по замисъл.
ИНЖЕНЕРНИ = (
    "batch_status",
    "annotation_class",
    "annotation_count",
    "diameter_mm",
    "declared_length_cm",
    "master_shape_code",
    "master_shape_name",
    "canonical_segment_lengths_cm",
    "canonical_turn_angles_deg",
    "production_segments_cm",
    "candidate_handles",
    "candidate_layer",
    "segment_count",
    "matcher_disposition",
    "matcher_evidence_band",
    "matcher_blocker_codes",
    "frame_category_code",
    "frame_title",
    "approval_origin",
    "review_reason_codes",
)

ЧИСЛА = (
    "actionable", "approved", "blocked", "final_control", "export_status",
)


def _sha(път: pathlib.Path) -> str:
    смес = hashlib.sha256()
    with път.open("rb") as поток:
        for парче in iter(lambda: поток.read(1 << 20), b""):
            смес.update(парче)
    return смес.hexdigest()


def _sha_на_дърво(корен: pathlib.Path, шаблон: str) -> str:
    смес = hashlib.sha256()
    for файл in sorted(корен.rglob(шаблон)):
        if not файл.is_file():
            continue
        смес.update(файл.relative_to(корен).as_posix().encode("utf-8"))
        смес.update(_sha(файл).encode("ascii"))
    return смес.hexdigest()


def _редове(резултат: dict) -> list[dict]:
    относителен = str(((резултат.get("analysis") or {}).get("report")) or "")
    копие = str(резултат.get("copy") or "")
    if not относителен or not копие:
        return []
    път = pathlib.Path(копие) / относителен
    if not път.is_file():
        return []
    try:
        данни = json.loads(път.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return []
    редове = данни.get("rows")
    return [р for р in редове if isinstance(р, dict)] if isinstance(редове, list) else []


def _по_позиция(редове: list[dict]) -> dict[str, dict]:
    резултат: dict[str, dict] = {}
    for ред in редове:
        ключ = str(ред.get("annotation_handle") or "")
        if ключ:
            резултат[ключ] = {име: ред.get(име) for име in ИНЖЕНЕРНИ}
    return резултат


def _числа(резултат: dict) -> dict:
    порта = резултат.get("gate") or {}
    контрол = резултат.get("final_control") or {}
    експорт = резултат.get("export") or {}
    return {
        "status": резултат.get("status"),
        "stop_code": резултат.get("stop_code"),
        "actionable": порта.get("actionable"),
        "approved": порта.get("approved"),
        "blocked": порта.get("blocked"),
        "final_control": контрол.get("approved_after"),
        "integrity_passed": контрол.get("integrity_passed"),
        "export_status": експорт.get("status"),
        "excel_positions": експорт.get("excel_positions"),
        "dxf_unique_positions": експорт.get("dxf_unique_positions"),
    }


def главно() -> int:
    парсер = argparse.ArgumentParser(description=__doc__)
    парсер.add_argument("--izhod", required=True)
    парсер.add_argument("--proekti", nargs="*")
    аргументи = парсер.parse_args()
    изход = pathlib.Path(аргументи.izhod)
    (изход / "KORPUS").mkdir(parents=True, exist_ok=True)
    работна = изход / "_korpus_rabotna"
    работна.mkdir(parents=True, exist_ok=True)

    sys.path.insert(0, str(КОРЕН))
    from lion_rebar.config.constants import APP_VERSION

    имена = аргументи.proekti or [
        п.name for п in sorted(ЧИСТИ.iterdir()) if п.is_dir()
    ]
    сводка: dict = {
        "схема": "opit11-korpus/v1",
        "започнат": datetime.now().astimezone().isoformat(timespec="seconds"),
        "код": APP_VERSION,
        "sha на кода": _sha_на_дърво(КОРЕН / "lion_rebar", "*.py"),
        "база": str(БАЗА),
        "проекти": {},
    }
    разлики: list[dict] = []
    for име in имена:
        проект = ЧИСТИ / име
        if not проект.is_dir():
            сводка["проекти"][име] = {"ГРЕШКА": "няма чисто копие"}
            continue
        копие = работна / ("proekt_%s" % име)
        if копие.exists():
            shutil.rmtree(копие, ignore_errors=True)
        shutil.copytree(проект, копие)
        среда = dict(os.environ)
        среда.update({
            "LION_REBAR_DATA_ROOT": str(работна / ("data_%s" % име)),
            "QT_QPA_PLATFORM": "offscreen",
            "PYTHONPATH": str(КОРЕН),
            "PYTHONIOENCODING": "utf-8",
            "LION_REBAR_LEARNING": "off",
            "LION_REBAR_AUTOCYCLE": "off",
            # Паметта е ВКЛЮЧЕНА: това е производственото подразбиране.
            "LION_REBAR_DECISION_MEMORY": "on",
        })
        отчет = изход / "KORPUS" / ("%s.json" % име)
        процес = subprocess.run(
            [
                sys.executable, str(FULL_PATH), str(копие),
                "--json", str(отчет),
                "--work-root", str(работна / ("run_%s" % име)),
            ],
            env=среда, capture_output=True,
        )
        резултат: dict = {}
        if отчет.is_file():
            try:
                резултат = json.loads(отчет.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                резултат = {}
        резултат["exit_code"] = процес.returncode
        нови = _по_позиция(_редове(резултат))

        базов_файл = БАЗА / ("%s.json" % име)
        стари: dict[str, dict] = {}
        базови_числа: dict = {}
        if базов_файл.is_file():
            try:
                базов = json.loads(базов_файл.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                базов = {}
            базови_числа = _числа(базов)
            стари = _по_позиция(_редове(базов))

        само_нови = sorted(set(нови) - set(стари))
        само_стари = sorted(set(стари) - set(нови))
        променени: list[dict] = []
        for ключ in sorted(set(нови) & set(стари)):
            for поле in ИНЖЕНЕРНИ:
                старо = стари[ключ].get(поле)
                ново = нови[ключ].get(поле)
                if старо != ново:
                    променени.append({
                        "проект": име, "позиция": ключ, "поле": поле,
                        "базова стойност": json.dumps(старо, ensure_ascii=False),
                        "нова стойност": json.dumps(ново, ensure_ascii=False),
                    })
        разлики.extend(променени)
        if само_нови or само_стари:
            for ключ in само_нови:
                разлики.append({
                    "проект": име, "позиция": ключ, "поле": "(нова позиция)",
                    "базова стойност": "", "нова стойност": "присъства",
                })
            for ключ in само_стари:
                разлики.append({
                    "проект": име, "позиция": ключ, "поле": "(липсваща позиция)",
                    "базова стойност": "присъстваше", "нова стойност": "",
                })
        сводка["проекти"][име] = {
            "нови числа": _числа(резултат),
            "базови числа": базови_числа,
            "позиции сега": len(нови),
            "позиции в базата": len(стари),
            "променени полета": len(променени),
            "само нови позиции": само_нови,
            "само базови позиции": само_стари,
            "exit": процес.returncode,
            "stderr_tail": процес.stderr.decode("utf-8", "replace")[-600:],
        }
        print(име, сводка["проекти"][име]["нови числа"],
              "разлики:", len(променени), flush=True)

    сводка["общо разлики по позиция"] = len(разлики)
    сводка["приключен"] = datetime.now().astimezone().isoformat(
        timespec="seconds")
    (изход / "KORPUS_SVODKA.json").write_text(
        json.dumps(сводка, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    with (изход / "KORPUS_POSITION_DIFF.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as поток:
        писач = csv.DictWriter(
            поток,
            fieldnames=[
                "проект", "позиция", "поле", "базова стойност", "нова стойност",
            ],
        )
        писач.writeheader()
        for ред in разлики:
            писач.writerow(ред)
    print("ГОТОВО:", изход / "KORPUS_SVODKA.json", "разлики:", len(разлики))
    return 0


if __name__ == "__main__":
    raise SystemExit(главно())
