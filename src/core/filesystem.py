"""
core.filesystem — Finding folders (local dirs, drives, GVFS/SMB shares, Windows
drive letters), listing media files, and safely writing renamed
files to disk (including Windows filename sanitisation).
"""

import os
import re
from pathlib import Path

from .display import BOLD, CYAN, DIM, GREEN, R, ask_yn, blank, dryline, err, render, success, warn


MEDIA_EXT = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".ts", ".wmv"}

IS_WINDOWS = (os.name == "nt")

def _find_all_locations() -> list[tuple[str, Path]]:
    locations: list[tuple[str, Path]] = []
    seen: set[Path] = set()

    def add(label: str, path: Path):
        if path.is_dir() and path not in seen:
            seen.add(path)
            locations.append((label, path))

    home = Path.home()
    add("Home", home)

    if IS_WINDOWS:
        # Windows: standard user folders live directly under the home
        # directory (no XDG config to read), and the equivalent of
        # "other mounted locations" is enumerating drive letters.
        for name in ("Videos", "Downloads", "Documents", "Music", "Pictures"):
            add(name, home / name)

        import string
        for letter in string.ascii_uppercase:
            drive = Path(f"{letter}:\\")
            if drive.is_dir():
                # Skip the drive the OS itself lives on if it's just "C:\"
                # with nothing else interesting at the top level — still
                # list it, just don't treat it specially.
                label = f"{letter}: drive"
                add(label, drive)

        # Windows network shares (mapped drive letters already covered
        # above; UNC paths like \\server\share aren't auto-discoverable
        # the way GVFS mounts are, so they're typed in manually instead).
        return locations

    # ── POSIX (Linux/macOS) below ──────────────────────────────────────────────
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

_WINDOWS_ILLEGAL_CHARS = re.compile(r'[<>:"|?*\x00-\x1f]')

def sanitize_filename(name: str) -> str:
    """
    Make a full filename (including its real extension, e.g. "Show.mkv")
    safe to create on the current OS. On Windows this strips characters
    that are illegal in NTFS/FAT filenames and trims trailing dots/
    spaces. On Linux/macOS this is a no-op.
    """
    if not IS_WINDOWS:
        return name
    # Preserve the extension separately so trimming trailing dots/spaces
    # doesn't accidentally eat into ".mkv" etc. Only treat the suffix as
    # a real extension if it's short and alphanumeric — otherwise (no
    # extension at all, or a dot that's actually part of the title, like
    # "Mr. Smith") just sanitize the whole string as one piece.
    stem, _, ext = name.rpartition(".")
    if stem and re.fullmatch(r'[A-Za-z0-9]{1,5}', ext):
        stem = _WINDOWS_ILLEGAL_CHARS.sub("", stem).rstrip(". ")
        return f"{stem}.{ext}"
    return _WINDOWS_ILLEGAL_CHARS.sub("", name).rstrip(". ")

def sanitize_filename_part(name: str) -> str:
    """
    Make a filename FRAGMENT (no extension involved at all — e.g. the
    output of _apply_output_fmt before ".mkv" is appended) safe on the
    current OS. Unlike sanitize_filename, this never tries to guess at
    an extension — any dot in the string is just part of the title.
    """
    if not IS_WINDOWS:
        return name
    return _WINDOWS_ILLEGAL_CHARS.sub("", name).rstrip(". ")

def safe_rename(src: Path, dst: Path, dry_run: bool) -> bool:
    dst = dst.with_name(sanitize_filename(dst.name))
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
