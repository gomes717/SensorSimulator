"""SensorTabs: start screen, one tab per sensor, alerts, blink and acknowledgement."""

import os
import sys
from datetime import UTC, datetime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication, QTabBar

from gui.run_clock import RunClock
from gui.sensor_tabs import SensorTabs

_APP = QApplication.instance() or QApplication([])

T = {"tbr2_below": 54.0, "tbr1_below": 70.0, "tar1_above": 180.0, "tar2_above": 250.0}


@pytest.fixture
def tabs():
    widget = SensorTabs(dict(T), 0.0, RunClock(datetime.now(UTC)))
    yield widget
    widget.close()


@pytest.fixture
def selected(tabs):
    seen: list[str] = []
    tabs.user_selected.connect(seen.append)
    return seen


def _reading(tabs, key, glucose, dev="AA"):
    tabs.note_message({"user_id": key, "dev_id": dev, "glucose_value": glucose})


# --- start screen / tab lifecycle ------------------------------------------


def test_nothing_connected_shows_the_start_screen(tabs):
    assert tabs.is_start_screen() and not tabs.tab_bar_visible()


def test_the_first_tab_replaces_the_start_screen_and_is_selected(tabs, selected):
    tabs.ensure_tab("A", "AA", slot=0)
    assert not tabs.is_start_screen() and tabs.tab_bar_visible()
    assert tabs.tab_keys() == ["A"] and selected == ["A"] and tabs.selected_user == "A"


def test_a_tab_shows_no_value_until_data_arrives(tabs):
    tabs.ensure_tab("A", "AA")
    header = tabs.header("A")
    assert not header.shows_alert_icon()


def test_a_second_tab_does_not_steal_the_selection(tabs):
    tabs.ensure_tab("A")
    tabs.ensure_tab("B")
    assert tabs.tab_keys() == ["A", "B"] and tabs.selected_user == "A"


def test_model_only_hides_the_strip_but_keeps_the_pages(tabs):
    tabs.set_model_only(True)
    assert not tabs.is_start_screen() and not tabs.tab_bar_visible()
    tabs.set_model_only(False)
    assert tabs.is_start_screen()


def test_closing_a_tab_asks_to_disconnect_and_removing_it_goes_back_to_the_start_screen(tabs):
    closed, cleared = [], []
    tabs.close_requested.connect(closed.append)
    tabs.selection_cleared.connect(lambda: cleared.append(1))
    tabs.ensure_tab("A", "AA")
    tabs._close_buttons["A"].click()
    assert closed == ["A"]
    tabs.remove_tab("A")
    assert tabs.tab_keys() == [] and cleared == [1] and tabs.is_start_screen()
    assert tabs.pages.get("A") is None


def test_removing_the_selected_tab_selects_a_neighbour(tabs, selected):
    tabs.ensure_tab("A")
    tabs.ensure_tab("B")
    tabs.remove_tab("A")
    assert tabs.selected_user == "B" and selected[-1] == "B"


def test_a_reconnect_under_a_new_id_keeps_the_tab_and_page(tabs):
    page = tabs.ensure_tab("old", "AA", slot=1)
    _reading(tabs, "old", 100.0)
    assert tabs.find_key(slot=1) == "old" and tabs.find_key(address="AA") == "old"
    tabs.rekey("old", "new")
    assert tabs.tab_keys() == ["new"] and tabs.pages.get("new") is page and page.key == "new"
    assert tabs.selected_user == "new" and tabs.address_of("new") == "AA"


# --- alerts ------------------------------------------------------------------


def test_in_range_shows_no_icon(tabs):
    _reading(tabs, "A", 120.0)
    header = tabs.header("A")
    assert not header.shows_alert_icon() and "in range" in header.toolTip()


@pytest.mark.parametrize(("glucose", "word"), [(65.0, "low"), (200.0, "high")])
def test_a_warning_shows_the_triangle_but_does_not_blink(tabs, glucose, word):
    _reading(tabs, "A", glucose)
    header = tabs.header("A")
    assert header.shows_alert_icon() and word in header.toolTip()
    assert "▲" not in header.toolTip() and "▼" not in header.toolTip()
    assert tabs.tint_of("A") != "blink" and tabs.tint_of("A") != "solid"


def test_the_tooltip_spells_out_the_level(tabs):
    _reading(tabs, "A", 262.0)
    assert "critically high" in tabs.header("A").toolTip()


def test_critical_on_an_unseen_tab_blinks_until_it_is_opened(tabs):
    tabs.ensure_tab("A")
    tabs.ensure_tab("B")  # A is selected
    _reading(tabs, "B", 300.0)
    assert tabs.tint_of("B") == "blink" and tabs._blink.isActive()
    tabs.select("B")  # opening it acknowledges it
    assert tabs.tint_of("B") == "solid" and tabs.tint_of("B") != "blink"
    assert not tabs._blink.isActive()


def test_critical_on_the_tab_already_open_goes_straight_to_solid(tabs):
    _reading(tabs, "A", 40.0)  # A is the selected tab
    assert tabs.tint_of("A") == "solid" and not tabs._blink.isActive()


def test_leaving_an_acknowledged_critical_tab_does_not_restart_the_blink(tabs):
    tabs.ensure_tab("A")
    tabs.ensure_tab("B")
    _reading(tabs, "B", 300.0)
    tabs.select("B")
    tabs.select("A")
    _reading(tabs, "B", 310.0)
    assert tabs.tint_of("B") == "solid" and tabs.tint_of("B") != "blink"


def test_recovering_then_going_critical_again_blinks_afresh(tabs):
    tabs.ensure_tab("A")
    tabs.ensure_tab("B")
    _reading(tabs, "B", 300.0)
    tabs.select("B")
    tabs.select("A")
    _reading(tabs, "B", 120.0)  # recovered
    assert tabs.tint_of("B") != "solid" and not tabs.header("B").shows_alert_icon()
    _reading(tabs, "B", 320.0)
    assert tabs.tint_of("B") == "blink"


def test_the_blink_phase_toggles_on_the_shared_timer(tabs):
    tabs.ensure_tab("A")
    tabs.ensure_tab("B")
    _reading(tabs, "B", 300.0)
    assert tabs._bar.blink_on is False
    tabs.tick_blink()
    assert tabs._bar.blink_on is True
    tabs.tick_blink()
    assert tabs._bar.blink_on is False


def test_going_offline_clears_the_alert_and_the_blink(tabs):
    tabs.ensure_tab("A")
    tabs.ensure_tab("B", "BB")
    _reading(tabs, "B", 300.0, dev="BB")
    tabs.mark_device_offline("BB")
    header = tabs.header("B")
    assert tabs.is_offline("B") and header.offline and not header.shows_alert_icon()
    assert tabs.tint_of("B") != "blink" and not tabs._blink.isActive()


def test_new_thresholds_re_evaluate_every_tab(tabs):
    _reading(tabs, "A", 190.0)
    assert tabs.header("A").shows_alert_icon()
    tabs.set_thresholds({**T, "tar1_above": 200.0})
    assert not tabs.header("A").shows_alert_icon()
    tabs.set_thresholds({**T, "tar1_above": 150.0, "tar2_above": 185.0})
    assert tabs.tint_of("A") == "solid"  # 190 is now critical, and A is the open tab


def test_only_the_alerting_sensor_alerts(tabs):
    for key, glucose in (("A", 120.0), ("B", 300.0), ("C", 65.0)):
        _reading(tabs, key, glucose)
    assert not tabs.header("A").shows_alert_icon()
    assert tabs.header("B").alert.level.value == "critical"
    assert tabs.header("C").alert.level.value == "warning"


# --- the "+" button --------------------------------------------------------


def test_the_plus_button_asks_to_connect_another_sensor(tabs):
    asked = []
    tabs.connect_requested.connect(lambda: asked.append(1))
    tabs.ensure_tab("A")
    tabs.plus_button.click()
    assert asked == [1]


def test_the_plus_button_shares_the_strip_with_the_tabs(tabs):
    assert not tabs.tab_bar_visible()  # start screen: its own big button instead
    tabs.ensure_tab("A")
    assert tabs.tab_bar_visible() and not tabs.plus_button.isHidden()
    tabs.set_model_only(True)
    assert not tabs.tab_bar_visible()  # Model Only has no board to connect


def test_a_reconnect_keeps_the_close_button_working_under_the_new_id(tabs):
    closed = []
    tabs.close_requested.connect(closed.append)
    tabs.ensure_tab("old", "AA")
    tabs.rekey("old", "new")
    tabs._close_buttons["new"].click()
    assert closed == ["new"]


def test_renaming_a_tab_keeps_its_avatar_and_name_widgets_alive():
    """Sending a person config renames the tab (slot -> patient); it once went blank."""
    names = {"A": "Nordic Glucose Sensor 1"}
    widget = SensorTabs(dict(T), 0.0, RunClock(datetime.now(UTC)), lambda key, _dev: names[key])
    try:
        widget.ensure_tab("A", "AA")
        header = widget.header("A")
        names["A"] = "test2 — Sensor 1"
        widget.refresh_labels()
        assert widget.header("A") is header
        assert header.name_text() == "test2 — Sensor 1"
        left = widget._bar.tabButton(0, QTabBar.ButtonPosition.LeftSide)
        assert left is header and header.name_text() == "test2 — Sensor 1"  # not deleted
    finally:
        widget.close()


def test_a_tab_is_drawn_down_to_the_bottom_of_the_bar(tabs):
    """Otherwise it is two-toned: page colour on top, strip colour beneath."""
    tabs.ensure_tab("A")
    tabs.resize(600, 300)
    tabs.show()
    bar = tabs._bar
    assert bar._tab_path(0).boundingRect().bottom() == pytest.approx(bar.height())


def test_the_selected_tab_is_one_solid_colour_top_to_bottom(tabs):
    """The reported 'half paint': the lower half of the selected tab showed the strip
    colour. Sample the rendered bar at the top and the bottom of the tab."""
    tabs.ensure_tab("A")
    tabs.resize(700, 300)
    tabs.show()
    bar = tabs._bar
    image = bar.grab().toImage()
    rect = bar.tabRect(0)
    x = rect.left() + 6  # a spot with no avatar / text on it
    top = image.pixelColor(x, rect.top() + 6)
    bottom = image.pixelColor(x, bar.height() - 3)
    assert top == bottom
