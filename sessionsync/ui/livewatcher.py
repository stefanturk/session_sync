"""Notices, within a few seconds, when Live opens or closes one of your songs.

The tabs only look at Live every minute or three, which is fine for the
"working" note but far too slow for "you just closed the song — push it?".
This checks Live alone (is it running, and which set did it last load) every
few seconds on its own thread, and says when that changes.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot

from sessionsync.core import livewatch

INTERVAL_MS = 3000


class _Probe(QObject):
    seen = Signal(str)  # the set Live has open, or "" for none

    def __init__(self, interval_ms: int):
        super().__init__()
        self.interval_ms = interval_ms
        self.timer = None

    @Slot()
    def start(self) -> None:
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.look)
        self.timer.start(self.interval_ms)
        self.look()

    @Slot()
    def stop(self) -> None:
        if self.timer is not None:
            self.timer.stop()

    @Slot()
    def look(self) -> None:
        try:
            document = livewatch.open_document()
        except Exception:  # noqa: BLE001 - best effort; never take the app down
            return
        self.seen.emit(str(document) if document else "")


class LiveWatcher(QObject):
    """Emits `changed(before, now)` — paths of the set Live has open, "" for none."""

    changed = Signal(str, str)

    def __init__(self, interval_ms: int = INTERVAL_MS, parent=None):
        super().__init__(parent)
        self.current: str | None = None  # None until the first look
        self._thread = QThread()
        self._probe = _Probe(interval_ms)
        self._probe.moveToThread(self._thread)
        self._thread.started.connect(self._probe.start)
        self._probe.seen.connect(self._seen)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        if self._thread.isRunning():
            QTimer.singleShot(0, self._probe, self._probe.stop)
            self._thread.quit()
            self._thread.wait(5000)

    @Slot(str)
    def _seen(self, document: str) -> None:
        before = self.current
        self.current = document
        if before is not None and before != document:
            self.changed.emit(before, document)
        elif before is None and document:
            self.changed.emit("", document)
