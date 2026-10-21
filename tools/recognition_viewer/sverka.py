# -*- coding: utf-8 -*-
r"""Сверка на одобреното от програмата с бележките на собственика от прегледа.

    python -m tools.recognition_viewer.sverka <копие на проект> [--belezhki <json>]

Защо: на 07.10.2026 поправка, минала 2758 теста и корпусна регресия с нула
разлики, пускаше в Excel рамена 128,8/18,8 при написани от собственика 130/20.
Тестовете проверяват механизма, корпусът — че познатото не е мръднало; нито
едното не сравнява НОВИЯ резултат с независим източник. Бележките на
собственика са такъв източник. Тази сверка ги сравнява с позициите, които
портата пуска (``build_batch_export_context`` — същата мярка като в
CLAUDE.md), и излиза с код 1 при всяко противоречие.

Само чете. Не пипа проекта, бележките или експорта.
"""
from __future__ import annotations

import argparse
import glob
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

КОРЕН = Path(__file__).resolve().parents[2]
if str(КОРЕН) not in sys.path:
    sys.path.insert(0, str(КОРЕН))

#: Колко може да се разминава размер, за да е „същият“. Котите са цели
#: сантиметри; всичко над това е друг размер.
ДОПУСК_CM = 0.05

ПРОТИВОРЕЧИЕ = "ПРОТИВОРЕЧИЕ"
ДЕСЕТИЧНИ = "ДЕСЕТИЧНИ_РАМЕНА"
СЪВПАДА = "СЪВПАДА"
СПРЯНА = "СПРЯНА"
ПРОИЗХОД = "ПРОИЗХОД"
БЕЗ_ПРОИЗХОД = "БЕЗ_ПРОИЗХОД"


@dataclass(frozen=True)
class Находка:
    вид: str
    handle: str
    надпис: str
    текст: str


def размери_от_бележка(бележка: str) -> list[float] | None:
    """„130/20“ → [130, 20]. Само когато ЦЯЛАТА бележка е такъв вектор.

    Свободен текст („двойно затворено стреме“) не е размер — ``None``.
    """

    чисто = str(бележка or "").strip()
    if not re.fullmatch(r"\d+(?:[.,]\d+)?(?:\s*/\s*\d+(?:[.,]\d+)?)+", чисто):
        return None
    return [float(ч.replace(",", ".")) for ч in re.split(r"\s*/\s*", чисто)]


def _еднакви(a: Sequence[float], b: Sequence[float]) -> bool:
    # Редът на рамената зависи от посоката на обхождане — сравнява се наборът.
    return len(a) == len(b) and all(
        abs(x - y) <= ДОПУСК_CM for x, y in zip(sorted(a), sorted(b))
    )


def сравни(
    бележки: Mapping[str, Mapping[str, Any]],
    одобрени: Mapping[str, Mapping[str, Any]],
    спрени: Iterable[str],
) -> list[Находка]:
    """Всяка бележка срещу решението на портата; плюс десетичните рамена.

    ``одобрени``: handle → {"text", "shape_code", "segments"} и по желание
    "source" (кой доказател е дал размерите) и "text_handles" (котите).
    Огънат прът без проследим източник е ``БЕЗ_ПРОИЗХОД`` — не се брои за
    потвърден (умението „Преглед на чертеж“, приемни критерии).
    """

    спрени = set(спрени)
    находки: list[Находка] = []
    for handle, б in sorted(бележки.items()):
        надпис = str(б.get("text") or handle)
        поз = одобрени.get(handle)
        форма = str(б.get("master_code") or "")
        размери = размери_от_бележка(str(б.get("note") or ""))
        if поз is None:
            if handle in спрени and (форма or размери):
                находки.append(Находка(СПРЯНА, handle, надпис, "спряна от портата — не стига до Excel"))
            continue
        if форма and форма != поз["shape_code"]:
            находки.append(Находка(
                ПРОТИВОРЕЧИЕ, handle, надпис,
                f"форма: собственикът {форма}, програмата {поз['shape_code']}",
            ))
            continue
        if размери is not None:
            if _еднакви(размери, поз["segments"]):
                находки.append(Находка(СЪВПАДА, handle, надпис, f"размери {поз['segments']}"))
            else:
                находки.append(Находка(
                    ПРОТИВОРЕЧИЕ, handle, надпис,
                    f"размери: собственикът {размери}, програмата {list(поз['segments'])}",
                ))
            continue
        if форма:
            находки.append(Находка(СЪВПАДА, handle, надпис, f"форма {форма}"))
    for handle, поз in sorted(одобрени.items()):
        if len(поз["segments"]) > 1 and any(
            abs(с - round(с)) > ДОПУСК_CM for с in поз["segments"]
        ):
            находки.append(Находка(
                ДЕСЕТИЧНИ, handle, str(поз.get("text") or handle),
                f"{поз['shape_code']} {list(поз['segments'])} — рамената не са цели сантиметри",
            ))
        if len(поз["segments"]) > 1 and "source" in поз:
            източник = str(поз.get("source") or "")
            коти = list(поз.get("text_handles") or ())
            if източник:
                находки.append(Находка(
                    ПРОИЗХОД, handle, str(поз.get("text") or handle),
                    f"{list(поз['segments'])} от {източник}"
                    + (f", коти {коти}" if коти else ""),
                ))
            else:
                находки.append(Находка(
                    БЕЗ_ПРОИЗХОД, handle, str(поз.get("text") or handle),
                    f"{list(поз['segments'])} — няма записан доказател за размерите",
                ))
    return находки


def _контекст(копие: Path):
    from lion_rebar.dxf.importer import DxfImporter
    from lion_rebar.export.batch_auto_output import build_batch_export_context
    from lion_rebar.export.excel_export import load_pinned_engine
    from lion_rebar.export.frame_decisions import load_frame_decisions
    from lion_rebar.project.data_identity import current_identity
    from lion_rebar.project.manager import ProjectManager
    from lion_rebar.session.layer_classifier import (
        filter_analysis_entities,
        high_confidence_noise_layers,
    )

    управител = ProjectManager()
    проект = управител.open_project(копие)
    engine = load_pinned_engine(проект, None)
    записи = DxfImporter().import_file(управител.source_dxf_path(проект)).entities
    слоеве = json.loads((копие / "working" / "layer_classification.json").read_text(encoding="utf-8"))
    за_анализ = (
        filter_analysis_entities(записи, слоеве)
        if high_confidence_noise_layers(слоеве) else list(записи)
    )
    отчети = sorted(glob.glob(str(копие / "output" / "04_REVIEW_REPORTS" / "*" / "*_geometry_batch.json")))
    if not отчети:
        raise SystemExit(f"Няма пакетен отчет в {копие}")
    отчет = Path(отчети[-1])
    return build_batch_export_context(
        отчет, entities=за_анализ, engine=engine, approved_links={},
        frame_decisions=load_frame_decisions(копие, current_identity(проект)),
    ), json.loads(отчет.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    парсер = argparse.ArgumentParser(prog="python -m tools.recognition_viewer.sverka")
    парсер.add_argument("kopie", type=Path, help="копие на проект след full_path.py")
    парсер.add_argument("--belezhki", type=Path, default=КОРЕН / "output" / "viewer" / "belezhki_operator.json")
    а = парсер.parse_args(argv)

    бележки_файл = json.loads(а.belezhki.read_text(encoding="utf-8"))
    контекст, отчет = _контекст(а.kopie)
    самоличност = (отчет.get("data_identity") or {}).get("source_sha256") or ""
    if бележки_файл.get("source_sha256") != самоличност:
        print("ОТКАЗ: бележките са за друг чертеж.", file=sys.stderr)
        return 2
    редове = {str(р.get("annotation_handle") or ""): р for р in (отчет.get("rows") or ())}

    def _произход(handle: str) -> dict:
        доказателство = (редове.get(handle) or {}).get("production_segment_evidence") or {}
        return {
            "source": str(доказателство.get("source") or ""),
            "text_handles": list(доказателство.get("chain_dimension_text_handles") or ()),
        }

    одобрени = {
        п.annotation_handle: {
            "text": п.annotation_text,
            "shape_code": п.shape_code,
            "segments": tuple(float(с) for с in п.segment_lengths_cm),
            **_произход(п.annotation_handle),
        }
        for п in контекст.positions
    }
    спрени = [б.annotation_handle for б in контекст.blockers]
    находки = сравни(бележки_файл.get("notes") or {}, одобрени, спрени)
    брой: dict[str, int] = {}
    for н in находки:
        брой[н.вид] = брой.get(н.вид, 0) + 1
        print(f"{н.вид:<17} {н.handle:<6} {н.надпис:<18} {н.текст}")
    print(f"одобрени {len(одобрени)} · спрени {len(спрени)} · бележки {len(бележки_файл.get('notes') or {})} · {брой}")
    return 1 if брой.get(ПРОТИВОРЕЧИЕ) or брой.get(ДЕСЕТИЧНИ) or брой.get(БЕЗ_ПРОИЗХОД) else 0


if __name__ == "__main__":
    raise SystemExit(main())
