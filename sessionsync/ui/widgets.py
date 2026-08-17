"""Small custom widgets a stylesheet cannot express."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget

from sessionsync.ui import theme


class Led(QWidget):
    """The indicator light in the header.

    It pulses while something is happening, because a status word that simply
    changes is easy to miss when you glance over from Ableton — movement is
    what catches an eye that is not looking directly at the window.
    """

    DIAMETER = 8

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._colour = QColor(theme.EDGE)
        self._opacity = 1.0
        self._step = -0.06
        self.setFixedSize(self.DIAMETER + 2, self.DIAMETER + 2)

        self._timer = QTimer(self)
        self._timer.setInterval(45)
        self._timer.timeout.connect(self._pulse)

    def set_colour(self, colour: str) -> None:
        self._colour = QColor(colour)
        self.update()

    def set_pulsing(self, pulsing: bool) -> None:
        if pulsing == self._timer.isActive():
            return
        if pulsing:
            self._timer.start()
        else:
            self._timer.stop()
            self._opacity = 1.0
            self.update()

    def _pulse(self) -> None:
        self._opacity += self._step
        if self._opacity <= 0.25:
            self._opacity, self._step = 0.25, abs(self._step)
        elif self._opacity >= 1.0:
            self._opacity, self._step = 1.0, -abs(self._step)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setOpacity(self._opacity)
        painter.setPen(Qt.NoPen)
        painter.setBrush(self._colour)
        painter.drawEllipse(QRectF(1, 1, self.DIAMETER, self.DIAMETER))
