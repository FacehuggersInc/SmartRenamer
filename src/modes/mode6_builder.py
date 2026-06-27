"""
modes.mode6_builder — Rename Mode 6: the guided 'Build From Sample' regex builder.
"""

import re
import json
from pathlib import Path

from ..core.config import _config_dir
from ..core.display import BLUE, BOLD, Back, CYAN, DIM, GREEN, MAGENTA, R, RED, YELLOW, _check_back, ask, ask_yn, blank, err, info, render, success, warn
from ..core.filesystem import ep_str, sanitize_filename_part
from ..core.rename_engine import get, review_settings


PATTERNS_FILE = _config_dir() / "patterns.json"

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
    return sanitize_filename_part(out)

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
