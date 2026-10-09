"""Make a generated image safe to use as tile texels.

Whatever a backend returns, the tileset must have exact pixel dimensions,
hard-edged alpha and a small palette, because Spyrite Tile maps each tile
cell to a texel rectangle with nearest-neighbour sampling. `normalize` is
the single place that guarantees it:

1. decode to RGBA;
2. resample to the target size with a box filter (exact for integer
   reductions, so a 1024 px SDXL image becomes 16 px by averaging 64x64
   blocks);
3. binarise alpha (below 128 becomes fully transparent, the rest opaque);
4. quantise without dithering, to a given palette or to the `max_colors`
   most representative colours of the opaque pixels, and restore alpha;
5. measure the seam: how much larger the jump between the first and last
   column (row) is than the average jump between neighbours. A tile that
   wraps well scores about 1; a hard edge scores about the image width.
   The ratios are reported, not enforced.
"""

from __future__ import annotations

import io
from dataclasses import asdict, dataclass

from PIL import Image, ImageChops, ImageStat

ALPHA_OPAQUE_THRESHOLD = 128
PALETTE_ENTRIES = 256


@dataclass(frozen=True)
class NormalizeReport:
    source_size_px: tuple[int, int]
    size_px: tuple[int, int]
    colors_used: int
    seam_ratio_x: float
    seam_ratio_y: float

    def as_dict(self) -> dict:
        return asdict(self)


def _parse_palette(palette_hex: tuple[str, ...] | list[str]) -> list[tuple[int, int, int]]:
    colours = []
    for entry in palette_hex:
        digits = entry.lstrip("#")
        if len(digits) != 6:
            raise ValueError(f"palette colour {entry!r} is not #rrggbb")
        colours.append(tuple(int(digits[i : i + 2], 16) for i in (0, 2, 4)))
    if not 1 <= len(colours) <= PALETTE_ENTRIES:
        raise ValueError(f"palette needs 1-{PALETTE_ENTRIES} colours, got {len(colours)}")
    return colours


def _palette_image(colours: list[tuple[int, int, int]]) -> Image.Image:
    """A P-mode image whose palette is `colours`.

    The unused entries repeat the last colour: left at zero they would be
    black, and the quantiser would map dark pixels onto them.
    """
    flat: list[int] = []
    for colour in colours:
        flat.extend(colour)
    flat.extend(list(colours[-1]) * (PALETTE_ENTRIES - len(colours)))
    image = Image.new("P", (1, 1))
    image.putpalette(flat)
    return image


def _derive_palette(opaque_rgb: list[tuple[int, int, int]], max_colors: int) -> Image.Image:
    """The `max_colors` palette of the opaque pixels, as a palette image."""
    strip = Image.new("RGB", (len(opaque_rgb), 1))
    strip.putdata(opaque_rgb)
    reduced = strip.quantize(
        colors=max_colors, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE
    )
    flat = reduced.getpalette() or []
    # The strip's palette indices are not guaranteed to be 0..n-1.
    present = sorted(set(reduced.tobytes()))
    colours = [(flat[3 * i], flat[3 * i + 1], flat[3 * i + 2]) for i in present]
    return _palette_image(colours)


def _rgb_pixels(image: Image.Image) -> list[tuple[int, int, int]]:
    """The (r, g, b) of every pixel, row-major."""
    data = image.convert("RGB").tobytes()
    return list(zip(data[0::3], data[1::3], data[2::3]))


def _seam_ratio(image: Image.Image, axis: str) -> float:
    width, height = image.size
    length = width if axis == "x" else height
    if length < 2:
        return 0.0
    if axis == "x":
        first, last = (0, 0, 1, height), (width - 1, 0, width, height)
        near_a, near_b = (0, 0, width - 1, height), (1, 0, width, height)
    else:
        first, last = (0, 0, width, 1), (0, height - 1, width, height)
        near_a, near_b = (0, 0, width, height - 1), (0, 1, width, height)
    seam = sum(ImageStat.Stat(ImageChops.difference(image.crop(first), image.crop(last))).mean)
    adjacent = sum(
        ImageStat.Stat(ImageChops.difference(image.crop(near_a), image.crop(near_b))).mean
    )
    if adjacent == 0:
        return 0.0
    return seam / adjacent


def normalize(
    png: bytes,
    width_px: int,
    height_px: int,
    palette_hex: tuple[str, ...] | list[str] | None = None,
    max_colors: int | None = None,
    binarize_alpha: bool = True,
) -> tuple[bytes, NormalizeReport]:
    """Return (PNG bytes, report); see the module docstring for the steps."""
    if width_px < 1 or height_px < 1:
        raise ValueError(f"target size {width_px}x{height_px} px is not positive")
    if max_colors is not None and not 1 <= max_colors <= PALETTE_ENTRIES:
        raise ValueError(f"max_colors must be 1-{PALETTE_ENTRIES}, got {max_colors}")

    image = Image.open(io.BytesIO(png)).convert("RGBA")
    source_size = image.size
    if image.size != (width_px, height_px):
        image = image.resize((width_px, height_px), Image.Resampling.BOX)

    alpha = image.getchannel("A")
    if binarize_alpha:
        alpha = alpha.point(lambda a: 255 if a >= ALPHA_OPAQUE_THRESHOLD else 0)
    rgb = image.convert("RGB")

    opaque_rgb = [colour for colour, a in zip(_rgb_pixels(rgb), alpha.tobytes()) if a > 0]
    if opaque_rgb and (palette_hex or max_colors):
        if palette_hex:
            palette = _palette_image(_parse_palette(palette_hex))
        else:
            assert max_colors is not None
            palette = _derive_palette(opaque_rgb, max_colors)
        rgb = rgb.quantize(palette=palette, dither=Image.Dither.NONE).convert("RGB")

    out = Image.new("RGBA", rgb.size)
    out.putdata(
        [
            (r, g, b, a) if a > 0 else (0, 0, 0, 0)
            for (r, g, b), a in zip(_rgb_pixels(rgb), alpha.tobytes())
        ]
    )
    colours_used = len(
        {colour for colour, a in zip(_rgb_pixels(rgb), alpha.tobytes()) if a > 0}
    )
    report = NormalizeReport(
        source_size_px=source_size,
        size_px=out.size,
        colors_used=colours_used,
        seam_ratio_x=_seam_ratio(out.convert("RGB"), "x"),
        seam_ratio_y=_seam_ratio(out.convert("RGB"), "y"),
    )
    buffer = io.BytesIO()
    out.save(buffer, format="PNG")
    return buffer.getvalue(), report
