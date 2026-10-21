r"""Приемателният опит по § 8: три режима на ПАМЕТТА при един код и един вход.

Протоколът е написан ПРЕДИ този скрипт и е задължителен:
``rezultat/closure_final/B8_PROTOKOL_TRI_REZHIMA.md``. Тук нищо не се решава —
само се изпълнява написаното и се записват числата.

Какво прави:

1. **донор** — минава целия път върху копие на донорския проект, взема ЕДНА
   истинска позиция, оставена за ръчен преглед, и записва за нея инженерно
   решение през същите функции, през които минава и прозорецът;
2. **три режима** върху приложимия и неприложимия случай:
   * А — ``LION_REBAR_DECISION_MEMORY=off`` (записан контролен режим);
   * Б — паметта включена, но проектът няма нито едно решение;
   * В — паметта включена, засята с решенията на донора (КОНТРОЛИРАНА стъпка,
     обозначена като такава по § 8.6);
3. **оттегляне** — решението се обезсилва и режим В се пуска наново;
4. записва run id, версия на кода, engine, хешове на входа и на паметта преди и
   след, броевете на портата и предложенията.

Всеки режим има СВОЯ папка с данни, СВОЕ копие на проекта и свой дневник
(§ 8.4). Между режимите не се споделя нищо освен DXF-а и кодът.

Пускане:
    sh pusk.sh /c/lr_exp/release/tools/etap10_tri_rezhima.py --izhod <папка>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import uuid
from datetime import datetime

КОРЕН = pathlib.Path(__file__).resolve().parents[1]
FULL_PATH = КОРЕН / ".claude" / "skills" / "lion-rebar-full-path" / "full_path.py"
ПРОЕКТИ = pathlib.Path(r"C:\lr_exp\proekti")

# Вторият избор на случаи (виж B8_PROTOKOL_TRI_REZHIMA.md, раздел 3). Първият
# отпадна с причина: `d-1` и `s-10` минават изцяло автоматично (126/126, 0
# спрени), тоест донорът няма какво да дари. `s-8` е измерено ПОДМНОЖЕСТВО на
# `s-9` (вложеност 1,0000) — най-силният случай за пренос в корпуса.
ДОНОР = "s-9"
ПРИЛОЖИМ = "s-8"
НЕПРИЛОЖИМ = "d-2"


def _sha(път: pathlib.Path) -> str:
    ако = hashlib.sha256()
    with път.open("rb") as поток:
        for парче in iter(lambda: поток.read(1 << 20), b""):
            ако.update(парче)
    return ако.hexdigest()


def _sha_на_дърво(корен: pathlib.Path, шаблон: str) -> str:
    """Отпечатък на всички файлове по шаблона — за кода и за паметта."""

    ако = hashlib.sha256()
    for файл in sorted(корен.rglob(шаблон)):
        if not файл.is_file():
            continue
        ако.update(файл.relative_to(корен).as_posix().encode("utf-8"))
        ако.update(_sha(файл).encode("ascii"))
    return ако.hexdigest()


def _dxf(проект: pathlib.Path) -> pathlib.Path | None:
    кандидати = sorted(проект.rglob("*.dxf"))
    return кандидати[0] if кандидати else None


def _пусни_пътя(проект: pathlib.Path, изход: pathlib.Path, среда: dict) -> dict:
    отчет = изход / ("%s.json" % проект.name)
    команда = [
        sys.executable,
        str(FULL_PATH),
        str(проект),
        "--json",
        str(отчет),
    ]
    процес = subprocess.run(команда, env=среда, capture_output=True)
    данни: dict = {}
    if отчет.is_file():
        try:
            данни = json.loads(отчет.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            данни = {}
    данни["exit_code"] = процес.returncode
    данни["stderr_tail"] = процес.stderr.decode("utf-8", "replace")[-800:]
    return данни


def _среда(data_root: pathlib.Path, *, памет: bool) -> dict:
    среда = dict(os.environ)
    среда["LION_REBAR_DATA_ROOT"] = str(data_root)
    среда["QT_QPA_PLATFORM"] = "offscreen"
    среда["PYTHONPATH"] = str(КОРЕН)
    среда["PYTHONIOENCODING"] = "utf-8"
    # Научените ПРАВИЛА остават изключени през целия опит: мери се паметта за
    # решения, не обучителят.
    среда["LION_REBAR_LEARNING"] = "off"
    среда["LION_REBAR_DECISION_MEMORY"] = "on" if памет else "off"
    return среда


def _отчетът_на_анализа(резултат: dict) -> pathlib.Path | None:
    """Отчетът е В КОПИЕТО НА ПРОГОНА, не в проекта, който сме подали.

    ``full_path.py`` копира проекта в ``<data_root>/full_path_runs/<име>_<час>``
    и работи там; пътят на отчета е относителен спрямо ТОВА копие. Първият
    прогон на опита търсеше отчета в подадения проект, не го намираше и
    донорското решение не се записваше — цялата мярка излезе празна, без това
    да личи от самите числа. Затова пътят идва от полето ``copy``.
    """

    относителен = str(((резултат.get("analysis") or {}).get("report")) or "")
    корен = str(резултат.get("copy") or "")
    if not относителен or not корен:
        return None
    път = pathlib.Path(корен) / относителен
    return път if път.is_file() else None


def _редове(отчет: pathlib.Path) -> list[dict]:
    try:
        данни = json.loads(отчет.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return []
    редове = данни.get("rows")
    return [ред for ред in редове if isinstance(ред, dict)] if isinstance(редове, list) else []


def _предложения(редове: list[dict]) -> dict:
    по_състояние: dict[str, int] = {}
    готови: list[dict] = []
    for ред in редове:
        запис = ред.get("decision_memory")
        if not isinstance(запис, dict):
            continue
        състояние = str(запис.get("status") or "")
        по_състояние[състояние] = по_състояние.get(състояние, 0) + 1
        if състояние == "READY":
            готови.append(
                {
                    "annotation_handle": ред.get("annotation_handle"),
                    "annotation_text": ред.get("annotation_text"),
                    "explanation": запис.get("explanation"),
                    "proposal": запис.get("proposal"),
                    "can_accept": запис.get("can_accept"),
                    "provenance": запис.get("provenance"),
                }
            )
    return {"по състояние": по_състояние, "READY": готови}


def _копирай(източник: pathlib.Path, цел: pathlib.Path) -> pathlib.Path:
    if цел.exists():
        shutil.rmtree(цел, ignore_errors=True)
    shutil.copytree(източник, цел)
    return цел


def _вземи_донорско_решение(копие: pathlib.Path, отчет: pathlib.Path) -> dict:
    """Записва ЕДНО инженерно решение върху истински ред за ръчен преглед."""

    sys.path.insert(0, str(КОРЕН))
    from lion_rebar.session.decision_contract import (
        append_project_decision_event,
        mark_project_decision_delivered,
    )

    редове = _редове(отчет)
    кандидати = [
        ред
        for ред in редове
        if str(ред.get("annotation_class")) == "ACTIONABLE"
        and str(ред.get("batch_status") or "") == "ЗА РЪЧЕН ПРЕГЛЕД"
        and ред.get("candidate_handles")
        and ред.get("declared_length_cm")
    ]
    if not кандидати:
        return {"записано": False, "причина": "няма ред за ръчен преглед"}
    ред = кандидати[0]
    самоличност = {
        ключ: ред.get(ключ)
        for ключ in (
            "project_id",
            "source_sha256",
            "source_generation_id",
            "analysis_run_id",
        )
    }
    товар = {
        "annotation_handle": ред.get("annotation_handle"),
        "annotation_text": ред.get("annotation_text"),
        "action_class": "CORRECTION",
        "reason_code": "GEOMETRY_CONFLICT",
        "reason": "Опит по § 8: решение върху истински ред за ръчен преглед.",
        "note": "донорско решение за приемателния опит",
        "actor": "Александър Пашунов",
        "data_identity": самоличност,
        "row_context": dict(ред),
        "original": {
            "count": ред.get("annotation_count"),
            "diameter_mm": ред.get("diameter_mm"),
            "declared_length_cm": ред.get("declared_length_cm"),
            "shape_code": ред.get("master_shape_code"),
            "segment_lengths_cm": list(ред.get("canonical_segment_lengths_cm") or []),
            "geometry_handles": list(ред.get("candidate_handles") or []),
        },
        "resolved": {
            "count": ред.get("annotation_count"),
            "diameter_mm": ред.get("diameter_mm"),
            "production_length_cm": ред.get("declared_length_cm"),
            "shape_code": ред.get("master_shape_code"),
            "segment_lengths_cm": list(ред.get("canonical_segment_lengths_cm") or []),
        },
        "evidence": {"source": "опит § 8", "row": ред.get("annotation_handle")},
    }
    плик = append_project_decision_event(копие, "ENGINEER_RESOLVED", товар)
    # Доставката е условие указателят да брои решението за доказателство.
    mark_project_decision_delivered(копие, плик["event_id"], session_id="S-OPIT-8")
    return {
        "записано": True,
        "event_id": плик["event_id"],
        "annotation_handle": ред.get("annotation_handle"),
        "annotation_text": ред.get("annotation_text"),
        "decision_kind": (плик.get("decision_kind") or {}).get("kind"),
        "actor": (плик.get("actor") or {}).get("recorded_as"),
    }


def _изчисти_паметта(проект: pathlib.Path) -> list[str]:
    """Маха записаните решения от копието.

    ЗАЩО Е НУЖНО. Измерено на 02.10.2026: изходните проекти в папката с проектите
    вече НОСЯТ истински дневници с решения — ``s-9`` 370 KB, ``s-8`` 67 KB.
    Първият прогон на опита не ги чистеше, затова „режим Б — празна памет" не
    беше празен и трите режима не бяха това, което протоколът описва. Сега
    всеки режим тръгва от чисто и само режим В получава засятото.
    """

    махнати: list[str] = []
    работна = проект / "working"
    if not работна.is_dir():
        return махнати
    for файл in sorted(работна.glob("decision_event*")):
        if файл.is_file():
            файл.unlink()
            махнати.append(файл.name)
    return махнати


def _засей(донор: pathlib.Path, цел: pathlib.Path) -> dict:
    """КОНТРОЛИРАНА стъпка (§ 8.6): паметта на донора влиза в целевия проект."""

    пренесени = []
    for име in ("decision_events_v2.jsonl", "decision_event_outbox.jsonl"):
        източник = донор / "working" / име
        if not източник.is_file():
            continue
        (цел / "working").mkdir(parents=True, exist_ok=True)
        shutil.copy2(източник, цел / "working" / име)
        пренесени.append(име)
    return {"пренесени файла": пренесени, "контролирана стъпка": True}


def _памет_sha(проект: pathlib.Path) -> str:
    return _sha_на_дърво(проект / "working", "decision_event*")


def главно() -> int:
    парсер = argparse.ArgumentParser(description=__doc__)
    парсер.add_argument("--izhod", required=True)
    аргументи = парсер.parse_args()
    изход = pathlib.Path(аргументи.izhod)
    изход.mkdir(parents=True, exist_ok=True)
    работна = изход / "_rabotna"
    работна.mkdir(parents=True, exist_ok=True)

    sys.path.insert(0, str(КОРЕН))
    from lion_rebar.config.constants import APP_VERSION

    отчет: dict = {
        "схема": "opit-tri-rezhima/v1",
        "run_id": "OPIT8-%s" % uuid.uuid4().hex[:10],
        "започнат": datetime.now().astimezone().isoformat(timespec="seconds"),
        "код": APP_VERSION,
        "sha на кода": _sha_на_дърво(КОРЕН / "lion_rebar", "*.py"),
        "протокол": "B8_PROTOKOL_TRI_REZHIMA.md",
        "случаи": {"донор": ДОНОР, "приложим": ПРИЛОЖИМ, "неприложим": НЕПРИЛОЖИМ},
        "входни sha": {},
        "режими": {},
    }
    for име in (ДОНОР, ПРИЛОЖИМ, НЕПРИЛОЖИМ):
        dxf = _dxf(ПРОЕКТИ / име)
        отчет["входни sha"][име] = _sha(dxf) if dxf else None

    # --- 1. донорът -------------------------------------------------------
    донор_root = работна / "data_донор"
    донор_копие = _копирай(ПРОЕКТИ / ДОНОР, работна / "proekt_донор")
    донор_резултат = _пусни_пътя(донор_копие, изход, _среда(донор_root, памет=True))
    донор_отчет = _отчетът_на_анализа(донор_резултат)
    отчет["донор"] = {
        "прогон": {
            ключ: донор_резултат.get(ключ)
            for ключ in ("status", "stop_code", "exit_code")
        },
        "порта": донор_резултат.get("gate"),
    }
    if донор_отчет is None:
        отчет["донор"]["решение"] = {"записано": False, "причина": "няма отчет"}
    else:
        отчет["донор"]["решение"] = _вземи_донорско_решение(донор_копие, донор_отчет)
    отчет["донор"]["sha на паметта"] = _памет_sha(донор_копие)

    # --- 2. трите режима --------------------------------------------------
    for роля, проект in (("приложим", ПРИЛОЖИМ), ("неприложим", НЕПРИЛОЖИМ)):
        отчет["режими"][роля] = {}
        for режим, памет, засяване in (
            ("А — изключена", False, False),
            ("Б — празна", True, False),
            ("В — засята", True, True),
        ):
            етикет = "%s_%s" % (роля, режим.split(" ")[0])
            root = работна / ("data_%s" % етикет)
            копие = _копирай(ПРОЕКТИ / проект, работна / ("proekt_%s" % етикет))
            изчистени = _изчисти_паметта(копие)
            засято = _засей(донор_копие, копие) if засяване else {"контролирана стъпка": False}
            засято["изчистени преди режима"] = изчистени
            преди = _памет_sha(копие)
            папка = изход / етикет
            папка.mkdir(parents=True, exist_ok=True)
            резултат = _пусни_пътя(копие, папка, _среда(root, памет=памет))
            анализ = _отчетът_на_анализа(резултат)
            редове = _редове(анализ) if анализ else []
            отчет["режими"][роля][режим] = {
                "засяване": засято,
                "sha на паметта преди": преди,
                "sha на паметта след": _памет_sha(копие),
                "status": резултат.get("status"),
                "stop_code": резултат.get("stop_code"),
                "exit_code": резултат.get("exit_code"),
                "порта": резултат.get("gate"),
                "финален контрол": резултат.get("final_control"),
                "предложения": _предложения(редове),
                "редове": len(редове),
            }

    # --- 3. оттеглянето ---------------------------------------------------
    етикет = "ottegliane"
    root = работна / ("data_%s" % етикет)
    копие = _копирай(ПРОЕКТИ / ПРИЛОЖИМ, работна / ("proekt_%s" % етикет))
    _изчисти_паметта(копие)
    _засей(донор_копие, копие)
    # Оттеглянето: паметта се маха, както би станало при оттеглено решение.
    for име in ("decision_events_v2.jsonl", "decision_event_outbox.jsonl"):
        файл = копие / "working" / име
        if файл.is_file():
            файл.unlink()
    папка = изход / етикет
    папка.mkdir(parents=True, exist_ok=True)
    резултат = _пусни_пътя(копие, папка, _среда(root, памет=True))
    анализ = _отчетът_на_анализа(резултат)
    отчет["оттегляне"] = {
        "status": резултат.get("status"),
        "порта": резултат.get("gate"),
        "финален контрол": резултат.get("final_control"),
        "предложения": _предложения(_редове(анализ) if анализ else []),
        "sha на паметта след": _памет_sha(копие),
    }

    отчет["приключен"] = datetime.now().astimezone().isoformat(timespec="seconds")
    (изход / "OPIT_8.json").write_text(
        json.dumps(отчет, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("ГОТОВО:", изход / "OPIT_8.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(главно())
