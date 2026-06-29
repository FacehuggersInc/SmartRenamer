"""
core.metadata — fetches correct episode titles from Jikan (MyAnimeList),
writes them into Plex via its local API (with field locking so they
survive future refreshes), and writes matching Kodi/Jellyfin-style .nfo
files as a portable backup.

This module is pure logic — no input()/print() calls. The interactive
flow (asking questions, showing previews, confirming) lives in
utilities/plex_metadata.py and is the thing both the standalone utility
and the "fetch metadata" extra step in other tools call into.
"""
import os
import re
import time
import json
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from .filesystem import MEDIA_EXT

EP_NUM_PATTERN = re.compile(r"[Ss](\d{1,2})[Ee](\d{1,3})")

JIKAN_BASE = "https://api.jikan.moe/v4"
JIKAN_RATE_LIMIT_SECONDS = 4.0   # Jikan's own guidance: 4 seconds between requests

PLEX_EPISODE_TYPE = 4
PLEX_SHOW_TYPE = 2


class MetadataError(Exception):
    """Raised for anything that should stop the flow with a clear message
    (network failure, bad token, no matches, etc.) rather than crash."""


# ─── .env loading ──────────────────────────────────────────────────────────────

# Set explicitly by run.py at startup to the directory run.py itself
# lives in — that's where the user's .env is expected to be. Falls back
# to the search order below if nothing set this (e.g. running via
# `python -m rename_media_package.main` directly instead of run.py).
_LAUNCHER_DIR: Path | None = None


def load_env_var(name: str, env_path: Path = None) -> str | None:
    """
    Minimal .env reader — looks for a line like 'NAME=value' in a .env
    file. Checks, in order: an explicit env_path if given, then
    _LAUNCHER_DIR (set by run.py to its own directory — this is where
    the .env is expected to live), then the current working directory,
    as a last-resort fallback for anyone running the package a
    different way. Does not require python-dotenv as a dependency.
    """
    candidates = []
    if env_path:
        candidates.append(Path(env_path))
    if _LAUNCHER_DIR is not None:
        candidates.append(_LAUNCHER_DIR / ".env")
    candidates.append(Path.cwd() / ".env")

    for path in candidates:
        if not path.is_file():
            continue
        try:
            for line in path.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                if key.strip() == name:
                    return value.strip().strip('"').strip("'")
        except Exception:
            continue
    return None


# ─── Jikan (MyAnimeList) ───────────────────────────────────────────────────────

def _jikan_get(path: str, params: dict = None) -> dict:
    url = f"{JIKAN_BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "rename-media-tool/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise MetadataError("Jikan rate-limited the request (429) — try again in a moment.")
        raise MetadataError(f"Jikan request failed: HTTP {e.code}")
    except urllib.error.URLError as e:
        raise MetadataError(f"Could not reach Jikan (no internet, or api.jikan.moe is down): {e}")


def search_anime(query: str, limit: int = 5) -> list[dict]:
    """
    Search MyAnimeList via Jikan. Returns a list of candidates, each
    with: mal_id, title, year, episode_count (may be None if unknown/
    still airing).
    """
    data = _jikan_get("/anime", {"q": query, "limit": limit})
    results = []
    for item in data.get("data", []):
        year = None
        aired = item.get("aired") or {}
        from_date = aired.get("from")
        if from_date:
            year = from_date[:4]
        results.append({
            "mal_id": item["mal_id"],
            "title": item.get("title") or item.get("title_english") or "Unknown",
            "title_english": item.get("title_english"),
            "year": year,
            "episode_count": item.get("episodes"),
        })
    return results


# ─── Anime Metadata Search Engine support ──────────────────────────────────────
# Separate from search_anime() above (which is tuned for the
# Fetch-&-Apply-Metadata flow) — these keep full pagination info and
# more fields, for a tool that lets the user browse Jikan directly.

ANIME_ENDPOINTS: dict[str, str] = {
    "full":            "Full record (synopsis, score, genres, studios, etc.)",
    "characters":      "Characters",
    "staff":           "Staff",
    "episodes":        "Episode list",
    "news":            "News",
    "videos":          "Videos (promos, episode previews)",
    "pictures":        "Pictures",
    "statistics":      "Statistics (watching/completed/dropped counts)",
    "recommendations": "Recommendations (similar anime)",
    "reviews":         "Reviews",
    "relations":       "Relations (sequels, spin-offs, adaptations)",
    "themes":          "Themes (opening/ending songs)",
    "external":        "External links",
}


def search_anime_paginated(query: str, page: int = 1) -> dict:
    """
    Search MyAnimeList via Jikan, keeping full pagination info. Returns
    {"results": [...], "has_next_page": bool, "last_visible_page": int}.
    Each result has: mal_id, title, title_english, year, episode_count,
    type, status, synopsis.
    """
    data = _jikan_get("/anime", {"q": query, "page": page})
    results = []
    for item in data.get("data", []):
        year = None
        aired = item.get("aired") or {}
        from_date = aired.get("from")
        if from_date:
            year = from_date[:4]
        results.append({
            "mal_id": item["mal_id"],
            "title": item.get("title") or item.get("title_english") or "Unknown",
            "title_english": item.get("title_english"),
            "year": year,
            "episode_count": item.get("episodes"),
            "type": item.get("type"),
            "status": item.get("status"),
            "synopsis": item.get("synopsis"),
        })
    pagination = data.get("pagination", {})
    return {
        "results": results,
        "has_next_page": pagination.get("has_next_page", False),
        "last_visible_page": pagination.get("last_visible_page", 1),
    }


def fetch_anime_endpoint(mal_id: int, endpoint: str, page: int = 1) -> dict:
    """
    Fetch one of ANIME_ENDPOINTS for a given MyAnimeList ID. Returns the
    raw Jikan response dict (so callers can inspect "data" and
    "pagination" themselves — different endpoints shape their data
    differently, e.g. "full" has no pagination at all, "characters"
    does). `page` is ignored by endpoints that don't paginate.
    """
    if endpoint == "full":
        path = f"/anime/{mal_id}/full"
        params = None
    else:
        path = f"/anime/{mal_id}/{endpoint}"
        params = {"page": page} if page > 1 else None
    return _jikan_get(path, params)


def fetch_episode_titles(mal_id: int) -> tuple[dict[int, str], set[int]]:
    """
    Fetch every episode's title for a MyAnimeList entry, paginated.
    Returns (titles, known_but_untitled):
      - titles: {absolute_episode_number: title} for episodes that DO
        have a title in Jikan's data.
      - known_but_untitled: absolute numbers Jikan returned an entry
        for, but with a blank/null title — a real, documented gap in
        MyAnimeList's own data for some long-running shows, especially
        through filler-heavy stretches. Distinct from a number Jikan
        never returned at all, which usually means the show simply
        doesn't have that many episodes.
    Jikan paginates episode lists after 100 entries; we follow
    pagination.has_next_page and pace requests at Jikan's requested
    4-second interval.
    """
    titles: dict[int, str] = {}
    known_but_untitled: set[int] = set()
    page = 1
    first = True
    while True:
        if not first:
            time.sleep(JIKAN_RATE_LIMIT_SECONDS)
        first = False
        data = _jikan_get(f"/anime/{mal_id}/episodes", {"page": page})
        episodes = data.get("data", [])
        if not episodes:
            break
        for ep in episodes:
            num = ep.get("mal_id")
            title = ep.get("title")
            if not num:
                continue
            if title:
                titles[num] = title
            else:
                known_but_untitled.add(num)
        pagination = data.get("pagination", {})
        if not pagination.get("has_next_page"):
            break
        page += 1
    return titles, known_but_untitled


# ─── Plex local API ─────────────────────────────────────────────────────────────

class PlexClient:
    """Thin wrapper around the handful of Plex local-API calls this tool
    needs. Talks XML (Plex's default response format) and parses just
    the attributes we care about — no external dependency required."""

    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.token = token

    def _get(self, path: str, params: dict = None) -> "ET.Element":
        import xml.etree.ElementTree as ET
        params = dict(params or {})
        params["X-Plex-Token"] = self.token
        url = f"{self.base_url}{path}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"Accept": "application/xml"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return ET.fromstring(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise MetadataError("Plex rejected the token (401) — check PLEX_TOKEN in .env.")
            raise MetadataError(f"Plex request failed: HTTP {e.code} for {path}")
        except urllib.error.URLError as e:
            raise MetadataError(f"Could not reach Plex at {self.base_url} — is the server running and the IP correct? ({e})")

    def _put(self, path: str, params: dict) -> None:
        params = dict(params)
        params["X-Plex-Token"] = self.token
        url = f"{self.base_url}{path}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, method="PUT")
        try:
            with urllib.request.urlopen(req, timeout=15):
                pass
        except urllib.error.HTTPError as e:
            raise MetadataError(f"Plex update failed: HTTP {e.code} (field name may be invalid)")
        except urllib.error.URLError as e:
            raise MetadataError(f"Could not reach Plex for update: {e}")

    def find_tv_library_section_ids(self) -> list[str]:
        root = self._get("/library/sections")
        return [
            d.get("key") for d in root.findall("Directory")
            if d.get("type") == "show"
        ]

    def find_show_by_path(self, folder: Path) -> tuple[dict | None, str]:
        """
        Search every TV library section for a show whose on-disk
        Location matches `folder`. Tries an exact path match first
        (works when this script and Plex see the same filesystem
        paths), then falls back to matching by folder NAME alone —
        this is what makes it work when Plex is running in a Docker
        container (e.g. on Unraid) and sees a completely different
        path prefix for the same physical folder, such as
        "/data/Anime/Naruto (2002)" vs this script's
        "/mnt/unraidmedia/TV Shows/Anime/Naruto (2002)" — same show,
        different path string, but the same final folder name.

        Returns (match_or_None, how_matched), where how_matched is one
        of "exact path", "folder name", or "" if nothing was found —
        so the caller can tell the user which kind of match was used,
        or warn about ambiguity instead of silently guessing.
        """
        folder_str = str(folder.resolve()).rstrip("/\\")
        folder_name = folder.name

        name_matches: list[dict] = []

        for section_id in self.find_tv_library_section_ids():
            root = self._get(f"/library/sections/{section_id}/all", {"type": PLEX_SHOW_TYPE})
            for show in root.findall("Directory"):
                rating_key = show.get("ratingKey")
                detail = self._get(f"/library/metadata/{rating_key}")
                for loc in detail.iter("Location"):
                    loc_path = loc.get("path", "")
                    if not loc_path:
                        continue
                    if loc_path.rstrip("/\\") == folder_str:
                        return {
                            "ratingKey": rating_key,
                            "title": show.get("title"),
                            "key": show.get("key"),
                            "section_id": section_id,
                            "location": loc_path,
                        }, "exact path"
                    if Path(loc_path.rstrip("/\\")).name == folder_name:
                        name_matches.append({
                            "ratingKey": rating_key,
                            "title": show.get("title"),
                            "key": show.get("key"),
                            "section_id": section_id,
                            "location": loc_path,
                        })

        if len(name_matches) == 1:
            return name_matches[0], "folder name"
        if len(name_matches) > 1:
            # Ambiguous — don't guess. Return all candidates via a
            # special marker the caller knows to handle.
            return {"ambiguous": name_matches}, "ambiguous"

        return None, ""

    def get_seasons(self, show_rating_key: str) -> list[dict]:
        root = self._get(f"/library/metadata/{show_rating_key}/children")
        return [
            {"ratingKey": s.get("ratingKey"), "index": s.get("index")}
            for s in root.findall("Directory")
        ]

    def get_episodes(self, season_rating_key: str) -> list[dict]:
        """Returns episodes with their current title and the on-disk
        file path, so we can match them to absolute episode numbers."""
        root = self._get(f"/library/metadata/{season_rating_key}/children")
        episodes = []
        for video in root.findall("Video"):
            file_path = None
            part = video.find("./Media/Part")
            if part is not None:
                file_path = part.get("file")
            episodes.append({
                "ratingKey": video.get("ratingKey"),
                "title": video.get("title"),
                "index": video.get("index"),
                "parentIndex": video.get("parentIndex"),
                "file": file_path,
                "section_id": None,   # filled in by caller if needed
            })
        return episodes

    def update_episode_title(self, section_id: str, episode_rating_key: str, title: str) -> None:
        self._put(
            f"/library/sections/{section_id}/all",
            {
                "type": PLEX_EPISODE_TYPE,
                "id": episode_rating_key,
                "title.value": title,
                "title.locked": "1",
            },
        )

    def refresh_show(self, show_rating_key: str) -> None:
        """Show-level refresh — episode-level refreshes are known to
        silently not update titles in some Plex versions, so this is
        triggered once at the end instead."""
        self._put(f"/library/metadata/{show_rating_key}/refresh", {})


# ─── Matching files to absolute episode numbers ───────────────────────────────

def extract_se(filename: str) -> tuple[int, int] | None:
    m = EP_NUM_PATTERN.search(filename)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None


def _path_suffix_match_length(a: Path, b: Path) -> int:
    """How many trailing path components two paths share, walking from
    the filename backwards. E.g. '.../TV Shows/Anime/Naruto (2002)/
    Season 01/Naruto - S01E01.mkv' vs '/media/TV Shows/Anime/Naruto
    (2002)/Season 01/Naruto - S01E01.mkv' share 5 — everything from
    'TV Shows' onward — even though their mount-point prefixes differ
    entirely."""
    count = 0
    for pa, pb in zip(reversed(a.parts), reversed(b.parts)):
        if pa == pb:
            count += 1
        else:
            break
    return count


def find_best_path_match(local_file: Path, candidate_paths: list[str],
                          min_suffix: int = 3) -> str | None:
    """
    Used when an EXACT path match against Plex's records fails — common
    when this script and Plex see the same files through different
    mount-point prefixes (e.g. this script sees
    '/mnt/unraidmedia/TV Shows/...' while Plex, running in a Docker
    container, sees '/media/TV Shows/...' for the identical file).

    Scores every candidate by how many TRAILING path components it
    shares with local_file, and returns the candidate with the longest
    match — but ONLY if that match is at least `min_suffix` components
    long (filename alone is never enough; this requires the season
    folder and show folder to agree too) AND is unambiguously the best
    — if two candidates tie for the longest match, returns None rather
    than guessing between them.
    """
    best_path = None
    best_len = 0
    second_best_len = 0
    for candidate in candidate_paths:
        length = _path_suffix_match_length(local_file, Path(candidate))
        if length > best_len:
            second_best_len = best_len
            best_len = length
            best_path = candidate
        elif length > second_best_len:
            second_best_len = length

    if best_len >= min_suffix and best_len > second_best_len:
        return best_path
    return None


def build_absolute_episode_map(season_dirs: list[tuple[int, Path]]) -> dict[tuple[int, int], int]:
    """
    Given a list of (season_number, season_folder_path) sorted by
    season number, figure out the ABSOLUTE episode number for every
    (season, episode) pair found on disk, by counting how many episodes
    came before it across all earlier seasons. Returns
    {(season, episode): absolute_episode_number}.
    """
    result: dict[tuple[int, int], int] = {}
    absolute_offset = 0
    for season_num, folder in sorted(season_dirs, key=lambda pair: pair[0]):
        try:
            # Only count actual VIDEO files here — a "Show - S01E01.nfo"
            # sidecar matches the same SxxExx pattern as its video file
            # and would otherwise be counted as its own separate
            # episode, throwing every absolute number after it off by
            # one for each leftover .nfo from a previous run.
            files = sorted(p for p in folder.iterdir()
                            if p.is_file() and p.suffix.lower() in MEDIA_EXT)
        except Exception:
            continue
        eps_in_season = []
        for f in files:
            se = extract_se(f.name)
            if se:
                eps_in_season.append(se[1])
        eps_in_season.sort()
        for i, ep in enumerate(eps_in_season, 1):
            result[(season_num, ep)] = absolute_offset + i
        absolute_offset += len(eps_in_season)
    return result


# ─── .nfo writing ───────────────────────────────────────────────────────────────

def write_episode_nfo(video_path: Path, title: str, season: int, episode: int,
                       plot: str = "") -> Path:
    """
    Write a Kodi/Jellyfin-style sidecar .nfo file next to a video file,
    e.g. 'Show - S01E05.mkv' -> 'Show - S01E05.nfo'. Returns the path
    written. This always succeeds independently of Plex — useful as a
    portable backup of the correct titles regardless of what Plex does
    with them.
    """
    nfo_path = video_path.with_suffix(".nfo")
    xml = (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n"
        "<episodedetails>\n"
        f"  <title>{xml_escape(title)}</title>\n"
        f"  <season>{season}</season>\n"
        f"  <episode>{episode}</episode>\n"
        f"  <plot>{xml_escape(plot)}</plot>\n"
        "</episodedetails>\n"
    )
    nfo_path.write_text(xml, encoding="utf-8")
    return nfo_path