"""Human-readable formatting of files and groups."""

from __future__ import annotations

import os
from pathlib import Path

from .matching import DuplicateGroup
from .media import MediaFile


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    raise AssertionError("unreachable")  # pragma: no cover


def display_path(path: Path) -> str:
    """Path relative to the working directory when that's shorter."""
    try:
        rel = os.path.relpath(path)
    except ValueError:  # pragma: no cover - different drive on Windows
        return str(path)
    return rel if len(rel) < len(str(path)) else str(path)


def describe(mf: MediaFile) -> str:
    parts = []
    if mf.width:
        parts.append(f"{mf.width}x{mf.height}")
    if mf.format:
        parts.append(mf.format)
    if mf.kind == "video" and mf.duration:
        minutes, seconds = divmod(round(mf.duration), 60)
        parts.append(f"{minutes}:{seconds:02d}")
    parts.append(human_size(mf.size))
    return ", ".join(parts)


def format_group(group: DuplicateGroup, keep: list[MediaFile] | None = None, numbered: bool = False) -> str:
    """Render a group with a keep/remove action per file.

    ``keep`` defaults to the suggested keeper only. With ``numbered`` (used when
    prompting) files are numbered instead and the suggestion is marked.
    """
    keep = keep if keep is not None else [group.keeper]
    if group.kind == "exact":
        title = "Identical files"
    elif group.kind == "similar":
        title = "Same image" if group.keeper.kind == "image" else "Same video"
    else:
        title = "Possible duplicates"
    lines = [f"{title} ({len(group.files)} files):"]
    width = max(len(display_path(mf.path)) for mf in group.files)
    for i, mf in enumerate(group.files, start=1):
        if numbered:
            lead = f"{i:>2})"
        else:
            lead = "keep  " if mf in keep else "remove"
        note = ""
        if mf in group.matches:
            note = f"  [{group.matches[mf].similarity:g}% similar]"
        elif numbered and mf is group.keeper:
            note = "  <- suggested"
        lines.append(f"  {lead}  {display_path(mf.path):<{width}}  ({describe(mf)}){note}")
    return "\n".join(lines)
