"""
modes.mode7_splitter — Rename Mode 7: the 'Split & Label' token splitter builder,
for dot/dash-bombed filenames.
"""

import re
import json
from pathlib import Path

from ..core.config import _config_dir
from ..core.display import BLUE, BOLD, Back, CYAN, DIM, GREEN, MAGENTA, R, RED, YELLOW, _check_back, ask, ask_yn, blank, c, err, info, render, success, warn
from ..core.rename_engine import get, review_settings
from .mode6_builder import _apply_output_fmt, _build_output_fmt


TOKEN_PATTERNS_FILE = _config_dir() / "token_patterns.json"

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

ANCHOR_PREFIX_PATTERNS = {
    "se":      re.compile(r"^[Ss]\d{1,2}[Ee]\d{1,3}(?:[vV]\d+)?"),
    "season":  re.compile(r"^[Ss]\d{1,2}(?:[vV]\d+)?"),
    "ep":      re.compile(r"^\d{1,4}(?:[vV]\d+)?"),
    "quality": re.compile(r"^\d{3,4}p", re.IGNORECASE),
}

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
