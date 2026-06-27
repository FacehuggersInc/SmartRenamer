"""
modes.mode8_trim — Rename Mode 8: 'Trim Filename' — split apart an already-correct
filename and keep just the range you want.
"""

import re
from pathlib import Path

from ..core.display import BOLD, Back, CYAN, DIM, GREEN, R, RED, YELLOW, _check_back, ask, blank, c, dryline, err, render, warn


def _trim_build_plan(stem: str, sep: str, start: int, end: int, sample_token_count: int) -> str:
    """
    Apply a trim recipe (start/end indices recorded against the SAMPLE's
    token count) to any stem, generalizing correctly when the kept range
    touches either edge of the sample.
    """
    tokens = stem.split(sep)
    n = len(tokens)
    sample_n = sample_token_count

    keep_from_front = (start == 0)
    keep_to_back = (end == sample_n - 1)

    if keep_from_front and keep_to_back:
        kept = tokens   # keeping everything — a no-op trim
    elif keep_from_front and not keep_to_back:
        drop_count = sample_n - 1 - end
        kept = tokens[: n - drop_count] if n - drop_count > 0 else tokens
    elif keep_to_back and not keep_from_front:
        drop_count = start
        kept = tokens[drop_count:] if drop_count < n else tokens
    else:
        kept = tokens[start:end + 1]

    return sep.join(kept)

def _trim_step_sample(files: list[Path]) -> str:
    render(
        title="Step 1/3 — Pick a file to learn from",
        sub="We'll split this filename apart so you can pick which part to keep.",
    )
    if files:
        print(f"  {BOLD}Detected:{R}  {files[0].name}")
        blank()
    sample = ask("Use this filename (or paste a different one)",
                 default=files[0].name if files else "", back=False)
    return sample.strip("'\"") or (files[0].name if files else "")

def _trim_step_separator_and_range(sample: str) -> tuple[str, int, int, int]:
    """Step 2: pick a separator, then pick the keep range. Returns
    (separator, start, end, sample_token_count)."""
    stem = Path(sample).stem
    sep = ""
    tokens: list[str] = []

    while True:
        render(
            title="Step 2/3 — Pick a separator",
            context_lines=[f"File: {DIM}{sample}{R}"],
            sub="What character splits the good part from the junk?",
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

        if sep not in stem:
            warn(f"That separator doesn't appear in the filename.")
            input("  Press Enter to continue...")
            continue

        tokens = stem.split(sep)
        break

    while True:
        render(
            title="Step 2/3 — Choose what to keep",
            context_lines=[f"File: {DIM}{sample}{R}"],
            sub="Pick the range of pieces to KEEP — everything else is dropped.",
        )
        for line in _trim_column_rows(tokens):
            print(f"  {line}")
        blank()
        print(f"  {DIM}Example: \"0-4\" keeps pieces 0 through 4 and drops the rest.{R}")
        print(f"  {DIM}\"0-0\" keeps just the first piece — handy when everything good")
        print(f"  is already merged into one piece by this separator.{R}")
        blank()

        raw = input(f"  Keep range (e.g. \"0-4\"): ").strip()
        if raw.lower() in ("b", "back"):
            raise Back()

        m = re.match(r'^(\d+)\s*-\s*(\d+)$', raw)
        if not m:
            err("Format: <start>-<end>, e.g. 0-4")
            input("  Press Enter to continue...")
            continue
        start, end = int(m.group(1)), int(m.group(2))
        if start > end:
            start, end = end, start
        if start >= len(tokens) or end >= len(tokens):
            err(f"Valid range is 0–{len(tokens)-1}.")
            input("  Press Enter to continue...")
            continue

        return sep, start, end, len(tokens)

def _trim_column_rows(tokens: list[str], max_width: int = 70) -> list[str]:
    """Same index/text column display style used in Mode 7."""
    cols = []
    for i, t in enumerate(tokens):
        idx_str = str(i)
        width = max(len(idx_str), len(t))
        cols.append((idx_str, t, width))

    groups, current, current_width = [], [], 0
    for c in cols:
        w = c[2] + 2
        if current and current_width + w > max_width:
            groups.append(current)
            current, current_width = [], 0
        current.append(c)
        current_width += w
    if current:
        groups.append(current)

    lines = []
    for g in groups:
        idx_parts = [f"{DIM}{i.ljust(w)}{R}" for i, t, w in g]
        txt_parts = [f"{CYAN}{BOLD}{t.ljust(w)}{R}" for i, t, w in g]
        lines.append("  ".join(idx_parts))
        lines.append("  ".join(txt_parts))
        lines.append("")
    if lines and lines[-1] == "":
        lines.pop()
    return lines

def _trim_step_preview(files: list[Path], sep: str, start: int, end: int,
                        sample_n: int) -> bool:
    """Step 3: show a dry-run preview across the batch. Returns True to
    proceed, False to go back and adjust the range."""
    while True:
        render(title="Step 3/3 — Preview")
        any_fail = False
        for f in files[:5]:
            new_stem = _trim_build_plan(f.stem, sep, start, end, sample_n)
            if not new_stem or new_stem == f.stem:
                if new_stem == f.stem:
                    print(f"  {YELLOW}={R} {DIM}{f.name}{R}  {DIM}(unchanged){R}")
                else:
                    print(f"  {RED}✗{R} {DIM}{f.name}{R}  {DIM}(would become empty — skipped){R}")
                    any_fail = True
                continue
            new_name = new_stem + f.suffix.lower()
            dryline(f"{DIM}{f.name}{R}")
            print(f"           {GREEN}→ {new_name}{R}")
        if any_fail:
            warn("Some files would end up with an empty name and will be skipped.")

        blank()
        print(f"  {CYAN}1{R} Looks good, continue   {CYAN}2{R} Go back and change the range   {CYAN}b{R} Back")
        raw = input(f"  Choice: ").strip().lower()
        _check_back(raw)
        if raw == "1":
            return True
        if raw == "2":
            raise Back()

def flow_trim_tail(files: list[Path], show: str, season: int, folder: Path):
    render(title="Mode 8 — Trim Filename",
           sub="For filenames that are already correct but have unwanted\n"
               "  junk attached — split apart, keep what you want, drop the rest.")

    sample = ""
    sep, start, end, sample_n = "", 0, 0, 0
    step = 1
    while step <= 3:
        try:
            if step == 1:
                sample = _trim_step_sample(files)
                step = 2
            elif step == 2:
                sep, start, end, sample_n = _trim_step_separator_and_range(sample)
                step = 3
            elif step == 3:
                if _trim_step_preview(files, sep, start, end, sample_n):
                    step = 4
        except Back:
            step = max(1, step - 1)

    def build(f, i):
        new_stem = _trim_build_plan(f.stem, sep, start, end, sample_n)
        if not new_stem or new_stem == f.stem:
            return None
        return new_stem + f.suffix.lower()

    return build
