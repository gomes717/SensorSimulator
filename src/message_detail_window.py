"""Window that displays all fields of a single data message."""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtCore import Qt


class MessageDetailWindow(QWidget):
    """Top-level window showing every key/value pair of a message dict."""

    def __init__(self, message: dict) -> None:
        """Build the detail view for *message*.

        The window title is derived from the ``id`` key when present,
        falling back to ``user_id``, then a generic label.
        """
        super().__init__()
        title = message.get("id") or message.get("user_id") or "Message"
        self.setWindowTitle(f"Message — {title}")
        self.resize(480, 360)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)

        group = QGroupBox("Message Details")
        form = QFormLayout(group)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)

        for key, value in message.items():
            label = QLabel(str(value))
            label.setWordWrap(True)
            form.addRow(f"<b>{key.replace('_', ' ').capitalize()}:</b>", label)

        scroll.setWidget(group)

        layout = QVBoxLayout(self)
        layout.addWidget(scroll)
