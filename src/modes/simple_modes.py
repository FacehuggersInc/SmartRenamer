"""
modes.simple_modes — Rename Modes 1-4: Standard Fansub, One Pace, Simple Numbered,
and Normalize S##E##.
"""

import re

from ..core.filesystem import ep_str, extract_quality
from ..core.parsers import parse_fansub, parse_one_pace, parse_simple, parse_sxxexx
from ..core.rename_engine import get, review_settings


def flow_fansub(files, show, season, folder):
    ctx = ["Mode 1 — Standard Fansub", f"e.g. [DB]Show_-_01_(info).mkv"]
    settings = [
        {"key": "show",    "label": "Show name", "value": show, "kind": "str",
         "hint": "Goes at the start of every renamed file."},
        {"key": "season",  "label": "Season number", "value": season, "kind": "int"},
        {"key": "use_seq", "label": "Sequential episode numbering", "value": False, "kind": "bool",
         "hint": "No = use number found in filename.  Yes = count files 1,2,3…"},
        {"key": "quality", "label": "Add quality tag (1080p)", "value": False, "kind": "bool"},
    ]
    settings = review_settings(settings, title="Standard Fansub", context_lines=ctx)
    show, season = get(settings, "show"), get(settings, "season")
    def build(f, i):
        d  = parse_fansub(f.name)
        ep = i if get(settings, "use_seq") else (d["ep"] or i)
        q  = f" ({d['quality']}p)" if get(settings, "quality") and d["quality"] else ""
        return f"{show} - {ep_str(season, ep)}{q}{f.suffix.lower()}"
    return build

def flow_one_pace(files, show, season, folder):
    ctx = ["Mode 2 — One Pace / Group+Range", "e.g. [Group][841-842] Arc 10 [720p].mp4"]
    settings = [
        {"key": "show",    "label": "Show name", "value": show, "kind": "str"},
        {"key": "season",  "label": "Season number", "value": season, "kind": "int"},
        {"key": "arc",     "label": "Include arc name", "value": True, "kind": "bool"},
        {"key": "part",    "label": "Include part number after arc", "value": False, "kind": "bool"},
        {"key": "quality", "label": "Add quality tag", "value": False, "kind": "bool"},
        {"key": "trans",   "label": "Add translation tag", "value": False, "kind": "bool"},
        {"key": "use_seq", "label": "Sequential episode numbering", "value": False, "kind": "bool"},
    ]
    settings = review_settings(settings, title="One Pace / Group+Range", context_lines=ctx)
    show, season = get(settings, "show"), get(settings, "season")
    def build(f, i):
        d    = parse_one_pace(f.name)
        ep   = i if get(settings, "use_seq") else (d["ep"] or i)
        base = f"{show} - {ep_str(season, ep)}"
        if get(settings, "arc") and d["arc"]:
            base += f" - {d['arc']}"
        if get(settings, "part") and d["ep"]:
            base += f" Part {d['ep']}"
        extras = []
        if get(settings, "trans") and d["translation"]:
            extras.append(d["translation"])
        if get(settings, "quality") and d["quality"]:
            extras.append(f"{d['quality']}p")
        if extras:
            base += " (" + ") (".join(extras) + ")"
        return base + f.suffix.lower()
    return build

def flow_simple(files, show, season, folder):
    ctx = ["Mode 3 — Simple Numbered Files", "e.g. 01.mkv  /  Episode 05.mkv"]
    settings = [
        {"key": "show",    "label": "Show name", "value": show, "kind": "str"},
        {"key": "season",  "label": "Season number", "value": season, "kind": "int"},
        {"key": "use_seq", "label": "Sequential episode numbering", "value": False, "kind": "bool"},
        {"key": "offset",  "label": "Episode number offset", "value": 0, "kind": "int",
         "hint": "Added to every episode number. 0=none, 12=start at E13."},
    ]
    settings = review_settings(settings, title="Simple Numbered Files", context_lines=ctx)
    show, season = get(settings, "show"), get(settings, "season")
    def build(f, i):
        d  = parse_simple(f.name)
        ep = (i if get(settings, "use_seq") else (d["ep"] or i)) + get(settings, "offset")
        return f"{show} - {ep_str(season, ep)}{f.suffix.lower()}"
    return build

def flow_sxxexx(files, show, season, folder):
    ctx = ["Mode 4 — Normalize existing S##E## files", "e.g. old.show.S01E04.1080p.mkv"]
    settings = [
        {"key": "show",    "label": "Show name", "value": show, "kind": "str"},
        {"key": "season",  "label": "Season (if not read from file)", "value": season, "kind": "int"},
        {"key": "keep_ep", "label": "Keep season+episode from filename", "value": True, "kind": "bool"},
        {"key": "quality", "label": "Add quality tag if found", "value": False, "kind": "bool"},
    ]
    settings = review_settings(settings, title="Normalize S##E## Files", context_lines=ctx)
    show, season = get(settings, "show"), get(settings, "season")
    def build(f, i):
        d = parse_sxxexx(f.name)
        if d["ep"] is None:
            return None
        s  = d["season"] if (get(settings, "keep_ep") and d["season"]) else season
        q  = extract_quality(f.name)
        qs = f" ({q}p)" if get(settings, "quality") and q else ""
        return f"{show} - {ep_str(s, d['ep'])}{qs}{f.suffix.lower()}"
    return build
