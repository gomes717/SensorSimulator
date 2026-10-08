"""Entry point for the GlucoEcho glucose monitoring application."""

import sys

from PyQt6.QtWidgets import QApplication

from gui import branding
from gui.main_window import MainWindow
from gui.theme import apply_theme
from models import app_settings


def main() -> None:
    """Create the Qt application, show the main window, and start the event loop."""
    app = QApplication(sys.argv)
    branding.install(app)
    apply_theme(app, app_settings.load_theme())
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
