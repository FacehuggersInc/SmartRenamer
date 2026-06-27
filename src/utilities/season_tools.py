"""
utilities.season_tools — Shared season-folder helpers (detecting Season NN folders,
finding the show's root, settling episode-number gaps) plus
the Renumber/Move Season and Split-Into-Seasons-By-Range
utilities built on top of them.
"""

import re
import shutil
from pathlib import Path

from ..core.display import BOLD, Back, CYAN, DIM, GREEN, R, YELLOW, ask, ask_yn, blank, dryline, err, info, render, success, warn
from ..core.filesystem import _clean_show_folder_name, list_media, pick_folder, safe_rename


SEASON_FOLDER_PATTERNS = [
    re.compile(r"^season\s*0*(\d+)$", re.IGNORECASE),
    re.compile(r"^s0*(\d+)$", re.IGNORECASE),
    re.compile(r"^season[_\-]0*(\d+)$", re.IGNORECASE),
]

EP_NUM_PATTERN = re.compile(r"[Ss](\d{1,2})[Ee](\d{1,3})")

def _detect_season_from_foldername(name: str) -> int | None:
    """Recognise 'Season 02', 'S02', 'Season_2', etc. Returns the season
    number, or None if the folder name doesn't look like a season folder."""
    name = name.strip()
    for pat in SEASON_FOLDER_PATTERNS:
        m = pat.match(name)
        if m:
            return int(m.group(1))
    return None

def _extract_ep_num(filename: str) -> int | None:
    m = EP_NUM_PATTERN.search(filename)
    return int(m.group(2)) if m else None

def _extract_season_num(filename: str) -> int | None:
    m = EP_NUM_PATTERN.search(filename)
    return int(m.group(1)) if m else None

def _renumber_se_in_filename(filename: str, new_season: int, new_ep: int) -> str:
    """Replace the first SxxExx occurrence in a filename with new numbers."""
    return EP_NUM_PATTERN.sub(f"S{new_season:02d}E{new_ep:02d}", filename, count=1)

def _resolve_season_root(folder: Path) -> Path:
    """
    Determine the correct root folder under which season folders should
    live, when moving files out of `folder` (itself a detected season
    folder). Normally this is simply folder.parent — the folder one
    level up, e.g. the show's main folder.

    But if that parent's OWN NAME also contains "season" (e.g. files are
    nested as Show/Season 01/Season 02/ for some reason), that parent
    isn't a sensible root either — keep walking up until a folder whose
    name does NOT look like a season folder is found, and use that as
    the root for season folders.
    """
    candidate = folder.parent
    while _detect_season_from_foldername(candidate.name) is not None and candidate.parent != candidate:
        candidate = candidate.parent
    return candidate

def _find_show_name_from_path(folder: Path) -> str:
    """
    Walk up from `folder`, skipping past any season-named folders (using
    the same root-finder used to relocate episodes — see
    _resolve_season_root), and clean the first non-season-named folder's
    name of year/Plex-ID metadata to get the show's name.
    """
    candidate = folder
    while _detect_season_from_foldername(candidate.name) is not None and candidate.parent != candidate:
        candidate = candidate.parent
    return _clean_show_folder_name(candidate.name)

def _confirm_show_name_for_folder(folder: Path, file_count: int, action_desc: str) -> bool:
    """
    Same safety gate as _confirm_show_name, but for folder-level
    operations (the season utilities) that don't have a build_fn to
    preview from — the show name is instead derived from the folder
    tree itself via _find_show_name_from_path. Returns True only on an
    exact typed match; anything else cancels.
    """
    detected_show = _find_show_name_from_path(folder)

    render(title="Final confirmation",
           context_lines=[f"About to {action_desc} {BOLD}{file_count}{R} file(s) in:",
                           f"{DIM}{folder}{R}"])

    if detected_show:
        print(f"  Detected show name from the folder structure:")
        blank()
        print(f"    {BOLD}{CYAN}{detected_show}{R}")
        blank()
        print(f"  {DIM}Type it exactly as shown above to confirm and proceed.{R}")
        print(f"  {DIM}Anything else cancels — no files will be changed.{R}")
    else:
        warn("Could not detect a show name from the folder structure.")
        print(f"  {DIM}Type 'CONFIRM' to proceed anyway, or anything else to cancel.{R}")
        detected_show = "CONFIRM"

    blank()
    typed = input(f"  {BOLD}Confirm{R}: ").strip()

    if typed == detected_show:
        success("Confirmed.")
        return True

    if typed:
        err(f"\"{typed}\" doesn't match \"{detected_show}\" — cancelled, no files changed.")
    else:
        info("Cancelled — no files changed.")
    input("  Press Enter to continue...")
    return False

def _find_season_sibling_dir(current_folder: Path, target_season: int) -> Path | None:
    """
    Look in the resolved season-folder ROOT (see _resolve_season_root)
    for an existing folder that already represents target_season (e.g.
    'Season 03'). Returns that path, or None if no such folder exists
    yet under the root.
    """
    root = _resolve_season_root(current_folder)
    if not root.is_dir():
        return None
    for sibling in root.iterdir():
        if sibling.is_dir():
            num = _detect_season_from_foldername(sibling.name)
            if num == target_season:
                return sibling
    return None

def _settle_gaps(folder: Path, season_num: int, dry_run: bool = True) -> list[tuple[Path, str]]:
    """
    Scan folder for media files belonging to season_num, sorted by their
    current episode number, and build a plan to renumber them so episode
    numbers are contiguous — relative order is always preserved. The
    first episode is always numbered 1, never 0, even if a file in the
    folder currently has E00 — episode numbering for these tools always
    starts at 1.
    """
    files = list_media(folder)
    numbered = [(f, _extract_ep_num(f.name)) for f in files]
    numbered = [(f, ep) for f, ep in numbered if ep is not None]
    if not numbered:
        return []

    numbered.sort(key=lambda pair: pair[1])
    start = 1   # episode numbering always starts at 1, regardless of what
                # the lowest existing number in the folder happens to be

    plan = []
    for i, (f, old_ep) in enumerate(numbered):
        new_ep = start + i
        if new_ep != old_ep:
            new_name = _renumber_se_in_filename(f.name, season_num, new_ep)
            plan.append((f, new_name))
    return plan

def _apply_rename_plan(plan: list[tuple[Path, str]], folder: Path, dry_run: bool) -> tuple[int, int]:
    """Apply a list of (old_path, new_filename) pairs within folder."""
    ok = skip = 0
    for old_path, new_name in plan:
        new_path = folder / new_name
        if safe_rename(old_path, new_path, dry_run):
            ok += 1
        else:
            skip += 1
    return ok, skip

def util_renumber_season(folder: Path = None):
    render(title="Renumber / Move Season",
           sub="Point at a season's folder, tell it the correct season number,\n"
               "  and it'll move + renumber the episodes for you.")

    if folder is None:
        folder = pick_folder(
            title="Choose the folder with episodes to renumber",
            sub="This can be a 'Season NN' folder, or any folder of episodes."
        )

    files = list_media(folder)
    if not files:
        warn("No media files found in that folder.")
        return

    detected_season = _detect_season_from_foldername(folder.name)

    render(title="Renumber / Move Season",
           context_lines=[f"Folder: {DIM}{folder}{R}", f"Files: {len(files)}"])

    if detected_season is not None:
        info(f"This looks like a season {detected_season} folder (from its name).")
    else:
        info("Folder name doesn't look like a season folder — that's fine.")

    blank()
    raw = ask("What season should these files belong to?",
              default=str(detected_season) if detected_season else "1", back=False)
    if not raw.isdigit():
        err("Season must be a number.")
        return
    target_season = int(raw)

    # ── Determine whether files need to MOVE to a different folder ───────────
    dest_folder = folder
    moving = False

    if detected_season is not None and detected_season != target_season:
        moving = True
        existing_sibling = _find_season_sibling_dir(folder, target_season)

        if existing_sibling:
            dest_folder = existing_sibling
            info(f"Found existing folder for season {target_season}: {DIM}{dest_folder}{R}")
        else:
            # Not obvious where to put it automatically by just looking
            # one level up — if the immediate parent ALSO looks like a
            # season folder (nested oddly), keep walking up until we hit
            # a folder that isn't itself season-named, and treat THAT as
            # the root for season folders.
            season_root = _resolve_season_root(folder)
            suggested = season_root / f"Season {target_season:02d}"
            blank()
            print(f"  {BOLD}No existing folder for season {target_season} was found.{R}")
            if season_root != folder.parent:
                print(f"  {DIM}(the immediate parent also looks like a season folder,")
                print(f"   so this goes one level further up, to the show's root){R}")
            print(f"  Suggested location: {DIM}{suggested}{R}")
            blank()
            print(f"  {CYAN}1{R} Create and use this location")
            print(f"  {CYAN}2{R} Choose a different folder")
            choice = input(f"  Choice: ").strip()
            if choice == "2":
                dest_folder = pick_folder(
                    title=f"Choose destination for season {target_season}",
                    sub="Where should these episodes end up?"
                )
            else:
                dest_folder = suggested

    elif detected_season is None:
        # Folder name gives no hint — ask explicitly whether files should
        # move anywhere, rather than assuming they stay put.
        blank()
        if ask_yn("Should these files move to a different folder?",
                  default_yes=False, back=False):
            dest_folder = pick_folder(
                title="Choose destination folder",
                sub="Where should these episodes end up?"
            )
            moving = (dest_folder != folder)

    dest_folder.mkdir(parents=True, exist_ok=True)

    # ── Figure out the append-at-end starting point ───────────────────────────
    existing_dest_files = list_media(dest_folder) if dest_folder != folder else []
    existing_eps = [_extract_ep_num(f.name) for f in existing_dest_files]
    existing_eps = [e for e in existing_eps if e is not None]
    highest_existing = max(existing_eps) if existing_eps else 0

    # Sort incoming files by their current episode number to preserve order
    incoming = sorted(
        ((f, _extract_ep_num(f.name)) for f in files),
        key=lambda pair: (pair[1] is None, pair[1])
    )

    # ── Build the full plan: move+renumber incoming, then settle gaps ─────────
    render(title="Renumber / Move Season — Preview",
           context_lines=[
               f"From: {DIM}{folder}{R}",
               f"To:   {DIM}{dest_folder}{R}" + (f"  {YELLOW}(moving){R}" if moving else ""),
               f"New season number: {target_season}",
           ])

    plan: list[tuple[Path, Path]] = []   # (src, dst) — dst may be in a different folder
    next_ep = highest_existing + 1
    for f, old_ep in incoming:
        new_name = _renumber_se_in_filename(f.name, target_season, next_ep)
        plan.append((f, dest_folder / new_name))
        next_ep += 1

    print(f"  {BOLD}{len(plan)} file(s) will be renumbered" +
          (f" and moved:{R}" if moving else f":{R}"))
    blank()
    for src, dst in plan:
        dryline(f"{DIM}{src.name}{R}")
        same_folder = src.parent == dst.parent
        arrow_label = dst.name if same_folder else f"{dst.parent.name}/{dst.name}"
        print(f"           {GREEN}→ {arrow_label}{R}")

    blank()
    print(f"  {CYAN}1{R} Apply for real   {CYAN}2{R} Cancel")
    action = input(f"  Choice: ").strip()
    if action != "1":
        info("Cancelled — no files changed.")
        return

    if not _confirm_show_name_for_folder(folder, len(plan), "renumber"):
        return

    # ── Apply: move (or rename in place) each file ────────────────────────────
    render(title="Renumbering…")
    ok = skip = 0
    for src, dst in plan:
        if dst.exists() and dst != src:
            warn(f"SKIP — target exists: {dst}")
            skip += 1
            continue
        try:
            src.rename(dst)
            success(f"{DIM}{src.name}{R}\n     → {GREEN}{dst}{R}")
            ok += 1
        except Exception as e:
            err(f"Failed: {src.name}  ({e})")
            skip += 1

    blank()
    info(f"{ok}/{len(plan)} file(s) processed.  {skip} skipped.")

    # ── If we moved files out of a season-named folder, offer to delete
    #    the now-empty original folder ────────────────────────────────────────
    if moving and detected_season is not None:
        try:
            remaining = list(folder.iterdir())
        except Exception:
            remaining = None
        if remaining is not None and not remaining:
            blank()
            if ask_yn(f"The original folder '{folder.name}' is now empty — delete it?",
                      default_yes=True, back=False):
                try:
                    folder.rmdir()
                    success(f"Deleted empty folder: {folder}")
                except Exception as e:
                    err(f"Could not delete folder: {e}")

    # ── Settle gaps in the destination folder ─────────────────────────────────
    blank()
    gap_plan = _settle_gaps(dest_folder, target_season)
    if gap_plan:
        blank()
        print(f"  {BOLD}Checking for gaps in season {target_season}'s episode numbers…{R}")
        print(f"  Found {len(gap_plan)} file(s) that need renumbering to stay contiguous:")
        blank()
        for f, new_name in gap_plan:
            dryline(f"{DIM}{f.name}{R}")
            print(f"           {GREEN}→ {new_name}{R}")
        blank()
        if ask_yn("Settle these gaps now?", default_yes=True, back=False):
            ok2, skip2 = _apply_rename_plan(gap_plan, dest_folder, dry_run=False)
            blank()
            info(f"{ok2}/{len(gap_plan)} file(s) renumbered to close gaps.")
    else:
        info(f"No gaps found in season {target_season} — episode numbers are contiguous.")

def util_define_season_ranges(folder: Path = None):
    render(title="Split Into Seasons By Range",
           sub="Define episode ranges (e.g. 1-12, 13-24) and each range becomes\n"
               "  its own Season folder, renumbered starting from episode 1.")

    if folder is None:
        folder = pick_folder(
            title="Choose the folder of episodes to split",
            sub="All episodes should be here, numbered continuously (flat, no subfolders)."
        )

    files = list_media(folder)
    if not files:
        warn("No media files found in that folder.")
        return

    numbered = [(f, _extract_ep_num(f.name)) for f in files]
    numbered = [(f, ep) for f, ep in numbered if ep is not None]
    if not numbered:
        warn("No files with a recognisable episode number were found.")
        return

    numbered.sort(key=lambda pair: pair[1])
    lowest, highest = numbered[0][1], numbered[-1][1]

    render(title="Split Into Seasons By Range",
           context_lines=[f"Folder: {DIM}{folder}{R}",
                           f"Episodes found: {lowest}–{highest}  ({len(numbered)} file(s))"])

    print(f"  Define each season as a range of episode numbers.")
    print(f"  {DIM}Example: season 1 → 1-12, season 2 → 13-24{R}")
    print(f"  {DIM}Ranges must move forward — each one must start after the last,")
    print(f"  and none can go beyond episode {highest} (the highest one found).{R}")
    blank()

    ranges: list[tuple[int, int, int]] = []   # (season_num, start_ep, end_ep)

    while True:
        next_season = len(ranges) + 1
        highest_used = max((b for _, _, b in ranges), default=lowest - 1)

        if ranges:
            blank()
            print(f"  {BOLD}Ranges so far:{R}")
            for s, a, b in ranges:
                print(f"    Season {s:02d}:  episodes {a}–{b}")
            blank()

        raw = input(
            f"  Season {next_season} range (e.g. \"1-12\" · 'undo' · 'done' · 'b'=back): "
        ).strip().lower()

        if raw in ("b", "back"):
            if ranges:
                # Treat 'back' mid-loop as "undo the last range" rather
                # than abandoning everything already entered — only exit
                # the whole utility if nothing has been defined yet.
                removed = ranges.pop()
                info(f"Removed Season {removed[0]:02d} ({removed[1]}–{removed[2]}).")
                continue
            raise Back()

        if raw == "undo":
            if ranges:
                removed = ranges.pop()
                info(f"Removed Season {removed[0]:02d} ({removed[1]}–{removed[2]}).")
            else:
                warn("Nothing to undo.")
            continue

        if raw == "done":
            break

        m = re.match(r'^(\d+)\s*-\s*(\d+)$', raw)
        if not m:
            err("Format: <start>-<end>, e.g. 1-12  (or 'undo' / 'done' / 'b')")
            continue
        start, end = int(m.group(1)), int(m.group(2))
        if start > end:
            start, end = end, start

        if start <= highest_used:
            if ranges:
                err(f"Must start after episode {highest_used} "
                    f"(the end of season {ranges[-1][0]}'s range).")
            else:
                err(f"Must start at episode {lowest} or later — that's the lowest one found.")
            continue

        if end > highest:
            err(f"Episode {end} doesn't exist — only {highest} episodes were found.")
            continue

        ranges.append((next_season, start, end))

    if not ranges:
        warn("No ranges defined — nothing to do.")
        return

    # ── Check coverage and warn about unassigned episodes ─────────────────────
    all_eps = set(ep for _, ep in numbered)
    covered = set()
    for _, a, b in ranges:
        covered.update(range(a, b + 1))
    uncovered = sorted(all_eps - covered)

    # ── Build the full rename+move plan ────────────────────────────────────────
    plan: list[tuple[Path, Path]] = []
    for season_num, start, end in ranges:
        season_files = [(f, ep) for f, ep in numbered if start <= ep <= end]
        season_files.sort(key=lambda pair: pair[1])
        season_dir = folder / f"Season {season_num:02d}"
        for i, (f, old_ep) in enumerate(season_files, 1):
            new_name = _renumber_se_in_filename(f.name, season_num, i)
            plan.append((f, season_dir / new_name))

    render(title="Split Into Seasons By Range — Preview",
           context_lines=[f"Folder: {DIM}{folder}{R}"])

    for season_num, start, end in ranges:
        season_plan = [(s, d) for s, d in plan if d.parent.name == f"Season {season_num:02d}"]
        print(f"  {BOLD}Season {season_num:02d}{R}  (episodes {start}–{end}, {len(season_plan)} file(s)):")
        for src, dst in season_plan[:3]:
            print(f"    {DIM}{src.name}{R}  →  {GREEN}{dst.parent.name}/{dst.name}{R}")
        if len(season_plan) > 3:
            print(f"    {DIM}… +{len(season_plan)-3} more{R}")
        blank()

    if uncovered:
        warn(f"{len(uncovered)} episode(s) not covered by any range — will be left untouched:")
        print(f"    {DIM}{', '.join(str(e) for e in uncovered[:15])}" +
              (f" …{R}" if len(uncovered) > 15 else f"{R}"))
        blank()

    print(f"  {CYAN}1{R} Apply for real   {CYAN}2{R} Cancel")
    action = input(f"  Choice: ").strip()
    if action != "1":
        info("Cancelled — no files changed.")
        return

    if not _confirm_show_name_for_folder(folder, len(plan), "organise"):
        return

    render(title="Splitting into seasons…")
    ok = skip = 0
    for src, dst in plan:
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists() and dst != src:
                warn(f"SKIP — target exists: {dst}")
                skip += 1
                continue
            src.rename(dst)
            success(f"{DIM}{src.name}{R}\n     → {GREEN}{dst.parent.name}/{dst.name}{R}")
            ok += 1
        except Exception as e:
            err(f"Failed: {src.name}  ({e})")
            skip += 1

    blank()
    info(f"{ok}/{len(plan)} file(s) organised into {len(ranges)} season folder(s).  {skip} skipped.")
