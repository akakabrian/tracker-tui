"""Entry point — `python tracker.py [path.mod] [--no-sound]`."""

from __future__ import annotations

import argparse

from tracker_tui.app import run


def main() -> None:
    p = argparse.ArgumentParser(prog="tracker-tui")
    p.add_argument("module", nargs="?", default=None,
                   help="path to a .mod/.xm/.it file to open (default: empty song)")
    p.add_argument("--no-sound", action="store_true",
                   help="disable audio output (UI-only mode)")
    args = p.parse_args()
    run(module_path=args.module, sound=not args.no_sound)


if __name__ == "__main__":
    main()
