from __future__ import annotations

import hashlib

import cv2
import pytest
from PIL import Image

from media_dedup import fingerprint as fp
from media_factory import load, write_video


def test_sha256(tmp_path):
    path = tmp_path / "f.bin"
    path.write_bytes(b"hello" * 1000)
    assert fp.sha256(path) == hashlib.sha256(b"hello" * 1000).hexdigest()


def test_image_metadata(save, make_media):
    mf = make_media(save(load(0).resize((1000, 750)), "big.png"))
    assert (mf.width, mf.height, mf.format) == (1000, 750, "PNG")
    assert len(mf.signatures) == 1
    # Decoded at reduced size, but the signature knows the real size.
    assert (mf.signatures[0].width, mf.signatures[0].height) == (1000, 750)


def test_exif_rotation_swaps_reported_size(tmp_path, make_media):
    exif = Image.Exif()
    exif[0x0112] = 6
    path = tmp_path / "sideways.jpg"
    load(0).rotate(90, expand=True).save(path, exif=exif)  # stored 480x640
    mf = make_media(path)
    assert (mf.width, mf.height) == (640, 480)


def test_corrupt_image_raises(tmp_path, make_media):
    path = tmp_path / "broken.jpg"
    path.write_bytes(b"definitely not a jpeg")
    with pytest.raises(Exception):
        make_media(path)


def test_video_metadata(tmp_path, make_media):
    mf = make_media(write_video(tmp_path / "v.mp4", load(0), size=(320, 240), frames=48, fps=24))
    assert (mf.width, mf.height, mf.format) == (320, 240, "MP4")
    assert mf.duration == pytest.approx(2.0, abs=0.1)
    assert len(mf.signatures) == fp.VIDEO_SAMPLES
    assert all(mf.signatures)


def test_unreadable_video_raises(tmp_path, make_media):
    path = tmp_path / "broken.mp4"
    path.write_bytes(b"\x00" * 1000)
    with pytest.raises(ValueError):
        make_media(path)


def test_video_without_frame_count_is_counted(tmp_path, make_media, monkeypatch):
    path = write_video(tmp_path / "v.mp4", load(0), frames=24)
    real_capture = cv2.VideoCapture

    class NoCountCapture:  # wraps (not subclasses) the C++ type
        def __init__(self, *args):
            self._cap = real_capture(*args)

        def get(self, prop):
            return 0 if prop == cv2.CAP_PROP_FRAME_COUNT else self._cap.get(prop)

        def __getattr__(self, name):
            return getattr(self._cap, name)

    monkeypatch.setattr(fp.cv2, "VideoCapture", NoCountCapture)
    mf = make_media(path)
    assert mf.duration == pytest.approx(1.0, abs=0.1)
    assert all(mf.signatures)


def test_sample_indices_are_time_based():
    # Same 2-second clip at 24 and 30 fps: samples land at the same moments.
    at24 = [i / 24 for i in fp._sample_indices(48, 24.0, 10)]
    at30 = [i / 30 for i in fp._sample_indices(60, 30.0, 10)]
    assert all(abs(a - b) <= 1 / 24 for a, b in zip(at24, at30))
    assert fp._sample_indices(10, 0.0, 5) == [1, 3, 5, 7, 9]
    assert max(fp._sample_indices(3, 30.0, 10)) <= 2


def test_fingerprint_all_reports_progress_and_errors(tmp_path, save, make_media):
    good = make_media(save(load(0), "ok.png"), fingerprinted=False)
    bad_path = tmp_path / "bad.png"
    bad_path.write_bytes(b"nope")
    bad = make_media(bad_path, fingerprinted=False)
    progress, errors = [], []
    ok = fp.fingerprint_all(
        [good, bad],
        jobs=2,
        on_progress=lambda done, total: progress.append((done, total)),
        on_error=lambda mf, exc: errors.append(mf),
    )
    assert ok == [good]
    assert errors == [bad]
    assert progress == [(1, 2), (2, 2)]


def test_undecodable_frames_leave_empty_slots(tmp_path, make_media, monkeypatch):
    path = write_video(tmp_path / "v.mp4", load(0))
    real_read = fp._read_frame
    monkeypatch.setattr(fp, "_read_frame", lambda cap, i: None if i < 5 else real_read(cap, i))
    mf = make_media(path)
    assert mf.signatures[0] is None
    assert all(mf.signatures[1:])

    monkeypatch.setattr(fp, "_read_frame", lambda cap, i: None)
    with pytest.raises(ValueError, match="could not decode any frames"):
        make_media(path)
