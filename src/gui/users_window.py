"""The Users window: every saved user, plus the two ways to make one — **+ New** and
**+ Read from…** (a board's slot turned into a user).

Reading follows ADR 0006: the slot's user is looked up by name among the saved users and
one of three things happens (see :mod:`models.user_board`) — an unknown name opens an unsaved
draft, a match opens the saved user, and a name that differs from the saved one asks whether
to overwrite it or create ``Name#2`` (which also tells the board its new name).

This window only lists, creates and deletes; *opening* a user is a signal
(:attr:`UsersWindow.open_requested`) for whoever shows the profile screen. A draft is a user
that exists only in memory until that screen saves it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from PyQt6.QtCore import QPoint, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gui.avatar import avatar_icon
from models import user_board, user_match, user_store
from models.types import User


class SlotReader(Protocol):
    """What the window needs of :class:`~gui.user_reader.UserReader`."""

    def read(
        self, session, on_done: Callable[[user_board.BoardReading | None, str], None]
    ) -> bool: ...

    def write_name(self, session, slot: int, name: str) -> None: ...


NEW_USER_NAME = "New user"
_OVERWRITE, _CREATE = "overwrite", "create"


def _ask_what_to_do(outcome: user_board.Differs) -> str | None:
    """The default question for a board user that differs from the saved one of its name."""
    name = outcome.saved.name
    copy_name = user_match.next_free_name(name, {name})
    box = QMessageBox()
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle("The board's user differs")
    box.setText(f'The board\'s "{name}" differs from the saved one.')
    box.setInformativeText(
        f"Different: {', '.join(outcome.differences)}.\n\n"
        "Overwrite the saved user with what the board runs, or keep both?"
    )
    overwrite = box.addButton("Overwrite the saved user", QMessageBox.ButtonRole.AcceptRole)
    create = box.addButton(f'Create "{copy_name}"', QMessageBox.ButtonRole.ActionRole)
    box.addButton(QMessageBox.StandardButton.Cancel)
    box.exec()
    clicked = box.clickedButton()
    if clicked is overwrite:
        return _OVERWRITE
    return _CREATE if clicked is create else None


def _confirm_delete(user: User) -> bool:
    answer = QMessageBox.question(
        None,
        "Delete user",
        f'Delete "{user.name}"? Its saved data is removed.',
    )
    return answer == QMessageBox.StandardButton.Yes


def _icon_for(user: User) -> QIcon:
    if user.picture:
        path = user_store.user_dir(user) / user.picture
        if path.is_file():
            return QIcon(str(path))
    return avatar_icon(user.id, user.name, 32)


class UsersWindow(QWidget):
    """List of users with + Read from…, + New, Open and Delete."""

    # (user, is_draft): a draft is unsaved — it is only in memory until its screen saves it.
    open_requested = pyqtSignal(object, bool)

    def __init__(
        self,
        users: list[User],
        *,
        save: Callable[[], None],
        live_sessions: Callable[[], list],
        reader: SlotReader,
        board_busy: Callable[[], bool],
        ask_differs: Callable[[user_board.Differs], str | None] = _ask_what_to_do,
        confirm_delete: Callable[[User], bool] = _confirm_delete,
        on_deleted: Callable[[User], None] = lambda _user: None,
        label: Callable[[Any], str] = lambda session: session.user_id,
        where: Callable[[User], str] = lambda _user: "",
        parent: QWidget | None = None,
    ) -> None:
        """*users* is the app's own list, changed in place; *save* persists it."""
        super().__init__(parent)
        self.setWindowTitle("Users")
        self.resize(420, 480)
        self._users = users
        self._save = save
        self._live_sessions = live_sessions
        self._reader = reader
        self._board_busy = board_busy
        self._ask_differs = ask_differs
        self._confirm_delete = confirm_delete
        self._on_deleted = on_deleted
        self._label = label
        self._where = where

        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.read_button = QPushButton("+ Read from…")
        self.read_button.setToolTip("Turn the user running on a connected sensor into a user")
        self.read_button.clicked.connect(self.pick_session_and_read)
        top.addWidget(self.read_button)
        new_button = QPushButton("+ New")
        new_button.clicked.connect(self.new_user)
        top.addWidget(new_button)
        top.addStretch(1)
        layout.addLayout(top)

        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda _item: self.open_selected())
        layout.addWidget(self.list, 1)

        bottom = QHBoxLayout()
        open_button = QPushButton("Open")
        open_button.clicked.connect(self.open_selected)
        bottom.addWidget(open_button)
        delete_button = QPushButton("Delete")
        delete_button.clicked.connect(self.delete_selected)
        bottom.addWidget(delete_button)
        bottom.addStretch(1)
        layout.addLayout(bottom)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.refresh()

    # ------------------------------------------------------------------
    # The list
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        """Rebuild the list from the users (keeps the selected user selected)."""
        keep = self._selected_user()
        self.list.clear()
        for user in self._users:
            where = self._where(user)
            item = QListWidgetItem(
                _icon_for(user), f"{user.name}  ·  {where}" if where else user.name
            )
            self.list.addItem(item)
        if keep is not None and keep in self._users:
            self.list.setCurrentRow(self._users.index(keep))

    def _selected_user(self) -> User | None:
        row = self.list.currentRow()
        return self._users[row] if 0 <= row < len(self._users) else None

    def new_user(self) -> None:
        """A fresh saved user, opened for editing."""
        taken = {u.name for u in self._users}
        name = (
            NEW_USER_NAME
            if NEW_USER_NAME not in taken
            else user_match.next_free_name(NEW_USER_NAME, taken)
        )
        user = user_store.new_user(name)
        self._users.append(user)
        self._save()
        self.refresh()
        self.open_requested.emit(user, False)

    def open_selected(self) -> None:
        user = self._selected_user()
        if user is not None:
            self.open_requested.emit(user, False)

    def delete_selected(self) -> None:
        user = self._selected_user()
        if user is None or not self._confirm_delete(user):
            return
        self._users.remove(user)
        user_store.delete(user)
        self._save()
        self.refresh()
        self._on_deleted(user)

    # ------------------------------------------------------------------
    # Read from…
    # ------------------------------------------------------------------

    def pick_session_and_read(self) -> None:
        """Read the one live sensor, or ask which when there are several."""
        sessions = self._live_sessions()
        if not sessions:
            self.status.setText("Connect a sensor first (Bluetooth), then read from it.")
        elif len(sessions) == 1:
            self.read_from(sessions[0])
        else:
            menu = QMenu(self)
            for session in sessions:
                menu.addAction(self._label(session), lambda s=session: self.read_from(s))
            menu.exec(self.read_button.mapToGlobal(QPoint(0, self.read_button.height())))

    def read_from(self, session) -> None:
        """Read *session*'s slot and act on what it says (see the module docstring)."""
        if self._board_busy():
            self.status.setText("The board is busy answering another read — try again in a moment.")
            return
        self.read_button.setEnabled(False)
        self.status.setText(f"Reading {self._label(session)}…")
        if not self._reader.read(
            session, lambda reading, error: self._on_read_done(session, reading, error)
        ):
            self.read_button.setEnabled(True)
            self.status.setText("A read is already running.")

    def _on_read_done(self, session, reading: user_board.BoardReading | None, error: str) -> None:
        self.read_button.setEnabled(True)
        if reading is None:
            self.status.setText(error)
            return
        outcome = user_board.classify(self._users, reading)
        if isinstance(outcome, user_board.Unknown):
            self.status.setText(f'"{outcome.board.name}" is not saved yet — review and save it.')
            self.open_requested.emit(outcome.board, True)
        elif isinstance(outcome, user_board.Matches):
            self.status.setText(f'"{outcome.saved.name}" matches the board.')
            self.open_requested.emit(outcome.saved, False)
        else:
            self._resolve_difference(outcome, session)

    def _resolve_difference(self, outcome: user_board.Differs, session) -> None:
        choice = self._ask_differs(outcome)
        if choice == _OVERWRITE:
            updated = user_board.overwrite(outcome)
            self._users[self._users.index(outcome.saved)] = updated
            self._save()
            self.refresh()
            self.status.setText(f'"{updated.name}" now holds what the board runs.')
            self.open_requested.emit(updated, False)
        elif choice == _CREATE:
            copy = user_board.create_copy(self._users, outcome)
            self._users.append(copy)
            self._save()
            self.refresh()
            slot = session.slot_index if session.slot_index is not None else 0
            self._reader.write_name(session, slot, copy.name)
            self.status.setText(f'Created "{copy.name}" and renamed the board\'s user to match.')
            self.open_requested.emit(copy, False)
        else:
            self.status.setText("Nothing changed.")
