"""Learn a Live version's set format from Live's own presets and real sets.

    python scripts/build_live_schema.py 11 "/Applications/Ableton Live 11 Standard.app" ~/Music ~/Desktop
    python scripts/build_live_schema.py 10 "/Applications/Ableton Live 10 Suite.app" ~/Music ~/Desktop

The first folder is the Live app (its presets and templates are kept as they are);
the rest hold people's own sets, from which only the structure is kept: no names
or paths leave this computer.

Writes sessionsync/adapters/ableton/schemas/live<major>.json.gz, which ships with
the app so a computer without that Live can still convert sets to it.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sessionsync.adapters.ableton.versions import SCHEMA_DIR, learn_schema  # noqa: E402

SKIP = ("Backup", "Past Revisions", ".sessionsync", ".Trash")


def corpus(roots):
    for root in roots:
        root = Path(root).expanduser()
        for pattern in ("*.als", "*.adg", "*.adv"):
            for f in root.rglob(pattern):
                if not any(part in SKIP for part in f.parts):
                    yield f


def main():
    major = int(sys.argv[1])
    files = list(corpus(sys.argv[2:3]))
    private = list(corpus(sys.argv[3:]))
    schema = learn_schema(major, files, private)
    SCHEMA_DIR.mkdir(exist_ok=True)
    out = SCHEMA_DIR / f"live{major}.json.gz"
    out.write_bytes(schema.to_json())
    print(f"Live {major}: {len(schema.counts)} elements from {len(files)} Live files + {len(private)} own sets -> {out} "
          f"({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
