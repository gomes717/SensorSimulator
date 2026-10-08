"""The sensor tab strip, the page stack under it, and the "connect first" start screen.

One tab per sensor, browser-style: avatar, name and an alert icon.
Before anything is connected the whole area is a centered "Connect Bluetooth"
call to action; once there are tabs, a "+" after the last one (as in a browser)
opens the Bluetooth window to connect another sensor. A sensor that drops keeps its tab (greyed, history intact) until
it is closed with its close button, which disconnects it.

Alerts follow models.alerts: a warning shows an amber triangle (the tooltip gives the
value and direction); a critical reading shows a red one and the tab **blinks red until you have
opened it** — then it stays solid red until the reading leaves the critical range.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPalette,
    QPen,
    QPixmap,
    QPolygonF,
)
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QTabBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from gui.avatar import avatar_icon
from gui.branding import LogoWidget
from gui.run_clock import RunClock
from gui.sensor_page import SensorPage
from gui.sensor_pages import SensorPages
from models import alerts

_BLINK_MS = 500
_AMBER = QColor("#f4b400")
_RED = QColor("#d32f2f")
_ICON = 16
_CRITICAL_TINT = QColor(211, 47, 47, 110)
_TAB_RADIUS = 9.0


def _strip_color(palette: QPalette) -> QColor:
    """The tab strip's background: a little darker than the page, so the selected tab —
    drawn in the page colour — reads as part of the page, as in a browser."""
    return palette.color(QPalette.ColorRole.Window).darker(118)


def alert_icon(level: alerts.Level) -> QPixmap:
    """A warning triangle with an exclamation mark: amber for a warning, red for critical."""
    color = _RED if level is alerts.Level.CRITICAL else _AMBER
    pixmap = QPixmap(_ICON, _ICON)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(color.darker(140), 1))
    painter.setBrush(QBrush(color))
    painter.drawPolygon(
        QPolygonF(
            [QPointF(_ICON / 2, 1.5), QPointF(_ICON - 1.5, _ICON - 2), QPointF(1.5, _ICON - 2)]
        )
    )
    painter.setPen(QPen(QColor("white"), 1.6))
    painter.drawLine(QPointF(_ICON / 2, 6), QPointF(_ICON / 2, 10))
    painter.drawPoint(QPointF(_ICON / 2, 12.2))
    painter.end()
    return pixmap


class TabHeader(QWidget):
    """What a tab shows: avatar, name, latest value, alert icon."""

    def __init__(self, user_id: str, name: str, picture: QPixmap | None = None) -> None:
        super().__init__()
        self._user_id = user_id
        self._picture_shown = False
        self._avatar = QLabel()
        self._name = QLabel(name)
        self.set_picture(picture)
        self._icon = QLabel()
        self._icon.setFixedSize(_ICON, _ICON)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(6)
        for widget in (self._avatar, self._name, self._icon):
            layout.addWidget(widget)
        self.alert: alerts.Alert = alerts.NORMAL
        self.offline = False
        self.fit()

    def fit(self) -> None:
        """Give the header a fixed width that fits its name, so a tab neither clips
        its text nor jitters when the alert icon appears (the tab bar sizes a tab
        once, from the button's size at that moment)."""
        metrics = self._name.fontMetrics()
        spacing = self.layout().spacing()
        width = (
            self.layout().contentsMargins().left()
            + self.layout().contentsMargins().right()
            + 22
            + _ICON
            + 2 * spacing
            + metrics.horizontalAdvance(self._name.text())
        )
        self.setFixedWidth(width)

    def name_text(self) -> str:
        """The sensor's name as shown."""
        return self._name.text()

    def shows_alert_icon(self) -> bool:
        """Whether the warning triangle is currently visible."""
        return not self._icon.pixmap().isNull()

    @property
    def shows_picture(self) -> bool:
        """Whether the avatar is the user's picture (not the generated initials disc)."""
        return self._picture_shown

    def set_picture(self, picture: QPixmap | None) -> None:
        """Show the user's *picture* as the avatar, or the initials disc when there is none."""
        self._picture_shown = picture is not None and not picture.isNull()
        if self._picture_shown and picture is not None:
            self._avatar.setPixmap(picture)
        else:
            self._avatar.setPixmap(avatar_icon(self._user_id, self._name.text()).pixmap(22, 22))

    def set_name(self, name: str) -> bool:
        """The sensor's current name (it can change when a slot is re-assigned).
        Returns whether it changed, in which case the tab needs re-laying out."""
        if name == self._name.text():
            return False
        self._name.setText(name)
        if not self._picture_shown:
            self._avatar.setPixmap(avatar_icon(self._user_id, name).pixmap(22, 22))
        self.fit()
        return True

    def show_reading(self, glucose: float, alert: alerts.Alert) -> None:
        """Alert icon for the latest reading; the tooltip carries the value and direction
        (the tab itself shows no number)."""
        self.alert = alert
        if alert.is_normal:
            self._icon.clear()
        else:
            self._icon.setPixmap(alert_icon(alert.level))
        self.setToolTip(alert.describe(glucose))

    def show_offline(self, offline: bool) -> None:
        """Grey the tab out and drop any alert (a stale reading must not alarm)."""
        self.offline = offline
        self.setEnabled(not offline)
        if offline:
            self.alert = alerts.NORMAL
            self._icon.clear()
            self.setToolTip("offline")


class _CloseButton(QToolButton):
    """A discreet tab close button: a small "x" with no box, and a faint round
    highlight only while the pointer is over it (as in a browser)."""

    _SIZE = 18

    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(self._SIZE, self._SIZE)
        self.setToolTip("Close this sensor (disconnects it)")
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setAutoRaise(True)

    def paintEvent(self, event: QPaintEvent) -> None:  # Qt naming
        """Draw the hover circle (if any) and the cross, in the text colour."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = self.palette().color(QPalette.ColorRole.WindowText)
        if self.underMouse():
            hover = QColor(color)
            hover.setAlpha(45)
            painter.setBrush(hover)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QRectF(self.rect()).adjusted(1, 1, -1, -1))
        color.setAlpha(190 if self.underMouse() else 130)
        painter.setPen(QPen(color, 1.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        c, r = self._SIZE / 2.0, 3.4
        painter.drawLine(QPointF(c - r, c - r), QPointF(c + r, c + r))
        painter.drawLine(QPointF(c - r, c + r), QPointF(c + r, c - r))
        painter.end()


class _Strip(QWidget):
    """The band behind the tabs and the "+" button, in the strip colour."""

    def paintEvent(self, event: QPaintEvent) -> None:  # Qt naming
        """Fill with the strip colour (read live, so a theme switch just works)."""
        painter = QPainter(self)
        painter.fillRect(self.rect(), _strip_color(self.palette()))
        painter.end()


class _AlertTabBar(QTabBar):
    """A browser-style tab bar: the selected tab is the page colour with rounded top
    corners, the others sit flat on the darker strip with thin separators, and an
    alerting tab is tinted red (blinking, or solid once acknowledged)."""

    def __init__(self) -> None:
        super().__init__()
        self.tints: dict[str, str] = {}  # tab key -> "blink" | "solid"
        self.blink_on = False
        self._hover = -1
        self.setMouseTracking(True)

    def key_at(self, index: int) -> str:
        """The sensor key stored on tab *index*."""
        return self.tabData(index)

    def mouseMoveEvent(self, event) -> None:  # Qt naming
        """Track the hovered tab so it can be drawn a touch lighter."""
        hover = self.tabAt(event.position().toPoint())
        if hover != self._hover:
            self._hover = hover
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event: QEvent) -> None:  # Qt naming
        """Clear the hover highlight."""
        self._hover = -1
        self.update()
        super().leaveEvent(event)

    def _tab_path(self, index: int) -> QPainterPath:
        """The tab's shape: rounded top corners, square bottom edge flush with the page."""
        rect = QRectF(self.tabRect(index))
        # Run to the bottom of the bar whatever height the style gave the tab itself,
        # or the tab is two-toned: the page colour on top, the strip colour beneath.
        rect.setBottom(float(self.height()))
        # One outline, traced once: a rounded rect with a square bottom built from a
        # rounded rect plus a rect overlapped, and filled even-odd, left the overlap
        # as a hole — the lower half of the tab showed the strip colour.
        r = min(_TAB_RADIUS, rect.width() / 2.0, rect.height())
        path = QPainterPath()
        path.moveTo(rect.left(), rect.bottom())
        path.lineTo(rect.left(), rect.top() + r)
        path.arcTo(QRectF(rect.left(), rect.top(), 2 * r, 2 * r), 180, -90)
        path.lineTo(rect.right() - r, rect.top())
        path.arcTo(QRectF(rect.right() - 2 * r, rect.top(), 2 * r, 2 * r), 90, -90)
        path.lineTo(rect.right(), rect.bottom())
        path.closeSubpath()
        return path

    def paintEvent(self, event: QPaintEvent) -> None:  # Qt naming
        """Draw the strip, the tab shapes and separators, then the red alert tints.
        (The avatar, name, icon and close button are child widgets painted on top.)"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        page = self.palette().color(QPalette.ColorRole.Window)
        strip = _strip_color(self.palette())
        painter.fillRect(self.rect(), strip)
        current = self.currentIndex()
        for index in range(self.count()):
            path = self._tab_path(index)
            if index == current:
                painter.fillPath(path, page)
            elif index == self._hover:
                painter.fillPath(path, strip.lighter(112))
            mode = self.tints.get(self.key_at(index))
            if mode == "solid" or (mode == "blink" and self.blink_on):
                painter.fillPath(path, _CRITICAL_TINT)
            # a thin divider after a flat tab, unless the next one is selected/hovered
            nxt = index + 1
            if (
                nxt < self.count()
                and index not in (current, self._hover)
                and nxt not in (current, self._hover)
            ):
                rect = self.tabRect(index)
                divider = self.palette().color(QPalette.ColorRole.WindowText)
                divider.setAlpha(60)
                painter.setPen(QPen(divider, 1))
                y0, y1 = rect.top() + rect.height() * 0.25, rect.bottom() - rect.height() * 0.25
                painter.drawLine(QPointF(rect.right(), y0), QPointF(rect.right(), y1))
        painter.end()


class _EmptyState(QWidget):
    """The start screen: nothing connected yet, one obvious thing to do."""

    connect_requested = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        title = QLabel("Connect a sensor to begin")
        font = title.font()
        font.setPointSize(font.pointSize() + 4)
        title.setFont(font)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.button = QPushButton("Connect Bluetooth")
        self.button.setMinimumSize(200, 44)
        self.button.clicked.connect(self.connect_requested)
        layout = QVBoxLayout(self)
        layout.addStretch(1)
        layout.addWidget(LogoWidget(), 0, Qt.AlignmentFlag.AlignCenter)
        layout.addSpacing(16)
        layout.addWidget(title)
        layout.addSpacing(12)
        layout.addWidget(self.button, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addStretch(1)


class SensorTabs(QWidget):
    """Tab strip + the stack of sensor pages, or the start screen when empty."""

    user_selected = pyqtSignal(str)  # the tab's sensor key
    selection_cleared = pyqtSignal()  # the last tab went away
    close_requested = pyqtSignal(str)  # close button pressed on this sensor's tab
    connect_requested = pyqtSignal()  # the start screen's button
    show_points_changed = pyqtSignal(bool)  # the graphs' "Show points" choice changed

    def __init__(
        self,
        thresholds: dict,
        view_window_s: float,
        clock: RunClock,
        label_for: Callable[[str, str | None], str] | None = None,
        parent=None,
        *,
        show_points: bool = False,
        picture_for: Callable[[str, str | None], QPixmap | None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._thresholds = thresholds
        self._label_for = label_for or (lambda user_id, _dev_id: user_id)
        # (key, address) -> the user's picture for that tab, or None (the initials disc)
        self._picture_for = picture_for or (lambda _user_id, _dev_id: None)
        self.pages = SensorPages(thresholds, view_window_s, clock, show_points=show_points)
        self.pages.show_points_changed.connect(self.show_points_changed)

        self._bar = _AlertTabBar()
        self._bar.setMovable(False)
        self._bar.setExpanding(False)
        self._bar.setDocumentMode(True)
        self._bar.currentChanged.connect(self._on_current_changed)

        # Like a browser: a "+" right after the last tab opens the Bluetooth window to
        # connect another sensor.
        self.plus_button = QToolButton()
        self.plus_button.setText("+")
        self.plus_button.setToolTip("Connect another sensor (Bluetooth)")
        self.plus_button.setAutoRaise(True)
        plus_font = self.plus_button.font()
        plus_font.setPointSize(plus_font.pointSize() + 6)
        self.plus_button.setFont(plus_font)
        self.plus_button.setFixedSize(32, 32)
        self.plus_button.clicked.connect(self.connect_requested)
        self._bar.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self._strip = _Strip()
        strip = QHBoxLayout(self._strip)
        strip.setContentsMargins(6, 6, 6, 0)  # a little air above the tabs, like a browser
        strip.setSpacing(2)
        strip.addWidget(self._bar)
        strip.addWidget(self.plus_button)
        strip.addStretch(1)

        content = QWidget()
        column = QVBoxLayout(content)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(self._strip)
        column.addWidget(self.pages, 1)

        self._empty = _EmptyState()
        self._empty.connect_requested.connect(self.connect_requested)
        self._root = QStackedWidget()
        self._root.addWidget(self._empty)
        self._root.addWidget(content)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._root)

        self._headers: dict[str, TabHeader] = {}
        self._close_buttons: dict[str, _CloseButton] = {}
        self._dev: dict[str, str] = {}  # sensor key -> BLE address feeding it
        self._ids: dict[str, int] = {}  # sensor key -> stable small #n
        self._glucose: dict[str, float] = {}  # latest reading, to re-evaluate on new thresholds
        self._acked: set[str] = set()  # critical tabs the user has already looked at
        self._offline: set[str] = set()
        self._model_only = False
        self.selected_user: str | None = None

        self._blink = QTimer(self)
        self._blink.setInterval(_BLINK_MS)
        self._blink.timeout.connect(self.tick_blink)
        self._refresh_view()

    # ------------------------------------------------------------------
    # Tabs
    # ------------------------------------------------------------------

    def _index_of(self, key: str) -> int:
        for index in range(self._bar.count()):
            if self._bar.key_at(index) == key:
                return index
        return -1

    def is_start_screen(self) -> bool:
        """Whether the "Connect Bluetooth" start screen is showing (no tabs, not Model Only)."""
        return self._root.currentIndex() == 0

    def tab_bar_visible(self) -> bool:
        """Whether the tab strip (tabs and the "+" button) is showing."""
        return not self._strip.isHidden()

    def tab_keys(self) -> list[str]:
        """The sensor keys with a tab, left to right."""
        return [self._bar.key_at(i) for i in range(self._bar.count())]

    def header(self, key: str) -> TabHeader | None:
        """The header widget of *key*'s tab, if it has one."""
        return self._headers.get(key)

    def ensure_tab(
        self, key: str, address: str | None = None, slot: int | None = None
    ) -> SensorPage:
        """The tab (and page) for sensor *key*, created the first time it is seen."""
        if address is not None:
            self._dev[key] = address
        page = self.pages.ensure(key, slot)
        if key not in self._headers:
            self._ids.setdefault(key, len(self._ids) + 1)
            header = TabHeader(key, self.label_of(key), self._picture_for(key, self._dev.get(key)))
            self._headers[key] = header
            first = self._bar.count() == 0
            # Qt announces the first tab as "current" from inside addTab(), before
            # its key is stored — so set it all up silently, then announce once.
            self._bar.blockSignals(True)
            index = self._bar.addTab("")
            self._bar.setTabData(index, key)
            self._bar.setTabButton(index, QTabBar.ButtonPosition.LeftSide, header)
            close = _CloseButton()
            close.clicked.connect(lambda _checked=False, b=close: self._close_clicked(b))
            self._close_buttons[key] = close
            self._bar.setTabButton(index, QTabBar.ButtonPosition.RightSide, close)
            self._bar.blockSignals(False)
            self._refresh_view()
            if first:
                self._on_current_changed(0)
        return page

    def _close_clicked(self, button: _CloseButton) -> None:
        """A tab's close button was pressed: ask for that sensor to be closed."""
        for key, candidate in self._close_buttons.items():
            if candidate is button:
                self.close_requested.emit(key)
                return

    def remove_tab(self, key: str) -> None:
        """Close *key*'s tab and discard its page."""
        index = self._index_of(key)
        if index < 0:
            return
        header = self._headers.pop(key, None)
        self._close_buttons.pop(key, None)
        self._bar.removeTab(index)
        if header is not None:
            header.deleteLater()
        self.pages.remove(key)
        for table in (self._dev, self._ids, self._glucose, self._bar.tints):
            table.pop(key, None)
        self._acked.discard(key)
        self._offline.discard(key)
        if not self._bar.count():
            self.selected_user = None
            self.selection_cleared.emit()
        self._refresh_blink()
        self._refresh_view()

    def rekey(self, old: str, new: str) -> None:
        """A sensor reconnected under a new session id: keep its tab and history."""
        index = self._index_of(old)
        if index < 0 or old == new:
            return
        self._bar.setTabData(index, new)
        self.pages.rekey(old, new)
        for table in (
            self._headers,
            self._close_buttons,
            self._dev,
            self._ids,
            self._glucose,
            self._bar.tints,
        ):
            if old in table:
                table[new] = table.pop(old)
        for group in (self._acked, self._offline):
            if old in group:
                group.discard(old)
                group.add(new)
        if self.selected_user == old:
            self.selected_user = new

    def find_key(self, *, address: str | None = None, slot: int | None = None) -> str | None:
        """The tab of the sensor reachable at *address*, or in board slot *slot*."""
        for key in self.tab_keys():
            page = self.pages.get(key)
            if slot is not None and page is not None and page.slot == slot:
                return key
            if address is not None and self._dev.get(key) == address:
                return key
        return None

    def is_offline(self, key: str) -> bool:
        """Whether *key*'s sensor is currently greyed out as disconnected."""
        return key in self._offline

    def address_of(self, key: str) -> str | None:
        """The BLE address feeding *key*'s tab, if known."""
        return self._dev.get(key)

    def label_of(self, key: str) -> str:
        """The tab's visible name (see *label_for*), which may differ from its key."""
        return self._label_for(key, self._dev.get(key)) or key

    def refresh_labels(self) -> None:
        """Re-resolve every tab's name and picture (a slot was re-assigned, a user was saved)."""
        for key, header in self._headers.items():
            header.set_picture(self._picture_for(key, self._dev.get(key)))
            self._rename(key, header)

    def _rename(self, key: str, header: TabHeader) -> None:
        """Apply the current label to *key*'s header and re-lay out its tab if it changed."""
        if header.set_name(self.label_of(key)):
            index = self._index_of(key)
            if index >= 0:
                # Make the bar measure the tab again. Do NOT re-set the header as the
                # tab's button for this: QTabBar deletes the previous button widget
                # when one is set, and that includes the very same widget — the tab
                # went blank (no avatar, no name) after a rename.
                self._bar.setTabText(index, " ")
                self._bar.setTabText(index, "")

    def select(self, key: str) -> None:
        """Bring *key*'s tab forward."""
        index = self._index_of(key)
        if index >= 0:
            self._bar.setCurrentIndex(index)

    # ------------------------------------------------------------------
    # Inbound updates
    # ------------------------------------------------------------------

    def note_message(self, msg: dict) -> None:
        """Update the tab for *msg*'s sensor from a decoded BLE notification: make
        sure it has one, clear a stale offline state, refresh value + alert."""
        key = msg.get("user_id")
        if key is None:
            return
        self.ensure_tab(key, msg.get("dev_id"))
        if key in self._offline:  # a message means it's back
            self.set_offline(key, False)
        if "glucose_value" in msg:
            glucose = msg["glucose_value"]
            self._glucose[key] = glucose
            self._apply_reading(key, glucose)
        self._rename(key, self._headers[key])

    def mark_device_offline(self, address: str) -> None:
        """A BLE session ended — grey every tab fed by *address* (issue 06)."""
        for key, dev in list(self._dev.items()):
            if dev == address:
                self.set_offline(key, True)

    def set_offline(self, key: str, offline: bool) -> None:
        """Grey (or restore) *key*'s tab; going offline clears its alert and tint."""
        header = self._headers.get(key)
        if header is None:
            return
        if offline:
            self._offline.add(key)
            self._acked.discard(key)
            self._glucose.pop(key, None)
            self._bar.tints.pop(key, None)
        else:
            self._offline.discard(key)
        header.show_offline(offline)
        self._refresh_blink()
        self._bar.update()

    def set_thresholds(self, thresholds: dict) -> None:
        """New range thresholds: re-evaluate every tab's alert at once."""
        self._thresholds = thresholds
        self.pages.set_thresholds(thresholds)
        for key, glucose in self._glucose.items():
            self._apply_reading(key, glucose)

    def set_model_only(self, model_only: bool) -> None:
        """Model Only has one synthetic page and no sensor tabs."""
        self._model_only = model_only
        self._refresh_view()

    # ------------------------------------------------------------------
    # Alerts + blink
    # ------------------------------------------------------------------

    def _apply_reading(self, key: str, glucose: float) -> None:
        header = self._headers[key]
        alert = alerts.alert_for(glucose, self._thresholds)
        header.show_reading(glucose, alert)
        if alert.level is alerts.Level.CRITICAL:
            if key == self.selected_user:
                self._acked.add(key)  # already looking at it
            self._bar.tints[key] = "solid" if key in self._acked else "blink"
        else:
            self._acked.discard(key)  # recovered: the next critical blinks afresh
            self._bar.tints.pop(key, None)
        self._refresh_blink()
        self._bar.update()

    def _refresh_blink(self) -> None:
        """Run the shared blink timer only while some tab is unacknowledged-critical."""
        blinking = any(mode == "blink" for mode in self._bar.tints.values())
        if blinking and not self._blink.isActive():
            self._blink.start()
        elif not blinking and self._blink.isActive():
            self._blink.stop()
            self._bar.blink_on = False

    def tick_blink(self) -> None:
        """One blink phase (the timer's slot; tests drive it directly)."""
        self._bar.blink_on = not self._bar.blink_on
        self._bar.update()

    def tint_of(self, key: str) -> str | None:
        """``"blink"`` for an unacknowledged critical tab, ``"solid"`` once the user has
        seen it, ``None`` when the tab is not critical."""
        return self._bar.tints.get(key)

    # ------------------------------------------------------------------
    # Selection / view
    # ------------------------------------------------------------------

    def _on_current_changed(self, index: int) -> None:
        if index < 0:
            return
        key = self._bar.key_at(index)
        self.selected_user = key
        if self._bar.tints.get(key) == "blink":  # opening it acknowledges it
            self._acked.add(key)
            self._bar.tints[key] = "solid"
            self._refresh_blink()
            self._bar.update()
        self.user_selected.emit(key)

    def _refresh_view(self) -> None:
        """Start screen when nothing is connected; otherwise the tabs + pages
        (Model Only: the pages without the tab strip)."""
        has_tabs = self._bar.count() > 0
        self._strip.setVisible(has_tabs and not self._model_only)
        self._root.setCurrentIndex(1 if (has_tabs or self._model_only) else 0)
