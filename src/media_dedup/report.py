"""Human-readable formatting of files and groups."""

from __future__ import annotations

import os
from pathlib import Path

import cv2
import numpy as np

from . import __version__
from . import signature as sig_mod
from .fingerprint import sha256
from .matching import (
    DURATION_TOLERANCE_FRAC,
    DURATION_TOLERANCE_S,
    VIDEO_MIN_BLUR,
    DuplicateGroup,
    _durations_match,
)
from .media import MediaFile
from .signature import DEFAULT_LIMITS, Limits, MatchLevel, Signature


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


def format_group(
    group: DuplicateGroup, keep: list[MediaFile] | None = None, numbered: bool = False, debug: bool = False
) -> str:
    """Render a group with a keep/remove action per file.

    ``keep`` defaults to the suggested keeper only. With ``numbered`` (used when
    prompting) files are numbered instead and the suggestion is marked. With
    ``debug``, the fingerprints and comparison details are appended.
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
    if debug:
        lines.append(format_debug(group))
    return "\n".join(lines)


# --- Debug output (--debug) ----------------------------------------------------

#: compare()'s default blur for still images.
_IMAGE_BLUR = 0.8


def debug_header(limits: Limits = DEFAULT_LIMITS) -> str:
    """Where the running code lives and the thresholds in force."""
    S = sig_mod
    overridden = [
        flag for flag, value, default in (
            ("--phash", limits.phash, S.PHASH_POSSIBLE), ("--dhash", limits.dhash, S.DHASH_POSSIBLE)
        ) if value != default
    ]
    return "\n".join([
        f"[debug] media-dedup {__version__} from {Path(__file__).parent}",
        f"[debug] CERTAIN needs:  pHash <= {S.PHASH_CERTAIN}, dHash <= {S.DHASH_CERTAIN}, "
        f"local diff <= {S.LOCAL_DIFF_CERTAIN:g}, colour diff <= {S.COLOR_DIFF_CERTAIN:g}, "
        f"aspect within x{S.ASPECT_TOLERANCE:g}, neither flat nor low contrast",
        f"[debug] POSSIBLE needs: pHash <= {limits.phash}, dHash <= {limits.dhash} "
        f"(two flat images: colour diff <= {S.FLAT_COLOR_DIFF:g}, hashes ignored)"
        + (f"  [set by {' and '.join(overridden)}]" if overridden else ""),
        f"[debug] flat: 128px std < {S.FLAT_STDDEV:g}; low contrast: 32px std < {S.LOW_CONTRAST_STDDEV:g}; "
        f"videos: durations within {DURATION_TOLERANCE_S:g} s or {DURATION_TOLERANCE_FRAC:.0%}, "
        f"frame blur >= {VIDEO_MIN_BLUR:g}",
    ])


def _signature_lines(sig: Signature) -> list[str]:
    small = cv2.resize(sig.gray, (32, 32), interpolation=cv2.INTER_AREA).astype(np.float32)
    flags = [name for name, on in (("FLAT", sig.flat), ("LOW CONTRAST", sig.low_contrast)) if on]
    return [
        f"pHash {sig.phash:016x}  {sig.phash:064b}",
        f"dHash {sig.dhash:016x}  {sig.dhash:064b}",
        f"contrast: 128px std {sig.gray.std():.2f}, 32px std {small.std():.2f}"
        + (f"  [{', '.join(flags)}]" if flags else ""),
    ]


def _explain(
    a: Signature, b: Signature, min_blur: float, limits: Limits = DEFAULT_LIMITS
) -> tuple[list[str], str]:
    """Each measurement behind compare(a, b) against its threshold, and why it
    came out at the level it did. Distances are the real ones, even for flat
    pairs (whose Match reports 0)."""
    S = sig_mod
    level = S.compare(a, b, min_blur, limits).level
    pd, dd = S.hamming(a.phash, b.phash), S.hamming(a.dhash, b.dhash)
    local, color = S.local_diff(a, b, min_blur), S.color_diff(a, b)
    checks = [
        (f"pHash distance {pd} (certain <= {S.PHASH_CERTAIN}, possible <= {limits.phash})",
         pd <= S.PHASH_CERTAIN),
        (f"dHash distance {dd} (certain <= {S.DHASH_CERTAIN}, possible <= {limits.dhash})",
         dd <= S.DHASH_CERTAIN),
        (f"local diff {local:.1f} (certain <= {S.LOCAL_DIFF_CERTAIN:g})", local <= S.LOCAL_DIFF_CERTAIN),
        (f"colour diff {color:.1f} (certain <= {S.COLOR_DIFF_CERTAIN:g})", color <= S.COLOR_DIFF_CERTAIN),
        (f"aspect {a.aspect:.3f} vs {b.aspect:.3f}", S.aspects_match(a.aspect, b.aspect)),
    ]
    lines = [f"{'ok  ' if ok else 'FAIL'} {text}" for text, ok in checks]

    if a.flat and b.flat:
        why = f"both flat, so hashes are ignored; matched on colour only (possible <= {S.FLAT_COLOR_DIFF:g})"
    elif a.flat or b.flat:
        why = "only one of them is flat"
    elif pd > limits.phash:
        why = f"pHash distance {pd} > {limits.phash}"
    elif dd > limits.dhash:
        why = f"dHash distance {dd} > {limits.dhash}"
    elif level is MatchLevel.CERTAIN:
        why = "every check passed"
    else:
        failed = [text.split(" (")[0] for text, ok in checks if not ok]
        if a.low_contrast or b.low_contrast:
            failed.append("low contrast")
        why = "both hashes within POSSIBLE limits; not certain: " + ", ".join(failed)
    return lines, f"{level.name}: {why}"


def _video_lines(keeper: MediaFile, other: MediaFile, limits: Limits = DEFAULT_LIMITS) -> list[str]:
    lines = [
        f"duration {keeper.duration:.2f} s vs {other.duration:.2f} s"
        + ("" if _durations_match(keeper, other) else "  (too different: never compared)")
    ]
    for i, (a, b) in enumerate(zip(keeper.signatures, other.signatures)):
        if a is None or b is None:
            lines.append(f"frame {i}: not decoded in {'the keeper' if a is None else 'this file'}")
            continue
        _, verdict = _explain(a, b, VIDEO_MIN_BLUR, limits)
        lines.append(
            f"frame {i}: pHash {sig_mod.hamming(a.phash, b.phash):2}  dHash {sig_mod.hamming(a.dhash, b.dhash):2}  "
            f"local {sig_mod.local_diff(a, b, VIDEO_MIN_BLUR):5.1f}  colour {sig_mod.color_diff(a, b):5.1f}  {verdict}"
        )
    return lines


def format_debug(group: DuplicateGroup) -> str:
    """Raw fingerprints of each file, and every check of each file against the keeper."""
    out = ["  [debug]"]
    if group.kind == "exact":
        try:
            digest = sha256(group.keeper.path)
        except OSError as exc:
            digest = f"unreadable ({exc})"
        out.append(f"    byte-identical: same size ({group.keeper.size} B) and SHA-256 {digest}")
        return "\n".join(out)

    keeper = group.keeper
    for i, mf in enumerate(group.files, start=1):
        details = [mf.kind, f"{mf.width}x{mf.height}"] + ([f"mode {mf.mode}"] if mf.mode else [])
        out.append(f"    {i}) {display_path(mf.path)}  ({', '.join(details)})" + ("  keeper" if mf is keeper else ""))
        body: list[str] = []
        if mf.kind == "image" and mf.signatures:
            body += _signature_lines(mf.signatures[0])
        elif mf.kind == "video":
            frames = [s for s in mf.signatures if s is not None]
            body.append(
                f"{len(frames)}/{len(mf.signatures)} frames decoded, "
                f"{sum(s.flat for s in frames)} flat, {sum(s.low_contrast for s in frames)} low contrast"
            )
        if mf is not keeper and mf.signatures and keeper.signatures:
            if mf.kind == "image":
                lines, verdict = _explain(keeper.signatures[0], mf.signatures[0], _IMAGE_BLUR, group.limits)
                body += ["vs keeper:"] + [f"  {line}" for line in lines] + [f"  => {verdict}"]
            else:
                overall = group.matches[mf].level.name if mf in group.matches else "?"
                body += ["vs keeper:"] + [f"  {line}" for line in _video_lines(keeper, mf, group.limits)]
                body.append(f"  => {overall} overall (certain: every non-blank frame certain; "
                            "possible: at least half of them possible)")
        out += [f"       {line}" for line in body]
    return "\n".join(out)
