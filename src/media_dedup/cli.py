"""Command line entry point: ``media-dedup [options] PATH...``, and
``media-dedup restore [DIR]`` to undo it.

``restore`` is recognised as the first argument rather than as an argparse
subcommand, so a plain ``media-dedup PATH`` keeps working; a folder that is
really named "restore" can still be scanned as ``./restore``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .fingerprint import fingerprint_all
from .matching import DuplicateGroup, find_exact, find_similar
from .media import MediaFile, scan
from .prompt import QuitRequested, ask
from .quarantine import DEFAULT_DIRNAME, Quarantine, default_directory, find_manifest, restore
from .report import debug_header, display_path, format_group, human_size


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="media-dedup",
        description=(
            "Find duplicate images and videos -- including resized and re-compressed "
            "copies -- and move the lesser copies into a quarantine folder. Files that "
            "might be duplicates (different caption, watermark, crop...) are shown to "
            "you to decide."
        ),
        epilog="To put removed files back, run: media-dedup restore [DIR]  (see media-dedup restore -h)",
    )
    parser.add_argument("paths", nargs="+", type=Path, metavar="PATH", help="folders (or files) to scan")
    parser.add_argument(
        "-d",
        "--dry-run",
        action="store_true",
        help="show what's duplicated and what would be removed, but don't remove anything",
    )
    parser.add_argument(
        "-n",
        "--non-interactive",
        action="store_true",
        help="never ask; leave possible duplicates alone (default when stdin isn't a terminal)",
    )
    parser.add_argument(
        "-c",
        "--checksum-only",
        action="store_true",
        help="only remove byte-identical copies (fast: skips image/video analysis)",
    )
    parser.add_argument(
        "--no-recursive",
        dest="recursive",
        action="store_false",
        help="only scan the top level of each folder",
    )
    parser.add_argument(
        "-q",
        "--quarantine-dir",
        type=Path,
        metavar="DIR",
        help="where removed files go (default: PATH/.media-dedup-removed, for the first PATH)",
    )
    parser.add_argument("-j", "--jobs", type=int, metavar="N", help="parallel decoders (default: CPU count)")
    parser.add_argument("-v", "--verbose", action="store_true", help="explain skipped files")
    parser.add_argument(
        "-D",
        "--debug",
        action="store_true",
        help="show each group's hashes and every comparison check, to see why files were grouped",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def build_restore_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="media-dedup restore",
        description=(
            "Move quarantined files back to where they were, using the quarantine's "
            "manifest.jsonl. Files whose original path is taken again are left in "
            "the quarantine, and nothing is ever overwritten."
        ),
    )
    parser.add_argument(
        "directory",
        nargs="?",
        type=Path,
        default=Path("."),
        metavar="DIR",
        help="the quarantine folder, or the folder that was scanned (default: current folder)",
    )
    parser.add_argument(
        "-o",
        "--only",
        action="append",
        default=[],
        metavar="PATTERN",
        help="only restore files whose name or path matches this glob, e.g. 'cat*.jpg' (repeatable)",
    )
    parser.add_argument("-d", "--dry-run", action="store_true", help="show what would be restored, change nothing")
    return parser


def _stdin_is_interactive() -> bool:
    return sys.stdin.isatty()


def _err(msg: str) -> None:
    print(msg, file=sys.stderr)


def _progress(done: int, total: int) -> None:
    if sys.stderr.isatty():
        end = "\n" if done == total else ""
        print(f"\rAnalysing {done}/{total}", end=end, file=sys.stderr, flush=True)


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["restore"]:
        return restore_main(argv[1:])
    parser = build_parser()
    args = parser.parse_args(argv)
    for path in args.paths:
        if not path.exists():
            parser.error(f"no such file or directory: {path}")
    if args.jobs is not None and args.jobs < 1:
        parser.error("--jobs must be at least 1")
    interactive = not args.non_interactive and not args.dry_run and _stdin_is_interactive()
    qdir = args.quarantine_dir or default_directory(args.paths)

    # 1. Discover.
    files = scan(args.paths, recursive=args.recursive, exclude=[qdir])
    n_images = sum(mf.kind == "image" for mf in files)
    _err(f"Found {_plural(n_images, 'image')} and {_plural(len(files) - n_images, 'video')}.")
    if not files:
        return 0

    # 2. Byte-identical copies (cheap; only same-size files are hashed). These
    #    are settled up front, so their extra copies are never decoded.
    exact = find_exact(files)

    # 3. Fingerprint the rest and look for visual duplicates.
    similar = [] if args.checksum_only else _find_visual(files, exact, args)
    automatic = exact + [g for g in similar if g.kind == "similar"]
    uncertain = [g for g in similar if g.kind == "uncertain"]

    if not automatic and not uncertain:
        print("No duplicates found.")
        return 0

    if args.debug:
        print(debug_header() + "\n")
    if args.dry_run:
        return _dry_run(automatic, uncertain, args.debug)
    return _apply(automatic, uncertain, Quarantine(qdir, args.paths), interactive, args.debug)


def _find_visual(
    files: list[MediaFile], exact: list[DuplicateGroup], args: argparse.Namespace
) -> list[DuplicateGroup]:
    exact_removed = {mf for g in exact for mf in g.others}
    errors: list[tuple[MediaFile, Exception]] = []
    fingerprinted = fingerprint_all(
        [mf for mf in files if mf not in exact_removed],
        jobs=args.jobs,
        on_progress=_progress,
        on_error=lambda mf, exc: errors.append((mf, exc)),
    )
    if errors:
        _err(f"Skipped {_plural(len(errors), 'file')} that couldn't be read" + (":" if args.verbose else " (-v for details)."))
        if args.verbose:
            for mf, exc in errors:
                _err(f"  {display_path(mf.path)}: {exc}")
    for group in exact:  # identical copies share the keeper's metadata
        for other in group.others:
            other.width, other.height = group.keeper.width, group.keeper.height
            other.format, other.duration = group.keeper.format, group.keeper.duration
    return find_similar(fingerprinted)


def restore_main(argv: list[str]) -> int:
    """Returns 1 if any selected file couldn't be put back."""
    parser = build_restore_parser()
    args = parser.parse_args(argv)
    manifest = find_manifest(args.directory)
    if manifest is None:
        parser.error(f"no quarantine manifest in {args.directory} or {args.directory / DEFAULT_DIRNAME}")
    result = restore(manifest, args.only, dry_run=args.dry_run)
    if not result.entries:
        print("Nothing in the quarantine matches." if args.only else "The quarantine manifest is empty.")
        return 0 if not args.only else 1

    restored = result.with_status("restored")
    verb = "Would restore" if args.dry_run else "Restored"
    for e in restored:
        print(f"{verb} {display_path(e.original)}")
    for e in result.with_status("taken"):
        _err(f"Skipped {display_path(e.quarantined)}: something is already at {display_path(e.original)}")
    for e in result.with_status("missing"):
        _err(f"Missing from the quarantine: {display_path(e.quarantined)} (was {display_path(e.original)})")
    for e in result.with_status("failed"):
        _err(f"Couldn't restore {display_path(e.original)}: {e.error}")
    if result.unreadable_lines:
        _err(f"Ignored {_plural(result.unreadable_lines, 'unreadable manifest line')}.")

    left = len(result.entries) - len(restored)
    print(
        f"{'Dry run: would restore' if args.dry_run else 'Restored'} {_plural(len(restored), 'file')}"
        + (f"; {left} couldn't be (see above)." if left else ".")
    )
    return 1 if left else 0


def _dry_run(automatic: list[DuplicateGroup], uncertain: list[DuplicateGroup], debug: bool = False) -> int:
    for group in automatic:
        print(format_group(group, debug=debug) + "\n")
    for group in uncertain:
        print("Would ask about these:")
        print(format_group(group, numbered=True, debug=debug) + "\n")
    removable = [mf for g in automatic for mf in g.others]
    print(
        f"Dry run: would remove {_plural(len(removable), 'file')} "
        f"({human_size(sum(mf.size for mf in removable))})"
        + (f"; {_plural(len(uncertain), 'group')} would need your decision." if uncertain else ".")
    )
    return 0


def _move_all(quarantine: Quarantine, files: list[MediaFile], kept: MediaFile, reason: str) -> None:
    for mf in files:
        try:
            quarantine.move(mf, kept, reason)
        except OSError as exc:
            _err(f"  Couldn't move {display_path(mf.path)}: {exc}")


def _apply(
    automatic: list[DuplicateGroup],
    uncertain: list[DuplicateGroup],
    quarantine: Quarantine,
    interactive: bool,
    debug: bool = False,
) -> int:
    for group in automatic:
        print(format_group(group, debug=debug) + "\n")
        _move_all(quarantine, group.others, group.keeper, group.kind)

    undecided = list(uncertain)
    if interactive:
        while undecided:
            group = undecided[0]
            try:
                keep = ask(group, debug=debug)
            except QuitRequested:
                print("Leaving the remaining possible duplicates alone.")
                break
            undecided.pop(0)
            _move_all(quarantine, [mf for mf in group.files if mf not in keep], keep[0], "user")
            print()
    elif undecided:
        for group in undecided:
            print("Not sure about these; leaving them alone:")
            print(format_group(group, numbered=True, debug=debug) + "\n")

    if quarantine.moved:
        print(
            f"Moved {_plural(len(quarantine.moved), 'file')} ({human_size(quarantine.bytes_moved)}) "
            f"to {display_path(quarantine.directory)}"
        )
        print(f"To undo: media-dedup restore {display_path(quarantine.directory)}")
    else:
        print("Nothing was removed.")
    if undecided:
        print(
            f"{_plural(len(undecided), 'group')} of possible duplicates left undecided"
            + ("." if interactive else "; run in a terminal without -n to decide.")
        )
    return 0
