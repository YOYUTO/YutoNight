"""Entry point for the bundled Windows application; contains no user settings."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys


def self_check(destination):
    import numpy as np
    from PySide6.QtCore import qVersion
    from PySide6 import QtMultimedia
    from yutonight.beauty import PRESETS, process_frame, _load_face_detector
    from yutonight.doctor import report

    data = report()
    detector = _load_face_detector()
    sample = np.zeros((16, 16, 3), np.uint8)
    data.update({
        "frozen": bool(getattr(sys, "frozen", False)),
        "haar_cascade": not detector.empty(),
        "qt_version": qVersion(),
        "qt_multimedia": QtMultimedia.QMediaPlayer is not None,
        "beauty_import": np.array_equal(process_frame(sample, PRESETS["原画"], []), sample),
    })
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if data["haar_cascade"] and data["beauty_import"] else 1


def smoke_test():
    # Offscreen exercises Qt's plugin loading without opening a user window.
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from yutonight.ui import MainWindow

    app = QApplication.instance() or QApplication([sys.argv[0]])
    window = MainWindow()
    window.show()

    def finish():
        window.dirty = False
        window.close()
        app.quit()

    QTimer.singleShot(3000, finish)
    return app.exec()


def main():
    parser = argparse.ArgumentParser(description="YutoNight local subtitle application")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--self-check", metavar="JSON_PATH")
    group.add_argument("--smoke-test", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        destination = Path(args.self_check)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text('{"self_check_started": true}', encoding="utf8")
        try:
            return self_check(destination)
        except Exception as exc:
            destination.write_text(json.dumps({"self_check_error": type(exc).__name__,
                                              "detail": str(exc)}, ensure_ascii=False, indent=2),
                                   encoding="utf8")
            return 1
    if args.smoke_test:
        return smoke_test()
    from yutonight.ui import main as ui_main
    return ui_main()


if __name__ == "__main__":
    raise SystemExit(main())
