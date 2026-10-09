"""build_room / extrude_edge / move_faces, inside Blender.

Ground truth is geometry computed by hand from the plan: a 16 px tile at 16 pixels per unit is a 1 m cell, so
every offset below is a small integer in metres. ``describe_tile_object`` is the read-back (it has its own
tests); UV consistency after ``move_faces`` is proved against the UVs of a freshly placed face of the same
tile and orientation, read straight from the mesh loops.
"""

import importlib
import re
import shutil
from pathlib import Path

import bmesh
import bpy
import pytest

api = importlib.import_module("bl_ext.user_default.spyrite_tile.api")
sprytile_core = importlib.import_module("bl_ext.user_default.spyrite_tile.sprytile_core")

FIXTURE_IMAGE = Path(__file__).resolve().parent.parent / "fixtures" / "tiles_16px.png"
PREFIX = "composites_test_"
TILESET = PREFIX + "tiles"
OBJECT = PREFIX + "obj"
PPU = 16
FLOOR = (0, 0)
WALL = (1, 0)
CEILING = (2, 0)
UV_TOLERANCE = 1e-3


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
    assert api.create_tileset(TILESET, str(FIXTURE_IMAGE), (16, 16))["reused_material"] is None
    api.create_tile_object(OBJECT, TILESET, PPU)
    return bpy.data.objects[OBJECT]


def _faces():
    return api.describe_tile_object(OBJECT, max_faces=10_000)["faces"]


def _summary(faces, plane):
    return sorted(
        (tuple(f["cell_xy"]), f["plane_offset_m"], tuple(f["tile_xy"]), f["facing"])
        for f in faces
        if f["plane"] == plane
    )


def _cells(xs, ys):
    return [(x, y) for y in ys for x in xs]


def _face_uvs(face_index):
    """The face's loop UVs, sorted (a quad's loop order is not part of the contract)."""
    mesh = bmesh.new()
    mesh.from_mesh(bpy.data.objects[OBJECT].data)
    try:
        mesh.faces.ensure_lookup_table()
        uv_layer = mesh.loops.layers.uv.verify()
        return sorted((round(loop[uv_layer].uv[0], 4), round(loop[uv_layer].uv[1], 4)) for loop in mesh.faces[face_index].loops)
    finally:
        mesh.free()


def _assert_uvs_equal(actual, expected):
    assert len(actual) == len(expected)
    for got, want in zip(actual, expected):
        assert got == pytest.approx(want, abs=UV_TOLERANCE)


def _scramble_uvs(face_index):
    obj = bpy.data.objects[OBJECT]
    mesh = bmesh.new()
    mesh.from_mesh(obj.data)
    try:
        mesh.faces.ensure_lookup_table()
        uv_layer = mesh.loops.layers.uv.verify()
        for loop in mesh.faces[face_index].loops:
            loop[uv_layer].uv = (0.5, 0.5)
        mesh.to_mesh(obj.data)
    finally:
        mesh.free()
    obj.data.update()


# ---------------------------------------------------------------------------
# build_room
# ---------------------------------------------------------------------------


def test_build_room_counts_planes_and_offsets(tile_object):
    report = api.build_room(OBJECT, TILESET, (4, 3, 2), FLOOR, WALL)
    assert report == {
        "floor": {"built": 12, "remapped": 0, "face_count": 26},
        "walls": {
            "back": {"built": 8, "remapped": 0, "face_count": 26},
            "left": {"built": 6, "remapped": 0, "face_count": 26},
        },
        "ceiling": None,
        "face_count": 26,
    }
    faces = _faces()
    assert len(faces) == 26
    assert all(f["on_grid"] and f["facing"] == 1 for f in faces)

    # floor: 4 x 3 cells on z = 0
    assert _summary(faces, "XY") == sorted(
        (cell, 0.0, FLOOR, 1) for cell in _cells(range(4), range(3))
    )
    # back wall: XZ at y = 3 m (normal -Y, toward the viewer), x cells 0-3, z rows 0-1
    assert _summary(faces, "XZ") == sorted(
        (cell, 3.0, WALL, 1) for cell in _cells(range(4), range(2))
    )
    # left wall: YZ at x = 0, y cells 0-2, z rows 0-1
    assert _summary(faces, "YZ") == sorted(
        (cell, 0.0, WALL, 1) for cell in _cells(range(3), range(2))
    )
    # world geometry: the room spans 4 x 3 x 2 m
    xs, ys, zs = zip(*(f["center_m"] for f in faces))
    assert (min(xs), max(xs)) == pytest.approx((0.0, 3.5))
    assert (min(ys), max(ys)) == pytest.approx((0.5, 3.0))
    assert (min(zs), max(zs)) == pytest.approx((0.0, 1.5))
    back = next(f for f in faces if f["plane"] == "XZ")
    assert back["normal"] == pytest.approx([0.0, -1.0, 0.0])


def test_build_room_origin_offset_ceiling_and_names(tile_object):
    report = api.build_room(
        OBJECT,
        TILESET,
        (2, 2, 3),
        [3, 3],
        [1, 1],
        walls=("left",),
        origin_cell=(2, 1),
        floor_offset_m=2.0,
        ceiling_tile=CEILING,
    )
    assert report["walls"] == {"left": {"built": 6, "remapped": 0, "face_count": 14}}
    assert report["ceiling"] == {"built": 4, "remapped": 0, "face_count": 14}
    faces = _faces()
    xy = [f for f in faces if f["plane"] == "XY"]
    # floor at z = 2, ceiling at 2 + 3 cells
    assert sorted((f["plane_offset_m"], tuple(f["tile_xy"])) for f in xy) == sorted(
        [(2.0, (3, 3))] * 4 + [(5.0, CEILING)] * 4
    )
    assert sorted(tuple(f["cell_xy"]) for f in xy if f["plane_offset_m"] == 2.0) == sorted(_cells(range(2, 4), range(1, 3)))
    # left wall at x = origin 2, y cells 1-2, z rows 2-4 (shifted by the floor offset)
    assert _summary(faces, "YZ") == sorted((cell, 2.0, (1, 1), 1) for cell in _cells(range(1, 3), range(2, 5)))


def test_build_room_without_walls_is_a_floor(tile_object):
    report = api.build_room(OBJECT, TILESET, (2, 2, 1), FLOOR, WALL, walls=())
    assert report["walls"] == {} and report["face_count"] == 4


def test_build_room_twice_remaps(tile_object):
    api.build_room(OBJECT, TILESET, (2, 2, 1), FLOOR, WALL)
    report = api.build_room(OBJECT, TILESET, (2, 2, 1), (3, 3), (2, 2))
    assert report["floor"] == {"built": 0, "remapped": 4, "face_count": 8}
    assert report["walls"]["back"]["remapped"] == 2 and report["walls"]["left"]["remapped"] == 2
    assert {tuple(f["tile_xy"]) for f in _faces() if f["plane"] == "XY"} == {(3, 3)}


@pytest.mark.parametrize("walls", [("front",), ("back", "right"), ("BACK",), (1,), "top"])
def test_build_room_rejects_other_walls(tile_object, walls):
    with pytest.raises(ValueError) as error:
        api.build_room(OBJECT, TILESET, (2, 2, 1), FLOOR, WALL, walls=walls)
    assert str(error.value) == (
        "walls may contain only 'back' and 'left': planes XZ (normal -Y) and YZ (normal +X) are the only wall "
        "planes; build the room so its open sides face -Y and +X"
    )
    assert api.describe_tile_object(OBJECT)["face_count"] == 0


def test_build_room_rejects_non_integer_floor_offset(tile_object):
    with pytest.raises(ValueError, match=r"floor_offset_m 0\.5 m is not a whole number of cells \(1\.0 m each\)"):
        api.build_room(OBJECT, TILESET, (2, 2, 1), FLOOR, WALL, floor_offset_m=0.5)
    # without walls nothing starts on a row, so any offset is fine
    assert api.build_room(OBJECT, TILESET, (2, 2, 1), FLOOR, WALL, walls=(), floor_offset_m=0.5)["face_count"] == 4


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"size_cells": (0, 2, 1)}, "size_cells must be >= 1 in every component"),
        ({"size_cells": (2, 2)}, "size_cells must have exactly 3 integers"),
        ({"origin_cell": (1,)}, "origin_cell must have exactly 2 integers"),
        ({"floor_tile": (4, 0)}, "floor_tile: tile_xy (4, 0) with tile_span (1, 1) is outside tileset"),
        ({"wall_tile": "nope"}, "wall_tile: unknown tile name 'nope' in tileset"),
        ({"ceiling_tile": (0, 9)}, "ceiling_tile: tile_xy"),
    ],
)
def test_build_room_validates_before_touching(tile_object, kwargs, message):
    arguments = {"size_cells": (2, 2, 1), "floor_tile": FLOOR, "wall_tile": WALL, **kwargs}
    with pytest.raises(ValueError, match=re.escape(message)):
        api.build_room(OBJECT, TILESET, **arguments)
    assert api.describe_tile_object(OBJECT)["face_count"] == 0


def test_build_room_honours_plane_rules_of_named_tiles(tmp_path):
    image = tmp_path / "named.png"
    shutil.copy(FIXTURE_IMAGE, image)
    (tmp_path / "named.spyrite.yaml").write_text(
        "spyrite_tileset: 1\ntiles:\n  floor: {xy: [0, 0], planes: [XY]}\n  brick: {xy: [1, 0], planes: [XZ]}\n"
    )
    api.create_tileset(TILESET, str(image), (16, 16))
    api.create_tile_object(OBJECT, TILESET, PPU)
    with pytest.raises(
        ValueError,
        match=re.escape("wall_tile: tile 'brick' is not allowed on plane YZ (allowed: XZ)"),
    ):
        api.build_room(OBJECT, TILESET, (2, 2, 1), "floor", "brick")
    assert api.describe_tile_object(OBJECT)["face_count"] == 0

    api.build_room(OBJECT, TILESET, (2, 2, 1), "floor", "brick", walls=("back",))
    faces = _faces()
    assert {f["tile"] for f in faces} == {"floor", "brick"}
    assert {tuple(f["tile_xy"]) for f in faces if f["plane"] == "XZ"} == {(1, 0)}


def test_build_room_left_wall_needs_square_tiles():
    api.create_tileset(TILESET, str(FIXTURE_IMAGE), (16, 8))
    api.create_tile_object(OBJECT, TILESET, PPU)
    with pytest.raises(ValueError, match="A 'left' wall needs square tiles"):
        api.build_room(OBJECT, TILESET, (2, 2, 1), FLOOR, WALL)
    assert api.describe_tile_object(OBJECT)["face_count"] == 0
    # the back wall only uses the x pitch and the y offset, so it works; cells are 1 m wide, 0.5 m deep/tall
    report = api.build_room(OBJECT, TILESET, (2, 2, 2), FLOOR, WALL, walls=("back",))
    assert report["face_count"] == 4 + 4
    back = [f for f in _faces() if f["plane"] == "XZ"]
    assert {f["plane_offset_m"] for f in back} == {1.0}  # (oy + d) * cell height = 2 * 0.5


# ---------------------------------------------------------------------------
# extrude_edge
# ---------------------------------------------------------------------------


def test_extrude_edge_north_raises_xz_wall(tile_object):
    api.fill_tiles(OBJECT, TILESET, (0, 0), (3, 2), FLOOR)
    report = api.extrude_edge(OBJECT, TILESET, "XY", 0.0, (0, 2), (3, 2), "N", 2, WALL)
    assert report == {
        "built": 8,
        "remapped": 0,
        "face_count": 12 + 8,
        "wall_plane": "XZ",
        "wall_offset_m": 3.0,
    }
    walls = [f for f in _faces() if f["plane"] == "XZ"]
    assert len(walls) == 8
    assert sorted((tuple(f["cell_xy"]), f["plane_offset_m"], tuple(f["tile_xy"])) for f in walls) == sorted(
        (cell, 3.0, WALL) for cell in _cells(range(4), range(2))
    )
    assert all(f["normal"] == pytest.approx([0.0, -1.0, 0.0]) for f in walls)


def test_extrude_edge_north_reversed_run_orientation_and_raised_floor(tile_object):
    report = api.extrude_edge(
        OBJECT, TILESET, "XY", 2.0, (3, 1), (1, 1), "N", 1, [2, 2], rotation_deg=90, flip_x=True
    )
    assert report["built"] == 3 and report["wall_offset_m"] == 2.0
    walls = _faces()
    assert sorted(tuple(f["cell_xy"]) for f in walls) == [(1, 2), (2, 2), (3, 2)]  # z row starts at 2 m / 1 m
    assert {(f["rotation_deg"], f["flip_x"], f["flip_y"], f["plane_offset_m"]) for f in walls} == {(90, True, False, 2.0)}


def test_extrude_edge_west_raises_yz_wall(tile_object):
    report = api.extrude_edge(OBJECT, TILESET, "XY", 0.0, (1, 0), (1, 2), "W", 2, WALL)
    assert report == {"built": 6, "remapped": 0, "face_count": 6, "wall_plane": "YZ", "wall_offset_m": 1.0}
    assert _summary(_faces(), "YZ") == sorted((cell, 1.0, WALL, 1) for cell in _cells(range(3), range(2)))


def test_extrude_edge_remaps_existing_wall(tile_object):
    api.extrude_edge(OBJECT, TILESET, "XY", 0.0, (0, 0), (1, 0), "N", 1, WALL)
    report = api.extrude_edge(OBJECT, TILESET, "XY", 0.0, (0, 0), (1, 0), "N", 1, (3, 3))
    assert (report["built"], report["remapped"], report["face_count"]) == (0, 2, 2)


@pytest.mark.parametrize("side, facing", [("S", "+Y"), ("E", "-X")])
def test_extrude_edge_refuses_unexpressible_sides(tile_object, side, facing):
    with pytest.raises(ValueError) as error:
        api.extrude_edge(OBJECT, TILESET, "XY", 0.0, (0, 0), (0, 0), side, 1, WALL)
    assert str(error.value) == (
        f"side {side} needs a {facing}-facing wall, which planes XY/XZ/YZ cannot express; "
        "build so the open sides face -Y and +X"
    )
    assert api.describe_tile_object(OBJECT)["face_count"] == 0


def test_extrude_edge_south_message_is_the_plan_text(tile_object):
    with pytest.raises(
        ValueError,
        match=re.escape(
            "side S needs a +Y-facing wall, which planes XY/XZ/YZ cannot express; "
            "build so the open sides face -Y and +X"
        ),
    ):
        api.extrude_edge(OBJECT, TILESET, "XY", 0.0, (0, 0), (2, 0), "S", 1, WALL)


@pytest.mark.parametrize("offset", [0.5, 0.3, -0.25])
def test_extrude_edge_needs_whole_cell_offset(tile_object, offset):
    with pytest.raises(ValueError, match=r"plane_offset_m .* is not a whole number of cells \(1\.0 m each\)"):
        api.extrude_edge(OBJECT, TILESET, "XY", offset, (0, 0), (2, 0), "N", 1, WALL)
    assert api.describe_tile_object(OBJECT)["face_count"] == 0


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"plane": "XZ"}, "plane must be 'XY'"),
        ({"plane": "ZZ"}, "plane must be one of"),
        ({"from_cell": (0, 0), "to_cell": (2, 1)}, "side N needs from_cell and to_cell in the same row"),
        ({"side": "W", "from_cell": (0, 0), "to_cell": (1, 2)}, "side W needs from_cell and to_cell in the same column"),
        ({"side": "up"}, "side must be one of"),
        ({"height_cells": 0}, "height_cells must be >= 1"),
        ({"height_cells": 1.5}, "height_cells must be an integer"),
        ({"rotation_deg": 45}, "rotation_deg must be one of"),
        ({"tile": (9, 9)}, "tile: tile_xy"),
        ({"tile": "nope"}, "tile: unknown tile name 'nope' in tileset"),
    ],
)
def test_extrude_edge_validates_before_touching(tile_object, kwargs, message):
    arguments = {
        "plane": "XY",
        "plane_offset_m": 0.0,
        "from_cell": (0, 0),
        "to_cell": (2, 0),
        "side": "N",
        "height_cells": 1,
        "tile": WALL,
        **kwargs,
    }
    with pytest.raises(ValueError, match=re.escape(message)):
        api.extrude_edge(OBJECT, TILESET, **arguments)
    assert api.describe_tile_object(OBJECT)["face_count"] == 0


def test_extrude_edge_west_needs_square_tiles():
    api.create_tileset(TILESET, str(FIXTURE_IMAGE), (16, 8))
    api.create_tile_object(OBJECT, TILESET, PPU)
    with pytest.raises(ValueError, match="A 'W' wall needs square tiles"):
        api.extrude_edge(OBJECT, TILESET, "XY", 0.0, (0, 0), (0, 1), "W", 1, WALL)


# ---------------------------------------------------------------------------
# move_faces
# ---------------------------------------------------------------------------


def _face_at(cell, layer="BASE", plane="XY"):
    return next(
        f for f in _faces() if tuple(f["cell_xy"]) == cell and f["layer"] == layer and f["plane"] == plane
    )


def _face_by_index(index):
    return next(f for f in _faces() if f["index"] == index)


def _orientation(face):
    return {key: face[key] for key in ("tile_xy", "tile_span", "rotation_deg", "flip_x", "flip_y", "layer", "plane")}


@pytest.mark.parametrize(
    "plane, offset, rotation, flip_x, flip_y",
    [
        ("XY", 0.0, 0, False, False),
        ("XY", 0.0, 90, True, False),
        ("XZ", 4.0, 270, False, True),
        ("YZ", 2.0, 180, True, True),
    ],
)
def test_move_faces_shifts_whole_pixels_and_keeps_uvs_consistent(tile_object, plane, offset, rotation, flip_x, flip_y):
    def place(cell):
        api.place_tiles(
            OBJECT,
            TILESET,
            [
                {
                    "cell_xy": cell,
                    "tile_xy": [3, 1],
                    "plane": plane,
                    "plane_offset_m": offset,
                    "rotation_deg": rotation,
                    "flip_x": flip_x,
                    "flip_y": flip_y,
                }
            ],
        )
        return _face_at(cell, plane=plane)

    original = place((5, 5))
    reference = place((9, 9))  # a freshly placed face of the same tile and orientation
    index = original["index"]
    original_uvs = _face_uvs(index)
    _assert_uvs_equal(original_uvs, _face_uvs(reference["index"]))
    _scramble_uvs(index)  # the move must rebuild UVs, not merely leave them alone

    delta = {"XY": (16, 0, 0), "XZ": (16, 0, 0), "YZ": (0, 16, 0)}[plane]
    assert api.move_faces(OBJECT, [index], delta) == {"moved": 1, "face_count": 2}

    moved = next(f for f in _faces() if f["index"] == index)
    shift = [m - o for m, o in zip(moved["center_m"], original["center_m"])]
    assert shift == pytest.approx([d / PPU for d in delta], abs=1e-6)
    assert shift == pytest.approx([1.0 if d else 0.0 for d in delta], abs=1e-6)  # exactly one metre
    assert _orientation(moved) == _orientation(original)
    assert moved["on_grid"] is True
    _assert_uvs_equal(_face_uvs(index), original_uvs)
    _assert_uvs_equal(_face_uvs(index), _face_uvs(reference["index"]))
    # the other face did not move
    assert next(f for f in _faces() if f["index"] == reference["index"])["center_m"] == reference["center_m"]


def test_move_faces_keeps_multi_tile_span_and_decal_layer(tile_object):
    api.place_tiles(
        OBJECT,
        TILESET,
        [
            {"cell_xy": [5, 5], "tile_xy": [0, 2], "tile_span": [2, 1]},
            {"cell_xy": [3, 3], "tile_xy": [0, 0]},
            {"cell_xy": [3, 3], "tile_xy": [2, 2], "layer": "DECAL", "rotation_deg": 90, "flip_y": True},
            {"cell_xy": [8, 3], "tile_xy": [0, 0]},
            {"cell_xy": [8, 3], "tile_xy": [2, 2], "layer": "DECAL", "rotation_deg": 90, "flip_y": True},
        ],
    )
    span = _face_at((5, 5))
    decal = _face_at((3, 3), "DECAL")
    reference_decal = _face_at((8, 3), "DECAL")
    assert span["tile_span"] == [2, 1] and decal["plane_offset_m"] == 0.002
    span_uvs, decal_uvs = _face_uvs(span["index"]), _face_uvs(decal["index"])
    _scramble_uvs(span["index"])
    _scramble_uvs(decal["index"])

    assert api.move_faces(OBJECT, [span["index"], decal["index"]], (0, 16, 0))["moved"] == 2

    new_span = next(f for f in _faces() if f["index"] == span["index"])
    new_decal = next(f for f in _faces() if f["index"] == decal["index"])
    for before, after in ((span, new_span), (decal, new_decal)):
        assert after["center_m"][1] - before["center_m"][1] == pytest.approx(1.0, abs=1e-6)
        assert _orientation(after) == _orientation(before)
    assert new_decal["plane_offset_m"] == 0.002 and new_decal["layer"] == "DECAL"
    _assert_uvs_equal(_face_uvs(span["index"]), span_uvs)
    _assert_uvs_equal(_face_uvs(decal["index"]), decal_uvs)
    _assert_uvs_equal(_face_uvs(decal["index"]), _face_uvs(reference_decal["index"]))


def test_move_faces_uses_the_objects_pixel_density():
    api.create_tileset(TILESET, str(FIXTURE_IMAGE), (16, 16))
    api.create_tile_object(OBJECT, TILESET, 32)  # one cell = 0.5 m
    api.place_tiles(OBJECT, TILESET, [{"cell_xy": [1, 1], "tile_xy": [2, 2]}])
    before = _face_at((1, 1))
    api.move_faces(OBJECT, [before["index"]], (16, -8, 24))
    after = next(f for f in _faces() if f["index"] == before["index"])
    assert [a - b for a, b in zip(after["center_m"], before["center_m"])] == pytest.approx([0.5, -0.25, 0.75], abs=1e-6)
    assert _orientation(after) == _orientation(before)


def test_move_faces_moves_vertices_shared_with_unmoved_faces(tile_object):
    api.fill_tiles(OBJECT, TILESET, (0, 0), (1, 0), (1, 1))
    left = _face_at((0, 0))
    neighbour = _face_at((1, 0))
    assert api.move_faces(OBJECT, [left["index"]], (0, 0, 16))["moved"] == 1
    # the shared edge moved up with the face: the neighbour is tilted, no longer a whole-cell rectangle
    assert next(f for f in _faces() if f["index"] == neighbour["index"])["on_grid"] is False
    assert next(f for f in _faces() if f["index"] == left["index"])["center_m"][2] == pytest.approx(1.0, abs=1e-6)


def test_move_faces_ignores_duplicate_indices(tile_object):
    api.place_tiles(OBJECT, TILESET, [{"cell_xy": [0, 0], "tile_xy": [1, 1]}])
    assert api.move_faces(OBJECT, [0, 0, 0], (0, 0, 16)) == {"moved": 1, "face_count": 1}
    assert _faces()[0]["center_m"][2] == pytest.approx(1.0, abs=1e-6)


@pytest.mark.parametrize(
    "indices, delta, message",
    [
        ([], (16, 0, 0), "face_indices is empty"),
        ("0", (16, 0, 0), "face_indices must be a list of integers"),
        ([0.5], (16, 0, 0), r"face_indices\[0\] must be an integer"),
        ([7], (16, 0, 0), r"face index 7 is out of range 0-0"),
        ([0], (16, 0), "delta_px must have exactly 3 integers"),
        ([0], (16.5, 0, 0), r"delta_px\[0\] must be an integer"),
    ],
)
def test_move_faces_validates_and_leaves_the_mesh_alone(tile_object, indices, delta, message):
    api.place_tiles(OBJECT, TILESET, [{"cell_xy": [0, 0], "tile_xy": [1, 1]}])
    before = api.describe_tile_object(OBJECT)
    with pytest.raises(ValueError, match=message):
        api.move_faces(OBJECT, indices, delta)
    assert api.describe_tile_object(OBJECT) == before


def test_move_faces_restores_scene_settings(tile_object):
    api.place_tiles(OBJECT, TILESET, [{"cell_xy": [0, 0], "tile_xy": [1, 1], "rotation_deg": 90, "flip_x": True}])
    data = bpy.context.scene.sprytile_data
    saved = {name: getattr(data, name) for name in ("uv_flip_x", "uv_flip_y", "mesh_rotate", "work_layer", "world_pixels")}
    grid = sprytile_core.get_grid(bpy.context, bpy.data.objects[OBJECT].sprytile_gridid)
    selection = tuple(grid.tile_selection)
    api.move_faces(OBJECT, [0], (16, 0, 0))
    assert {name: getattr(data, name) for name in saved} == saved
    assert tuple(grid.tile_selection) == selection
    assert bpy.data.objects[OBJECT].mode == "OBJECT"


def _vertex_uvs(face_index):
    """{vertex position relative to the face centre (local, 4 dp): (u, v)}: which corner got which UV."""
    mesh = bmesh.new()
    mesh.from_mesh(bpy.data.objects[OBJECT].data)
    try:
        mesh.faces.ensure_lookup_table()
        uv_layer = mesh.loops.layers.uv.verify()
        face = mesh.faces[face_index]
        center = face.calc_center_bounds()
        return {
            tuple(round(c, 4) for c in (loop.vert.co - center)): (loop[uv_layer].uv[0], loop[uv_layer].uv[1])
            for loop in face.loops
        }
    finally:
        mesh.free()


def _assert_vertex_uvs_equal(actual, expected):
    assert actual.keys() == expected.keys()
    for key, want in expected.items():
        assert actual[key] == pytest.approx(want, abs=1e-6), f"corner {key}"


@pytest.mark.parametrize(
    "cell, tile, span",
    [
        ((0, 0), [2, 2], [2, 1]),  # span ends on the last tileset column
        ((0, 0), [3, 1], [1, 2]),  # a one-column span on the last column
        ((2, 1), [0, 0], [2, 2]),  # an interior span
    ],
)
def test_move_faces_keeps_uvs_of_spans_on_the_last_tileset_column(tile_object, cell, tile, span):
    api.place_tiles(OBJECT, TILESET, [{"cell_xy": cell, "tile_xy": tile, "tile_span": span}])
    face = _face_at(cell)
    assert face["tile_span"] == span and face["tile_xy"] == tile
    before = _vertex_uvs(face["index"])
    assert all(0.0 <= u <= 1.0 and 0.0 <= v <= 1.0 for u, v in before.values())

    api.move_faces(OBJECT, [face["index"]], (0, 16, 0))

    after = _face_by_index(face["index"])
    assert after["tile_xy"] == tile and after["tile_span"] == span
    _assert_vertex_uvs_equal(_vertex_uvs(face["index"]), before)


@pytest.mark.parametrize("rotation, flip_x", [(0, False), (90, True)])
def test_move_faces_keeps_uvs_of_reversed_faces(tile_object, rotation, flip_x):
    api.place_tiles(
        OBJECT, TILESET, [{"cell_xy": [0, 0], "tile_xy": [1, 1], "rotation_deg": rotation, "flip_x": flip_x}]
    )
    obj = bpy.data.objects[OBJECT]
    mesh = bmesh.new()
    mesh.from_mesh(obj.data)
    bmesh.ops.reverse_faces(mesh, faces=list(mesh.faces))
    mesh.to_mesh(obj.data)
    mesh.free()
    obj.data.update()
    face = _face_at((0, 0))
    assert face["facing"] == -1
    before = _vertex_uvs(face["index"])

    api.move_faces(OBJECT, [face["index"]], (16, 0, 0))

    after = _face_by_index(face["index"])
    assert after["facing"] == -1 and _orientation(after) == _orientation(face)
    _assert_vertex_uvs_equal(_vertex_uvs(face["index"]), before)


def _widen_first_face_and_store_paint_mode(**paint):
    """Stretch the face to 2 m x 1 m and store the paint settings of a Paint-tool face with ``paint``."""
    obj = bpy.data.objects[OBJECT]
    data = bpy.context.scene.sprytile_data
    saved = {name: getattr(data, name) for name in ("paint_mode", *paint)}
    mesh = bmesh.new()
    mesh.from_mesh(obj.data)
    try:
        mesh.faces.ensure_lookup_table()
        for vert in mesh.faces[0].verts:
            if vert.co.x > 0.5:
                vert.co.x += 1.0
        data.paint_mode = "PAINT"
        for name, value in paint.items():
            setattr(data, name, value)
        stored = sprytile_core.get_paint_settings(data)
        mesh.faces[0][mesh.faces.layers.int.get("paint_settings")] = stored
        mesh.to_mesh(obj.data)
    finally:
        mesh.free()
        for name, value in saved.items():
            setattr(data, name, value)
    obj.data.update()
    return stored


def test_move_faces_rebuilds_paint_tool_faces_the_way_they_were_painted(tile_object):
    api.place_tiles(OBJECT, TILESET, [{"cell_xy": [0, 0], "tile_xy": [1, 1]}])
    _widen_first_face_and_store_paint_mode(
        paint_align="CENTER", paint_uv_snap=True, paint_edge_snap=False, paint_stretch_x=True, paint_stretch_y=True
    )
    api.move_faces(OBJECT, [0], (16, 0, 0))
    uvs = list(_vertex_uvs(0).values())
    # stretched: the 2 m wide face shows exactly tile (1, 1): columns 1/4..2/4 and rows 2/4..3/4 from the top
    assert min(u for u, _ in uvs) >= 0.25 - 1e-3 and max(u for u, _ in uvs) <= 0.5 + 1e-3
    assert max(u for u, _ in uvs) - min(u for u, _ in uvs) > 0.2
    assert min(v for _, v in uvs) >= 0.5 - 1e-3 and max(v for _, v in uvs) <= 0.75 + 1e-3


def test_move_faces_keeps_the_scene_paint_settings(tile_object):
    data = bpy.context.scene.sprytile_data
    names = ("paint_mode", "paint_align", "paint_uv_snap", "paint_edge_snap", "paint_stretch_x", "paint_stretch_y")
    api.place_tiles(OBJECT, TILESET, [{"cell_xy": [0, 0], "tile_xy": [1, 1]}])
    _widen_first_face_and_store_paint_mode(paint_align="TOP_LEFT", paint_stretch_x=True)
    data.paint_stretch_y = True
    data.paint_edge_snap = True
    saved = {name: getattr(data, name) for name in names}
    api.move_faces(OBJECT, [0], (16, 0, 0))
    assert {name: getattr(data, name) for name in names} == saved


def test_move_faces_refuses_faces_without_tile_data(tile_object):
    api.place_tiles(OBJECT, TILESET, [{"cell_xy": [0, 0], "tile_xy": [1, 1]}])
    obj = bpy.data.objects[OBJECT]
    mesh = bmesh.new()
    mesh.from_mesh(obj.data)
    verts = [mesh.verts.new(co) for co in ((5, 5, 0), (6, 5, 0), (5, 6, 0))]
    mesh.faces.new(verts)
    mesh.to_mesh(obj.data)
    mesh.free()
    obj.data.update()
    before = [list(f["center_m"]) for f in _faces()]
    assert len(before) == 2

    with pytest.raises(ValueError, match=re.escape("faces [1] have no tile data")):
        api.move_faces(OBJECT, [0, 1], (16, 0, 0))
    assert [list(f["center_m"]) for f in _faces()] == before  # nothing moved, not even the tiled face


def test_move_faces_moves_in_world_space_on_a_transformed_object(tile_object):
    import math

    obj = bpy.data.objects[OBJECT]
    obj.location = (3.0, -2.0, 1.0)
    obj.rotation_euler = (0.0, 0.0, math.radians(90))
    bpy.context.view_layer.update()
    api.place_tiles(
        OBJECT, TILESET, [{"cell_xy": [2, 1], "tile_xy": [3, 2], "rotation_deg": 90, "flip_x": True}]
    )
    face = _face_by_index(0)
    before_uvs = _vertex_uvs(0)

    def world_center():
        return obj.matrix_world @ obj.data.polygons[0].center

    start = world_center()
    api.move_faces(OBJECT, [0], (32, -16, 16))
    shift = world_center() - start
    assert list(shift) == pytest.approx([2.0, -1.0, 1.0], abs=1e-5)  # world pixels, not local
    assert _orientation(_face_by_index(0)) == _orientation(face)
    _assert_vertex_uvs_equal(_vertex_uvs(0), before_uvs)
