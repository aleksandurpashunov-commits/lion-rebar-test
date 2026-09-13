"""Заключен holdout пробег — един и същ DXF, независимо върху всяко дърво.

Използване:  python3 run_holdout.py <release_root> <out_dir> <label>
"""
import csv
import collections
import json
import sys
from pathlib import Path

ROOT = sys.argv[1]
OUT = Path(sys.argv[2])
LABEL = sys.argv[3]
DXF = "/mnt/user-data/uploads/Test_v_0_1_43_1.dxf"

sys.path.insert(0, "/home/claude/work/shim")
sys.path.insert(0, ROOT)

from lion_rebar.dxf.importer import DxfImporter  # noqa: E402
from lion_rebar.dxf.batch_geometry_report import build_batch_geometry_report  # noqa: E402
from lion_rebar.dxf.recognition_policy import RecognitionPolicy  # noqa: E402
from lion_rebar.engine.package_io import inspect_engine_bundle  # noqa: E402

engine_path = next(Path(ROOT, "engine_updates").glob("*v0.3.13*.lionengine"))
engine, _m, _n = inspect_engine_bundle(engine_path)

res = DxfImporter().import_file(Path(DXF))
OUT.mkdir(parents=True, exist_ok=True)
report = build_batch_geometry_report(
    res.entities, OUT, project_name=f"HOLDOUT_{LABEL}",
    shape_definitions=engine.shapes, control_tests=engine.tests,
    policy=RecognitionPolicy.from_settings(engine.settings),
    engine_metadata={"engine_version": engine.version},
)
d = report.to_dict()

csv_path = next(OUT.glob("*/*_geometry_batch.csv"))
with open(csv_path, encoding="utf-8-sig", newline="") as fh:
    rows = list(csv.DictReader(fh))
key = next(c for c in rows[0] if "annotation_handle" in c)

STRONG = ("СИЛЕН", "СИЛЕН ПО ГЕОМЕТРИЯ", "СИЛНО СТРЕМЕ F006")
f006 = [r for r in rows if str(r.get("master_shape_code") or "") == "F006"
        or "СТРЕМЕ" in str(r.get("batch_status") or "")]
prod = collections.Counter(str(r.get("f006_production_status") or "") for r in rows)

metrics = {
    "label": LABEL,
    "app_version": (Path(ROOT) / "lion_rebar/config/constants.py").read_text(
        encoding="utf-8").split('APP_VERSION = "')[1].split('"')[0],
    "total_parsed_rows": len(rows),
    "total_annotations": d.get("total_annotations"),
    "processed_annotations": d.get("processed_annotations"),
    "actionable_positions": d.get("actionable_count"),
    "strong_plain": d.get("strong_count"),
    "strong_geometry": d.get("geometry_strong_count"),
    "strong_f006": d.get("f006_strong_count"),
    "strong_total": (d.get("strong_count", 0) + d.get("geometry_strong_count", 0)
                     + d.get("f006_strong_count", 0)),
    "manual_review": d.get("review_count"),
    "risk": d.get("risk_count"),
    "no_candidate": d.get("no_candidate_count"),
    "errors": d.get("error_count"),
    "ambiguous": d.get("master_ambiguity_count"),
    "ownership_conflicts": d.get("ownership_conflict_count"),
    "f006_total": len(f006),
    "f006_strong_rows": sum(1 for r in f006 if str(r.get("batch_status")) in STRONG),
    "f006_manual_review": sum(1 for r in f006
                              if str(r.get("batch_status")) == "ЗА РЪЧЕН ПРЕГЛЕД"),
    "f006_production_exact": prod.get("F006_PRODUCTION_EXACT", 0),
    "f006_production_derived": prod.get("F006_PRODUCTION_DERIVED", 0),
    "f006_production_review": prod.get("F006_PRODUCTION_REVIEW", 0),
    "production_export_blocked": sum(1 for r in rows
                                     if str(r.get("production_export_blocked")).lower()
                                     in ("true", "1")),
    "asymmetric_hook_cases": sum(1 for r in rows
                                 if str(r.get("f006_asymmetric_hooks")).lower()
                                 in ("true", "1")),
}
if metrics["actionable_positions"]:
    metrics["strong_rate_percent"] = round(
        metrics["strong_total"] / metrics["actionable_positions"] * 100, 2)

rowmap = {r[key]: {"text": r.get("annotation_text", ""),
                   "status": r.get("batch_status", ""),
                   "master": r.get("master_shape_code", ""),
                   "production": r.get("f006_production_status", ""),
                   "blockers": r.get("matcher_blocker_codes", ""),
                   "review_reasons": r.get("review_reason_codes", "")}
          for r in rows}

(OUT / "metrics.json").write_text(
    json.dumps(metrics, ensure_ascii=False, indent=1), encoding="utf-8")
(OUT / "rows.json").write_text(
    json.dumps(rowmap, ensure_ascii=False, sort_keys=True), encoding="utf-8")
print(json.dumps(metrics, ensure_ascii=False, indent=1))
