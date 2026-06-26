# Media Batch Renamer

A terminal tool for batch-renaming anime/TV episode files on Linux into clean,
consistent names like `Show Name - S01E01 - Episode Title.mkv`. Built for
people with messy fansub/release-group filenames who want a fast, repeatable
way to clean them up — no scripting required.

```bash
chmod +x rename_media.py
./rename_media.py
```

Requires Python 3.10+ (standard library only — no extra packages to install).

---

## What it does

You point it at a folder, tell it what kind of filenames are in there, answer
a few questions, preview the result, and confirm. Nothing is renamed until you
explicitly say so — every mode shows a **dry run** first.

It also remembers folder locations (including SMB/network shares mounted
through Nautilus), and lets you save custom rename patterns to reuse on future
batches.

---

## Choosing a folder

When asked for a folder, you'll see:

- **Local locations** — your home directory, Videos/Downloads/Documents/Music/
  Pictures (read from your desktop's actual folder settings if available),
  and anything mounted under `/media` or `/mnt` (USB drives, etc.)
- **Network shares** — any SMB/FTP/SFTP/MTP share currently mounted through
  Nautilus ("Files"), shown with a readable name like `nas / media (SMB)`
  instead of the raw mount path

Pick a number to browse into that location, or just paste/type any path
directly — including drag-and-drop from a file manager. Once inside a
location you can:

- Type a **number** to open that subfolder
- Type **part of a name** (e.g. `hori`) to search all subfolders, even ones
  not currently shown on screen
- Type an **absolute or relative path** to jump anywhere
- Press **Enter** to use the folder you're currently looking at

---

## The 7 rename modes

| Mode | Best for | Example input |
|---|---|---|
| **1 — Standard Fansub** | `[Group]Show_-_01_(info).mkv` style releases | `[DB]Kaoru Hana wa Rin to Saku_-_01_(...).mkv` |
| **2 — One Pace / Group+Range** | Releases with arc names and episode ranges | `[One Pace][841-842] Whole Cake Island 10 [720p].mp4` |
| **3 — Simple Numbered** | Plain numbered files | `01.mkv`, `Episode 05.mkv` |
| **4 — Normalize S##E##** | Files that already have season/episode info, just messy otherwise | `old.show.S01E04.1080p.mkv` |
| **5 — Raw Regex** | You already know regex and want to type a pattern directly | any |
| **6 — Build From Sample** | Filenames with recognisable words/brackets separating fields | `Horimiya - Episode 01 - Title 1080p.mkv` |
| **7 — Split & Label** | Filenames where every field uses the *same* separator (dot-bombed release names) | `Blue.Box.S01E07.Title.1080p...-GROUP.mkv` |

Modes 1–4 ask a short list of yes/no and text questions tailored to that
format (sequential vs. parsed episode numbers, whether to include quality
tags, etc.) and show you a summary you can edit before continuing.

Mode 5 is for people comfortable writing their own regex with named groups
(`(?P<ep>\d+)`, `(?P<show>.+?)`, etc.).

**Modes 6 and 7 are guided builders for everything else** — see below for
which one fits your files.

---

## Mode 6: Build From Sample

This mode doesn't ask you to know regex. Instead, it walks you through
**identifying the parts of one of your real filenames**, then reuses those
same parts to build a clean new name for every file in the folder.

### Step 1 — Pick a sample
It shows you the first file it found and lets you use it (or paste a
different filename) as the example to work from.

### Step 2 — Identify the parts
Your sample filename is shown broken into colour-coded pieces:

```
Horimiya - Episode 01 - A Tiny Happenstance 1080p BDRip x265...
└show┘   └sep┘ └word_ep┘└ep┘ └sep┘ └────title────┘ └quality┘
```

Below that is a numbered list of available **parts**:

**Parts that capture a value** (these become usable in the new filename):

| # | Shorthand | Captures |
|---|---|---|
| 1 | `show` | the show/series name |
| 2 | `ep` | episode number |
| 3 | `season` | season number |
| 4 | `title` | episode title |
| 5 | `quality` | resolution tag (720p, 1080p, …) |

**Connecting parts** (matched but not kept — just punctuation/structure):

| # | Shorthand | Matches |
|---|---|---|
| 6 | `sep` | `" - "` (space-dash-space) |
| 7 | `word_ep` | the word "Episode" or "Ep." |
| 8 | `space` | one or more spaces |
| 9 | `dot` | a literal `.` |
| 10 | `bracket_open` | `[` |
| 11 | `bracket_close` | `]` |
| 12 | `custom` | any exact text you type |

You add parts **one at a time, left to right**, matching them to the
filename above — by typing either the **number** or the **shorthand name**
(e.g. typing `show` does the same thing as typing `1`). After each part you
add, it re-tests live against your sample so you can see what's matching so
far. Type `undo` to remove the last part, `done` when you're finished.

You must include an `ep` part — that's how the tool knows which number to use
when building each new filename.

### Step 3 — Check it against your files
Shows the parts you identified tested against (up to) five real files in the
folder, so you can confirm it's working correctly before moving on. You can
edit the underlying pattern by hand here if you want to fine-tune it.

### Step 4 — Rebuild the filename
Now you decide how the parts you identified get put back together. Available
tokens:

| Token | Produces |
|---|---|
| `{show}` | the show name |
| `{SE}` | `S01E01` |
| `{S}` | `S01` |
| `{E}` | `E01` |
| `{title}` | the episode title |
| `{quality}` | the quality tag |

A sensible default is suggested based on what you captured (e.g.
`{show} - {SE} - {title} ({quality})`), and you'll see a live preview of
what your actual files will be renamed to before confirming.

### Step 5 — Save for next time
You can save the whole recipe (parts + output format) under a name of your
choice. Next time you pick Mode 6, choosing "Load a saved pattern" skips
straight to a dry run — no rebuilding needed. Saved patterns live in
`~/.config/rename_media/patterns.json`.

---

## Mode 7: Split & Label

Use this instead of Mode 6 when your filenames don't have recognisable
words or brackets separating fields — every part is jammed together with
the *same* separator, like:

```
Blue.Box.S01E07.Can.I.Have.One.1080p.NF.WEB-DL.DUAL.DDP5.1.H.264-VARYG.mkv
```

There's no word like "Episode" or a bracket to anchor on here — just dots.
Mode 7 handles this by splitting the whole filename on one separator and
letting you label each resulting piece directly, by its position.

### Step 1 — Pick a sample
Same as Mode 6 — pick or paste the filename to learn from.

### Step 2 — Pick a separator
Choose what splits the fields apart: dot, space, dash, underscore, or type
your own. The filename is split immediately into numbered pieces.

### Step 3 — Label each piece
Every piece is shown with its index number above it:

```
0         1        2    3  4    5    6      7
Blue Box  S01E07   Can  I  Have One  1080p  NF...
[show]    [se]     [title→ ]              [quality]
```

A `[label]` tag appears directly under each piece once you assign it — colour
is just a secondary cue, the text label is always there so it's never
ambiguous what's stored where.

Type a label name (or its number) followed by the index or range it applies
to:

```
show 0-1        ← merges indices 0 and 1 into the show name
se 2            ← S01E07 combined season+episode
title 3-6       ← the episode title, however many words it is
quality 7       ← the quality tag
skip 8-14       ← everything else, ignored
```

**Built-in labels:**

| Label | Captures |
|---|---|
| `show` | the show/series name |
| `se` | season+episode combined, e.g. `S01E07` |
| `season` | season number only |
| `ep` | episode number only |
| `title` | episode title (length can vary between files — see below) |
| `quality` | resolution tag |
| `skip` | ignored — not used in the new filename |

**Custom labels** — for anything not on that list (release group, codec,
audio format, language tag, etc.), use:

```
custom <name> <index>          e.g.  custom group 2
custom <name> <start>-<end>    e.g.  custom group 2-3
```

This works exactly like a built-in label — `custom group 2` creates a new
field called `group`, and it becomes available as `{group}` in the output
format in Step 5. You can reuse a custom name you already defined just by
typing it directly (`group 5`) once it exists.

**Other commands available at this step:**

| Command | Effect |
|---|---|
| `resplit <index> <sep>` | split one piece further on a different separator |
| `undo` | revert your last label, split, or resplit |
| `done` | finish labelling and move to Step 4 |
| `b` | go back to Step 2 |

### Why `title` (and custom fields) can be any length
`se`, `season`, `ep`, and `quality` are found **by pattern** — the tool
recognises what `S01E07` or `1080p` look like and re-finds them in every
file, wherever they land. `title` has no fixed pattern, so it's defined
instead as **everything between the anchors next to it** — whatever's left
between the episode marker and the quality tag, regardless of word count.
That means you only need to label one sample file; episodes with longer or
shorter titles are handled automatically:

```
Blue.Box.S01E07.Can.I.Have.One.1080p...        → title = "Can I Have One"
Blue.Box.S01E08.The.Next.One.720p...           → title = "The Next One"
```

The same is true for any **custom** field — as long as it sits next to at
least one fixed anchor (`se`, `season`, `ep`, or `quality`), its length is
allowed to vary too. If you define *two* free-text fields back-to-back with
nothing fixed between them (e.g. a custom `group` immediately followed by
`title`), the **first** one is locked to whatever width it had in your
sample — only the last free field in a run can stretch.

### Step 4 — Check it against your files
Re-applies your labels to up to five real files and shows what each one
resolves to, so you can confirm titles/custom fields are landing correctly
before moving on.

### Step 5 — Rebuild the filename
Same output-format builder as Mode 6, plus any custom fields you defined
show up as their own tokens (e.g. `{group}`):

```
{show} - {SE} {group} - {title} ({quality})
```
```
Blue Box - S01E07 - Can I Have One (1080p).mkv
```

Patterns built in Mode 7 save separately from Mode 6, at
`~/.config/rename_media/token_patterns.json`.

---

## Going back

Almost every prompt in the tool accepts **`b`** (or `back`) to return to the
previous step or screen — including in the middle of building a pattern in
Mode 6 or Mode 7. Nothing is lost when you go back; your progress is kept so
you can fix one thing and continue.

In Mode 7's labelling step specifically, `undo` reverts just your last
label/split without leaving the step — useful for quick corrections without
losing everything else you've already labelled.

---

## After building a rename plan

Every mode ends the same way:

1. **Dry run** — shows you exactly what would happen, with a yellow
   `DRY RUN` label on every line so it's never ambiguous whether changes are
   real
2. Choose:
   - **Apply for real** — does the actual renaming
   - **Change settings** — go back and adjust anything, then see another
     dry run
   - **Cancel** — back to the main menu, nothing touched

---

## Utilities

Outside the rename modes, three standalone tools:

- **Preview files in a folder** — just lists the media files found, no
  renaming
- **Split into Season XX/ subfolders** — scans filenames for `S01`/`S02` tags
  and moves files into matching `Season 01/`, `Season 02/` folders
- **Rename show name across files** — if you've already renamed files to
  `Old Name - S01E01...` format and want to swap just the show name prefix
  across the whole batch, without re-running a full rename mode

---

## Supported file types

`.mkv` `.mp4` `.avi` `.m4v` `.mov` `.ts` `.wmv`

## Notes

- Renames never overwrite an existing file — if the target name already
  exists, that file is skipped and reported, not clobbered.
- Nothing is touched until you explicitly confirm at the end of a dry run.
- Saved Mode 6 patterns live at `~/.config/rename_media/patterns.json`.
- Saved Mode 7 patterns live at `~/.config/rename_media/token_patterns.json`.
  Both are plain JSON and can be inspected, backed up, or hand-edited.