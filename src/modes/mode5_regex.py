"""
modes.mode5_regex — Rename Mode 5: type-your-own raw regex.
"""

import re

from ..core.display import BOLD, DIM, R, ask, err, render
from ..core.filesystem import clean_title, ep_str, extract_quality
from ..core.rename_engine import get, review_settings


def flow_custom_regex(files, show, season, folder):
    render(title="Mode 5 — Raw Regex")
    print(f"""  {DIM}Write a Python regex with NAMED groups.

  Required: {BOLD}(?P<ep>\\d+){R}{DIM}          Optional: {BOLD}(?P<season>\\d+){R}{DIM}
            {BOLD}(?P<show>.+?){R}{DIM}                    {BOLD}(?P<title>.+?){R}{DIM}
                                       {BOLD}(?P<quality>\\d{{3,4}}p){R}{DIM}

  e.g.  S(?P<season>\\d+)E(?P<ep>\\d+)
  Type 'b' to go back.{R}
""")
    while True:
        pattern = ask("Regex", back=True)
        if not pattern:
            continue
        try:
            rx = re.compile(pattern, re.IGNORECASE)
            if "ep" not in rx.groupindex:
                err("Pattern must contain (?P<ep>…)")
                input("  Press Enter to continue...")
                continue
            break
        except re.error as e:
            err(f"Invalid regex: {e}")
            input("  Press Enter to continue...")

    has_title = "title" in rx.groupindex
    settings = [
        {"key": "show",    "label": "Show name", "value": show, "kind": "str"},
        {"key": "season",  "label": "Season number", "value": season, "kind": "int"},
        {"key": "quality", "label": "Add quality tag if found", "value": False, "kind": "bool"},
    ]
    if has_title:
        settings.insert(2, {"key": "title", "label": "Include matched title",
                             "value": True, "kind": "bool"})
    settings = review_settings(settings, title="Raw Regex",
                                context_lines=[f"Pattern: {DIM}{pattern}{R}"])
    show, season = get(settings, "show"), get(settings, "season")

    def build(f, i):
        m = rx.search(f.stem)
        if not m:
            return None
        try:
            ep = int(m.group("ep"))
        except (IndexError, ValueError):
            return None
        s = season
        if "season" in rx.groupindex:
            try: s = int(m.group("season"))
            except (IndexError, ValueError): pass
        title_part = ""
        if has_title and get(settings, "title"):
            try:
                t = clean_title(m.group("title"))
                if t: title_part = f" - {t}"
            except IndexError: pass
        q  = extract_quality(f.name)
        qs = f" ({q}p)" if get(settings, "quality") and q else ""
        return f"{show} - {ep_str(s, ep)}{title_part}{qs}{f.suffix.lower()}"
    return build
