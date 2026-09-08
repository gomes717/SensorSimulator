"""Entry point for the TCC glucose monitoring application."""

import sys

from PyQt6.QtWidgets import QApplication

from graphic.main_window import MainWindow
from graphic.theme import apply_theme
from models import app_settings


def main() -> None:
    """Create the Qt application, show the main window, and start the event loop."""
    app = QApplication(sys.argv)
    apply_theme(app, app_settings.load_theme())
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
