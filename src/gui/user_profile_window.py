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
from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from gui import user_picture
from gui.avatar_picker import choose_avatar
from gui.user_csv_page import CsvPage
from gui.user_model_page import ModelPage
from gui.user_preview_window import PreviewWindow
from gui.user_profile_page import ProfilePage
from gui.user_schedule_pages import EXERCISE, FOOD, SchedulePage
from gui.widgets import fit_to_screen
from models import app_settings, user_edit, user_send, user_store
from models.types import User

PAGE_ORDER = ("profile", "csv", "food", "exercise", "model")
_HEADER_PICTURE = 96


@dataclass
class SendDeps:
    """What **Send to…** needs from the app: the live sensors, how to push to one, who to tell
    when it landed, and how to describe a sensor in the chooser (what is on it now)."""

    live_sessions: Callable[[], list]
    send: Callable[[Any, User, Callable[[bool, str], None]], bool]
    on_sent: Callable[[int, User, str], None]  # (slot, the user that was sent, the message)
    describe: Callable[[Any], str]


@dataclass
class ProfileDeps:
    """What the screen needs from the app: the users list, how to persist it, and who to tell.
    *send* is None when the app cannot send (the Send button is then hidden)."""

    users: list[User]
    save: Callable[[], None]
    on_saved: Callable[[User, str | None], None]  # (the saved user, its name before this save)
    send: SendDeps | None = None


@dataclass
class Dialogs:
    """The questions the screen can ask. Injectable so tests answer them without a modal box."""

    ask_unsaved: Callable[[], str | None]  # "save" | "discard" | None (cancel)
    ask_save_before_send: Callable[[], str | None]  # "save" | "send" | None (cancel)
    choose_sensor: Callable[[list, Callable[[Any], str]], Any]


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


def _ask_save_before_send() -> str | None:
    """Save and send / Send without saving / Cancel for a send with unsaved changes."""
    box = QMessageBox()
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle("Send with unsaved changes")
    box.setText("This user has changes that are not saved.")
    box.setInformativeText("Save them before sending, or send what is on screen as it is?")
    save = box.addButton("Save and send", QMessageBox.ButtonRole.AcceptRole)
    send = box.addButton("Send without saving", QMessageBox.ButtonRole.ActionRole)
    box.addButton(QMessageBox.StandardButton.Cancel)
    box.exec()
    clicked = box.clickedButton()
    if clicked is save:
        return "save"
    return "send" if clicked is send else None


def _choose_sensor(sessions: list, describe: Callable[[Any], str]) -> Any:
    """Pick which live sensor to send to (None if cancelled)."""
    labels = [describe(session) for session in sessions]
    label, ok = QInputDialog.getItem(None, "Send to…", "Send to which sensor?", labels, 0, False)
    return sessions[labels.index(label)] if ok else None


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
        ask_save_before_send: Callable[[], str | None] = _ask_save_before_send,
        choose_sensor: Callable[[list, Callable[[Any], str]], Any] = _choose_sensor,
        choose_picture: Callable[[], Path | None] = choose_avatar,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowFlag(Qt.WindowType.Window, True)
        fit_to_screen(self, 760, 560)
        self._deps = deps
        self._dialogs = Dialogs(ask_unsaved, ask_save_before_send, choose_sensor)
        self._user = copy.deepcopy(user)
        self._saved: User | None = None if is_draft else copy.deepcopy(user)  # None = a draft
        self._picture_source: Path | None = None
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
        self.send_button = QPushButton("Send to…")
        self.send_button.setToolTip("Put this user on one of the connected sensors")
        self.send_button.clicked.connect(self.send)
        self.send_button.setVisible(deps.send is not None)
        bottom.addWidget(self.send_button)
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
        """Register a page; the menu lists registered pages in :data:`PAGE_ORDER` (a key not in
        it has a page but no menu entry)."""
        self.pages[key] = widget
        # Each page scrolls inside the window instead of stretching it: a page taller than the
        # screen (the graphs and tables) must not make the whole window taller than the screen.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(widget)
        self.stack.addWidget(scroll)
        if key not in PAGE_ORDER:
            return
        current = self._current_key()
        row = sum(1 for earlier in PAGE_ORDER[: PAGE_ORDER.index(key)] if earlier in self.pages)
        item = QListWidgetItem(label)
        item.setData(Qt.ItemDataRole.UserRole, key)
        self.menu.blockSignals(True)
        self.menu.insertItem(row, item)
        self.menu.blockSignals(False)
        self._select(current or "profile")
        self._apply_gating()

    def _show(self, key: str) -> None:
        """Bring *key*'s page forward (the stack holds each page inside its scroll area)."""
        viewport = self.pages[key].parentWidget()
        scroll = viewport.parentWidget() if viewport is not None else None
        if scroll is not None:
            self.stack.setCurrentWidget(scroll)

    def current_page(self) -> QWidget | None:
        """The page now showing."""
        scroll = self.stack.currentWidget()
        return scroll.widget() if isinstance(scroll, QScrollArea) else scroll

    def _current_key(self) -> str | None:
        item = self.menu.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def _select(self, key: str) -> None:
        for row in range(self.menu.count()):
            item = self.menu.item(row)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == key:
                self.menu.setCurrentRow(row)
                self._show(key)
                return

    def _show_menu_page(self, row: int) -> None:
        item = self.menu.item(row)
        if item is not None:
            self._show(item.data(Qt.ItemDataRole.UserRole))

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
    # Send to…
    # ------------------------------------------------------------------

    def _say(self, text: str, *, ok: bool = False) -> None:
        """Show *text* under the page: red for a problem, green for something that worked."""
        self.error.setStyleSheet("color: #2e7d32;" if ok else "color: #b00020;")
        self.error.setText(text)

    def send(self) -> None:
        """Put the user on a connected sensor: choose it, offer to save first if there are
        unsaved changes, then push. What goes to the board is what was saved (after *Save and
        send*) or what is on screen (*Send without saving*)."""
        deps = self._deps.send
        if deps is None:
            return
        sessions = deps.live_sessions()
        if not sessions:
            self._say("Connect a sensor first (Bluetooth), then send.")
            return
        user_edit.normalize(self._user)
        others = [u for u in self._deps.users if u.id != self._user.id]
        problem = user_edit.validate(self._user, others) or user_send.refusal(self._user)
        if problem:
            self._say(problem)
            return
        session = self._dialogs.choose_sensor(sessions, deps.describe)
        if session is None:
            return
        if self.is_dirty:
            choice = self._dialogs.ask_save_before_send()
            if choice is None or (choice == "save" and not self.save()):
                return
        sent = copy.deepcopy(self._user)
        slot = session.slot_index if session.slot_index is not None else 0

        def done(ok: bool, message: str) -> None:
            self._say(message, ok=ok)
            if ok:
                deps.on_sent(slot, sent, message)

        self._say("Sending…", ok=True)
        if not deps.send(session, sent, done):
            self._say("A send is already running.")

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
        previous_name = self._saved.name if self._saved is not None else None
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
        self._deps.on_saved(stored, previous_name)
        self.saved.emit(stored)
        return True

    def closeEvent(self, a0: QCloseEvent | None) -> None:  # Qt naming (PyQt's own parameter)
        """Ask what to do with unsaved changes before closing."""
        event = a0
        if event is None:
            return
        if self.is_dirty:
            choice = self._dialogs.ask_unsaved()
            if choice == "save":
                if not self.save():
                    event.ignore()
                    return
            elif choice != "discard":
                event.ignore()
                return
        self.closed.emit(self)
        event.accept()
