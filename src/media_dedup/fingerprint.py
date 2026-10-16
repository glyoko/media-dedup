"""Decode media files and compute their content hashes and signatures."""

from __future__ import annotations

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Iterable

import cv2
from PIL import Image

from .media import MediaFile
from .signature import Signature, signature_from_array, signature_from_image

#: Number of frames sampled (evenly spaced, avoiding the very start and end).
VIDEO_SAMPLES = 10
#: JPEGs are decoded at reduced size (much faster); still far above the 128px
#: our signatures need.
DECODE_SIZE = (512, 512)
_CHUNK = 1 << 20

# Plenty of memes are bigger than PIL's default "decompression bomb" limit is
# comfortable with; we only ever decode at reduced size anyway.
Image.MAX_IMAGE_PIXELS = 400_000_000


def sha256(path: os.PathLike) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint_image(mf: MediaFile) -> None:
    with Image.open(mf.path) as img:
        width, height = img.size
        # EXIF orientations 5-8 rotate the image by 90 degrees.
        if img.getexif().get(0x0112, 1) in (5, 6, 7, 8):
            width, height = height, width
        mf.format = img.format or mf.path.suffix.lstrip(".").upper()
        mf.mode = img.mode
        img.draft("RGB", DECODE_SIZE)
        sig = signature_from_image(img, size=(width, height))
    mf.width, mf.height = width, height
    mf.signatures = [sig]


def _read_frame(cap: cv2.VideoCapture, index: int):
    cap.set(cv2.CAP_PROP_POS_FRAMES, index)
    ok, frame = cap.read()
    return frame if ok and frame is not None else None


def _sample_indices(count: int, fps: float, samples: int) -> list[int]:
    """Frame indices at evenly spaced *times*, so that copies re-encoded at a
    different frame rate are sampled at the same moments."""
    if fps <= 0:
        return [min(count - 1, int(count * (i + 0.5) / samples)) for i in range(samples)]
    duration = count / fps
    return [min(count - 1, round(duration * (i + 0.5) / samples * fps)) for i in range(samples)]


def fingerprint_video(mf: MediaFile, samples: int = VIDEO_SAMPLES) -> None:
    cap = cv2.VideoCapture(str(mf.path))
    try:
        if not cap.isOpened():
            raise ValueError("could not open video")
        fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
        count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if count <= 0:  # Some containers don't record a frame count.
            count = 0
            while cap.grab():
                count += 1
            cap.release()
            cap = cv2.VideoCapture(str(mf.path))
        if count <= 0:
            raise ValueError("video has no frames")

        # One slot per sample position (None if that frame wouldn't decode), so
        # that frame i of one video is always compared with frame i of another.
        signatures: list[Signature | None] = []
        width = height = 0
        for index in _sample_indices(count, fps, samples):
            frame = _read_frame(cap, index)
            if frame is None:
                signatures.append(None)
                continue
            height, width = frame.shape[:2]
            signatures.append(signature_from_array(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)))
        if not any(signatures):
            raise ValueError("could not decode any frames")
    finally:
        cap.release()

    mf.width, mf.height = width, height
    mf.duration = count / fps if fps > 0 else 0.0
    mf.format = mf.path.suffix.lstrip(".").upper()
    mf.signatures = signatures


def fingerprint(mf: MediaFile) -> None:
    if mf.kind == "image":
        fingerprint_image(mf)
    else:
        fingerprint_video(mf)


def fingerprint_all(
    files: Iterable[MediaFile],
    jobs: int | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    on_error: Callable[[MediaFile, Exception], None] | None = None,
) -> list[MediaFile]:
    """Fingerprint ``files`` in parallel; returns those that succeeded."""
    files = list(files)
    ok: list[MediaFile] = []

    def work(mf: MediaFile) -> Exception | None:
        try:
            fingerprint(mf)
        except Exception as exc:  # noqa: BLE001 - any undecodable file is skipped
            return exc
        return None

    with ThreadPoolExecutor(max_workers=jobs or os.cpu_count() or 4) as pool:
        for done, (mf, error) in enumerate(zip(files, pool.map(work, files)), start=1):
            if error is None:
                ok.append(mf)
            elif on_error:
                on_error(mf, error)
            if on_progress:
                on_progress(done, len(files))
    return ok
