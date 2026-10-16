"""Find groups of duplicate files.

Three kinds of group come out of here:

``exact``
    Byte-identical files (same SHA-256). Always safe to collapse.
``similar``
    Files that are visually the same picture/video at possibly different
    resolution or compression (every member is a CERTAIN match for the keeper).
    The best-quality copy is kept and the rest are removed automatically.
``uncertain``
    Files that look related but might differ in a meaningful way (a caption,
    watermark, crop, colour change...). The user decides.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Literal

import numpy as np

from . import fingerprint
from .media import MediaFile
from .quality import rank
from .signature import DEFAULT_LIMITS, Limits, Match, MatchLevel, compare

GroupKind = Literal["exact", "similar", "uncertain"]

#: Videos whose durations differ by more than this many seconds, or by more
#: than this fraction, are never considered the same.
DURATION_TOLERANCE_S = 1.0
DURATION_TOLERANCE_FRAC = 0.05
#: Frames sampled from two encodes of a video are never perfectly aligned in
#: time (e.g. 24 vs 30 fps), so motion shifts edges slightly between them. A
#: stronger blur absorbs that while still exposing captions and watermarks.
VIDEO_MIN_BLUR = 2.5


@dataclass
class DuplicateGroup:
    kind: GroupKind
    files: list[MediaFile]  # best-first; files[0] is the suggested keeper
    # How each non-keeper compares to the keeper (absent for exact groups).
    matches: dict[MediaFile, Match] = field(default_factory=dict)
    # The hash limits the group was found with (for --debug).
    limits: Limits = DEFAULT_LIMITS

    @property
    def keeper(self) -> MediaFile:
        return self.files[0]

    @property
    def others(self) -> list[MediaFile]:
        return self.files[1:]


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[MediaFile, MediaFile] = {}

    def find(self, x: MediaFile) -> MediaFile:
        self.parent.setdefault(x, x)
        while self.parent[x] is not x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: MediaFile, b: MediaFile) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra is not rb:
            self.parent[rb] = ra

    def groups(self) -> list[list[MediaFile]]:
        out: dict[MediaFile, list[MediaFile]] = defaultdict(list)
        for x in list(self.parent):
            out[self.find(x)].append(x)
        return [g for g in out.values() if len(g) > 1]


# --- Exact duplicates --------------------------------------------------------


def find_exact(files: Iterable[MediaFile]) -> list[DuplicateGroup]:
    """Group byte-identical files. Only same-size files are ever hashed."""
    by_size: dict[int, list[MediaFile]] = defaultdict(list)
    for mf in files:
        by_size[mf.size].append(mf)
    groups = []
    for same_size in by_size.values():
        if len(same_size) < 2:
            continue
        by_hash: dict[str, list[MediaFile]] = defaultdict(list)
        for mf in same_size:
            try:
                by_hash[fingerprint.sha256(mf.path)].append(mf)
            except OSError:
                continue
        groups.extend(DuplicateGroup("exact", rank(g)) for g in by_hash.values() if len(g) > 1)
    return groups


# --- Visual similarity -------------------------------------------------------


def compare_files(a: MediaFile, b: MediaFile, limits: Limits = DEFAULT_LIMITS) -> Match:
    """Compare two fingerprinted files of the same kind."""
    if a.kind != b.kind:
        return Match(MatchLevel.NONE)
    if a.kind == "image":
        return compare(a.signatures[0], b.signatures[0], limits=limits)
    return _compare_videos(a, b, limits)


def _durations_match(a: MediaFile, b: MediaFile) -> bool:
    if not a.duration or not b.duration:
        return True  # unknown; let the frames decide
    diff = abs(a.duration - b.duration)
    return diff <= DURATION_TOLERANCE_S or diff <= DURATION_TOLERANCE_FRAC * max(a.duration, b.duration)


def _compare_videos(a: MediaFile, b: MediaFile, limits: Limits = DEFAULT_LIMITS) -> Match:
    """Compare videos frame-by-frame at the same relative sample positions."""
    if not _durations_match(a, b):
        return Match(MatchLevel.NONE)
    pairs = [(sa, sb) for sa, sb in zip(a.signatures, b.signatures) if sa is not None and sb is not None]
    if not pairs:
        return Match(MatchLevel.NONE)
    results = [compare(sa, sb, VIDEO_MIN_BLUR, limits) for sa, sb in pairs]
    # Frames that are blank in both videos (fades, black frames) carry no
    # information about whether the videos are the same.
    informative = [m for m, (sa, sb) in zip(results, pairs) if not (sa.flat and sb.flat)]
    if not informative:
        level = MatchLevel.POSSIBLE if all(m.level for m in results) else MatchLevel.NONE
        return Match(level, 0, 0, 0.0, max(m.color_diff for m in results))

    n_possible = sum(m.level >= MatchLevel.POSSIBLE for m in informative)
    if (
        all(m.level is MatchLevel.CERTAIN for m in informative)
        and len(informative) * 2 >= len(results)
        and len(results) == len(a.signatures) == len(b.signatures)
    ):
        level = MatchLevel.CERTAIN
    elif n_possible * 2 >= len(informative):
        level = MatchLevel.POSSIBLE
    else:
        level = MatchLevel.NONE
    return Match(
        level,
        phash_distance=round(sum(m.phash_distance for m in informative) / len(informative)),
        dhash_distance=round(sum(m.dhash_distance for m in informative) / len(informative)),
        local_diff=max(m.local_diff for m in informative),
        color_diff=max(m.color_diff for m in informative),
    )


def _image_candidates(images: list[MediaFile], max_phash: int) -> Iterable[tuple[MediaFile, MediaFile]]:
    """Pairs of images whose phashes are close enough to be worth comparing."""
    informative = [mf for mf in images if not mf.signatures[0].flat]
    flat = [mf for mf in images if mf.signatures[0].flat]
    if informative:
        hashes = np.array([mf.signatures[0].phash for mf in informative], dtype=np.uint64)
        for i in range(len(informative) - 1):
            distances = np.bitwise_count(hashes[i + 1 :] ^ hashes[i])
            for j in np.nonzero(distances <= max_phash)[0]:
                yield informative[i], informative[i + 1 + int(j)]
    for i, a in enumerate(flat):
        for b in flat[i + 1 :]:
            yield a, b


def _video_candidates(videos: list[MediaFile]) -> Iterable[tuple[MediaFile, MediaFile]]:
    """Pairs of videos with compatible durations (sweep over sorted durations)."""
    known = sorted((v for v in videos if v.duration), key=lambda v: v.duration)
    unknown = [v for v in videos if not v.duration]
    for i, a in enumerate(known):
        for b in known[i + 1 :]:
            if not _durations_match(a, b):
                break
            yield a, b
    for i, a in enumerate(unknown):
        for b in unknown[i + 1 :] + known:
            yield a, b


def find_similar(files: Iterable[MediaFile], limits: Limits = DEFAULT_LIMITS) -> list[DuplicateGroup]:
    """Group visually similar fingerprinted files into similar/uncertain groups."""
    files = [mf for mf in files if mf.signatures]
    edges: dict[tuple[MediaFile, MediaFile], Match] = {}
    pairs = list(_image_candidates([f for f in files if f.kind == "image"], limits.phash))
    pairs += _video_candidates([f for f in files if f.kind == "video"])
    for a, b in pairs:
        m = compare_files(a, b, limits)
        if m.level > MatchLevel.NONE:
            edges[(a, b)] = m

    def match_between(a: MediaFile, b: MediaFile) -> Match:
        return edges.get((a, b)) or edges.get((b, a)) or compare_files(a, b, limits)

    # 1. Clusters linked by CERTAIN matches. To guard against chains (A~B~C
    #    where A and C differ), only members that are a CERTAIN match for the
    #    chosen keeper itself stay in the group.
    certain_uf = _UnionFind()
    for (a, b), m in edges.items():
        if m.level is MatchLevel.CERTAIN:
            certain_uf.union(a, b)

    groups: list[DuplicateGroup] = []
    representative: dict[MediaFile, MediaFile] = {}  # removed file -> its keeper
    for cluster in certain_uf.groups():
        ranked = rank(cluster)
        keeper = ranked[0]
        matches = {o: match_between(keeper, o) for o in ranked[1:]}
        members = [keeper] + [o for o in ranked[1:] if matches[o].level is MatchLevel.CERTAIN]
        if len(members) > 1:
            groups.append(
                DuplicateGroup("similar", members, {o: matches[o] for o in members[1:]}, limits)
            )
            for o in members[1:]:
                representative[o] = keeper

    # 2. Everything else that's linked at all becomes an "uncertain" group, made
    #    of the survivors of step 1 (files already slated for removal are
    #    represented by their keeper).
    def rep(mf: MediaFile) -> MediaFile:
        return representative.get(mf, mf)

    #    The same anti-chaining rule applies: union-find over POSSIBLE links
    #    would otherwise chain hundreds of unrelated files that merely share a
    #    layout (e.g. screenshots) into one group, so each group holds only
    #    files that themselves match its keeper.
    possible_uf = _UnionFind()
    neighbours: dict[MediaFile, set[MediaFile]] = defaultdict(set)
    for a, b in edges:
        ra, rb = rep(a), rep(b)
        if ra is not rb:
            possible_uf.union(ra, rb)
            neighbours[ra].add(rb)
            neighbours[rb].add(ra)
    for cluster in possible_uf.groups():
        remaining = rank(cluster)
        while len(remaining) > 1:
            keeper = remaining.pop(0)
            matches = {
                o: m
                for o in remaining
                if o in neighbours[keeper] and (m := match_between(keeper, o)).level > MatchLevel.NONE
            }
            if matches:
                members = [keeper] + list(matches)
                groups.append(DuplicateGroup("uncertain", members, matches, limits))
                remaining = [o for o in remaining if o not in matches]
    return groups
