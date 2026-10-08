"""The application's name (GlucoEcho) and its light / dark icon (gui/branding.py)."""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication

from gui import branding
from gui.main_window import MainWindow
from gui.sensor_tabs import _EmptyState


@pytest.fixture(scope="module", autouse=True)
def _app():
    return QApplication.instance() or QApplication([])


def _ink_lightness(dark: bool) -> int:
    """Lightness of the opaque pixels of the 256 px icon (the ring's left edge)."""
    image = branding.icon_for(dark).pixmap(256, 256).toImage()
    pixel = image.pixelColor(10, 128)  # inside the ring, at its widest
    assert pixel.alpha() == 255
    return pixel.lightness()


def test_the_main_window_is_titled_glucoecho():
    window = MainWindow()
    try:
        assert window.windowTitle() == "GlucoEcho" == branding.APP_NAME
    finally:
        window.sim.engines.stop_all()
        window.close()


def test_the_icon_has_a_dark_ink_for_light_mode_and_a_light_ink_for_dark_mode():
    assert _ink_lightness(dark=False) < 60
    assert _ink_lightness(dark=True) > 200


@pytest.mark.parametrize("dark", [False, True], ids=["light", "dark"])
def test_the_icon_is_crisp_at_every_size_and_transparent_between_ring_and_disc(dark):
    icon = branding.icon_for(dark)
    assert not icon.isNull()
    big = icon.pixmap(256, 256).toImage()
    assert big.pixelColor(128, 31).alpha() == 0  # the gap between the ring and the disc
    assert big.pixelColor(0, 0).alpha() == 0  # the corner outside the ring


@pytest.mark.parametrize("variant", ["light", "dark"])
@pytest.mark.parametrize("suffix", [".svg", ".png", ".ico"])
def test_the_icon_files_ship_in_every_format(variant, suffix):
    assert (branding.ICON_DIR / f"glucoecho_{variant}{suffix}").stat().st_size > 0


def test_the_start_screen_shows_the_logo_above_its_title_and_faded():
    state = _EmptyState()
    logos = state.findChildren(branding.LogoWidget)
    assert len(logos) == 1
    assert logos[0]._opacity < 1  # pyright: ignore[reportPrivateUsage]
    state.resize(500, 400)
    shot = state.grab().toImage()
    ink = [shot.pixelColor(x, y) for x in range(0, 500, 3) for y in range(0, 400, 3)]
    assert any(abs(c.lightness() - state.palette().window().color().lightness()) > 20 for c in ink)
    state.close()
