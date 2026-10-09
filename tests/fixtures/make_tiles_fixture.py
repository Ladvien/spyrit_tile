"""Generate the tileset fixtures: 64x64 RGBA sheets of 4x4 distinct opaque 16 px tiles.

Regenerate with (from the repo root):
    uv run --all-packages python tests/fixtures/make_tiles_fixture.py

tiles_16px.png: tile (col, row), row counted from the top, is a solid colour given by
`tile_color`; all 16 colours are distinct.

tiles_oriented_16px.png: the same tiles, each split into four 8 px quadrants with distinct
colours (`quadrant_colors`): top-left the tile colour, top-right near white, bottom-left near
black, bottom-right the inverted tile colour. Any rotation or mirror of a tile moves at least two
quadrants, so the quadrant layout tells all 8 orientations apart. A solid tile cannot.
"""

from pathlib import Path

from PIL import Image

TILE_PX = 16
GRID = 4


def tile_color(col: int, row: int) -> tuple[int, int, int, int]:
    return (col * 80 + 20, row * 80 + 20, (col + row * GRID) * 15 + 10, 255)


QUADRANT_PX = TILE_PX // 2


def quadrant_colors(col: int, row: int) -> dict[str, tuple[int, int, int, int]]:
    """Colour of each quadrant of tile (col, row): keys 'tl', 'tr', 'bl', 'br' as drawn in the image."""
    r, g, b, a = tile_color(col, row)
    return {"tl": (r, g, b, a), "tr": (235, 235, 235, a), "bl": (15, 15, 15, a), "br": (255 - r, 255 - g, 255 - b, a)}


def oriented_sheet() -> Image.Image:
    img = Image.new("RGBA", (TILE_PX * GRID, TILE_PX * GRID))
    offsets = {"tl": (0, 0), "tr": (QUADRANT_PX, 0), "bl": (0, QUADRANT_PX), "br": (QUADRANT_PX, QUADRANT_PX)}
    for row in range(GRID):
        for col in range(GRID):
            colours = quadrant_colors(col, row)
            assert len(set(colours.values())) == 4, (col, row, colours)
            for key, (dx, dy) in offsets.items():
                x, y = col * TILE_PX + dx, row * TILE_PX + dy
                img.paste(colours[key], (x, y, x + QUADRANT_PX, y + QUADRANT_PX))
    return img


def main() -> None:
    img = Image.new("RGBA", (TILE_PX * GRID, TILE_PX * GRID))
    for row in range(GRID):
        for col in range(GRID):
            img.paste(tile_color(col, row), (col * TILE_PX, row * TILE_PX, (col + 1) * TILE_PX, (row + 1) * TILE_PX))
    assert len({tile_color(c, r) for c in range(GRID) for r in range(GRID)}) == GRID * GRID
    out = Path(__file__).resolve().parent / "tiles_16px.png"
    img.save(out)
    print(out)
    oriented = out.with_name("tiles_oriented_16px.png")
    oriented_sheet().save(oriented)
    print(oriented)


if __name__ == "__main__":
    main()
