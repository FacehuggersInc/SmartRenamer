"""
utilities.setup_show — Set Up Show + Season Folders: create a show's season folders
and pull files in from a remembered Downloads folder.
"""

import re
import json
import shutil
from pathlib import Path

from ..core.config import _config_dir
from ..core.display import BOLD, Back, CYAN, DIM, GREEN, R, YELLOW, ask, ask_yn, blank, dryline, err, info, render, success, warn
from ..core.filesystem import list_media, pick_folder
from ..core.registry import UtilEntry
from .season_tools import _detect_season_from_foldername


SETUP_PAIR_FILE = _config_dir() / "setup_pair.json"

def _load_setup_pair() -> tuple[Path, Path] | None:
    if SETUP_PAIR_FILE.exists():
        try:
            data = json.loads(SETUP_PAIR_FILE.read_text())
            src, dl = Path(data["source"]), Path(data["downloads"])
            if src.is_dir() and dl.is_dir():
                return src, dl
        except Exception:
            pass
    return None

def _save_setup_pair(source: Path, downloads: Path) -> None:
    SETUP_PAIR_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETUP_PAIR_FILE.write_text(json.dumps(
        {"source": str(source), "downloads": str(downloads)}, indent=2
    ))

def _existing_season_folders(folder: Path) -> list[tuple[int, Path]]:
    """Return (season_number, path) for every immediate subfolder of
    `folder` whose name looks like a season folder, sorted by number."""
    found = []
    try:
        for sub in folder.iterdir():
            if sub.is_dir():
                num = _detect_season_from_foldername(sub.name)
                if num is not None:
                    found.append((num, sub))
    except PermissionError:
        pass
    return sorted(found, key=lambda pair: pair[0])

def _list_subfolders(folder: Path) -> list[Path]:
    try:
        return sorted(
            e for e in folder.iterdir()
            if e.is_dir() and not e.name.startswith(".")
        )
    except PermissionError:
        return []

def _pick_subfolder_at_level(current: Path, season_num: int, top: Path) -> tuple[str, Path | None]:
    """
    Show the subfolders directly inside `current`, and — if `current`
    itself already has media files — offer to use it as-is instead of
    descending further. Returns a (action, path) pair:
      ("use", current)   — use the current folder's own files
      ("descend", child) — go into this child next
      ("skip", None)     — user typed 'done', skip the season
    Raises Back() to step up one level (or out of the picker entirely
    if already at the top).
    """
    while True:
        subfolders = _list_subfolders(current)
        own_files = list_media(current)

        render(
            title=f"Pick a download folder for Season {season_num:02d}",
            context_lines=[f"Looking in: {DIM}{current}{R}"],
            sub="Pick the subfolder whose files belong to this season — its\n"
                "  contents get moved as a whole into the season folder.",
        )

        if own_files:
            print(f"  {GREEN}This folder has {len(own_files)} media file(s) of its own.{R}")
            blank()

        if not subfolders:
            if not own_files:
                warn("No subfolders here, and no media files either.")
            blank()
            opts = []
            if own_files:
                opts.append(f"{BOLD}Enter{R} = use this folder")
            opts.append(f"{BOLD}b{R} = go back up a level")
            opts.append(f"{BOLD}done{R} = skip this season")
            print(f"  {DIM}{'  ·  '.join(opts)}{R}")
        else:
            for i, sub in enumerate(subfolders, 1):
                count = len(list_media(sub))
                has_more = bool(_list_subfolders(sub))
                if count:
                    tag = f"{GREEN}{count} file(s){R}"
                elif has_more:
                    tag = f"{YELLOW}no files — has subfolders{R}"
                else:
                    tag = f"{DIM}empty{R}"
                print(f"  {CYAN}{i:>3}{R}  {sub.name}  {DIM}({tag}){R}")
            blank()
            print(f"  {DIM}Type a number, or part of a name to search.{R}")
            hint = "'b' = go back up a level · 'done' = skip this season"
            if own_files:
                hint = "Enter = use this folder  ·  " + hint
            print(f"  {DIM}{hint}{R}")
        blank()

        raw = input(f"  Choice: ").strip()
        low = raw.lower()

        if raw == "" and own_files:
            return "use", current

        if low in ("b", "back"):
            if current == top:
                raise Back()
            current = current.parent
            continue
        if low == "done":
            return "skip", None

        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(subfolders):
                return "descend", subfolders[idx]
            err(f"Enter 1–{len(subfolders)}, a name, 'b', or 'done'.")
            input("  Press Enter to continue...")
            continue

        if not subfolders:
            err("Enter 'b' or 'done'" + (", or Enter to use this folder" if own_files else "") + ".")
            input("  Press Enter to continue...")
            continue

        term = raw.lower()
        matches = [s for s in subfolders if term in s.name.lower()]
        if not matches:
            err(f"No subfolders match '{raw}'.")
            input("  Press Enter to continue...")
            continue
        if len(matches) == 1:
            return "descend", matches[0]

        render(title=f"Matches for \"{raw}\"", context_lines=[f"Looking in: {DIM}{current}{R}"])
        for i, sub in enumerate(matches, 1):
            count = len(list_media(sub))
            print(f"  {CYAN}{i}{R}  {sub.name}  {DIM}({count} file(s)){R}")
        blank()
        pick = input(f"  Number (Enter=cancel): ").strip()
        if pick.isdigit() and 1 <= int(pick) <= len(matches):
            return "descend", matches[int(pick) - 1]

def _pick_files_for_season(downloads_folder: Path, season_num: int) -> tuple[list[Path], Path | None]:
    """
    Interactive picker: shows the subfolders inside the Downloads folder
    (each one usually a single release/batch, e.g. "MyShow.S01.Complete")
    and lets the user descend into them — a folder that itself has no
    media files but DOES have further subfolders (e.g. a "Disc 1"/"Disc
    2" structure, or a generic download-client wrapper folder) can be
    entered too, repeating until a folder with actual media files in it
    is found, or the user backs all the way out. Returns every media
    file found inside the finally-chosen folder — these get moved as a
    whole. Returns (files, chosen_subfolder) so the caller can also
    clean up the empty subfolder afterward; (empty list, None) if the
    season is skipped or there's nothing usable to pick.
    """
    top_subfolders = _list_subfolders(downloads_folder)

    if not top_subfolders:
        warn("No subfolders found in the Downloads folder.")
        blank()
        if ask_yn("Use the media files directly inside the Downloads folder instead?",
                  default_yes=False, back=False):
            return list_media(downloads_folder), None
        return [], None

    current = downloads_folder
    while True:
        action, target = _pick_subfolder_at_level(current, season_num, downloads_folder)

        if action == "skip":
            return [], None

        if action == "use":
            return list_media(target), target

        # action == "descend": move into that child and loop. The picker
        # itself already only ever offers a child as a descend target,
        # so we don't need to re-check for media files here — the next
        # iteration's render will show "Enter = use this folder" itself
        # if the child turns out to have files.
        current = target

def _confirm_and_move(files: list[Path], dest: Path) -> tuple[int, int]:
    """Show a dry-run preview of moving files into dest, then apply on confirm."""
    if not files:
        return 0, 0

    render(title=f"Move into {dest.name}", context_lines=[f"Destination: {DIM}{dest}{R}"])
    print(f"  {BOLD}{len(files)} file(s) will be moved:{R}")
    blank()
    for f in files:
        dryline(f"{DIM}{f.name}{R}")
        print(f"           {GREEN}→ {dest.name}/{f.name}{R}")

    blank()
    if not ask_yn("Move these files?", default_yes=True, back=False):
        info("Skipped — no files moved for this season.")
        return 0, len(files)

    ok = skip = 0
    for f in files:
        target = dest / f.name
        if target.exists():
            warn(f"SKIP — already exists: {target.name}")
            skip += 1
            continue
        try:
            shutil.move(str(f), str(target))
            success(f"{f.name}  →  {dest.name}/")
            ok += 1
        except Exception as e:
            err(f"Failed: {f.name}  ({e})")
            skip += 1
    return ok, skip

def util_setup_show(source: Path = None, downloads: Path = None):
    render(title="Set Up Show + Season Folders",
           sub="Creates (or reuses) a show's season folders, then helps you\n"
               "  pull files in from a Downloads folder, one season at a time.")

    remembered = _load_setup_pair()

    if remembered and source is None and downloads is None:
        rem_source, rem_downloads = remembered
        render(title="Set Up Show + Season Folders",
               context_lines=[f"① Source:    {DIM}{rem_source}{R}",
                               f"② Downloads: {DIM}{rem_downloads}{R}"])
        print(f"  Use these folders again?")
        blank()
        if ask_yn("Use the folders shown above", default_yes=True, back=False):
            source, downloads = rem_source, rem_downloads

    # ── Step A: Source folder ──────────────────────────────────────────────────
    if source is None:
        source = pick_folder(
            title="① Choose the Source folder",
            sub="The show's root folder will be created here (or already exists here)."
        )

    # ── Step B: Downloads folder ────────────────────────────────────────────────
    if downloads is None:
        downloads = pick_folder(
            title="② Choose the Downloads folder",
            sub="Where new episode files show up — not necessarily your system Downloads."
        )

    _save_setup_pair(source, downloads)

    # ── Step C: does Source already have season folders? ──────────────────────
    show_root = source
    existing = _existing_season_folders(source)

    if not existing:
        # Source itself might already BE a show folder with no seasons yet,
        # or it might be a general media folder — either way, ask for a
        # show title and create the structure fresh.
        render(title="Set Up Show + Season Folders",
               context_lines=[f"Source: {DIM}{source}{R}"])
        show_title = ask("Show title", hint="A new folder with this name will be created here.", back=False)
        if not show_title:
            err("Show title cannot be empty.")
            return

        raw_n = ask("How many seasons?", default="1", back=False)
        n_seasons = int(raw_n) if raw_n.isdigit() and int(raw_n) > 0 else 1

        show_root = source / show_title
        show_root.mkdir(parents=True, exist_ok=True)

        for s in range(1, n_seasons + 1):
            (show_root / f"Season {s:02d}").mkdir(exist_ok=True)

        success(f"Created: {show_root}")
        for s in range(1, n_seasons + 1):
            info(f"  Season {s:02d}/")

        existing = _existing_season_folders(show_root)
    else:
        info(f"Found {len(existing)} existing season folder(s) in {source} — "
             f"skipping straight to filling them.")

    if not existing:
        warn("No season folders to fill — nothing more to do.")
        return

    # ── Step D/E: per-season, ask to pull from Downloads, pick files, move ────
    for season_num, season_dir in existing:
        blank()
        render(title=f"Season {season_num:02d}",
               context_lines=[f"Folder: {DIM}{season_dir}{R}"])
        try:
            look = ask_yn(f"Look for Season {season_num:02d} files in the Downloads folder?",
                          default_yes=True)
        except Back:
            continue

        if not look:
            info(f"Skipped Season {season_num:02d}.")
            continue

        try:
            chosen_files, chosen_subfolder = _pick_files_for_season(downloads, season_num)
        except Back:
            continue

        if not chosen_files:
            info(f"No files selected for Season {season_num:02d}.")
            continue

        ok, skip = _confirm_and_move(chosen_files, season_dir)
        blank()
        info(f"Season {season_num:02d}: {ok} moved, {skip} skipped.")

        # If everything came from a subfolder and it's now empty, offer
        # to remove it so the Downloads folder doesn't accumulate clutter.
        if chosen_subfolder is not None and ok > 0:
            try:
                remaining = list(chosen_subfolder.iterdir())
            except Exception:
                remaining = None
            if remaining is not None and not remaining:
                blank()
                if ask_yn(f"'{chosen_subfolder.name}' is now empty — delete it?",
                          default_yes=True, back=False):
                    try:
                        chosen_subfolder.rmdir()
                        success(f"Deleted: {chosen_subfolder.name}")
                    except Exception as e:
                        err(f"Could not delete folder: {e}")

    blank()
    success("Done setting up the show's folders.")


UTILITY_ENTRIES = [
    UtilEntry(
        "Set Up Show + Season Folders",
        "Create season folders and pull files in from a Downloads folder.",
        util_setup_show,
    ),
]
