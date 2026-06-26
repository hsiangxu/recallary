from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from recallary.config import Settings
from recallary.gui.main_window import MainWindow


def main() -> int:
    settings = Settings.from_root()
    settings.configure_local_storage()

    application = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow(settings)
    window.show()
    return int(application.exec())
