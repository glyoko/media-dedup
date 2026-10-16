"""Helpers that build test media (variants of the sample images, and videos)."""

from __future__ import annotations

import io
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

DATA_DIR = Path(__file__).parent / "data" / "images"
SAMPLE_IMAGES = sorted(DATA_DIR.glob("*.jpg"))


def load(name_or_index: str | int) -> Image.Image:
    path = SAMPLE_IMAGES[name_or_index] if isinstance(name_or_index, int) else DATA_DIR / name_or_index
    with Image.open(path) as img:
        return img.convert("RGB")


def recompress(img: Image.Image, quality: int) -> Image.Image:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=quality)
    with Image.open(io.BytesIO(buf.getvalue())) as out:
        return out.convert("RGB")


def _font(size: int) -> ImageFont.ImageFont:
    return ImageFont.load_default(size)


def caption(img: Image.Image, text: str = "WHEN THE TESTS PASS") -> Image.Image:
    """Meme-style white text with a black outline across the top."""
    img = img.copy()
    draw = ImageDraw.Draw(img)
    draw.text(
        (img.width // 2, 10), text, font=_font(img.height // 9), fill="white",
        anchor="mt", stroke_width=3, stroke_fill="black",
    )
    return img


def watermark(img: Image.Image, text: str = "@memelord") -> Image.Image:
    """Semi-transparent text in the bottom-right corner."""
    base = img.convert("RGBA")
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).text(
        (base.width - 10, base.height - 10), text, font=_font(base.height // 12),
        fill=(255, 255, 255, 170), anchor="rb", stroke_width=1, stroke_fill=(0, 0, 0, 170),
    )
    return Image.alpha_composite(base, layer).convert("RGB")


def crop(img: Image.Image, fraction: float) -> Image.Image:
    w, h = img.size
    return img.crop((int(w * fraction), int(h * fraction), int(w * (1 - fraction)), int(h * (1 - fraction))))


def write_video(
    path: Path,
    source: Image.Image,
    size: tuple[int, int] = (320, 240),
    frames: int = 48,
    fps: float = 24.0,
) -> Path:
    """Write a short "pan across the image" video, so frames differ over time."""
    rgb = np.asarray(source.convert("RGB"))
    h, w = rgb.shape[:2]
    win_w, win_h = int(w * 0.6), int(h * 0.6)
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, size)
    assert writer.isOpened(), "OpenCV can't write mp4 on this system"
    for i in range(frames):
        t = i / max(frames - 1, 1)
        x, y = int((w - win_w) * t), int((h - win_h) * t)
        frame = cv2.resize(rgb[y : y + win_h, x : x + win_w], size, interpolation=cv2.INTER_AREA)
        writer.write(cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))
    writer.release()
    return path
