"""Calibration tests: every sample image, put through realistic transformations.

Benign degradations (resizing, re-compression, format changes) must be CERTAIN
matches, so they're cleaned up automatically. Meaningful edits (captions,
watermarks, crops, colour changes) must *never* be CERTAIN -- at worst the user
gets asked. Unrelated images must not match at all.
"""

from __future__ import annotations

import dataclasses
import itertools

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageEnhance, ImageOps

from media_dedup.signature import (
    DHASH_POSSIBLE,
    Limits,
    PHASH_POSSIBLE,
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


def test_dhash_limit_never_rejects_an_edit_that_phash_accepts(originals):
    """The DHASH_POSSIBLE gate must not lose any edit that pHash flags as possible."""
    for variant in ("caption", "short caption", "watermark", "crop 5%", "brighter"):
        for img, sig in originals:
            edited = signature_from_image(EDITED[variant](img))
            if hamming(sig.phash, edited.phash) <= PHASH_POSSIBLE:
                assert hamming(sig.dhash, edited.dhash) <= DHASH_POSSIBLE - 3, variant  # keep a margin
                assert compare(sig, edited).level is MatchLevel.POSSIBLE, variant


def test_phash_collision_with_unrelated_dhash_is_not_a_match(originals):
    """Unrelated photos with a similar layout can land within PHASH_POSSIBLE;
    a near-random dHash distance must rule them out."""
    (_, a), (_, b) = originals[0], originals[1]
    collided = dataclasses.replace(b, phash=a.phash ^ ((1 << PHASH_POSSIBLE) - 1))
    assert hamming(a.phash, collided.phash) == PHASH_POSSIBLE
    assert hamming(a.dhash, collided.dhash) > DHASH_POSSIBLE
    match = compare(a, collided)
    assert match.level is MatchLevel.NONE
    assert match.dhash_distance == hamming(a.dhash, collided.dhash)


def test_dhash_at_the_limit_is_still_possible(originals):
    _, a = originals[0]
    near = dataclasses.replace(
        a, phash=a.phash ^ ((1 << PHASH_POSSIBLE) - 1), dhash=a.dhash ^ ((1 << DHASH_POSSIBLE) - 1)
    )
    assert compare(a, near).level is MatchLevel.POSSIBLE


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

    def test_noisy_blank_frame_is_flat(self):
        noise = np.random.default_rng(0).normal(8, 3, (480, 640, 3))
        assert signature_from_array(np.clip(noise, 0, 255).astype(np.uint8)).flat

    def test_sparse_screenshots_are_not_flat(self):
        """A few lines of text on white were "flat", so every such screenshot
        matched every other one at "100% similar"."""
        sigs = []
        for seed in (1, 2):
            img = Image.new("RGB", (1170, 2532), "white")
            draw = ImageDraw.Draw(img)
            rng = np.random.default_rng(seed)
            for _ in range(8):
                draw.text((60, int(rng.integers(100, 2400))), "".join(rng.choice(list("abcdefgh "), 60)), fill="black")
            sigs.append(signature_from_image(img))
        assert not any(s.flat for s in sigs)
        assert compare(*sigs).level is MatchLevel.NONE

    def test_dark_photos_are_not_flat(self):
        dark = [ImageEnhance.Brightness(load(i)).enhance(0.06) for i in (0, 1)]
        sigs = [signature_from_image(img) for img in dark]
        assert not any(s.flat for s in sigs)
        assert compare(*sigs).level is MatchLevel.NONE
        # Low contrast: still found, but asked about rather than removed.
        assert all(s.low_contrast for s in sigs)
        assert compare(sigs[0], signature_from_image(recompress(dark[0], 80))).level is MatchLevel.POSSIBLE

    @pytest.mark.parametrize("edit", [caption, watermark])
    def test_darkened_edits_of_low_contrast_images_are_never_certain(self, edit):
        """In a very dark image a darkened caption or watermark is only ~15 levels
        brighter than its surroundings, under LOCAL_DIFF_CERTAIN."""
        darken = lambda img: ImageEnhance.Brightness(img).enhance(0.06)
        checked = 0
        for i in range(len(SAMPLE_IMAGES)):
            img = load(i)
            original = signature_from_image(darken(img))
            if not original.low_contrast:
                continue
            checked += 1
            match = compare(original, signature_from_image(darken(edit(img))))
            assert match.level is MatchLevel.POSSIBLE, (i, match)
        assert checked >= len(SAMPLE_IMAGES) // 2

    def test_normal_photos_are_not_low_contrast(self, originals):
        assert not any(sig.low_contrast for _, sig in originals)


class TestHighBitDepth:
    """16-bit and float images used to decode as solid white or black."""

    def gray16(self, i):
        return Image.fromarray(np.asarray(load(i).convert("L")).astype(np.uint16) * 257)

    def test_16_bit_images_keep_their_content(self):
        a, b = signature_from_image(self.gray16(0)), signature_from_image(self.gray16(1))
        assert not a.flat and not b.flat
        assert compare(a, b).level is MatchLevel.NONE

    def test_16_bit_matches_its_8_bit_copy(self):
        eight = signature_from_image(load(0).convert("L"))
        assert compare(signature_from_image(self.gray16(0)), eight).level is MatchLevel.CERTAIN

    def test_float_image_matches_its_8_bit_copy(self):
        gray = np.asarray(load(0).convert("L"))
        as_float = Image.fromarray(gray.astype(np.float32) / 255)
        assert as_float.mode == "F"
        assert compare(signature_from_image(as_float), signature_from_image(Image.fromarray(gray))).level is MatchLevel.CERTAIN


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


def test_limits_override_the_possible_thresholds(originals):
    img, sig = originals[0]
    edited = signature_from_image(caption(img))
    pd, dd = hamming(sig.phash, edited.phash), hamming(sig.dhash, edited.dhash)
    assert compare(sig, edited).level is MatchLevel.POSSIBLE
    assert compare(sig, edited, limits=Limits(phash=pd - 1)).level is MatchLevel.NONE
    assert compare(sig, edited, limits=Limits(dhash=dd - 1)).level is MatchLevel.NONE
    assert compare(sig, edited, limits=Limits(phash=pd, dhash=dd)).level is MatchLevel.POSSIBLE
