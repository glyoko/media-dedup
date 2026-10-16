"""Ask the user what to do with an uncertain duplicate group."""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Callable, Iterable

from .matching import DuplicateGroup
from .media import MediaFile
from .report import format_group

HELP = """\
  Enter      keep the suggested file (1), remove the others
  2  /  1,3  keep only the listed file(s), remove the others
  a          keep all of them (they're not duplicates)
  v          open the files in your default viewer
  q          stop asking; leave this and any remaining groups alone
"""


class QuitRequested(Exception):
    """The user asked to stop resolving groups."""


def open_files(files: Iterable[MediaFile]) -> None:
    """Open files with the platform's default viewer."""
    paths = [str(mf.path) for mf in files]
    if sys.platform == "darwin":
        subprocess.run(["open", *paths], check=False)
    elif os.name == "nt":  # pragma: no cover - platform specific
        for p in paths:
            os.startfile(p)  # type: ignore[attr-defined]
    else:  # pragma: no cover - platform specific
        for p in paths:
            subprocess.run(["xdg-open", p], check=False)


def parse_choice(answer: str, count: int) -> list[int] | None:
    """Parse "2", "1,3" or "1 3" into 0-based indices; None if invalid."""
    tokens = answer.replace(",", " ").split()
    try:
        indices = sorted({int(t) - 1 for t in tokens})
    except ValueError:
        return None
    if not indices or any(i < 0 or i >= count for i in indices):
        return None
    return indices


def ask(
    group: DuplicateGroup,
    input_fn: Callable[[str], str] | None = None,
    output: Callable[[str], None] = print,
    opener: Callable[[Iterable[MediaFile]], None] | None = None,
    debug: bool = False,
) -> list[MediaFile]:
    """Return the files to keep (all of them means "not duplicates").

    Raises :class:`QuitRequested` on ``q`` or end-of-input.
    """
    input_fn = input_fn or input
    opener = opener or open_files
    output(format_group(group, numbered=True, debug=debug))
    while True:
        try:
            answer = input_fn("Keep which? [Enter=1, numbers, a=all, v=view, q=quit, ?=help] ").strip().lower()
        except EOFError:
            raise QuitRequested from None
        if answer == "":
            return [group.keeper]
        if answer in ("a", "all"):
            return list(group.files)
        if answer in ("q", "quit"):
            raise QuitRequested
        if answer in ("v", "view"):
            opener(group.files)
            continue
        if answer in ("?", "h", "help"):
            output(HELP)
            continue
        indices = parse_choice(answer, len(group.files))
        if indices is None:
            output(f"  Didn't understand {answer!r}.")
            output(HELP)
            continue
        return [group.files[i] for i in indices]
