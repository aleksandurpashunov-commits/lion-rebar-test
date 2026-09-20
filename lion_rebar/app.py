from __future__ import annotations

import sys
from pathlib import Path

from lion_rebar.utils.app_paths import AppPaths
from lion_rebar.utils.logging_setup import configure_logging


def main() -> int:
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
    except ImportError:
        print(
            "Липсва PySide6. Стартирай INSTALL_LION_REBAR.bat, след което START_LION_REBAR.bat.",
            file=sys.stderr,
        )
        return 2

    from lion_rebar.engine.repository import EngineRepository
    from lion_rebar.session import SessionService
    from lion_rebar.ui.main_window import MainWindow

    paths = AppPaths.default()
    logger = configure_logging(paths.app_logs / "application.log")
    try:
        repository = EngineRepository(paths.engine_registry)
        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            bundle_root = Path(sys._MEIPASS)
        else:
            bundle_root = Path(__file__).resolve().parents[1]
        # Пакетираният engine, който тази версия на приложението носи.
        # Инсталира се САМО от изрично изброен предшественик и само след
        # структурна проверка плюс всички контролни тестове
        # (``install_trusted_bundled_engine``). Машина, която вече е на
        # целевата версия, не се пипа; публикуван файл със същата версия се
        # запазва, вместо да бъде презаписан.
        #
        # 0.3.15 връща вярната самоличност на отвореното петсегментно стреме
        # (F009 активна, F010 -> alias). Предшествениците от 0.3.12 и 0.3.13
        # също се допускат, за да не остане машина без път напред; проектът
        # после се мигрира от таблицата ``_KNOWN_ENGINE_CORRECTIONS``, която
        # за тях връща за преглед и F003/F005 от поправката на 0.3.14.
        bundled_engine = (
            bundle_root
            / "engine_updates"
            / "lion-rebar-master_v0.3.15_F009_OPEN_STIRRUP_IDENTITY_PRODUCTION_DRAFT.lionengine"
        )
        if bundled_engine.is_file():
            repository.install_trusted_bundled_engine(
                bundled_engine,
                allowed_current_versions={"0.0.0", "0.3.12", "0.3.13", "0.3.14"},
                expected_target_version="0.3.15",
            )
    except Exception as exc:
        app = QApplication(sys.argv)
        QMessageBox.critical(None, "LION REBAR", f"Engine registry не може да се отвори:\n{exc}")
        return 3

    app = QApplication(sys.argv)
    app.setApplicationName("LION REBAR DESKTOP")
    app.setOrganizationName("Lion Build Ltd.")
    session_service = SessionService(paths)
    window = MainWindow(repository, logger, session_service=session_service)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
