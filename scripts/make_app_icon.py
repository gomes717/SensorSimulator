"""Render the GlucoEcho application icon (a light-mode and a dark-mode variant).

The icon is a ring around a disc that two wave lines cut through. The geometry below was
measured off the original 76 px bitmap (ring r 34..38, disc r 27.05, wave bands 3.2 thick), so
the icon is now resolution independent: this script turns it into

    src/assets/icons/glucoecho_light.{svg,png,ico}   dark ink, for light title bars / taskbars
    src/assets/icons/glucoecho_dark.{svg,png,ico}    light ink, for dark title bars / taskbars

The ring gap and the wave lines are transparent (real cut-outs), so one file works on any
background. Re-run after changing the geometry:  ``uv run python scripts/make_app_icon.py``
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPainterPath

OUT_DIR = Path(__file__).resolve().parent.parent / "src" / "assets" / "icons"

# Ink colours: the app's own light / dark text colours (gui/theme.py).
INK = {"light": "#141414", "dark": "#f2f2f2"}

CENTER = 38.0
RING_OUTER, RING_INNER, DISC = 38.0, 34.0, 27.05
BAND_HALF = 1.6  # the wave bands are 3.2 thick, measured vertically
PAD = 2.0  # transparent margin around the ring, in icon units
VIEW = 2 * CENTER + 2 * PAD

# Wave centre lines: (x, y) every 1 unit from the original, flat beyond the disc on both sides.
UPPER = (
    (14, 37.8), (15, 37.8), (16, 37.71), (17, 37.58), (18, 37.4), (19, 37.12), (20, 36.74),
    (21, 36.25), (22, 35.64), (23, 34.91), (24, 34.07), (25, 33.16), (26, 32.18), (27, 31.16),
    (28, 30.14), (29, 29.15), (30, 28.26), (31, 27.53), (32, 26.99), (33, 26.68), (34, 26.62),
    (35, 26.81), (36, 27.23), (37, 27.87), (38, 28.71), (39, 29.68), (40, 30.71), (41, 31.74),
    (42, 32.73), (43, 33.69), (44, 34.55), (45, 35.32), (46, 35.96), (47, 36.49), (48, 36.92),
    (49, 37.26), (50, 37.5), (51, 37.66), (52, 37.78), (53, 37.89), (54, 37.96), (55, 38.01),
    (56, 38.06), (57, 38.09), (58, 38.11), (59, 38.13), (60, 38.16), (61, 38.18), (62, 38.19),
)  # fmt: skip
LOWER = (
    (14, 50.29), (15, 50.29), (16, 50.24), (17, 50.18), (18, 50.08), (19, 49.93), (20, 49.75),
    (21, 49.54), (22, 49.27), (23, 48.9), (24, 48.41), (25, 47.82), (26, 47.14), (27, 46.35),
    (28, 45.45), (29, 44.47), (30, 43.44), (31, 42.39), (32, 41.38), (33, 40.48), (34, 39.74),
    (35, 39.19), (36, 38.84), (37, 38.74), (38, 38.93), (39, 39.38), (40, 40.04), (41, 40.86),
    (42, 41.8), (43, 42.81), (44, 43.83), (45, 44.82), (46, 45.76), (47, 46.63), (48, 47.42),
    (49, 48.1), (50, 48.64), (51, 49.06), (52, 49.39), (53, 49.63), (54, 49.82), (55, 49.96),
    (56, 50.07), (57, 50.15), (58, 50.2), (59, 50.2), (60, 50.2), (61, 50.2), (62, 50.2),
)  # fmt: skip

ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
PNG_SIZE = 1024
_STEP = 0.05  # sampling step of the smoothed centre line, in icon units


def _catmull_rom(points: list[tuple[float, float]], x: float) -> float:
    """Height of the Catmull-Rom spline through *points* (x ascending) at *x*."""
    i = next(k for k, p in enumerate(points) if p[0] > x) - 1
    (_, y0), (x1, y1), (x2, y2), (_, y3) = points[i - 1 : i + 3]
    t = (x - x1) / (x2 - x1)
    return 0.5 * (
        2 * y1
        + (-y0 + y2) * t
        + (2 * y0 - 5 * y1 + 4 * y2 - y3) * t * t
        + (-y0 + 3 * y1 - 3 * y2 + y3) * t**3
    )


def _band(centre: tuple[tuple[float, float], ...]) -> QPainterPath:
    """A closed band of vertical thickness 2*BAND_HALF along the smoothed *centre* line,
    running flat out to both sides of the disc."""
    left, right = centre[0][1], centre[-1][1]
    pts = [(-2.0, left), (6.0, left), *centre, (70.0, right), (78.0, right)]
    xs = [6.0 + k * _STEP for k in range(int((70.0 - 6.0) / _STEP) + 1)]
    ys = [_catmull_rom(pts, min(x, 69.999)) for x in xs]
    path = QPainterPath(QPointF(xs[0], ys[0] - BAND_HALF))
    for x, y in zip(xs[1:], ys[1:], strict=True):
        path.lineTo(x, y - BAND_HALF)
    for x, y in zip(reversed(xs), reversed(ys), strict=True):
        path.lineTo(x, y + BAND_HALF)
    path.closeSubpath()
    return path


def _circle(radius: float) -> QPainterPath:
    path = QPainterPath()
    path.addEllipse(QPointF(CENTER, CENTER), radius, radius)
    return path


def glyph() -> QPainterPath:
    """The icon as one path in icon units: the ring plus the wave-cut disc."""
    ring = _circle(RING_OUTER).subtracted(_circle(RING_INNER))
    waves = _band(UPPER).united(_band(LOWER))
    return ring.united(_circle(DISC).subtracted(waves))


def to_svg(path: QPainterPath, ink: str) -> str:
    """The SVG text of *path* (nonzero and even-odd agree: the subpaths never overlap)."""
    parts: list[str] = []
    i = 0
    while i < path.elementCount():
        el = path.elementAt(i)
        if el.isMoveTo():
            parts.append(("Z " if parts else "") + f"M{el.x:.3f} {el.y:.3f}")
        elif el.isLineTo():
            parts.append(f"L{el.x:.3f} {el.y:.3f}")
        else:  # a cubic: this element and the next two are its control points and end
            c2, end = path.elementAt(i + 1), path.elementAt(i + 2)
            parts.append(f"C{el.x:.3f} {el.y:.3f} {c2.x:.3f} {c2.y:.3f} {end.x:.3f} {end.y:.3f}")
            i += 2
        i += 1
    d = " ".join(parts) + " Z"
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{-PAD} {-PAD} {VIEW} {VIEW}" '
        f'width="{VIEW:g}" height="{VIEW:g}">\n'
        f"  <title>GlucoEcho</title>\n"
        f'  <path fill="{ink}" fill-rule="evenodd" d="{d}"/>\n'
        f"</svg>\n"
    )


def render(path: QPainterPath, ink: str, size: int) -> QImage:
    """Rasterise *path* into a transparent *size* x *size* image."""
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    scale = size / VIEW
    painter.scale(scale, scale)
    painter.translate(PAD, PAD)
    painter.fillPath(path, QColor(ink))
    painter.end()
    return image


def _png_bytes(image: QImage) -> bytes:
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buf, "PNG")
    return bytes(data.data())


def write_ico(path: Path, images: list[tuple[int, bytes]]) -> None:
    """A Windows .ico holding PNG-compressed images (*size*, png bytes), Vista and later."""
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blobs = b"", b""
    for size, png in images:
        dim = 0 if size >= 256 else size  # 0 means 256
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset + len(blobs))
        blobs += png
    path.write_bytes(header + entries + blobs)


def main() -> None:
    """Write the SVG, the 1024 px PNG and the multi-size ICO for both variants."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    shape = glyph()
    for variant, ink in INK.items():
        stem = OUT_DIR / f"glucoecho_{variant}"
        stem.with_suffix(".svg").write_text(to_svg(shape, ink), encoding="utf-8")
        render(shape, ink, PNG_SIZE).save(str(stem.with_suffix(".png")))
        write_ico(
            stem.with_suffix(".ico"),
            [(size, _png_bytes(render(shape, ink, size))) for size in ICO_SIZES],
        )
        print(f"wrote {stem.name}.svg / .png / .ico")


if __name__ == "__main__":
    sys.exit(main())
