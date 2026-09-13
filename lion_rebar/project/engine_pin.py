"""Точно описание на разминаването между engine на проекта и активния engine.

P0-4 (наблюдавано на Windows): диалогът твърдеше „друга engine версия“,
докато и двете страни показваха ``v0.3.13``. Реалното разминаване е било
по SHA/подпис, но съобщението показваше само версията.

Модулът е чист (без Qt), за да може да се тества без UI среда.
"""
from __future__ import annotations

_SHORT = 16


def _short(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return "(липсва)"
    return f"{text[:_SHORT]}…" if len(text) > _SHORT else text


def describe_engine_pin_mismatch(
    *,
    project_engine_id: str | None,
    project_version: str | None,
    project_sha256: str | None,
    current_engine_id: str | None,
    current_version: str | None,
    current_sha256: str | None,
) -> list[dict]:
    """Връща списък с РЕАЛНИТЕ разминавания; празен списък = няма разминаване.

    SHA се сравнява само когато проектът е записал такъв — стари проекти без
    записан подпис не бива да произвеждат предупреждение.
    """

    differences: list[dict] = []
    if str(project_engine_id or "") != str(current_engine_id or ""):
        differences.append({
            "field": "engine_id",
            "label": "Различен engine",
            "project": str(project_engine_id or "(липсва)"),
            "current": str(current_engine_id or "(липсва)"),
        })
    if str(project_version or "") != str(current_version or ""):
        differences.append({
            "field": "version",
            "label": "Различна engine версия",
            "project": str(project_version or "(липсва)"),
            "current": str(current_version or "(липсва)"),
        })
    recorded = str(project_sha256 or "").strip()
    if recorded and recorded != str(current_sha256 or "").strip():
        differences.append({
            "field": "content_sha256",
            "label": "Същата версия, но различен engine подпис (SHA-256)",
            "project": _short(recorded),
            "current": _short(current_sha256),
        })
    return differences
