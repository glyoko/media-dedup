from __future__ import annotations

from pathlib import Path
from typing import Callable

import pytest
from PIL import Image

from media_dedup.fingerprint import fingerprint
from media_dedup.media import MediaFile, media_kind


@pytest.fixture
def save(tmp_path: Path) -> Callable[..., Path]:
    """save(img, "sub/name.jpg", **pil_kwargs) -> path under tmp_path."""

    def _save(img: Image.Image, name: str, **kwargs) -> Path:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        img.save(path, **kwargs)
        return path

    return _save


def media_file(path: Path, root: Path | None = None, fingerprinted: bool = True) -> MediaFile:
    st = path.stat()
    kind = media_kind(path)
    assert kind is not None
    mf = MediaFile(path=path, root=root or path.parent, kind=kind, size=st.st_size, mtime=st.st_mtime)
    if fingerprinted:
        fingerprint(mf)
    return mf


@pytest.fixture
def make_media() -> Callable[..., MediaFile]:
    return media_file
