#!/usr/bin/env python3
"""
run.py — launcher for the rename_media_package.

This file exists purely so you can run the tool the same simple way as
before:

    ./run.py
    ./run.py /path/to/your/show

It just makes sure Python can find the rename_media_package next to it,
then hands off to the real entry point in rename_media_package/main.py.
You should never need to edit this file.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.main import main, _fs_module

if __name__ == "__main__":
    if len(sys.argv) > 1:
        candidate = Path(sys.argv[1]).expanduser()
        if candidate.is_dir():
            _fs_module._CLI_INITIAL_PATH = (
                candidate.resolve() if not str(candidate).startswith("/run/user") else candidate
            )
        elif candidate.is_file():
            _fs_module._CLI_INITIAL_PATH = candidate.parent
        else:
            print(f"  Warning: path not found, ignoring: {sys.argv[1]}")
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n  Interrupted.\n")
        sys.exit(0)
