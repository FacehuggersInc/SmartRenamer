#!/usr/bin/env python3
"""
rename_media.py — Batch media file renamer for anime/TV shows
"""

import os
import re
import sys
import json
import shutil
from pathlib import Path

# ─── ANSI Colors ──────────────────────────────────────────────────────────────
R      = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RED    = "\033[91m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BLUE   = "\033[94m"
MAGENTA= "\033[95m"
BG_YEL = "\033[43m"
BLK    = "\033[30m"

def c(text, color): return f"{color}{text}{R}"
def success(text):  print(f"  {GREEN}✓{R}  {text}")
def warn(text):     print(f"  {YELLOW}⚠{R}  {text}")
def err(text):      print(f"  {RED}✗{R}  {text}")
def info(text):     print(f"  {BLUE}·{R}  {text}")
def blank():        print()
def dryline(text):  print(f"  {BG_YEL}{BLK} DRY RUN {R}  {text}")
def sep_line():     print(f"  {DIM}{'─'*58}{R}")
def thick_line():   print(f"  {BOLD}{'─'*58}{R}")

MEDIA_EXT = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".ts", ".wmv"}

# ─── Screen clearing ──────────────────────────────────────────────────────────

def clear_screen():
    """Clear the terminal. Falls back gracefully if not a real TTY."""
    os.system("cls" if os.name == "nt" else "clear")


def render(*, title: str = "", context_lines: list[str] = None,
           sub: str = "") -> None:
    """
    Standard frame for every step: clears the screen, then prints a compact
    context block (title + any 'what's being built' lines) before the step's
    own content continues below. Every step should call this first.
    """
    clear_screen()
    print(f"{BOLD}{CYAN}╭{'─'*58}╮{R}")
    if title:
        print(f"{BOLD}{CYAN}│{R}  {BOLD}{title}{R}")
        if context_lines:
            print(f"{BOLD}{CYAN}├{'─'*58}┤{R}")
    if context_lines:
        for line in context_lines:
            print(f"{BOLD}{CYAN}│{R}  {line}")
    print(f"{BOLD}{CYAN}╰{'─'*58}╯{R}")
    if sub:
        print(f"  {DIM}{sub}{R}")
    blank()


# ─── Back navigation ──────────────────────────────────────────────────────────

class Back(Exception):
    pass

def _check_back(raw: str):
    if raw.strip().lower() in ("b", "back"):
        raise Back()

# ─── Input helpers ────────────────────────────────────────────────────────────

def ask(label: str, hint: str = "", default: str = "", back: bool = True) -> str:
    if hint:
        print(f"  {DIM}{hint}{R}")
    suffix = f"  {DIM}('b'=back){R}" if back else ""
    if default:
        val = input(f"  {BOLD}{label}{R} [{DIM}{default}{R}]{suffix}: ").strip()
        if back: _check_back(val)
        return val if val else default
    val = input(f"  {BOLD}{label}{R}{suffix}: ").strip()
    if back: _check_back(val)
    return val

def ask_yn(label: str, hint: str = "", default_yes: bool = False, back: bool = True) -> bool:
    opts = f"{BOLD}Y{R}/n" if default_yes else f"y/{BOLD}N{R}"
    bk   = f"  {DIM}('b'=back){R}" if back else ""
    if hint:
        print(f"  {DIM}{hint}{R}")
    raw = input(f"  {label} [{opts}]{bk}: ").strip().lower()
    if back: _check_back(raw)
    return (raw == "y") if raw else default_yes

def section(title: str):
    print(f"\n  {CYAN}{BOLD}── {title}{R}")

# ─── Location discovery (local dirs + GVFS/SMB mounts) ───────────────────────

def _find_all_locations() -> list[tuple[str, Path]]:
    locations: list[tuple[str, Path]] = []
    seen: set[Path] = set()

    def add(label: str, path: Path):
        if path.is_dir() and path not in seen:
            seen.add(path)
            locations.append((label, path))

    home = Path.home()
    add("Home", home)

    WANT_XDG = {
        "XDG_VIDEOS_DIR": "Videos", "XDG_DOWNLOAD_DIR": "Downloads",
        "XDG_DOCUMENTS_DIR": "Documents", "XDG_MUSIC_DIR": "Music",
        "XDG_PICTURES_DIR": "Pictures",
    }
    xdg_file = home / ".config" / "user-dirs.dirs"
    if xdg_file.exists():
        for line in xdg_file.read_text().splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            if k not in WANT_XDG:
                continue
            v = os.path.expandvars(v.strip('"').replace("$HOME", str(home)))
            add(WANT_XDG[k], Path(v))
    else:
        for name in ("Videos", "Downloads", "Documents", "Music", "Pictures"):
            add(name, home / name)

    uid_name = os.environ.get("USER", os.environ.get("LOGNAME", ""))
    for root in [Path("/media") / uid_name, Path("/media"), Path("/mnt")]:
        if not root.is_dir():
            continue
        try:
            for e in sorted(root.iterdir()):
                if not e.name.startswith("."):
                    add(e.name, e)
        except PermissionError:
            pass

    GVFS_SCHEME = re.compile(
        r"^(smb-share|sftp|ftp|ftps|dav|davs|mtp|gphoto2|afp|nfs|http|https):",
        re.IGNORECASE,
    )
    uid = os.getuid()
    for gvfs_root in [Path(f"/run/user/{uid}/gvfs"), home / ".gvfs"]:
        if not gvfs_root.is_dir():
            continue
        try:
            for e in sorted(gvfs_root.iterdir()):
                if not e.is_dir() or not GVFS_SCHEME.match(e.name):
                    continue
                raw_name = e.name
                m_smb = re.match(r"smb-share:server=([^,]+),share=([^,]+)", raw_name, re.I)
                m_host = re.match(r"(\w+):.*?host=([^,]+)", raw_name, re.I)
                if m_smb:
                    label = f"{m_smb.group(1)} / {m_smb.group(2)}  (SMB)"
                elif m_host:
                    label = f"{m_host.group(2)}  ({m_host.group(1).upper()})"
                else:
                    label = re.sub(r"^\w+[-:]", "", raw_name)
                add(label, e)
        except PermissionError:
            pass

    return locations


def _resolve_path(raw: str) -> Path | None:
    p = Path(os.path.expandvars(raw)).expanduser()
    if not p.is_absolute():
        p = Path.cwd() / p
    if p.is_dir():
        return p
    try:
        rp = p.resolve()
        if rp.is_dir():
            return rp
    except Exception:
        pass
    return None


def _browse_subfolder(base: Path) -> Path | None:
    """Drill into subfolders. Numbers navigate; partial text searches all subdirs."""
    while True:
        try:
            all_subdirs = sorted(
                e for e in base.iterdir()
                if e.is_dir() and not e.name.startswith(".")
            )
        except PermissionError:
            all_subdirs = []

        display = all_subdirs[:15]
        overflow = len(all_subdirs) - 15

        render(title="Choose folder", context_lines=[f"📁 {DIM}{base}{R}"])

        if display:
            cols = []
            for i, d in enumerate(display, 1):
                cols.append(f"{CYAN}{i:>2}{R} {d.name}")
            # print in a tight 2-column layout if it fits
            for line in cols:
                print(f"  {line}")
            if overflow > 0:
                print(f"  {DIM}… +{overflow} more — type part of a name to search{R}")
        else:
            print(f"  {DIM}(no subfolders){R}")

        blank()
        print(f"  {DIM}Enter=use this · number=open · text=search name · path=jump{R}")
        raw = input(f"  {BOLD}Folder{R}: ").strip().strip("'\"")

        if raw == "":
            return base

        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(display):
                base = display[idx]
                continue
            err(f"Enter 1–{len(display)}, a name, or a path.")
            input("  Press Enter to continue...")
            continue

        candidate = Path(os.path.expandvars(raw)).expanduser()
        if candidate.is_absolute():
            if candidate.is_dir():
                base = candidate
                continue
            try:
                rp = candidate.resolve()
                if rp.is_dir():
                    base = rp
                    continue
            except Exception:
                pass
            err(f"Path not found: {raw}")
            input("  Press Enter to continue...")
            continue

        term = raw.lower()
        matches = [d for d in all_subdirs if term in d.name.lower()]

        if not matches:
            rel = base / raw
            if rel.is_dir():
                base = rel
                continue
            err(f"No subfolders match '{raw}'.")
            input("  Press Enter to continue...")
            continue

        if len(matches) == 1:
            base = matches[0]
            continue

        render(title="Choose folder",
               context_lines=[f"📁 {DIM}{base}{R}", f"🔎 matches for \"{raw}\""])
        for i, d in enumerate(matches, 1):
            print(f"  {CYAN}{i}{R}  {d.name}")
        blank()
        pick = input(f"  {BOLD}Number{R} (Enter=cancel): ").strip()
        if pick.isdigit() and 1 <= int(pick) <= len(matches):
            base = matches[int(pick) - 1]


def pick_folder() -> Path:
    locations = _find_all_locations()
    gvfs_labels = {"SMB", "FTP", "MTP", "SFTP", "DAV"}
    local_locs = [(l, p) for l, p in locations if not any(t in l for t in gvfs_labels)]
    net_locs   = [(l, p) for l, p in locations if any(t in l for t in gvfs_labels)]
    all_locs   = local_locs + net_locs
    n_local    = len(local_locs)

    while True:
        render(title="Choose folder")

        if local_locs:
            print(f"  {BOLD}Local:{R}")
            for i, (label, path) in enumerate(local_locs, 1):
                print(f"    {CYAN}{i}{R} {label}")
        if net_locs:
            print(f"  {BOLD}Network:{R}")
            for i, (label, path) in enumerate(net_locs, n_local + 1):
                print(f"    {CYAN}{i}{R} {label}")
        if not locations:
            print(f"  {DIM}No locations detected.{R}")

        blank()
        print(f"  {DIM}Pick a number, or paste/type any path (drag-and-drop OK).{R}")
        raw = input(f"  {BOLD}Folder{R}: ").strip().strip("'\"")

        if not raw:
            continue

        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(all_locs):
                result = _browse_subfolder(all_locs[idx][1])
                if result:
                    return result
                continue
            err(f"Enter 1–{len(all_locs)} or a path.")
            input("  Press Enter to continue...")
            continue

        p = _resolve_path(raw)
        if p:
            return p

        err(f"Folder not found: {raw}")
        print(f"  {DIM}Tip: Nautilus → right-click → Copy Location, then paste.{R}")
        input("  Press Enter to continue...")


# ─── File helpers ─────────────────────────────────────────────────────────────

def list_media(folder: Path) -> list[Path]:
    return sorted(f for f in folder.iterdir()
                  if f.is_file() and f.suffix.lower() in MEDIA_EXT)

def ep_str(season: int, ep: int) -> str:
    return f"S{season:02d}E{ep:02d}"

def extract_quality(name: str) -> str:
    m = re.search(r'(\d{3,4})p', name, re.IGNORECASE)
    return m.group(1) if m else ""

def clean_title(s: str) -> str:
    s = s.replace("_", " ").replace(".", " ")
    return re.sub(r'\s+', ' ', s).strip(" -")

def safe_rename(src: Path, dst: Path, dry_run: bool) -> bool:
    if dst.exists() and dst != src:
        warn(f"SKIP — exists: {dst.name}")
        return False
    if dry_run:
        dryline(f"{DIM}{src.name}{R}")
        print(f"           {GREEN}→ {dst.name}{R}")
        return True
    try:
        src.rename(dst)
        success(f"{DIM}{src.name}{R}\n     → {GREEN}{dst.name}{R}")
        return True
    except Exception as e:
        err(f"Failed: {src.name}  ({e})")
        return False


# ─── Parsers ──────────────────────────────────────────────────────────────────

def parse_fansub(filename: str) -> dict:
    stem = Path(filename).stem
    stem = re.sub(r'^\[.*?\]', '', stem).strip(" _")
    m = re.search(r'(?:_-_|[-_ ])(\d{1,4})(?:v\d)?(?:\s|\(|_|$)', stem)
    if not m:
        m = re.search(r'(\d{1,4})(?:v\d)?$', stem)
    ep = int(m.group(1)) if m else None
    title_part = re.split(r'_-_|(?<!\d)-(?!\d)', stem)[0].strip(" _")
    title_part = re.sub(r'\[.*?\]', '', title_part)
    show_guess = clean_title(title_part)
    return {"ep": ep, "quality": extract_quality(filename), "show_guess": show_guess}

def parse_one_pace(filename: str) -> dict:
    stem = Path(filename).stem
    cleaned = re.sub(r'^\[.*?\]\[.*?\]\s*', '', stem)
    quality = extract_quality(filename)
    trans_m = re.search(r'\[(En[^\]]*)\]', filename, re.IGNORECASE)
    translation = trans_m.group(1) if trans_m else ""
    cleaned = re.sub(r'\[.*?\]', '', cleaned).strip()
    parts = cleaned.split()
    ep_num, arc_parts = None, []
    for part in parts:
        if re.match(r'^\d+$', part):
            ep_num = int(part)
        elif ep_num is None:
            arc_parts.append(part)
    return {"ep": ep_num, "arc": " ".join(arc_parts),
            "quality": quality, "translation": translation}

def parse_simple(filename: str) -> dict:
    nums = re.findall(r'\d+', Path(filename).stem)
    return {"ep": int(nums[0]) if nums else None}

def parse_sxxexx(filename: str) -> dict:
    m = re.search(r'[Ss](\d{1,2})[Ee](\d{1,3})', filename)
    if m:
        return {"season": int(m.group(1)), "ep": int(m.group(2))}
    return {"season": None, "ep": None}


# ─── Settings review ──────────────────────────────────────────────────────────

def _yn(val: bool) -> str:
    return f"{GREEN}Yes{R}" if val else f"{YELLOW}No{R}"

def review_settings(settings: list[dict], *, title: str = "Settings",
                     context_lines: list[str] = None) -> list[dict]:
    """
    Shows current settings, lets the user type a number to edit one,
    Enter to continue, or 'b' to go back. Re-renders (clears screen) each loop
    so the context block stays at the top and the list never gets buried.
    """
    while True:
        render(title=title, context_lines=context_lines)
        print(f"  {BOLD}Settings{R}  {DIM}(Enter=continue · number=edit · b=back){R}")
        sep_line()
        for i, s in enumerate(settings, 1):
            v = s["value"]
            display = _yn(v) if s["kind"] == "bool" else f"{CYAN}{v}{R}"
            print(f"    {DIM}{i}{R}  {s['label']:<38}  {display}")
        sep_line()
        blank()

        raw = input(f"  Choice: ").strip()

        if raw.lower() in ("b", "back"):
            raise Back()
        if raw == "":
            return settings
        if not raw.isdigit() or not (1 <= int(raw) <= len(settings)):
            err(f"Enter 1–{len(settings)}, Enter, or 'b'.")
            input("  Press Enter to continue...")
            continue

        idx = int(raw) - 1
        s   = settings[idx]
        blank()
        if s.get("hint"):
            print(f"  {DIM}{s['hint']}{R}")

        if s["kind"] == "bool":
            try:
                s["value"] = ask_yn(s["label"], default_yes=s["value"])
            except Back:
                pass
        elif s["kind"] == "int":
            try:
                raw_v = ask(s["label"], default=str(s["value"]))
                if raw_v.lstrip("-").isdigit():
                    s["value"] = int(raw_v)
                else:
                    err("Not a number — keeping current value.")
                    input("  Press Enter to continue...")
            except Back:
                pass
        elif s["kind"] == "str":
            try:
                opts = s.get("options")
                if opts:
                    print(f"  Options: {', '.join(opts)}")
                raw_v = ask(s["label"], default=str(s["value"]))
                if opts and raw_v not in opts:
                    err(f"Must be one of: {', '.join(opts)}")
                    input("  Press Enter to continue...")
                elif raw_v:
                    s["value"] = raw_v
            except Back:
                pass


def get(settings: list[dict], key: str):
    for s in settings:
        if s["key"] == key:
            return s["value"]
    raise KeyError(key)


# ─── Core rename loop ─────────────────────────────────────────────────────────

def run_rename(files, folder, dry_run, build_fn):
    ok = skip = 0
    for i, f in enumerate(files, 1):
        new_name = build_fn(f, i)
        if new_name is None:
            warn(f"Skipping — could not determine new name: {f.name}")
            skip += 1
            continue
        if safe_rename(f, folder / new_name, dry_run):
            ok += 1
        else:
            skip += 1
    blank()
    verb = "would be " if dry_run else ""
    info(f"{ok}/{len(files)} files {verb}renamed.  {skip} skipped.")
    return ok, skip


# ─── Simple flows (modes 1–4) ─────────────────────────────────────────────────

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


# ─── Regex Builder (Mode 6) ───────────────────────────────────────────────────
#
# Framing for the user: we're identifying the PARTS that make up their
# current filename (show name, episode number, title, etc.) so we can
# reuse those exact parts to build a clean new filename. Internal jargon
# like "regex" and "capture group" stays out of user-facing text wherever
# possible — it's framed as "parts of the filename" throughout.

PATTERNS_FILE = Path.home() / ".config" / "rename_media" / "patterns.json"

CAPTURE_BLOCKS = {
    "show":    {"label": "Show name",        "regex": r"(?P<show>.+?)",
                "example": ("Horimiya - Episode 01 - Title 1080p", "Horimiya")},
    "ep":      {"label": "Episode number",   "regex": r"(?P<ep>\d+)",
                "example": ("Horimiya - Episode 01 - Title 1080p", "01")},
    "season":  {"label": "Season number",    "regex": r"(?P<season>\d{1,2})",
                "example": ("Show S02E05 Title", "02")},
    "title":   {"label": "Episode title",    "regex": r"(?P<title>.+?)",
                "example": ("Horimiya - Episode 01 - A Tiny Happenstance 1080p", "A Tiny Happenstance")},
    "quality": {"label": "Quality / resolution", "regex": r"(?P<quality>\d{3,4}p)",
                "example": ("Horimiya - Episode 01 - Title 1080p BDRip", "1080p")},
}

SEPARATOR_BLOCKS = {
    "sep":     {"label": '" - " (dash)',        "regex": r"\s*-\s*"},
    "word_ep": {"label": '"Episode"/"Ep."',     "regex": r"(?:Episode|Ep\.?)\s+"},
    "space":   {"label": "space",                "regex": r"\s+"},
    "dot":     {"label": 'dot "."',              "regex": r"\."},
    "bracket_open":  {"label": '"["',            "regex": r"\["},
    "bracket_close": {"label": '"]"',            "regex": r"\]"},
}

ALL_BLOCK_KEYS = list(CAPTURE_BLOCKS) + list(SEPARATOR_BLOCKS) + ["custom"]


def _block_regex(key: str, custom_text: str = "") -> str:
    if key in CAPTURE_BLOCKS:
        return CAPTURE_BLOCKS[key]["regex"]
    if key == "custom":
        return re.escape(custom_text)
    return SEPARATOR_BLOCKS[key]["regex"]


def _assemble_regex(sequence: list[dict]) -> str:
    parts = []
    for i, item in enumerate(sequence):
        key = item["key"]
        rx  = _block_regex(key, item.get("custom", ""))
        if key == "title":
            nxt = sequence[i + 1]["key"] if i + 1 < len(sequence) else ""
            if nxt == "quality":
                rx = r"(?P<title>.+?)\s+"
            elif i == len(sequence) - 1:
                rx = r"(?P<title>.+?)(?=\s*\[|\s*-\s*\w+$|$)"
            else:
                rx = r"(?P<title>.+?)"
        parts.append(rx)
    return "^" + "".join(parts) + r"(?:[\s\[].*)?$"


def _test_pattern(pattern: str, files: list[Path]) -> list[dict]:
    try:
        rx = re.compile(pattern, re.IGNORECASE)
    except re.error:
        return [{"file": f.name, "match": None} for f in files[:5]]
    results = []
    for f in files[:5]:
        m = rx.match(f.stem)
        results.append({"file": f.name, "match": m.groupdict() if m else None})
    return results


def _show_test_results(results: list[dict]):
    print(f"  {BOLD}Matching against your files:{R}")
    any_fail = False
    for r in results:
        if r["match"]:
            gd    = r["match"]
            parts = [f"{DIM}{k}{R}={CYAN}{gd[k]}{R}"
                     for k in ("show","season","ep","title","quality")
                     if k in gd and gd[k]]
            print(f"  {GREEN}✓{R} {DIM}{r['file']}{R}")
            print(f"      {' '.join(parts)}")
        else:
            print(f"  {RED}✗{R} no match: {DIM}{r['file']}{R}")
            any_fail = True
    if any_fail:
        warn("Some files didn't match.")


def _load_saved_patterns() -> dict:
    if PATTERNS_FILE.exists():
        try:
            return json.loads(PATTERNS_FILE.read_text())
        except Exception:
            pass
    return {}


def _save_pattern(name: str, data: dict):
    PATTERNS_FILE.parent.mkdir(parents=True, exist_ok=True)
    saved = _load_saved_patterns()
    saved[name] = data
    PATTERNS_FILE.write_text(json.dumps(saved, indent=2))
    success(f"Saved as \"{name}\"")


def _pick_saved_pattern() -> dict | None:
    saved = _load_saved_patterns()
    if not saved:
        warn("No saved patterns yet.")
        input("  Press Enter to continue...")
        return None
    names = list(saved.keys())
    render(title="Load a saved pattern")
    for i, name in enumerate(names, 1):
        p = saved[name]
        print(f"  {CYAN}{i}{R}  {BOLD}{name}{R}  {DIM}→ {p.get('output_fmt','?')}{R}")
    blank()
    while True:
        raw = input(f"  {BOLD}Number{R} (b=back): ").strip()
        _check_back(raw)
        if raw.isdigit() and 1 <= int(raw) <= len(names):
            return saved[names[int(raw) - 1]]
        err(f"Enter 1–{len(names)} or 'b'.")


# ── Output format ─────────────────────────────────────────────────────────────

OUTPUT_TOKENS = {
    "{show}": "Show name", "{SE}": "S01E01", "{S}": "S01",
    "{E}": "E01", "{title}": "title", "{quality}": "1080p",
}

def _build_output_fmt(captured_groups: set[str], default_suggestion: str = None) -> str:
    print(f"  {BOLD}Tokens:{R}  " + "  ".join(
        f"{CYAN}{t}{R}{DIM}={d}{R}" for t, d in OUTPUT_TOKENS.items()
    ))
    blank()
    suggestion = default_suggestion or "{show} - {SE}"
    if default_suggestion is None:
        if "title"   in captured_groups: suggestion += " - {title}"
        if "quality" in captured_groups: suggestion += " ({quality})"
    fmt = ask("New filename format", default=suggestion)
    return fmt if fmt else suggestion


def _apply_output_fmt(fmt: str, groups: dict, season_override: int) -> str:
    season = int(groups.get("season") or season_override)
    ep     = int(groups.get("ep", 1))
    out    = fmt
    out    = out.replace("{show}",    groups.get("show", "").strip())
    out    = out.replace("{SE}",      ep_str(season, ep))
    out    = out.replace("{S}",       f"S{season:02d}")
    out    = out.replace("{E}",       f"E{ep:02d}")
    out    = out.replace("{title}",   groups.get("title", "").strip())
    q      = groups.get("quality", "")
    out    = out.replace("{quality}", q if q else "")
    out    = re.sub(r'  +', ' ', out).strip(" -–")
    out    = re.sub(r'\s+-\s*$', '', out).strip()
    return out


def _preview_lines(pattern: str, fmt: str, season: int, files: list[Path], n: int = 3) -> list[str]:
    """Build a short list of 'old → new' preview lines for context blocks."""
    try:
        rx = re.compile(pattern, re.IGNORECASE)
    except re.error:
        return []
    lines = []
    for f in files[:n]:
        m = rx.match(f.stem)
        if m:
            out = _apply_output_fmt(fmt, m.groupdict(), season) + f.suffix.lower()
            lines.append(f"{DIM}{f.name[:42]}{'…' if len(f.name)>42 else ''}{R}")
            lines.append(f"  {GREEN}→ {out}{R}")
    return lines


# ── Builder step machine ──────────────────────────────────────────────────────
#
# A BuildState carries everything accumulated so far through the step
# machine, so every step can render a compact "what we have so far" block
# at the top before showing its own content.

class BuildState:
    def __init__(self):
        self.sample: str = ""
        self.sequence: list[dict] = []
        self.pattern: str = ""
        self.output_fmt: str = ""

    def sequence_str(self) -> str:
        if not self.sequence:
            return f"{DIM}(none yet){R}"
        parts = []
        for s in self.sequence:
            if s["key"] == "custom":
                parts.append(f"{MAGENTA}\"{s.get('custom','')}\"{R}")
            elif s["key"] in CAPTURE_BLOCKS:
                parts.append(f"{GREEN}{s['key']}{R}")
            else:
                parts.append(f"{DIM}{s['key']}{R}")
        return " → ".join(parts)

    def context_lines(self, extra: list[str] = None) -> list[str]:
        lines = []
        if self.sample:
            disp = self.sample if len(self.sample) <= 50 else self.sample[:47] + "…"
            lines.append(f"File: {DIM}{disp}{R}")
        if self.sequence:
            lines.append(f"Parts so far: {self.sequence_str()}")
        if extra:
            lines.extend(extra)
        return lines


def _builder_step_sample(files: list[Path], state: BuildState) -> None:
    """Step 1: get a sample filename to rebuild from."""
    render(
        title="Step 1/5 — Pick a file to learn from",
        sub="We'll break this filename into parts, then reuse those parts to build clean new names.",
    )
    if files:
        print(f"  {BOLD}Detected:{R}  {files[0].name}")
        blank()
    sample = ask("Use this filename (or paste a different one)",
                 default=files[0].name if files else "", back=False)
    state.sample = sample.strip("'\"") or (files[0].name if files else "")


def _annotate_sample(sample: str) -> list[str]:
    """Return colour-coded lines breaking the sample into recognisable parts."""
    stem = Path(sample).stem
    BLOCK_COLORS = {
        "show": MAGENTA, "sep": DIM, "word_ep": YELLOW, "ep": CYAN,
        "title": GREEN, "quality": RED, "season": BLUE, "dot": DIM, "space": DIM,
    }
    PROBE_ORDER = [
        ("show",    r"^(.+?)(?=\s*-\s*(?:Episode|Ep\.?)\s+\d|\s*-\s*\d|\.\d)"),
        ("sep",     r"\s*-\s*"),
        ("word_ep", r"(?:Episode|Ep\.?)\s+"),
        ("ep",      r"\d{1,4}"),
        ("sep",     r"\s*-\s*"),
        ("title",   r".+?(?=\s+\d{3,4}p\b)"),
        ("space",   r"\s+"),
        ("quality", r"\d{3,4}p"),
        ("season",  r"(?<=[Ss])\d{1,2}(?=[Ee])"),
        ("dot",     r"\."),
    ]
    spans, pos, used = [], 0, set()
    for key, pat in PROBE_ORDER:
        if pos >= len(stem):
            break
        try:
            m = re.match(pat, stem[pos:], re.IGNORECASE)
            if m and m.group(0):
                spans.append((pos, pos + len(m.group(0)), key if key not in used else key + "_2"))
                used.add(key)
                pos += len(m.group(0))
        except re.error:
            pass

    coloured, cursor = "", 0
    for start, end, key in sorted(spans, key=lambda x: x[0]):
        base_key = key.rstrip("_2")
        col = BLOCK_COLORS.get(base_key, "")
        if cursor < start:
            coloured += f"{DIM}{stem[cursor:start]}{R}"
        coloured += f"{col}{BOLD}{stem[start:end]}{R}"
        cursor = end
    if cursor < len(stem):
        coloured += f"{DIM}{stem[cursor:]}{R}"

    legend_keys = list(dict.fromkeys(key.rstrip("_2") for _, _, key in spans))
    legend = "  ".join(
        f"{BLOCK_COLORS.get(k,'')}{BOLD}{k}{R}"
        for k in legend_keys
    )
    return [coloured, f"{DIM}↳ {legend}{R}" if legend else ""]


def _builder_step_blocks(files: list[Path], state: BuildState) -> None:
    """Step 2: identify the parts of the filename, one at a time."""
    cap_keys = list(CAPTURE_BLOCKS.keys())
    sep_keys = list(SEPARATOR_BLOCKS.keys())
    all_by_num = cap_keys + sep_keys + ["custom"]

    annotated = _annotate_sample(state.sample)

    while True:
        render(
            title="Step 2/5 — Identify the parts",
            context_lines=[f"File: {DIM}{state.sample}{R}"] + annotated,
            sub="Pick the part that matches each piece of the filename, left to right.",
        )

        print(f"  {BOLD}Parts that capture a value:{R}")
        for i, k in enumerate(cap_keys, 1):
            b = CAPTURE_BLOCKS[k]
            print(f"   {CYAN}{i:>2}{R} {MAGENTA}{k:<8}{R} {b['label']:<22}{DIM}e.g. \"{b['example'][1]}\"{R}")
        blank()
        print(f"  {BOLD}Connecting parts (no value kept):{R}")
        for i, k in enumerate(sep_keys, 1):
            b = SEPARATOR_BLOCKS[k]
            print(f"   {CYAN}{i+len(cap_keys):>2}{R} {MAGENTA}{k:<14}{R} {b['label']}")
        cust_num = len(cap_keys) + len(sep_keys) + 1
        print(f"   {CYAN}{cust_num:>2}{R} {MAGENTA}{'custom':<14}{R} text you type")
        blank()

        if state.sequence:
            print(f"  {BOLD}Built so far:{R}  {state.sequence_str()}")
            try:
                pat = _assemble_regex(state.sequence)
                rx  = re.compile(pat, re.IGNORECASE)
                m   = rx.match(Path(state.sample).stem)
                if m:
                    gd = m.groupdict()
                    print(f"  {GREEN}✓ matches:{R}  " +
                          "  ".join(f"{DIM}{k}{R}={GREEN}{v}{R}" for k, v in gd.items() if v))
                else:
                    print(f"  {YELLOW}✗ no match yet{R}")
            except Exception:
                pass
        else:
            print(f"  {DIM}Nothing added yet.{R}")

        blank()
        raw = input(f"  Add part (number or name, 'done', 'undo', 'b'): ").strip()
        low = raw.lower()

        if low in ("b", "back"):
            raise Back()
        if low == "done":
            if not state.sequence:
                err("Add at least one part first.")
                input("  Press Enter to continue...")
                continue
            if not any(s["key"] == "ep" for s in state.sequence):
                err("You need an 'Episode number' part — it's how files get numbered.")
                input("  Press Enter to continue...")
                continue
            return
        if low == "undo":
            if state.sequence:
                removed = state.sequence.pop()
                info(f"Removed: {removed['key']}")
            continue

        key = None
        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(all_by_num):
                key = all_by_num[idx]
            else:
                err(f"Enter 1–{len(all_by_num)}, a name, 'done', 'undo', or 'b'.")
                input("  Press Enter to continue...")
                continue
        elif low in ALL_BLOCK_KEYS:
            key = low
        else:
            err(f"Unknown part '{raw}'.")
            input("  Press Enter to continue...")
            continue

        if key == "custom":
            txt = input(f"  Exact text to match: ").strip()
            if txt:
                state.sequence.append({"key": "custom", "custom": txt})
        else:
            state.sequence.append({"key": key})


def _builder_step_test(files: list[Path], state: BuildState) -> None:
    """Step 3: confirm the assembled pattern correctly identifies parts in real files."""
    state.pattern = _assemble_regex(state.sequence)

    while True:
        render(
            title="Step 3/5 — Check it against your files",
            context_lines=state.context_lines(),
        )
        test_targets = list(files[:5])
        sample_path  = Path(state.sample)
        if state.sample and state.sample not in [f.name for f in test_targets]:
            test_targets = [sample_path] + test_targets[:4]
        _show_test_results(_test_pattern(state.pattern, test_targets))

        blank()
        print(f"  {CYAN}1{R} Looks good, continue   {CYAN}2{R} Edit manually   {CYAN}b{R} Back")
        raw = input(f"  Choice: ").strip().lower()
        _check_back(raw)

        if raw == "1":
            return
        if raw == "2":
            new_pat = ask("Pattern", default=state.pattern)
            try:
                re.compile(new_pat, re.IGNORECASE)
                state.pattern = new_pat
            except re.error as e:
                err(f"Invalid: {e}")
                input("  Press Enter to continue...")


def _builder_step_output(files: list[Path], state: BuildState, season: int) -> None:
    """Step 4: decide how the identified parts get put back together."""
    try:
        rx = re.compile(state.pattern, re.IGNORECASE)
        captured = set(rx.groupindex.keys())
    except Exception:
        rx, captured = None, set()

    fmt_override = None
    while True:
        render(
            title="Step 4/5 — Rebuild the filename",
            context_lines=state.context_lines(),
            sub="Now choose how to put the parts back together into the new name.",
        )
        fmt = _build_output_fmt(captured, default_suggestion=fmt_override)
        preview = _preview_lines(state.pattern, fmt, season, files, n=3)
        if preview:
            blank()
            print(f"  {BOLD}Preview:{R}")
            for line in preview:
                print(f"  {line}")
        else:
            warn("No files matched — go back and check the parts.")

        blank()
        print(f"  {CYAN}1{R} Use this   {CYAN}2{R} Try a different format   {CYAN}b{R} Back")
        raw = input(f"  Choice: ").strip().lower()
        _check_back(raw)
        if raw == "1":
            state.output_fmt = fmt
            return
        if raw == "2":
            fmt_override = fmt


def _builder_step_save(state: BuildState, show: str) -> None:
    """Step 5: optionally save this part-recipe for reuse."""
    render(
        title="Step 5/5 — Save for next time?",
        context_lines=state.context_lines(),
    )
    if ask_yn("Save this pattern?", default_yes=True):
        pname = ask("Name", default=show or "my-pattern")
        _save_pattern(pname, {
            "pattern":    state.pattern,
            "output_fmt": state.output_fmt,
            "sequence":   [s["key"] for s in state.sequence],
        })


def flow_regex_builder(files: list[Path], show: str, season: int, folder: Path):
    render(title="Mode 6 — Build From a Sample File",
           sub="We look at one of your filenames, find its parts, then reuse those\n"
               "  parts to build a clean new name for every file.")

    print(f"  {CYAN}1{R} Build new (step-by-step)   {CYAN}2{R} Load a saved pattern")
    blank()

    state = BuildState()
    loaded = False

    while True:
        start = input(f"  {BOLD}Choice{R}: ").strip()
        if start == "2":
            try:
                saved = _pick_saved_pattern()
            except Back:
                continue
            if saved:
                state.pattern    = saved.get("pattern", "")
                state.output_fmt = saved.get("output_fmt", "")
                render(title="Loaded pattern",
                       context_lines=[f"Output: {DIM}{state.output_fmt}{R}"])
                _show_test_results(_test_pattern(state.pattern, files))
                input("  Press Enter to continue...")
                loaded = True
                break
        if start in ("1", "2"):
            break
        err("Enter 1 or 2.")

    if not loaded:
        step = 1
        while step <= 5:
            try:
                if step == 1:
                    _builder_step_sample(files, state)
                    step = 2
                elif step == 2:
                    _builder_step_blocks(files, state)
                    step = 3
                elif step == 3:
                    _builder_step_test(files, state)
                    step = 4
                elif step == 4:
                    _builder_step_output(files, state, season)
                    step = 5
                elif step == 5:
                    _builder_step_save(state, show)
                    step = 6
            except Back:
                step = max(1, step - 1)

    try:
        rx_final = re.compile(state.pattern, re.IGNORECASE)
    except re.error as e:
        err(f"Pattern is invalid: {e}")
        return None

    settings = [
        {"key": "show",   "label": "Show name (overrides detected)", "value": show, "kind": "str"},
        {"key": "season", "label": "Season number", "value": season, "kind": "int"},
        {"key": "output", "label": "Output format", "value": state.output_fmt, "kind": "str"},
    ]
    settings = review_settings(settings, title="Final Settings",
                                context_lines=state.context_lines())

    final_show       = get(settings, "show")
    final_season     = get(settings, "season")
    final_output_fmt = get(settings, "output")

    def build(f, i):
        m = rx_final.match(f.stem)
        if not m:
            return None
        gd = m.groupdict()
        if final_show:
            gd["show"] = final_show
        return _apply_output_fmt(final_output_fmt, gd, final_season) + f.suffix.lower()

    return build


# ─── Utilities ────────────────────────────────────────────────────────────────

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
            pairs.append((f, folder / (new_name + rest + f.suffix.lower())))

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


# ─── Main ─────────────────────────────────────────────────────────────────────

FLOW_BUILDERS = {
    "1": flow_fansub, "2": flow_one_pace, "3": flow_simple,
    "4": flow_sxxexx, "5": flow_custom_regex, "6": flow_regex_builder,
}

MODE_LABELS = {
    "1": ("Standard Fansub",        "[DB]Show_-_01_(info).mkv"),
    "2": ("One Pace / Group+Range", "[Group][841-842] Arc 10 [720p].mp4"),
    "3": ("Simple Numbered",        "01.mkv / Episode 05.mkv"),
    "4": ("Normalize S##E##",       "old.show.S01E04.1080p.mkv"),
    "5": ("Raw Regex",              "type your own pattern"),
    "6": ("Build From Sample",      "guided, works on any format"),
}


def main_menu():
    render(title="Media Batch Renamer · Linux Edition",
           sub="Type 'b' at most prompts to go back a step.")
    print(f"  {BOLD}Rename modes:{R}")
    for k, (label, example) in MODE_LABELS.items():
        print(f"   {CYAN}{k}{R} {label:<24}{DIM}{example}{R}")
    blank()
    print(f"  {BOLD}Utilities:{R}")
    print(f"   {CYAN}7{R} Preview files in a folder")
    print(f"   {CYAN}8{R} Split into Season XX/ subfolders")
    print(f"   {CYAN}9{R} Rename show name across files")
    print(f"   {CYAN}q{R} Quit")
    blank()
    return input(f"  {BOLD}Choice{R}: ").strip().lower()


def setup_show_and_season(files, choice):
    detected_show = ""
    if choice == "1":
        detected_show = parse_fansub(files[0].name).get("show_guess", "")

    show = ask("Show name", hint="Start of every renamed file.", default=detected_show)
    if not show:
        err("Show name cannot be empty.")
        return None, None
    raw_s  = ask("Season", default="1")
    season = int(raw_s) if raw_s.isdigit() else 1
    return show, season


def main():
    while True:
        choice = main_menu()

        if choice == "q":
            print(f"\n  {DIM}Bye!{R}\n")
            break

        if choice == "7":
            util_preview(pick_folder())
            input("\n  Press Enter to return to menu...")
            continue
        if choice == "8":
            util_split(pick_folder())
            input("\n  Press Enter to return to menu...")
            continue
        if choice == "9":
            util_rename_show(pick_folder())
            input("\n  Press Enter to return to menu...")
            continue

        if choice not in FLOW_BUILDERS:
            err("Please enter 1–9 or q.")
            input("  Press Enter to continue...")
            continue

        label, example = MODE_LABELS[choice]
        render(title=f"Mode: {label}", context_lines=[f"Matches: {DIM}{example}{R}"])

        folder = pick_folder()
        files  = list_media(folder)

        if not files:
            warn("No media files found in that folder.")
            input("  Press Enter to continue...")
            continue

        render(title=f"Mode: {label}",
               context_lines=[f"Folder: {DIM}{folder}{R}", f"Files found: {len(files)}"])

        show, season = setup_show_and_season(files, choice)
        if show is None:
            input("  Press Enter to continue...")
            continue

        build_fn = None
        while build_fn is None:
            try:
                build_fn = FLOW_BUILDERS[choice](files, show, season, folder)
            except Back:
                render(title=f"Mode: {label}")
                show, season = setup_show_and_season(files, choice)
                if show is None:
                    break
        if build_fn is None:
            continue

        while True:
            render(title="Dry run — no files changed yet",
                   context_lines=[f"Mode: {label}", f"Folder: {DIM}{folder}{R}"])
            run_rename(files, folder, dry_run=True, build_fn=build_fn)

            blank()
            print(f"  {CYAN}1{R} Apply for real   {CYAN}2{R} Change settings   {CYAN}3{R} Cancel")
            action = input(f"  Choice: ").strip()

            if action == "1":
                files = list_media(folder)
                render(title="Renaming…")
                run_rename(files, folder, dry_run=False, build_fn=build_fn)
                input("\n  Press Enter to continue...")
                break
            elif action == "2":
                try:
                    build_fn = FLOW_BUILDERS[choice](files, show, season, folder)
                except Back:
                    pass
            elif action == "3":
                info("Cancelled — no files changed.")
                break
            else:
                err("Enter 1, 2, or 3.")
                input("  Press Enter to continue...")

        blank()
        if not ask_yn("Rename another batch?", back=False):
            print(f"\n  {DIM}Done!{R}\n")
            break


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n\n  {DIM}Interrupted.{R}\n")
        sys.exit(0)