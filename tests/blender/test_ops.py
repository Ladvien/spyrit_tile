"""spyrite_tile_ops.ops against the add-on api, inside Blender.

Each op must produce the same geometry as calling `api` directly with the
equivalent arguments, and must refuse cleanly when the add-on is disabled.
The disabled test comes last and re-enables the add-on in `finally`.
"""

from pathlib import Path

import addon_utils
import bpy
import pytest

from spyrite_tile_ops import ops

ADDON_MODULE = "bl_ext.user_default.spyrite_tile"
FIXTURE_IMAGE = Path(__file__).resolve().parent.parent / "fixtures" / "tiles_16px.png"
TILE_SIZE_PX = (16, 16)
PIXELS_PER_UNIT_PX = 16
COLUMNS = ROWS = 4
ROUND_DIGITS = 5
TOLERANCE = 1e-4
# Sprytile insets every UV rectangle by about 0.0004 so neighbours never bleed.
UV_TOLERANCE = 1e-3
PREFIX = "ops_test_"
TILESET_NAME = PREFIX + "tiles"
OPS_OBJECT_NAME = PREFIX + "via_ops"
API_OBJECT_NAME = PREFIX + "via_api"

PLACEMENTS = [
    ops.TilePlacement(cell_xy=(2, 3), tile_xy=(1, 0)),
    ops.TilePlacement(
        cell_xy=(0, 0), tile_xy=(3, 3), plane="XZ", plane_offset_m=2.0, flip_x=True
    ),
    ops.TilePlacement(
        cell_xy=(1, 0), tile_xy=(2, 1), plane="YZ", plane_offset_m=1.0, rotation_deg=90.0
    ),
]


def _api():
    return ops._addon_api()


def _remove_test_data():
    for blender_object in [o for o in bpy.data.objects if o.name.startswith(PREFIX)]:
        bpy.data.objects.remove(blender_object, do_unlink=True)
    for mesh in [m for m in bpy.data.meshes if m.name.startswith(PREFIX)]:
        bpy.data.meshes.remove(mesh)
    for material in [m for m in bpy.data.materials if m.name.startswith(PREFIX)]:
        bpy.data.materials.remove(material)


@pytest.fixture
def tileset():
    _remove_test_data()
    report = ops.import_tileset(
        name=TILESET_NAME, image_path=FIXTURE_IMAGE, tile_size_px=TILE_SIZE_PX
    )
    yield report
    _remove_test_data()


def _rounded(values):
    return tuple(round(float(value), ROUND_DIGITS) for value in values)


def _geometry(object_name):
    """Per face: (normal, [(vertex position, uv) per loop]). Objects sit at identity."""
    mesh = bpy.data.objects[object_name].data
    uv_data = mesh.uv_layers.active.data
    return [
        (
            _rounded(polygon.normal),
            [
                (
                    _rounded(mesh.vertices[mesh.loops[index].vertex_index].co),
                    _rounded(uv_data[index].uv),
                )
                for index in polygon.loop_indices
            ],
        )
        for polygon in mesh.polygons
    ]


def _extent(face, which, axis):
    values = [loop[which][axis] for loop in face[1]]
    return min(values), max(values)


def _create_object_pair():
    ops_report = ops.create_tile_object(
        name=OPS_OBJECT_NAME,
        tileset_name=TILESET_NAME,
        pixels_per_unit_px=PIXELS_PER_UNIT_PX,
    )
    api_result = _api().create_tile_object(
        object_name=API_OBJECT_NAME,
        material_name=TILESET_NAME,
        pixels_per_unit=PIXELS_PER_UNIT_PX,
    )
    return ops_report, api_result


def test_import_tileset_reports_the_grid(tileset):
    assert tileset.material_name == TILESET_NAME
    assert tileset.image_size_px == (64, 64)
    assert tileset.tile_size_px == TILE_SIZE_PX
    assert (tileset.columns, tileset.rows) == (COLUMNS, ROWS)


def test_create_tile_object_matches_api(tileset):
    ops_report, api_result = _create_object_pair()
    assert ops_report == ops.TileObjectReport(
        object_name=OPS_OBJECT_NAME,
        material_name=TILESET_NAME,
        grid_id=api_result["grid_id"],
        pixels_per_unit=PIXELS_PER_UNIT_PX,
    )
    assert ops_report.grid_id == tileset.grid_id
    assert bpy.data.objects[OPS_OBJECT_NAME].type == "MESH"


def test_place_tiles_matches_api(tileset):
    _create_object_pair()
    report = ops.place_tiles(
        object_name=OPS_OBJECT_NAME, tileset_name=TILESET_NAME, placements=PLACEMENTS
    )
    api_result = _api().place_tiles(
        object_name=API_OBJECT_NAME,
        material_name=TILESET_NAME,
        placements=[
            {
                "cell_xy": list(p.cell_xy),
                "tile_xy": list(p.tile_xy),
                "tile_span": list(p.tile_span),
                "plane": p.plane,
                "plane_offset_m": p.plane_offset_m,
                "rotation_deg": p.rotation_deg,
                "flip_x": p.flip_x,
                "flip_y": p.flip_y,
                "layer": p.layer,
            }
            for p in PLACEMENTS
        ],
    )
    assert report == ops.TileEditReport(
        face_count=api_result["face_count"],
        built=api_result["built"],
        remapped=api_result["remapped"],
    )
    assert report.built == len(PLACEMENTS) and report.face_count == len(PLACEMENTS)

    via_ops = _geometry(OPS_OBJECT_NAME)
    assert via_ops == _geometry(API_OBJECT_NAME)

    # Ground truth from the plan, not from the api: the XY placement at cell
    # (2, 3), tile_xy (1, 0), spans x in [2, 3], y in [3, 4], z = 0, normal +Z,
    # and shows the top-row, second-column tile.
    floor = next(face for face in via_ops if face[0] == (0.0, 0.0, 1.0))
    assert _extent(floor, 0, 0) == pytest.approx((2.0, 3.0), abs=TOLERANCE)
    assert _extent(floor, 0, 1) == pytest.approx((3.0, 4.0), abs=TOLERANCE)
    assert _extent(floor, 0, 2) == pytest.approx((0.0, 0.0), abs=TOLERANCE)
    assert _extent(floor, 1, 0) == pytest.approx((0.25, 0.5), abs=UV_TOLERANCE)
    assert _extent(floor, 1, 1) == pytest.approx((0.75, 1.0), abs=UV_TOLERANCE)
    # The probe varies: three planes with three different normals.
    assert len({face[0] for face in via_ops}) == len(PLACEMENTS)


def test_fill_remove_paint_and_report_match_api(tileset):
    _create_object_pair()
    fill = ops.fill_tiles(
        object_name=OPS_OBJECT_NAME,
        tileset_name=TILESET_NAME,
        cell_min_xy=(0, 0),
        cell_max_xy=(3, 3),
        tile_xy=(0, 0),
    )
    api_fill = _api().fill_tiles(
        object_name=API_OBJECT_NAME,
        material_name=TILESET_NAME,
        cell_min_xy=(0, 0),
        cell_max_xy=(3, 3),
        tile_xy=(0, 0),
    )
    assert fill.face_count == api_fill["face_count"] == COLUMNS * ROWS
    assert fill.built == api_fill["built"]

    removal = ops.remove_tiles(object_name=OPS_OBJECT_NAME, cells=[(0, 0)])
    api_removal = _api().remove_tiles(
        object_name=API_OBJECT_NAME, plane="XY", plane_offset_m=0.0, cells=[[0, 0]]
    )
    assert removal.removed == api_removal["removed"] == 1
    assert removal.face_count == api_removal["face_count"] == COLUMNS * ROWS - 1

    painting = ops.paint_faces(
        object_name=OPS_OBJECT_NAME,
        tileset_name=TILESET_NAME,
        face_indices=[0, 1],
        tile_xy=(3, 3),
        rotation_deg=90.0,
        flip_x=True,
    )
    api_painting = _api().paint_faces(
        object_name=API_OBJECT_NAME,
        material_name=TILESET_NAME,
        face_indices=[0, 1],
        tile_xy=(3, 3),
        rotation_deg=90.0,
        flip_x=True,
        flip_y=False,
    )
    assert painting.painted == api_painting["painted"] == 2
    assert _geometry(OPS_OBJECT_NAME) == _geometry(API_OBJECT_NAME)

    reading = ops.tile_object_report(object_name=OPS_OBJECT_NAME)
    api_reading = _api().describe_tile_object(object_name=API_OBJECT_NAME)
    assert reading.object_name == OPS_OBJECT_NAME
    assert reading.face_count == api_reading["face_count"] == COLUMNS * ROWS - 1
    assert reading.truncated is api_reading["truncated"] is False
    assert len(reading.faces) == len(api_reading["faces"])
    for face, api_face in zip(reading.faces, api_reading["faces"]):
        assert face.index == api_face["index"]
        assert face.center_m == pytest.approx(tuple(api_face["center_m"]))
        assert face.normal == pytest.approx(tuple(api_face["normal"]))
        assert face.tile_xy == tuple(api_face["tile_xy"])
        assert face.material == api_face["material"] == TILESET_NAME
        assert face.tile_span == tuple(api_face["tile_span"])
        assert (face.rotation_deg, face.flip_x, face.flip_y) == (
            api_face["rotation_deg"], api_face["flip_x"], api_face["flip_y"])
        assert face.layer == api_face["layer"] == "BASE"
        assert face.plane == api_face["plane"] == "XY"
        assert face.facing == api_face["facing"] == 1
        assert face.plane_offset_m == api_face["plane_offset_m"] == 0.0
        assert face.cell_xy == tuple(api_face["cell_xy"])
        assert face.on_grid is api_face["on_grid"] is True
        assert face.tileset == api_face["tileset"] == TILESET_NAME
        assert face.tile is api_face["tile"] is None
    painted = {face.index: face.tile_xy for face in reading.faces}
    assert painted[0] == painted[1] == (3, 3)


def test_scene_report_matches_api(tileset):
    _create_object_pair()
    ops.fill_tiles(OPS_OBJECT_NAME, TILESET_NAME, (0, 0), (1, 1), (0, 0))
    reading = ops.scene_report()
    api_scene = _api().describe_scene()

    mine = next(t for t in reading.tilesets if t.material_name == TILESET_NAME)
    api_mine = next(t for t in api_scene["tilesets"] if t["material_name"] == TILESET_NAME)
    assert mine.image_size_px == tuple(api_mine["image_size_px"]) == (64, 64)
    assert mine.tile_size_px == tuple(api_mine["tile_size_px"]) == TILE_SIZE_PX
    assert (mine.columns, mine.rows) == (api_mine["columns"], api_mine["rows"]) == (COLUMNS, ROWS)
    assert mine.grid_id == api_mine["grid_id"] == tileset.grid_id
    assert mine.image_path == api_mine["image_path"]
    assert mine.tile_names == {}

    obj = next(o for o in reading.tile_objects if o.object_name == OPS_OBJECT_NAME)
    api_obj = next(o for o in api_scene["tile_objects"] if o["object_name"] == OPS_OBJECT_NAME)
    assert obj.face_count == api_obj["face_count"] == 4
    assert obj.pixels_per_unit == api_obj["pixels_per_unit"] == PIXELS_PER_UNIT_PX
    assert obj.material_name == TILESET_NAME and obj.overlay_of is None
    assert reading.removed_tilesets == tuple(api_scene["removed_tilesets"])
    assert reading.world_pixels == api_scene["settings"]["world_pixels"]
    assert reading.mesh_decal_offset == pytest.approx(api_scene["settings"]["mesh_decal_offset"])
    assert reading.auto_merge is api_scene["settings"]["auto_merge"]


def test_set_pixel_art_view_makes_workbench_show_unlit_texels():
    report = ops.set_pixel_art_view()
    scene = bpy.context.scene
    solid_viewports = [
        area.spaces.active.shading
        for window in bpy.context.window_manager.windows
        for area in window.screen.areas
        if area.type == "VIEW_3D" and area.spaces.active.shading.type == "SOLID"
    ]
    assert report == ops.PixelArtViewReport(
        color_type="TEXTURE",
        light="FLAT",
        render_aa="OFF",
        view_transform="Standard",
        viewports_textured=len(solid_viewports),
    )
    assert all(shading.color_type == "TEXTURE" for shading in solid_viewports)
    assert scene.display.shading.color_type == "TEXTURE"
    assert scene.display.shading.light == "FLAT"
    assert scene.display.render_aa == "OFF"
    assert scene.view_settings.view_transform == "Standard"


@pytest.mark.parametrize("rotation_deg", [45.0, 91.0, -90.0, 360.0])
def test_rotation_deg_outside_the_four_quarter_turns_is_refused(rotation_deg):
    bad_placement = ops.TilePlacement(
        cell_xy=(0, 0), tile_xy=(0, 0), rotation_deg=rotation_deg
    )
    with pytest.raises(ValueError, match=r"rotation_deg must be one of \(0, 90, 180, 270\)"):
        ops.place_tiles("any", "any", [bad_placement])
    with pytest.raises(ValueError, match="rotation_deg must be one of"):
        ops.fill_tiles("any", "any", (0, 0), (1, 1), (0, 0), rotation_deg=rotation_deg)
    with pytest.raises(ValueError, match="rotation_deg must be one of"):
        ops.paint_faces("any", "any", [0], (0, 0), rotation_deg=rotation_deg)


def test_tile_object_and_scene_report_are_the_only_reads_only_ops():
    from blended.ops._contract import is_reads_only

    assert [name for name in ops.__all__ if is_reads_only(getattr(ops, name))] == [
        "tile_object_report",
        "scene_report",
    ]


def test_ops_refuse_when_the_addon_is_disabled():
    assert addon_utils.check(ADDON_MODULE) == (True, True)
    bpy.ops.preferences.addon_disable(module=ADDON_MODULE)
    try:
        assert addon_utils.check(ADDON_MODULE)[1] is False
        with pytest.raises(RuntimeError, match="Spyrite Tile add-on is not enabled") as error:
            ops.place_tiles(
                "any", "any", [ops.TilePlacement(cell_xy=(0, 0), tile_xy=(0, 0))]
            )
        assert "`make install-addon`" in str(error.value)
        with pytest.raises(RuntimeError, match="not enabled"):
            ops.tile_object_report("any")
    finally:
        bpy.ops.preferences.addon_enable(module=ADDON_MODULE)
    assert addon_utils.check(ADDON_MODULE) == (True, True)
