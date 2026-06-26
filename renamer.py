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


def _browse_subfolder(base: Path, title: str = "Choose folder") -> Path | None:
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

        render(title=title, context_lines=[f"📁 {DIM}{base}{R}"])

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

        render(title=title,
               context_lines=[f"📁 {DIM}{base}{R}", f"🔎 matches for \"{raw}\""])
        for i, d in enumerate(matches, 1):
            print(f"  {CYAN}{i}{R}  {d.name}")
        blank()
        pick = input(f"  {BOLD}Number{R} (Enter=cancel): ").strip()
        if pick.isdigit() and 1 <= int(pick) <= len(matches):
            base = matches[int(pick) - 1]


# If a path was passed on the command line, it pre-fills the very first
# folder pick in the session — after that this is cleared so later picks
# behave normally. Set by main() at startup from sys.argv.
_CLI_INITIAL_PATH: Path | None = None


def pick_folder(title: str = "Choose folder", sub: str = "") -> Path:
    global _CLI_INITIAL_PATH

    if _CLI_INITIAL_PATH is not None:
        preset = _CLI_INITIAL_PATH
        _CLI_INITIAL_PATH = None   # only ever offered once
        render(title=title, sub=sub,
               context_lines=[f"From command line: {DIM}{preset}{R}"])
        print(f"  Use this folder?")
        blank()
        if ask_yn("Use this folder", default_yes=True, back=False):
            return preset
        # fall through to the normal picker if declined

    locations = _find_all_locations()
    gvfs_labels = {"SMB", "FTP", "MTP", "SFTP", "DAV"}
    local_locs = [(l, p) for l, p in locations if not any(t in l for t in gvfs_labels)]
    net_locs   = [(l, p) for l, p in locations if any(t in l for t in gvfs_labels)]
    all_locs   = local_locs + net_locs
    n_local    = len(local_locs)

    while True:
        render(title=title, sub=sub)

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
                result = _browse_subfolder(all_locs[idx][1], title=title)
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
        m = re.match(r'^(\d+)(?:[vV]\d+)?$', part)
        if m:
            ep_num = int(m.group(1))
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


def _confirm_show_name(files: list[Path], folder: Path, build_fn) -> bool:
    """
    Final safety gate before any real rename happens. Shows the show name
    that will actually be used (taken from a live preview of the first
    file's new name, not just whatever was typed earlier — some modes let
    the show name be overridden deep in their own settings) and requires
    the user to type it back exactly before proceeding. Returns True only
    on an exact match; any mismatch or blank input cancels.
    """
    if not files:
        return False

    preview_name = build_fn(files[0], 1)
    if preview_name is None:
        # fall back to trying a few more files in case the first one
        # legitimately doesn't match the pattern
        for f in files[1:5]:
            preview_name = build_fn(f, 1)
            if preview_name is not None:
                break

    detected_show = ""
    if preview_name:
        # the show name is everything before the first " - " separator
        # in the standard output convention used across all modes
        m = re.match(r'^(.+?)\s+-\s+', preview_name)
        if m:
            detected_show = m.group(1)

    render(title="Final confirmation",
           context_lines=[f"About to rename {BOLD}{len(files)}{R} file(s) in:",
                           f"{DIM}{folder}{R}"])

    if detected_show:
        print(f"  The show name in the new filenames will be:")
        blank()
        print(f"    {BOLD}{CYAN}{detected_show}{R}")
        blank()
        print(f"  {DIM}Type it exactly as shown above to confirm and rename for real.{R}")
        print(f"  {DIM}Anything else cancels — no files will be changed.{R}")
    else:
        warn("Could not detect a show name from the preview.")
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
    "se":      {"label": "Season+Episode combined", "regex": r"[Ss](?P<season>\d{1,2})[Ee](?P<ep>\d{1,3})(?:[vV]\d+)?",
                "example": ("Show.S01E07.Title.1080p", "season=01, ep=07")},
    "ep":      {"label": "Episode number",   "regex": r"(?P<ep>\d+)(?:[vV]\d+)?",
                "example": ("Horimiya - Episode 01 - Title 1080p", "01")},
    "season":  {"label": "Season number",    "regex": r"(?P<season>\d{1,2})",
                "example": ("Show S02E05 Title", "02")},
    "title":   {"label": "Episode title",    "regex": r"(?P<title>.+?)",
                "example": ("Horimiya - Episode 01 - A Tiny Happenstance 1080p", "A Tiny Happenstance")},
    "quality": {"label": "Quality / resolution", "regex": r"(?P<quality>\d{3,4}p)",
                "example": ("Horimiya - Episode 01 - Title 1080p BDRip", "1080p")},
    "text":    {"label": "Any text (variable length)", "regex": r"(?P<text>.+?)",
                "example": ("Show S01E02 [982497234]", "982497234 (inside brackets)")},
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


FREE_TEXT_BLOCK_KEYS = {"show", "title", "text"}


def _strip_group_names(pattern: str) -> str:
    """
    Convert (?P<name>...) to (?:...) so a block's regex can be reused
    inside a lookahead without redefining the same named group twice.
    Lookaheads only need to match the same shape, not capture anything.
    """
    return re.sub(r"\(\?P<\w+>", "(?:", pattern)


def _assemble_regex(sequence: list[dict]) -> str:
    """
    Any free-text block (show, title, text) becomes a lazy capture that
    stops right before whatever block comes next in the sequence —
    figured out generically from that next block's own regex, not a
    hardcoded list of special cases. This means free-text blocks work
    correctly no matter what follows them: a quality tag, a bracket,
    custom literal text, another capture block, anything.

    If a free-text block is the LAST block in the sequence, it instead
    stops at a sensible default boundary (a bracket tag, a trailing
    "-ReleaseGroup" suffix, or end of string) so it doesn't swallow an
    entire junk tail that was never explicitly labelled.
    """
    parts = []
    for i, item in enumerate(sequence):
        key = item["key"]
        rx  = _block_regex(key, item.get("custom", ""))

        if key in FREE_TEXT_BLOCK_KEYS:
            nxt = sequence[i + 1] if i + 1 < len(sequence) else None
            if nxt is not None:
                nxt_rx = _strip_group_names(_block_regex(nxt["key"], nxt.get("custom", "")))
                rx = f"(?P<{key}>.+?)(?=" + nxt_rx + ")"
            else:
                rx = f"(?P<{key}>.+?)(?=\\s*\\[|\\s*-\\s*\\w+$|$)"

        parts.append(rx)
    return "^" + "".join(parts) + r"(?:.*)?$"


def _clean_groupdict(gd: dict) -> dict:
    """Strip incidental leading/trailing whitespace left over from a
    free-text capture stopping right at a separator boundary."""
    return {k: (v.strip() if isinstance(v, str) else v) for k, v in gd.items()}


def _test_pattern(pattern: str, files: list[Path]) -> list[dict]:
    try:
        rx = re.compile(pattern, re.IGNORECASE)
    except re.error:
        return [{"file": f.name, "match": None} for f in files[:5]]
    results = []
    for f in files[:5]:
        m = rx.match(f.stem)
        results.append({"file": f.name, "match": _clean_groupdict(m.groupdict()) if m else None})
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
BUILT_IN_GROUP_KEYS = {"show", "season", "ep", "title", "quality"}

def _build_output_fmt(captured_groups: set[str], default_suggestion: str = None) -> str:
    custom_keys = sorted(captured_groups - BUILT_IN_GROUP_KEYS)
    shown = "  ".join(f"{CYAN}{t}{R}{DIM}={d}{R}" for t, d in OUTPUT_TOKENS.items())
    if custom_keys:
        shown += "  " + "  ".join(f"{CYAN}{{{k}}}{R}{DIM}=your custom field{R}" for k in custom_keys)
    print(f"  {BOLD}Tokens:{R}  {shown}")
    blank()
    suggestion = default_suggestion or "{show} - {SE}"
    if default_suggestion is None:
        for k in custom_keys:
            suggestion += f" {{{k}}}"
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
    # any other captured key (custom fields) substitutes the same way —
    # e.g. {group} for a custom field named "group"
    for key, value in groups.items():
        if key in ("show", "season", "ep", "title", "quality"):
            continue
        token = "{" + key + "}"
        if token in out:
            out = out.replace(token, str(value).strip())
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
            out = _apply_output_fmt(fmt, _clean_groupdict(m.groupdict()), season) + f.suffix.lower()
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
            print(f"   {CYAN}{i:>2}{R} {MAGENTA}{k:<8}{R} {b['label']:<24}{DIM}e.g. \"{b['example'][1]}\"{R}")
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
                    gd = _clean_groupdict(m.groupdict())
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
            if not any(s["key"] in ("ep", "se") for s in state.sequence):
                err("You need an 'Episode number' or 'Season+Episode' part — it's how files get numbered.")
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
        gd = _clean_groupdict(m.groupdict())
        if final_show:
            gd["show"] = final_show
        return _apply_output_fmt(final_output_fmt, gd, final_season) + f.suffix.lower()

    return build


# ─── Token Splitter Builder (Mode 7) ──────────────────────────────────────────
#
# An alternative to Mode 6 for filenames where fields aren't separated by
# recognisable words/brackets — e.g. dot-bombed release names like
# "Blue.Box.S01E07.Can.I.Have.One.1080p.NF.WEB-DL.DUAL.DDP5.1.H.264-VARYG".
#
# Approach: pick a separator, split the whole filename into indexed tokens,
# then assign each token (or a range) to a key by typing the key then an
# index or index range. Recognisable keys (se, season, ep, quality) are
# re-found by PATTERN when the recipe is applied to other files in the
# batch, so a different title word-count per episode doesn't break it —
# the gap between two recognised anchors becomes the title automatically.

TOKEN_PATTERNS_FILE = Path.home() / ".config" / "rename_media" / "token_patterns.json"

TOKEN_KEYS = {
    "show":    "Show name",
    "se":      "Season+Episode combined (e.g. S01E07)",
    "season":  "Season number only",
    "ep":      "Episode number only",
    "title":   "Episode title",
    "quality": "Quality / resolution",
    "custom":  "Custom field — you name it (e.g. group, codec, audio)",
    "skip":    "Ignore — not used in the new filename",
}
TOKEN_KEY_ORDER = ["show", "se", "season", "ep", "title", "quality", "custom", "skip"]

KEY_COLORS = {
    "show": MAGENTA, "season": BLUE, "ep": CYAN, "se": CYAN,
    "title": GREEN, "quality": RED, "skip": DIM,
}

CUSTOM_KEY_PALETTE = [
    "\033[38;5;208m",  # orange
    "\033[38;5;141m",  # purple
    "\033[38;5;44m",   # teal
    "\033[38;5;220m",  # gold
    "\033[38;5;204m",  # pink
    "\033[38;5;120m",  # light green
]

def _key_color(key: str) -> str:
    """Stable colour for a key name. Built-ins use KEY_COLORS; any other
    name (i.e. a custom field) gets a deterministic colour from a separate
    palette so it never collides with a built-in colour."""
    if key in KEY_COLORS:
        return KEY_COLORS[key]
    idx = sum(ord(c) for c in key) % len(CUSTOM_KEY_PALETTE)
    return CUSTOM_KEY_PALETTE[idx]

ANCHOR_PATTERNS = {
    "se":      re.compile(r"^[Ss]\d{1,2}[Ee]\d{1,3}(?:[vV]\d+)?$"),
    "season":  re.compile(r"^[Ss]\d{1,2}(?:[vV]\d+)?$"),
    "ep":      re.compile(r"^\d{1,4}(?:[vV]\d+)?$"),
    "quality": re.compile(r"^\d{3,4}p$", re.IGNORECASE),
}

# Prefix-only variants (no trailing $) used only to detect an anchor
# that's been fused with extra trailing text inside the same token, e.g.
# 'S01E02-name' when split on spaces — 'name' has no separator from the
# anchor. These are intentionally separate from ANCHOR_PATTERNS above,
# which must stay strict full-token matches for extract_with_recipe's
# anchor search to avoid false positives.
ANCHOR_PREFIX_PATTERNS = {
    "se":      re.compile(r"^[Ss]\d{1,2}[Ee]\d{1,3}(?:[vV]\d+)?"),
    "season":  re.compile(r"^[Ss]\d{1,2}(?:[vV]\d+)?"),
    "ep":      re.compile(r"^\d{1,4}(?:[vV]\d+)?"),
    "quality": re.compile(r"^\d{3,4}p", re.IGNORECASE),
}

# Keys that are NOT free-position text — i.e. found by pattern matching,
# not by gap-filling between other anchors.
FIXED_ANCHOR_KEYS = set(ANCHOR_PATTERNS.keys())


def _split_fused_anchors_list(tokens: list[str], skip: set[str] = None) -> list[str]:
    """
    Plain-text version of the fused-anchor fix-up, shared by SplitState
    (used live while labelling) and extract_with_recipe (used when the
    recipe is replayed against other files in the batch) — so a token
    like 'S01E02-name' always gets split into 'S01E02' + '-name' the
    same way, no matter which file it came from.

    A token that already fully matches a recognised anchor on its own
    (e.g. a clean 'S01E07' token from a dot-separated filename) is left
    completely untouched — splitting is only attempted when NO pattern
    matches the whole token. When a split is needed, the LONGEST partial
    match wins (so 'S01E02-name' splits on the full 'se' pattern, not
    the shorter 'season' prefix 'S01').
    """
    skip = skip or set()
    new_tokens: list[str] = []
    for tok in tokens:
        if tok in skip:
            new_tokens.append(tok)
            continue

        # If the whole token already matches some anchor exactly, it's
        # already clean — never touch it.
        if any(full_pat.match(tok) for full_pat in ANCHOR_PATTERNS.values()):
            new_tokens.append(tok)
            continue

        # Otherwise, find the longest partial-prefix match across all
        # anchor patterns and split there.
        best_end = 0
        for pat in ANCHOR_PREFIX_PATTERNS.values():
            m = pat.match(tok)
            if m and m.end() > best_end:
                best_end = m.end()

        if 0 < best_end < len(tok):
            new_tokens.append(tok[:best_end])
            leftover = tok[best_end:]
            if leftover:
                new_tokens.append(leftover)
        else:
            new_tokens.append(tok)
    return new_tokens


def _is_pure_literal(text: str) -> bool:
    """True if text has no letters or digits — i.e. it's pure punctuation
    like '[', ']', '-', '(', ')'. These can be re-found in another file
    by an exact text match rather than treated as ambiguous free text."""
    return bool(text) and not re.search(r'[a-zA-Z0-9]', text)


def _split_bracket_tokens_list(tokens: list[str], skip: set[str] = None) -> list[str]:
    """
    Plain-text version of the bracket-splitting fix-up — a token that's
    entirely wrapped in [ ] or ( ) becomes three separate tokens (open,
    inner content, close) so the content can be labelled on its own.
    """
    skip = skip or set()
    new_tokens: list[str] = []
    for tok in tokens:
        if tok in skip:
            new_tokens.append(tok)
            continue
        m = re.match(r'^(\[|\()(.+)(\]|\))$', tok)
        if m:
            new_tokens.extend([m.group(1), m.group(2), m.group(3)])
        else:
            new_tokens.append(tok)
    return new_tokens


class Token:
    __slots__ = ("text", "key", "width")
    def __init__(self, text: str, key: str | None = None, width: int = 1):
        self.text  = text
        self.key   = key
        self.width = width   # number of original tokens this merged token represents


class SplitState:
    def __init__(self, stem: str):
        self.stem = stem
        self.tokens: list[Token] = [Token(stem)]
        self.output_fmt: str = ""
        self.separator: str = ""
        self._history: list[list[Token]] = []   # undo stack of token snapshots
        self.resplits: list[dict] = []           # recorded resplit operations, in order

    def _snapshot(self) -> None:
        self._history.append([Token(t.text, t.key, t.width) for t in self.tokens])

    def undo(self) -> bool:
        if not self._history:
            return False
        self.tokens = self._history.pop()
        # also roll back the most recent resplit record if one was made
        # at the same point — best-effort, matches typical usage where
        # undo immediately follows the action it's undoing.
        if self.resplits:
            self.resplits.pop()
        return True

    def split_all(self, sep: str) -> int:
        self._snapshot()
        new_tokens, count = [], 0
        for tok in self.tokens:
            if tok.key is None and sep in tok.text:
                new_tokens.extend(Token(p) for p in tok.text.split(sep))
                count += 1
            else:
                new_tokens.append(tok)
        self.tokens = new_tokens
        self.separator = sep

        # Auto-fix two common fusion problems that would otherwise make
        # season/episode or bracketed content unrecognisable. These run
        # on plain text lists (shared with extract_with_recipe) so the
        # same fix-ups apply identically during interactive labelling
        # AND when the recipe is replayed against other files later.
        assigned_texts = {t.text for t in self.tokens if t.key is not None}
        plain = [t.text for t in self.tokens]
        plain = _split_fused_anchors_list(plain, skip=assigned_texts)
        plain = _split_bracket_tokens_list(plain, skip=assigned_texts)
        self.tokens = [
            Token(text, None) for text in plain
        ] if not assigned_texts else self._rebuild_preserving_keys(plain)
        return count

    def _rebuild_preserving_keys(self, plain_texts: list[str]) -> list[Token]:
        """
        After re-running the auto-split helpers, rebuild the token list,
        keeping the key/width of any token whose text didn't change
        (i.e. it was already labelled and the helpers left it alone).
        """
        old_by_text = {t.text: t for t in self.tokens if t.key is not None}
        rebuilt = []
        for text in plain_texts:
            old = old_by_text.get(text)
            if old is not None:
                rebuilt.append(old)
            else:
                rebuilt.append(Token(text))
        return rebuilt

    def split_token(self, idx: int, sep: str) -> bool:
        tok = self.tokens[idx]
        if sep not in tok.text:
            return False
        self._snapshot()
        pieces = [Token(p) for p in tok.text.split(sep)]
        self.tokens[idx:idx+1] = pieces

        # Record this resplit so it can be replayed on other files in the
        # batch. We scope it to "the last anchor key already assigned
        # at this point" so replay only touches the same relative region
        # of the filename — e.g. resplitting a release-tag token after
        # 'quality' won't also split dashes inside the show name.
        last_anchor_key = None
        for t in self.tokens[:idx]:
            if t.key in FIXED_ANCHOR_KEYS:
                last_anchor_key = t.key
        self.resplits.append({"after_key": last_anchor_key, "sep": sep})
        return True

    def assign(self, start: int, end: int, key: str, joiner: str = " ") -> None:
        self._snapshot()
        merged_text = joiner.join(t.text for t in self.tokens[start:end+1])
        width = end - start + 1
        self.tokens[start:end+1] = [Token(merged_text, key, width)]

    def unassign(self, idx: int) -> None:
        self._snapshot()
        self.tokens[idx].key = None

    def extract(self) -> dict:
        result = {}
        for t in self.tokens:
            if t.key in (None, "skip"):
                continue
            if t.key == "se":
                m = re.match(r'[Ss](\d{1,2})[Ee](\d{1,3})', t.text)
                if m:
                    result["season"] = m.group(1)
                    result["ep"]     = m.group(2)
            else:
                result[t.key] = t.text
        return result

    def column_rows(self, max_width: int = 70) -> list[str]:
        """
        Build a clear 3-row display: index numbers, token text, and a
        [label] tag directly underneath each labelled token — wrapped to
        max_width so it doesn't overflow narrow terminals. Colour is used
        as a secondary cue on top of the explicit [label] text, not the
        only signal.
        """
        cols = []
        for i, t in enumerate(self.tokens):
            idx_str   = str(i)
            label_str = f"[{t.key}]" if t.key else ""
            width     = max(len(idx_str), len(t.text), len(label_str))
            cols.append((idx_str, t.text, label_str, width, t.key))

        # wrap into groups that fit max_width
        groups, current, current_width = [], [], 0
        for c in cols:
            w = c[3] + 2
            if current and current_width + w > max_width:
                groups.append(current)
                current, current_width = [], 0
            current.append(c)
            current_width += w
        if current:
            groups.append(current)

        lines = []
        for g in groups:
            idx_parts, txt_parts, lbl_parts = [], [], []
            any_label = False
            for idx_str, text, label_str, width, key in g:
                idx_parts.append(f"{DIM}{idx_str.ljust(width)}{R}")
                col = _key_color(key) if key else YELLOW
                txt_parts.append(f"{col}{BOLD}{text.ljust(width)}{R}")
                if label_str:
                    any_label = True
                    lbl_parts.append(f"{col}{label_str.ljust(width)}{R}")
                else:
                    lbl_parts.append(" " * width)
            lines.append("  ".join(idx_parts))
            lines.append("  ".join(txt_parts))
            if any_label:
                lines.append("  ".join(lbl_parts))
            lines.append("")  # blank line between wrapped groups
        if lines and lines[-1] == "":
            lines.pop()
        return lines

    def has_required_keys(self) -> bool:
        keys = {t.key for t in self.tokens if t.key}
        return "ep" in keys or "se" in keys

    def summary_line(self) -> str:
        """Compact one-line 'key=value' summary for context headers in later steps."""
        parts = []
        for t in self.tokens:
            if t.key:
                col = _key_color(t.key)
                parts.append(f"{DIM}{t.key}{R}={col}{t.text}{R}")
        return "  ".join(parts) if parts else f"{DIM}(nothing labelled){R}"

    def key_order_assigned(self) -> list[str]:
        """
        Keys in the order the user assigned them — used to build the
        recipe. Every occurrence is kept (not deduplicated): 'skip' in
        particular is commonly used more than once (e.g. for an opening
        AND closing bracket), and each occurrence needs to consume its
        own token slot when the recipe is replayed on other files.
        """
        return [t.key for t in self.tokens if t.key]

    def key_widths(self) -> dict[str, int]:
        """
        How many source tokens each assigned key consumed in the sample.
        Used so a free-text key directly followed by ANOTHER free-text key
        (e.g. a custom 'group' field immediately before 'title') gets a
        fixed width — only the LAST free key in a back-to-back run can
        safely stretch to fill a variable-length gap.
        """
        return {t.key: t.width for t in self.tokens if t.key}

    def literal_anchors(self) -> list[str | None]:
        """
        Parallel list to key_order_assigned(), one entry per occurrence.
        If that occurrence's sample text was pure punctuation (e.g. "[",
        "]", "-", "(") — i.e. it contains no letters or digits — its
        exact text is recorded here so it can be RE-FOUND BY EXACT MATCH
        in other files, the same way se/quality/etc. are found by regex.
        This is what lets a literal bracket correctly bound a variable-
        length free-text field next to it, instead of forcing that field
        into an incorrect fixed width.
        Non-literal occurrences (actual text/numbers) get None here.
        """
        return [
            t.text if (t.key and _is_pure_literal(t.text)) else None
            for t in self.tokens if t.key
        ]


def _load_token_patterns() -> dict:
    if TOKEN_PATTERNS_FILE.exists():
        try:
            return json.loads(TOKEN_PATTERNS_FILE.read_text())
        except Exception:
            pass
    return {}


def _save_token_pattern(name: str, data: dict):
    TOKEN_PATTERNS_FILE.parent.mkdir(parents=True, exist_ok=True)
    saved = _load_token_patterns()
    saved[name] = data
    TOKEN_PATTERNS_FILE.write_text(json.dumps(saved, indent=2))
    success(f"Saved as \"{name}\"")


def _pick_token_pattern() -> dict | None:
    saved = _load_token_patterns()
    if not saved:
        warn("No saved patterns yet.")
        input("  Press Enter to continue...")
        return None
    names = list(saved.keys())
    render(title="Load a saved pattern")
    for i, name in enumerate(names, 1):
        p = saved[name]
        print(f"  {CYAN}{i}{R}  {BOLD}{name}{R}  {DIM}sep=\"{p.get('separator','?')}\"  → {p.get('output_fmt','?')}{R}")
    blank()
    while True:
        raw = input(f"  {BOLD}Number{R} (b=back): ").strip()
        _check_back(raw)
        if raw.isdigit() and 1 <= int(raw) <= len(names):
            return saved[names[int(raw) - 1]]
        err(f"Enter 1–{len(names)} or 'b'.")


def _apply_resplits(tokens: list[str], resplits: list[dict], key_order: list[str]) -> list[str]:
    """
    Replay recorded resplit operations against a fresh token list (from a
    different file in the batch). Each resplit is scoped to only the
    tokens AFTER the anchor key that was already assigned at the moment
    the resplit happened during interactive labelling — so a resplit done
    on a release-tag token near the end of the filename never reaches
    back and breaks apart something earlier, like a show name containing
    the same separator character.
    """
    if not resplits:
        return tokens

    def find_anchors(toks: list[str]) -> dict[str, int]:
        positions: dict[str, int] = {}
        cursor = 0
        for key in key_order:
            if key in FIXED_ANCHOR_KEYS:
                pat = ANCHOR_PATTERNS[key]
                for i in range(cursor, len(toks)):
                    if pat.match(toks[i]):
                        positions[key] = i
                        cursor = i + 1
                        break
        return positions

    for r in resplits:
        after_key = r["after_key"]
        rsep      = r["sep"]
        anchors   = find_anchors(tokens)
        start_idx = (anchors[after_key] + 1) if after_key in anchors else 0

        new_tokens = tokens[:start_idx]
        for t in tokens[start_idx:]:
            if rsep in t:
                new_tokens.extend(t.split(rsep))
            else:
                new_tokens.append(t)
        tokens = new_tokens

    return tokens


def extract_with_recipe(stem: str, separator: str, key_order: list[str],
                         key_widths: dict[str, int] = None,
                         resplits: list[dict] = None,
                         literal_anchors: list[str | None] = None) -> dict:
    """
    Apply a saved separator + key_order recipe to a new filename stem.

    key_order is the FULL list of labelled occurrences in order, e.g.
    ["show", "se", "title", "skip", "custom_id", "skip"] — repeated keys
    (most commonly "skip", used for both an opening and closing bracket)
    are kept as separate entries, not deduplicated, since each one needs
    to consume its own token slot when replayed on another file.

    literal_anchors is a list the same length as key_order. For any
    occurrence whose sample text was pure punctuation (e.g. "[", "]",
    "-"), the corresponding entry holds that exact text — letting it be
    RE-FOUND BY EXACT MATCH in other files, the same way se/quality are
    found by regex. This is what correctly bounds a variable-length
    free-text field sitting next to a literal bracket, without forcing
    that field into an incorrect fixed width.

    key_widths (token-count per key from the sample) is now only used as
    a last-resort fallback for two free-text fields directly adjacent
    with no anchor OR literal between them — a genuinely ambiguous case.

    resplits replays any additional 'resplit' operations performed during
    interactive labelling — e.g. splitting a release-tag token further on
    a different separator — scoped to only the region after the anchor
    that was already assigned at that point.
    """
    key_widths = key_widths or {}
    literal_anchors = literal_anchors or [None] * len(key_order)
    tokens = stem.split(separator)
    # Same auto-fix-ups applied live during labelling — keeps replay on
    # other files in the batch consistent with what the user saw and
    # labelled in the sample (fused anchors split off, bracket contents
    # separated out).
    tokens = _split_fused_anchors_list(tokens)
    tokens = _split_bracket_tokens_list(tokens)
    tokens = _apply_resplits(tokens, resplits or [], key_order)
    n = len(tokens)

    # Pass 1 — locate every occurrence that's findable by a fixed rule:
    # either a regex anchor (se/season/ep/quality) or an exact-text
    # literal anchor (a recorded punctuation token like "[" or "-").
    # Walk key_order in order so repeated anchors of the same kind can't
    # collide with each other, and so a literal's position is always
    # searched for AFTER the previous found anchor, preserving order.
    occurrence_positions: dict[int, int] = {}   # index into key_order -> token index
    search_cursor = 0
    for occ_idx, key in enumerate(key_order):
        lit = literal_anchors[occ_idx] if occ_idx < len(literal_anchors) else None
        if key in FIXED_ANCHOR_KEYS:
            pat = ANCHOR_PATTERNS[key]
            for i in range(search_cursor, n):
                if pat.match(tokens[i]):
                    occurrence_positions[occ_idx] = i
                    search_cursor = i + 1
                    break
        elif lit is not None:
            for i in range(search_cursor, n):
                if tokens[i] == lit:
                    occurrence_positions[occ_idx] = i
                    search_cursor = i + 1
                    break

    # Pass 2 — walk key_order left to right with a cursor. Anchored
    # occurrences (regex or literal) consume exactly one token at their
    # found position. "show" and any free-text key (title, skip without
    # a literal, or a custom name) consume from the cursor up to the
    # next ANCHORED occurrence's position — found anchors always win
    # over ambiguous width-locking, since they're unambiguous by
    # construction. Only when no anchored occurrence follows at all do
    # we fall back to the sample's recorded width.
    result: dict = {}
    cursor = 0
    for occ_idx, key in enumerate(key_order):
        if occ_idx in occurrence_positions:
            idx = occurrence_positions[occ_idx]
            if key == "se":
                m = re.match(r'[Ss](\d{1,2})[Ee](\d{1,3})', tokens[idx])
                if m:
                    result["season"] = m.group(1)
                    result["ep"]     = m.group(2)
            elif key not in ("skip",):
                result[key] = tokens[idx]
            cursor = idx + 1
            continue

        # Not directly anchored — consume up to the next anchored
        # occurrence (by token position), or to the sample width if no
        # later occurrence is anchored at all.
        end = None
        for later_idx in range(occ_idx + 1, len(key_order)):
            if later_idx in occurrence_positions:
                end = occurrence_positions[later_idx]
                break
        if end is None:
            end = min(cursor + key_widths.get(key, n - cursor), n)

        if key == "show":
            if end > cursor:
                result["show"] = re.sub(r'^[\s\-_.]+', '', " ".join(tokens[cursor:end]))
        elif key != "skip" and end > cursor:
            result[key] = re.sub(r'^[\s\-_.]+', '', " ".join(tokens[cursor:end]))

        cursor = max(end, cursor)

    return result


def _preview_token_lines(stem: str, separator: str, key_order: list[str],
                          fmt: str, season: int, files: list[Path], n: int = 3,
                          key_widths: dict[str, int] = None,
                          resplits: list[dict] = None,
                          literal_anchors: list = None) -> list[str]:
    lines = []
    for f in files[:n]:
        groups = extract_with_recipe(f.stem, separator, key_order, key_widths, resplits, literal_anchors)
        if not groups:
            continue
        out = _apply_output_fmt(fmt, groups, season) + f.suffix.lower()
        short = f.name if len(f.name) <= 42 else f.name[:39] + "…"
        lines.append(f"{DIM}{short}{R}")
        lines.append(f"  {GREEN}→ {out}{R}")
    return lines


# ── Step machine ───────────────────────────────────────────────────────────────

def _splitter_step_sample(files: list[Path]) -> str:
    render(
        title="Step 1/5 — Pick a file to learn from",
        sub="We'll split this filename apart, then label each piece so we can\n"
            "  rebuild a clean new name for every file using the same labels.",
    )
    if files:
        print(f"  {BOLD}Detected:{R}  {files[0].name}")
        blank()
    sample = ask("Use this filename (or paste a different one)",
                 default=files[0].name if files else "", back=False)
    return sample.strip("'\"") or (files[0].name if files else "")


def _splitter_step_separator(sample: str) -> SplitState:
    stem = Path(sample).stem
    while True:
        render(
            title="Step 2/5 — Pick a separator",
            context_lines=[f"File: {DIM}{sample}{R}"],
            sub="What character splits the fields apart in this filename?",
        )
        print(f"  {DIM}{stem}{R}")
        blank()
        print(f"  {CYAN}1{R} dot \".\"     {CYAN}2{R} space \" \"     {CYAN}3{R} dash \"-\"     {CYAN}4{R} underscore \"_\"")
        print(f"  {CYAN}5{R} type a custom separator")
        blank()
        raw = input(f"  Choice: ").strip().lower()
        _check_back(raw)

        sep_map = {"1": ".", "2": " ", "3": "-", "4": "_"}
        if raw in sep_map:
            sep = sep_map[raw]
        elif raw == "5":
            sep = ask("Separator", back=False)
            if not sep:
                continue
        else:
            err("Enter 1–5.")
            input("  Press Enter to continue...")
            continue

        state = SplitState(stem)
        n = state.split_all(sep)
        if n == 0:
            warn(f"That separator doesn't appear in the filename.")
            input("  Press Enter to continue...")
            continue
        return state


def _splitter_step_assign(sample: str, state: SplitState) -> None:
    while True:
        render(
            title="Step 3/5 — Label each piece",
            context_lines=[f"File: {DIM}{sample}{R}"],
            sub="Type a label, then the index (or range) it applies to.",
        )

        for line in state.column_rows():
            print(f"  {line}")
        blank()

        print(f"  {BOLD}Labels:{R}")
        for i, key in enumerate(TOKEN_KEY_ORDER, 1):
            print(f"   {CYAN}{i}{R} {MAGENTA}{key:<8}{R} {TOKEN_KEYS[key]}")
        blank()
        used_custom = sorted(k for k in state.key_order_assigned() if k not in TOKEN_KEYS)
        if used_custom:
            print(f"  {BOLD}Custom fields already in use:{R} " +
                  "  ".join(f"{_key_color(k)}{k}{R}" for k in used_custom))
            blank()
        print(f"  {DIM}Examples: \"show 0\"  ·  \"se 2\"  ·  \"title 3-6\"{R}")
        print(f"  {DIM}\"custom group 2\"  → label index 2 as a new field called 'group'{R}")
        print(f"  {DIM}'resplit N sep' = split token N further · 'undo' = undo last label{R}")
        print(f"  {DIM}'done' when every needed piece is labelled · 'b' = go back{R}")
        blank()

        raw = input(f"  Command: ").strip()
        low = raw.lower()

        if low in ("b", "back"):
            raise Back()
        if low == "done":
            if not state.has_required_keys():
                err("Label at least the episode number (key 'ep' or 'se').")
                input("  Press Enter to continue...")
                continue
            return
        if low == "undo":
            if state.undo():
                info("Undone.")
            else:
                warn("Nothing to undo.")
            continue
        if low.startswith("resplit"):
            parts = raw.split(maxsplit=2)
            if len(parts) < 3:
                err("Usage: resplit <index> <separator>")
                input("  Press Enter to continue...")
                continue
            try:
                idx = int(parts[1])
            except ValueError:
                err("Index must be a number.")
                input("  Press Enter to continue...")
                continue
            if not state.split_token(idx, parts[2]):
                err(f"Separator '{parts[2]}' not found in that token.")
                input("  Press Enter to continue...")
            continue

        # ── Custom field syntax: "custom <name> <index>" or "custom <name> <start>-<end>"
        if low.startswith("custom "):
            cm = re.match(r'^custom\s+(\w+)\s+(\d+)(?:-(\d+))?$', raw, re.IGNORECASE)
            if not cm:
                err("Format: custom <name> <index>  or  custom <name> <start>-<end>")
                input("  Press Enter to continue...")
                continue
            cname, start_s, end_s = cm.groups()
            cname = cname.lower()
            if cname in TOKEN_KEYS:
                err(f"'{cname}' is a reserved label name — pick something else.")
                input("  Press Enter to continue...")
                continue
            start = int(start_s)
            end   = int(end_s) if end_s else start
            if start >= len(state.tokens) or end >= len(state.tokens) or start > end:
                err(f"Index out of range — valid range is 0–{len(state.tokens)-1}.")
                input("  Press Enter to continue...")
                continue
            joiner = " " if state.separator in (".", "_") else state.separator
            state.assign(start, end, cname, joiner=joiner)
            continue

        m = re.match(r'^(\w+)\s+(\d+)(?:-(\d+))?$', raw)
        if not m:
            err("Format: <label> <index> or <label> <start>-<end>")
            input("  Press Enter to continue...")
            continue

        key_input, start_s, end_s = m.groups()
        key_input = key_input.lower()

        # resolve key by number or name (built-ins only here — customs use 'custom name idx')
        key = None
        if key_input.isdigit():
            ki = int(key_input) - 1
            if 0 <= ki < len(TOKEN_KEY_ORDER):
                key = TOKEN_KEY_ORDER[ki]
                if key == "custom":
                    err("To add a custom field, type: custom <name> <index>")
                    input("  Press Enter to continue...")
                    continue
        elif key_input in TOKEN_KEYS and key_input != "custom":
            key = key_input
        elif key_input in state.key_order_assigned():
            # re-using an already-defined custom name without the 'custom' prefix
            key = key_input

        if key is None:
            err(f"Unknown label '{key_input}'. For a new custom field, type: custom {key_input} <index>")
            input("  Press Enter to continue...")
            continue

        start = int(start_s)
        end   = int(end_s) if end_s else start

        if start >= len(state.tokens) or end >= len(state.tokens) or start > end:
            err(f"Index out of range — valid range is 0–{len(state.tokens)-1}.")
            input("  Press Enter to continue...")
            continue

        joiner = " " if state.separator in (".", "_") else state.separator
        state.assign(start, end, key, joiner=joiner)


def _splitter_step_test(files: list[Path], sample: str, state: SplitState) -> list[str]:
    """Step 4: confirm the labelling reproduces correctly across the batch."""
    key_order       = state.key_order_assigned()
    key_widths      = state.key_widths()
    resplits        = state.resplits
    literal_anchors = state.literal_anchors()
    while True:
        render(
            title="Step 4/5 — Check it against your files",
            context_lines=[f"File: {DIM}{sample}{R}", f"Labels: {state.summary_line()}"],
        )
        print(f"  {BOLD}Re-applying your labels to each file:{R}")
        any_fail = False
        for f in files[:5]:
            groups = extract_with_recipe(f.stem, state.separator, key_order, key_widths,
                                          resplits, literal_anchors)
            if groups.get("ep") or groups.get("season"):
                shown = "  ".join(f"{DIM}{k}{R}={CYAN}{v}{R}" for k, v in groups.items())
                print(f"  {GREEN}✓{R} {DIM}{f.name}{R}")
                print(f"      {shown}")
            else:
                print(f"  {RED}✗{R} no episode number found: {DIM}{f.name}{R}")
                any_fail = True
        if any_fail:
            warn("Some files didn't resolve — you may need to relabel a piece.")

        blank()
        print(f"  {CYAN}1{R} Looks good, continue   {CYAN}2{R} Go back and relabel   {CYAN}b{R} Back")
        raw = input(f"  Choice: ").strip().lower()
        _check_back(raw)
        if raw == "1":
            return key_order
        if raw == "2":
            raise Back()


def _splitter_step_output(files: list[Path], sample: str, state: SplitState,
                           key_order: list[str], season: int) -> str:
    key_widths      = state.key_widths()
    resplits        = state.resplits
    literal_anchors = state.literal_anchors()
    captured = set(extract_with_recipe(Path(sample).stem, state.separator, key_order,
                                        key_widths, resplits, literal_anchors).keys())
    fmt_override = None
    while True:
        render(
            title="Step 5/5 — Rebuild the filename",
            context_lines=[f"Labels: {state.summary_line()}"],
            sub="Choose how the labelled pieces get put back together into the new name.",
        )
        fmt = _build_output_fmt(captured, default_suggestion=fmt_override)
        preview = _preview_token_lines(sample, state.separator, key_order, fmt, season, files,
                                        n=3, key_widths=key_widths, resplits=resplits,
                                        literal_anchors=literal_anchors)
        if preview:
            blank()
            print(f"  {BOLD}Preview:{R}")
            for line in preview:
                print(f"  {line}")
        else:
            warn("No files matched — go back and check your labels.")

        blank()
        print(f"  {CYAN}1{R} Use this   {CYAN}2{R} Try a different format   {CYAN}b{R} Back")
        raw = input(f"  Choice: ").strip().lower()
        _check_back(raw)
        if raw == "1":
            return fmt
        if raw == "2":
            fmt_override = fmt


def flow_token_splitter(files: list[Path], show: str, season: int, folder: Path):
    render(title="Mode 7 — Split & Label", sub=(
        "For filenames where every field is jammed together with the same\n"
        "  separator (dots, dashes, etc.) — split it apart, then label each piece."
    ))

    print(f"  {CYAN}1{R} Build new (step-by-step)   {CYAN}2{R} Load a saved pattern")
    blank()

    saved_recipe = None
    while True:
        start = input(f"  {BOLD}Choice{R}: ").strip()
        if start == "2":
            try:
                saved_recipe = _pick_token_pattern()
            except Back:
                continue
            if saved_recipe:
                break
        if start in ("1", "2"):
            break
        err("Enter 1 or 2.")

    key_widths: dict[str, int] = {}
    resplits: list[dict] = []
    literal_anchors: list = []

    if saved_recipe:
        separator       = saved_recipe["separator"]
        key_order       = saved_recipe["key_order"]
        output_fmt      = saved_recipe["output_fmt"]
        key_widths      = saved_recipe.get("key_widths", {})
        resplits        = saved_recipe.get("resplits", [])
        literal_anchors = saved_recipe.get("literal_anchors", [])
        render(title="Loaded pattern",
               context_lines=[f"Separator: {DIM}\"{separator}\"{R}", f"Output: {DIM}{output_fmt}{R}"])
        for f in files[:5]:
            groups = extract_with_recipe(f.stem, separator, key_order, key_widths,
                                          resplits, literal_anchors)
            status = "✓" if (groups.get("ep") or groups.get("season")) else "✗"
            col = GREEN if status == "✓" else RED
            print(f"  {col}{status}{R} {DIM}{f.name}{R}")
        input("  Press Enter to continue...")
    else:
        sample = ""
        state  = None
        key_order = []
        output_fmt = ""
        step = 1
        while step <= 5:
            try:
                if step == 1:
                    sample = _splitter_step_sample(files)
                    step = 2
                elif step == 2:
                    state = _splitter_step_separator(sample)
                    step = 3
                elif step == 3:
                    _splitter_step_assign(sample, state)
                    step = 4
                elif step == 4:
                    key_order = _splitter_step_test(files, sample, state)
                    step = 5
                elif step == 5:
                    output_fmt = _splitter_step_output(files, sample, state, key_order, season)
                    step = 6
            except Back:
                step = max(1, step - 1)

        key_widths      = state.key_widths()
        resplits        = state.resplits
        literal_anchors = state.literal_anchors()

        render(title="Save for next time?",
               context_lines=[f"Labels: {state.summary_line()}"])
        if ask_yn("Save this pattern?", default_yes=True):
            pname = ask("Name", default=show or "my-pattern")
            _save_token_pattern(pname, {
                "separator":       state.separator,
                "key_order":       key_order,
                "output_fmt":      output_fmt,
                "key_widths":      key_widths,
                "resplits":        resplits,
                "literal_anchors": literal_anchors,
            })
        separator = state.separator

    settings = [
        {"key": "show",   "label": "Show name (overrides detected)", "value": show, "kind": "str"},
        {"key": "season", "label": "Season number (fallback)",       "value": season, "kind": "int"},
        {"key": "output", "label": "Output format",                  "value": output_fmt, "kind": "str"},
    ]
    settings = review_settings(settings, title="Final Settings")
    final_show, final_season, final_output_fmt = (
        get(settings, "show"), get(settings, "season"), get(settings, "output")
    )

    def build(f, i):
        groups = extract_with_recipe(f.stem, separator, key_order, key_widths,
                                      resplits, literal_anchors)
        if not (groups.get("ep") or groups.get("season")):
            return None
        if final_show:
            groups["show"] = final_show
        return _apply_output_fmt(final_output_fmt, groups, final_season) + f.suffix.lower()

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


# ─── Overwrite by Episode Number ──────────────────────────────────────────────
#
# Takes two folders: a SOURCE folder of already-correctly-named episodes,
# and a MATCH folder of episodes (often messily named) that should replace
# the source files' CONTENT while keeping the source's clean filename.
# Both folders are scanned for an SxxExx pattern; files pairing up by the
# same season+episode get the source file's content replaced with the
# match file's content. The original source file is never deleted outright
# — it's moved into a .backup_before_overwrite/ subfolder first.

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


# ─── Season management helpers (shared by both new utilities) ────────────────

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


def _clean_show_folder_name(name: str) -> str:
    """
    Strip the metadata tags media servers commonly add to a show's root
    folder name, leaving just the show title:
      - (2020)                  year
      - {tvdb-12345}            Plex/Jellyfin agent ID
      - {imdb-tt1234567}
      - [tmdbid-12345]
    """
    cleaned = name
    cleaned = re.sub(r'\{[^}]*\}', '', cleaned)     # {tvdb-...}, {imdb-...}
    cleaned = re.sub(r'\[[^\]]*\]', '', cleaned)    # [tmdbid-...]
    cleaned = re.sub(r'\(\d{4}\)', '', cleaned)     # (2020)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip(" -_")
    return cleaned


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


# ─── Feature 1: Renumber / Move Season ────────────────────────────────────────

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


# ─── Feature 2: Define Episode Ranges → Season Folders ───────────────────────

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
    blank()

    ranges: list[tuple[int, int, int]] = []   # (season_num, start_ep, end_ep)
    next_season = 1

    while True:
        if ranges:
            blank()
            print(f"  {BOLD}Ranges so far:{R}")
            for s, a, b in ranges:
                print(f"    Season {s:02d}:  episodes {a}–{b}")
            blank()

        raw = input(f"  Season {next_season} range (e.g. \"1-12\", or 'done'): ").strip().lower()

        if raw in ("b", "back"):
            raise Back()
        if raw == "done":
            break

        m = re.match(r'^(\d+)\s*-\s*(\d+)$', raw)
        if not m:
            err("Format: <start>-<end>, e.g. 1-12")
            continue
        start, end = int(m.group(1)), int(m.group(2))
        if start > end:
            start, end = end, start

        overlap = [
            (s, a, b) for s, a, b in ranges
            if not (end < a or start > b)
        ]
        if overlap:
            err(f"Overlaps with season {overlap[0][0]} ({overlap[0][1]}–{overlap[0][2]}).")
            continue

        ranges.append((next_season, start, end))
        next_season += 1

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


# ─── Main ─────────────────────────────────────────────────────────────────────

FLOW_BUILDERS = {
    "1": flow_fansub, "2": flow_one_pace, "3": flow_simple,
    "4": flow_sxxexx, "5": flow_custom_regex, "6": flow_regex_builder,
    "7": flow_token_splitter,
}

MODE_LABELS = {
    "1": ("Standard Fansub",        "[DB]Show_-_01_(info).mkv",
          "Parses [Group]Show_-_NN_(info) style filenames into S01E01 format."),
    "2": ("One Pace / Group+Range", "[Group][841-842] Arc 10 [720p].mp4",
          "Handles arc names and episode ranges from One Pace style releases."),
    "3": ("Simple Numbered",        "01.mkv / Episode 05.mkv",
          "Plain numbered files — turns 01.mkv into Show - S01E01.mkv."),
    "4": ("Normalize S##E##",       "old.show.S01E04.1080p.mkv",
          "Cleans up messy filenames that already contain a season+episode."),
    "5": ("Raw Regex",              "type your own pattern",
          "For advanced users — type your own regex with named groups."),
    "6": ("Build From Sample",      "guided, works on any format",
          "Step-by-step builder: pick a sample, identify its parts, rebuild."),
    "7": ("Split & Label",          "Blue.Box.S01E07.Title.1080p...-GROUP",
          "For dot/dash-bombed filenames — split on a separator, label each piece."),
}


def main_menu():
    context = []
    if _CLI_INITIAL_PATH is not None:
        context.append(f"📁 Starting folder ready: {DIM}{_CLI_INITIAL_PATH}{R}")

    render(title="Media Batch Renamer · Linux Edition",
           context_lines=context,
           sub="Type 'b' at most prompts to go back a step.")
    print(f"  {BOLD}Rename modes:{R}")
    for k, (label, example, summary) in MODE_LABELS.items():
        print(f"   {CYAN}{k}{R} {label:<24}{DIM}{example}{R}")
        print(f"      {DIM}{summary}{R}")
    blank()
    print(f"  {BOLD}Utilities:{R}")
    print(f"   {CYAN}8{R} Preview files in a folder")
    print(f"      {DIM}Just lists the media files found — no renaming.{R}")
    print(f"   {CYAN}9{R} Split into Season XX/ subfolders")
    print(f"      {DIM}Scans filenames for S01/S02 tags and sorts files into folders.{R}")
    print(f"   {CYAN}10{R} Rename show name across files")
    print(f"      {DIM}Swaps the show-name prefix on files already named ...-S01E01.{R}")
    print(f"   {CYAN}11{R} Overwrite by Episode Number")
    print(f"      {DIM}Replaces a Source file's content with a Match file's, by S/E number.{R}")
    print(f"   {CYAN}12{R} Clean up backup folders")
    print(f"      {DIM}Finds and deletes .backup_before_overwrite folders left by option 11.{R}")
    print(f"   {CYAN}13{R} Renumber / Move Season")
    print(f"      {DIM}Moves a season's episodes, appends them, and closes any gaps.{R}")
    print(f"   {CYAN}14{R} Split Into Seasons By Range")
    print(f"      {DIM}Define episode ranges — each becomes its own Season folder.{R}")
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

        if choice == "8":
            util_preview(pick_folder())
            input("\n  Press Enter to return to menu...")
            continue
        if choice == "9":
            util_split(pick_folder())
            input("\n  Press Enter to return to menu...")
            continue
        if choice == "10":
            util_rename_show(pick_folder())
            input("\n  Press Enter to return to menu...")
            continue
        if choice == "11":
            util_overwrite_by_episode()
            input("\n  Press Enter to return to menu...")
            continue
        if choice == "12":
            util_cleanup_backups()
            input("\n  Press Enter to return to menu...")
            continue
        if choice == "13":
            try:
                util_renumber_season()
            except Back:
                pass
            input("\n  Press Enter to return to menu...")
            continue
        if choice == "14":
            try:
                util_define_season_ranges()
            except Back:
                pass
            input("\n  Press Enter to return to menu...")
            continue

        if choice not in FLOW_BUILDERS:
            err("Please enter 1–14 or q.")
            input("  Press Enter to continue...")
            continue

        label, example, summary = MODE_LABELS[choice]
        render(title=f"Mode: {label}",
               context_lines=[f"Matches: {DIM}{example}{R}", f"{DIM}{summary}{R}"])


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
                if not _confirm_show_name(files, folder, build_fn):
                    continue
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
    if len(sys.argv) > 1:
        candidate = Path(sys.argv[1]).expanduser()
        if candidate.is_dir():
            _CLI_INITIAL_PATH = candidate.resolve() if not str(candidate).startswith("/run/user") else candidate
        elif candidate.is_file():
            _CLI_INITIAL_PATH = candidate.parent
        else:
            print(f"  Warning: path not found, ignoring: {sys.argv[1]}")
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n\n  {DIM}Interrupted.{R}\n")
        sys.exit(0)