"""Entry point for the bundled app.

A frozen app has no `python -m`, so PyInstaller needs a plain script to start
from. Everything real lives in `sessionsync.ui.app`.
"""

import multiprocessing
import sys

from sessionsync.ui.app import main

if __name__ == "__main__":
    # Frozen apps re-exec themselves for subprocesses; without this a child
    # would start a second copy of the window.
    multiprocessing.freeze_support()
    sys.exit(main())
