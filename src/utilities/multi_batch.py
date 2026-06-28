"""
utilities.multi_batch — Multi-Batch Rename: run any rename mode across every Season
folder under a chosen root, one after another.
"""

from pathlib import Path

from ..core.dispatch import FLOW_BUILDERS, MODE_LABELS, run_rename_mode_on_folder
from ..core import display as _display
from ..core.display import BOLD, CYAN, DIM, R, ask_yn, blank, err, info, render, success, warn
from ..core.filesystem import list_media, pick_folder
from ..core.registry import UtilEntry
from .season_tools import util_renumber_season
from .setup_show import _existing_season_folders


def util_multi_batch(root: Path = None):
    render(title="Multi-Batch Rename",
           sub="Pick a folder with Season subfolders, then run a rename mode\n"
               "  on each one in turn — no need to repeat the folder picker.")

    if root is None:
        root = pick_folder(
            title="Choose the folder with Season subfolders",
            sub="Each Season NN subfolder found here will be processed one at a time."
        )

    seasons = _existing_season_folders(root)
    if not seasons:
        warn(f"No Season-named subfolders found in {root}.")
        return

    blank()
    info(f"Found {len(seasons)} season folder(s):")
    for num, path in seasons:
        print(f"    {DIM}Season {num:02d}  →  {path.name}{R}")
    blank()
    if not ask_yn(f"Process all {len(seasons)} of these?", default_yes=True, back=False):
        info("Cancelled.")
        return

    total = len(seasons)
    try:
        for i, (season_num, season_dir) in enumerate(seasons, 1):
            _display._BATCH_CONTEXT = f"Multi-Batch — Season {season_num:02d}  ({i} of {total})"

            if not season_dir.is_dir():
                render(title=f"Season {season_num:02d}",
                       context_lines=[f"Folder: {DIM}{season_dir}{R}"])
                info(f"This folder no longer exists — probably moved into another\n"
                     f"      season earlier in this batch. Skipping.")
                input("  Press Enter to continue...")
                continue

            render(title=f"Season {season_num:02d}",
                   context_lines=[f"Folder: {DIM}{season_dir}{R}"])

            files = list_media(season_dir)
            if not files:
                warn(f"No media files in {season_dir.name} — skipping.")
                input("  Press Enter to continue...")
                continue

            print(f"  {BOLD}Pick a rename mode for this season:{R}\n")
            for k, (label, example, summary) in MODE_LABELS.items():
                print(f"   {CYAN}{k}{R} {label:<24}{DIM}{example}{R}")
                print(f"      {DIM}{summary}{R}")
            blank()
            print(f"   {CYAN}move{R} Move this season under another season number")
            print(f"      {DIM}Renumber + relocate these episodes (e.g. fold Season 02 into Season 01).{R}")
            print(f"   {CYAN}skip{R} Skip this season entirely")
            blank()

            mode_choice = input(f"  {BOLD}Choice{R}: ").strip().lower()

            if mode_choice == "skip":
                info(f"Skipped Season {season_num:02d}.")
                continue

            if mode_choice == "move":
                util_renumber_season(season_dir)
                input("\n  Press Enter to continue...")
                continue

            if mode_choice not in FLOW_BUILDERS:
                err("Not a valid mode — skipping this season.")
                input("  Press Enter to continue...")
                continue

            run_rename_mode_on_folder(mode_choice, season_dir)
    finally:
        _display._BATCH_CONTEXT = None

    render(title="Multi-Batch Rename — Done")
    success(f"Finished going through {total} season folder(s).")


UTILITY_ENTRIES = [
    UtilEntry(
        "Multi-Batch Rename",
        "Run a rename mode across every Season folder, one after another.",
        util_multi_batch,
    ),
]