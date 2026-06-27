"""
core.registry — lets utilities/*.py files register themselves for the
main menu without main.py needing to know they exist.

Each utilities/*.py file ends with a module-level list called
UTILITY_ENTRIES, built from UtilEntry(...). main.py never imports those
functions directly — it discovers every submodule of the utilities
package at startup, collects their UTILITY_ENTRIES lists, and uses
whatever it finds. Adding a new utility means writing one new file and
one new list entry in it; main.py itself never changes.
"""
import inspect
import pkgutil
import importlib
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class UtilEntry:
    label: str
    summary: str
    func: Callable
    example: str = ""

    @property
    def takes_folder(self) -> bool:
        """
        True if this utility's first required parameter is literally
        named 'folder' — meaning main.py should call pick_folder() and
        pass the result in. Anything else (no required params, or a
        differently-named one like 'source'/'root') means the utility
        manages its own folder picking, possibly more than one.
        """
        sig = inspect.signature(self.func)
        required = [
            p for p in sig.parameters.values()
            if p.default is inspect.Parameter.empty
        ]
        return len(required) == 1 and required[0].name == "folder"


def discover_utilities(package) -> list[UtilEntry]:
    """
    Import every submodule of `package` (e.g. the utilities package)
    and collect their UTILITY_ENTRIES lists, in a stable order: modules
    sorted alphabetically by name, entries within a module kept in the
    order they were declared.
    """
    entries: list[UtilEntry] = []
    mod_infos = sorted(
        pkgutil.iter_modules(package.__path__),
        key=lambda m: m.name,
    )
    for mod_info in mod_infos:
        module = importlib.import_module(f"{package.__name__}.{mod_info.name}")
        entries.extend(getattr(module, "UTILITY_ENTRIES", []))
    return entries
