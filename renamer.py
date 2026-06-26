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
BG_YEL = "\033[43m"
BLK    = "\033[30m"

def c(text, color): return f"{color}{text}{R}"
def header(text):   print(f"\n{BOLD}{CYAN}{'─'*62}{R}\n  {BOLD}{text}{R}\n{DIM}{'─'*62}{R}")
def success(text):  print(f"  {GREEN}✓{R}  {text}")
def warn(text):     print(f"  {YELLOW}⚠{R}  {text}")
def err(text):      print(f"  {RED}✗{R}  {text}")
def info(text):     print(f"  {BLUE}·{R}  {text}")
def blank():        print()
def dryline(text):  print(f"  {BG_YEL}{BLK} DRY RUN {R}  {text}")
def sep_line():     print(f"  {DIM}{'─'*58}{R}")

MEDIA_EXT = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".ts", ".wmv"}

# ─── Back navigation ──────────────────────────────────────────────────────────
# Any step can raise Back() to return to the previous step.
# Type "b" or "back" at most prompts to trigger it.

class Back(Exception):
    pass

def _check_back(raw: str):
    if raw.strip().lower() in ("b", "back"):
        raise Back()

# ─── Input helpers ────────────────────────────────────────────────────────────

def ask(label: str, hint: str = "", default: str = "", back: bool = True) -> str:
    if hint:
        print(f"  {DIM}{hint}{R}")
    suffix = f"  {DIM}(or 'b' to go back){R}" if back else ""
    if default:
        val = input(f"  {BOLD}{label}{R} [{DIM}{default}{R}]{suffix}: ").strip()
        if back: _check_back(val)
        return val if val else default
    val = input(f"  {BOLD}{label}{R}{suffix}: ").strip()
    if back: _check_back(val)
    return val

def ask_yn(label: str, hint: str = "", default_yes: bool = False, back: bool = True) -> bool:
    opts = f"{BOLD}Y{R}/n" if default_yes else f"y/{BOLD}N{R}"
    bk   = f"  {DIM}(or 'b' to go back){R}" if back else ""
    if hint:
        print(f"  {DIM}{hint}{R}")
    raw = input(f"  {label} [{opts}]{bk}: ").strip().lower()
    if back: _check_back(raw)
    return (raw == "y") if raw else default_yes

def section(title: str):
    print(f"\n  {CYAN}{BOLD}── {title}{R}")

def step_header(n: int, total: int, title: str):
    print(f"\n  {BOLD}{CYAN}Step {n}/{total}  —  {title}{R}")
    print(f"  {DIM}Type 'b' at any prompt to go back to the previous step.{R}")
    sep_line()

# ─── GVFS / SMB mount discovery ───────────────────────────────────────────────

def _find_all_locations() -> list[tuple[str, Path]]:
    """
    Return labelled (name, path) pairs for every useful location on this machine:
      - Home directory
      - XDG user dirs  (Videos, Downloads, Documents, Music, Pictures)
      - /media/<user>  children  (USB drives, optical discs)
      - /media  and  /mnt  direct children  (other local mounts)
      - GVFS mounts  (Nautilus SMB / network shares)
    Duplicates and non-existent paths are filtered out.
    """
    locations: list[tuple[str, Path]] = []
    seen: set[Path] = set()

    def add(label: str, path: Path):
        if path.is_dir() and path not in seen:
            seen.add(path)
            locations.append((label, path))

    home = Path.home()
    add("Home", home)

    # XDG user dirs
    WANT_XDG = {
        "XDG_VIDEOS_DIR":    "Videos",
        "XDG_DOWNLOAD_DIR":  "Downloads",
        "XDG_DOCUMENTS_DIR": "Documents",
        "XDG_MUSIC_DIR":     "Music",
        "XDG_PICTURES_DIR":  "Pictures",
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
        # Fallback: common names directly under home
        for name in ("Videos", "Downloads", "Documents", "Music", "Pictures"):
            add(name, home / name)

    # Local media mounts  (/media/<user>, /media, /mnt)
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

    # GVFS / Nautilus network shares  (SMB, FTP, MTP, etc.)
    # /run/user/{uid}/doc  is the XDG document portal (random hex IDs per app) — skip it entirely.
    # Only keep entries under /run/user/{uid}/gvfs  and  ~/.gvfs  whose names start with
    # a real GVFS URI scheme — that rules out any stray portal or temp entries.
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
                # Build a human-readable label from the URI-style name
                raw_name = e.name
                m_smb = re.match(r"smb-share:server=([^,]+),share=([^,]+)", raw_name, re.I)
                m_host = re.match(r"(\w+):.*?host=([^,]+)", raw_name, re.I)
                if m_smb:
                    label = f"{m_smb.group(1)} / {m_smb.group(2)}  (SMB)"
                elif m_host:
                    label = f"{m_host.group(2)}  ({m_host.group(1).upper()})"
                else:
                    # fallback: strip the scheme prefix for brevity
                    label = re.sub(r"^\w+[-:]", "", raw_name)
                add(label, e)
        except PermissionError:
            pass

    return locations


def _resolve_path(raw: str) -> Path | None:
    """Resolve a typed/pasted path without calling resolve() on GVFS paths."""
    p = Path(os.path.expandvars(raw)).expanduser()
    if not p.is_absolute():
        p = Path.cwd() / p
    if p.is_dir():
        return p
    # Last-ditch for normal symlinks — but not on FUSE/GVFS
    try:
        rp = p.resolve()
        if rp.is_dir():
            return rp
    except Exception:
        pass
    return None


def _browse_subfolder(base: Path) -> Path | None:
    """
    Drill into subfolders of base.
    - Shows up to 20 subdirs as a numbered list.
    - Numbers navigate directly.
    - Partial text matches case-insensitively; if one match → enter it;
      if multiple → show only those and ask for a number.
    - Absolute paths and relative paths (from current base) also accepted.
    - Enter with no input confirms the current folder.
    """
    # If the input is unambiguously a number that was shown, use it.
    # If it matches one subdir name (partial, case-insensitive), enter it.
    # If it matches several, list only those and ask again.

    while True:
        try:
            all_subdirs = sorted(
                e for e in base.iterdir()
                if e.is_dir() and not e.name.startswith(".")
            )
        except PermissionError:
            all_subdirs = []

        display = all_subdirs[:20]
        overflow = len(all_subdirs) - 20

        blank()
        print(f"  {BOLD}Current folder:{R}  {DIM}{base}{R}")
        blank()

        if display:
            print(f"  {BOLD}Subfolders:{R}")
            for i, d in enumerate(display, 1):
                print(f"    {CYAN}{i}{R}  {d.name}")
            if overflow > 0:
                print(f"    {DIM}… {overflow} more not shown — type a partial name to search all{R}")
        else:
            print(f"  {DIM}(no subfolders){R}")

        blank()
        print(f"  {BOLD}Enter{R}          use this folder")
        print(f"  {BOLD}number{R}         open that subfolder")
        print(f"  {BOLD}partial name{R}   search all subfolders (e.g. \"hori\" → Horimiya)")
        print(f"  {BOLD}/absolute/path{R} or {BOLD}relative/path{R}  jump anywhere")
        blank()

        raw = input(f"  {BOLD}Choice{R}: ").strip().strip("'\"")

        # ── Confirm current folder ────────────────────────────────────────────
        if raw == "":
            return base

        # ── Numbered pick from the displayed list ─────────────────────────────
        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(display):
                base = display[idx]
                continue
            err(f"Enter 1–{len(display)}, a name, or a path.")
            continue

        # ── Absolute or clearly relative path ────────────────────────────────
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
            continue

        # ── Partial / exact name search across ALL subdirs ────────────────────
        term = raw.lower()
        matches = [d for d in all_subdirs if term in d.name.lower()]

        if not matches:
            # Last resort: treat as a relative path
            rel = base / raw
            if rel.is_dir():
                base = rel
                continue
            err(f"No subfolders match '{raw}'.")
            continue

        if len(matches) == 1:
            base = matches[0]
            info(f"Matched: {matches[0].name}")
            continue

        # Multiple matches — show a short disambiguation list
        blank()
        print(f"  {BOLD}{len(matches)} matches for '{raw}':{R}")
        for i, d in enumerate(matches, 1):
            print(f"    {CYAN}{i}{R}  {d.name}")
        blank()

        while True:
            pick = input(f"  {BOLD}Number{R} (or Enter to cancel): ").strip()
            if pick == "":
                break
            if pick.isdigit() and 1 <= int(pick) <= len(matches):
                base = matches[int(pick) - 1]
                break
            err(f"Enter 1–{len(matches)} or leave blank.")



def pick_folder() -> Path:
    section("Choose folder")
    locations = _find_all_locations()

    # Group into sections for display
    gvfs_labels = {"SMB", "FTP", "MTP", "SFTP", "DAV"}

    local_locs  = [(l, p) for l, p in locations
                   if not any(tag in l for tag in gvfs_labels)]
    net_locs    = [(l, p) for l, p in locations
                   if any(tag in l for tag in gvfs_labels)]

    all_locs    = local_locs + net_locs   # combined list for number indexing
    n_local     = len(local_locs)

    blank()
    if local_locs:
        print(f"  {BOLD}Local & removable:{R}")
        for i, (label, path) in enumerate(local_locs, 1):
            print(f"    {CYAN}{i}{R}  {BOLD}{label:<18}{R}  {DIM}{path}{R}")
        blank()

    if net_locs:
        print(f"  {BOLD}Network shares  (Nautilus / GVFS):{R}")
        for i, (label, path) in enumerate(net_locs, n_local + 1):
            print(f"    {CYAN}{i}{R}  {BOLD}{label:<28}{R}  {DIM}{path}{R}")
        blank()

    if not locations:
        print(f"  {DIM}No locations detected — type or paste a path.{R}")
        blank()

    print(f"  {DIM}Enter a number to browse, or paste / type any path directly.")
    print(f"  Drag-and-drop from Nautilus also works (quotes are stripped).{R}")

    while True:
        blank()
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
            else:
                err(f"Enter 1–{len(all_locs)} or a path.")
                continue

        # Typed / pasted path
        p = _resolve_path(raw)
        if p:
            return p

        err(f"Folder not found: {raw}")
        print(f"  {DIM}Tip: in Nautilus right-click the folder → Copy Location, then paste.{R}")


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
        warn(f"SKIP — target already exists: {dst.name}")
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
# Returns the modified settings list, or raises Back() if user types 'b'.

def _yn(val: bool) -> str:
    return f"{GREEN}Yes{R}" if val else f"{YELLOW}No{R}"

def review_settings(settings: list[dict]) -> list[dict]:
    while True:
        blank()
        sep_line()
        print(f"  {BOLD}  Settings  —  press Enter to continue, number to edit, 'b' to go back{R}")
        sep_line()
        for i, s in enumerate(settings, 1):
            v = s["value"]
            display = _yn(v) if s["kind"] == "bool" else f"{CYAN}{v}{R}"
            print(f"    {DIM}{i}{R}  {s['label']:<40}  {display}")
        sep_line()
        blank()

        raw = input(f"  Choice: ").strip()

        if raw.lower() in ("b", "back"):
            raise Back()

        if raw == "":
            return settings

        if not raw.isdigit() or not (1 <= int(raw) <= len(settings)):
            err(f"Enter a number 1–{len(settings)}, Enter to continue, or 'b' to go back.")
            continue

        idx = int(raw) - 1
        s   = settings[idx]
        if s.get("hint"):
            print(f"\n  {DIM}{s['hint']}{R}")

        if s["kind"] == "bool":
            try:
                s["value"] = ask_yn(s["label"], default_yes=s["value"])
            except Back:
                pass   # stay on the settings screen

        elif s["kind"] == "int":
            try:
                raw_v = ask(s["label"], default=str(s["value"]))
                if raw_v.lstrip("-").isdigit():
                    s["value"] = int(raw_v)
                else:
                    err("Not a number — keeping current value.")
            except Back:
                pass

        elif s["kind"] == "str":
            try:
                opts  = s.get("options")
                if opts:
                    print(f"  Options: {', '.join(opts)}")
                raw_v = ask(s["label"], default=str(s["value"]))
                if opts and raw_v not in opts:
                    err(f"Must be one of: {', '.join(opts)} — keeping current value.")
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
# Each returns a build_fn or raises Back().

def flow_fansub(files, show, season, folder):
    header("Mode 1 — Standard Fansub")
    settings = [
        {"key": "show",    "label": "Show name",
         "value": show,    "kind": "str",
         "hint": "The text that goes at the start of every renamed file."},
        {"key": "season",  "label": "Season number",
         "value": season,  "kind": "int",
         "hint": "Used to build S01, S02 etc."},
        {"key": "use_seq", "label": "Sequential episode numbering",
         "value": False,   "kind": "bool",
         "hint": "No  = use the number found in each filename (e.g. _-_05_ → E05).\n"
                 "  Yes = ignore filename numbers, count files alphabetically."},
        {"key": "quality", "label": "Add quality tag  (e.g. 1080p)",
         "value": False,   "kind": "bool",
         "hint": "Yes → My Show - S01E01 (1080p).mkv\n"
                 "  No  → My Show - S01E01.mkv"},
    ]
    settings = review_settings(settings)
    show   = get(settings, "show")
    season = get(settings, "season")
    def build(f, i):
        d  = parse_fansub(f.name)
        ep = i if get(settings, "use_seq") else (d["ep"] or i)
        q  = f" ({d['quality']}p)" if get(settings, "quality") and d["quality"] else ""
        return f"{show} - {ep_str(season, ep)}{q}{f.suffix.lower()}"
    return build


def flow_one_pace(files, show, season, folder):
    header("Mode 2 — One Pace / Group+Range")
    settings = [
        {"key": "show",    "label": "Show name",        "value": show,   "kind": "str"},
        {"key": "season",  "label": "Season number",    "value": season, "kind": "int"},
        {"key": "arc",     "label": "Include arc name", "value": True,   "kind": "bool",
         "hint": "Yes → My Show - S01E10 - Whole Cake Island.mkv"},
        {"key": "part",    "label": "Include part number after arc", "value": False, "kind": "bool",
         "hint": "Yes → … - Whole Cake Island Part 10.mkv"},
        {"key": "quality", "label": "Add quality tag  (e.g. 720p)",   "value": False, "kind": "bool"},
        {"key": "trans",   "label": "Add translation tag  (e.g. En Sub)", "value": False, "kind": "bool"},
        {"key": "use_seq", "label": "Sequential episode numbering",    "value": False, "kind": "bool",
         "hint": "No = use number in filename.  Yes = count files 1, 2, 3…"},
    ]
    settings = review_settings(settings)
    show   = get(settings, "show")
    season = get(settings, "season")
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
    header("Mode 3 — Simple Numbered Files")
    settings = [
        {"key": "show",    "label": "Show name",   "value": show,   "kind": "str"},
        {"key": "season",  "label": "Season number","value": season, "kind": "int"},
        {"key": "use_seq", "label": "Sequential episode numbering", "value": False, "kind": "bool",
         "hint": "No = use number found in filename.  Yes = count files 1, 2, 3…"},
        {"key": "offset",  "label": "Episode number offset",        "value": 0,     "kind": "int",
         "hint": "Added to every episode number.  0 = none.  12 = start at E13."},
    ]
    settings = review_settings(settings)
    show   = get(settings, "show")
    season = get(settings, "season")
    def build(f, i):
        d  = parse_simple(f.name)
        ep = (i if get(settings, "use_seq") else (d["ep"] or i)) + get(settings, "offset")
        return f"{show} - {ep_str(season, ep)}{f.suffix.lower()}"
    return build


def flow_sxxexx(files, show, season, folder):
    header("Mode 4 — Normalize existing S##E## files")
    settings = [
        {"key": "show",     "label": "Show name",  "value": show,   "kind": "str"},
        {"key": "season",   "label": "Season number (if not read from file)",
                                                    "value": season, "kind": "int"},
        {"key": "keep_ep",  "label": "Keep season+episode from filename", "value": True, "kind": "bool",
         "hint": "Yes = read S01E04 out of each filename.\n"
                 "  No  = use the season number above for every file."},
        {"key": "quality",  "label": "Add quality tag if found",  "value": False, "kind": "bool"},
    ]
    settings = review_settings(settings)
    show   = get(settings, "show")
    season = get(settings, "season")
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
    header("Mode 5 — Raw Regex")
    print(f"""
  {DIM}Write a Python regex with NAMED capture groups.

  Required:  {BOLD}(?P<ep>\\d+){R}{DIM}           — episode number
  Optional:  {BOLD}(?P<season>\\d+){R}{DIM}       — season number
             {BOLD}(?P<show>.+?){R}{DIM}          — show name
             {BOLD}(?P<title>.+?){R}{DIM}         — episode title
             {BOLD}(?P<quality>\\d{{3,4}}p){R}{DIM}  — quality tag

  Examples:
    Full:  {BOLD}^(?P<show>.+?) - Episode (?P<ep>\\d+) - (?P<title>.+?)\\s+(?P<quality>\\d{{3,4}}p){R}
    Short: {BOLD}(?P<ep>\\d{{1,3}}){R}
    SxxEx: {BOLD}S(?P<season>\\d+)E(?P<ep>\\d+){R}

  Type 'b' to go back.{R}
""")
    while True:
        try:
            pattern = ask("Regex", back=True)
        except Back:
            raise
        if not pattern:
            continue
        try:
            rx = re.compile(pattern, re.IGNORECASE)
            if "ep" not in rx.groupindex:
                err("Pattern must contain (?P<ep>…)")
                continue
            break
        except re.error as e:
            err(f"Invalid regex: {e}")

    has_title = "title" in rx.groupindex
    settings = [
        {"key": "show",    "label": "Show name",    "value": show,   "kind": "str"},
        {"key": "season",  "label": "Season number","value": season, "kind": "int"},
        {"key": "quality", "label": "Add quality tag if found", "value": False, "kind": "bool"},
    ]
    if has_title:
        settings.insert(2, {"key": "title", "label": "Include matched title",
                             "value": True, "kind": "bool"})
    settings = review_settings(settings)
    show   = get(settings, "show")
    season = get(settings, "season")

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

PATTERNS_FILE = Path.home() / ".config" / "rename_media" / "patterns.json"

# ── Block catalogue ───────────────────────────────────────────────────────────

CAPTURE_BLOCKS = {
    "show":    {
        "label":   "Show / series name",
        "regex":   r"(?P<show>.+?)",
        "example": ("Horimiya - Episode 01 - Title 1080p",
                    "show", "Horimiya"),
    },
    "ep":      {
        "label":   "Episode number",
        "regex":   r"(?P<ep>\d+)",
        "example": ("Horimiya - Episode 01 - Title 1080p",
                    "ep", "01"),
    },
    "season":  {
        "label":   "Season number",
        "regex":   r"(?P<season>\d{1,2})",
        "example": ("Show S02E05 Title",
                    "season", "02"),
    },
    "title":   {
        "label":   "Episode title",
        "regex":   r"(?P<title>.+?)",
        "example": ("Horimiya - Episode 01 - A Tiny Happenstance 1080p",
                    "title", "A Tiny Happenstance"),
    },
    "quality": {
        "label":   "Quality / resolution tag",
        "regex":   r"(?P<quality>\d{3,4}p)",
        "example": ("Horimiya - Episode 01 - Title 1080p BDRip",
                    "quality", "1080p"),
    },
}

SEPARATOR_BLOCKS = {
    "sep":     {
        "label":   '" - "  (space-dash-space)',
        "regex":   r"\s*-\s*",
        "example": ("Show - Episode 01", "the dash between parts"),
    },
    "word_ep": {
        "label":   '"Episode" or "Ep."  keyword',
        "regex":   r"(?:Episode|Ep\.?)\s+",
        "example": ("Show - Episode 01 - Title", "the word 'Episode'"),
    },
    "space":   {
        "label":   "One or more spaces",
        "regex":   r"\s+",
        "example": ("Show 01 Title", "gap between words"),
    },
    "dot":     {
        "label":   'Literal dot  "."',
        "regex":   r"\.",
        "example": ("Show.01.Title", "the dots"),
    },
    "bracket_open":  {
        "label":   'Opening bracket  "["',
        "regex":   r"\[",
        "example": ("[DB] Show - 01", "the opening bracket"),
    },
    "bracket_close": {
        "label":   'Closing bracket  "]"',
        "regex":   r"\]",
        "example": ("[DB] Show - 01", "the closing bracket"),
    },
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
                # something concrete follows — lazy is safe, \s+ anchors the handoff
                rx = r"(?P<title>.+?)\s+"
            elif i == len(sequence) - 1:
                # title is the LAST block in the sequence.
                # Stop before a bracket-tag run ([CR][1080p]...) or a trailing
                # " - Group" suffix if one is present; otherwise be greedy and
                # take everything to the end. A lazy .+? here would only ever
                # match the bare minimum (often one word) because nothing
                # forces it to extend.
                rx = r"(?P<title>.+?)(?=\s*\[|\s*-\s*\w+$|$)"
            else:
                # another named block follows directly — keep lazy so that
                # block's own pattern determines where title stops
                rx = r"(?P<title>.+?)"

        parts.append(rx)

    # Trailing catch-all for anything after the last meaningful block
    # (release tags, group names, hashes, etc.) — always optional.
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
    blank()
    print(f"  {BOLD}Pattern test:{R}")
    any_fail = False
    for r in results:
        if r["match"]:
            gd    = r["match"]
            parts = [f"{DIM}{k}{R}={CYAN}{gd[k]}{R}"
                     for k in ("show","season","ep","title","quality")
                     if k in gd and gd[k]]
            print(f"  {GREEN}✓{R}  {DIM}{r['file']}{R}")
            print(f"     {' '.join(parts)}")
        else:
            print(f"  {RED}✗{R}  No match: {DIM}{r['file']}{R}")
            any_fail = True
    if any_fail:
        blank()
        warn("Some files didn't match — you may need to adjust the pattern.")


# ── Save / Load ───────────────────────────────────────────────────────────────

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
    success(f"Saved as \"{name}\"  →  {PATTERNS_FILE}")


def _pick_saved_pattern() -> dict | None:
    saved = _load_saved_patterns()
    if not saved:
        warn("No saved patterns yet.")
        return None
    names = list(saved.keys())
    blank()
    print(f"  {BOLD}Saved patterns:{R}\n")
    for i, name in enumerate(names, 1):
        p = saved[name]
        print(f"    {CYAN}{i}{R}  {BOLD}{name}{R}")
        print(f"       {DIM}Output: {p.get('output_fmt','?')}{R}")
        blank()
    while True:
        raw = input(f"  {BOLD}Pick a number{R} (or 'b' to go back): ").strip()
        _check_back(raw)
        if raw.isdigit() and 1 <= int(raw) <= len(names):
            return saved[names[int(raw) - 1]]
        err(f"Enter 1–{len(names)} or 'b'.")


# ── Output format ─────────────────────────────────────────────────────────────

OUTPUT_TOKENS = {
    "{show}":    "Show name",
    "{SE}":      "Season+episode  → S01E01",
    "{S}":       "Season only     → S01",
    "{E}":       "Episode only    → E01",
    "{title}":   "Episode title   (if captured)",
    "{quality}": "Quality tag     (if captured, e.g. 1080p)",
}

def _build_output_fmt(captured_groups: set[str]) -> str:
    blank()
    print(f"  {BOLD}── Output filename format ──{R}\n")
    print(f"  Available tokens:\n")
    for tok, desc in OUTPUT_TOKENS.items():
        grp   = tok.strip("{}")
        avail = f"  {YELLOW}← not captured, will be blank{R}" \
                if grp in ("show","title","quality") and grp not in captured_groups else ""
        print(f"    {CYAN}{tok:<12}{R}  {DIM}{desc}{R}{avail}")
    blank()

    suggestion = "{show} - {SE}"
    if "title"   in captured_groups: suggestion += " - {title}"
    if "quality" in captured_groups: suggestion += " ({quality})"

    fmt = ask("Output format", default=suggestion)
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


# ── Builder step machine ──────────────────────────────────────────────────────

def _builder_step_sample(files: list[Path]) -> str:
    """Step 1: get a sample filename."""
    step_header(1, 5, "Sample filename")
    blank()
    if files:
        print(f"  {DIM}First file detected:{R}")
        print(f"  {CYAN}{files[0].name}{R}")
    blank()
    print(f"  Paste one of your filenames so we can test the pattern as we build it.")
    print(f"  Press Enter to use the first file above.")
    blank()
    sample = ask("Sample filename",
                 default=files[0].name if files else "",
                 back=False)   # step 1 — nowhere to go back to
    return sample.strip("'\"") or (files[0].name if files else "")


def _annotate_sample(sample: str) -> None:
    """
    Print the sample filename stem with colour-coded spans showing which
    blocks a naive left-to-right scan would identify, followed by a legend.
    This is purely illustrative — it doesn't constrain what the user picks.
    """
    stem = Path(sample).stem

    # Ordered probes: try to find each block's span in the stem sequentially.
    # We walk left-to-right and mark spans greedily so the display is clean.
    BLOCK_COLORS = {
        "show":          "\033[95m",   # magenta
        "sep":           "\033[2m",    # dim
        "word_ep":       "\033[33m",   # yellow
        "ep":            "\033[96m",   # cyan
        "title":         "\033[92m",   # green
        "quality":       "\033[91m",   # red
        "season":        "\033[94m",   # blue
        "dot":           "\033[2m",
        "space":         "\033[2m",
        "bracket_open":  "\033[2m",
        "bracket_close": "\033[2m",
    }

    # Build a list of (start, end, block_key) spans by scanning left to right
    # with a simple greedy approach using each block's regex.
    PROBE_ORDER = [
        # Try to identify parts of a typical "Show - Episode NN - Title Quality" line
        ("show",      r"^(.+?)(?=\s*-\s*(?:Episode|Ep\.?)\s+\d|\s*-\s*\d|\.\d)"),
        ("sep",       r"\s*-\s*"),
        ("word_ep",   r"(?:Episode|Ep\.?)\s+"),
        ("ep",        r"\d{1,4}"),
        ("sep",       r"\s*-\s*"),
        # title stops before quality tag; falls back to end-of-string
        ("title",     r".+?(?=\s+\d{3,4}p\b)"),
        ("space",     r"\s+"),
        ("quality",   r"\d{3,4}p"),
        ("season",    r"(?<=[Ss])\d{1,2}(?=[Ee])"),
        ("dot",       r"\."),
    ]

    spans: list[tuple[int, int, str]] = []
    pos = 0
    used: set[str] = set()   # prevent duplicate keys cluttering the display

    for key, pat in PROBE_ORDER:
        if pos >= len(stem):
            break
        try:
            m = re.match(pat, stem[pos:], re.IGNORECASE)
            if m and m.group(0):
                start = pos
                end   = pos + len(m.group(0))
                # Only record the first match per key for the legend
                spans.append((start, end, key if key not in used else key + "_2"))
                used.add(key)
                pos = end
        except re.error:
            pass

    # Build the coloured string
    coloured = ""
    cursor   = 0
    for start, end, key in sorted(spans, key=lambda x: x[0]):
        base_key = key.rstrip("_2")
        col = BLOCK_COLORS.get(base_key, "")
        # unmatched gap
        if cursor < start:
            coloured += f"{DIM}{stem[cursor:start]}{R}"
        coloured += f"{col}{BOLD}{stem[start:end]}{R}"
        cursor = end
    # remainder
    if cursor < len(stem):
        coloured += f"{DIM}{stem[cursor:]}{R}"

    blank()
    print(f"  {BOLD}Your sample filename:{R}")
    print(f"  {coloured}")
    blank()

    # Legend — only show keys that actually appeared
    seen_keys = [key.rstrip("_2") for _, _, key in spans]
    legend_keys = list(dict.fromkeys(seen_keys))   # deduplicated, ordered
    if legend_keys:
        print(f"  {DIM}Colour guide:{R}")
        for key in legend_keys:
            col   = BLOCK_COLORS.get(key, "")
            label = CAPTURE_BLOCKS[key]["label"] if key in CAPTURE_BLOCKS \
                    else SEPARATOR_BLOCKS.get(key, {}).get("label", key)
            print(f"    {col}{BOLD}{key}{R}  {DIM}{label}{R}")
    blank()
    sep_line()


def _builder_step_blocks(sample: str) -> list[dict]:
    """Step 2: pick blocks by number, building the sequence interactively."""
    step_header(2, 5, "Pick blocks")

    _annotate_sample(sample)

    cap_keys = list(CAPTURE_BLOCKS.keys())
    sep_keys = list(SEPARATOR_BLOCKS.keys())

    # Print capture blocks with example of what each captures
    print(f"  {BOLD}Capture blocks{R}  — extract a value from the filename:\n")
    for i, k in enumerate(cap_keys, 1):
        b    = CAPTURE_BLOCKS[k]
        ex_f, ex_grp, ex_val = b["example"]
        print(f"  {CYAN}{i:2}{R}  {BOLD}{k:<10}{R}  {b['label']}")
        print(f"       {DIM}regex: {b['regex']}{R}")
        print(f"       {DIM}e.g. in \"{ex_f}\"{R}")
        print(f"       {DIM}captures {ex_grp} → {GREEN}{ex_val}{R}{DIM}{R}")
        blank()

    print(f"  {BOLD}Separator / structure blocks{R}  — match punctuation, no value saved:\n")
    for i, k in enumerate(sep_keys, 1):
        b    = SEPARATOR_BLOCKS[k]
        ex_f, ex_desc = b["example"]
        print(f"  {CYAN}{i+len(cap_keys):2}{R}  {BOLD}{k:<14}{R}  {b['label']}")
        print(f"       {DIM}regex: {b['regex']}{R}")
        print(f"       {DIM}matches {ex_desc} in \"{ex_f}\"{R}")
        blank()

    cust_num = len(cap_keys) + len(sep_keys) + 1
    print(f"  {CYAN}{cust_num:2}{R}  {BOLD}custom{R}          Any literal text you type")
    blank()

    print(f"  {DIM}Your sample:{R}  {CYAN}{sample}{R}")
    blank()

    # Interactive add-one-at-a-time sequence builder
    print(f"  Build your sequence one block at a time.")
    print(f"  Type a block {BOLD}number{R} or {BOLD}name{R} to add it.")
    print(f"  Type {BOLD}done{R} when finished, {BOLD}undo{R} to remove the last block, {BOLD}b{R} to go back.")
    blank()

    all_by_num = cap_keys + sep_keys + ["custom"]
    sequence   = []

    while True:
        # Show current sequence and a live regex test
        if sequence:
            seq_str = "  →  ".join(
                f"{CYAN}{s['key']}{R}" + (f"({DIM}{s.get('custom','')}{R})" if s['key']=='custom' else "")
                for s in sequence
            )
            print(f"\n  {BOLD}Current sequence:{R}  {seq_str}")

            # Live test
            try:
                pat = _assemble_regex(sequence)
                rx  = re.compile(pat, re.IGNORECASE)
                m   = rx.match(Path(sample).stem)
                if m:
                    gd    = m.groupdict()
                    parts = [f"{DIM}{k}{R}={GREEN}{v}{R}"
                             for k, v in gd.items() if v]
                    print(f"  {GREEN}✓  match:{R}  {' '.join(parts)}")
                else:
                    print(f"  {YELLOW}✗  no match yet{R}")
            except Exception:
                print(f"  {DIM}(pattern incomplete){R}")
        else:
            print(f"\n  {DIM}Sequence is empty — add blocks below.{R}")

        blank()
        raw = input(f"  Add block (number/name/done/undo/b): ").strip()
        low = raw.lower()

        if low in ("b", "back"):
            raise Back()

        if low == "done":
            if not sequence:
                err("Add at least one block first.")
                continue
            if not any(s["key"] == "ep" for s in sequence):
                err("Sequence must include the 'ep' block — it's how episode numbers are found.")
                continue
            return sequence

        if low == "undo":
            if sequence:
                removed = sequence.pop()
                info(f"Removed: {removed['key']}")
            else:
                warn("Nothing to undo.")
            continue

        # Look up by number or name
        key = None
        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(all_by_num):
                key = all_by_num[idx]
            else:
                err(f"Enter a number 1–{len(all_by_num)}, a block name, 'done', 'undo', or 'b'.")
                continue
        elif low in ALL_BLOCK_KEYS:
            key = low
        else:
            err(f"Unknown block '{raw}'. Valid: {', '.join(ALL_BLOCK_KEYS)}")
            continue

        if key == "custom":
            txt = input(f"  Custom literal text: ").strip()
            if txt:
                sequence.append({"key": "custom", "custom": txt})
                info(f"Added: custom(\"{txt}\")")
        else:
            sequence.append({"key": key})
            info(f"Added: {key}")


def _builder_step_test(pattern: str, sample: str, files: list[Path]) -> str:
    """Step 3: show the assembled regex, test it, optionally edit."""
    step_header(3, 5, "Test pattern")
    blank()
    print(f"  {BOLD}Generated regex:{R}")
    print(f"  {CYAN}{pattern}{R}")

    test_targets = list(files[:5])
    sample_path  = Path(sample)
    if sample and sample not in [f.name for f in test_targets]:
        test_targets = [sample_path] + test_targets[:4]

    _show_test_results(_test_pattern(pattern, test_targets))

    blank()
    print(f"  {BOLD}Options:{R}")
    print(f"    {CYAN}1{R}  Continue with this pattern")
    print(f"    {CYAN}2{R}  Edit the regex manually")
    print(f"    {CYAN}b{R}  Back to block picker")
    blank()

    while True:
        raw = input(f"  Choice: ").strip().lower()
        _check_back(raw)

        if raw == "1":
            return pattern

        if raw == "2":
            while True:
                blank()
                new_pat = ask("Regex", default=pattern)
                try:
                    re.compile(new_pat, re.IGNORECASE)
                    _show_test_results(_test_pattern(new_pat, test_targets))
                    blank()
                    if ask_yn("Use this pattern?", default_yes=True):
                        return new_pat
                    # else loop and let them edit again
                except re.error as e:
                    err(f"Invalid regex: {e}")

        err("Enter 1, 2, or 'b'.")


def _builder_step_output(captured_groups: set[str], season: int,
                          pattern: str, files: list[Path]) -> str:
    """Step 4: define the output filename format."""
    step_header(4, 5, "Output format")

    fmt = _build_output_fmt(captured_groups)

    # Preview
    blank()
    print(f"  {BOLD}Preview:{R}")
    rx    = re.compile(pattern, re.IGNORECASE)
    shown = 0
    for f in files[:3]:
        m = rx.match(f.stem)
        if m:
            out = _apply_output_fmt(fmt, m.groupdict(), season) + f.suffix.lower()
            print(f"  {DIM}{f.name}{R}")
            print(f"    {GREEN}→ {out}{R}")
            shown += 1
    if not shown:
        warn("Pattern matched no files — check your blocks in the previous step.")

    blank()
    print(f"    {CYAN}1{R}  Use this format")
    print(f"    {CYAN}2{R}  Edit the format")
    print(f"    {CYAN}b{R}  Back to pattern test")
    blank()

    while True:
        raw = input(f"  Choice: ").strip().lower()
        _check_back(raw)
        if raw == "1":
            return fmt
        if raw == "2":
            try:
                fmt = _build_output_fmt(captured_groups)
                # re-show preview
                blank()
                print(f"  {BOLD}Preview:{R}")
                for f in files[:3]:
                    m = rx.match(f.stem)
                    if m:
                        out = _apply_output_fmt(fmt, m.groupdict(), season) + f.suffix.lower()
                        print(f"  {DIM}{f.name}{R}")
                        print(f"    {GREEN}→ {out}{R}")
            except Back:
                pass   # stay on the options
        else:
            err("Enter 1, 2, or 'b'.")


def _builder_step_save(pattern: str, output_fmt: str,
                        sequence: list[dict], show: str) -> None:
    """Step 5: optionally save the pattern."""
    step_header(5, 5, "Save pattern")
    blank()
    print(f"  Save this pattern so you can load it next time without rebuilding.")
    blank()
    try:
        if ask_yn("Save for future use?", default_yes=True):
            pname = ask("Pattern name",
                        hint="A short label — e.g. 'horimiya-style' or 'generic-ep-title'",
                        default=show or "my-pattern")
            _save_pattern(pname, {
                "pattern":    pattern,
                "output_fmt": output_fmt,
                "sequence":   [s["key"] for s in sequence],
            })
    except Back:
        pass   # skipping save is fine


def flow_regex_builder(files: list[Path], show: str, season: int, folder: Path):
    header("Mode 6 — Regex Builder")

    # ── Entry: new or load ────────────────────────────────────────────────────
    blank()
    print(f"  {BOLD}Start from:{R}")
    print(f"    {CYAN}1{R}  Build a new pattern step-by-step")
    print(f"    {CYAN}2{R}  Load a saved pattern")
    blank()

    pattern    = ""
    output_fmt = ""
    sequence   = []

    while True:
        start = input(f"  {BOLD}Choice{R}: ").strip()
        if start == "2":
            try:
                saved = _pick_saved_pattern()
            except Back:
                continue
            if saved:
                pattern    = saved.get("pattern", "")
                output_fmt = saved.get("output_fmt", "")
                blank()
                info(f"Output format: {DIM}{output_fmt}{R}")
                _show_test_results(_test_pattern(pattern, files))
                break
            # no saved patterns — fall through to builder
        if start in ("1", "2"):
            break
        err("Enter 1 or 2.")

    # ── Step machine (new build path) ─────────────────────────────────────────
    if not pattern:
        step = 1
        sample = ""
        while step <= 5:
            try:
                if step == 1:
                    sample   = _builder_step_sample(files)
                    step = 2

                elif step == 2:
                    sequence = _builder_step_blocks(sample)
                    pattern  = _assemble_regex(sequence)
                    step = 3

                elif step == 3:
                    pattern  = _builder_step_test(pattern, sample, files)
                    step = 4

                elif step == 4:
                    try:
                        rx_tmp = re.compile(pattern, re.IGNORECASE)
                        captured_grps = set(rx_tmp.groupindex.keys())
                    except Exception:
                        captured_grps = set()
                    output_fmt = _builder_step_output(captured_grps, season, pattern, files)
                    step = 5

                elif step == 5:
                    _builder_step_save(pattern, output_fmt, sequence, show)
                    step = 6   # done

            except Back:
                step = max(1, step - 1)

    # ── Final settings review before rename ───────────────────────────────────
    try:
        rx_final = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        err(f"Pattern is invalid: {e}")
        return None

    captured_grps = set(rx_final.groupindex.keys())
    settings = [
        {"key": "show",   "label": "Show name (overrides captured value)",
         "value": show,   "kind": "str",
         "hint": "Leave as-is to use whatever the pattern captures, or type a fixed name."},
        {"key": "season", "label": "Season number",
         "value": season, "kind": "int"},
        {"key": "output", "label": "Output format",
         "value": output_fmt, "kind": "str",
         "hint": "Tokens: {show} {SE} {S} {E} {title} {quality}"},
    ]
    settings = review_settings(settings)

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
    header("Preview — files in folder")
    files = list_media(folder)
    if not files:
        warn("No media files found.")
        return
    for i, f in enumerate(files, 1):
        print(f"  {DIM}{i:3d}.{R}  {f.name}")
    blank()
    info(f"{len(files)} media file(s) found.")


def util_split(folder: Path):
    header("Utility — Split into Season subfolders")
    files = list_media(folder)
    if not files:
        warn("No media files found.")
        return

    preview: dict[Path, list[Path]] = {}
    for f in files:
        m  = re.search(r'[Ss](\d{1,2})[Ee]\d+', f.name)
        sn = int(m.group(1)) if m else 0
        preview.setdefault(folder / f"Season {sn:02d}", []).append(f)

    blank()
    print(f"  {BOLD}Files will be moved into:{R}")
    for sub, flist in sorted(preview.items()):
        print(f"    {CYAN}{sub.name}/{R}  —  {len(flist)} file(s)")
    blank()
    if not ask_yn("Proceed with moving?", back=False):
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
    header("Utility — Rename show name across existing files")
    files = list_media(folder)
    if not files:
        warn("No media files found.")
        return

    first     = files[0].stem
    detected  = ""
    sep_match = re.search(r'\s+-\s+S\d{2}E\d+', first)
    if sep_match:
        detected = first[:sep_match.start()].strip()

    section("Current show name")
    if detected:
        print(f"  {DIM}Detected: \"{detected}\"{R}")
    old_name = ask("Current show name to replace",
                   hint="The text at the start of each filename, before  - S01E01",
                   default=detected, back=False)
    if not old_name:
        err("No name entered.")
        return

    section("New show name")
    new_name = ask("New show name",
                   hint=f"Every file starting with \"{old_name}\" will have it replaced.",
                   back=False)
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
        info("Cancelled — no files changed.")
        return

    ok = 0
    for src, dst in pairs:
        if dst.exists() and dst != src:
            warn(f"SKIP — target exists: {dst.name}")
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
    "1": flow_fansub,
    "2": flow_one_pace,
    "3": flow_simple,
    "4": flow_sxxexx,
    "5": flow_custom_regex,
    "6": flow_regex_builder,
}

MODE_LABELS = {
    "1": ("Standard Fansub",        "[DB]Show_-_01_(info).mkv"),
    "2": ("One Pace / Group+Range",  "[Group][841-842] Arc 10 [720p].mp4"),
    "3": ("Simple Numbered",         "01.mkv  /  Episode 05.mkv"),
    "4": ("Normalize S##E## files",  "old.show.S01E04.1080p.mkv"),
    "5": ("Raw Regex",               "type your own pattern directly"),
    "6": ("Regex Builder",           "Show - Episode 01 - Title 1080p.mkv  ← guided step-by-step"),
}


def main():
    print(f"""\
{BOLD}{CYAN}
  ╔══════════════════════════════════════════╗
  ║   Media Batch Renamer  ·  Linux Edition  ║
  ╚══════════════════════════════════════════╝{R}
  Rename anime / TV episode files to clean, consistent names.
  Type {BOLD}b{R} at any prompt to go back to the previous step.
""")

    while True:
        header("Main Menu")
        print(f"  {BOLD}Rename modes{R}  — pick the one that matches your files:\n")
        for k, (label, example) in MODE_LABELS.items():
            print(f"    {CYAN}{k}{R}  {BOLD}{label}{R}")
            print(f"       {DIM}{example}{R}\n")

        print(f"  {BOLD}Utilities{R}\n")
        print(f"    {CYAN}7{R}  Preview files in a folder")
        print(f"    {CYAN}8{R}  Split files into Season XX/ subfolders")
        print(f"    {CYAN}9{R}  Rename show name across existing files")
        print(f"    {CYAN}q{R}  Quit\n")

        choice = input(f"  {BOLD}Choice{R}: ").strip().lower()

        if choice == "q":
            print(f"\n  {DIM}Bye!{R}\n")
            break

        if choice == "7":
            util_preview(pick_folder())
            continue
        if choice == "8":
            util_split(pick_folder())
            continue
        if choice == "9":
            util_rename_show(pick_folder())
            continue

        if choice not in FLOW_BUILDERS:
            err("Please enter 1–9 or q.")
            continue

        # ── Shared setup ─────────────────────────────────────────────────────
        label, example = MODE_LABELS[choice]
        blank()
        print(f"  {BOLD}Mode:{R} {label}")
        print(f"  {DIM}Matches files like:  {example}{R}")

        folder = pick_folder()
        files  = list_media(folder)

        if not files:
            warn("No media files found in that folder.")
            continue

        blank()
        info(f"Found {BOLD}{len(files)}{R} media file(s) in:  {DIM}{folder}{R}")
        blank()

        # Pre-fill show name from filename if possible
        detected_show = ""
        if choice == "1":
            detected_show = parse_fansub(files[0].name).get("show_guess", "")

        section("Show name")
        try:
            show = ask("Show name",
                       hint="This becomes the start of every renamed file.",
                       default=detected_show)
        except Back:
            continue
        if not show:
            err("Show name cannot be empty.")
            continue

        section("Season number")
        try:
            raw_s = ask("Season", hint="Used to build S01E01, S02E03 etc.", default="1")
        except Back:
            continue
        season = int(raw_s) if raw_s.isdigit() else 1

        # ── Build rename function ─────────────────────────────────────────────
        build_fn = None
        while build_fn is None:
            try:
                build_fn = FLOW_BUILDERS[choice](files, show, season, folder)
            except Back:
                # Back from the very first settings screen → re-ask show/season
                section("Show name")
                try:
                    show = ask("Show name", default=show)
                except Back:
                    break
                section("Season number")
                try:
                    raw_s = ask("Season", default=str(season))
                    season = int(raw_s) if raw_s.isdigit() else season
                except Back:
                    pass
        if build_fn is None:
            continue

        # ── Dry run / confirm loop ────────────────────────────────────────────
        while True:
            blank()
            print(f"  {BG_YEL}{BLK} DRY RUN {R}  {BOLD}Preview — no files will be changed{R}")
            blank()
            run_rename(files, folder, dry_run=True, build_fn=build_fn)

            blank()
            print(f"  {BOLD}What would you like to do?{R}")
            print(f"    {CYAN}1{R}  Apply these renames  {DIM}(make it real){R}")
            print(f"    {CYAN}2{R}  Change settings      {DIM}(go back and adjust){R}")
            print(f"    {CYAN}3{R}  Cancel               {DIM}(back to main menu){R}")
            blank()
            action = input(f"  Choice: ").strip()

            if action == "1":
                files = list_media(folder)
                blank()
                print(f"  {BOLD}── Renaming ──{R}")
                blank()
                run_rename(files, folder, dry_run=False, build_fn=build_fn)
                break

            elif action == "2":
                try:
                    build_fn = FLOW_BUILDERS[choice](files, show, season, folder)
                except Back:
                    pass   # stay in the dry-run loop with old build_fn

            elif action == "3":
                info("Cancelled — no files changed.")
                break
            else:
                err("Enter 1, 2, or 3.")

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