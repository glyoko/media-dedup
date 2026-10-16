"""Perceptual signatures for single images (or single video frames).

A :class:`Signature` bundles a few complementary fingerprints:

* ``phash`` -- 64-bit DCT perceptual hash. Robust to resizing and re-compression;
  good for finding *candidates*.
* ``dhash`` -- 64-bit gradient (difference) hash. Cheap second opinion that
  catches some phash collisions.
* ``gray`` / ``color`` -- small thumbnails. Hashes are blind to small
  *localised* edits (a different meme caption, a watermark), so before declaring
  two images certainly identical we diff the grayscale thumbnails block-by-block
  and look for any region that changed a lot, and compare overall colour.

Comparing two signatures yields a :class:`MatchLevel`: ``CERTAIN`` (safe to
remove the lesser copy automatically), ``POSSIBLE`` (ask the user) or ``NONE``.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image, ImageOps

HASH_BITS = 64
GRAY_SIZE = 128
BLOCK_SIZE = 4
COLOR_SIZE = 16

# --- Thresholds -------------------------------------------------------------
# These were calibrated against the images in tests/data/images and a set of
# synthetic degradations (resize, JPEG re-compression, format conversion,
# captions, watermarks, crops, flips...). See tests/test_signature.py.

#: Max phash Hamming distance (out of 64) for a CERTAIN match.
PHASH_CERTAIN = 6
#: Max dhash Hamming distance (out of 64) for a CERTAIN match.
DHASH_CERTAIN = 10
#: Max phash Hamming distance for a POSSIBLE match (user gets asked).
PHASH_POSSIBLE = 14
#: Max mean difference (0-255) of any 4x4 block of the 128x128 grayscale
#: thumbnails for a CERTAIN match. Benign degradations (even JPEG quality 8
#: or several rounds of resize + re-compress) stay below ~7; captions and
#: watermarks measured >= ~21.
LOCAL_DIFF_CERTAIN = 12.0
#: Max mean absolute colour difference (0-255) for a CERTAIN match.
COLOR_DIFF_CERTAIN = 10.0
#: Aspect ratios within this factor of each other are considered equal.
ASPECT_TOLERANCE = 1.03
#: Grayscale std-dev below which an image is "flat" (hashes are meaningless).
FLAT_STDDEV = 4.0
#: Max mean colour difference for two flat images to be a POSSIBLE match.
FLAT_COLOR_DIFF = 6.0


class MatchLevel(enum.IntEnum):
    NONE = 0
    POSSIBLE = 1
    CERTAIN = 2


@dataclass(frozen=True)
class Match:
    level: MatchLevel
    phash_distance: int = HASH_BITS
    dhash_distance: int = HASH_BITS
    local_diff: float = 255.0
    color_diff: float = 255.0

    @property
    def similarity(self) -> float:
        """A human-friendly 0-100 score derived from the phash distance."""
        return round(100.0 * (1.0 - self.phash_distance / HASH_BITS), 1)


@dataclass(frozen=True, eq=False)
class Signature:
    phash: int
    dhash: int
    gray: np.ndarray  # (GRAY_SIZE, GRAY_SIZE) uint8
    color: np.ndarray  # (COLOR_SIZE, COLOR_SIZE, 3) uint8, RGB
    width: int
    height: int
    flat: bool

    @property
    def aspect(self) -> float:
        return self.width / self.height


def _bits_to_int(bits: np.ndarray) -> int:
    return int.from_bytes(np.packbits(bits.astype(np.uint8).ravel()).tobytes(), "big")


def to_rgb_array(img: Image.Image) -> np.ndarray:
    """Normalise a PIL image to an RGB uint8 array.

    Applies EXIF orientation and composites any transparency onto white, so a
    PNG with alpha and its flattened JPEG twin produce the same signature.
    """
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
        img = img.convert("RGBA")
        background = Image.new("RGBA", img.size, (255, 255, 255, 255))
        img = Image.alpha_composite(background, img)
    return np.asarray(img.convert("RGB"))


def signature_from_array(rgb: np.ndarray, size: tuple[int, int] | None = None) -> Signature:
    """Compute a signature from an (H, W, 3) RGB uint8 array.

    ``size`` is the (width, height) of the original media, if ``rgb`` was
    decoded at reduced resolution (e.g. JPEG draft mode).
    """
    if rgb.shape[0] == 0 or rgb.shape[1] == 0:
        raise ValueError("empty image")
    width, height = size if size else (rgb.shape[1], rgb.shape[0])
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)

    # pHash: low-frequency DCT coefficients compared to their median.
    small = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA).astype(np.float32)
    low = cv2.dct(small)[:8, :8].ravel()
    phash = _bits_to_int(low > np.median(low[1:]))

    # dHash: horizontal gradient sign on a 9x8 grid.
    tiny = cv2.resize(gray, (9, 8), interpolation=cv2.INTER_AREA).astype(np.int16)
    dhash = _bits_to_int(tiny[:, 1:] > tiny[:, :-1])

    return Signature(
        phash=phash,
        dhash=dhash,
        gray=cv2.resize(gray, (GRAY_SIZE, GRAY_SIZE), interpolation=cv2.INTER_AREA),
        color=cv2.resize(rgb, (COLOR_SIZE, COLOR_SIZE), interpolation=cv2.INTER_AREA),
        width=width,
        height=height,
        flat=float(small.std()) < FLAT_STDDEV,
    )


def signature_from_image(img: Image.Image, size: tuple[int, int] | None = None) -> Signature:
    return signature_from_array(to_rgb_array(img), size)


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def local_diff(a: Signature, b: Signature, min_blur: float = 0.8) -> float:
    """Largest mean difference of any small block between the two thumbnails.

    Both thumbnails are blurred first -- more so when one source is small, since
    upscaling a small image to 128px leaves it soft -- so resampling and JPEG
    noise average out, while genuine content changes (text, logos, objects)
    stay concentrated in a few blocks and stand out.
    """
    smallest = min(a.width, a.height, b.width, b.height)
    sigma = max(min_blur, GRAY_SIZE / smallest) if smallest < 2 * GRAY_SIZE else min_blur
    ga = cv2.GaussianBlur(a.gray.astype(np.float32), (0, 0), sigma)
    gb = cv2.GaussianBlur(b.gray.astype(np.float32), (0, 0), sigma)
    n = GRAY_SIZE // BLOCK_SIZE
    blocks = np.abs(ga - gb).reshape(n, BLOCK_SIZE, n, BLOCK_SIZE).mean(axis=(1, 3))
    return float(blocks.max())


def color_diff(a: Signature, b: Signature) -> float:
    """Mean absolute difference (0-255) of the small colour thumbnails."""
    return float(np.abs(a.color.astype(np.int16) - b.color.astype(np.int16)).mean())


def aspects_match(a: float, b: float) -> bool:
    return max(a, b) / min(a, b) <= ASPECT_TOLERANCE


def compare(a: Signature, b: Signature, min_blur: float = 0.8) -> Match:
    """Classify how likely it is that two signatures show the same picture.

    ``min_blur`` is the minimum Gaussian sigma (in thumbnail pixels) applied
    before looking for local differences; video frames use a larger value
    because frames sampled from two encodes are never perfectly aligned in time.
    """
    if a.flat or b.flat:
        # Solid-colour images all hash alike; only the colour is informative,
        # and that's not enough to delete anything without asking.
        if not (a.flat and b.flat):
            return Match(MatchLevel.NONE)
        color = color_diff(a, b)
        level = MatchLevel.POSSIBLE if color <= FLAT_COLOR_DIFF else MatchLevel.NONE
        return Match(level, 0, 0, 0.0, color)

    pd = hamming(a.phash, b.phash)
    if pd > PHASH_POSSIBLE:
        return Match(MatchLevel.NONE, pd)
    dd = hamming(a.dhash, b.dhash)
    local, color = local_diff(a, b, min_blur), color_diff(a, b)
    certain = (
        pd <= PHASH_CERTAIN
        and dd <= DHASH_CERTAIN
        and local <= LOCAL_DIFF_CERTAIN
        and color <= COLOR_DIFF_CERTAIN
        and aspects_match(a.aspect, b.aspect)
    )
    level = MatchLevel.CERTAIN if certain else MatchLevel.POSSIBLE
    return Match(level, pd, dd, local, color)
