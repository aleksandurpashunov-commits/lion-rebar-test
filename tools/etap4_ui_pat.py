"""Истинският потребителски път ПРЕЗ ПРОЗОРЕЦА, воден програмно.

Прави точно това, което прави операторът, но без ръка:

  отваряне на проект → импорт на DXF → пакетен анализ → запис → затваряне
  → (нов процес) повторно отваряне → експорт

и проверява, че обучението е работило през НОРМАЛНИЯ път: приложени знания,
нови кандидати, приети правила и записана памет.

Модалните диалози се отговарят автоматично — иначе прозорецът чака човек.
Това е единственото изкуствено нещо тук; всичко останало минава през същите
методи, които викат бутоните.

Пуска се на два етапа:
  ``--etap 1``  отваря, анализира, записва и излиза;
  ``--etap 2``  отваря наново (НОВ процес) и проверява, че наученото е налице.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time

КОРЕН = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(КОРЕН))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _без_диалози(*, презапис: bool = False, файл_за_запис: str = ""):
    """Автоматичен отговор на модалните прозорци.

    ``презапис`` отговаря „Да" на въпроса за вече съществуващи изходни файлове.
    ``файл_за_запис`` замества диалога за име на файл. Диалогът за листовете се
    приема С ПОДРАЗБИРАНИЯТА СИ — нищо не се изключва на ръка, за да не бъде
    „отключен" проект чрез скриване на позиции.
    """

    from PySide6.QtWidgets import QMessageBox

    отговор = (QMessageBox.StandardButton.Yes if презапис
               else QMessageBox.StandardButton.No)
    QMessageBox.question = staticmethod(lambda *a, **k: отговор)
    QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
    QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
    QMessageBox.critical = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
    if файл_за_запис:
        from PySide6.QtWidgets import QDialog, QFileDialog

        QFileDialog.getSaveFileName = staticmethod(
            lambda *a, **k: (файл_за_запис, "Excel Workbook (*.xlsx)"))
        from lion_rebar.ui.export_excel_dialog import ExportExcelDialog

        ExportExcelDialog.exec = lambda self: QDialog.DialogCode.Accepted


def _изчакай(app, условие, *, секунди: float, стъпка: float = 0.2) -> bool:
    край = time.time() + секунди
    while time.time() < край:
        app.processEvents()
        if условие():
            return True
        time.sleep(стъпка)
    app.processEvents()
    return bool(условие())


def main() -> int:
    parser = argparse.ArgumentParser(description="Потребителският път през прозореца")
    parser.add_argument("проект")
    parser.add_argument("--etap", type=int, choices=(1, 2, 3), default=1)
    parser.add_argument("--json", type=pathlib.Path, required=True)
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--izhod", default="",
                        help="за --etap 3: къде да запише Excel-а")
    args = parser.parse_args()

    from PySide6.QtWidgets import QApplication

    _без_диалози(презапис=args.etap == 3,
                 файл_за_запис=args.izhod if args.etap == 3 else "")
    app = QApplication.instance() or QApplication(sys.argv[:1])

    from lion_rebar.engine.repository import EngineRepository
    from lion_rebar.learning.policy_bridge import load_store
    from lion_rebar.project.manager import ProjectManager
    from lion_rebar.ui.main_window import MainWindow
    from lion_rebar.utils.app_paths import AppPaths
    from lion_rebar.utils.logging_setup import configure_logging

    корен = pathlib.Path(args.проект)
    отчет: dict = {"етап": args.etap, "pid": os.getpid(), "проект": str(корен)}

    преди = load_store()
    отчет["памет_преди"] = {"път": str(преди.path), "правила": len(преди),
                            "приети": len(преди.accepted()),
                            "приети_ид": [п.rule_id for п in преди.accepted()]}

    пътища = AppPaths.default()
    дневник = configure_logging(пътища.app_logs / "ui_pat.log")
    прозорец = MainWindow(EngineRepository(пътища.engine_registry), дневник)
    прозорец.show()
    app.processEvents()
    отчет["заглавие"] = прозорец.windowTitle()

    # --- отваряне на проекта (същото, което прави „Отвори проект")
    мениджър = ProjectManager()
    проект = мениджър.open_project(корен)
    прозорец._set_project(проект)
    app.processEvents()
    отчет["проект_отворен"] = проект.name

    # --- импорт на DXF (същото, което прави „Импорт DXF", без диалога с отчета)
    dxf = мениджър.source_dxf_path(проект)
    отчет["dxf"] = str(dxf) if dxf else ""
    if dxf and pathlib.Path(dxf).is_file():
        прозорец._load_dxf_from_project(pathlib.Path(dxf), show_report=False)
        app.processEvents()
    внесено = getattr(прозорец, "import_result", None)
    отчет["обекти_след_импорт"] = len(getattr(внесено, "entities", []) or [])
    отчет["обекти_за_анализ"] = len(прозорец._analysis_entities())

    if args.etap == 1:
        # --- пакетен анализ (бутонът „Пакетен анализ + Excel")
        прозорец._last_batch_report_path = None
        прозорец.run_batch_geometry_analysis()
        готов = _изчакай(
            app, lambda: getattr(прозорец, "_last_batch_report_path", None) is not None,
            секунди=args.timeout)
        отчет["анализът_завърши"] = готов
        отчет["отчет_на_анализа"] = str(
            getattr(прозорец, "_last_batch_report_path", "") or "")
        # --- учебният цикъл е в отделна нишка: изчакваме я
        def _нишката_спря() -> bool:
            н = getattr(прозорец, "_learning_thread", None)
            try:
                return н is None or not н.isRunning()
            except RuntimeError:  # изтрита от Qt = приключила
                return True

        приключила = _изчакай(app, _нишката_спря, секунди=240.0)
        отчет["учебната_нишка_приключи"] = приключила

        състояние = getattr(прозорец, "_learning_status", None)
        отчет["обучение"] = състояние.to_dict() if състояние is not None else None
        отчет["ред_за_лентата"] = състояние.short_line() if състояние else ""

        прозорец.save_project()
        app.processEvents()
        отчет["проектът_е_записан"] = True

    if args.etap == 3:
        # --- Финален контрол + Excel/DXF експорт (бутонът „Excel + DXF")
        # Минава през СЪЩИЯ метод, който вика менюто. Работникът е в отделна
        # нишка, затова се чака край, а не само връщането на метода.
        отчет["експорт_поискан_в"] = str(args.izhod)
        # ЗАЩО НЕ СЕ ПРИХВАЩА СИГНАЛЪТ. По-ранна редакция подменяше
        # ``прозорец._export_finished`` със своя функция, за да улови резултата.
        # Qt насочва сигнал към нишката на получателя САМО когато слотът е
        # свързан метод на QObject; обикновена функция няма получател-обект,
        # затова връзката ставаше ПРЯКА и обработчикът се изпълняваше в нишката
        # на работника. А той пипа интерфейса — затваря лентата на напредъка,
        # вика deleteLater(), показва QMessageBox — и процесът увисваше с
        # написани изходни файлове, но без отчет.
        #
        # Измерено (`rezultat/roadmap_completion/ui_sonda.py`, копие на s-10):
        # без подмяна готово за 54,7 s; с подмяна `_прихвани` се изпълнява в
        # нишка Dummy-1 и не се връща.
        #
        # Резултатът не се губи: работникът записва FINAL_EXPORT_MANIFEST.json
        # до изходните файлове и водачът го чете оттам.
        прозорец.run_excel_export()
        app.processEvents()

        def _експортът_спря() -> bool:
            н = getattr(прозорец, "_export_thread", None)
            try:
                return н is None or not н.isRunning()
            except RuntimeError:
                return True

        отчет["експортът_приключи"] = _изчакай(
            app, _експортът_спря, секунди=args.timeout)
        изход = pathlib.Path(args.izhod)
        манифест = изход.parent / "FINAL_EXPORT_MANIFEST.json"
        отчет["експорт_резултат"] = (
            json.loads(манифест.read_text(encoding="utf-8"))
            if манифест.is_file() else None
        )
        отчет["файлове"] = {
            п.name: {"байтове": п.stat().st_size,
                     "променен": time.strftime(
                         "%Y-%m-%dT%H:%M:%S", time.localtime(п.stat().st_mtime))}
            for п in sorted(изход.parent.glob(изход.stem + "*"))
            if п.is_file()
        }
        отчет["лента_на_състоянието"] = прозорец.statusBar().currentMessage()

    след = load_store()
    отчет["памет_след"] = {"път": str(след.path), "правила": len(след),
                           "приети": len(след.accepted()),
                           "приети_ид": [п.rule_id for п in след.accepted()]}
    наблюдения = pathlib.Path(след.path).parent / "nablyudeniya.jsonl"
    отчет["наблюдения"] = (
        len([р for р in наблюдения.read_text(encoding="utf-8").splitlines() if р.strip()])
        if наблюдения.is_file() else 0)

    # Отчетът се записва ПРЕДИ затварянето. Измерено на 28.09.2026: след
    # експорта затварянето на прозореца не се върна и процесът увисна с
    # написани изходни файлове, но без отчет — тоест доказателството се губеше
    # заради стъпка, която нищо не доказва.
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(отчет, ensure_ascii=False, indent=1), encoding="utf-8")

    прозорец.close()
    app.processEvents()
    print("етап %d | pid %d | проект %s" % (args.etap, os.getpid(), отчет.get("проект_отворен")))
    print("   памет преди: %d правила (%d приети) -> след: %d (%d приети)" % (
        отчет["памет_преди"]["правила"], отчет["памет_преди"]["приети"],
        отчет["памет_след"]["правила"], отчет["памет_след"]["приети"]))
    if args.etap == 1:
        print("   анализът завърши:", отчет.get("анализът_завърши"),
              "| учебната нишка:", отчет.get("учебната_нишка_приключи"))
        print("   лента:", отчет.get("ред_за_лентата"))
    print("   наблюдения:", отчет["наблюдения"])
    print("записан:", args.json)
    sys.stdout.flush()
    # Qt понякога не пуска процеса след експорт в отделна нишка. Отчетът вече е
    # на диска, затова изходът е безусловен.
    os._exit(0)


if __name__ == "__main__":
    raise SystemExit(main())
