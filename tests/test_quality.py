from __future__ import annotations

from pathlib import Path

import pytest

from media_dedup.media import MediaFile
from media_dedup.quality import looks_like_copy, rank


def mf(name="a.jpg", width=100, height=100, size=1000, fmt="JPEG", mtime=0.0) -> MediaFile:
    return MediaFile(
        path=Path("/x") / name, root=Path("/x"), kind="image", size=size,
        mtime=mtime, width=width, height=height, format=fmt,
    )


def best(*files):
    return rank(files)[0]


def test_resolution_wins_over_size():
    big = mf("big.jpg", 200, 200, size=10)
    assert best(mf(size=10_000), big) is big


def test_size_breaks_resolution_ties():
    heavy = mf("heavy.jpg", size=5000)
    assert best(mf(size=1000), heavy) is heavy


def test_lossless_breaks_size_ties():
    png = mf("a.png", fmt="PNG")
    assert best(mf("a.jpg"), png) is png


def test_original_name_beats_copy_name():
    original = mf("cat.jpg")
    assert best(mf("cat (1).jpg"), original, mf("cat copy.jpg")) is original


def test_older_file_then_shorter_path():
    older = mf("b.jpg", mtime=1.0)
    assert best(mf("a.jpg", mtime=2.0), older) is older
    short = mf("ab.jpg")
    assert best(mf("abc.jpg"), short) is short


@pytest.mark.parametrize(
    "name, expected",
    [
        ("cat (1).jpg", True),
        ("cat copy.jpg", True),
        ("cat copy 2.jpg", True),
        ("cat-copy.jpg", True),
        ("cat_1.jpg", True),
        ("cat.jpg", False),
        ("IMG_1234.jpg", False),
        ("copycat.jpg", False),
    ],
)
def test_looks_like_copy(name, expected):
    assert looks_like_copy(mf(name)) is expected
