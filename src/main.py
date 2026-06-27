"""
main — Entry point: command-line argument parsing, the main menu,
and the top-level program loop.

This file is intentionally generic. It never lists individual utilities
by name — it discovers them automatically from the utilities package
(see core.registry) and assigns menu numbers in order, right after the
rename modes. Adding a new utility means writing one new file under
utilities/ with a UTILITY_ENTRIES list at the bottom; this file does
not change.
"""

import sys
from pathlib import Path

from .core.dispatch import FLOW_BUILDERS, MODE_LABELS, run_rename_mode_on_folder
from .core.display import BOLD, Back, CYAN, DIM, R, ask_yn, blank, err, render
from .core import filesystem as _fs_module
from .core.filesystem import pick_folder
from .core.registry import discover_utilities
from . import utilities

UTILITY_ENTRIES = discover_utilities(utilities)

# Menu numbers for utilities start right after the rename modes and are
# assigned purely by position — nothing here is hand-numbered.
_MODE_KEYS = list(MODE_LABELS.keys())
_FIRST_UTIL_NUM = len(_MODE_KEYS) + 1
UTILITY_KEYS = {
    str(_FIRST_UTIL_NUM + i): entry
    for i, entry in enumerate(UTILITY_ENTRIES)
}


def main_menu() -> str:
    context = []
    if _fs_module._CLI_INITIAL_PATH is not None:
        context.append(f"📁 Starting folder ready: {DIM}{_fs_module._CLI_INITIAL_PATH}{R}")

    render(title="Media Batch Renamer · Linux Edition",
           context_lines=context,
           sub="Type 'b' at most prompts to go back a step.")

    print(f"  {BOLD}Rename modes:{R}")
    for k, (label, example, summary) in MODE_LABELS.items():
        print(f"   {CYAN}{k}{R} {label:<24}{DIM}{example}{R}")
        print(f"      {DIM}{summary}{R}")

    blank()
    print(f"  {BOLD}Utilities:{R}")
    for k, entry in UTILITY_KEYS.items():
        print(f"   {CYAN}{k}{R} {entry.label}")
        print(f"      {DIM}{entry.summary}{R}")

    print(f"   {CYAN}q{R} Quit")
    blank()
    return input(f"  {BOLD}Choice{R}: ").strip().lower()


def _run_utility(entry) -> None:
    """Call a utility entry's function, supplying a picked folder first
    if its signature asks for one, and swallowing Back() the same way
    every other navigable flow in this program does."""
    try:
        if entry.takes_folder:
            entry.func(pick_folder())
        else:
            entry.func()
    except Back:
        pass
    input("\n  Press Enter to return to menu...")


def main() -> None:
    while True:
        choice = main_menu()

        match choice:
            case "q":
                print(f"\n  {DIM}Bye!{R}\n")
                return

            case key if key in UTILITY_KEYS:
                _run_utility(UTILITY_KEYS[key])

            case key if key in FLOW_BUILDERS:
                folder = pick_folder()
                run_rename_mode_on_folder(key, folder)
                blank()
                if not ask_yn("Rename another batch?", back=False):
                    print(f"\n  {DIM}Done!{R}\n")
                    return

            case _:
                last_key = max(
                    (int(k) for k in (*FLOW_BUILDERS, *UTILITY_KEYS)),
                    default=1,
                )
                err(f"Please enter 1–{last_key} or q.")
                input("  Press Enter to continue...")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        candidate = Path(sys.argv[1]).expanduser()
        match candidate:
            case _ if candidate.is_dir():
                _fs_module._CLI_INITIAL_PATH = (
                    candidate.resolve() if not str(candidate).startswith("/run/user") else candidate
                )
            case _ if candidate.is_file():
                _fs_module._CLI_INITIAL_PATH = candidate.parent
            case _:
                print(f"  Warning: path not found, ignoring: {sys.argv[1]}")
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n\n  {DIM}Interrupted.{R}\n")
        sys.exit(0)
