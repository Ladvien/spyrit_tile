"""Combine single tiles into one tileset image.

Spyrite Tile reads a tileset as a uniform grid. `tile_xy` is
(column from the left, row from the top), the same as an image editor and
the sheets Retro Diffusion returns, so an op that places `tile_xy=(1, 0)`
uses the second tile of the first row.
"""

from __future__ import annotations

import io
import re
from collections.abc import Sequence
from pathlib import Path

import yaml
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


_NAME_PATTERN = re.compile(r"[A-Za-z0-9_]+")
_PLANES = ("XY", "XZ", "YZ")


def sidecar_text(
    names: Sequence[str],
    placed: Sequence[tuple[int, int]],
    planes_by_name: dict[str, list[str]] | None = None,
) -> str:
    """The `<atlas>.spyrite.yaml` tile-name sidecar the Blender add-on reads next to the image.

    `names` has one entry per input tile, in input order (so `placed[i]` is `names[i]`'s tile_xy).
    `planes_by_name` optionally restricts a name to some of XY/XZ/YZ; absent = any plane.
    """
    if len(names) != len(placed):
        raise ValueError(f"names has {len(names)} entries but there are {len(placed)} input tiles")
    for name in names:
        if not isinstance(name, str) or not _NAME_PATTERN.fullmatch(name):
            raise ValueError(f"tile name {name!r} may only contain letters, digits and '_'")
    if len(set(names)) != len(names):
        raise ValueError(f"tile names must be unique, got {list(names)}")
    planes_by_name = planes_by_name or {}
    unknown = set(planes_by_name) - set(names)
    if unknown:
        raise ValueError(f"planes_by_name has names that are not in names: {sorted(unknown)}")
    tiles: dict[str, dict] = {}
    for name, (column, row) in zip(names, placed):
        entry: dict = {"xy": [column, row]}
        if name in planes_by_name:
            planes = list(planes_by_name[name])
            if not planes or any(p not in _PLANES for p in planes) or len(set(planes)) != len(planes):
                raise ValueError(
                    f"planes_by_name[{name!r}] must be a non-empty list without repeats from {list(_PLANES)}, "
                    f"got {planes!r}"
                )
            entry["planes"] = planes
        tiles[name] = entry
    return yaml.safe_dump({"spyrite_tileset": 1, "tiles": tiles}, sort_keys=False, default_flow_style=None)
