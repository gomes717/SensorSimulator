"""Entry point for the TCC glucose monitoring application."""
import sys
from PyQt6.QtWidgets import QApplication
from main_window import MainWindow


def main() -> None:
    """Create the Qt application, show the main window, and start the event loop."""
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
