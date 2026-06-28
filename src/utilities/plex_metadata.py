"""
utilities.plex_metadata — fetches correct episode titles from Jikan
(MyAnimeList) and applies them two ways: written + locked into Plex via
its local API, and as a Kodi/Jellyfin-style .nfo file next to each
episode as a portable backup.

The interactive flow (fetch_and_apply_metadata) is reusable — it's the
thing both this standalone utility and the "fetch metadata" extra step
offered by other rename/season tools call into, so the experience is
identical no matter where it's launched from.
"""
import os
from collections import defaultdict
from pathlib import Path

from ..core.config import _config_dir
from ..core.display import (
    BOLD, Back, CYAN, DIM, GREEN, R, RED, YELLOW,
    ask, ask_yn, blank, err, info, render, success, warn,
)
from ..core.filesystem import MEDIA_EXT, list_media, pick_folder
from ..core.registry import UtilEntry
from ..core.metadata import (
    MetadataError, PlexClient, build_absolute_episode_map,
    extract_se, fetch_episode_titles, find_best_path_match, load_env_var,
    search_anime, write_episode_nfo,
)
from .season_tools import _detect_season_from_foldername, _find_show_name_from_path, _resolve_season_root


def _search_and_confirm_show(default_query: str) -> dict | None:
    """Search Jikan, show candidates, let the user confirm one. Returns
    the chosen candidate dict, or None if cancelled."""
    query = ask("Show title to search for", default=default_query, back=False)
    if not query:
        return None

    try:
        results = search_anime(query)
    except MetadataError as e:
        err(str(e))
        return None

    if not results:
        warn(f"No matches found for '{query}'.")
        return None

    render(title="Confirm the show",
           sub="Pick the entry that matches your files — sequel seasons\n"
               "  (e.g. Shippuden) are usually separate entries here.")
    for i, r in enumerate(results, 1):
        ep_count = r["episode_count"] if r["episode_count"] else "?"
        year = r["year"] or "????"
        print(f"  {CYAN}{i}{R}  {BOLD}{r['title']}{R}  {DIM}({year}, {ep_count} episodes){R}")
    blank()
    print(f"  {CYAN}0{R}  None of these — cancel")
    blank()

    raw = input(f"  Choice: ").strip()
    if raw.isdigit() and 1 <= int(raw) <= len(results):
        return results[int(raw) - 1]
    return None


def _gather_season_dirs(show_folder: Path) -> list[tuple[int, Path]]:
    """
    Find every Season NN subfolder directly under show_folder. If
    show_folder itself has no season subfolders but DOES have media
    files directly inside it, treat it as a single season 1.

    Raises MetadataError if two or more DIFFERENT folders both parse
    to the same season number (e.g. a 'Season 01' folder and an 'S01'
    folder both present) — silently combining them would double-count
    every episode in that season, and silently skipping one would
    throw away real files. Whichever it is, this needs a human to
    look at it, not a guess.
    """
    by_number: dict[int, list[Path]] = {}
    try:
        for sub in show_folder.iterdir():
            if sub.is_dir():
                num = _detect_season_from_foldername(sub.name)
                if num is not None:
                    by_number.setdefault(num, []).append(sub)
    except PermissionError:
        pass

    conflicts = {num: paths for num, paths in by_number.items() if len(paths) > 1}
    if conflicts:
        lines = [f"Multiple folders both look like season {num}:" +
                 "".join(f"\n      {p}" for p in paths)
                 for num, paths in sorted(conflicts.items())]
        raise MetadataError(
            "Found more than one folder claiming the same season number — "
            "stopping before this silently doubles or drops episodes:\n   "
            + "\n   ".join(lines)
            + "\n\n   Rename or remove whichever one shouldn't be there, then try again."
        )

    found = [(num, paths[0]) for num, paths in by_number.items()]

    if found:
        return sorted(found, key=lambda pair: pair[0])

    if list_media(show_folder):
        return [(1, show_folder)]
    return []


def _build_flat_absolute_map(season_dirs: list[tuple[int, Path]]) -> dict[tuple[int, int], int]:
    """
    Like build_absolute_episode_map, but ignores season-folder structure
    entirely when computing absolute numbers — every file across every
    folder is just numbered sequentially in folder order. Use this when
    the SOURCE metadata (Jikan) thinks of the show as one continuous
    season but the local files were split into multiple folders purely
    for organisation, and the per-season boundaries don't actually
    matter for matching against the source's absolute episode list.
    """
    result: dict[tuple[int, int], int] = {}
    absolute = 0
    for season_num, folder in sorted(season_dirs, key=lambda pair: pair[0]):
        try:
            # Media files only — a leftover "...S01E01.nfo" sidecar
            # would otherwise be counted as its own separate episode.
            files = sorted(p for p in folder.iterdir()
                            if p.is_file() and p.suffix.lower() in MEDIA_EXT)
        except Exception:
            continue
        # Re-sort by the EXTRACTED episode number, not the raw filename —
        # alphabetical sort puts "E10" before "E2" for unpadded numbers,
        # which would silently scramble the absolute count otherwise.
        numbered = [(extract_se(f.name), f) for f in files]
        numbered = [(se, f) for se, f in numbered if se]
        numbered.sort(key=lambda pair: pair[0][1])
        for se, f in numbered:
            absolute += 1
            result[(season_num, se[1])] = absolute
    return result


def _consolidate_seasons_interactive(season_dirs: list[tuple[int, Path]]) -> Path | None:
    """
    Actually move every file from every season folder into the FIRST
    season folder, renumbered contiguously as one season — a real,
    permanent restructure (unlike _build_flat_absolute_map, which only
    changes matching for this one run). Shows a preview and requires
    confirmation. Returns the destination folder on success, or None if
    cancelled.

    A video file and its sidecar .nfo (same name, different extension —
    e.g. "Show - S01E01.mkv" and "Show - S01E01.nfo") are treated as
    ONE episode and always renumbered and moved together. The .nfo is
    NEVER counted as a separate episode in its own right — only actual
    video files advance the episode count.
    """
    if len(season_dirs) < 2:
        warn("Only one season folder here — nothing to consolidate.")
        return None

    all_media: list[Path] = []
    for season_num, folder in sorted(season_dirs, key=lambda pair: pair[0]):
        try:
            files = sorted(p for p in folder.iterdir()
                            if p.is_file() and p.suffix.lower() in MEDIA_EXT and extract_se(p.name))
        except Exception:
            continue
        all_media.extend(files)

    if not all_media:
        warn("No recognisable episode files found to consolidate.")
        return None

    dest_folder = season_dirs[0][1]
    width = max(2, len(str(len(all_media))))

    from .season_tools import _renumber_se_in_filename

    # Each plan entry is (src_video, dst_video, src_nfo_or_None, dst_nfo_or_None)
    # — the .nfo (if present) always takes the SAME new name as its video
    # file, just with .nfo instead of the video extension, and is never
    # given its own slot in the numbering.
    plan: list[tuple[Path, Path, Path | None, Path | None]] = []
    for i, video in enumerate(all_media, 1):
        new_stem_name = _renumber_se_in_filename(video.stem, 1, i, width)
        dst_video = dest_folder / (new_stem_name + video.suffix)

        src_nfo = video.with_suffix(".nfo")
        dst_nfo = dst_video.with_suffix(".nfo") if src_nfo.is_file() else None
        if not src_nfo.is_file():
            src_nfo = None

        plan.append((video, dst_video, src_nfo, dst_nfo))

    render(title="Consolidate into one season — Preview",
           context_lines=[f"Destination: {DIM}{dest_folder}{R}"])
    nfo_count = sum(1 for _, _, src_nfo, _ in plan if src_nfo is not None)
    print(f"  {BOLD}{len(plan)} episode(s) will be moved and renumbered{R}"
          + (f"  {DIM}({nfo_count} with a paired .nfo){R}" if nfo_count else "") + ":")
    blank()
    for video, dst_video, src_nfo, dst_nfo in plan[:10]:
        same = video.parent == dst_video.parent
        label = dst_video.name if same else f"{dst_video.parent.name}/{dst_video.name}"
        print(f"    {DIM}{video.parent.name}/{video.name}{R}  →  {GREEN}{label}{R}")
        if src_nfo is not None:
            print(f"      {DIM}+ {src_nfo.name}{R}  →  {GREEN}{dst_nfo.name}{R}  {DIM}(paired){R}")
    if len(plan) > 10:
        print(f"    {DIM}… +{len(plan) - 10} more{R}")

    blank()
    if not ask_yn(f"Move all {len(plan)} episode(s) into {dest_folder.name}?", default_yes=False, back=False):
        info("Cancelled — no files moved.")
        return None

    ok = skip = 0
    for video, dst_video, src_nfo, dst_nfo in plan:
        if dst_video.exists() and dst_video != video:
            warn(f"SKIP — target exists: {dst_video.name}")
            skip += 1
            continue
        try:
            video.rename(dst_video)
            ok += 1
        except Exception as e:
            err(f"Failed: {video.name}  ({e})")
            skip += 1
            continue

        # Move the paired .nfo right alongside its video, same new name.
        if src_nfo is not None:
            try:
                if dst_nfo.exists() and dst_nfo != src_nfo:
                    warn(f"SKIP — .nfo target exists: {dst_nfo.name}")
                else:
                    src_nfo.rename(dst_nfo)
            except Exception as e:
                err(f"Failed to move paired .nfo for {video.name}: {e}")

    blank()
    success(f"{ok}/{len(plan)} episode(s) consolidated into {dest_folder}.  {skip} skipped.")

    # Clean up now-empty season folders left behind
    for season_num, folder in season_dirs[1:]:
        try:
            if not list(folder.iterdir()):
                folder.rmdir()
        except Exception:
            pass

    return dest_folder


def _retry_with_absolute_map(show_folder: Path, season_dirs: list[tuple[int, Path]],
                              candidate: dict, titles_by_absolute: dict,
                              known_but_untitled: set, absolute_map: dict,
                              write_default_nfo: bool = True) -> None:
    """
    Re-run the matching step with a different (season, episode) ->
    absolute mapping than the default season-aware one — used after the
    user picks 'try matching as one season' or after a real
    consolidation. This re-enters the same preview/apply flow as the
    main function, just with the new mapping already computed.
    """
    plan = []
    no_title_in_source = []
    unmapped = []
    for season_num, folder in season_dirs:
        for f in sorted(list_media(folder)):
            se = extract_se(f.name)
            if not se:
                unmapped.append((f, "couldn't read a season/episode number from the filename"))
                continue
            _, ep = se
            absolute = absolute_map.get((season_num, ep))
            if absolute is None:
                unmapped.append((f, "couldn't work out its absolute episode number"))
                continue
            title = titles_by_absolute.get(absolute)
            if title:
                plan.append((f, season_num, ep, absolute, title))
            elif absolute in known_but_untitled:
                no_title_in_source.append((f, absolute))
            else:
                unmapped.append((f, f"absolute episode {absolute} isn't in MyAnimeList's episode list at all"))

    render(title="Preview (retry)",
           context_lines=[f"Show: {DIM}{candidate['title']}{R}",
                           f"Matched: {len(plan)} / {len(plan) + len(no_title_in_source) + len(unmapped)} episode(s)"])
    for f, season_num, ep, absolute, title in plan[:10]:
        print(f"  {DIM}S{season_num:02d}E{ep:02d}{R}  →  {GREEN}{title}{R}")
    if len(plan) > 10:
        print(f"  {DIM}… +{len(plan) - 10} more{R}")
    if no_title_in_source:
        blank()
        warn(f"{len(no_title_in_source)} episode(s) still have no title in MyAnimeList's data.")
    if unmapped:
        blank()
        warn(f"{len(unmapped)} file(s) still couldn't be matched.")
        for f, reason in unmapped[:5]:
            print(f"    {DIM}{f.name}{R}  {DIM}({reason}){R}")

    if not plan:
        warn("Still nothing to apply.")
        return

    blank()
    write_nfo = ask_yn("Write backup .nfo files next to each episode?", default_yes=write_default_nfo, back=False)
    try_plex = ask_yn("Also try writing these into Plex (and locking them)?", default_yes=True, back=False)

    blank()
    if not ask_yn(f"Apply to {len(plan)} episode(s)?", default_yes=True, back=False):
        info("Cancelled — nothing changed.")
        return

    nfo_count = 0
    if write_nfo:
        render(title="Writing .nfo files…")
        for f, season_num, ep, absolute, title in plan:
            try:
                write_episode_nfo(f, title, season_num, ep)
                nfo_count += 1
            except Exception as e:
                err(f"Failed to write .nfo for {f.name}: {e}")
        success(f"{nfo_count}/{len(plan)} .nfo file(s) written.")

    if try_plex:
        blank()
        _apply_to_plex(plan, show_folder)


def fetch_and_apply_metadata(show_folder: Path = None, plex_base_url: str = None) -> None:
    """
    The full interactive metadata flow: find the show, fetch correct
    episode titles, preview, and apply (.nfo always; Plex if reachable).
    Safe to call from the standalone utility OR as an extra step at the
    end of another tool — nothing here assumes how it was reached.
    """
    render(title="Fetch & Apply Episode Metadata",
           sub="Pulls correct episode titles from MyAnimeList (via Jikan) and\n"
               "  writes them into Plex (locked) plus a backup .nfo per episode.")

    if show_folder is None:
        show_folder = pick_folder(
            title="Choose the show's folder",
            sub="The folder containing Season NN subfolders (or the episodes directly)."
        )

    try:
        season_dirs = _gather_season_dirs(show_folder)
    except MetadataError as e:
        err(str(e))
        return
    if not season_dirs:
        warn("No media files or season folders found there.")
        return

    guessed_name = _find_show_name_from_path(show_folder)
    candidate = _search_and_confirm_show(guessed_name)
    if candidate is None:
        info("Cancelled.")
        return

    render(title="Fetching episode titles…",
           context_lines=[f"Show: {DIM}{candidate['title']}{R}"],
           sub="This can take a little while for long-running shows — Jikan\n"
               "  asks for a few seconds between requests to stay polite.")
    try:
        titles_by_absolute, known_but_untitled = fetch_episode_titles(candidate["mal_id"])
    except MetadataError as e:
        err(str(e))
        return

    if not titles_by_absolute and not known_but_untitled:
        warn("No episode data came back for that show at all.")
        return

    se_to_absolute = build_absolute_episode_map(season_dirs)

    # ── Build the plan: every file we can map to a known title ────────────────
    plan = []          # (video_path, season, episode, absolute_num, title)
    no_title_in_source = []   # (video_path, season, absolute) — Jikan has this
                              # episode, but no title for it
    unmapped = []      # (video_path, season_or_none, reason)
    for season_num, folder in season_dirs:
        for f in sorted(list_media(folder)):
            se = extract_se(f.name)
            if not se:
                unmapped.append((f, season_num, "couldn't read a season/episode number from the filename"))
                continue
            _, ep = se
            absolute = se_to_absolute.get((season_num, ep))
            if absolute is None:
                unmapped.append((f, season_num, "couldn't work out its absolute episode number"))
                continue
            title = titles_by_absolute.get(absolute)
            if title:
                plan.append((f, season_num, ep, absolute, title))
            elif absolute in known_but_untitled:
                no_title_in_source.append((f, season_num, absolute))
            else:
                unmapped.append((f, season_num, f"absolute episode {absolute} isn't in MyAnimeList's episode list at all"))

    render(title="Preview",
           context_lines=[f"Show: {DIM}{candidate['title']}{R}",
                           f"Matched: {len(plan)} / "
                           f"{len(plan) + len(no_title_in_source) + len(unmapped)} episode(s)"])

    # ── Per-season breakdown — the whole point is to make it obvious if
    # matches are NOT actually spread across every season folder, even
    # when the flat total above looks fine at a glance. ──────────────────────
    from collections import defaultdict
    season_totals = defaultdict(lambda: {"matched": 0, "no_title": 0, "unmapped": 0})
    for _, season_num, *_ in plan:
        season_totals[season_num]["matched"] += 1
    for _, season_num, _ in no_title_in_source:
        season_totals[season_num]["no_title"] += 1
    for _, season_num, _ in unmapped:
        season_totals[season_num]["unmapped"] += 1

    print(f"  {BOLD}By season:{R}")
    for season_num, folder in season_dirs:
        counts = season_totals.get(season_num, {"matched": 0, "no_title": 0, "unmapped": 0})
        total_in_season = counts["matched"] + counts["no_title"] + counts["unmapped"]
        if total_in_season == 0:
            print(f"    Season {season_num:02d}:  {YELLOW}no files found at all{R}")
        elif counts["matched"] == total_in_season:
            print(f"    Season {season_num:02d}:  {GREEN}{counts['matched']}/{total_in_season} matched{R}")
        elif counts["matched"] == 0:
            print(f"    Season {season_num:02d}:  {RED}0/{total_in_season} matched{R}  "
                  f"{DIM}(this season didn't get ANY titles — check it specifically){R}")
        else:
            print(f"    Season {season_num:02d}:  {YELLOW}{counts['matched']}/{total_in_season} matched{R}")
    blank()

    for f, season_num, ep, absolute, title in plan[:10]:
        print(f"  {DIM}S{season_num:02d}E{ep:02d}{R}  →  {GREEN}{title}{R}")
    if len(plan) > 10:
        print(f"  {DIM}… +{len(plan) - 10} more{R}")

    if no_title_in_source:
        blank()
        warn(f"{len(no_title_in_source)} episode(s) exist in MyAnimeList's list but have "
             f"NO TITLE recorded there — this is a real gap in MAL's own data, not something\n"
             f"      this tool can fix. Common for filler-heavy stretches of long shows.")
        for f, season_num, absolute in no_title_in_source[:5]:
            print(f"    {DIM}{f.name}{R}  {DIM}(absolute ep {absolute}){R}")
        if len(no_title_in_source) > 5:
            print(f"    {DIM}… +{len(no_title_in_source) - 5} more{R}")
        print(f"  {DIM}These will be left completely untouched — no .nfo, no Plex write.{R}")

    if unmapped:
        blank()
        warn(f"{len(unmapped)} file(s) couldn't be matched at all:")
        for f, season_num, reason in unmapped[:5]:
            print(f"    {DIM}{f.name}{R}  {DIM}({reason}){R}")
        if len(unmapped) > 5:
            print(f"    {DIM}… +{len(unmapped) - 5} more{R}")

    # ── Detect a likely SEASON-STRUCTURE mismatch, not just a data gap ─────────
    # The fingerprint: a meaningful chunk of files are "out of range" of the
    # source's absolute episode list specifically (not unparseable filenames,
    # not a MAL title gap) — that pattern shows up when your season folder
    # boundaries don't line up with what the source data assumes, or your
    # per-season file counts don't match its season breakdown.
    out_of_range = [f for f, season_num, reason in unmapped if "isn't in MyAnimeList's episode list" in reason]
    total_files = len(plan) + len(no_title_in_source) + len(unmapped)
    mal_total = candidate.get("episode_count")

    suspect_mismatch = bool(out_of_range) and (
        len(out_of_range) > total_files * 0.1
        or (mal_total and total_files != mal_total)
    )

    if suspect_mismatch:
        blank()
        warn("This looks like a SEASON STRUCTURE mismatch, not just missing data.")
        if mal_total:
            print(f"  {DIM}MyAnimeList lists {mal_total} total episodes; you have {total_files} files.{R}")
        print(f"  {DIM}{len(out_of_range)} file(s) map to an absolute episode number")
        print(f"  the source doesn't recognise at all — usually means your season")
        print(f"  folders don't split at the same points the source data assumes.{R}")
        blank()
        print(f"  {CYAN}1{R} Try matching as if everything were ONE season")
        print(f"     {DIM}(quick, doesn't move any files — just changes how episode{R}")
        print(f"     {DIM}numbers are matched against the title list this one time){R}")
        print(f"  {CYAN}2{R} Actually consolidate these folders into one season")
        print(f"     {DIM}(moves + renumbers files for real, using the same{R}")
        print(f"     {DIM}gap-safe logic as Renumber / Move Season){R}")
        print(f"  {CYAN}3{R} No — keep going with what's already matched above")
        blank()
        retry_choice = input(f"  Choice: ").strip()

        if retry_choice == "1":
            flat_map = _build_flat_absolute_map(season_dirs)
            return _retry_with_absolute_map(
                show_folder, season_dirs, candidate, titles_by_absolute,
                known_but_untitled, flat_map, write_default_nfo=True,
            )
        if retry_choice == "2":
            consolidated_dest = _consolidate_seasons_interactive(season_dirs)
            if consolidated_dest is None:
                info("Consolidation cancelled — continuing with what's already matched.")
            else:
                info("Re-matching against the consolidated folder…")
                new_season_dirs = [(1, consolidated_dest)]
                flat_map = build_absolute_episode_map(new_season_dirs)
                return _retry_with_absolute_map(
                    consolidated_dest, new_season_dirs, candidate, titles_by_absolute,
                    known_but_untitled, flat_map, write_default_nfo=True,
                )
        # choice 3 or anything else: fall through with the original plan

    if not plan:
        warn("Nothing to apply.")
        return

    blank()
    write_nfo = ask_yn("Write backup .nfo files next to each episode?", default_yes=True, back=False)
    try_plex = ask_yn("Also try writing these into Plex (and locking them)?", default_yes=True, back=False)

    blank()
    if not ask_yn(f"Apply to {len(plan)} episode(s)?", default_yes=True, back=False):
        info("Cancelled — nothing changed.")
        return

    # ── .nfo files — always safe, no network/server dependency ────────────────
    nfo_count = 0
    if write_nfo:
        render(title="Writing .nfo files…")
        for f, season_num, ep, absolute, title in plan:
            try:
                write_episode_nfo(f, title, season_num, ep)
                nfo_count += 1
            except Exception as e:
                err(f"Failed to write .nfo for {f.name}: {e}")
        success(f"{nfo_count}/{len(plan)} .nfo file(s) written.")

    # ── Plex ────────────────────────────────────────────────────────────────────
    if try_plex:
        blank()
        _apply_to_plex(plan, show_folder, plex_base_url)


def _apply_to_plex(plan: list[tuple], show_folder: Path, plex_base_url: str = None) -> None:
    token = load_env_var("PLEX_TOKEN")
    if not token:
        err("No PLEX_TOKEN found in a .env file — skipping Plex, .nfo files are still written.")
        return

    # If the user pointed this tool directly at a SEASON folder (common
    # for single-season shows, where there's nothing else to point at),
    # show_folder here would be ".../Hunter x Hunter (2011)/Season 01" —
    # but Plex's own Location for the SHOW is one level up, at
    # ".../Hunter x Hunter (2011)". Walk up past any season-named folder
    # before looking anything up in Plex. This never affects episode
    # numbering (already computed earlier from the original show_folder)
    # — it only affects which folder we ask Plex to match against.
    plex_show_folder = show_folder
    if _detect_season_from_foldername(show_folder.name) is not None:
        plex_show_folder = _resolve_season_root(show_folder)

    base_url = plex_base_url or load_env_var("PLEX_BASE_URL") or "http://127.0.0.1:32400"
    client = PlexClient(base_url, token)

    render(title="Connecting to Plex…", context_lines=[f"Server: {DIM}{base_url}{R}"])
    try:
        show, how_matched = client.find_show_by_path(plex_show_folder)
    except MetadataError as e:
        err(str(e))
        return

    if show is not None and "ambiguous" in show:
        candidates = show["ambiguous"]
        warn(f"Found {len(candidates)} different Plex shows whose folder is named "
             f"'{plex_show_folder.name}' — can't tell which one you mean:")
        for i, c in enumerate(candidates, 1):
            print(f"    {CYAN}{i}{R}  {c['title']}  {DIM}({c['location']}){R}")
        blank()
        pick = input(f"  Pick one (Enter to cancel): ").strip()
        if pick.isdigit() and 1 <= int(pick) <= len(candidates):
            show = candidates[int(pick) - 1]
            how_matched = "folder name (you picked)"
        else:
            info("Cancelled — Plex not touched.")
            return

    if show is None:
        warn(f"Couldn't find a Plex show whose folder matches:\n      {DIM}{plex_show_folder}{R}")
        if plex_show_folder != show_folder:
            print(f"  {DIM}(walked up from the season folder you pointed at: {show_folder}){R}")
        info("Make sure the show has already been scanned into your Plex library at least once.")
        return

    info(f"Found in Plex: {BOLD}{show['title']}{R}  {DIM}(matched by {how_matched}){R}")
    if how_matched.startswith("folder name"):
        print(f"  {DIM}Note: matched by folder NAME, not the exact path — this is normal{R}")
        print(f"  {DIM}if Plex runs in a container (e.g. Unraid Docker) and sees a different{R}")
        print(f"  {DIM}path prefix than this script does.{R}")

    if not show.get("ratingKey"):
        err(f"Plex returned this show with no ratingKey at all — can't look up its "
            f"seasons/episodes.\n      Raw match data: {show}")
        info("This usually means something unexpected came back from Plex — try "
             "again, and if it keeps happening, this is worth reporting as a bug.")
        return

    try:
        seasons = client.get_seasons(show["ratingKey"])
    except MetadataError as e:
        err(str(e))
        return

    if not seasons:
        warn(f"Plex shows '{show['title']}' with NO seasons at all — has it been "
             f"scanned/refreshed since the files were added?")
        return

    # Match every local file to its Plex episode record by EXACT FILE
    # PATH first — this is the most certain way to know we're editing
    # the right Plex episode, since Plex's own season/episode numbering
    # is exactly what might be wrong (that's the reason this tool
    # exists). If the exact path isn't found, fall back to matching by
    # the longest shared PATH SUFFIX — this is what makes it work when
    # this script and Plex see the same files through different
    # mount-point prefixes (e.g. "/mnt/unraidmedia/TV Shows/..." here
    # vs Plex's "/media/TV Shows/..." for the identical file). The
    # suffix match requires several path components to agree (not just
    # the filename) and refuses to guess if two candidates tie.
    plex_episode_by_file: dict[str, dict] = {}
    plex_episode_counts_by_season: dict[str, int] = {}
    for season in seasons:
        season_index = season.get("index", "?")
        try:
            episodes = client.get_episodes(season["ratingKey"])
        except MetadataError as e:
            err(str(e))
            continue
        plex_episode_counts_by_season[season_index] = len(episodes)
        for ep in episodes:
            if ep["file"]:
                plex_episode_by_file[str(Path(ep["file"]).resolve())] = ep

    blank()
    print(f"  {BOLD}Plex's own season/episode counts for this show:{R}")
    for season_index in sorted(plex_episode_counts_by_season,
                                key=lambda s: (s == "?", s)):
        print(f"    Season {season_index}: {plex_episode_counts_by_season[season_index]} episode(s) in Plex")

    # For every file we matched, compare what PLEX currently has against
    # what JIKAN says the title should be. Jikan is the source of truth
    # here — if they disagree, Plex gets corrected to match Jikan, not
    # the other way around.
    all_plex_paths = list(plex_episode_by_file.keys())
    updates = []
    no_plex_record = []
    already_correct = []
    matched_by_suffix = 0
    for f, season_num, ep_num, absolute, jikan_title in plan:
        resolved = f.resolve()
        plex_ep = plex_episode_by_file.get(str(resolved))
        if plex_ep is None:
            fallback_path = find_best_path_match(resolved, all_plex_paths)
            if fallback_path is not None:
                plex_ep = plex_episode_by_file[fallback_path]
                matched_by_suffix += 1
        if plex_ep is None:
            no_plex_record.append((f, season_num))
            continue
        plex_title = plex_ep["title"]
        if plex_title != jikan_title:
            updates.append((plex_ep, jikan_title, plex_title, f, season_num))
        else:
            already_correct.append((f, season_num))

    if matched_by_suffix:
        blank()
        info(f"{matched_by_suffix} episode(s) didn't match by exact path, but were "
             f"matched by their shared folder structure instead (likely a mount-point "
             f"or container path-prefix difference between this script and Plex).")

    # ── Per-season breakdown, so it's never necessary to dig through a
    # flat file list to see whether one season was silently skipped ────────────
    season_summary: dict[int, dict[str, int]] = defaultdict(
        lambda: {"updating": 0, "already_correct": 0, "no_plex_record": 0})
    for _, jikan_title, plex_title, _, season_num in updates:
        season_summary[season_num]["updating"] += 1
    for _, season_num in already_correct:
        season_summary[season_num]["already_correct"] += 1
    for _, season_num in no_plex_record:
        season_summary[season_num]["no_plex_record"] += 1

    blank()
    print(f"  {BOLD}Against your files, by season:{R}")
    for season_num in sorted(season_summary):
        s = season_summary[season_num]
        total = s["updating"] + s["already_correct"] + s["no_plex_record"]
        parts = []
        if s["already_correct"]:
            parts.append(f"{GREEN}{s['already_correct']} already correct{R}")
        if s["updating"]:
            parts.append(f"{YELLOW}{s['updating']} will be updated{R}")
        if s["no_plex_record"]:
            parts.append(f"{RED}{s['no_plex_record']} no Plex record{R}")
        print(f"    Season {season_num:02d}  ({total} file(s)):  " + ", ".join(parts))

    if no_plex_record:
        blank()
        warn(f"{len(no_plex_record)} file(s) have no matching record in Plex at all "
             f"(scan the library first):")
        for f, season_num in no_plex_record[:5]:
            print(f"    {DIM}S{season_num:02d}  {f.name}{R}")
        if len(no_plex_record) > 5:
            print(f"    {DIM}… +{len(no_plex_record) - 5} more{R}")
        blank()
        print(f"  {DIM}If EVERY file shows this, Plex likely sees a different path for{R}")
        print(f"  {DIM}this show's files than this script does. Example comparison:{R}")
        sample_plex_paths = list(plex_episode_by_file.keys())[:1]
        sample_our_path = str(no_plex_record[0][0].resolve())
        print(f"    {DIM}This script looked for:  {sample_our_path}{R}")
        if sample_plex_paths:
            print(f"    {DIM}Plex has on record:      {sample_plex_paths[0]}{R}")
        else:
            print(f"    {DIM}Plex has on record:      (no file paths at all for this show){R}")

    if not updates:
        blank()
        info("Plex already has the correct titles for every matched episode.")
        return

    blank()
    print(f"  {BOLD}{len(updates)} episode(s) disagree between Plex and MyAnimeList — "
          f"Jikan wins, Plex gets corrected:{R}")
    for plex_ep, jikan_title, plex_title, f, season_num in updates[:10]:
        shown_plex = plex_title or "(empty)"
        print(f"    {DIM}S{season_num:02d}  Plex: \"{shown_plex}\"{R}")
        print(f"          {GREEN}MAL:  \"{jikan_title}\"{R}")
    if len(updates) > 10:
        print(f"    {DIM}… +{len(updates) - 10} more{R}")

    blank()
    if not ask_yn("Write these to Plex and lock them?", default_yes=True, back=False):
        info("Cancelled — Plex not touched.")
        return

    ok = 0
    for plex_ep, jikan_title, plex_title, f, season_num in updates:
        try:
            client.update_episode_title(show["section_id"], plex_ep["ratingKey"], jikan_title)
            ok += 1
        except MetadataError as e:
            err(f"Failed on {f.name}: {e}")

    blank()
    success(f"{ok}/{len(updates)} episode(s) updated and locked in Plex.")

    blank()
    if ask_yn("Refresh the show in Plex now so changes show up?", default_yes=True, back=False):
        try:
            client.refresh_show(show["ratingKey"])
            success("Refresh triggered.")
        except MetadataError as e:
            err(str(e))


def util_fetch_metadata(folder):
    """Standalone entry point for the main menu."""
    try:
        fetch_and_apply_metadata(folder)
    except Back:
        pass


UTILITY_ENTRIES = [
    UtilEntry(
        "Fetch & Apply Episode Metadata",
        "Pulls correct titles from MyAnimeList, writes .nfo backups, updates + locks Plex.",
        util_fetch_metadata,
    ),
]