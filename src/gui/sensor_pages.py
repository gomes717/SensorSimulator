"""The stack of per-sensor pages, and which one is on screen.

Holds one :class:`SensorPage` per sensor (keyed by its session ``user_id``) plus
one default page (key ``None``) that doubles as the Model Only trace and the
empty "select a sensor" view. It owns creation and lookup — a message or a
command always resolves to *its* page — and nothing about what the data means.
"""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QStackedWidget

from gui.run_clock import RunClock
from gui.sensor_page import SensorPage


class SensorPages(QStackedWidget):
    """Creates pages on demand and shows exactly one of them."""

    page_created = pyqtSignal(object)  # the new SensorPage, so the host can wire it
    show_points_changed = pyqtSignal(bool)  # any page's "Show points" box changed

    def __init__(
        self,
        thresholds: dict,
        view_window_s: float,
        clock: RunClock,
        parent=None,
        *,
        show_points: bool = False,
    ) -> None:
        super().__init__(parent)
        self._show_points = show_points
        self._thresholds = thresholds
        self._view_window_s = view_window_s
        self._clock = clock
        self._pages: dict[str | None, SensorPage] = {}
        self.default_page = self._create(None, None)
        self.setCurrentWidget(self.default_page)

    def _create(self, key: str | None, slot: int | None) -> SensorPage:
        page = SensorPage(
            key,
            slot,
            self._thresholds,
            self._view_window_s,
            self._clock,
            show_points=self._show_points,
        )
        page.show_points_toggled.connect(self._on_points_toggled)
        self._pages[key] = page
        self.addWidget(page)
        self.page_created.emit(page)
        return page

    # -- lookup ---------------------------------------------------------

    def ensure(self, key: str | None, slot: int | None = None) -> SensorPage:
        """The page for *key*, created on first sight. *slot* fills in a page that
        was created before its sensor's slot was known."""
        page = self._pages.get(key)
        if page is None:
            return self._create(key, slot)
        if page.slot is None and slot is not None:
            page.slot = slot
        return page

    def get(self, key: str | None) -> SensorPage | None:
        """The page for *key* if it exists; never creates one."""
        return self._pages.get(key)

    def all(self) -> list[SensorPage]:
        """Every page, the default one first."""
        return list(self._pages.values())

    def sensor_pages(self) -> list[SensorPage]:
        """Every page that belongs to a sensor (not the default / Model Only page)."""
        return [p for k, p in self._pages.items() if k is not None]

    def _on_points_toggled(self, show: bool) -> None:
        """One page's box changed: every page follows (the choice is app-wide)."""
        self._show_points = show
        for page in self._pages.values():
            page.set_show_points(show)
        self.show_points_changed.emit(show)

    def remove(self, key: str) -> None:
        """Discard *key*'s page (its sensor's tab was closed)."""
        page = self._pages.pop(key, None)
        if page is not None:
            self.removeWidget(page)
            page.deleteLater()

    def rekey(self, old: str, new: str) -> None:
        """A sensor reconnected under a new session id: keep its page and history."""
        page = self._pages.pop(old, None)
        if page is not None:
            page.key = new
            self._pages[new] = page

    # -- display --------------------------------------------------------

    def show_page(self, page: SensorPage) -> None:
        """Bring *page* to the front."""
        self.setCurrentWidget(page)

    def current_page(self) -> SensorPage:
        """The page on screen."""
        page = self.currentWidget()
        assert isinstance(page, SensorPage)
        return page

    # -- fan-out --------------------------------------------------------

    def clear_all(self) -> None:
        """Empty every page's series (a restart; the clock is re-anchored first)."""
        for page in self._pages.values():
            page.clear()

    def set_thresholds(self, thresholds: dict) -> None:
        """New range thresholds for every page, including pages created later."""
        self._thresholds = thresholds
        for page in self._pages.values():
            page.set_thresholds(thresholds)

    def set_view_window(self, seconds: float) -> None:
        """New rolling window for every page, including pages created later."""
        self._view_window_s = seconds
        for page in self._pages.values():
            page.set_view_window(seconds)

    def rebuild_for_theme(self) -> None:
        """Recreate every page's canvases for a new palette."""
        for page in self._pages.values():
            page.rebuild_for_theme()
