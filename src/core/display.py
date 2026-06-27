"""
core.display — Terminal colours, the screen frame (render()), input
helpers (ask/ask_yn), and the Back-navigation exception.
"""

import os



if os.name == "nt":
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        STD_OUTPUT_HANDLE = -11
        ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        handle = kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING)
    except Exception:
        # If this fails for any reason, the script still runs — colours
        # just won't render correctly on that particular console.
        pass

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

def clear_screen():
    """Clear the terminal. Falls back gracefully if not a real TTY."""
    os.system("cls" if os.name == "nt" else "clear")

_BATCH_CONTEXT: str | None = None

def render(*, title: str = "", context_lines: list[str] = None,
           sub: str = "") -> None:
    """
    Standard frame for every step: clears the screen, then prints a compact
    context block (title + any 'what's being built' lines) before the step's
    own content continues below. Every step should call this first.
    """
    clear_screen()
    if _BATCH_CONTEXT:
        context_lines = [f"🔁 {_BATCH_CONTEXT}"] + (context_lines or [])
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

class Back(Exception):
    pass

def _check_back(raw: str):
    if raw.strip().lower() in ("b", "back"):
        raise Back()

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
