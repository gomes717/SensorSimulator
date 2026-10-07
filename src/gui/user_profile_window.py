"""The profile screen: one window per user.

A column on the left holds the picture, the name and the menu (Profile, CSV, Food, Exercise,
Model); the chosen page fills the right; Save sits at the bottom. The mode picks which pages
are usable (see :func:`models.user_edit.pages_for`).

The screen edits a **copy** of the user and writes it into the app's list only on Save, so
closing without saving changes nothing; a draft (a user read from a board that nobody has
saved) is added by its first Save. Which pages exist is up to whoever builds the window:
:meth:`UserProfileWindow.add_page` registers one and the menu keeps a fixed order.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from gui import user_picture
from gui.user_csv_page import CsvPage
from gui.user_model_page import ModelPage
from gui.user_preview_window import PreviewWindow
from gui.user_profile_page import ProfilePage, choose_picture_file
from gui.user_schedule_pages import EXERCISE, FOOD, SchedulePage
from models import app_settings, user_edit, user_store
from models.types import User

PAGE_ORDER = ("profile", "csv", "food", "exercise", "model")
_HEADER_PICTURE = 96


@dataclass
class ProfileDeps:
    """What the screen needs from the app: the users list, how to persist it, and who to tell."""

    users: list[User]
    save: Callable[[], None]
    on_saved: Callable[[User], None]


def _ask_unsaved() -> str | None:
    """Save / Discard / Cancel for a screen closed with unsaved changes."""
    box = QMessageBox()
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle("Unsaved changes")
    box.setText("This user has changes that are not saved.")
    save = box.addButton("Save", QMessageBox.ButtonRole.AcceptRole)
    discard = box.addButton("Discard", QMessageBox.ButtonRole.DestructiveRole)
    box.addButton(QMessageBox.StandardButton.Cancel)
    box.exec()
    clicked = box.clickedButton()
    if clicked is save:
        return "save"
    return "discard" if clicked is discard else None


class UserProfileWindow(QWidget):
    """Edit one user; Save writes it into the app's list."""

    saved = pyqtSignal(object)
    closed = pyqtSignal(object)

    def __init__(
        self,
        user: User,
        is_draft: bool,
        deps: ProfileDeps,
        *,
        ask_unsaved: Callable[[], str | None] = _ask_unsaved,
        choose_picture: Callable[[], Path | None] = choose_picture_file,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.resize(760, 560)
        self._deps = deps
        self._ask_unsaved = ask_unsaved
        self._user = copy.deepcopy(user)
        self._saved: User | None = None if is_draft else copy.deepcopy(user)  # None = a draft
        self._picture_source: Path | None = None
        self._labels: dict[str, str] = {}
        self.pages: dict[str, QWidget] = {}

        root = QHBoxLayout(self)
        left = QVBoxLayout()
        self.picture_label = QLabel()
        self.picture_label.setFixedSize(_HEADER_PICTURE, _HEADER_PICTURE)
        left.addWidget(self.picture_label, 0, Qt.AlignmentFlag.AlignHCenter)
        self.name_label = QLabel()
        self.name_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.name_label.setStyleSheet("font-weight: bold;")
        left.addWidget(self.name_label)
        self.menu = QListWidget()
        self.menu.setFixedWidth(170)
        self.menu.currentRowChanged.connect(self._show_menu_page)
        left.addWidget(self.menu, 1)
        root.addLayout(left)

        right = QVBoxLayout()
        self.stack = QStackedWidget()
        right.addWidget(self.stack, 1)
        self.error = QLabel("")
        self.error.setWordWrap(True)
        self.error.setStyleSheet("color: #b00020;")
        right.addWidget(self.error)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        self.preview_button = QPushButton("Preview")
        self.preview_button.setToolTip("A 24 h graph of this user, with the edits on screen")
        self.preview_button.clicked.connect(self.preview)
        bottom.addWidget(self.preview_button)
        save_button = QPushButton("Save")
        save_button.clicked.connect(self.save)
        bottom.addWidget(save_button)
        right.addLayout(bottom)
        root.addLayout(right, 1)

        self.profile_page = ProfilePage(
            self._user, self._changed, self._picture_chosen, choose_picture
        )
        self.model_page = ModelPage(self._user, self._changed)
        self.add_page("profile", "Profile", self.profile_page)
        self.add_page("csv", "CSV", CsvPage(self._user, self._changed))
        self.add_page("food", "Food", SchedulePage(self._user, FOOD, self._changed))
        self.add_page("exercise", "Exercise", SchedulePage(self._user, EXERCISE, self._changed))
        self.add_page("model", "Model", self.model_page)
        self._update_header()

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    @property
    def user(self) -> User:
        """The copy being edited (not the app's saved one until Save)."""
        return self._user

    @property
    def is_dirty(self) -> bool:
        """Whether closing now would lose something: a draft, a chosen picture, or an edit."""
        return self._saved is None or self._picture_source is not None or self._user != self._saved

    # ------------------------------------------------------------------
    # Pages and the menu
    # ------------------------------------------------------------------

    def add_page(self, key: str, label: str, widget: QWidget) -> None:
        """Register a page; the menu lists registered pages in :data:`PAGE_ORDER`."""
        self.pages[key] = widget
        self.stack.addWidget(widget)
        self._labels[key] = label
        self._rebuild_menu()

    def _rebuild_menu(self) -> None:
        current = self._current_key()
        self.menu.blockSignals(True)
        self.menu.clear()
        for key in PAGE_ORDER:
            if key in self.pages:
                item = QListWidgetItem(self._labels[key])
                item.setData(Qt.ItemDataRole.UserRole, key)
                self.menu.addItem(item)
        self.menu.blockSignals(False)
        self._select(current or "profile")
        self._apply_gating()

    def _current_key(self) -> str | None:
        item = self.menu.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def _select(self, key: str) -> None:
        for row in range(self.menu.count()):
            item = self.menu.item(row)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == key:
                self.menu.setCurrentRow(row)
                self.stack.setCurrentWidget(self.pages[key])
                return

    def _show_menu_page(self, row: int) -> None:
        item = self.menu.item(row)
        if item is not None:
            self.stack.setCurrentWidget(self.pages[item.data(Qt.ItemDataRole.UserRole)])

    def _apply_gating(self) -> None:
        """Enable the pages the mode allows; leave one that just got switched off."""
        enabled = user_edit.pages_for(self._user.mode)
        for row in range(self.menu.count()):
            item = self.menu.item(row)
            if item is None:
                continue
            on = item.data(Qt.ItemDataRole.UserRole) in enabled
            item.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                if on
                else Qt.ItemFlag.NoItemFlags
            )
        key = self._current_key()
        if key is not None and key not in enabled:
            self._select("profile")

    # ------------------------------------------------------------------
    # Edits
    # ------------------------------------------------------------------

    def _changed(self) -> None:
        """A page edited the user: refresh the header, the title, the gating and the pages."""
        self.error.setText("")
        self._apply_gating()
        for page in self.pages.values():
            refresh = getattr(page, "refresh", None)
            if refresh is not None:
                refresh()
        self._update_header()

    def _picture_chosen(self, path: Path) -> None:
        self._picture_source = path
        self._changed()

    def _update_header(self) -> None:
        self.name_label.setText(self._user.name)
        self.picture_label.setPixmap(
            user_picture.pixmap_for(self._user, _HEADER_PICTURE, self._picture_source)
        )
        marker = " *" if self.is_dirty else ""
        self.setWindowTitle(f"User — {self._user.name}{marker}")

    # ------------------------------------------------------------------
    # Preview
    # ------------------------------------------------------------------

    def preview(self) -> PreviewWindow:
        """Open a 24 h preview of the user as it is on screen (saved or not)."""
        window = PreviewWindow(self._user, app_settings.load(), parent=self)
        window.show()
        window.raise_()
        return window

    # ------------------------------------------------------------------
    # Save and close
    # ------------------------------------------------------------------

    def save(self) -> bool:
        """Check the user, store its picture, write it into the users list and persist.
        False (with the reason shown) if it cannot be saved."""
        user_edit.normalize(self._user)
        others = [u for u in self._deps.users if u.id != self._user.id]
        problem = user_edit.validate(self._user, others)
        if problem:
            self.error.setText(problem)
            return False
        if self._picture_source is not None:
            try:
                self._user.picture = user_picture.store_picture(
                    user_store.user_dir(self._user), self._picture_source
                )
            except ValueError as exc:
                self.error.setText(f"The picture could not be used: {exc}")
                return False
        stored = copy.deepcopy(self._user)
        for index, existing in enumerate(self._deps.users):
            if existing.id == stored.id:
                self._deps.users[index] = stored
                break
        else:
            self._deps.users.append(stored)
        self._deps.save()
        self._saved = copy.deepcopy(self._user)
        self._picture_source = None
        self.error.setText("")
        self.profile_page.refresh()
        self._update_header()
        self._deps.on_saved(stored)
        self.saved.emit(stored)
        return True

    def closeEvent(self, a0: QCloseEvent | None) -> None:  # Qt naming (PyQt's own parameter)
        """Ask what to do with unsaved changes before closing."""
        event = a0
        if event is None:
            return
        if self.is_dirty:
            choice = self._ask_unsaved()
            if choice == "save":
                if not self.save():
                    event.ignore()
                    return
            elif choice != "discard":
                event.ignore()
                return
        self.closed.emit(self)
        event.accept()
