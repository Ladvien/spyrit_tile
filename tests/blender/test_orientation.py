"""Tile orientation against the documented contract, inside Blender (headless).

Contract (api.py module docstring): the tile picture is turned counter-clockwise by `rotation_deg`
as seen from the face's normal side, then mirrored left-right (`flip_x`) / top-bottom (`flip_y`).
Viewed from the normal side the plane's (right, up) axes are: XY (+X, +Y), XZ (+X, +Z), YZ (+Y, +Z).

Oracle, outside the code under test: the fixture `tiles_oriented_16px.png` has four distinctly
coloured 8 px quadrants per tile, so all 8 orientations differ. For every face, the texel under each
quadrant centre is looked up through the face's own corner UVs (bilinear across the quad, in the
viewer's frame) and read from the IMAGE's pixels; it must equal the colour of the quadrant that the
contract moves there, also read from the image. Nothing here reuses Sprytile's UV code.
"""

import importlib
import itertools
from pathlib import Path

import bpy
import pytest

api = importlib.import_module("bl_ext.user_default.spyrite_tile.api")
sprytile_utils = importlib.import_module("bl_ext.user_default.spyrite_tile.sprytile_utils")

FIXTURE_IMAGE = Path(__file__).resolve().parent.parent / "fixtures" / "tiles_oriented_16px.png"
PREFIX = "orient_test_"
TILESET = PREFIX + "tiles"
TILE_PX = 16
SHEET_TILES = 4
PPU = 16
VIEWER_FRAME = {"XY": ((1, 0, 0), (0, 1, 0)), "XZ": ((1, 0, 0), (0, 0, 1)), "YZ": ((0, 1, 0), (0, 0, 1))}
ROTATIONS = (0, 90, 180, 270)
CASES = list(itertools.product(VIEWER_FRAME, ROTATIONS, (False, True), (False, True)))


def _remove_test_data():
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for obj in [o for o in bpy.data.objects if o.name.startswith(PREFIX)]:
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in [m for m in bpy.data.meshes if m.name.startswith(PREFIX)]:
        bpy.data.meshes.remove(mesh)
    for material in [m for m in bpy.data.materials if m.name.startswith(PREFIX)]:
        bpy.data.materials.remove(material)
    sprytile_utils.validate_grids(bpy.context.scene)


@pytest.fixture(scope="module")
def image_pixels():
    image = bpy.data.images.load(str(FIXTURE_IMAGE), check_existing=False)
    width, height = image.size
    pixels = list(image.pixels)
    bpy.data.images.remove(image)
    return width, height, pixels


@pytest.fixture(autouse=True)
def clean_scene():
    _remove_test_data()
    api.create_tileset(TILESET, str(FIXTURE_IMAGE), (TILE_PX, TILE_PX))
    yield
    _remove_test_data()


def texel(image_pixels, u, v):
    """RGB (0-255) of the texel under (u, v); v counts from the bottom like Blender's pixels."""
    width, height, pixels = image_pixels
    x = min(width - 1, max(0, int(u * width)))
    y = min(height - 1, max(0, int(v * height)))
    start = 4 * (y * width + x)
    return tuple(round(c * 255) for c in pixels[start:start + 3])


def tile_quadrants(image_pixels, tile_xy):
    """2x2 colours of a tile as drawn in the image: [[top-left, top-right], [bottom-left, bottom-right]]."""
    col, row = tile_xy
    out = []
    for qr in (0, 1):  # quadrant row from the top
        line = []
        for qc in (0, 1):
            u = (col * TILE_PX + (qc + 0.5) * TILE_PX / 2) / (SHEET_TILES * TILE_PX)
            v = 1 - (row * TILE_PX + (qr + 0.5) * TILE_PX / 2) / (SHEET_TILES * TILE_PX)
            line.append(texel(image_pixels, u, v))
        out.append(line)
    assert len({c for line in out for c in line}) == 4, f"fixture tile {tile_xy} quadrants not distinct"
    return out


def contract(quadrants, rotation_deg, flip_x, flip_y):
    """The 2x2 picture after turning it counter-clockwise, then mirroring, per the docstring."""
    m = [list(line) for line in quadrants]
    for _ in range(rotation_deg // 90):
        m = [[m[j][1 - i] for j in range(2)] for i in range(2)]  # new[i][j] = old[j][n-1-i]
    if flip_x:
        m = [line[::-1] for line in m]
    if flip_y:
        m = m[::-1]
    return m


def seen_quadrants(obj, face_index, plane, image_pixels):
    """2x2 colours the face shows, viewed from its normal side, top row first."""
    mesh = obj.data
    poly = mesh.polygons[face_index]
    uvs = mesh.uv_layers.active.data
    right, up = VIEWER_FRAME[plane]
    corners = []
    for loop_index in poly.loop_indices:
        position = obj.matrix_world @ mesh.vertices[mesh.loops[loop_index].vertex_index].co
        r = sum(p * a for p, a in zip(position, right))
        u = sum(p * a for p, a in zip(position, up))
        corners.append((r, u, tuple(uvs[loop_index].uv)))
    assert len(corners) == 4, "orientation oracle expects quads"
    r0, r1 = min(c[0] for c in corners), max(c[0] for c in corners)
    u0, u1 = min(c[1] for c in corners), max(c[1] for c in corners)

    def corner_uv(at_r, at_u):
        return next(c[2] for c in corners if abs(c[0] - at_r) < 1e-6 and abs(c[1] - at_u) < 1e-6)

    bl, br, tl, tr = corner_uv(r0, u0), corner_uv(r1, u0), corner_uv(r0, u1), corner_uv(r1, u1)

    def uv_at(s, t):  # s along right, t along up, both in [0, 1]
        return tuple(
            (1 - s) * (1 - t) * bl[k] + s * (1 - t) * br[k] + (1 - s) * t * tl[k] + s * t * tr[k] for k in range(2)
        )

    return [[texel(image_pixels, *uv_at(s, t)) for s in (0.25, 0.75)] for t in (0.75, 0.25)]


def _face_at(obj, plane, cell):
    right, up = VIEWER_FRAME[plane]
    for poly in obj.data.polygons:
        centre = obj.matrix_world @ poly.center
        r = sum(p * a for p, a in zip(centre, right))
        u = sum(p * a for p, a in zip(centre, up))
        if abs(r - (cell[0] + 0.5)) < 1e-4 and abs(u - (cell[1] + 0.5)) < 1e-4:
            return poly.index
    raise AssertionError(f"no face at cell {cell} on {plane}")


@pytest.mark.parametrize("plane,rotation_deg,flip_x,flip_y", CASES)
def test_place_tiles_shows_the_picture_the_contract_describes(plane, rotation_deg, flip_x, flip_y, image_pixels):
    case = CASES.index((plane, rotation_deg, flip_x, flip_y))
    tile = (case % SHEET_TILES, (case // SHEET_TILES) % SHEET_TILES)
    name = PREFIX + "obj"
    api.create_tile_object(name, TILESET, PPU)
    api.place_tiles(name, TILESET, [{"cell_xy": [1, 2], "tile_xy": list(tile), "plane": plane,
                                     "rotation_deg": rotation_deg, "flip_x": flip_x, "flip_y": flip_y}])
    obj = bpy.data.objects[name]
    expected = contract(tile_quadrants(image_pixels, tile), rotation_deg, flip_x, flip_y)
    assert seen_quadrants(obj, _face_at(obj, plane, (1, 2)), plane, image_pixels) == expected


@pytest.mark.parametrize("plane,rotation_deg,flip_x,flip_y", CASES)
def test_paint_faces_shows_the_picture_the_contract_describes(plane, rotation_deg, flip_x, flip_y, image_pixels):
    case = CASES.index((plane, rotation_deg, flip_x, flip_y))
    tile = ((case + 5) % SHEET_TILES, (case // SHEET_TILES + 1) % SHEET_TILES)
    name = PREFIX + "obj"
    api.create_tile_object(name, TILESET, PPU)
    api.place_tiles(name, TILESET, [{"cell_xy": [1, 2], "tile_xy": [0, 0], "plane": plane}])
    obj = bpy.data.objects[name]
    face = _face_at(obj, plane, (1, 2))
    api.paint_faces(name, TILESET, [face], list(tile), rotation_deg=rotation_deg, flip_x=flip_x, flip_y=flip_y)
    expected = contract(tile_quadrants(image_pixels, tile), rotation_deg, flip_x, flip_y)
    assert seen_quadrants(obj, face, plane, image_pixels) == expected


def test_the_oracle_can_tell_all_eight_orientations_apart(image_pixels):
    quadrants = tile_quadrants(image_pixels, (2, 1))
    pictures = {
        str(contract(quadrants, r, fx, fy))
        for r, fx, fy in itertools.product(ROTATIONS, (False, True), (False, True))
    }
    assert len(pictures) == 8  # 16 combinations, 8 distinct orientations of a square
