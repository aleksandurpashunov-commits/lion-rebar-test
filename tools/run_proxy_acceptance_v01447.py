from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from lion_rebar import __version__
from lion_rebar.dxf.batch_geometry_report import build_batch_geometry_report
from lion_rebar.dxf.importer import DxfImporter
from lion_rebar.engine.package_io import inspect_engine_bundle
from lion_rebar.utils.files import sha256_file


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENGINE = (
    ROOT
    / "engine_updates"
    / "lion-rebar-master_v0.3.12_POSITION_KEY_F001_CALIBRATION_PRODUCTION_DRAFT.lionengine"
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reproduce the v0.1.44.7 ACAD proxy acceptance report."
    )
    parser.add_argument("--dxf", type=Path, required=True)
    parser.add_argument("--engine", type=Path, default=DEFAULT_ENGINE)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-proxy", type=int)
    args = parser.parse_args()

    engine, manifest, _names = inspect_engine_bundle(args.engine)
    imported = DxfImporter().import_file(args.dxf)
    args.output.mkdir(parents=True, exist_ok=True)
    report = build_batch_geometry_report(
        imported.entities,
        args.output,
        project_name=f"PROXY_ACCEPTANCE_V{__version__.replace('.', '')}",
        shape_definitions=engine.shapes,
        control_tests=engine.tests,
        policy=engine.settings,
        engine_metadata={
            "engine_id": engine.engine_id,
            "engine_version": engine.version,
            "engine_content_sha256": manifest.get("engine_sha256"),
        },
        checkpoint_every=50,
    )
    payload = json.loads(report.json_path.read_text(encoding="utf-8"))
    proxy_rows = [
        row for row in payload["rows"] if row.get("proxy_parent_provenance")
    ]
    result = {
        "schema": "lion-rebar/proxy-acceptance@1",
        "app_version": __version__,
        "engine_version": engine.version,
        "source_dxf": str(args.dxf),
        "source_sha256": sha256_file(args.dxf),
        "import_summary": imported.summary.to_dict(),
        "batch_summary": payload["summary"],
        "proxy_resolved_rows": len(proxy_rows),
        "proxy_master_counts": dict(
            sorted(Counter(row.get("master_shape_code") for row in proxy_rows).items())
        ),
        "report_json": str(report.json_path),
        "report_zip": str(report.zip_path),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))

    failures: list[str] = []
    if args.expected_proxy is not None and len(proxy_rows) != args.expected_proxy:
        failures.append(
            f"proxy rows {len(proxy_rows)} != expected {args.expected_proxy}"
        )
    if imported.summary.proxy_unresolved_annotation_count:
        failures.append(
            f"unresolved proxy annotations: {imported.summary.proxy_unresolved_annotation_count}"
        )
    if report.error_count:
        failures.append(f"batch errors: {report.error_count}")
    if report.master_ambiguity_count:
        failures.append(f"MASTER ambiguities: {report.master_ambiguity_count}")
    if report.ownership_conflict_count:
        failures.append(f"ownership conflicts: {report.ownership_conflict_count}")
    if failures:
        print("ACCEPTANCE FAILED: " + "; ".join(failures))
        return 1
    print("ACCEPTANCE PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
