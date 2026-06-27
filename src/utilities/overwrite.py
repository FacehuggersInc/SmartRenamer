"""
utilities.overwrite — Overwrite by Episode Number, and the backup-folder cleanup
utility that goes with it.
"""

import re
import shutil
from pathlib import Path

from ..core.display import BOLD, CYAN, DIM, GREEN, R, ask_yn, blank, c, err, info, render, success, warn
from ..core.filesystem import list_media, pick_folder
from ..core.registry import UtilEntry
from .season_tools import _confirm_show_name_for_folder


SXXEXX_SEARCH = re.compile(r'[Ss](\d{1,2})[\.\-_ ]?[Ee](\d{1,3})(?:[vV]\d+)?')

def _extract_se_loose(filename: str) -> tuple[int, int] | None:
    """Find a SxxExx-style season+episode anywhere in the filename,
    tolerating a dot/dash/space/underscore between S## and E##, and an
    optional trailing version suffix like v2."""
    m = SXXEXX_SEARCH.search(filename)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    return None

def _build_se_map(files: list[Path]) -> dict[tuple[int, int], list[Path]]:
    mapping: dict[tuple[int, int], list[Path]] = {}
    for f in files:
        se = _extract_se_loose(f.name)
        if se:
            mapping.setdefault(se, []).append(f)
    return mapping

def util_overwrite_by_episode(source_folder: Path = None, match_folder: Path = None):
    render(title="Overwrite by Episode Number",
           sub="Replace already-named episodes in a Source folder with the\n"
               "  content of matching episodes from a Match folder, by episode number.")

    if source_folder is None:
        source_folder = pick_folder(
            title="① Choose the SOURCE folder",
            sub="These files already have the names you want to KEEP.\n"
                "  Only their content will be replaced — filenames stay the same."
        )

    if match_folder is None:
        match_folder = pick_folder(
            title="② Choose the MATCH folder",
            sub="These files provide the NEW content to copy in.\n"
                "  They're matched to Source files by season+episode number."
        )

    source_files = list_media(source_folder)
    match_files  = list_media(match_folder)

    if not source_files:
        warn("No media files found in the Source folder.")
        return
    if not match_files:
        warn("No media files found in the Match folder.")
        return

    render(title="Folders selected",
           context_lines=[
               f"① SOURCE  {DIM}(keeps its names){R}",
               f"   {source_folder}",
               f"② MATCH   {DIM}(provides new content){R}",
               f"   {match_folder}",
           ])
    info(f"Source: {len(source_files)} file(s) found.")
    info(f"Match:  {len(match_files)} file(s) found.")
    blank()
    if not ask_yn("Continue with these folders?", default_yes=True, back=False):
        info("Cancelled.")
        return

    source_map = _build_se_map(source_files)
    match_map  = _build_se_map(match_files)

    # Flag anything that couldn't be parsed at all
    unparsed_source = [f for f in source_files if _extract_se_loose(f.name) is None]
    unparsed_match  = [f for f in match_files if _extract_se_loose(f.name) is None]

    # Build the pairing plan, prompting for disambiguation when a season+
    # episode has more than one candidate in the match folder.
    plan: list[tuple[Path, Path]] = []          # (source_file, chosen_match_file)
    skipped_multi: list[tuple] = []
    unmatched_source: list[Path] = []

    for se, src_list in sorted(source_map.items()):
        if len(src_list) > 1:
            # Multiple source files share the same S/E — flag for visibility,
            # but still try to pair each one individually below.
            pass
        candidates = match_map.get(se)
        if not candidates:
            unmatched_source.extend(src_list)
            continue
        for src in src_list:
            if len(candidates) == 1:
                plan.append((src, candidates[0]))
            else:
                skipped_multi.append((se, src, candidates))

    unmatched_match = [f for se, flist in match_map.items()
                        if se not in source_map for f in flist]

    # ── Show a summary before anything else ───────────────────────────────────
    blank()
    info(f"Source folder: {len(source_files)} file(s),  Match folder: {len(match_files)} file(s)")
    blank()

    if unparsed_source:
        warn(f"{len(unparsed_source)} Source file(s) had no recognisable S/E number — skipped:")
        for f in unparsed_source[:5]:
            print(f"    {DIM}{f.name}{R}")
        if len(unparsed_source) > 5:
            print(f"    {DIM}… +{len(unparsed_source)-5} more{R}")
        blank()

    if unparsed_match:
        warn(f"{len(unparsed_match)} Match file(s) had no recognisable S/E number — skipped:")
        for f in unparsed_match[:5]:
            print(f"    {DIM}{f.name}{R}")
        if len(unparsed_match) > 5:
            print(f"    {DIM}… +{len(unparsed_match)-5} more{R}")
        blank()

    # ── Resolve multi-candidate matches interactively ─────────────────────────
    for se, src, candidates in skipped_multi:
        render(title="Multiple matches found",
               context_lines=[f"Source: {DIM}{src.name}{R}",
                               f"Episode: S{se[0]:02d}E{se[1]:02d}"])
        print(f"  {BOLD}Pick which Match file should overwrite this Source file:{R}\n")
        for i, c in enumerate(candidates, 1):
            print(f"    {CYAN}{i}{R}  {c.name}")
        print(f"    {CYAN}0{R}  Skip this episode")
        blank()
        while True:
            raw = input(f"  Choice: ").strip()
            if raw == "0":
                break
            if raw.isdigit() and 1 <= int(raw) <= len(candidates):
                plan.append((src, candidates[int(raw) - 1]))
                break
            err(f"Enter 0–{len(candidates)}.")

    if not plan:
        warn("Nothing to overwrite — no matching episodes found.")
        return

    # ── Dry-run preview ────────────────────────────────────────────────────────
    while True:
        render(title="Overwrite by Episode Number — Preview",
               context_lines=[f"① Source: {DIM}{source_folder}{R}",
                               f"② Match:  {DIM}{match_folder}{R}"])
        print(f"  {BOLD}{len(plan)} file(s) will be overwritten:{R}\n")
        for src, match in plan:
            se = _extract_se_loose(src.name)
            print(f"  {DIM}S{se[0]:02d}E{se[1]:02d}{R}  {BOLD}{src.name}{R}")
            print(f"        {DIM}content replaced from:{R} {match.name}")
        if unmatched_source:
            blank()
            warn(f"{len(unmatched_source)} Source episode(s) have no match — left untouched:")
            for f in unmatched_source[:5]:
                print(f"    {DIM}{f.name}{R}")
            if len(unmatched_source) > 5:
                print(f"    {DIM}… +{len(unmatched_source)-5} more{R}")
        if unmatched_match:
            blank()
            info(f"{len(unmatched_match)} Match file(s) have no Source episode — ignored.")

        blank()
        print(f"  {BOLD}Originals are NEVER deleted{R} — they're moved into")
        print(f"  {DIM}{source_folder / '.backup_before_overwrite'}{R}")
        blank()
        print(f"  {CYAN}1{R} Apply for real   {CYAN}2{R} Cancel")
        action = input(f"  Choice: ").strip()

        if action == "2" or action == "":
            info("Cancelled — no files changed.")
            return
        if action != "1":
            err("Enter 1 or 2.")
            continue
        break

    if not _confirm_show_name_for_folder(source_folder, len(plan), "overwrite"):
        return

    # ── Perform the overwrite ──────────────────────────────────────────────────
    backup_dir = source_folder / ".backup_before_overwrite"
    ok = skip = 0
    render(title="Overwriting…")
    for src, match in plan:
        try:
            backup_dir.mkdir(exist_ok=True)
            backup_target = backup_dir / src.name
            if backup_target.exists():
                # avoid clobbering an earlier backup of the same name
                stem, suf = src.stem, src.suffix
                n = 1
                while (backup_dir / f"{stem} ({n}){suf}").exists():
                    n += 1
                backup_target = backup_dir / f"{stem} ({n}){suf}"
            shutil.move(str(src), str(backup_target))
            shutil.move(str(match), str(src))
            success(f"{DIM}{src.name}{R}  {GREEN}← {match.name}{R}")
            ok += 1
        except Exception as e:
            err(f"Failed on {src.name}: {e}")
            skip += 1

    blank()
    info(f"{ok}/{len(plan)} file(s) overwritten.  {skip} failed.")
    info(f"Originals backed up to: {DIM}{backup_dir}{R}")

    if ok > 0 and backup_dir.is_dir():
        blank()
        print(f"  {DIM}You can delete the backup now if you've checked the new files,{R}")
        print(f"  {DIM}or come back to it later from the main menu (option 12).{R}")
        blank()
        if ask_yn("Delete the backup folder now?", default_yes=False, back=False):
            try:
                shutil.rmtree(backup_dir)
                success(f"Deleted: {backup_dir}")
            except Exception as e:
                err(f"Could not delete backup folder: {e}")

def util_cleanup_backups(folder: Path = None):
    """
    Find and optionally delete .backup_before_overwrite folders left behind
    by the Overwrite by Episode Number tool. Scans the given folder and one
    level of its subfolders, since backups live inside whichever Source
    folder they were created in.
    """
    render(title="Clean Up Backup Folders",
           sub="Finds .backup_before_overwrite folders left by the\n"
               "  'Overwrite by Episode Number' tool and lets you delete them.")

    if folder is None:
        folder = pick_folder(
            title="Choose where to search",
            sub="We'll look here and one level into subfolders for backups."
        )

    found: list[Path] = []
    candidate = folder / ".backup_before_overwrite"
    if candidate.is_dir():
        found.append(candidate)
    try:
        for sub in folder.iterdir():
            if sub.is_dir() and not sub.name.startswith("."):
                cand2 = sub / ".backup_before_overwrite"
                if cand2.is_dir():
                    found.append(cand2)
    except PermissionError:
        pass

    if not found:
        blank()
        info(f"No backup folders found under: {DIM}{folder}{R}")
        return

    render(title="Backup folders found", context_lines=[f"Searched: {DIM}{folder}{R}"])
    for i, bdir in enumerate(found, 1):
        try:
            file_count = sum(1 for _ in bdir.iterdir())
        except PermissionError:
            file_count = "?"
        print(f"  {CYAN}{i}{R}  {bdir}  {DIM}({file_count} file(s)){R}")

    blank()
    print(f"  {CYAN}a{R}  Delete ALL of the above")
    print(f"  {CYAN}1-{len(found)}{R}  Delete one by number")
    print(f"  {CYAN}n{R}  Cancel, delete nothing")
    blank()

    raw = input(f"  Choice: ").strip().lower()

    to_delete: list[Path] = []
    if raw == "a":
        to_delete = found
    elif raw.isdigit() and 1 <= int(raw) <= len(found):
        to_delete = [found[int(raw) - 1]]
    else:
        info("Cancelled — nothing deleted.")
        return

    blank()
    print(f"  {BOLD}About to permanently delete:{R}")
    for bdir in to_delete:
        print(f"    {DIM}{bdir}{R}")
    blank()
    if not ask_yn("Are you sure?", default_yes=False, back=False):
        info("Cancelled — nothing deleted.")
        return

    ok = 0
    for bdir in to_delete:
        try:
            shutil.rmtree(bdir)
            success(f"Deleted: {bdir}")
            ok += 1
        except Exception as e:
            err(f"Failed to delete {bdir}: {e}")

    blank()
    info(f"{ok}/{len(to_delete)} backup folder(s) deleted.")


UTILITY_ENTRIES = [
    UtilEntry(
        "Overwrite by Episode Number",
        "Replaces a Source file's content with a Match file's, by S/E number.",
        util_overwrite_by_episode,
    ),
    UtilEntry(
        "Clean up backup folders",
        "Finds and deletes .backup_before_overwrite folders left by Overwrite by Episode Number.",
        util_cleanup_backups,
    ),
]
