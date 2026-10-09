"""Generate tiles_16px.png: a 64x64 RGBA sheet of 4x4 distinct opaque 16 px tiles.

Regenerate with (from the repo root):
    uv run --all-packages python tests/fixtures/make_tiles_fixture.py

Tile (col, row), row counted from the top, is a solid colour given by
`tile_color`; all 16 colours are distinct.
"""

from pathlib import Path

from PIL import Image

TILE_PX = 16
GRID = 4


def tile_color(col: int, row: int) -> tuple[int, int, int, int]:
    return (col * 80 + 20, row * 80 + 20, (col + row * GRID) * 15 + 10, 255)


def main() -> None:
    img = Image.new("RGBA", (TILE_PX * GRID, TILE_PX * GRID))
    for row in range(GRID):
        for col in range(GRID):
            img.paste(tile_color(col, row), (col * TILE_PX, row * TILE_PX, (col + 1) * TILE_PX, (row + 1) * TILE_PX))
    assert len({tile_color(c, r) for c in range(GRID) for r in range(GRID)}) == GRID * GRID
    out = Path(__file__).resolve().parent / "tiles_16px.png"
    img.save(out)
    print(out)


if __name__ == "__main__":
    main()
