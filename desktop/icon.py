"""The tray icon, drawn at runtime rather than shipped as a binary asset.

Generating it keeps the repository free of a checked-in .ico and means the
bundle has one less data file to resolve at a path that changes when frozen.
"""
from __future__ import annotations

from PIL import Image, ImageDraw

#: The dashboard's primary blue (see .streamlit/config.toml).
BRAND = (46, 111, 219, 255)
PAPER = (255, 255, 255, 255)


def tray_image(size: int = 64) -> Image.Image:
    """A rounded blue tile holding an envelope — mail, plus a date.

    Drawn on a transparent background so it sits correctly on both light and
    dark system trays.
    """
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    pad = size // 10
    draw.rounded_rectangle(
        (pad, pad, size - pad, size - pad), radius=size // 6, fill=BRAND
    )

    # The envelope: a body with its flap drawn as two strokes meeting in the
    # middle, which stays legible when the tray scales this down to 16px.
    left, right = size * 0.28, size * 0.72
    top, bottom = size * 0.36, size * 0.64
    stroke = max(1, size // 22)
    draw.rectangle((left, top, right, bottom), outline=PAPER, width=stroke)
    draw.line((left, top, size / 2, size * 0.53), fill=PAPER, width=stroke)
    draw.line((right, top, size / 2, size * 0.53), fill=PAPER, width=stroke)
    return image
