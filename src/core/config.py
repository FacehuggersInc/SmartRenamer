"""
core.config — Where this tool stores its saved patterns and remembered
folder pairs (~/.config on Linux/macOS, %APPDATA% on Windows).
"""

import os
from pathlib import Path

from .filesystem import IS_WINDOWS


def _config_dir() -> Path:
    """
    Where this tool stores its saved patterns. Uses the conventional
    location on each OS: %APPDATA%\\rename_media on Windows,
    ~/.config/rename_media on Linux/macOS.
    """
    if IS_WINDOWS:
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
        return base / "rename_media"
    return Path.home() / ".config" / "rename_media"
