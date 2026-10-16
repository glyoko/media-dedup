"""Calibration tests: every sample image, put through realistic transformations.

Benign degradations (resizing, re-compression, format changes) must be CERTAIN
matches, so they're cleaned up automatically. Meaningful edits (captions,
watermarks, crops, colour changes) must *never* be CERTAIN -- at worst the user
gets asked. Unrelated images must not match at all.
"""

from __future__ import annotations

import itertools

import numpy as np
import pytest
from PIL import Image, ImageEnhance, ImageOps

from media_dedup.signature import (
    Match,
    MatchLevel,
    aspects_match,
    compare,
    hamming,
    signature_from_array,
    signature_from_image,
    to_rgb_array,
)
from media_factory import SAMPLE_IMAGES, caption, crop, load, recompress, watermark

assert len(SAMPLE_IMAGES) >= 12, "tests/data/images should hold the sample images"

BENIGN = {
    "half size": lambda i: i.resize((i.width // 2, i.height // 2), Image.LANCZOS),
    "thumbnail": lambda i: i.resize((96, 72), Image.LANCZOS),
    "upscaled": lambda i: i.resize((i.width * 3 // 2, i.height * 3 // 2), Image.BICUBIC),
    "jpeg q30": lambda i: recompress(i, 30),
    "jpeg q8": lambda i: recompress(i, 8),
    "shared many times": lambda i: recompress(
        recompress(i.resize((i.width * 2 // 3, i.height * 2 // 3)), 50).resize((300, 225)), 35
    ),
}

EDITED = {
    "caption": caption,
    "short caption": lambda i: caption(i, "NOBODY:"),
    "watermark": watermark,
    "crop 5%": lambda i: crop(i, 0.05),
    "crop 15%": lambda i: crop(i, 0.15),
    "brighter": lambda i: ImageEnhance.Brightness(i).enhance(1.15),
    "mirrored": ImageOps.mirror,
}


@pytest.fixture(scope="module")
def originals():
    return [(load(i), signature_from_image(load(i))) for i in range(len(SAMPLE_IMAGES))]


@pytest.mark.parametrize("variant", BENIGN)
def test_benign_degradations_are_certain(originals, variant):
    for img, sig in originals:
        match = compare(sig, signature_from_image(BENIGN[variant](img)))
        assert match.level is MatchLevel.CERTAIN, (variant, match)


@pytest.mark.parametrize("variant", EDITED)
def test_edits_are_never_certain(originals, variant):
    for img, sig in originals:
        match = compare(sig, signature_from_image(EDITED[variant](img)))
        assert match.level is not MatchLevel.CERTAIN, (variant, match)


def test_most_captions_and_watermarks_are_flagged_as_possible(originals):
    for edit in ("caption", "watermark"):
        levels = [compare(sig, signature_from_image(EDITED[edit](img))).level for img, sig in originals]
        assert levels.count(MatchLevel.POSSIBLE) >= len(levels) * 0.75, edit


def test_same_template_different_caption_is_not_certain():
    for i in range(len(SAMPLE_IMAGES)):
        img = load(i)
        a = signature_from_image(caption(img, "WHEN THE TESTS PASS"))
        b = signature_from_image(caption(img, "NOBODY:"))
        assert compare(a, b).level is MatchLevel.POSSIBLE


def test_unrelated_images_do_not_match(originals):
    for (_, a), (_, b) in itertools.combinations(originals, 2):
        assert compare(a, b).level is MatchLevel.NONE


def test_identical_signature_is_certain_and_100_percent(originals):
    _, sig = originals[0]
    match = compare(sig, sig)
    assert match.level is MatchLevel.CERTAIN
    assert match.similarity == 100.0


def test_aspect_ratio_change_is_not_certain():
    img = load(0)
    stretched = img.resize((img.width, int(img.height * 0.8)))
    match = compare(signature_from_image(img), signature_from_image(stretched))
    assert match.level is not MatchLevel.CERTAIN


def test_transparency_is_composited_on_white():
    rgba = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    rgba.paste((200, 30, 30, 255), (16, 16, 48, 48))
    flattened = Image.new("RGB", (64, 64), (255, 255, 255))
    flattened.paste((200, 30, 30), (16, 16, 48, 48))
    assert np.array_equal(to_rgb_array(rgba), np.asarray(flattened))
    assert compare(signature_from_image(rgba), signature_from_image(flattened)).level is MatchLevel.CERTAIN


def test_palette_image_with_transparency():
    img = Image.new("P", (32, 32), 0)
    img.info["transparency"] = 0
    assert to_rgb_array(img)[0, 0].tolist() == [255, 255, 255]


def test_exif_orientation_is_applied(tmp_path):
    img = load(0)
    exif = Image.Exif()
    exif[0x0112] = 6  # "rotate 90 degrees clockwise to display"
    path = tmp_path / "sideways.jpg"
    img.rotate(90, expand=True).save(path, quality=95, exif=exif)
    with Image.open(path) as sideways:
        assert compare(signature_from_image(img), signature_from_image(sideways)).level is MatchLevel.CERTAIN


class TestFlatImages:
    def flat(self, color, size=(100, 80)):
        return signature_from_image(Image.new("RGB", size, color))

    def test_flat_detection(self):
        assert self.flat("black").flat
        assert not signature_from_image(load(0)).flat

    def test_same_colour_flats_are_only_possible(self):
        assert compare(self.flat("black"), self.flat((2, 2, 2), (50, 40))).level is MatchLevel.POSSIBLE

    def test_different_colour_flats_do_not_match(self):
        assert compare(self.flat("black"), self.flat("white")).level is MatchLevel.NONE

    def test_flat_vs_photo_does_not_match(self):
        assert compare(self.flat("gray"), signature_from_image(load(0))).level is MatchLevel.NONE


def test_empty_array_is_rejected():
    with pytest.raises(ValueError):
        signature_from_array(np.zeros((0, 10, 3), dtype=np.uint8))


def test_size_override_is_recorded():
    sig = signature_from_array(np.zeros((10, 20, 3), dtype=np.uint8), size=(2000, 1000))
    assert (sig.width, sig.height, sig.aspect) == (2000, 1000, 2.0)


def test_helpers():
    assert hamming(0b1011, 0b0001) == 2
    assert aspects_match(4 / 3, 640 / 481)
    assert not aspects_match(4 / 3, 16 / 9)
    assert Match(MatchLevel.NONE).similarity == 0.0
