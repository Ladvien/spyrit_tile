"""spyrite_tile.api against the real add-on, inside Blender (headless, no UI context).

Fixture: tests/fixtures/tiles_16px.png, 64x64, 4x4 distinct solid 16 px tiles. With 16 px tiles at 16
pixels per unit one cell is one metre. `tile_xy` is (column from left, row from top); UV v counts from the
bottom, so tile (col, row) occupies u in [col/4, (col+1)/4] and v in [1 - (row+1)/4, 1 - row/4].
"""

import importlib
import math
from pathlib import Path

import bmesh
import bpy
import pytest

api = importlib.import_module("bl_ext.user_default.spyrite_tile.api")
sprytile_utils = importlib.import_module("bl_ext.user_default.spyrite_tile.sprytile_utils")

FIXTURE_IMAGE = Path(__file__).resolve().parent.parent / "fixtures" / "tiles_16px.png"
OTHER_IMAGE = FIXTURE_IMAGE.with_name("tiles_oriented_16px.png")  # a second image: the same image + layout would reuse the tileset
PREFIX = "api_test_"
TILESET = PREFIX + "tiles"
OBJECT = PREFIX + "obj"
TILE_PX = 16
SHEET_TILES = 4
PPU = 16
GEOMETRY_TOL = 1e-5
# Sprytile insets UVs ~0.0004 (auto pad 0.05 px of a 64 px sheet) inside the tile edge
UV_TOL = 1e-3


def _remove_test_data():
    for obj in [o for o in bpy.data.objects if o.name.startswith(PREFIX)]:
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in [m for m in bpy.data.meshes if m.name.startswith(PREFIX)]:
        bpy.data.meshes.remove(mesh)
    for material in [m for m in bpy.data.materials if m.name.startswith(PREFIX)]:
        bpy.data.materials.remove(material)
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    sprytile_utils.validate_grids(bpy.context.scene)


@pytest.fixture(autouse=True)
def clean_scene():
    _remove_test_data()
    yield
    _remove_test_data()


@pytest.fixture
def tileset():
    return api.create_tileset(TILESET, str(FIXTURE_IMAGE), (TILE_PX, TILE_PX))


@pytest.fixture
def tile_object(tileset):
    api.create_tile_object(OBJECT, TILESET, PPU)
    return bpy.data.objects[OBJECT]


def tile_rect(column, row, span=(1, 1)):
    """(u0, u1, v0, v1) of the tile at (column from left, row from top)."""
    return (
        column / SHEET_TILES,
        (column + span[0]) / SHEET_TILES,
        1 - (row + span[1]) / SHEET_TILES,
        1 - row / SHEET_TILES,
    )


def read_faces(obj):
    """World vertices, normal, per-loop uv and tile layer of each polygon (object mode only)."""
    assert obj.mode == "OBJECT"
    mesh = obj.data
    uv_layer = mesh.uv_layers.active.data
    tile_ids = mesh.attributes["grid_tile_id"].data
    faces = []
    for poly in mesh.polygons:
        faces.append(
            {
                "verts": [tuple(obj.matrix_world @ mesh.vertices[i].co) for i in poly.vertices],
                "normal": tuple(poly.normal),
                "uvs": [tuple(uv_layer[loop].uv) for loop in poly.loop_indices],
                "tile_id": tile_ids[poly.index].value,
            }
        )
    return faces


def span(values):
    return min(values), max(values)


def link_object(obj):
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.update()


def assert_close(actual, expected, tol):
    assert actual == pytest.approx(expected, abs=tol), f"{actual} != {expected} (tol {tol})"


def assert_uv_rect(face, rect):
    u0, u1, v0, v1 = rect
    us = [uv[0] for uv in face["uvs"]]
    vs = [uv[1] for uv in face["uvs"]]
    assert_close(min(us), u0, UV_TOL)
    assert_close(max(us), u1, UV_TOL)
    assert_close(min(vs), v0, UV_TOL)
    assert_close(max(vs), v1, UV_TOL)


def assert_box(face, x, y, z, tol=GEOMETRY_TOL):
    for axis, expected in zip(range(3), (x, y, z)):
        low, high = span([v[axis] for v in face["verts"]])
        if isinstance(expected, tuple):
            assert_close(low, expected[0], tol)
            assert_close(high, expected[1], tol)
        else:
            assert_close(low, expected, tol)
            assert_close(high, expected, tol)


# --- create_tileset / create_tile_object -------------------------------------------------------------------


def test_create_tileset_reports_layout(tileset):
    assert tileset["material_name"] == TILESET
    assert tileset["image_size_px"] == [64, 64]
    assert tileset["tile_size_px"] == [16, 16]
    assert tileset["columns"] == 4
    assert tileset["rows"] == 4
    assert isinstance(tileset["grid_id"], int)
    assert tileset["image_name"] in bpy.data.images

    material = bpy.data.materials[TILESET]
    node = sprytile_utils.get_material_texture_node(material)
    assert node is not None
    assert node.interpolation == "Closest"
    assert node.image.name == tileset["image_name"]
    assert material.surface_render_method == "DITHERED"
    assert material.users >= 1

    mat_data = sprytile_utils.get_mat_data(bpy.context, TILESET)
    grid = mat_data.grids[0]
    assert grid.id == tileset["grid_id"]
    assert tuple(grid.grid) == (16, 16)
    assert tuple(grid.padding) == (0, 0)
    assert tuple(grid.margin) == (0, 0, 0, 0)


def test_create_tileset_twice_reuses_material_and_grid(tileset):
    again = api.create_tileset(TILESET, str(FIXTURE_IMAGE), (TILE_PX, TILE_PX))
    assert again["grid_id"] == tileset["grid_id"]
    assert len([m for m in bpy.context.scene.sprytile_mats if m.mat_id == TILESET]) == 1


def test_create_tileset_padding_and_margin_keep_tile_size_and_layout():
    report = api.create_tileset(TILESET, str(FIXTURE_IMAGE), (14, 14), padding_px=(1, 1))
    assert report["columns"] == 4 and report["rows"] == 4
    grid = sprytile_utils.get_mat_data(bpy.context, TILESET).grids[0]
    assert tuple(grid.grid) == (14, 14)
    assert tuple(grid.padding) == (1, 1)

    report = api.create_tileset(TILESET, str(FIXTURE_IMAGE), (14, 14), margin_px=(1, 1, 1, 1))
    assert report["columns"] == 4 and report["rows"] == 4
    grid = sprytile_utils.get_mat_data(bpy.context, TILESET).grids[0]
    assert tuple(grid.grid) == (14, 14)
    assert tuple(grid.padding) == (0, 0)
    assert tuple(grid.margin) == (1, 1, 1, 1)


def test_padding_offsets_uvs_into_the_tile():
    api.create_tileset(TILESET, str(FIXTURE_IMAGE), (14, 14), padding_px=(1, 1))
    api.create_tile_object(OBJECT, TILESET, PPU)
    api.place_tiles(OBJECT, TILESET, [{"cell_xy": (0, 0), "tile_xy": (1, 0)}])
    (face,) = read_faces(bpy.data.objects[OBJECT])
    us = [uv[0] for uv in face["uvs"]]
    vs = [uv[1] for uv in face["uvs"]]
    # Tile column 1 of a 16 px pitch starts at 16 px; padding 1 px puts the 14 px of tile at 17..31
    assert_close(min(us), 17 / 64, UV_TOL)
    assert_close(max(us), 31 / 64, UV_TOL)
    # Row 0 from the top is pitch row 3 from the bottom: 48 + 1 .. 48 + 15
    assert_close(min(vs), 49 / 64, UV_TOL)
    assert_close(max(vs), 63 / 64, UV_TOL)


@pytest.mark.parametrize(
    "kwargs, fragment",
    [
        ({"image_path": "tiles_16px.png"}, "absolute"),
        ({"image_path": "/nonexistent/dir/missing.png"}, "does not exist"),
        ({"tile_size_px": (0, 16)}, "tile_size_px"),
        ({"tile_size_px": (16,)}, "tile_size_px"),
        ({"padding_px": (-1, 0)}, "padding_px"),
        ({"margin_px": (0, 0, 0)}, "margin_px"),
        ({"tile_size_px": (128, 128)}, "does not fit"),
        ({"tile_size_px": (16, 15)}, "multiple"),
    ],
)
def test_create_tileset_validation(kwargs, fragment):
    arguments = {"material_name": TILESET, "image_path": str(FIXTURE_IMAGE), "tile_size_px": (16, 16)}
    arguments.update(kwargs)
    with pytest.raises(ValueError, match=fragment):
        api.create_tileset(**arguments)
    assert TILESET not in bpy.data.materials


def test_create_tile_object(tileset):
    report = api.create_tile_object(OBJECT, TILESET, 32)
    assert report == {
        "object_name": OBJECT,
        "material_name": TILESET,
        "grid_id": tileset["grid_id"],
        "pixels_per_unit": 32,
    }
    obj = bpy.data.objects[OBJECT]
    assert obj.type == "MESH"
    assert obj.name in bpy.context.scene.collection.objects
    assert [slot.material.name for slot in obj.material_slots] == [TILESET]
    assert obj.sprytile_gridid == tileset["grid_id"]
    assert bpy.context.scene.sprytile_data.world_pixels == 32
    assert len(obj.data.polygons) == 0


def test_create_tile_object_validation(tileset):
    empty = bpy.data.objects.new(PREFIX + "empty", None)
    link_object(empty)
    with pytest.raises(ValueError, match="not a MESH"):
        api.create_tile_object(PREFIX + "empty", TILESET, 16)
    with pytest.raises(ValueError, match="No material named"):
        api.create_tile_object(OBJECT, PREFIX + "nothing", 16)
    with pytest.raises(ValueError, match="pixels_per_unit"):
        api.create_tile_object(OBJECT, TILESET, 4)
    assert OBJECT not in bpy.data.objects


# --- place_tiles -------------------------------------------------------------------------------------------


def test_place_tile_at_cell_builds_one_textured_quad(tile_object):
    result = api.place_tiles(OBJECT, TILESET, [{"cell_xy": (2, 3), "tile_xy": (1, 0)}])
    assert result == {"built": 1, "remapped": 0, "face_count": 1}
    (face,) = read_faces(tile_object)
    assert_box(face, (2.0, 3.0), (3.0, 4.0), 0.0)
    assert_close(face["normal"][2], 1.0, GEOMETRY_TOL)
    assert_uv_rect(face, tile_rect(1, 0))
    assert_uv_rect(face, (0.25, 0.5, 0.75, 1.0))


def test_placing_again_at_the_same_cell_remaps_the_face(tile_object):
    api.place_tiles(OBJECT, TILESET, [{"cell_xy": (2, 3), "tile_xy": (1, 0)}])
    result = api.place_tiles(OBJECT, TILESET, [{"cell_xy": (2, 3), "tile_xy": (3, 3)}])
    assert result == {"built": 0, "remapped": 1, "face_count": 1}
    (face,) = read_faces(tile_object)
    assert_box(face, (2.0, 3.0), (3.0, 4.0), 0.0)
    assert_uv_rect(face, (0.75, 1.0, 0.0, 0.25))
    # Sprytile row 0 (bottom of the sheet) * 4 columns + column 3
    assert face["tile_id"] == 3


def test_tile_id_layer_uses_sprytile_bottom_origin_rows(tile_object):
    api.place_tiles(OBJECT, TILESET, [{"cell_xy": (0, 0), "tile_xy": (1, 0)}])
    (face,) = read_faces(tile_object)
    assert face["tile_id"] == 3 * 4 + 1


def uv_at_shifted_vertex(face, vertex, dx):
    """The uv of `face`'s corner at `vertex` moved by dx along x."""
    wanted = tuple(round(c, 5) for c in (vertex[0] + dx, vertex[1], vertex[2]))
    (match,) = [uv for vert, uv in zip(face["verts"], face["uvs"]) if tuple(round(c, 5) for c in vert) == wanted]
    return match


def test_flip_x_mirrors_u(tile_object):
    api.place_tiles(OBJECT, TILESET, [{"cell_xy": (0, 0), "tile_xy": (1, 0)}])
    api.place_tiles(OBJECT, TILESET, [{"cell_xy": (5, 0), "tile_xy": (1, 0), "flip_x": True}])
    plain, flipped = read_faces(tile_object)
    u0, u1, _, _ = tile_rect(1, 0)
    for plain_uv, plain_vert in zip(plain["uvs"], plain["verts"]):
        flipped_uv = uv_at_shifted_vertex(flipped, plain_vert, 5)
        assert_close(flipped_uv[0], u0 + u1 - plain_uv[0], UV_TOL)
        assert_close(flipped_uv[1], plain_uv[1], UV_TOL)


def test_flip_y_mirrors_v(tile_object):
    api.place_tiles(OBJECT, TILESET, [{"cell_xy": (0, 0), "tile_xy": (1, 0)}])
    api.place_tiles(OBJECT, TILESET, [{"cell_xy": (5, 0), "tile_xy": (1, 0), "flip_y": True}])
    plain, flipped = read_faces(tile_object)
    _, _, v0, v1 = tile_rect(1, 0)
    for plain_uv, plain_vert in zip(plain["uvs"], plain["verts"]):
        shifted = tuple(round(c, 5) for c in (plain_vert[0] + 5, plain_vert[1], plain_vert[2]))
        match = [uv for vert, uv in zip(flipped["verts"], flipped["uvs"]) if tuple(round(c, 5) for c in vert) == shifted]
        assert len(match) == 1
        assert_close(match[0][0], plain_uv[0], UV_TOL)
        assert_close(match[0][1], v0 + v1 - plain_uv[1], UV_TOL)


def test_xz_plane_offset_is_y_and_normal_is_minus_y(tile_object):
    api.place_tiles(
        OBJECT, TILESET, [{"cell_xy": (1, 2), "tile_xy": (2, 1), "plane": "XZ", "plane_offset_m": 2.0}]
    )
    (face,) = read_faces(tile_object)
    assert_box(face, (1.0, 2.0), 2.0, (2.0, 3.0))
    assert all(v[1] == pytest.approx(2.0, abs=GEOMETRY_TOL) for v in face["verts"])
    assert_close(face["normal"][1], -1.0, GEOMETRY_TOL)
    assert_uv_rect(face, tile_rect(2, 1))


def test_yz_plane_offset_is_x_and_normal_is_plus_x(tile_object):
    api.place_tiles(
        OBJECT, TILESET, [{"cell_xy": (3, 1), "tile_xy": (0, 2), "plane": "YZ", "plane_offset_m": -1.0}]
    )
    (face,) = read_faces(tile_object)
    assert_box(face, -1.0, (3.0, 4.0), (1.0, 2.0))
    assert_close(face["normal"][0], 1.0, GEOMETRY_TOL)
    assert_uv_rect(face, tile_rect(0, 2))


@pytest.mark.parametrize("plane", ["XY", "XZ", "YZ"])
@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_rotation_keeps_the_cell_and_turns_the_picture_counter_clockwise(tile_object, plane, rotation):
    api.place_tiles(
        OBJECT,
        TILESET,
        [{"cell_xy": (2, 3), "tile_xy": (1, 0), "plane": plane, "rotation_deg": rotation}],
    )
    (face,) = read_faces(tile_object)
    axes = {"XY": (0, 1, 2), "XZ": (0, 2, 1), "YZ": (1, 2, 0)}[plane]
    right_axis, up_axis, normal_axis = axes
    assert_box_axes = {right_axis: (2.0, 3.0), up_axis: (3.0, 4.0), normal_axis: 0.0}
    assert_box(face, assert_box_axes[0], assert_box_axes[1], assert_box_axes[2])
    normal_sign = -1.0 if plane == "XZ" else 1.0
    assert_close(face["normal"][normal_axis], normal_sign, GEOMETRY_TOL)

    u0, u1, v0, v1 = tile_rect(1, 0)
    cos, sin = round(math.cos(math.radians(rotation))), round(math.sin(math.radians(rotation)))
    for vert, uv in zip(face["verts"], face["uvs"]):
        # Position inside the cell, -0.5..0.5 along the plane's right/up
        px = vert[right_axis] - 2.5
        py = vert[up_axis] - 3.5
        # The picture turns CCW by the rotation: a point at (px, py) shows the picture at the clockwise-turned spot
        tx = cos * px + sin * py
        ty = cos * py - sin * px
        assert_close(uv[0], u0 + (tx + 0.5) * (u1 - u0), UV_TOL)
        assert_close(uv[1], v0 + (ty + 0.5) * (v1 - v0), UV_TOL)


def test_rotation_90_moves_the_image_top_left_to_the_cell_bottom_left(tile_object):
    api.place_tiles(OBJECT, TILESET, [{"cell_xy": (0, 0), "tile_xy": (1, 0), "rotation_deg": 90}])
    (face,) = read_faces(tile_object)
    u0, u1, v0, v1 = tile_rect(1, 0)
    (corner,) = [uv for vert, uv in zip(face["verts"], face["uvs"]) if vert[0] < 0.5 and vert[1] < 0.5]
    assert_close(corner[0], u0, UV_TOL)
    assert_close(corner[1], v1, UV_TOL)


def test_tile_span_builds_one_quad_with_two_tiles_of_uv(tile_object):
    result = api.place_tiles(
        OBJECT, TILESET, [{"cell_xy": (0, 0), "tile_xy": (1, 2), "tile_span": (2, 1)}]
    )
    assert result["built"] == 1 and result["face_count"] == 1
    (face,) = read_faces(tile_object)
    assert_box(face, (0.0, 2.0), (0.0, 1.0), 0.0)
    assert_uv_rect(face, tile_rect(1, 2, (2, 1)))
    described = api.describe_tile_object(OBJECT)
    assert described["faces"][0]["tile_xy"] == [1, 2]


def test_tall_tile_span_uses_the_top_left_tile_and_rows_downwards(tile_object):
    api.place_tiles(OBJECT, TILESET, [{"cell_xy": (0, 0), "tile_xy": (2, 0), "tile_span": (1, 3)}])
    (face,) = read_faces(tile_object)
    assert_box(face, (0.0, 1.0), (0.0, 3.0), 0.0)
    assert_uv_rect(face, tile_rect(2, 0, (1, 3)))
    assert api.describe_tile_object(OBJECT)["faces"][0]["tile_xy"] == [2, 0]


def test_adjacent_tiles_share_vertices_with_auto_merge(tile_object):
    assert bpy.context.scene.sprytile_data.auto_merge
    api.fill_tiles(OBJECT, TILESET, (0, 0), (1, 1), (0, 0))
    assert len(tile_object.data.polygons) == 4
    assert len(tile_object.data.vertices) == 9


def test_decal_sits_above_its_base_and_remaps_in_place(tile_object):
    api.place_tiles(OBJECT, TILESET, [{"cell_xy": (0, 0), "tile_xy": (0, 0)}])
    result = api.place_tiles(OBJECT, TILESET, [{"cell_xy": (0, 0), "tile_xy": (2, 2), "layer": "DECAL"}])
    assert result == {"built": 1, "remapped": 0, "face_count": 2}
    base, decal = read_faces(tile_object)
    offset = bpy.context.scene.sprytile_data.mesh_decal_offset
    assert_box(base, (0.0, 1.0), (0.0, 1.0), 0.0)
    assert_box(decal, (0.0, 1.0), (0.0, 1.0), offset, tol=1e-6)
    assert_uv_rect(decal, tile_rect(2, 2))
    assert_uv_rect(base, tile_rect(0, 0))

    again = api.place_tiles(OBJECT, TILESET, [{"cell_xy": (0, 0), "tile_xy": (3, 3), "layer": "DECAL"}])
    assert again == {"built": 0, "remapped": 1, "face_count": 2}
    base, decal = read_faces(tile_object)
    assert_uv_rect(decal, tile_rect(3, 3))
    assert_uv_rect(base, tile_rect(0, 0))
    removed = api.remove_tiles(OBJECT, "XY", offset, [[0, 0]])
    assert removed == {"removed": 1, "face_count": 1}


def test_failed_placement_rolls_the_whole_call_back(tile_object):
    api.place_tiles(OBJECT, TILESET, [{"cell_xy": (9, 9), "tile_xy": (0, 0)}])
    with pytest.raises(RuntimeError, match="DECAL needs a BASE"):
        api.place_tiles(
            OBJECT,
            TILESET,
            [
                {"cell_xy": (0, 0), "tile_xy": (0, 0)},
                {"cell_xy": (5, 5), "tile_xy": (0, 0), "layer": "DECAL"},
            ],
        )
    assert tile_object.mode == "OBJECT"
    assert len(tile_object.data.polygons) == 1
    assert len(tile_object.data.vertices) == 4


def test_object_transform_does_not_move_cells_in_world_space(tile_object):
    tile_object.location = (5.0, -2.0, 1.0)
    api.place_tiles(OBJECT, TILESET, [{"cell_xy": (2, 3), "tile_xy": (1, 0)}])
    (face,) = read_faces(tile_object)
    assert_box(face, (2.0, 3.0), (3.0, 4.0), 0.0)


def test_pixels_per_unit_scales_the_cell(tileset):
    api.create_tile_object(OBJECT, TILESET, 32)
    api.place_tiles(OBJECT, TILESET, [{"cell_xy": (2, 3), "tile_xy": (1, 0)}])
    (face,) = read_faces(bpy.data.objects[OBJECT])
    assert_box(face, (1.0, 1.5), (1.5, 2.0), 0.0)


def test_two_tilesets_on_one_object_get_their_own_material_slots(tile_object):
    api.create_tileset(PREFIX + "other", str(OTHER_IMAGE), (TILE_PX, TILE_PX))
    api.place_tiles(OBJECT, TILESET, [{"cell_xy": (0, 0), "tile_xy": (0, 0)}])
    api.place_tiles(OBJECT, PREFIX + "other", [{"cell_xy": (1, 0), "tile_xy": (0, 0)}])
    described = api.describe_tile_object(OBJECT)
    assert [f["material"] for f in described["faces"]] == [TILESET, PREFIX + "other"]
    assert tile_object.sprytile_gridid == sprytile_utils.get_mat_data(bpy.context, TILESET).grids[0].id


# --- fill / remove -----------------------------------------------------------------------------------------


def test_fill_then_remove(tile_object):
    filled = api.fill_tiles(OBJECT, TILESET, (0, 0), (3, 3), (0, 0))
    assert filled == {"built": 16, "remapped": 0, "face_count": 16}
    assert len(tile_object.data.polygons) == 16

    removed = api.remove_tiles(OBJECT, "XY", 0.0, [[0, 0]])
    assert removed == {"removed": 1, "face_count": 15}
    assert len(tile_object.data.polygons) == 15
    for face in read_faces(tile_object):
        lows = (min(v[0] for v in face["verts"]), min(v[1] for v in face["verts"]))
        assert lows != (0.0, 0.0)


def test_fill_over_existing_tiles_remaps(tile_object):
    api.fill_tiles(OBJECT, TILESET, (0, 0), (1, 1), (0, 0))
    again = api.fill_tiles(OBJECT, TILESET, (0, 0), (1, 1), (2, 2))
    assert again == {"built": 0, "remapped": 4, "face_count": 4}
    for face in read_faces(tile_object):
        assert_uv_rect(face, tile_rect(2, 2))


def test_fill_on_wall_plane_with_offset(tile_object):
    api.fill_tiles(OBJECT, TILESET, (0, 0), (7, 3), (1, 0), plane="XZ", plane_offset_m=0.0)
    faces = read_faces(tile_object)
    assert len(faces) == 32
    assert all(f["normal"][1] == pytest.approx(-1.0, abs=GEOMETRY_TOL) for f in faces)
    assert span([v[0] for f in faces for v in f["verts"]]) == (0.0, 8.0)
    assert span([v[2] for f in faces for v in f["verts"]]) == (0.0, 4.0)


def test_remove_only_touches_the_given_plane_and_cells(tile_object):
    api.fill_tiles(OBJECT, TILESET, (0, 0), (1, 1), (0, 0))
    api.fill_tiles(OBJECT, TILESET, (0, 0), (1, 1), (1, 0), plane="XZ", plane_offset_m=0.0)
    assert len(tile_object.data.polygons) == 8
    assert api.remove_tiles(OBJECT, "XY", 1.0, [[0, 0]])["removed"] == 0
    assert api.remove_tiles(OBJECT, "XY", 0.0, [[5, 5]])["removed"] == 0
    result = api.remove_tiles(OBJECT, "XZ", 0.0, [[0, 0], [1, 1]])
    assert result == {"removed": 2, "face_count": 6}
    assert len([f for f in read_faces(tile_object) if abs(f["normal"][2]) > 0.5]) == 4


def test_remove_covers_every_cell_of_a_span_face(tile_object):
    api.place_tiles(OBJECT, TILESET, [{"cell_xy": (0, 0), "tile_xy": (0, 0), "tile_span": (2, 1)}])
    assert api.remove_tiles(OBJECT, "XY", 0.0, [[1, 0]]) == {"removed": 1, "face_count": 0}


# --- paint_faces -------------------------------------------------------------------------------------------


def test_paint_faces_remaps_an_existing_face(tile_object):
    api.fill_tiles(OBJECT, TILESET, (0, 0), (1, 0), (0, 0))
    result = api.paint_faces(OBJECT, TILESET, [1], (2, 1))
    assert result == {"painted": 1, "face_count": 2}
    first, second = read_faces(tile_object)
    assert_uv_rect(first, tile_rect(0, 0))
    assert_uv_rect(second, tile_rect(2, 1))
    described = api.describe_tile_object(OBJECT)
    assert [f["tile_xy"] for f in described["faces"]] == [[0, 0], [2, 1]]


@pytest.mark.parametrize("plane", ["XY", "XZ", "YZ"])
@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_paint_agrees_with_place_on_every_plane(tile_object, plane, rotation):
    """Painting a face upright gives the UVs placing the same tile on that plane gives."""
    api.place_tiles(
        OBJECT,
        TILESET,
        [{"cell_xy": (0, 0), "tile_xy": (1, 2), "plane": plane, "rotation_deg": rotation, "flip_x": True}],
    )
    (placed,) = read_faces(tile_object)
    api.paint_faces(OBJECT, TILESET, [0], (3, 0))
    api.paint_faces(OBJECT, TILESET, [0], (1, 2), rotation_deg=rotation, flip_x=True)
    (painted,) = read_faces(tile_object)
    for placed_uv, painted_uv in zip(placed["uvs"], painted["uvs"]):
        assert_close(painted_uv[0], placed_uv[0], UV_TOL)
        assert_close(painted_uv[1], placed_uv[1], UV_TOL)


def test_paint_faces_sets_the_tilesets_material(tileset):
    api.create_tileset(PREFIX + "other", str(OTHER_IMAGE), (TILE_PX, TILE_PX))
    api.create_tile_object(OBJECT, TILESET, PPU)
    api.place_tiles(OBJECT, TILESET, [{"cell_xy": (0, 0), "tile_xy": (0, 0)}])
    api.paint_faces(OBJECT, PREFIX + "other", [0], (1, 1))
    described = api.describe_tile_object(OBJECT)
    assert described["faces"][0]["material"] == PREFIX + "other"
    assert described["faces"][0]["tile_xy"] == [1, 1]


def test_paint_faces_validation(tile_object):
    api.place_tiles(OBJECT, TILESET, [{"cell_xy": (0, 0), "tile_xy": (0, 0)}])
    with pytest.raises(ValueError, match="out of range 0-0"):
        api.paint_faces(OBJECT, TILESET, [1], (0, 0))
    with pytest.raises(ValueError, match="face_indices is empty"):
        api.paint_faces(OBJECT, TILESET, [], (0, 0))
    with pytest.raises(ValueError, match="columns 0-3"):
        api.paint_faces(OBJECT, TILESET, [0], (4, 0))
    assert tile_object.mode == "OBJECT"


# --- describe_tile_object ----------------------------------------------------------------------------------


def test_describe_agrees_with_what_was_placed(tile_object):
    placements = [
        {"cell_xy": (0, 0), "tile_xy": (0, 0)},
        {"cell_xy": (1, 0), "tile_xy": (3, 0)},
        {"cell_xy": (2, 0), "tile_xy": (0, 3)},
        {"cell_xy": (3, 0), "tile_xy": (2, 1), "plane": "XZ", "plane_offset_m": 1.0},
        {"cell_xy": (4, 4), "tile_xy": (1, 2), "plane": "YZ", "plane_offset_m": -2.0},
    ]
    api.place_tiles(OBJECT, TILESET, placements)
    described = api.describe_tile_object(OBJECT)
    assert described["object_name"] == OBJECT
    assert described["face_count"] == 5
    assert described["truncated"] is False
    faces = {tuple(f["tile_xy"]): f for f in described["faces"]}
    assert set(faces) == {(0, 0), (3, 0), (0, 3), (2, 1), (1, 2)}
    assert [f["index"] for f in described["faces"]] == [0, 1, 2, 3, 4]
    assert faces[(3, 0)]["center_m"] == pytest.approx([1.5, 0.5, 0.0], abs=1e-5)
    assert faces[(3, 0)]["normal"] == pytest.approx([0.0, 0.0, 1.0], abs=1e-5)
    assert faces[(2, 1)]["center_m"] == pytest.approx([3.5, 1.0, 0.5], abs=1e-5)
    assert faces[(2, 1)]["normal"] == pytest.approx([0.0, -1.0, 0.0], abs=1e-5)
    assert faces[(1, 2)]["center_m"] == pytest.approx([-2.0, 4.5, 4.5], abs=1e-5)
    assert faces[(1, 2)]["normal"] == pytest.approx([1.0, 0.0, 0.0], abs=1e-5)
    assert {f["material"] for f in described["faces"]} == {TILESET}


def test_describe_truncates(tile_object):
    api.fill_tiles(OBJECT, TILESET, (0, 0), (3, 3), (0, 0))
    described = api.describe_tile_object(OBJECT, max_faces=3)
    assert described["face_count"] == 16
    assert described["truncated"] is True
    assert [f["index"] for f in described["faces"]] == [0, 1, 2]
    assert api.describe_tile_object(OBJECT, max_faces=0)["faces"] == []


def test_describe_empty_object_and_untiled_face(tile_object):
    assert api.describe_tile_object(OBJECT) == {
        "object_name": OBJECT,
        "face_count": 0,
        "truncated": False,
        "faces": [],
    }
    mesh = bpy.data.meshes.new(PREFIX + "plain")
    bm = bmesh.new()
    bm.faces.new([bm.verts.new(v) for v in ((0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0))])
    bm.to_mesh(mesh)
    bm.free()
    plain = bpy.data.objects.new(PREFIX + "plain", mesh)
    link_object(plain)
    face = api.describe_tile_object(plain.name)["faces"][0]
    assert face["tile_xy"] == [-1, -1]
    assert face["material"] == ""


def test_describe_works_in_edit_mode_without_leaving_it(tile_object):
    api.place_tiles(OBJECT, TILESET, [{"cell_xy": (0, 0), "tile_xy": (2, 2)}])
    bpy.context.view_layer.objects.active = tile_object
    bpy.ops.object.mode_set(mode="EDIT")
    described = api.describe_tile_object(OBJECT)
    assert tile_object.mode == "EDIT"
    assert described["faces"][0]["tile_xy"] == [2, 2]
    bpy.ops.object.mode_set(mode="OBJECT")


# --- validation --------------------------------------------------------------------------------------------


def test_tile_outside_the_sheet_names_the_valid_range(tile_object):
    with pytest.raises(ValueError, match=r"columns 0-3"):
        api.place_tiles(OBJECT, TILESET, [{"cell_xy": (0, 0), "tile_xy": (4, 0)}])
    with pytest.raises(ValueError, match=r"rows 0-3"):
        api.place_tiles(OBJECT, TILESET, [{"cell_xy": (0, 0), "tile_xy": (0, 4)}])
    with pytest.raises(ValueError, match=r"columns 0-3"):
        api.place_tiles(OBJECT, TILESET, [{"cell_xy": (0, 0), "tile_xy": (3, 0), "tile_span": (2, 1)}])
    with pytest.raises(ValueError, match=r"columns 0-3"):
        api.fill_tiles(OBJECT, TILESET, (0, 0), (1, 1), (-1, 0))
    assert len(tile_object.data.polygons) == 0


@pytest.mark.parametrize(
    "placement, fragment",
    [
        ({"cell_xy": (0, 0), "tile_xy": (0, 0), "rotation_deg": 45}, "rotation_deg"),
        ({"cell_xy": (0, 0), "tile_xy": (0, 0), "rotation_deg": -90}, "rotation_deg"),
        ({"cell_xy": (0, 0), "tile_xy": (0, 0), "plane": "ZZ"}, "plane"),
        ({"cell_xy": (0, 0), "tile_xy": (0, 0), "layer": "TOP"}, "layer"),
        ({"cell_xy": (0, 0), "tile_xy": (0, 0), "tile_span": (0, 1)}, "tile_span"),
        ({"cell_xy": (0, 0), "tile_xy": (0, 0), "plane_offset_m": float("nan")}, "plane_offset_m"),
        ({"cell_xy": (0, 0), "tile_xy": (0, 0), "flip_x": 1}, "flip_x"),
        ({"cell_xy": (0.5, 0), "tile_xy": (0, 0)}, "cell_xy"),
        ({"cell_xy": (0, 0)}, "missing required"),
        ({"cell_xy": (0, 0), "tile_xy": (0, 0), "colour": 1}, "unknown keys"),
    ],
)
def test_bad_placements_raise_value_error(tile_object, placement, fragment):
    with pytest.raises(ValueError, match=fragment):
        api.place_tiles(OBJECT, TILESET, [placement])
    assert len(tile_object.data.polygons) == 0


def test_bad_object_and_tileset_names(tile_object):
    ok = [{"cell_xy": (0, 0), "tile_xy": (0, 0)}]
    with pytest.raises(ValueError, match="No object named"):
        api.place_tiles(PREFIX + "ghost", TILESET, ok)
    with pytest.raises(ValueError, match="No material named"):
        api.place_tiles(OBJECT, PREFIX + "ghost", ok)
    with pytest.raises(ValueError, match="placements is empty"):
        api.place_tiles(OBJECT, TILESET, [])
    empty = bpy.data.objects.new(PREFIX + "empty", None)
    link_object(empty)
    with pytest.raises(ValueError, match="not a MESH"):
        api.place_tiles(PREFIX + "empty", TILESET, ok)
    with pytest.raises(ValueError, match="not a MESH"):
        api.describe_tile_object(PREFIX + "empty")
    material = bpy.data.materials.new(PREFIX + "plain_material")
    with pytest.raises(ValueError, match="not a tileset"):
        api.place_tiles(OBJECT, material.name, ok)
    with pytest.raises(ValueError, match="cell_max_xy"):
        api.fill_tiles(OBJECT, TILESET, (2, 2), (1, 1), (0, 0))
    with pytest.raises(ValueError, match="plane"):
        api.remove_tiles(OBJECT, "AB", 0.0, [[0, 0]])
    with pytest.raises(ValueError, match="cells is empty"):
        api.remove_tiles(OBJECT, "XY", 0.0, [])


def test_hidden_object_is_refused(tile_object):
    tile_object.hide_set(True)
    with pytest.raises(ValueError, match="hidden"):
        api.place_tiles(OBJECT, TILESET, [{"cell_xy": (0, 0), "tile_xy": (0, 0)}])


# --- state restoration -------------------------------------------------------------------------------------


def _scratch_object(name):
    obj = bpy.data.objects.new(PREFIX + name, bpy.data.meshes.new(PREFIX + name))
    link_object(obj)
    return obj


def test_mode_selection_and_active_object_are_restored(tile_object):
    other = _scratch_object("other")
    third = _scratch_object("third")
    view_layer = bpy.context.view_layer
    for obj in view_layer.objects:
        obj.select_set(False)
    other.select_set(True)
    third.select_set(True)
    view_layer.objects.active = other

    api.place_tiles(OBJECT, TILESET, [{"cell_xy": (0, 0), "tile_xy": (0, 0)}])
    api.paint_faces(OBJECT, TILESET, [0], (1, 1))
    api.remove_tiles(OBJECT, "XY", 0.0, [[0, 0]])
    api.describe_tile_object(OBJECT)

    assert view_layer.objects.active == other
    assert {o.name for o in view_layer.objects if o.select_get()} == {other.name, third.name}
    assert tile_object.mode == "OBJECT"
    assert other.mode == "OBJECT"


def test_no_active_object_stays_none(tile_object):
    view_layer = bpy.context.view_layer
    view_layer.objects.active = None
    api.place_tiles(OBJECT, TILESET, [{"cell_xy": (0, 0), "tile_xy": (0, 0)}])
    assert view_layer.objects.active is None
    assert not any(o.select_get() for o in view_layer.objects)


def test_target_already_in_edit_mode_stays_in_edit_mode(tile_object):
    view_layer = bpy.context.view_layer
    view_layer.objects.active = tile_object
    tile_object.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    result = api.place_tiles(OBJECT, TILESET, [{"cell_xy": (2, 3), "tile_xy": (1, 0)}])
    assert result["face_count"] == 1
    assert tile_object.mode == "EDIT"
    assert view_layer.objects.active == tile_object
    bm = bmesh.from_edit_mesh(tile_object.data)
    assert len(bm.faces) == 1
    bpy.ops.object.mode_set(mode="OBJECT")
    (face,) = read_faces(tile_object)
    assert_box(face, (2.0, 3.0), (3.0, 4.0), 0.0)


def test_other_object_in_edit_mode_is_restored_to_edit_mode(tile_object):
    other = _scratch_object("editing")
    view_layer = bpy.context.view_layer
    view_layer.objects.active = other
    other.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")

    api.place_tiles(OBJECT, TILESET, [{"cell_xy": (0, 0), "tile_xy": (0, 0)}])
    assert other.mode == "EDIT"
    assert view_layer.objects.active == other
    assert other.select_get()
    assert tile_object.mode == "OBJECT"
    assert len(tile_object.data.polygons) == 1
    bpy.ops.object.mode_set(mode="OBJECT")


def test_mode_restored_when_the_call_fails(tile_object):
    other = _scratch_object("other")
    view_layer = bpy.context.view_layer
    view_layer.objects.active = other
    other.select_set(True)
    with pytest.raises(RuntimeError):
        api.place_tiles(OBJECT, TILESET, [{"cell_xy": (5, 5), "tile_xy": (0, 0), "layer": "DECAL"}])
    assert tile_object.mode == "OBJECT"
    assert view_layer.objects.active == other
    assert other.select_get() and not tile_object.select_get()


def test_sprytile_settings_are_restored(tile_object):
    data = bpy.context.scene.sprytile_data
    grid = sprytile_utils.get_mat_data(bpy.context, TILESET).grids[0]
    data.uv_flip_x = True
    data.uv_flip_y = True
    data.mesh_rotate = math.radians(180)
    data.paint_align = "TOP_LEFT"
    data.paint_mode = "FILL"
    data.work_layer = "DECAL_1"
    data.work_layer_mode = "UV_DECAL"
    data.world_pixels = 64
    grid.tile_selection = (1, 2, 3, 1)
    tile_object.sprytile_gridid = 12345

    snapshot = (
        data.uv_flip_x,
        data.uv_flip_y,
        data.mesh_rotate,
        data.paint_align,
        data.paint_mode,
        data.work_layer,
        data.work_layer_mode,
        data.world_pixels,
        tuple(grid.tile_selection),
        tile_object.sprytile_gridid,
        tuple(bpy.context.scene.cursor.location),
        data.auto_merge,
    )

    def current():
        return (
            data.uv_flip_x,
            data.uv_flip_y,
            data.mesh_rotate,
            data.paint_align,
            data.paint_mode,
            data.work_layer,
            data.work_layer_mode,
            data.world_pixels,
            tuple(grid.tile_selection),
            tile_object.sprytile_gridid,
            tuple(bpy.context.scene.cursor.location),
            data.auto_merge,
        )

    api.place_tiles(
        OBJECT,
        TILESET,
        [
            {"cell_xy": (0, 0), "tile_xy": (1, 1), "rotation_deg": 90, "flip_x": True, "layer": "BASE"},
            {"cell_xy": (0, 0), "tile_xy": (2, 1), "layer": "DECAL"},
        ],
    )
    assert current() == snapshot
    api.paint_faces(OBJECT, TILESET, [0], (3, 3), rotation_deg=270, flip_y=True)
    assert current() == snapshot
    with pytest.raises(RuntimeError):
        api.place_tiles(OBJECT, TILESET, [{"cell_xy": (7, 7), "tile_xy": (0, 0), "layer": "DECAL"}])
    assert current() == snapshot
    # The faces carry the settings that were applied, not the scene's leftovers
    base, decal = read_faces(tile_object)
    assert_uv_rect(decal, tile_rect(2, 1))
    assert_close(decal["normal"][2], 1.0, GEOMETRY_TOL)
    # Painting used the object's own pixel density, not the scene's 64
    assert_box(base, (0.0, 1.0), (0.0, 1.0), 0.0)


def test_scene_cursor_does_not_move_the_grid(tile_object):
    bpy.context.scene.cursor.location = (10.0, 20.0, 30.0)
    api.place_tiles(OBJECT, TILESET, [{"cell_xy": (2, 3), "tile_xy": (1, 0)}])
    (face,) = read_faces(tile_object)
    assert_box(face, (2.0, 3.0), (3.0, 4.0), 0.0)
    assert tuple(bpy.context.scene.cursor.location) == (10.0, 20.0, 30.0)


def test_api_re_registers_the_sprytile_properties_after_teardown():
    """'Remove Sprytile data' unregisters the scene properties; the API puts them back."""
    bpy.ops.sprytile.props_teardown()
    assert not hasattr(bpy.types.Scene, "sprytile_data")
    report = api.create_tileset(TILESET, str(FIXTURE_IMAGE), (TILE_PX, TILE_PX))
    assert hasattr(bpy.types.Scene, "sprytile_data")
    assert hasattr(bpy.types.Object, "sprytile_gridid")
    assert report["columns"] == 4
    api.create_tile_object(OBJECT, TILESET, PPU)
    assert api.place_tiles(OBJECT, TILESET, [{"cell_xy": (0, 0), "tile_xy": (0, 0)}])["face_count"] == 1
