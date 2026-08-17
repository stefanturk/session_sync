"""Entry point for the bundled app.

A frozen app has no `python -m`, so PyInstaller needs a plain script to start
from. Everything real lives in `sessionsync.ui.app`.

A double-clicked app that fails silently is the worst thing this could do —
there is no terminal to print to — so everything is logged and any failure to
start raises a dialog.
"""

import multiprocessing
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path

LOG = Path.home() / "Library" / "Logs" / "SessionSync.log"


def _log(text: str) -> None:
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(text + "\n")
    except OSError:
        pass


def _report(error: BaseException) -> None:
    _log(traceback.format_exc())
    detail = f"{type(error).__name__}: {error}".replace('"', "'")
    try:
        subprocess.run(
            [
                "/usr/bin/osascript",
                "-e",
                f'display dialog "SessionSync could not start.\n\n{detail}\n\n'
                f'Full log: {LOG}" with title "SessionSync" '
                'buttons {"OK"} default button 1 with icon caution',
            ],
            check=False,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        pass


if __name__ == "__main__":
    # Frozen apps re-exec themselves for subprocesses; without this a child
    # would start a second copy of the window.
    multiprocessing.freeze_support()
    _log(f"--- {datetime.now():%Y-%m-%d %H:%M:%S} ---")
    try:
        from sessionsync.ui.app import main

        sys.exit(main())
    except SystemExit:
        raise
    except BaseException as error:  # noqa: BLE001 - last line before silence
        _report(error)
        sys.exit(1)
