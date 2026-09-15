"""The left-hand sensor list — one row per BLE ``user_id``, with an avatar, a
stable ``#n`` id, the latest glucose value and a LOW/HIGH/offline badge.

Pulled out of :class:`MainWindow` (issue 18): the four row dicts
(``_user_items`` / ``_user_ids`` / ``_user_dev`` / ``_offline_users``) and the
five methods that maintained them lived on the god object. Here the widget owns
its own rows and emits :attr:`user_selected` (the resolved ``user_id`` string)
when the selection changes.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import QTreeWidget, QTreeWidgetItem

from gui.avatar import avatar_icon


class UserTree(QTreeWidget):
    user_selected = pyqtSignal(str)  # the row's user_id (or its visible text)

    def __init__(
        self,
        thresholds: dict,
        label_for: Callable[[str, str | None], str] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._thresholds = thresholds
        # Resolves a row's *visible* name from its user_id + the address feeding
        # it. The user_id is fixed at connect time (it keys the history buffers),
        # so a session opened before its name was known keeps a bare address
        # forever; this lets the row show the live, board-layout-resolved label
        # without moving the key underneath the graph history.
        self._label_for = label_for or (lambda user_id, _dev_id: user_id)
        self.setHeaderLabels(["User", "Glucose"])
        self.header().setStretchLastSection(True)
        self.currentItemChanged.connect(self._on_current_changed)

        self._items: dict[str, QTreeWidgetItem] = {}
        self._ids: dict[str, int] = {}  # user_id -> stable small #n
        self._dev: dict[str, str] = {}  # user_id -> BLE address feeding it
        self._offline: set[str] = set()
        self.selected_user: str | None = None

    def set_thresholds(self, thresholds: dict) -> None:
        self._thresholds = thresholds

    # ------------------------------------------------------------------
    # Inbound updates
    # ------------------------------------------------------------------

    def note_message(self, msg: dict) -> None:
        """Update the row for *msg*'s user from a decoded BLE notification.

        Tracks which device feeds the row (for offline marking), clears a
        stale offline badge when traffic resumes, and — for a CGM measurement
        — refreshes the glucose value + LOW/HIGH badge. The row updates
        regardless of run state; it is a live status readout, not plot data.
        """
        user_id = msg.get("user_id")
        if user_id is not None:
            dev_id = msg.get("dev_id")
            if dev_id is not None:
                self._dev[user_id] = dev_id
            if user_id in self._offline:  # a message means it's back
                self._offline.discard(user_id)
                self.set_row_offline(user_id, offline=False)

        if "glucose_value" in msg:
            glucose = msg["glucose_value"]
            item = self.ensure_item(user_id)
            item.setText(1, f"{glucose:.2f}")
            self._update_alert(item, user_id, glucose)

    def mark_device_offline(self, address: str) -> None:
        """A BLE session ended — badge every row fed by *address* as offline (issue 06)."""
        for user_id, dev_id in self._dev.items():
            if dev_id == address:
                self._offline.add(user_id)
                self.set_row_offline(user_id, offline=True)

    # ------------------------------------------------------------------
    # Rows
    # ------------------------------------------------------------------

    def label_of(self, user_id: str) -> str:
        """The row's visible name (see *label_for*), which may differ from its key."""
        return self._label_for(user_id, self._dev.get(user_id)) or user_id

    def ensure_item(self, user_id: str) -> QTreeWidgetItem:
        """Return the row for *user_id*, creating it with a #n id + avatar on first sight."""
        item = self._items.get(user_id)
        if item is not None:
            return item
        uid = self._ids.setdefault(user_id, len(self._ids) + 1)
        item = QTreeWidgetItem([f"#{uid}  {self.label_of(user_id)}", "—"])
        item.setIcon(0, avatar_icon(user_id, user_id))
        item.setData(0, Qt.ItemDataRole.UserRole, user_id)
        self.addTopLevelItem(item)
        self._items[user_id] = item
        # Auto-select the first sensor to appear so its graph shows without an
        # extra click; later rows don't steal the selection.
        if self.selected_user is None:
            self.setCurrentItem(item)
        return item

    def _update_alert(self, item: QTreeWidgetItem, user_id: str, glucose: float) -> None:
        """Show a LOW/HIGH badge beside the user's name when out of the target range."""
        base = f"#{self._ids.get(user_id, 0)}  {self.label_of(user_id)}"
        if glucose < self._thresholds["tbr1_below"]:
            item.setText(0, f"{base}   ▼ LOW")
            item.setForeground(0, QBrush(QColor("#c0392b")))
        elif glucose > self._thresholds["tar1_above"]:
            item.setText(0, f"{base}   ▲ HIGH")
            item.setForeground(0, QBrush(QColor("#e67e22")))
        else:
            item.setText(0, base)
            item.setForeground(0, QBrush())

    def set_row_offline(self, user_id: str, *, offline: bool) -> None:
        """Mark (or clear) the tree row for *user_id* as disconnected."""
        item = self._items.get(user_id)
        if item is None:
            return
        base = f"#{self._ids.get(user_id, 0)}  {self.label_of(user_id)}"
        if offline:
            item.setText(0, f"{base}   ⚊ offline")
            item.setForeground(0, QBrush(QColor("#7f8c8d")))
            item.setText(1, "—")
        else:
            item.setText(0, base)
            item.setForeground(0, QBrush())

    def _on_current_changed(self, current: QTreeWidgetItem | None, _prev) -> None:
        if current is None:
            return
        self.selected_user = current.data(0, Qt.ItemDataRole.UserRole) or current.text(0)
        self.user_selected.emit(self.selected_user)
