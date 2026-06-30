from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime

from filelock import FileLock, Timeout
from PySide6.QtWidgets import QApplication

from recallary.config import Settings
from recallary.gui.main_window import MainWindow


def _write_ready(settings: Settings, token: str) -> None:
    settings.gui_ready_path.write_text(
        "\n".join(
            [
                f"token={token}",
                f"pid={os.getpid()}",
                f"timestamp={datetime.now(UTC).isoformat(timespec='seconds')}",
                "",
            ]
        ),
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ready-token", required=True)
    args, qt_args = parser.parse_known_args(argv)

    settings = Settings.from_root()
    settings.configure_local_storage()

    lock = FileLock(settings.app_lock_path, timeout=0)
    try:
        lock.acquire()
    except Timeout:
        return 3

    try:
        application = QApplication.instance() or QApplication([sys.argv[0], *qt_args])
        window = MainWindow(settings)
        window.show()
        application.processEvents()
        _write_ready(settings, args.ready_token)
        return int(application.exec())
    finally:
        lock.release()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
