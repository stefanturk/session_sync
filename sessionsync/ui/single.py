"""One Session Sync per person, however many times it is started.

On a Mac, clicking "▶ Open in Live" while the app is running hands the link to
the running app (macOS does that). Windows starts a *second* copy with the link
on its command line instead, so that copy passes the link to the first one
here and quits — two copies would be two engines on the same songs.
"""

from __future__ import annotations

import getpass
import hashlib

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

#: Sent when the app was simply started again: bring the window forward.
SHOW = "show"


def server_name(user: str | None = None) -> str:
    who = user or _user()
    return "SessionSync-" + hashlib.sha1(who.encode("utf-8")).hexdigest()[:12]


def _user() -> str:
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001 - no login name; still one per machine
        return "anyone"


def forward(message: str, *, name: str | None = None, timeout_ms: int = 800) -> bool:
    """Give `message` to an already-running Session Sync. False if there is none."""
    socket = QLocalSocket()
    socket.connectToServer(name or server_name())
    if not socket.waitForConnected(timeout_ms):
        return False
    socket.write(message.encode("utf-8") + b"\n")
    socket.flush()
    socket.waitForBytesWritten(timeout_ms)
    socket.disconnectFromServer()
    if socket.state() != QLocalSocket.UnconnectedState:
        socket.waitForDisconnected(timeout_ms)
    return True


class Listener(QObject):
    """The running app's end: emits each message another copy forwarded."""

    received = Signal(str)

    def __init__(self, name: str | None = None, parent=None):
        super().__init__(parent)
        self.name = name or server_name()
        self.server = QLocalServer(self)
        self.server.newConnection.connect(self._connected)

    def listen(self) -> bool:
        if self.server.listen(self.name):
            return True
        # A copy that crashed can leave its socket file behind (Mac, Linux).
        QLocalServer.removeServer(self.name)
        return self.server.listen(self.name)

    def close(self) -> None:
        self.server.close()

    def _connected(self) -> None:
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            buffer = bytearray()

            def read(socket=socket, buffer=buffer):
                buffer.extend(bytes(socket.readAll()))
                while b"\n" in buffer:
                    line, _, rest = bytes(buffer).partition(b"\n")
                    buffer[:] = rest
                    text = line.decode("utf-8", errors="replace").strip()
                    if text:
                        self.received.emit(text)

            socket.readyRead.connect(read)
            socket.disconnected.connect(socket.deleteLater)
            if socket.bytesAvailable():
                read()
