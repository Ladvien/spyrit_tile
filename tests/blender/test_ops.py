"""spyrite_tile_ops.ops against the add-on api, inside Blender.

Each op must produce the same geometry as calling `api` directly with the
equivalent arguments, and must refuse cleanly when the add-on is disabled.
The disabled test comes last and re-enables the add-on in `finally`.
"""

from pathlib import Path

import addon_utils
import bmesh
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


def test_import_tileset_reuse_matches_api(tileset):
    report = ops.import_tileset(
        name=TILESET_NAME + "_again", image_path=FIXTURE_IMAGE, tile_size_px=TILE_SIZE_PX
    )
    api_result = _api().create_tileset(
        material_name=TILESET_NAME + "_api", image_path=str(FIXTURE_IMAGE), tile_size_px=list(TILE_SIZE_PX)
    )
    assert tileset.reused_material is None
    assert report.reused_material == TILESET_NAME == api_result["reused_material"]
    assert report.material_name == api_result["material_name"] == TILESET_NAME
    assert bpy.data.materials.get(TILESET_NAME + "_again") is None


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


def test_fill_pattern_matches_api(tileset):
    _create_object_pair()
    pattern = {"kind": "random", "tiles": [{"tile": [0, 0]}, {"tile": [1, 1]}, {"tile": [2, 2]}], "seed": 5}
    spec = ops.PatternSpec(
        kind="random",
        tiles=[ops.PatternTile(tile=(0, 0)), ops.PatternTile(tile=(1, 1)), ops.PatternTile(tile=(2, 2))],
        seed=5,
    )
    report = ops.fill_pattern(
        object_name=OPS_OBJECT_NAME,
        tileset_name=TILESET_NAME,
        pattern=spec,
        cell_min_xy=(0, 0),
        cell_max_xy=(2, 2),
    )
    api_result = _api().fill_pattern(
        object_name=API_OBJECT_NAME,
        material_name=TILESET_NAME,
        pattern=pattern,
        cell_min_xy=[0, 0],
        cell_max_xy=[2, 2],
    )
    assert report.face_count == api_result["face_count"] == 9
    assert report.built == api_result["built"] and report.cells == api_result["cells"] == 9
    assert list(report.assignments) == api_result["assignments"]
    assert _geometry(OPS_OBJECT_NAME) == _geometry(API_OBJECT_NAME)


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


def test_create_overlay_object_matches_api(tileset):
    _create_object_pair()
    report = ops.create_overlay_object(
        name=OPS_OBJECT_NAME + "_ov", base_object_name=OPS_OBJECT_NAME, tileset_name=TILESET_NAME, lift_m=0.004
    )
    api_result = _api().create_overlay_object(
        name=API_OBJECT_NAME + "_ov", base_object_name=API_OBJECT_NAME, material_name=TILESET_NAME, lift_m=0.004
    )
    assert report == ops.TileObjectReport(
        object_name=OPS_OBJECT_NAME + "_ov",
        material_name=api_result["material_name"],
        grid_id=api_result["grid_id"],
        pixels_per_unit=api_result["pixels_per_unit"],
        overlay_of=OPS_OBJECT_NAME,
        lift_m=0.004,
    )
    assert api_result["overlay_of"] == API_OBJECT_NAME and api_result["lift_m"] == 0.004
    ops.fill_tiles(
        object_name=report.object_name, tileset_name=TILESET_NAME, cell_min_xy=(0, 0), cell_max_xy=(1, 1), tile_xy=(0, 0)
    )
    _api().fill_tiles(
        object_name=API_OBJECT_NAME + "_ov",
        material_name=TILESET_NAME,
        cell_min_xy=(0, 0),
        cell_max_xy=(1, 1),
        tile_xy=(0, 0),
    )
    via_ops = _geometry(report.object_name)
    assert via_ops == _geometry(API_OBJECT_NAME + "_ov")
    assert {face[1][0][0][2] for face in via_ops} == {0.004}
    assert bpy.data.objects[report.object_name].parent.name == OPS_OBJECT_NAME


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
        assert face.tile == api_face["tile"]
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
    assert mine.tile_names == api_mine["tile_names"]
    assert mine.tile_names["grass"] == {"xy": [0, 0], "planes": None, "tags": ["floor"]}

    obj = next(o for o in reading.tile_objects if o.object_name == OPS_OBJECT_NAME)
    api_obj = next(o for o in api_scene["tile_objects"] if o["object_name"] == OPS_OBJECT_NAME)
    assert obj.face_count == api_obj["face_count"] == 4
    assert obj.pixels_per_unit == api_obj["pixels_per_unit"] == PIXELS_PER_UNIT_PX
    assert obj.material_name == TILESET_NAME and obj.overlay_of is None
    assert reading.removed_tilesets == tuple(api_scene["removed_tilesets"])
    assert reading.world_pixels == api_scene["settings"]["world_pixels"]
    assert reading.mesh_decal_offset == pytest.approx(api_scene["settings"]["mesh_decal_offset"])
    assert reading.auto_merge is api_scene["settings"]["auto_merge"]


def test_select_tile_faces_matches_api(tileset):
    _create_object_pair()
    ops.fill_tiles(OPS_OBJECT_NAME, TILESET_NAME, (0, 0), (2, 2), (0, 0))
    ops.fill_tiles(OPS_OBJECT_NAME, TILESET_NAME, (1, 1), (1, 1), (2, 2))
    selector = ops.TileFaceSelector(tile=(2, 2), plane="XY", cell_min_xy=(0, 0), cell_max_xy=(2, 2))
    selection = ops.select_tile_faces(OPS_OBJECT_NAME, selector)
    direct = _api().select_faces(
        OPS_OBJECT_NAME, {"tile": [2, 2], "plane": "XY", "cell_min_xy": [0, 0], "cell_max_xy": [2, 2]}
    )
    assert selection.face_indices == tuple(direct["face_indices"]) and selection.count == direct["count"] == 1
    region = ops.select_tile_faces(OPS_OBJECT_NAME, ops.TileFaceSelector(plane="XY", connected_to_cell=(0, 0)))
    assert region.count == 8
    assert ops.select_tile_faces(OPS_OBJECT_NAME, ops.TileFaceSelector()).count == 9


def test_named_tiles_through_ops_match_api(tmp_path):
    import shutil

    image = tmp_path / "named.png"
    shutil.copy(FIXTURE_IMAGE, image)
    (tmp_path / "named.spyrite.yaml").write_text(
        "spyrite_tileset: 1\ntiles:\n  grass: {xy: [3, 3]}\n  wall: {xy: [1, 0], planes: [XZ]}\n"
    )
    _remove_test_data()
    try:
        report = ops.import_tileset(name=TILESET_NAME, image_path=image, tile_size_px=TILE_SIZE_PX)
        assert report.tile_names == _api().create_tileset(
            TILESET_NAME, str(image), TILE_SIZE_PX
        )["tile_names"]
        assert report.tile_names["wall"] == {"xy": [1, 0], "planes": ["XZ"], "tags": []}
        _create_object_pair()
        scene_tiles = next(t for t in ops.scene_report().tilesets if t.material_name == TILESET_NAME)
        assert scene_tiles.tile_names == report.tile_names
        placements = [
            ops.TilePlacement(cell_xy=(0, 0), tile="grass"),
            ops.TilePlacement(cell_xy=(1, 0), tile="wall", plane="XZ"),
            ops.TilePlacement(cell_xy=(2, 0), tile=(1, 2)),
        ]
        via_ops = ops.place_tiles(OPS_OBJECT_NAME, TILESET_NAME, placements)
        via_api = _api().place_tiles(
            API_OBJECT_NAME,
            TILESET_NAME,
            [{"cell_xy": [0, 0], "tile": "grass"}, {"cell_xy": [1, 0], "tile": "wall", "plane": "XZ"},
             {"cell_xy": [2, 0], "tile": [1, 2]}],
        )
        assert via_ops.face_count == via_api["face_count"] == 3
        assert _geometry(OPS_OBJECT_NAME) == _geometry(API_OBJECT_NAME)
        ops.fill_tiles(OPS_OBJECT_NAME, TILESET_NAME, (0, 0), (0, 0), "grass")
        ops.paint_faces(OPS_OBJECT_NAME, TILESET_NAME, [0], "grass")
        reading = ops.tile_object_report(OPS_OBJECT_NAME)
        by_index = {face.index: face for face in reading.faces}
        assert by_index[0].tile == "grass" and by_index[0].tile_xy == (3, 3)
        assert [f.tile for f in reading.faces].count(None) == 1
    finally:
        _remove_test_data()


def test_build_room_matches_api(tileset):
    _create_object_pair()
    report = ops.build_room(
        object_name=OPS_OBJECT_NAME,
        tileset_name=TILESET_NAME,
        size_cells=(4, 3, 2),
        floor_tile=(0, 0),
        wall_tile=(1, 0),
        ceiling_tile=(2, 0),
    )
    api_result = _api().build_room(
        object_name=API_OBJECT_NAME,
        material_name=TILESET_NAME,
        size_cells=[4, 3, 2],
        floor_tile=[0, 0],
        wall_tile=[1, 0],
        ceiling_tile=[2, 0],
    )
    assert report == ops.RoomReport(
        floor=ops.TileEditReport(face_count=38, built=12),
        walls={
            "back": ops.TileEditReport(face_count=38, built=8),
            "left": ops.TileEditReport(face_count=38, built=6),
        },
        ceiling=ops.TileEditReport(face_count=38, built=12),
        face_count=38,
    )
    assert report.face_count == api_result["face_count"]
    assert _geometry(OPS_OBJECT_NAME) == _geometry(API_OBJECT_NAME)
    with pytest.raises(ValueError, match="walls may contain only 'back' and 'left'"):
        ops.build_room(OPS_OBJECT_NAME, TILESET_NAME, (1, 1, 1), (0, 0), (1, 0), walls=("front",))


def test_extrude_edge_matches_api(tileset):
    _create_object_pair()
    report = ops.extrude_edge(
        object_name=OPS_OBJECT_NAME,
        tileset_name=TILESET_NAME,
        from_cell=(0, 2),
        to_cell=(3, 2),
        side="N",
        count=2,
        tile=(1, 0),
    )
    api_result = _api().extrude_edge(
        object_name=API_OBJECT_NAME,
        material_name=TILESET_NAME,
        plane="XY",
        plane_offset_m=0.0,
        from_cell=[0, 2],
        to_cell=[3, 2],
        side="N",
        height_cells=2,
        tile=[1, 0],
    )
    assert report == ops.TileEditReport(face_count=api_result["face_count"], built=api_result["built"])
    assert report.built == 8
    assert _geometry(OPS_OBJECT_NAME) == _geometry(API_OBJECT_NAME)
    with pytest.raises(ValueError, match="side S needs a"):
        ops.extrude_edge(OPS_OBJECT_NAME, TILESET_NAME, (0, 0), (1, 0), "S", 1, (1, 0))


def test_move_faces_matches_api(tileset):
    _create_object_pair()
    for name in (OPS_OBJECT_NAME, API_OBJECT_NAME):
        _api().fill_tiles(name, TILESET_NAME, [0, 0], [1, 1], [2, 1], rotation_deg=90.0, flip_x=True)
    report = ops.move_faces(OPS_OBJECT_NAME, [0, 3], (16, 0, -8))
    api_result = _api().move_faces(API_OBJECT_NAME, [0, 3], [16, 0, -8])
    assert report == ops.TileEditReport(face_count=api_result["face_count"], moved=api_result["moved"])
    assert (report.moved, report.face_count) == (2, 4)
    assert _geometry(OPS_OBJECT_NAME) == _geometry(API_OBJECT_NAME)

def test_checkpoint_ops_match_api(tileset):
    _create_object_pair()
    ops.fill_tiles(OPS_OBJECT_NAME, TILESET_NAME, (0, 0), (1, 1), (0, 0))
    made = ops.checkpoint([OPS_OBJECT_NAME], label="ops")
    api_made = _api().checkpoint([API_OBJECT_NAME], label="api")
    assert made.objects == (OPS_OBJECT_NAME,) and api_made["objects"] == [API_OBJECT_NAME]
    listing = ops.list_checkpoints()
    api_listing = _api().list_checkpoints()
    assert [(c.checkpoint_id, c.label, c.objects) for c in listing.checkpoints] == [
        (c["checkpoint_id"], c["label"], tuple(c["objects"])) for c in api_listing
    ]
    ops.remove_tiles(OPS_OBJECT_NAME, [(0, 0), (1, 1)])
    assert ops.tile_object_report(OPS_OBJECT_NAME).face_count == 2
    restored = ops.rollback(str(made.checkpoint_id))
    api_restored = _api().rollback(api_made["checkpoint_id"])
    assert restored.restored == (OPS_OBJECT_NAME,) and api_restored["restored"] == [API_OBJECT_NAME]
    assert ops.tile_object_report(OPS_OBJECT_NAME).face_count == 4
    assert ops.discard_checkpoint(str(made.checkpoint_id)).discarded == (OPS_OBJECT_NAME,)
    _api().discard_checkpoint(api_made["checkpoint_id"])
    assert ops.list_checkpoints().checkpoints == ()


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


def test_build_spec_and_export_spec_match_api(tmp_path):
    import shutil

    for suffix in (".png", ".spyrite.yaml"):
        shutil.copy(FIXTURE_IMAGE.with_name("tiles_16px" + suffix), tmp_path / ("tiles" + suffix))
    text = (
        "spyrite_spec: 1\npixels_per_unit: 16\ntilesets:\n  {t}:\n    image: ./tiles.png\n    tile_size_px: [16, 16]\n"
        "objects:\n  {o}:\n    tileset: {t}\n    fills:\n      - {{cells: [[0, 0], [2, 1]], tile: grass}}\n"
        "    tiles:\n      - {{plane: XZ, cell: [0, 0], tile: wall_top, rotation_deg: 90}}\n"
    )
    ops_spec = tmp_path / "ops.spyrite.yaml"
    ops_spec.write_text(text.format(t=TILESET_NAME, o=OPS_OBJECT_NAME), encoding="utf-8")
    api_spec = tmp_path / "api.spyrite.yaml"
    api_spec.write_text(text.format(t=TILESET_NAME + "_api", o=API_OBJECT_NAME), encoding="utf-8")
    _remove_test_data()
    try:
        report = ops.build_spec(spec_path=ops_spec)
        api_report = _api().build_spec(spec_path=str(api_spec))
        assert report.spec_path == str(ops_spec)
        assert report.tilesets[0].material_name == TILESET_NAME
        assert report.tilesets[0].tile_names == api_report["tilesets"][0]["tile_names"]
        assert report.tilesets[0].tile_names["wall_top"]["planes"] == ["XZ", "YZ"]
        assert report.objects == (ops.SpecObjectReport(OPS_OBJECT_NAME, built=7, remapped=0, face_count=7),)
        assert report.objects[0].face_count == api_report["objects"][0]["face_count"]
        assert _geometry(OPS_OBJECT_NAME) == _geometry(API_OBJECT_NAME)

        out = tmp_path / "exported.spyrite.yaml"
        exported = ops.export_spec(objects=[OPS_OBJECT_NAME], spec_path=out)
        api_exported = _api().export_spec([API_OBJECT_NAME], str(tmp_path / "api_exported.spyrite.yaml"))
        assert exported == ops.SpecExportReport(str(out), objects=1, tiles=7, unexported_faces={})
        assert (exported.objects, exported.tiles) == (api_exported["objects"], api_exported["tiles"])
        # The api spec reuses the ops tileset (same image), so only the object name differs
        assert out.read_text(encoding="utf-8").replace(OPS_OBJECT_NAME, "O") == (
            (tmp_path / "api_exported.spyrite.yaml").read_text(encoding="utf-8").replace(API_OBJECT_NAME, "O")
        )
    finally:
        _remove_test_data()


def test_verify_tile_object_matches_api(tileset, tmp_path):
    _create_object_pair()
    ops.fill_tiles(OPS_OBJECT_NAME, TILESET_NAME, (0, 0), (3, 3), (2, 1))
    ops.place_tiles(OPS_OBJECT_NAME, TILESET_NAME, [ops.TilePlacement(cell_xy=(1, 1), tile_xy=(0, 3))])
    objects_before = sorted(o.name for o in bpy.data.objects)
    report = ops.verify_tile_object(OPS_OBJECT_NAME, view="top", evidence_dir=tmp_path / "ops")
    api_result = _api().verify_tile_object(OPS_OBJECT_NAME, view="top", evidence_dir=str(tmp_path / "api"))

    assert isinstance(report, ops.VerifyReport)
    assert report.ok is api_result["ok"] is True
    assert report.measured == api_result["measured"] == 16
    assert report.mismatches == () and api_result["mismatches"] == []
    assert report.max_channel_delta == pytest.approx(api_result["max_channel_delta"])
    assert report.evidence_dir == str(tmp_path / "ops")
    assert report.render_path == str(tmp_path / "ops" / "render.png")
    assert Path(report.render_path).is_file()
    assert sorted(o.name for o in bpy.data.objects) == objects_before

    # UVs that show another tile than the face data says come back as FaceMismatch
    obj = bpy.data.objects[OPS_OBJECT_NAME]
    mesh = bmesh.new()
    mesh.from_mesh(obj.data)
    mesh.faces.ensure_lookup_table()
    saved = {layer.name: mesh.faces[0][layer] for layer in mesh.faces.layers.int}
    mesh.free()
    _api().paint_faces(OPS_OBJECT_NAME, TILESET_NAME, [0], (3, 3))
    mesh = bmesh.new()
    mesh.from_mesh(obj.data)
    mesh.faces.ensure_lookup_table()
    for layer in mesh.faces.layers.int:
        mesh.faces[0][layer] = saved[layer.name]
    mesh.to_mesh(obj.data)
    mesh.free()
    bad = ops.verify_tile_object(OPS_OBJECT_NAME, view="top", evidence_dir=tmp_path / "bad")
    assert bad.ok is False and bad.measured == 16
    assert len(bad.mismatches) == 1
    mismatch = bad.mismatches[0]
    assert isinstance(mismatch, ops.FaceMismatch)
    assert (mismatch.index, mismatch.plane) == (0, "XY")
    assert mismatch.tile_xy == (2, 1) and len(mismatch.cell_xy) == 2
    assert mismatch.max_channel_delta > 12.0


def test_reload_core_matches_api():
    report = ops.reload_core()
    assert list(report.reloaded) == ops._addon_api().reload_core()["reloaded"]


def test_tile_object_and_scene_report_are_the_only_reads_only_ops():
    from blended.ops._contract import is_reads_only

    assert [name for name in ops.__all__ if is_reads_only(getattr(ops, name))] == [
        "tile_object_report",
        "scene_report",
        "select_tile_faces",
        "list_checkpoints",
        "export_spec",
        "verify_tile_object",
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
