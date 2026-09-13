from __future__ import annotations

from lion_rebar.config.constants import DEFAULT_CATEGORIES, DEFAULT_POSITION_STATUSES
from lion_rebar.engine.models import CategoryDefinition, EnginePackage


def create_bootstrap_engine() -> EnginePackage:
    """Create a deliberately non-production bootstrap engine.

    It contains governance and sheet/status definitions, but no fabricated MASTER shapes.
    The user must import and publish the actual MASTER HTML/XLSX data.
    """

    return EnginePackage(
        engine_id="lion-rebar-bootstrap",
        title="LION REBAR BOOTSTRAP — IMPORT MASTER BEFORE PRODUCTION",
        version="0.0.0",
        state="PUBLISHED",
        approved_by="SYSTEM",
        approval_note="Bootstrap only. It is not a production rebar engine.",
        production_ready=False,
        categories=[
            CategoryDefinition(
                code=item["code"],
                sheet_name=item["sheet_name"],
                aliases=item["aliases"],
                sort_order=index * 10,
            )
            for index, item in enumerate(DEFAULT_CATEGORIES, start=1)
        ],
        position_statuses=list(DEFAULT_POSITION_STATUSES),
        settings={
            "max_segments": 12,
            "published_snapshots_are_immutable": True,
            "project_pins_engine_version": True,
            "require_all_tests_to_pass_for_production": True,
            "warning": "NO MASTER SHAPES LOADED",
        },
    )
