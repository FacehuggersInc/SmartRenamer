"""
utilities.basic — Preview, Split-into-Season-folders, and Rename-show-name-
across-files utilities.
"""

import re
from pathlib import Path

from ..core.display import BOLD, CYAN, DIM, GREEN, R, ask, ask_yn, blank, dryline, err, info, render, success, warn
from ..core.filesystem import list_media, sanitize_filename


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
