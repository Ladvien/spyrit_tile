"""Metamorphic relations for spyrite_tile.api, inside Blender (headless).

Each test runs the API twice (or more) on inputs related by a known transformation and asserts the
outputs are related the same way. No expected UV or vertex value is written down: a relation holds or
it does not, so the tests catch defects that a hand-computed expectation sharing the code's own maths
would miss. Where colour matters the oracle samples the tileset IMAGE at the face's UV centre
(`_texel_at`), which does not reuse Sprytile's tile-id bookkeeping.

Relations:
- translation: moving every cell by (dx, dy) moves every vertex by (dx, dy) cells, UVs unchanged;
- ppu scaling: doubling pixels-per-unit halves every vertex, UVs unchanged;
- plane permutation: XZ at offset 0 is XY with y and z swapped, UVs unchanged;
- rotation 180 == flip_x + flip_y;
- history independence: placing A, then B, then A on one cell equals placing A once;
- order independence: shuffled placements give the same cell -> tile map;
- fill == the union of single placements;
- remove is the inverse of place;
- atlas permutation: swapping tiles in the image and swapping tile_xy accordingly shows the same colours;
- save/load: a saved copy of the object reads back identically.
"""

import importlib
import random
import tempfile
from pathlib import Path

import bpy
import pytest

api = importlib.import_module("bl_ext.user_default.spyrite_tile.api")
sprytile_utils = importlib.import_module("bl_ext.user_default.spyrite_tile.sprytile_utils")

FIXTURE_IMAGE = Path(__file__).resolve().parent.parent / "fixtures" / "tiles_16px.png"
PREFIX = "meta_test_"
TILESET = PREFIX + "tiles"
PERMUTED_TILESET = PREFIX + "tiles_permuted"
TILE_PX = 16
SHEET_TILES = 4
PPU = 16
DIGITS = 4  # rounding for comparing positions and UVs: well above float noise, below 1e-3 of a cell
SHUFFLE_SEED = 20261009
ALL_TILES = [(c, r) for r in range(SHEET_TILES) for c in range(SHEET_TILES)]


def _remove_test_data():
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for obj in [o for o in bpy.data.objects if o.name.startswith(PREFIX)]:
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in [m for m in bpy.data.meshes if m.name.startswith(PREFIX)]:
        bpy.data.meshes.remove(mesh)
    for material in [m for m in bpy.data.materials if m.name.startswith(PREFIX)]:
        bpy.data.materials.remove(material)
    for image in [i for i in bpy.data.images if i.name.startswith(PREFIX)]:
        bpy.data.images.remove(image)
    sprytile_utils.validate_grids(bpy.context.scene)


@pytest.fixture(autouse=True)
def clean_scene():
    _remove_test_data()
    api.create_tileset(TILESET, str(FIXTURE_IMAGE), (TILE_PX, TILE_PX))
    yield
    _remove_test_data()


_counter = iter(range(10**6))


def build(placements, ppu=PPU, tileset=TILESET):
    """A fresh tile object with `placements` placed; returns the object."""
    name = f"{PREFIX}obj_{next(_counter)}"
    api.create_tile_object(name, tileset, ppu)
    if placements:
        api.place_tiles(name, tileset, placements)
    return bpy.data.objects[name]


def place(cell, tile, **extra):
    return {"cell_xy": list(cell), "tile_xy": list(tile), **extra}


def _r(values):
    return tuple(round(v, DIGITS) + 0.0 for v in values)


def faces_of(obj):
    """{face centre: {vertex position: uv}} in world space, rounded; one entry per face."""
    mesh = obj.data
    uvs = mesh.uv_layers.active.data
    out = {}
    for poly in mesh.polygons:
        corners = {}
        for loop_index in poly.loop_indices:
            position = obj.matrix_world @ mesh.vertices[mesh.loops[loop_index].vertex_index].co
            corners[_r(position)] = _r(uvs[loop_index].uv)
        centre = _r(obj.matrix_world @ poly.center)
        assert centre not in out, f"two faces share the centre {centre}"
        out[centre] = corners
    return out


def _image_pixels(image):
    width, height = image.size
    flat = list(image.pixels)
    return width, height, flat


def _texel_at(image_data, uv):
    """RGBA of the image texel under `uv` (nearest), from the image file's own pixels."""
    width, height, flat = image_data
    x = min(width - 1, max(0, int(uv[0] * width)))
    y = min(height - 1, max(0, int(uv[1] * height)))
    start = 4 * (y * width + x)
    return tuple(round(v, 3) for v in flat[start:start + 4])


def colours_of(obj, image):
    """{face centre: colour of the texel under the face's mean UV}."""
    data = _image_pixels(image)
    out = {}
    for centre, corners in faces_of(obj).items():
        uvs = list(corners.values())
        mean = (sum(u for u, _ in uvs) / len(uvs), sum(v for _, v in uvs) / len(uvs))
        out[centre] = _texel_at(data, mean)
    return out


def _shift(position, offset):
    return _r(p + o for p, o in zip(position, offset))


def _transform_faces(faces, mapping):
    return {
        mapping(centre): {mapping(position): uv for position, uv in corners.items()}
        for centre, corners in faces.items()
    }


def _fixture_image():
    return next(i for i in bpy.data.images if i.filepath and Path(bpy.path.abspath(i.filepath)).name == FIXTURE_IMAGE.name)


# --- geometric relations ----------------------------------------------------------------------------------


@pytest.mark.parametrize("plane", ["XY", "XZ", "YZ"])
def test_translating_cells_translates_vertices_and_keeps_uvs(plane):
    cells = [(0, 0), (1, 0), (2, 1), (0, 3)]
    tiles = [(1, 0), (3, 2), (0, 3), (2, 2)]
    offset_cells = (3, -2)
    base = build([place(c, t, plane=plane) for c, t in zip(cells, tiles)])
    moved = build([place((c[0] + offset_cells[0], c[1] + offset_cells[1]), t, plane=plane)
                   for c, t in zip(cells, tiles)])
    right_up = {"XY": ((1, 0, 0), (0, 1, 0)), "XZ": ((1, 0, 0), (0, 0, 1)), "YZ": ((0, 1, 0), (0, 0, 1))}[plane]
    world_offset = tuple(offset_cells[0] * r + offset_cells[1] * u for r, u in zip(*right_up))
    expected = _transform_faces(faces_of(base), lambda p: _shift(p, world_offset))
    assert faces_of(moved) == expected


def test_doubling_pixels_per_unit_halves_every_vertex_and_keeps_uvs():
    placements = [place((x, y), ALL_TILES[(x * 3 + y) % 16]) for x in range(3) for y in range(3)]
    coarse = build(placements, ppu=PPU)
    fine = build(placements, ppu=PPU * 2)
    expected = _transform_faces(faces_of(coarse), lambda p: _r(v / 2 for v in p))
    assert faces_of(fine) == expected


def test_the_front_wall_is_the_floor_with_y_and_z_swapped():
    placements = [((x, y), ALL_TILES[(5 * x + y) % 16]) for x in range(3) for y in range(2)]
    floor = build([place(c, t, plane="XY") for c, t in placements])
    wall = build([place(c, t, plane="XZ", plane_offset_m=0.0) for c, t in placements])
    expected = _transform_faces(faces_of(floor), lambda p: _r((p[0], p[2], p[1])))
    assert faces_of(wall) == expected


@pytest.mark.parametrize("tile", [(0, 0), (2, 1), (3, 3)])
def test_rotating_180_equals_flipping_both_axes(tile):
    rotated = build([place((0, 0), tile, rotation_deg=180)])
    flipped = build([place((0, 0), tile, flip_x=True, flip_y=True)])
    assert faces_of(rotated) == faces_of(flipped)


# --- history and order ------------------------------------------------------------------------------------


@pytest.mark.parametrize("extra", [{}, {"rotation_deg": 90}, {"flip_x": True}])
def test_replacing_a_cell_has_no_memory(extra):
    once = build([place((1, 1), (2, 3), **extra)])
    obj = build([place((1, 1), (2, 3), **extra)])
    api.place_tiles(obj.name, TILESET, [place((1, 1), (0, 1), rotation_deg=270, flip_y=True)])
    api.place_tiles(obj.name, TILESET, [place((1, 1), (2, 3), **extra)])
    assert faces_of(obj) == faces_of(once)


def test_placement_order_does_not_matter():
    placements = [place((x, y), ALL_TILES[(7 * x + 3 * y) % 16]) for x in range(4) for y in range(4)]
    shuffled = list(placements)
    random.Random(SHUFFLE_SEED).shuffle(shuffled)
    assert shuffled != placements
    image = _fixture_image()
    in_order = build(placements)
    out_of_order = build(shuffled)
    assert len(out_of_order.data.polygons) == len(in_order.data.polygons) == 16
    assert colours_of(out_of_order, image) == colours_of(in_order, image)


def test_fill_equals_the_union_of_single_placements():
    filled_name = f"{PREFIX}filled"
    api.create_tile_object(filled_name, TILESET, PPU)
    api.fill_tiles(filled_name, TILESET, (1, 2), (4, 4), (3, 1), plane="XZ", plane_offset_m=2.0)
    singles = build([place((x, y), (3, 1), plane="XZ", plane_offset_m=2.0)
                     for x in range(1, 5) for y in range(2, 5)])
    assert faces_of(bpy.data.objects[filled_name]) == faces_of(singles)


def test_removing_cells_undoes_placing_them():
    kept = [(0, 0), (2, 1), (3, 3)]
    extra = [(1, 1), (2, 2), (0, 3)]
    tiles = {cell: ALL_TILES[i] for i, cell in enumerate(kept + extra)}
    only_kept = build([place(c, tiles[c]) for c in kept])
    both = build([place(c, tiles[c]) for c in kept + extra])
    result = api.remove_tiles(both.name, "XY", 0.0, [list(c) for c in extra])
    assert result["removed"] == len(extra)
    image = _fixture_image()
    assert colours_of(both, image) == colours_of(only_kept, image)


# --- colour relations (image oracle) ----------------------------------------------------------------------


def _permuted_fixture(path):
    """The fixture with its tiles moved by tile p -> PERMUTATION[p]; returns the mapping."""
    source = bpy.data.images.load(str(FIXTURE_IMAGE), check_existing=False)
    width, height = source.size
    pixels = list(source.pixels)
    order = list(ALL_TILES)
    random.Random(SHUFFLE_SEED).shuffle(order)
    mapping = dict(zip(ALL_TILES, order))
    out = [0.0] * len(pixels)
    for (col, row), (new_col, new_row) in mapping.items():
        for dy in range(TILE_PX):
            for dx in range(TILE_PX):
                # image rows count from the bottom; tile rows from the top
                sy = height - 1 - (row * TILE_PX + dy)
                ty = height - 1 - (new_row * TILE_PX + dy)
                sx, tx = col * TILE_PX + dx, new_col * TILE_PX + dx
                out[4 * (ty * width + tx):4 * (ty * width + tx) + 4] = pixels[4 * (sy * width + sx):4 * (sy * width + sx) + 4]
    permuted = bpy.data.images.new(PREFIX + "permuted", width, height, alpha=True)
    permuted.pixels = out
    permuted.filepath_raw = str(path)
    permuted.file_format = "PNG"
    permuted.save()
    bpy.data.images.remove(source)
    bpy.data.images.remove(permuted)
    return mapping


def test_permuting_the_atlas_and_the_tile_coordinates_shows_the_same_colours():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "permuted.png"
        mapping = _permuted_fixture(path)
        api.create_tileset(PERMUTED_TILESET, str(path), (TILE_PX, TILE_PX))
        cells = [((i % 4, i // 4), tile) for i, tile in enumerate(ALL_TILES)]
        original = build([place(c, t) for c, t in cells])
        permuted = build([place(c, mapping[t]) for c, t in cells], tileset=PERMUTED_TILESET)
        permuted_image = next(i for i in bpy.data.images if Path(bpy.path.abspath(i.filepath)).name == "permuted.png")
        original_colours = colours_of(original, _fixture_image())
        assert len(set(original_colours.values())) == 16  # the probe varies: one colour per tile
        assert colours_of(permuted, permuted_image) == original_colours


def test_a_saved_copy_reads_back_identically():
    obj = build([place((x, y), ALL_TILES[(x + 4 * y) % 16], rotation_deg=90 * (x % 4))
                 for x in range(4) for y in range(2)])
    before = faces_of(obj)
    with tempfile.TemporaryDirectory() as directory:
        path = str(Path(directory) / "copy.blend")
        bpy.ops.wm.save_as_mainfile(filepath=path, copy=True)
        with bpy.data.libraries.load(path) as (source, target):
            target.objects = [obj.name]
        loaded = target.objects[0]
        try:
            assert loaded is not None and loaded.name != obj.name
            assert faces_of(loaded) == before
            assert loaded.data.attributes["grid_tile_id"].data[0].value == obj.data.attributes["grid_tile_id"].data[0].value
        finally:
            mesh = loaded.data
            bpy.data.objects.remove(loaded)
            bpy.data.meshes.remove(mesh)
