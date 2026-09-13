from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from lion_rebar.utils.files import ensure_directory


@dataclass(frozen=True)
class AppPaths:
    root: Path
    engine_registry: Path
    app_logs: Path
    settings_file: Path
    session_root: Path
    session_db: Path
    session_state_file: Path
    session_recovery_file: Path
    learning_packs: Path

    @classmethod
    def default(cls) -> "AppPaths":
        explicit_root = os.environ.get("LION_REBAR_DATA_ROOT")
        if explicit_root:
            root = Path(explicit_root).expanduser()
        else:
            base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
            if base:
                root = Path(base) / "LionBuild" / "LionRebarDesktop"
            else:
                root = Path.home() / ".lion_rebar_desktop"
        engine_registry = root / "engine_registry"
        app_logs = root / "logs"
        session_root = root / "session_core"
        learning_packs = session_root / "learning_packs"
        for directory in (root, engine_registry, app_logs, session_root, learning_packs):
            ensure_directory(directory)
        return cls(
            root=root,
            engine_registry=engine_registry,
            app_logs=app_logs,
            settings_file=root / "settings.json",
            session_root=session_root,
            session_db=session_root / "lion_rebar_sessions.sqlite3",
            session_state_file=session_root / "current_session.json",
            session_recovery_file=session_root / "SESSION_RECOVERY_LATEST.json",
            learning_packs=learning_packs,
        )
