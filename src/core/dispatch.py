"""
core.dispatch — Wires every rename mode (1-8) into a single FLOW_BUILDERS /
MODE_LABELS dispatch table, and drives one mode end-to-end
against an already-chosen folder. Used by both the normal
main() loop and the Multi-Batch utility.
"""

from pathlib import Path

from .display import Back, CYAN, DIM, R, ask, blank, err, info, render, warn
from .filesystem import list_media
from .parsers import parse_fansub
from .rename_engine import _confirm_show_name, run_rename
from ..modes.mode5_regex import flow_custom_regex
from ..modes.mode6_builder import flow_regex_builder
from ..modes.mode7_splitter import flow_token_splitter
from ..modes.mode8_trim import flow_trim_tail
from ..modes.simple_modes import flow_fansub, flow_one_pace, flow_simple, flow_sxxexx


FLOW_BUILDERS = {
    "1": flow_fansub, "2": flow_one_pace, "3": flow_simple,
    "4": flow_sxxexx, "5": flow_custom_regex, "6": flow_regex_builder,
    "7": flow_token_splitter, "8": flow_trim_tail,
}

MODE_LABELS = {
    "1": ("Standard Fansub",        "[DB]Show_-_01_(info).mkv",
          "Parses [Group]Show_-_NN_(info) style filenames into S01E01 format."),
    "2": ("One Pace / Group+Range", "[Group][841-842] Arc 10 [720p].mp4",
          "Handles arc names and episode ranges from One Pace style releases."),
    "3": ("Simple Numbered",        "01.mkv / Episode 05.mkv",
          "Plain numbered files — turns 01.mkv into Show - S01E01.mkv."),
    "4": ("Normalize S##E##",       "old.show.S01E04.1080p.mkv",
          "Cleans up messy filenames that already contain a season+episode."),
    "5": ("Raw Regex",              "type your own pattern",
          "For advanced users — type your own regex with named groups."),
    "6": ("Build From Sample",      "guided, works on any format",
          "Step-by-step builder: pick a sample, identify its parts, rebuild."),
    "7": ("Split & Label",          "Blue.Box.S01E07.Title.1080p...-GROUP",
          "For dot/dash-bombed filenames — split on a separator, label each piece."),
    "8": ("Trim Filename",          "Show - S01E01 - Title.1080p.WEBRip-GROUP",
          "Already-correct names with junk attached — split, keep a range, drop the rest."),
}

def setup_show_and_season(files, choice):
    detected_show = ""
    if choice == "1":
        detected_show = parse_fansub(files[0].name).get("show_guess", "")

    show = ask("Show name", hint="Start of every renamed file.", default=detected_show)
    if not show:
        err("Show name cannot be empty.")
        return None, None
    raw_s  = ask("Season", default="1")
    season = int(raw_s) if raw_s.isdigit() else 1
    return show, season

def run_rename_mode_on_folder(choice: str, folder: Path) -> bool:
    """
    Runs one rename mode (1–8) against an already-chosen folder — no
    folder picker shown. Used both by the normal main() loop (after its
    own pick_folder() call) and by the Multi-Batch tool, which supplies
    a different season folder each time and sets _BATCH_CONTEXT so every
    screen shown along the way reflects which batch step this is.

    Returns True if the mode completed (applied or explicitly cancelled
    by the user), False if there was nothing to do (e.g. empty folder)
    so the caller can decide whether to count it as a completed step.
    """
    label, example, summary = MODE_LABELS[choice]
    render(title=f"Mode: {label}",
           context_lines=[f"Folder: {DIM}{folder}{R}", f"Matches: {DIM}{example}{R}"])

    files = list_media(folder)
    if not files:
        warn("No media files found in that folder.")
        input("  Press Enter to continue...")
        return False

    render(title=f"Mode: {label}",
           context_lines=[f"Folder: {DIM}{folder}{R}", f"Files found: {len(files)}"])

    show, season = setup_show_and_season(files, choice)
    if show is None:
        input("  Press Enter to continue...")
        return False

    build_fn = None
    while build_fn is None:
        try:
            build_fn = FLOW_BUILDERS[choice](files, show, season, folder)
        except Back:
            render(title=f"Mode: {label}", context_lines=[f"Folder: {DIM}{folder}{R}"])
            show, season = setup_show_and_season(files, choice)
            if show is None:
                break
    if build_fn is None:
        return False

    while True:
        render(title="Dry run — no files changed yet",
               context_lines=[f"Mode: {label}", f"Folder: {DIM}{folder}{R}"])
        run_rename(files, folder, dry_run=True, build_fn=build_fn)

        blank()
        print(f"  {CYAN}1{R} Apply for real   {CYAN}2{R} Change settings   {CYAN}3{R} Cancel")
        action = input(f"  Choice: ").strip()

        if action == "1":
            files = list_media(folder)
            if not _confirm_show_name(files, folder, build_fn):
                continue
            render(title="Renaming…", context_lines=[f"Folder: {DIM}{folder}{R}"])
            run_rename(files, folder, dry_run=False, build_fn=build_fn)
            input("\n  Press Enter to continue...")
            return True
        elif action == "2":
            try:
                build_fn = FLOW_BUILDERS[choice](files, show, season, folder)
            except Back:
                pass
        elif action == "3":
            info("Cancelled — no files changed.")
            return True
        else:
            err("Enter 1, 2, or 3.")
            input("  Press Enter to continue...")
