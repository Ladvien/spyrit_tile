"""Combine single tiles into one tileset image.

Spyrite Tile reads a tileset as a uniform grid. `tile_xy` is
(column from the left, row from the top), the same as an image editor and
the sheets Retro Diffusion returns, so an op that places `tile_xy=(1, 0)`
uses the second tile of the first row.
"""

from __future__ import annotations

import io
from collections.abc import Sequence
from pathlib import Path

from PIL import Image


def compose_atlas(
    paths: Sequence[str | Path], tile_size_px: int, columns: int
) -> tuple[bytes, list[tuple[int, int]]]:
    """Place the tiles row-major from the top-left on a transparent canvas.

    Returns (PNG bytes, the `tile_xy` of each input in input order). Every
    input must be exactly `tile_size_px` square.
    """
    if not paths:
        raise ValueError("compose_atlas needs at least one tile")
    if tile_size_px < 1 or columns < 1:
        raise ValueError(f"tile_size_px={tile_size_px} and columns={columns} must be positive")
    rows = -(-len(paths) // columns)
    canvas = Image.new("RGBA", (columns * tile_size_px, rows * tile_size_px), (0, 0, 0, 0))
    placed: list[tuple[int, int]] = []
    for index, path in enumerate(paths):
        with Image.open(path) as opened:
            tile = opened.convert("RGBA")
        if tile.size != (tile_size_px, tile_size_px):
            raise ValueError(
                f"{path}: tile is {tile.size[0]}x{tile.size[1]} px, expected "
                f"{tile_size_px}x{tile_size_px}"
            )
        column, row = index % columns, index // columns
        canvas.paste(tile, (column * tile_size_px, row * tile_size_px))
        placed.append((column, row))
    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG")
    return buffer.getvalue(), placed
