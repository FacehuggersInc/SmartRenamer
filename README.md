# Media Batch Renamer

A terminal tool for batch-renaming anime/TV episode files into clean,
consistent names like `Show Name - S01E01 - Episode Title.mkv`, keeping a
season's folder structure tidy, and optionally pulling in correct episode
titles and writing them into Plex. Runs on Linux, macOS, and Windows.

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
- **Fix S01EP01 → S01E01** — corrects filenames using `EP` instead of `E` (e.g.
  `Show - s01ep01.mkv`), which some media servers won't recognise as a valid
  episode tag at all
- **Multi-Batch Rename** — runs a rename mode across every Season folder in turn
- **Overwrite by Episode Number** — replaces a Source file's content with a Match file's, by episode
- **Clean up backup folders** — deletes leftover `.backup_before_overwrite` folders
- **Fetch & Apply Episode Metadata** — pulls correct episode titles from
  MyAnimeList and writes them into Plex (locked) plus a `.nfo` backup per
  episode — see the dedicated section below, there are a few important
  caveats
- **Renumber / Move Season** — moves a season's episodes, appends them, closes gaps
- **Split Into Seasons By Range** — turns a flat folder of episodes into Season folders
- **Set Up Show + Season Folders** — creates season folders and pulls files in from Downloads

(Exact menu numbers depend on what's installed — the menu lists them for you
with a one-line description under each.)

---

## Fetch & Apply Episode Metadata — read this before using it

This is the most powerful utility here, but also the one with the most
caveats. Please read all of these before relying on it:

### It's anime-only, currently
Episode titles are pulled from **MyAnimeList**, via the Jikan API. This
works well for anime specifically because MyAnimeList tends to track
absolute episode numbering accurately even when local files (or even other
metadata sources like TheTVDB) disagree about season boundaries — which is
exactly the kind of mismatch this tool exists to work around. It has **no
support for non-anime TV shows** — there's no other data source wired in
for that.

### It needs a `.env` file, and it must sit next to `run.py`
Create a plain text file named `.env` in the same folder as `run.py`:

```
PLEX_TOKEN=your_plex_token_here
PLEX_BASE_URL=http://192.168.1.x:32400
```

- **`PLEX_TOKEN`** is required *only if* you want this tool to write into
  Plex directly. [How to find your Plex token](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/).
- **`PLEX_BASE_URL`** is optional — it defaults to `http://127.0.0.1:32400`
  (i.e. "Plex is running on this same machine"). Set it explicitly if this
  script runs somewhere else on your network than your Plex server does.
- If `PLEX_TOKEN` is missing entirely, the tool **skips Plex automatically**
  and still writes the `.nfo` backup files — you don't need Plex at all if
  all you want is the `.nfo` files (see below).

### If you don't use Plex at all
You can ignore the `.env` file completely. The tool always writes a
Kodi/Jellyfin-style `.nfo` sidecar file next to each episode
(`Show - S01E01.mkv` → `Show - S01E01.nfo`) regardless of whether Plex is
configured or reachable — these work directly with Jellyfin and Kodi, and
serve as a portable backup of the correct titles either way.

### How it decides what's "correct"
**MyAnimeList is treated as the source of truth.** If Plex already has a
different title for an episode, this tool overwrites Plex's title to match
MyAnimeList's — not the other way around. Once written, the title field is
**locked** in Plex so it won't get silently changed back by a future
automatic refresh.

### How it finds the right Plex episode
Matching is done by **file path**, not by title or by Plex's own season/
episode numbers — those numbers are exactly what might be wrong in the
first place, so trusting them to find the episode would defeat the point.
If the exact path doesn't match (common when this script and Plex see the
same files through different mount points — e.g. a Docker container on
Unraid seeing `/media/...` for what this script sees as
`/mnt/unraidmedia/...`), it falls back to matching by the longest shared
folder structure instead, and tells you clearly when that fallback was
used. If a file genuinely can't be matched to anything in Plex, it's left
alone in Plex — its `.nfo` backup is still written regardless.

### Point it at the show's root folder, not a season folder
The tool can find Season subfolders on its own — give it the folder that
*contains* `Season 01/`, `Season 02/`, etc. (or, for a single-season show,
the show's own folder, not the `Season 01` folder inside it). It needs the
real show folder to be able to ask Plex about the show at all.

---

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