"""Issues #19 (cannot paint on linked duplicated objects) and #108 (can only paint on faces facing the wrong way).

`TileBuilder.raycast_object` compared the object-space face normal with the WORLD ray direction, so on any
object that is rotated, mirrored or negatively scaled (a moved/rotated linked duplicate, Alt+D) the back-face
test flipped: the visible side passed through and the hidden side was hit. It also returned an object-space
normal (or `matrix @ normal`, translation included) where callers expect a world normal.

Ground truth is the mesh itself: the quad's front is the object-space +Z side, so the ray origin is built from
the object-space point (0.5, 0.5, +-0.5) pushed through `matrix_world`, with no use of any normal.
"""

import importlib
import math
from pathlib import Path
from types import SimpleNamespace

import bmesh
import bpy
import pytest
from mathutils import Euler, Matrix, Quaternion, Vector

api = importlib.import_module("bl_ext.user_default.spyrite_tile.api")
sprytile_utils = importlib.import_module("bl_ext.user_default.spyrite_tile.sprytile_utils")
sprytile_builder = importlib.import_module("bl_ext.user_default.spyrite_tile.sprytile_builder")
tool_paint = importlib.import_module("bl_ext.user_default.spyrite_tile.sprytile_tools.tool_paint")

FIXTURE_IMAGE = Path(__file__).resolve().parent.parent / "fixtures" / "tiles_16px.png"
PREFIX = "i19_108_"
TILESET = PREFIX + "tiles"
OBJECT = PREFIX + "obj"
PPU = 16
UV_TOL = 1e-3
BUILT_TILE = (0, 0)
PAINT_TILE = (2, 1)
BUILD_TILE = (3, 2)


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


@pytest.fixture(autouse=True)
def clean_scene():
    _remove_test_data()
    yield
    _remove_test_data()
    bpy.context.scene.sprytile_data.allow_backface = False


@pytest.fixture
def quad():
    """An object holding one 1 m quad on the XY plane (object-space normal +Z) painted with BUILT_TILE."""
    api.create_tileset(TILESET, str(FIXTURE_IMAGE), (16, 16))
    api.create_tile_object(OBJECT, TILESET, PPU)
    api.place_tiles(OBJECT, TILESET, [{"cell_xy": (0, 0), "tile_xy": BUILT_TILE}])
    obj = bpy.data.objects[OBJECT]
    assert len(obj.data.polygons) == 1
    assert tuple(obj.data.polygons[0].normal) == pytest.approx((0, 0, 1), abs=1e-6)
    return obj


def _transform(location=(0.0, 0.0, 0.0), rotation=(0.0, 0.0, 0.0), scale=(1.0, 1.0, 1.0)):
    return (
        Matrix.Translation(location)
        @ Euler(rotation).to_matrix().to_4x4()
        @ Matrix.Diagonal(Vector(scale).to_4d())
    )


CASES = {
    "identity": _transform(),
    "translated": _transform(location=(3, -2, 5)),
    "rot_z_pi": _transform(location=(3, -2, 5), rotation=(0, 0, math.pi)),
    "rot_x_pi": _transform(location=(3, -2, 5), rotation=(math.pi, 0, 0)),
    "scale_x_neg": _transform(location=(3, -2, 5), scale=(-1, 1, 1)),
    "scale_z_neg": _transform(location=(3, -2, 5), scale=(1, 1, -1)),
    "scale_xz_neg_rotated": _transform(location=(-4, 1, 2), rotation=(0.4, -0.3, 1.1), scale=(-1, 1, -1)),
    "non_uniform_rotated": _transform(location=(-4, 1, 2), rotation=(0.4, -0.3, 1.1), scale=(2, 0.5, 3)),
    "non_uniform_neg_rotated": _transform(location=(1, 2, 3), rotation=(1.2, 0.2, -0.7), scale=(2, -0.5, 3)),
}


def _make_target(quad, case, linked_duplicate):
    """The object to raycast: the quad itself, or a moved/rotated linked duplicate sharing its mesh."""
    if linked_duplicate:
        target = quad.copy()
        target.name = PREFIX + "dup"
        bpy.context.scene.collection.objects.link(target)
        assert target.data is quad.data
    else:
        target = quad
    target.matrix_world = CASES[case]
    bpy.context.view_layer.update()
    return target


def _enter_edit(obj):
    for other in bpy.context.view_layer.objects:
        other.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.context.scene.sprytile_data.allow_backface = False


def _leave_edit(obj):
    bpy.ops.object.mode_set(mode="OBJECT")


def _rays(obj):
    """(origin, direction) of a ray from the front (visible) and one from the back (hidden) side of the quad."""
    matrix = obj.matrix_world
    centre = matrix @ Vector((0.5, 0.5, 0.0))
    visible_origin = matrix @ Vector((0.5, 0.5, 0.5))
    hidden_origin = matrix @ Vector((0.5, 0.5, -0.5))
    return (visible_origin, centre - visible_origin), (hidden_origin, centre - hidden_origin)


def _assert_world_normal(obj, normal, direction):
    matrix3 = obj.matrix_world.to_3x3()
    assert normal.length == pytest.approx(1.0, abs=1e-5), f"normal {tuple(normal)} is not a unit vector"
    # perpendicular to the surface: both in-plane edges, transformed to world space
    for axis in (Vector((1, 0, 0)), Vector((0, 1, 0))):
        assert normal.dot((matrix3 @ axis).normalized()) == pytest.approx(0.0, abs=1e-5)
    # a front hit faces the viewer
    assert normal.dot(direction) < 0


ALL_CASES = [(case, dup) for case in CASES for dup in (False, True)]
IDS = [f"{case}{'-linked_dup' if dup else ''}" for case, dup in ALL_CASES]


@pytest.mark.parametrize("case,linked_duplicate", ALL_CASES, ids=IDS)
def test_raycast_hits_visible_side_and_skips_hidden_side(quad, case, linked_duplicate):
    target = _make_target(quad, case, linked_duplicate)
    _enter_edit(target)
    try:
        (v_origin, v_dir), (h_origin, h_dir) = _rays(target)
        location, normal, face_index, distance = sprytile_builder.TileBuilder.raycast_object(target, v_origin, v_dir)
        assert face_index == 0, f"visible side was not hit on {case}"
        centre = target.matrix_world @ Vector((0.5, 0.5, 0.0))
        assert (location - centre).length == pytest.approx(0.0, abs=1e-4)
        assert distance == pytest.approx((centre - v_origin).length, abs=1e-4)
        _assert_world_normal(target, normal, v_dir)

        result = sprytile_builder.TileBuilder.raycast_object(target, h_origin, h_dir)
        assert result == (None, None, None, None), f"hidden side was hit on {case}: {result}"

        # allow_backface turns the hidden side into a hit; its normal faces away from the ray
        bpy.context.scene.sprytile_data.allow_backface = True
        location, normal, face_index, distance = sprytile_builder.TileBuilder.raycast_object(target, h_origin, h_dir)
        assert face_index == 0
        assert normal.dot(h_dir) > 0
    finally:
        _leave_edit(target)


def _paint_tool(obj):
    """ToolPaint with a stub modal and context (no window, no viewport) plus the list of cursor hits."""
    hits = []
    bm = bmesh.from_edit_mesh(obj.data)
    tool = tool_paint.ToolPaint.__new__(tool_paint.ToolPaint)
    tool.modal = SimpleNamespace(builder=SimpleNamespace(bmesh=bm), add_virtual_cursor=hits.append)
    context = SimpleNamespace(
        object=obj, scene=bpy.context.scene, region_data=SimpleNamespace(view_rotation=Quaternion())
    )
    return tool, context, hits


def _select_paint_tile(obj, tile):
    tileset = api._find_tileset(TILESET)
    origin_x, origin_y = tileset.sprytile_origin(tile, (1, 1))
    tileset.grid.tile_selection = (origin_x, origin_y, 1, 1)
    obj.sprytile_gridid = tileset.grid.id
    data = bpy.context.scene.sprytile_data
    data.world_pixels = PPU
    data.uv_flip_x = False
    data.uv_flip_y = False
    data.mesh_rotate = 0.0
    data.work_layer = "BASE"
    data.work_layer_mode = "MESH_DECAL"


def _face_tile_and_uv_centre(obj):
    """grid_tile_id and the mean UV of the only face (object mode)."""
    mesh = obj.data
    uv_layer = mesh.uv_layers.active.data
    uvs = [uv_layer[i].uv for i in mesh.polygons[0].loop_indices]
    centre = (sum(uv[0] for uv in uvs) / len(uvs), sum(uv[1] for uv in uvs) / len(uvs))
    return mesh.attributes["grid_tile_id"].data[0].value, centre


def _tile_id(tile):
    """The grid_tile_id Sprytile stores: row counted from the bottom of the sheet, 4 tiles per row."""
    origin_x, origin_y = api._find_tileset(TILESET).sprytile_origin(tile, (1, 1))
    return origin_y * 4 + origin_x


def _tile_centre(tile):
    """UV centre of the tile at (column from left, row from top) of the 4x4 fixture sheet."""
    column, row = tile
    return ((column + 0.5) / 4, 1 - (row + 0.5) / 4)


@pytest.mark.parametrize("case,linked_duplicate", ALL_CASES, ids=IDS)
def test_paint_remaps_the_visible_face_only(quad, case, linked_duplicate):
    target = _make_target(quad, case, linked_duplicate)
    built_id, built_centre = _face_tile_and_uv_centre(target)
    assert built_id == _tile_id(BUILT_TILE)
    assert built_centre == pytest.approx(_tile_centre(BUILT_TILE), abs=UV_TOL)

    (v_origin, v_dir), (h_origin, h_dir) = _rays(target)
    scene = bpy.context.scene

    # Hidden side: nothing is painted
    _enter_edit(target)
    try:
        _select_paint_tile(target, PAINT_TILE)
        tool, context, hits = _paint_tool(target)
        tool.execute(context, scene, h_origin, h_dir)
        assert hits == []
    finally:
        _leave_edit(target)
    assert _face_tile_and_uv_centre(target)[0] == built_id, f"hidden side was painted on {case}"

    # Visible side: painted
    _enter_edit(target)
    try:
        _select_paint_tile(target, PAINT_TILE)
        tool, context, hits = _paint_tool(target)
        tool.execute(context, scene, v_origin, v_dir)
        assert len(hits) == 1, f"visible side was not painted on {case}"
        bmesh.update_edit_mesh(target.data)
    finally:
        _leave_edit(target)
    tile_id, centre = _face_tile_and_uv_centre(target)
    assert tile_id == _tile_id(PAINT_TILE)
    assert centre == pytest.approx(_tile_centre(PAINT_TILE), abs=UV_TOL)


@pytest.mark.parametrize("case,linked_duplicate", ALL_CASES, ids=IDS)
def test_build_onto_the_same_cell_remaps(quad, case, linked_duplicate):
    target = _make_target(quad, case, linked_duplicate)
    matrix = target.matrix_world
    matrix3 = matrix.to_3x3()
    tileset = api._find_tileset(TILESET)
    origin_x, origin_y = tileset.sprytile_origin(BUILD_TILE, (1, 1))

    # The cell of the existing face in world space; the plane normal is its front side (from the mesh, not from
    # the code under test: object +Z through the normal matrix)
    grid_origin = matrix @ Vector((0.0, 0.0, 0.0))
    grid_right = matrix3 @ Vector((1.0, 0.0, 0.0))
    grid_up = matrix3 @ Vector((0.0, 1.0, 0.0))
    front = (matrix @ Vector((0.0, 0.0, 1.0))) - grid_origin
    plane_normal = grid_right.cross(grid_up).normalized()
    if plane_normal.dot(front) < 0:
        plane_normal = -plane_normal
    uv_right = grid_right.normalized()
    uv_up = grid_up.normalized()

    _enter_edit(target)
    try:
        _select_paint_tile(target, BUILD_TILE)
        context = bpy.context
        builder = sprytile_builder.TileBuilder(context, target)
        builder.update_bmesh_tree(context, True)
        faces_before = len(builder.bmesh.faces)
        face_index = builder.construct_face(
            context,
            (0, 0),
            [1, 1],
            (origin_x, origin_y),
            (origin_x, origin_y),
            grid_up,
            grid_right,
            uv_up,
            uv_right,
            plane_normal,
            work_layer_mask=0,
            grid_origin=grid_origin,
        )
        assert face_index == 0, f"build onto an existing cell returned {face_index} on {case}"
        assert len(builder.bmesh.faces) == faces_before, "a face was built instead of remapped"
        bmesh.update_edit_mesh(target.data)
    finally:
        _leave_edit(target)
    tile_id, centre = _face_tile_and_uv_centre(target)
    assert tile_id == _tile_id(BUILD_TILE)
    assert centre == pytest.approx(_tile_centre(BUILD_TILE), abs=UV_TOL)


def test_pass_through_keeps_the_ray_length(quad):
    """A hidden face is skipped, but the ray must not outgrow its ray_dist while skipping."""
    api.place_tiles(OBJECT, TILESET, [{"cell_xy": (0, 0), "tile_xy": BUILT_TILE, "plane_offset_m": 1.0}])
    assert len(quad.data.polygons) == 2
    _enter_edit(quad)
    try:
        bm = bmesh.from_edit_mesh(quad.data)
        upper = next(f for f in bm.faces if f.calc_center_median().z > 0.5)
        upper.hide_set(True)
        origin = Vector((0.5, 0.5, 2.0))
        down = Vector((0.0, 0.0, -1.0))
        # the lower face is 2 m away: reachable with 2.5, not with 1.2 (it used to be found through the skip)
        reached = sprytile_builder.TileBuilder.raycast_object(quad, origin, down, ray_dist=2.5)
        assert reached[2] is not None and reached[3] == pytest.approx(2.0, abs=1e-4)
        assert sprytile_builder.TileBuilder.raycast_object(quad, origin, down, ray_dist=1.2) == (None,) * 4
    finally:
        _leave_edit(quad)
