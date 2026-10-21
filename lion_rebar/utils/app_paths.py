from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from lion_rebar.config.constants import APP_DATA_FOLDER
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

    @property
    def learned_knowledge(self) -> Path:
        """Научените правила за прочит — до останалите данни на приложението.

        СВОЙСТВО, а не поле: ``AppPaths`` се сглобява на ръка в тестовете и в
        диагностиката, а ново задължително поле би счупило всяко такова място
        (измерено: 9 провала). Следва ``root``, тоест и ``LION_REBAR_DATA_ROOT``.
        """

        return self.root / "naucheno"

    @classmethod
    def default(cls) -> "AppPaths":
        explicit_root = os.environ.get("LION_REBAR_DATA_ROOT")
        if explicit_root:
            root = Path(explicit_root).expanduser()
        else:
            base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
            if base:
                root = Path(base) / "LionBuild" / APP_DATA_FOLDER
            else:
                root = Path.home() / f".{APP_DATA_FOLDER.lower()}"
        engine_registry = root / "engine_registry"
        app_logs = root / "logs"
        session_root = root / "session_core"
        learning_packs = session_root / "learning_packs"
        for directory in (root, engine_registry, app_logs, session_root,
                          learning_packs, root / "naucheno"):
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
