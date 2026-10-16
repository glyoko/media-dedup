from __future__ import annotations

import os
from pathlib import Path

from PIL import Image

from media_dedup.media import media_kind, scan


def touch_image(path: Path, color="red") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), color).save(path)
    return path


def names(files) -> list[str]:
    return sorted(str(mf.path.relative_to(mf.root)) for mf in files)


def test_media_kind():
    assert media_kind(Path("a.JPG")) == "image"
    assert media_kind(Path("a.webp")) == "image"
    assert media_kind(Path("clip.MOV")) == "video"
    assert media_kind(Path("notes.txt")) is None
    assert media_kind(Path("noext")) is None


def test_scan_is_recursive_by_default(tmp_path):
    touch_image(tmp_path / "a.png")
    touch_image(tmp_path / "sub" / "deeper" / "b.jpg")
    (tmp_path / "notes.txt").write_text("hi")
    assert names(scan([tmp_path])) == ["a.png", "sub/deeper/b.jpg"]


def test_scan_non_recursive(tmp_path):
    touch_image(tmp_path / "a.png")
    touch_image(tmp_path / "sub" / "b.jpg")
    assert names(scan([tmp_path], recursive=False)) == ["a.png"]


def test_scan_skips_hidden_symlinks_and_excluded(tmp_path):
    real = touch_image(tmp_path / "a.png")
    touch_image(tmp_path / ".hidden.png")
    touch_image(tmp_path / "._a.png")  # macOS resource fork
    touch_image(tmp_path / ".cache" / "c.png")
    touch_image(tmp_path / "quarantine" / "d.png")
    (tmp_path / "link.png").symlink_to(real)
    found = scan([tmp_path], exclude=[tmp_path / "quarantine"])
    assert names(found) == ["a.png"]


def test_scan_counts_hard_links_and_repeated_paths_once(tmp_path):
    real = touch_image(tmp_path / "a.png")
    os.link(real, tmp_path / "b.png")
    found = scan([tmp_path, tmp_path, real])
    assert len(found) == 1


def test_scan_accepts_individual_files(tmp_path):
    img = touch_image(tmp_path / "x" / "a.png")
    (found,) = scan([img])
    assert found.root == img.parent
    assert found.kind == "image"
    assert found.size == img.stat().st_size


def test_media_file_basics(tmp_path, make_media):
    mf = make_media(touch_image(tmp_path / "a.png"))
    assert mf.pixels == 64
    assert "a.png" in repr(mf)
