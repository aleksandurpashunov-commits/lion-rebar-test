"""Манифест на етап 2: код, данни, engine и научено знание.

Записва SHA-256 на всичко, което влиза в резултата, за да може прогонът да се
повтори и да се докаже, че сравняваните режими са ползвали едно и също.

Само чете.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import subprocess
from datetime import datetime

ОСНОВНА = pathlib.Path(
    r"I:\Моят диск\Lion-Rebar\LION_REBAR_v0_1_44_21-rc44_WINDOWS\release")
КОПИЕ = pathlib.Path(r"C:\lr_exp\release")
ДАННИ = pathlib.Path(r"C:\lr_exp\data")
ПРОЕКТИ = pathlib.Path(r"C:\lr_exp\proekti")
РЕЗУЛТАТ = pathlib.Path(r"C:\lr_exp\rezultat\etap_2")


def _sha(п: pathlib.Path) -> str:
    h = hashlib.sha256()
    with п.open("rb") as f:
        for блок in iter(lambda: f.read(1 << 20), b""):
            h.update(блок)
    return h.hexdigest()


def _пропуска(отн: str) -> bool:
    части = отн.split("/")
    return (".venv" in части or "__pycache__" in части or ".git" in части
            or ".pytest_cache" in части or отн.endswith(".pyc"))


def _дърво(корен: pathlib.Path, *, само: tuple[str, ...] = ()) -> dict[str, str]:
    изход: dict[str, str] = {}
    if not корен.is_dir():
        return изход
    for п in корен.rglob("*"):
        if not п.is_file():
            continue
        отн = str(п.relative_to(корен)).replace("\\", "/")
        if _пропуска(отн):
            continue
        if само and not отн.startswith(само):
            continue
        изход[отн] = _sha(п)
    return изход


def main() -> int:
    global РЕЗУЛТАТ
    parser = argparse.ArgumentParser(description="Манифест на етап 2")
    parser.add_argument("--json", type=pathlib.Path,
                        default=РЕЗУЛТАТ / "MANIFEST_etap2.json")
    parser.add_argument("--rezultat", type=pathlib.Path, default=None,
                        help="друга папка с резултати (напр. etap_3)")
    args = parser.parse_args()
    if args.rezultat:
        РЕЗУЛТАТ = args.rezultat

    глава_основна = subprocess.run(
        ["git", "-C", str(ОСНОВНА), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=False).stdout.strip()
    глава_копие = subprocess.run(
        ["git", "-C", str(КОПИЕ), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=False).stdout.strip()
    промени_копие = subprocess.run(
        ["git", "-C", str(КОПИЕ), "status", "--porcelain"],
        capture_output=True, text=True, check=False).stdout.strip().splitlines()

    учебен_пакет = _дърво(КОПИЕ, само=("lion_rebar/learning/",))
    инструменти = {к: v for к, v in _дърво(КОПИЕ, само=("tools/",)).items()
                   if k_e_etap(к)}
    променени_файлове = _дърво(КОПИЕ, само=(
        "lion_rebar/dxf/disagreement_report.py",
        "lion_rebar/utils/app_paths.py",
        "lion_rebar/dxf/convention_profile.py",
        "tests/test_learning_convention.py",
        "tests/test_learning_robustness.py",
        "tests/test_learning_auto_cycle.py",
        "tests/test_disagreement_report.py",
        ".claude/skills/lion-rebar-full-path/full_path.py",
    ))

    хранилище = ДАННИ / "naucheno" / "naucheno.json"
    външни = {
        име: (_sha(ДАННИ / "naucheno" / име) if (ДАННИ / "naucheno" / име).is_file() else None)
        for име in ("izvori.json", "vunshno_znanie.json", "nablyudeniya.jsonl")
    }
    engine_снимки = {}
    for проект in sorted(ПРОЕКТИ.iterdir()) if ПРОЕКТИ.is_dir() else ():
        папка = проект / "master"
        for ф in sorted(папка.glob("engine_*.json")) if папка.is_dir() else ():
            engine_снимки["%s/%s" % (проект.name, ф.name)] = _sha(ф)

    манифест = {
        "schema": "lion-rebar-etap2-manifest/v1",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "основна_програма": {
            "път": str(ОСНОВНА), "git_head": глава_основна,
            "забележка": "НЕ е променяна; сверява се с experiment_verify_isolation.py",
        },
        "експериментален_код": {
            "път": str(КОПИЕ), "git_head": глава_копие,
            "неприключени_промени": промени_копие,
            "учебен_пакет": учебен_пакет,
            "променени_или_нови_файлове": променени_файлове,
            "инструменти_на_етапите": инструменти,
        },
        "данни": {
            "LION_REBAR_DATA_ROOT": str(ДАННИ),
            "хранилище_научено": {
                "път": str(хранилище),
                "съществува": хранилище.is_file(),
                "sha256": _sha(хранилище) if хранилище.is_file() else None,
                "байтове": хранилище.stat().st_size if хранилище.is_file() else 0,
            },
            "engine_снимки_на_проектите": engine_снимки,
            "външно_знание": външни,
        },
        "резултати": _дърво(РЕЗУЛТАТ),
        "среда": {
            "LION_REBAR_LEARNING": "празно=ВКЛ, off=ИЗКЛ",
            "LION_REBAR_HARDCODED_ALIASES": "празно=ВКЛ, off=изпразва position_key_*_aliases",
            "LION_REBAR_NAUCHENO": "път до друго хранилище (за честен пренос)",
            "LION_REBAR_AUTOCYCLE": "празно=ВКЛ, off=спира автоматичния цикъл",
        },
    }
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(манифест, ensure_ascii=False, indent=1),
                         encoding="utf-8")
    print("манифест:", args.json)
    print("  основна програма HEAD      ", глава_основна[:12])
    print("  експериментален код HEAD   ", глава_копие[:12])
    print("  неприключени промени в копието:", len(промени_копие))
    print("  файлове в учебния пакет    ", len(учебен_пакет))
    print("  инструменти на етапите     ", len(инструменти))
    print("  хранилище sha256           ",
          (манифест["данни"]["хранилище_научено"]["sha256"] or "(няма)")[:16])
    print("  engine снимки              ", len(engine_снимки))
    print("  файлове в резултата        ", len(манифест["резултати"]))
    return 0


def k_e_etap(отн: str) -> bool:
    име = отн.rsplit("/", 1)[-1]
    return име.startswith(("etap2_", "etap3_", "experiment_"))


if __name__ == "__main__":
    raise SystemExit(main())
