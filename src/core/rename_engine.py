"""
core.rename_engine — The shared rename loop (dry-run + real), the settings-review
screen used by every simple mode, and the final show-name
confirmation gate required before any real rename happens.
"""

import re
from pathlib import Path

from .display import BOLD, Back, CYAN, DIM, GREEN, R, YELLOW, ask, ask_yn, blank, err, info, render, sep_line, success, warn
from .filesystem import _clean_show_folder_name, safe_rename


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
            detected_show = _clean_show_folder_name(m.group(1))

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
