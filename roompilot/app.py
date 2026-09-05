from __future__ import annotations

import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys
import time
import traceback

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication

from . import __version__
from .controller import Bridge
from .storage import ProjectStore


def main(argv=None):
    parser = argparse.ArgumentParser(description="RoomPilot — 空間校正工作室")
    parser.add_argument("--data-dir", type=Path, help="自訂本地專案資料夾")
    parser.add_argument("--import", dest="import_path", type=Path, help="匯入量測至新專案")
    parser.add_argument("--project-bundle", type=Path, help="開啟可攜專案副本")
    parser.add_argument("--open-project", help="開啟已儲存專案 ID")
    parser.add_argument("--screenshot", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--self-test-file", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--report", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.self_test_file:
        if not args.report:
            parser.error("--self-test-file requires --report")
        try:
            from .diagnostics import check_sample
            report = check_sample(args.self_test_file)
        except Exception:
            report = {"passed": False, "error": traceback.format_exc()}
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0 if report["passed"] else 1
    QQuickStyle.setStyle("Basic")
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("RoomPilot")
    app.setOrganizationName("RoomPilot")
    app.setApplicationVersion(__version__)
    # Qt's offscreen Windows platform has no system font enumeration.
    # Normal desktop launches use Windows font discovery automatically.
    if os.environ.get("QT_QPA_PLATFORM") == "offscreen" and sys.platform == "win32":
        font_dir = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        for filename in ("msjh.ttc", "msjhbd.ttc", "segoeui.ttf", "seguisym.ttf"):
            QFontDatabase.addApplicationFont(str(font_dir / filename))
    app.setFont(QFont("Microsoft JhengHei UI", 10))
    store = ProjectStore(args.data_dir)
    log = RotatingFileHandler(store.directory / "roompilot.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, handlers=[log], format="%(asctime)s %(levelname)s %(message)s")
    logging.info("RoomPilot %s starting", __version__)
    bridge = Bridge(store)
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("bridge", bridge)
    engine.warnings.connect(lambda messages: [logging.warning(str(item.toString())) for item in messages])
    path = Path(__file__).parent / "ui" / "Main.qml"
    engine.load(QUrl.fromLocalFile(str(path.resolve())))
    if not engine.rootObjects():
        logging.error("Could not load interface")
        bridge.shutdown()
        return 1
    app.aboutToQuit.connect(bridge.shutdown)
    if args.open_project:
        bridge.selectProject(args.open_project)
    elif args.project_bundle:
        try:
            project = store.import_bundle(args.project_bundle)
            bridge.selectProject(project["id"])
        except Exception as exc:
            bridge._message = str(exc)
            bridge._message_kind = "error"
            bridge._refresh()
    elif args.import_path:
        bridge.createProject(args.import_path.stem, "")
        QTimer.singleShot(300, lambda: bridge.importPath(str(args.import_path), "baseline", ""))
    elif store.list_projects():
        bridge.selectProject(store.list_projects()[0]["id"])
    if args.screenshot:
        capture_started = time.monotonic()
        def capture():
            if bridge._job and time.monotonic() - capture_started < 60:
                QTimer.singleShot(300, capture)
                return
            args.screenshot.parent.mkdir(parents=True, exist_ok=True)
            image = engine.rootObjects()[0].grabWindow()
            image.save(str(args.screenshot))
            app.quit()
        QTimer.singleShot(1800, capture)
    result = app.exec()
    # Destroy QML objects before the context property is released.
    del engine
    return result


if __name__ == "__main__":
    raise SystemExit(main())
