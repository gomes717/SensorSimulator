"""The open profile screens: one window per user, so opening a user twice raises the window it
already has instead of making a second copy that could be saved over the first."""

from __future__ import annotations

from PyQt6.QtWidgets import QWidget

from gui.user_profile_window import ProfileDeps, UserProfileWindow
from models.types import User


class UserProfiles:
    """Opens, raises and forgets profile windows."""

    def __init__(self, deps: ProfileDeps, parent: QWidget | None) -> None:
        self._deps = deps
        self._parent = parent
        self._windows: dict[str, UserProfileWindow] = {}

    def open(self, user: User, is_draft: bool) -> UserProfileWindow:
        """Show *user*'s screen (a draft is a user that is not saved yet)."""
        window = self._windows.get(user.id)
        if window is None:
            window = UserProfileWindow(user, is_draft, self._deps, parent=self._parent)
            window.closed.connect(lambda w, key=user.id: self._forget(key, w))
            self._windows[user.id] = window
        window.show()
        window.raise_()
        window.activateWindow()
        return window

    def _forget(self, key: str, window: UserProfileWindow) -> None:
        if self._windows.get(key) is window:
            del self._windows[key]
