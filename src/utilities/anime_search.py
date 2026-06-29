"""
utilities.anime_search — a standalone browser for MyAnimeList data via
Jikan: search for an anime, page through results, pick one, then
explore whatever data Jikan has for it (episodes, characters, staff,
pictures, etc.) — with pagination wherever Jikan itself paginates, an
in-memory cache so revisiting something already fetched doesn't hit
the network again, and the option to save whatever's currently on
screen to a file.

Navigation: every level is its own loop that catches Back() raised by
the level below it — pressing Enter on a sub-screen returns to the
menu that opened it, not all the way out, UNLESS that sub-screen IS
the search field itself (the top of this tool), in which case Back()
propagates out of util_anime_search() entirely, returning to whatever
menu called this tool.
"""
import json
from datetime import datetime
from pathlib import Path

from ..core.display import (
    BOLD, Back, CYAN, DIM, GREEN, R, YELLOW,
    ask, ask_yn, blank, err, info, render, success, warn,
)
from ..core.metadata import (
    ANIME_ENDPOINTS, MetadataError, fetch_anime_endpoint, search_anime_paginated,
)
from ..core.registry import UtilEntry

# In-memory only — scoped to one run of this tool, cleared each time
# util_anime_search() is called fresh. Keyed by (mal_id, endpoint, page).
_cache: dict[tuple, dict] = {}


def _cached_fetch(mal_id: int, endpoint: str, page: int) -> dict:
    key = (mal_id, endpoint, page)
    if key in _cache:
        return _cache[key]
    data = fetch_anime_endpoint(mal_id, endpoint, page)
    _cache[key] = data
    return data


def _save_data(data: dict, suggested_name: str) -> None:
    blank()
    raw = ask("Save as (path or filename)", default=f"{suggested_name}.json", back=False)
    if not raw:
        info("Cancelled — nothing saved.")
        return
    path = Path(raw).expanduser()
    if path.is_dir():
        path = path / f"{suggested_name}.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        success(f"Saved: {path.resolve()}")
    except Exception as e:
        err(f"Could not save: {e}")


def _summarize_value(value, depth: int = 0, max_depth: int = 2) -> str:
    """Render one JSON value as a few readable lines for the console —
    just enough to browse by, not a full pretty-printer."""
    indent = "  " * depth
    if isinstance(value, dict):
        if depth >= max_depth:
            return f"{indent}{{...}}"
        lines = []
        for k, v in value.items():
            if isinstance(v, (dict, list)) and depth + 1 >= max_depth:
                lines.append(f"{indent}{k}: …")
            else:
                lines.append(f"{indent}{k}: {_summarize_value(v, depth + 1, max_depth)}")
        return "\n".join(lines)
    if isinstance(value, list):
        if not value:
            return "[]"
        shown = value[:3]
        rendered = ", ".join(
            v.get("name", v.get("title", str(v))) if isinstance(v, dict) else str(v)
            for v in shown
        )
        suffix = f"  (+{len(value)-3} more)" if len(value) > 3 else ""
        return f"[{rendered}]{suffix}"
    return str(value)


def _show_endpoint_data(mal_id: int, anime_title: str, endpoint: str) -> None:
    """Level 4: view (paginated) data for one endpoint, with save/back."""
    page = 1
    while True:
        try:
            try:
                raw = _cached_fetch(mal_id, endpoint, page)
            except MetadataError as e:
                err(str(e))
                input("  Press Enter to continue...")
                return

            data = raw.get("data")
            pagination = raw.get("pagination", {})
            has_next = pagination.get("has_next_page", False)
            last_page = pagination.get("last_visible_page", page)

            render(
                title=f"{ANIME_ENDPOINTS.get(endpoint, endpoint)}",
                context_lines=[f"Anime: {DIM}{anime_title}{R}",
                                f"Page {page}" + (f" of {last_page}" if last_page > 1 else "")],
            )

            if data is None:
                print(f"  {DIM}(no data){R}")
            elif isinstance(data, list):
                if not data:
                    print(f"  {DIM}(empty){R}")
                for i, item in enumerate(data[:25], 1):
                    if isinstance(item, dict):
                        label = (item.get("title") or item.get("name") or
                                 item.get("entry", {}).get("name") if isinstance(item.get("entry"), dict) else None)
                        label = label or item.get("mal_id", f"item {i}")
                        print(f"  {CYAN}{i:>3}{R}  {label}")
                    else:
                        print(f"  {CYAN}{i:>3}{R}  {item}")
                if len(data) > 25:
                    print(f"  {DIM}… +{len(data) - 25} more (use save to get all of it){R}")
            else:
                print(_summarize_value(data, max_depth=3))

            blank()
            options = []
            if page > 1:
                options.append("p = previous page")
            if has_next:
                options.append("n = next page")
            options.append("s = save this data to a file")
            options.append("Enter = back")
            print(f"  {DIM}{'  ·  '.join(options)}{R}")
            blank()

            raw_choice = input(f"  Choice: ").strip().lower()

            if raw_choice == "":
                return
            if raw_choice in ("b", "back"):
                return
            if raw_choice == "n" and has_next:
                page += 1
                continue
            if raw_choice == "p" and page > 1:
                page -= 1
                continue
            if raw_choice == "s":
                safe_title = "".join(c for c in anime_title if c.isalnum() or c in " -_").strip()
                _save_data(raw, f"{safe_title} - {endpoint} - p{page}")
                input("  Press Enter to continue...")
                continue
            # anything else: just redraw
        except Back:
            return


def _show_endpoint_menu(mal_id: int, anime_title: str) -> None:
    """Level 3: pick which kind of data to look at for this anime."""
    while True:
        render(title=anime_title, sub="Pick what to look at.")
        keys = list(ANIME_ENDPOINTS.keys())
        for i, key in enumerate(keys, 1):
            print(f"  {CYAN}{i:>2}{R}  {ANIME_ENDPOINTS[key]}")
        blank()
        print(f"  {DIM}Enter a number, or 'b' to go back to search results.{R}")
        blank()

        raw = input(f"  Choice: ").strip().lower()
        if raw in ("b", "back", ""):
            return
        if raw.isdigit() and 1 <= int(raw) <= len(keys):
            endpoint = keys[int(raw) - 1]
            try:
                _show_endpoint_data(mal_id, anime_title, endpoint)
            except Back:
                continue
            continue
        err(f"Enter 1–{len(keys)} or 'b'.")
        input("  Press Enter to continue...")


def _show_search_results(query: str) -> None:
    """Level 2: paginated search results — pick one to explore."""
    page = 1
    while True:
        try:
            result = search_anime_paginated(query, page)
        except MetadataError as e:
            err(str(e))
            input("  Press Enter to continue...")
            return

        results = result["results"]
        if not results and page == 1:
            warn(f"No results for \"{query}\".")
            input("  Press Enter to continue...")
            return

        render(title=f"Results for \"{query}\"",
               context_lines=[f"Page {page}" +
                               (f" of {result['last_visible_page']}" if result['last_visible_page'] > 1 else "")])
        for i, r in enumerate(results, 1):
            year = f" ({r['year']})" if r['year'] else ""
            eps = f", {r['episode_count']} ep" if r['episode_count'] else ""
            print(f"  {CYAN}{i:>2}{R}  {BOLD}{r['title']}{R}{year}{eps}  {DIM}[{r['type'] or '?'}]{R}")

        blank()
        options = []
        if page > 1:
            options.append("p = previous page")
        if result["has_next_page"]:
            options.append("n = next page")
        options.append("Enter a number to pick one")
        options.append("b = back to search")
        print(f"  {DIM}{'  ·  '.join(options)}{R}")
        blank()

        raw = input(f"  Choice: ").strip().lower()
        if raw in ("b", "back", ""):
            return
        if raw == "n" and result["has_next_page"]:
            page += 1
            continue
        if raw == "p" and page > 1:
            page -= 1
            continue
        if raw.isdigit() and 1 <= int(raw) <= len(results):
            chosen = results[int(raw) - 1]
            try:
                _show_endpoint_menu(chosen["mal_id"], chosen["title"])
            except Back:
                continue
            continue
        err(f"Enter 1–{len(results)}, 'n'/'p' for pages, or 'b'.")
        input("  Press Enter to continue...")


def util_anime_search() -> None:
    """
    Level 1: the search field. This is the only level where Back()
    propagating out of this function is correct — everywhere deeper,
    Back() is caught locally so it only steps up one menu level.
    """
    _cache.clear()
    render(title="Anime Metadata Search",
           sub="Search MyAnimeList (via Jikan) for any anime, then browse its\n"
               "  episodes, characters, staff, pictures, and more.")
    while True:
        query = ask("Search for an anime", back=False)
        if not query:
            info("Nothing entered — exiting.")
            return
        _show_search_results(query)
        blank()
        if not ask_yn("Search again?", default_yes=True, back=False):
            return


UTILITY_ENTRIES = [
    UtilEntry(
        "Anime Metadata Search",
        "Search MyAnimeList and browse episodes, characters, staff, pictures, and more.",
        util_anime_search,
    ),
]