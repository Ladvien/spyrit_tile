"""describe_tile_object / describe_scene read back what place_tiles was given, inside Blender.

Ground truth is the placement dicts handed to ``place_tiles``; the decoder is exercised through the add-on's
own encoder (the real placement path), not through a test-side re-implementation of the bit layout.
"""

import importlib
from pathlib import Path

import bmesh
import bpy
import pytest

api = importlib.import_module("bl_ext.user_default.spyrite_tile.api")
sprytile_core = importlib.import_module("bl_ext.user_default.spyrite_tile.sprytile_core")

FIXTURE_IMAGE = Path(__file__).resolve().parent.parent / "fixtures" / "tiles_16px.png"
# tests/fixtures/tiles_16px.spyrite.yaml
FIXTURE_NAMES = {(0, 0): "grass", (1, 0): "stone", (2, 0): "wall_top", (0, 1): "water"}
PREFIX = "readback_test_"
TILESET = PREFIX + "tiles"
OBJECT = PREFIX + "obj"
PPU = 16
PLANE_OFFSETS = {"XY": 0.0, "XZ": 6.0, "YZ": 9.0}
PLANE_FACING = {"XY": (2, 1), "XZ": (1, 1), "YZ": (0, 1)}  # (axis, sign of the normal)
FLIPS = [(False, False), (True, False), (False, True), (True, True)]


def _remove_test_data():
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for obj in [o for o in bpy.data.objects if o.name.startswith(PREFIX)]:
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in [m for m in bpy.data.meshes if m.name.startswith(PREFIX)]:
        bpy.data.meshes.remove(mesh)
    for material in [m for m in bpy.data.materials if m.name.startswith(PREFIX)]:
        bpy.data.materials.remove(material)
    sprytile_core.validate_grids(bpy.context.scene)


@pytest.fixture(autouse=True)
def clean_scene():
    _remove_test_data()
    yield
    _remove_test_data()


@pytest.fixture
def tile_object():
    # a reused material would leave TILESET undefined: fail here, not in create_tile_object
    assert api.create_tileset(TILESET, str(FIXTURE_IMAGE), (16, 16))["reused_material"] is None
    api.create_tile_object(OBJECT, TILESET, PPU)
    return bpy.data.objects[OBJECT]


def orientation_placements(plane):
    return [
        {
            "cell_xy": [x, y],
            "tile_xy": [(x + y) % 4, (x + 2 * y) % 4],
            "plane": plane,
            "plane_offset_m": PLANE_OFFSETS[plane],
            "rotation_deg": 90 * x,
            "flip_x": FLIPS[y][0],
            "flip_y": FLIPS[y][1],
        }
        for x in range(4)
        for y in range(4)
    ]


@pytest.mark.parametrize("plane", ["XY", "XZ", "YZ"])
def test_every_orientation_reads_back_as_placed(tile_object, plane):
    placements = orientation_placements(plane)
    api.place_tiles(OBJECT, TILESET, placements)
    described = api.describe_tile_object(OBJECT)
    assert described["face_count"] == 16
    by_cell = {tuple(face["cell_xy"]): face for face in described["faces"]}
    assert len(by_cell) == 16
    axis, sign = PLANE_FACING[plane]
    for placement in placements:
        face = by_cell[tuple(placement["cell_xy"])]
        assert face["rotation_deg"] == placement["rotation_deg"]
        assert face["flip_x"] is placement["flip_x"]
        assert face["flip_y"] is placement["flip_y"]
        assert face["plane"] == plane
        assert face["facing"] == 1
        assert face["plane_offset_m"] == placement["plane_offset_m"]
        assert face["layer"] == "BASE"
        assert face["tile_xy"] == placement["tile_xy"]
        assert face["tile_span"] == [1, 1]
        assert face["on_grid"] is True
        assert face["tileset"] == TILESET
        assert face["tile"] == FIXTURE_NAMES.get(tuple(placement["tile_xy"]))
        assert face["center_m"][axis] == pytest.approx(placement["plane_offset_m"])


def test_all_three_planes_in_one_object(tile_object):
    for plane in PLANE_OFFSETS:
        api.place_tiles(OBJECT, TILESET, orientation_placements(plane))
    faces = api.describe_tile_object(OBJECT)["faces"]
    assert len(faces) == 48
    for plane in PLANE_OFFSETS:
        assert sum(1 for f in faces if f["plane"] == plane) == 16


def test_decal_reports_layer_and_lifted_offset(tile_object):
    api.place_tiles(
        OBJECT,
        TILESET,
        [
            {"cell_xy": [2, 1], "tile_xy": [0, 0]},
            {"cell_xy": [2, 1], "tile_xy": [1, 1], "layer": "DECAL", "rotation_deg": 180, "flip_y": True},
        ],
    )
    faces = api.describe_tile_object(OBJECT)["faces"]
    assert len(faces) == 2
    base = next(f for f in faces if f["layer"] == "BASE")
    decal = next(f for f in faces if f["layer"] == "DECAL")
    assert base["plane_offset_m"] == 0.0
    assert decal["plane_offset_m"] == 0.002
    assert decal["cell_xy"] == base["cell_xy"] == [2, 1]
    assert decal["on_grid"] is True  # a whole-cell rectangle on one (lifted) offset
    assert (decal["rotation_deg"], decal["flip_x"], decal["flip_y"]) == (180, False, True)


def test_multi_tile_span_reads_back(tile_object):
    api.place_tiles(OBJECT, TILESET, [{"cell_xy": [1, 1], "tile_xy": [0, 2], "tile_span": [2, 1]}])
    face = api.describe_tile_object(OBJECT)["faces"][0]
    assert face["tile_span"] == [2, 1]
    assert face["tile_xy"] == [0, 2]
    assert face["cell_xy"] == [1, 1]
    assert face["on_grid"] is True


def test_scratch_triangle_is_off_grid(tile_object):
    mesh = tile_object.data
    bm = bmesh.new()
    bm.faces.new([bm.verts.new(v) for v in ((0, 0, 0), (1, 0, 0), (0, 1, 0))])
    bm.to_mesh(mesh)
    bm.free()
    api.paint_faces(OBJECT, TILESET, [0], (3, 3), rotation_deg=90, flip_x=True)
    face = api.describe_tile_object(OBJECT)["faces"][0]
    assert face["on_grid"] is False
    assert face["plane"] == "XY"
    assert face["facing"] == 1
    assert face["tile_xy"] == [3, 3]
    assert face["rotation_deg"] == 90 and face["flip_x"] is True and face["flip_y"] is False


def test_downward_face_has_negative_facing_and_offgrid_quad_is_flagged(tile_object):
    mesh = tile_object.data
    bm = bmesh.new()
    # winding makes the normal -Z; the quad is 1 x 1.5 cells, so not whole cells
    bm.faces.new([bm.verts.new(v) for v in ((0, 0, 0), (0, 1.5, 0), (1, 1.5, 0), (1, 0, 0))])
    bm.to_mesh(mesh)
    bm.free()
    face = api.describe_tile_object(OBJECT)["faces"][0]
    assert face["plane"] == "XY" and face["facing"] == -1
    assert face["on_grid"] is False


def test_untiled_face_has_neutral_orientation():
    mesh = bpy.data.meshes.new(PREFIX + "plain")
    bm = bmesh.new()
    bm.faces.new([bm.verts.new(v) for v in ((0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0))])
    bm.to_mesh(mesh)
    bm.free()
    plain = bpy.data.objects.new(PREFIX + "plain", mesh)
    bpy.context.scene.collection.objects.link(plain)
    bpy.context.view_layer.update()
    face = api.describe_tile_object(plain.name)["faces"][0]
    assert (face["rotation_deg"], face["flip_x"], face["flip_y"], face["layer"]) == (0, False, False, "BASE")
    assert face["tileset"] == "" and face["tile_span"] == [1, 1]
    assert face["plane"] == "XY"


def test_describe_scene_lists_tileset_and_object(tile_object):
    api.fill_tiles(OBJECT, TILESET, (0, 0), (2, 1), (1, 1))
    scene = api.describe_scene()
    tileset = next(t for t in scene["tilesets"] if t["material_name"] == TILESET)
    assert tileset["image_size_px"] == [64, 64]
    assert tileset["tile_size_px"] == [16, 16]
    assert tileset["padding_px"] == [0, 0]
    assert tileset["margin_px"] == [0, 0, 0, 0]
    assert (tileset["columns"], tileset["rows"]) == (4, 4)
    assert Path(tileset["image_path"]).resolve() == FIXTURE_IMAGE.resolve()
    assert sorted(tileset["tile_names"]) == ["grass", "stone", "wall_top", "water"]
    obj = next(o for o in scene["tile_objects"] if o["object_name"] == OBJECT)
    assert obj["face_count"] == 6
    assert obj["pixels_per_unit"] == PPU
    assert obj["material_name"] == TILESET
    assert obj["grid_id"] == tileset["grid_id"]
    assert obj["location_m"] == [0.0, 0.0, 0.0]
    assert obj["overlay_of"] is None
    assert scene["removed_tilesets"] == []
    assert set(scene["settings"]) == {"world_pixels", "mesh_decal_offset", "auto_merge"}
    assert scene["settings"]["mesh_decal_offset"] == pytest.approx(0.002)


def test_sidecar_that_does_not_fit_the_layout_still_reads_back():
    """The fixture's sidecar names (2, 0), which a 32 px layout of the 64 px image does not have."""
    assert api.create_tileset(TILESET, str(FIXTURE_IMAGE), (32, 32))["reused_material"] is None
    api.create_tile_object(OBJECT, TILESET, PPU)
    api.place_tiles(OBJECT, TILESET, [{"cell_xy": [0, 0], "tile": "grass"}, {"cell_xy": [1, 0], "tile_xy": [1, 1]}])
    faces = sorted(api.describe_tile_object(OBJECT)["faces"], key=lambda f: f["cell_xy"])
    assert [(f["tile_xy"], f["tile"]) for f in faces] == [([0, 0], "grass"), ([1, 1], None)]
    tileset = next(t for t in api.describe_scene()["tilesets"] if t["material_name"] == TILESET)
    assert (tileset["columns"], tileset["rows"]) == (2, 2)
    assert tileset["tile_names"]["wall_top"]["xy"] == [2, 0]
