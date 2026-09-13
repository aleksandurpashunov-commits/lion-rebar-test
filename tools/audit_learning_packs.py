from __future__ import annotations

import argparse
import json
from pathlib import Path

from lion_rebar.session.learning_migration import audit_learning_packs, report_markdown


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only LION REBAR Learning Pack migration audit")
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    paths: list[Path] = []
    for candidate in args.inputs:
        if candidate.is_dir():
            paths.extend(sorted(candidate.glob("*.zip")))
        else:
            paths.append(candidate)
    report = audit_learning_packs(paths)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    args.output.with_suffix(".md").write_text(report_markdown(report), encoding="utf-8")
    print(json.dumps(report["totals"], ensure_ascii=False, sort_keys=True))
    return 0 if not report["errors"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
