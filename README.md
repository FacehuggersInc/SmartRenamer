# Media Batch Renamer — package structure

This used to be one ~4,300-line file. It's now split into a package so you
can find things quickly and add new functionality without touching code
you don't need to.

## How to run it

```bash
chmod +x run.py
./run.py
./run.py /path/to/your/show     # optional starting folder
```

`run.py` is a thin launcher — it just points Python at the package next to
it and calls the real entry point. You should never need to edit it.

## Layout

```
run.py                          ← launcher, don't touch
rename_media_package/
  main.py                       ← the menu loop and dispatch
  core/
    display.py                 ← colours, render(), ask()/ask_yn(), Back
    filesystem.py               ← folder picking, list_media, safe_rename
    parsers.py                  ← filename parsers for Modes 1-4
    rename_engine.py            ← the dry-run/apply loop, settings screen,
                                   final show-name confirmation
    config.py                    ← where saved patterns live on disk
    dispatch.py                  ← wires every mode into one table; drives
                                    a mode end-to-end against a folder
  modes/
    simple_modes.py              ← Modes 1-4
    mode5_regex.py                ← Mode 5
    mode6_builder.py              ← Mode 6 (Build From Sample)
    mode7_splitter.py             ← Mode 7 (Split & Label)
    mode8_trim.py                 ← Mode 8 (Trim Filename)
  utilities/
    basic.py                      ← Preview / Split-into-seasons / Rename-show-name
    overwrite.py                  ← Overwrite by Episode Number + backup cleanup
    season_tools.py               ← shared season-folder helpers + the two
                                     season-renumbering utilities
    setup_show.py                  ← Set Up Show + Season Folders
    multi_batch.py                 ← Multi-Batch Rename
```

## Where to add things

**A new rename mode** → new file in `modes/`, then two lines in
`core/dispatch.py`:

```python
from ..modes.my_new_mode import flow_my_thing
# ...
FLOW_BUILDERS["9"] = flow_my_thing
MODE_LABELS["9"] = ("My Thing", "example.mkv", "One-line description.")
```

**A new utility** → new file in `utilities/`, then in `main.py` add one
`if choice == "18":` block calling it, plus one line in the menu listing
in `main_menu()`. That's it — `main.py` doesn't need to know anything
about how your utility works internally.

**A new helper used by several existing files** (a new parser, a new
string-cleanup function, a new way to find folders) → add it to whichever
`core/*.py` file it's closest to in spirit. If it's genuinely new
territory, add a new file under `core/` and import from it wherever
needed — nothing else needs to change.

**A fix or tweak to something that already exists** → find it by name;
every file is named for what's in it, and each one is small enough to
read in a minute or two.

## How the pieces fit together

- `core/` never imports from `modes/` or `utilities/` — it's the
  foundation everything else builds on.
- `modes/` only imports from `core/` (mode 7 also borrows two small
  helpers from mode 6's output-formatting code).
- `utilities/` only imports from `core/`, and occasionally from each
  other (the season-related utilities share `season_tools.py`).
- `core/dispatch.py` is the one place that knows about every mode — it's
  what lets both the normal menu and the Multi-Batch tool run any mode
  against a folder without needing their own copy of that logic.
- `main.py` sits on top of all of it and only deals with the menu and
  top-level argument parsing.

If you're ever unsure where to put something: if it touches `Path`,
folders, or renaming files, it's `core`. If it's a self-contained way of
deciding what a new filename should be, it's a mode. If it's a one-off
folder-management operation, it's a utility.

## A couple of things to know about if you're adding global state

Two flags are shared across the whole program by being mutated from
outside the file that defines them:

- `core.filesystem._CLI_INITIAL_PATH` — the folder passed on the command
  line, offered once then cleared.
- `core.display._BATCH_CONTEXT` — set by the Multi-Batch tool so every
  screen shows which season it's on.

If you ever need to add something similar, **don't** write
`from core.display import _BATCH_CONTEXT` and then `global _BATCH_CONTEXT`
in your file — that only rebinds your own file's local copy of the name,
it won't actually change anything the rest of the program sees. Instead,
import the module itself and set the attribute on it directly:

```python
from ..core import display as _display
# ...
_display._BATCH_CONTEXT = "whatever you need"
```
