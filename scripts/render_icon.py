"""Draw the SessionSync Dock icon.

Rendered with Qt, which is already a dependency, rather than adding an image
library or checking a binary into the repository.

    python scripts/render_icon.py out.png [size]

The mark: a white waveform inside a black, white-outlined rounded square, with a
small purple sync arrow curling around it. It has to stay legible at 16 px in the menu bar, so there are five bars, no
thin strokes, and no text.
"""

from __future__ import annotations

import math
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QBrush,
    QColor,
    QGuiApplication,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
)

# Ableton Live's palette: a black tile with a white keyline, a white mark and a
# purple accent.
BACKGROUND = "#0b0b0d"
OUTLINE = "#ffffff"
WAVE = "#ffffff"
ARROW = "#9b6bff"

# Bar heights as a fraction of the icon, middle-out. Deliberately asymmetric so
# it reads as audio rather than as a chart.
BARS = (0.30, 0.58, 0.86, 0.62, 0.38)


def render(size: int) -> QImage:
    image = QImage(size, size, QImage.Format_ARGB32)
    image.fill(Qt.transparent)

    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    _background(painter, size)
    _waveform(painter, size)
    _sync_arrow(painter, size)
    painter.end()
    return image


def _background(painter: QPainter, size: int) -> None:
    # macOS icons sit inside a rounded square with a little breathing room.
    inset = size * 0.06
    radius = size * 0.225
    # A white tile with a slightly smaller black one on top: the white is the
    # outline, wholly outside the black.
    outer = QRectF(inset, inset, size - inset * 2, size - inset * 2)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QBrush(QColor(OUTLINE)))
    painter.drawRoundedRect(outer, radius, radius)

    edge = size * 0.03
    painter.setBrush(QBrush(QColor(BACKGROUND)))
    painter.drawRoundedRect(
        outer.adjusted(edge, edge, -edge, -edge), radius - edge, radius - edge
    )


def _waveform(painter: QPainter, size: int) -> None:
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(WAVE))

    # Sized to sit wholly inside the ring drawn below — the two marks touching
    # is what made the first attempt read as noise at small sizes.
    width = size * 0.068
    gap = size * 0.044
    total = len(BARS) * width + (len(BARS) - 1) * gap
    x = (size - total) / 2
    centre = size * 0.5

    for fraction in BARS:
        height = size * fraction * 0.46
        painter.drawRoundedRect(
            QRectF(x, centre - height / 2, width, height), width / 2, width / 2
        )
        x += width + gap


# The ring is broken rather than closed, so it reads as a direction of travel
# instead of a plain circle. The head sits on the tangent at the arc's end.
ARC_START_DEG = 118.0
ARC_SPAN_DEG = 284.0


def _sync_arrow(painter: QPainter, size: int) -> None:
    margin = size * 0.195
    box = QRectF(margin, margin, size - margin * 2, size - margin * 2)

    pen = QPen(QColor(ARROW))
    pen.setWidthF(size * 0.048)
    pen.setCapStyle(Qt.RoundCap)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    painter.drawArc(box, int(ARC_START_DEG * 16), int(ARC_SPAN_DEG * 16))

    end = math.radians(ARC_START_DEG + ARC_SPAN_DEG)
    cx, cy = box.center().x(), box.center().y()
    rx, ry = box.width() / 2, box.height() / 2
    tip = QPointF(cx + rx * math.cos(end), cy - ry * math.sin(end))

    # Qt sweeps counter-clockwise, so the tangent at the end points this way.
    tangent = math.atan2(-math.cos(end), -math.sin(end))

    head = size * 0.08
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(ARROW))
    painter.save()
    painter.translate(tip)
    painter.rotate(math.degrees(tangent))
    path = QPainterPath()
    path.moveTo(head, 0)
    path.lineTo(-head * 0.75, -head * 0.8)
    path.lineTo(-head * 0.75, head * 0.8)
    path.closeSubpath()
    painter.drawPath(path)
    painter.restore()


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    QGuiApplication([])
    size = int(sys.argv[2]) if len(sys.argv) > 2 else 1024
    if not render(size).save(sys.argv[1]):
        print(f"Could not write {sys.argv[1]}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
