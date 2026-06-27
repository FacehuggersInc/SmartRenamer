# Media Batch Renamer

A terminal tool for batch-renaming anime/TV episode files into clean,
consistent names like `Show Name - S01E01 - Episode Title.mkv`, and for
keeping a season's folder structure tidy. Runs on Linux, macOS, and
Windows.

```bash
chmod +x run.py
./run.py
./run.py /path/to/your/show     # optional starting folder
```

## What's included

**Rename modes** (pick the one that matches your filenames):

1. **Standard Fansub** — `[Group]Show_-_01_(info).mkv` style releases
2. **One Pace / Group+Range** — releases with arc names and episode ranges
3. **Simple Numbered** — plain numbered files (`01.mkv`, `Episode 05.mkv`)
4. **Normalize S##E##** — cleans up filenames that already have a season+episode
5. **Raw Regex** — type your own regex with named groups
6. **Build From Sample** — guided builder: pick a sample, identify its parts, rebuild
7. **Split & Label** — for dot/dash-bombed filenames; split on a separator, label each piece
8. **Trim Filename** — already-correct names with junk attached; keep a range, drop the rest

**Utilities:**

- **Preview files in a folder** — lists media files, no renaming
- **Split into Season XX/ subfolders** — sorts files by S01/S02 tag
- **Rename show name across files** — swaps the show-name prefix on already-renamed files
- **Multi-Batch Rename** — runs a rename mode across every Season folder in turn
- **Overwrite by Episode Number** — replaces a Source file's content with a Match file's, by episode
- **Clean up backup folders** — deletes leftover `.backup_before_overwrite` folders
- **Renumber / Move Season** — moves a season's episodes, appends them, closes gaps
- **Split Into Seasons By Range** — turns a flat folder of episodes into Season folders
- **Set Up Show + Season Folders** — creates season folders and pulls files in from Downloads

## Adding a new tool

**A new rename mode** — add a file in `modes/`, then register it in
`core/dispatch.py`:

```python
from ..modes.my_new_mode import flow_my_thing

FLOW_BUILDERS["9"] = flow_my_thing
MODE_LABELS["9"] = ("My Thing", "example.mkv", "One-line description.")
```

**A new utility** — add a file in `utilities/` ending with a
`UTILITY_ENTRIES` list:

```python
from ..core.registry import UtilEntry

def util_my_thing(folder):   # name the param 'folder' to get one picked for you
    ...

UTILITY_ENTRIES = [
    UtilEntry("My Thing", "One-line description.", util_my_thing),
]
```

That's it — `main.py` never needs editing. It discovers every file in
`utilities/` on startup, collects each one's `UTILITY_ENTRIES`, and
assigns menu numbers automatically right after the rename modes.