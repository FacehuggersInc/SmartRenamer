"""
main — Entry point: command-line argument parsing, the main menu,
and the top-level program loop.
"""

import sys
from pathlib import Path

from .core.dispatch import FLOW_BUILDERS, MODE_LABELS, run_rename_mode_on_folder
from .core.display import BOLD, Back, CYAN, DIM, R, ask_yn, blank, err, render
from .core import filesystem as _fs_module
from .core.filesystem import pick_folder
from .utilities.basic import util_preview, util_rename_show, util_split
from .utilities.multi_batch import util_multi_batch
from .utilities.overwrite import util_cleanup_backups, util_overwrite_by_episode
from .utilities.season_tools import util_define_season_ranges, util_renumber_season
from .utilities.setup_show import util_setup_show


def main_menu():
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
    print(f"   {CYAN}9{R} Preview files in a folder")
    print(f"      {DIM}Just lists the media files found — no renaming.{R}")
    print(f"   {CYAN}10{R} Split into Season XX/ subfolders")
    print(f"      {DIM}Scans filenames for S01/S02 tags and sorts files into folders.{R}")
    print(f"   {CYAN}11{R} Rename show name across files")
    print(f"      {DIM}Swaps the show-name prefix on files already named ...-S01E01.{R}")
    print(f"   {CYAN}12{R} Overwrite by Episode Number")
    print(f"      {DIM}Replaces a Source file's content with a Match file's, by S/E number.{R}")
    print(f"   {CYAN}13{R} Clean up backup folders")
    print(f"      {DIM}Finds and deletes .backup_before_overwrite folders left by option 12.{R}")
    print(f"   {CYAN}14{R} Renumber / Move Season")
    print(f"      {DIM}Moves a season's episodes, appends them, and closes any gaps.{R}")
    print(f"   {CYAN}15{R} Split Into Seasons By Range")
    print(f"      {DIM}Define episode ranges — each becomes its own Season folder.{R}")
    print(f"   {CYAN}16{R} Set Up Show + Season Folders")
    print(f"      {DIM}Create season folders and pull files in from a Downloads folder.{R}")
    print(f"   {CYAN}17{R} Multi-Batch Rename")
    print(f"      {DIM}Run a rename mode across every Season folder, one after another.{R}")
    print(f"   {CYAN}q{R} Quit")
    blank()
    return input(f"  {BOLD}Choice{R}: ").strip().lower()

def main():
    while True:
        choice = main_menu()

        if choice == "q":
            print(f"\n  {DIM}Bye!{R}\n")
            break

        if choice == "9":
            util_preview(pick_folder())
            input("\n  Press Enter to return to menu...")
            continue
        if choice == "10":
            util_split(pick_folder())
            input("\n  Press Enter to return to menu...")
            continue
        if choice == "11":
            util_rename_show(pick_folder())
            input("\n  Press Enter to return to menu...")
            continue
        if choice == "12":
            util_overwrite_by_episode()
            input("\n  Press Enter to return to menu...")
            continue
        if choice == "13":
            util_cleanup_backups()
            input("\n  Press Enter to return to menu...")
            continue
        if choice == "14":
            try:
                util_renumber_season()
            except Back:
                pass
            input("\n  Press Enter to return to menu...")
            continue
        if choice == "15":
            try:
                util_define_season_ranges()
            except Back:
                pass
            input("\n  Press Enter to return to menu...")
            continue

def main():
    while True:
        choice = main_menu()

        if choice == "q":
            print(f"\n  {DIM}Bye!{R}\n")
            break

        if choice == "9":
            util_preview(pick_folder())
            input("\n  Press Enter to return to menu...")
            continue
        if choice == "10":
            util_split(pick_folder())
            input("\n  Press Enter to return to menu...")
            continue
        if choice == "11":
            util_rename_show(pick_folder())
            input("\n  Press Enter to return to menu...")
            continue
        if choice == "12":
            util_overwrite_by_episode()
            input("\n  Press Enter to return to menu...")
            continue
        if choice == "13":
            util_cleanup_backups()
            input("\n  Press Enter to return to menu...")
            continue
        if choice == "14":
            try:
                util_renumber_season()
            except Back:
                pass
            input("\n  Press Enter to return to menu...")
            continue
        if choice == "15":
            try:
                util_define_season_ranges()
            except Back:
                pass
            input("\n  Press Enter to return to menu...")
            continue
        if choice == "16":
            try:
                util_setup_show()
            except Back:
                pass
            input("\n  Press Enter to return to menu...")
            continue
        if choice == "17":
            try:
                util_multi_batch()
            except Back:
                pass
            input("\n  Press Enter to return to menu...")
            continue

        if choice not in FLOW_BUILDERS:
            err("Please enter 1–17 or q.")
            input("  Press Enter to continue...")
            continue

        folder = pick_folder()
        run_rename_mode_on_folder(choice, folder)

        blank()
        if not ask_yn("Rename another batch?", back=False):
            print(f"\n  {DIM}Done!{R}\n")
            break

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
        print(f"\n\n  {DIM}Interrupted.{R}\n")
        sys.exit(0)
