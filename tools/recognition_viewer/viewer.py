# -*- coding: utf-8 -*-
"""Визуален преглед на разпознаването — сглобяване на картината.

ИНСТРУМЕНТ САМО ЗА ЧЕТЕНЕ. Не пипа Engine, Matcher, MASTER, праговете,
правилата и експорта. Чете чертежа през собствения вносител на програмата и
пакетния отчет, и рисува какво е решил Matcher-ът за всяка позиция.
"""
from __future__ import annotations

import hashlib
import html
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from tools.recognition_viewer import geometry as геом

ЗЕЛЕНО = "#16a34a"
ЖЪЛТО = "#d97706"
ЧЕРВЕНО = "#dc2626"
СИНЬО = "#0284c7"
СИВО = "#c7cbd1"
ФОН = "#ffffff"

ВИДОВЕ = {
    "зелено": ("точен top-1, без флаг", ЗЕЛЕНО),
    "жълто": ("двусмислие или флаг", ЖЪЛТО),
    "червено": ("няма кандидат, конфликт с размерите или MANUAL", ЧЕРВЕНО),
    "не-позиция": ("надпис без дължина или без бройка — не е позиция", СИНЬО),
    "несвързано": ("геометрия или текст без позиция", СИВО),
}


@dataclass
class Позиция:
    handle: str
    вид: str
    text: str
    count: Any
    diameter: Any
    declared: Any
    shape_code: str
    shape_name: str
    batch_status: str
    analysis_status: str
    segments: list[Any]
    turns: list[Any]
    geometry_length: Any
    numeric_difference: Any
    candidate_handles: list[str]
    candidate_layer: str
    candidate_score: Any
    ambiguous: list[str]
    reasons: list[str]
    infos: list[str]
    notes: list[str]
    gate_reasons: list[str]
    frame: str
    top: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "handle": self.handle, "вид": self.вид, "text": self.text,
            "count": self.count, "diameter": self.diameter, "declared": self.declared,
            "shape_code": self.shape_code, "shape_name": self.shape_name,
            "batch_status": self.batch_status, "analysis_status": self.analysis_status,
            "segments": self.segments, "turns": self.turns,
            "geometry_length": self.geometry_length,
            "numeric_difference": self.numeric_difference,
            "candidate_layer": self.candidate_layer,
            "candidate_score": self.candidate_score,
            "ambiguous": self.ambiguous, "reasons": self.reasons,
            "infos": self.infos, "notes": self.notes,
            "gate_reasons": self.gate_reasons, "frame": self.frame,
            "top": self.top,
        }


def sha256_на(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as поток:
        for блок in iter(lambda: поток.read(1 << 20), b""):
            h.update(блок)
    return h.hexdigest()


def прочети_отчет(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def спрени_от_портата(summary_path: Path | None) -> dict[str, list[str]]:
    """``handle`` → причини, от ``gate.blocked_positions`` на ``full_path``."""

    if summary_path is None or not Path(summary_path).is_file():
        return {}
    данни = json.loads(Path(summary_path).read_text(encoding="utf-8"))
    спрени: dict[str, list[str]] = {}
    for ред in (данни.get("gate") or {}).get("blocked_positions") or ():
        handle = str(ред.get("handle") or "")
        причини = [
            str(п[0]) if isinstance(п, (list, tuple)) else str(п)
            for п in (ред.get("reasons") or ())
        ]
        if handle:
            спрени[handle] = причини
    return спрени


def _списък(стойност: Any) -> list[Any]:
    return list(стойност) if isinstance(стойност, (list, tuple)) else []


def класифицирай(ред: Mapping[str, Any], gate: Sequence[str]) -> str:
    """Цветът на една позиция. Само чете полетата на отчета."""

    клас = str(ред.get("annotation_class") or "")
    статус = str(ред.get("batch_status") or "")
    ако_спряна = bool(gate)
    причини = [str(к) for к in _списък(ред.get("review_reason_codes"))]
    двусмислие = [str(к) for к in _списък(ред.get("master_ambiguous_shape_codes"))]

    if клас != "ACTIONABLE":
        return "не-позиция"
    if статус == "НЯМА КАНДИДАТ":
        return "червено"
    if str(ред.get("matcher_disposition") or "").upper() == "MANUAL" and причини:
        return "червено"
    тежки = {
        "MASTER_LENGTH_CHECK", "MISSING_ACTIVE_MASTER", "MISSING_GEOMETRY",
        "PRODUCTION_CONTRADICTS_PRINTED", "MASTER_CONFLICT",
    }
    if ако_спряна and (set(gate) & тежки):
        return "червено"
    if двусмислие or причини or статус == "ЗА РЪЧЕН ПРЕГЛЕД":
        return "жълто"
    if ако_спряна:
        return "жълто"
    return "зелено"


def позиции_от(
    отчет: Mapping[str, Any],
    спрени: Mapping[str, Sequence[str]],
) -> list[Позиция]:
    резултат: list[Позиция] = []
    for ред in отчет.get("rows") or ():
        handle = str(ред.get("annotation_handle") or "")
        gate = list(спрени.get(handle) or ())
        top = []
        for алтернатива in _списък(ред.get("alternative_candidates"))[:2]:
            if isinstance(алтернатива, Mapping):
                top.append(
                    {
                        "rank": алтернатива.get("rank"),
                        "layer": алтернатива.get("layer"),
                        "score": алтернатива.get("score"),
                        "confidence": алтернатива.get("confidence"),
                        "basis": алтернатива.get("basis"),
                        "handles": _списък(алтернатива.get("handles"))[:4],
                    }
                )
        резултат.append(
            Позиция(
                handle=handle,
                вид=класифицирай(ред, gate),
                text=str(ред.get("annotation_text") or ""),
                count=ред.get("annotation_count"),
                diameter=ред.get("diameter_mm"),
                declared=ред.get("declared_length_cm"),
                shape_code=str(ред.get("master_shape_code") or ""),
                shape_name=str(ред.get("master_shape_name") or ""),
                batch_status=str(ред.get("batch_status") or ""),
                analysis_status=str(ред.get("analysis_status") or ""),
                segments=[round(float(с), 2) for с in _списък(ред.get("canonical_segment_lengths_cm"))],
                turns=[round(float(с), 1) for с in _списък(ред.get("canonical_turn_angles_deg"))],
                geometry_length=ред.get("effective_geometry_length_cm"),
                numeric_difference=ред.get("numeric_difference"),
                candidate_handles=[str(х) for х in _списък(ред.get("candidate_handles"))],
                candidate_layer=str(ред.get("candidate_layer") or ""),
                candidate_score=ред.get("candidate_score"),
                ambiguous=[str(к) for к in _списък(ред.get("master_ambiguous_shape_codes"))],
                reasons=[str(к) for к in _списък(ред.get("review_reason_codes"))],
                infos=[str(к) for к in _списък(ред.get("review_info_codes"))],
                notes=[str(к) for к in _списък(ред.get("candidate_notes"))][:4],
                gate_reasons=gate,
                frame=str(ред.get("frame_title") or ред.get("frame_category_code") or ""),
                top=top,
            )
        )
    return резултат


@dataclass
class Картина:
    svg: str
    width: int
    height: int
    summary: dict[str, int]
    инфо: dict[str, dict[str, Any]]


def построй(
    записи: Sequence[Any],
    позиции: Sequence[Позиция],
    *,
    ширина: int = 2400,
) -> Картина:
    """Чертежът с наложени резултати, като SVG."""

    цвят_на_handle: dict[str, str] = {}
    инфо_на_handle: dict[str, str] = {}
    for позиция in позиции:
        for handle in [позиция.handle, *позиция.candidate_handles]:
            предишен = цвят_на_handle.get(handle)
            # Червеното бие жълтото, жълтото бие зеленото: ако един обект е
            # заявен от две позиции, по-тежкото състояние трябва да се вижда.
            ред = {"зелено": 0, "не-позиция": 1, "жълто": 2, "червено": 3}
            if предишен is None or ред[позиция.вид] > ред[предишен]:
                цвят_на_handle[handle] = позиция.вид
                инфо_на_handle[handle] = позиция.handle

    точка_на_handle: dict[str, tuple[float, float]] = {}
    for запис in записи:
        g = getattr(запис, "geometry", None) or {}
        т = g.get("insert") or g.get("position")
        if isinstance(т, (list, tuple)) and len(т) >= 2:
            try:
                точка_на_handle[str(getattr(запис, "handle", ""))] = (
                    float(т[0]), -float(т[1])
                )
            except (TypeError, ValueError):
                pass

    нарисувани = геом.всички(записи, мащаб=1.0)
    кутия = геом.обхват(нарисувани)
    if кутия is None:
        raise ValueError("Чертежът няма нито един обект за рисуване.")
    x0, y0, x1, y1 = кутия
    w = max(x1 - x0, 1e-6)
    h = max(y1 - y0, 1e-6)
    поле = max(w, h) * 0.02
    x0 -= поле; y0 -= поле; x1 += поле; y1 += поле
    w, h = x1 - x0, y1 - y0
    височина = int(ширина * h / w)
    дебелина = max(w, h) / 2400.0

    слоеве: dict[str, list[str]] = {к: [] for к in ВИДОВЕ}
    броене = {к: 0 for к in ВИДОВЕ}
    for н in нарисувани:
        вид = цвят_на_handle.get(н.handle, "несвързано")
        слоеве[вид].append(
            f'<path d="{н.path}" data-h="{html.escape(инфо_на_handle.get(н.handle, ""))}"/>'
        )
    for позиция in позиции:
        броене[позиция.вид] += 1
    броене["несвързано"] = sum(
        1 for н in нарисувани if н.handle not in цвят_на_handle
    )

    части = [
        f'<svg xmlns="http://www.w3.org/2000/svg" id="чертеж" '
        f'viewBox="{x0:.3f} {y0:.3f} {w:.3f} {h:.3f}" '
        f'width="{ширина}" height="{височина}">',
        f'<rect x="{x0:.3f}" y="{y0:.3f}" width="{w:.3f}" height="{h:.3f}" fill="{ФОН}"/>',
        '<g id="сцена">',
    ]
    # Редът е нарочен: сивото отдолу, червеното най-отгоре.
    for вид, дебелина_дял in (
        ("несвързано", 0.6), ("не-позиция", 0.9), ("зелено", 1.6),
        ("жълто", 2.2), ("червено", 2.8),
    ):
        ако = слоеве.get(вид) or []
        if not ако:
            continue
        цвят = ВИДОВЕ[вид][1]
        части.append(
            f'<g class="{вид}" fill="none" stroke="{цвят}" '
            f'stroke-width="{дебелина * дебелина_дял:.5f}" '
            f'stroke-linecap="round" stroke-linejoin="round">'
            + "".join(ако)
            + "</g>"
        )
    # Етикети САМО върху проблемните позиции: иначе картината става нечетима.
    етикети = []
    размер = max(w, h) / 110.0
    for позиция in позиции:
        if позиция.вид not in {"червено", "жълто"}:
            continue
        т = точка_на_handle.get(позиция.handle)
        if т is None:
            continue
        цвят = ВИДОВЕ[позиция.вид][1]
        текст = html.escape(позиция.text[:18])
        x, y = т[0], т[1] - размер * 0.6
        общо = (
            f'x="{x:.2f}" y="{y:.2f}" font-family="Arial" '
            f'font-size="{размер:.3f}" text-anchor="middle"'
        )
        # Ореолът е ОТДЕЛЕН надпис под цветния: Qt не поддържа ``paint-order``
        # и при един надпис белият контур застава ОТГОРЕ, тоест текстът изчезва.
        етикети.append(
            f'<text {общо} fill="none" stroke="#ffffff" '
            f'stroke-width="{размер * 0.28:.3f}" stroke-linejoin="round">{текст}</text>'
        )
        етикети.append(f'<text {общо} fill="{цвят}">{текст}</text>')
    if етикети:
        части.append('<g class="етикети">' + "".join(етикети) + "</g>")
    части.append("</g></svg>")

    инфо = {п.handle: п.to_dict() for п in позиции}
    return Картина(
        svg="".join(части),
        width=ширина,
        height=височина,
        summary=броене,
        инфо=инфо,
    )
