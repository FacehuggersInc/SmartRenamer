"""
utilities.basic — Preview, Split-into-Season-folders, and Rename-show-name-
across-files utilities.
"""

import re
import shutil
from pathlib import Path

from ..core.display import BOLD, CYAN, DIM, GREEN, R, ask, ask_yn, blank, dryline, err, info, render, success, warn
from ..core.filesystem import list_media, sanitize_filename
from ..core.registry import UtilEntry


def util_preview(folder: Path):
    render(title="Preview files")
    files = list_media(folder)
    if not files:
        warn("No media files found.")
        return
    for i, f in enumerate(files, 1):
        print(f"  {DIM}{i:3d}.{R}  {f.name}")
    blank()
    info(f"{len(files)} media file(s) found.")

def util_split(folder: Path):
    render(title="Split into Season subfolders")
    files = list_media(folder)
    if not files:
        warn("No media files found.")
        return

    preview: dict[Path, list[Path]] = {}
    for f in files:
        m  = re.search(r'[Ss](\d{1,2})[Ee]\d+', f.name)
        sn = int(m.group(1)) if m else 0
        preview.setdefault(folder / f"Season {sn:02d}", []).append(f)

    print(f"  {BOLD}Will move into:{R}")
    for sub, flist in sorted(preview.items()):
        print(f"    {CYAN}{sub.name}/{R}  — {len(flist)} file(s)")
    blank()
    if not ask_yn("Proceed?", back=False):
        return
    for sub, flist in preview.items():
        sub.mkdir(exist_ok=True)
        for f in flist:
            try:
                shutil.move(str(f), sub / f.name)
                success(f"{f.name}  →  {sub.name}/")
            except Exception as e:
                err(f"Failed: {e}")

def util_rename_show(folder: Path):
    render(title="Rename show name across files")
    files = list_media(folder)
    if not files:
        warn("No media files found.")
        return

    first     = files[0].stem
    detected  = ""
    sep_match = re.search(r'\s+-\s+S\d{2}E\d+', first)
    if sep_match:
        detected = first[:sep_match.start()].strip()

    if detected:
        print(f"  {DIM}Detected: \"{detected}\"{R}")
    old_name = ask("Current show name to replace", default=detected, back=False)
    if not old_name:
        err("No name entered.")
        return

    new_name = ask("New show name", back=False)
    if not new_name:
        err("No name entered.")
        return

    pairs: list[tuple[Path, Path]] = []
    for f in files:
        if f.stem.startswith(old_name):
            rest = f.stem[len(old_name):]
            pairs.append((f, folder / sanitize_filename(new_name + rest + f.suffix.lower())))

    if not pairs:
        warn(f"No files start with \"{old_name}\".")
        return

    blank()
    print(f"  {BOLD}Preview ({len(pairs)} file(s)):{R}")
    for src, dst in pairs:
        dryline(f"{DIM}{src.name}{R}")
        print(f"           {GREEN}→ {dst.name}{R}")

    blank()
    if not ask_yn("Apply these renames?", back=False):
        info("Cancelled.")
        return

    ok = 0
    for src, dst in pairs:
        if dst.exists() and dst != src:
            warn(f"SKIP — exists: {dst.name}")
            continue
        try:
            src.rename(dst)
            success(f"{DIM}{src.name}{R}\n     → {GREEN}{dst.name}{R}")
            ok += 1
        except Exception as e:
            err(f"Failed: {e}")
    blank()
    info(f"{ok}/{len(pairs)} files renamed.")


def util_find_replace(folder: Path):
    render(title="Find & Replace in Filenames",
           sub="Replace one exact piece of text with another, across every\n"
               "  filename in the folder — e.g. swap 'ep' for 'e' to turn\n"
               "  'Show - s01ep01.mkv' into 'Show - s01e01.mkv'.")
    files = list_media(folder)
    if not files:
        warn("No media files found.")
        return

    target = ask("Text to find", back=False)
    if not target:
        err("Nothing entered — cancelled.")
        return

    replacement = ask("Replace it with", default="", back=False)
    blank()
    case_sensitive = ask_yn("Match case exactly?", default_yes=False, back=False)

    flags = 0 if case_sensitive else re.IGNORECASE
    pattern = re.compile(re.escape(target), flags)

    # Only ever touch the STEM — the extension is never part of the
    # search/replace, so a target string that happens to also appear in
    # a file extension (e.g. searching for "mkv") can't corrupt it.
    pairs: list[tuple[Path, Path]] = []
    for f in files:
        new_stem, count = pattern.subn(replacement, f.stem)
        if count > 0:
            new_name = sanitize_filename(new_stem + f.suffix.lower())
            pairs.append((f, folder / new_name))

    if not pairs:
        info(f"No files contained \"{target}\" — nothing to change.")
        return

    blank()
    print(f"  {BOLD}{len(pairs)} file(s) will be renamed:{R}")
    for src, dst in pairs:
        dryline(f"{DIM}{src.name}{R}")
        print(f"           {GREEN}→ {dst.name}{R}")

    blank()
    if not ask_yn("Apply these renames?", back=False):
        info("Cancelled.")
        return

    ok = 0
    for src, dst in pairs:
        if dst.exists() and dst != src:
            warn(f"SKIP — exists: {dst.name}")
            continue
        try:
            src.rename(dst)
            success(f"{DIM}{src.name}{R}\n     → {GREEN}{dst.name}{R}")
            ok += 1
        except Exception as e:
            err(f"Failed: {e}")
    blank()
    info(f"{ok}/{len(pairs)} files renamed.")


UTILITY_ENTRIES = [
    UtilEntry(
        "Preview files in a folder",
        "Just lists the media files found — no renaming.",
        util_preview,
    ),
    UtilEntry(
        "Split into Season XX/ subfolders",
        "Scans filenames for S01/S02 tags and sorts files into folders.",
        util_split,
    ),
    UtilEntry(
        "Rename show name across files",
        "Swaps the show-name prefix on files already named ...-S01E01.",
        util_rename_show,
    ),
    UtilEntry(
        "Find & Replace in Filenames",
        "Replace one exact piece of text with another, across every filename in a folder.",
        util_find_replace,
    ),
]