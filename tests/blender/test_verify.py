"""verify_tile_object renders a tile object with Workbench and judges every visible face from pixels.

Ground truth is the tileset image file (distinct colours / oriented quadrants) and the placement dicts handed
to ``place_tiles``; the render is the thing under test.
"""

import importlib
import os
import shutil
from pathlib import Path

import bmesh
import bpy
import pytest

api = importlib.import_module("bl_ext.user_default.spyrite_tile.api")
sprytile_core = importlib.import_module("bl_ext.user_default.spyrite_tile.sprytile_core")

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
COLOR_IMAGE = FIXTURES / "tiles_16px.png"
ORIENTED_IMAGE = FIXTURES / "tiles_oriented_16px.png"
PREFIX = "verify_test_"
TILESET = PREFIX + "tiles"
OBJECT = PREFIX + "obj"
PPU = 16
FLIPS = [(False, False), (True, False), (False, True), (True, True)]


def _remove_test_data():
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for obj in [o for o in bpy.data.objects if o.name.startswith(PREFIX)]:
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in [m for m in bpy.data.meshes if m.name.startswith(PREFIX)]:
        bpy.data.meshes.remove(mesh)
    for collection in [c for c in bpy.data.collections if c.name.startswith(PREFIX)]:
        bpy.data.collections.remove(collection)
    for material in [m for m in bpy.data.materials if m.name.startswith(PREFIX)]:
        bpy.data.materials.remove(material)
    sprytile_core.validate_grids(bpy.context.scene)


@pytest.fixture(autouse=True)
def clean_scene():
    _remove_test_data()
    yield
    _remove_test_data()


def _board(image, plane="XY", offset=0.0, orientations=False):
    """A 4x4 board on ``plane`` where cell (x, y) shows tile (x, y); optionally turned and mirrored."""
    assert api.create_tileset(TILESET, str(image), (16, 16))["reused_material"] is None
    api.create_tile_object(OBJECT, TILESET, PPU)
    placements = [
        {
            "cell_xy": [x, y],
            "tile_xy": [x, y],
            "plane": plane,
            "plane_offset_m": offset,
            "rotation_deg": 90 * x if orientations else 0,
            "flip_x": FLIPS[y][0] if orientations else False,
            "flip_y": FLIPS[y][1] if orientations else False,
        }
        for x in range(4)
        for y in range(4)
    ]
    api.place_tiles(OBJECT, TILESET, placements)
    return bpy.data.objects[OBJECT]


def _scene_state():
    scene = bpy.context.scene
    render = scene.render
    return {
        "engine": render.engine,
        "camera": scene.camera,
        "resolution": (render.resolution_x, render.resolution_y, render.resolution_percentage),
        "filepath": render.filepath,
        "use_file_extension": render.use_file_extension,
        "media_type": render.image_settings.media_type,
        "file_format": render.image_settings.file_format,
        "color_management": render.image_settings.color_management,
        "render_flags": (
            render.use_stamp, render.use_border, render.use_crop_to_border, render.use_compositing,
            render.use_sequencer, render.film_transparent, render.dither_intensity,
        ),
        "shading": (scene.display.shading.color_type, scene.display.shading.light),
        "render_aa": scene.display.render_aa,
        "view_transform": scene.view_settings.view_transform,
        "look": scene.view_settings.look,
        "curve_mapping": scene.view_settings.use_curve_mapping,
        "display_device": scene.display_settings.display_device,
        "hide_render": sorted((o.name, o.hide_render) for o in bpy.data.objects),
        "objects": sorted(o.name for o in bpy.data.objects),
        "cameras": sorted(c.name for c in bpy.data.cameras),
        "images": sorted(i.name for i in bpy.data.images),
        "active": bpy.context.view_layer.objects.active,
    }


def _tile_state(object_name):
    return api.describe_tile_object(object_name)


def test_board_with_distinct_tiles_verifies(tmp_path):
    _board(COLOR_IMAGE)
    before = _scene_state()
    described_before = _tile_state(OBJECT)
    report = api.verify_tile_object(OBJECT, view="top", evidence_dir=str(tmp_path))
    assert report["ok"] is True
    assert report["measured"] == 16
    assert report["mismatches"] == []
    assert report["max_channel_delta"] <= 12.0
    assert report["evidence_dir"] == str(tmp_path)
    assert report["render_path"] == str(tmp_path / "render.png")
    assert os.path.getsize(report["render_path"]) > 0
    # the scene is exactly as it was: settings, hidden flags, datablocks, mesh data
    assert _scene_state() == before
    assert _tile_state(OBJECT) == described_before


def test_auto_view_picks_the_plane_with_most_faces(tmp_path):
    _board(COLOR_IMAGE, plane="XZ", offset=2.0)
    report = api.verify_tile_object(OBJECT, evidence_dir=str(tmp_path))
    assert (report["ok"], report["measured"]) == (True, 16)
    wrong_side = api.verify_tile_object(OBJECT, view="top", evidence_dir=str(tmp_path))
    assert wrong_side["measured"] == 0 and wrong_side["ok"] is False


@pytest.mark.parametrize("plane,view", [("XY", "top"), ("XZ", "front"), ("YZ", "right")])
def test_orientations_verify_on_every_plane(tmp_path, plane, view):
    _board(ORIENTED_IMAGE, plane=plane, offset=3.0, orientations=True)
    report = api.verify_tile_object(OBJECT, view=view, evidence_dir=str(tmp_path))
    assert (report["ok"], report["measured"], report["mismatches"]) == (True, 16, [])


def test_default_evidence_dir_is_a_fresh_temporary_directory():
    _board(COLOR_IMAGE)
    first = api.verify_tile_object(OBJECT)
    second = api.verify_tile_object(OBJECT)
    try:
        assert first["ok"] and second["ok"]
        assert first["evidence_dir"] != second["evidence_dir"]
        assert os.path.basename(first["evidence_dir"]).startswith("spyrite_verify_")
        assert os.path.exists(first["render_path"])
    finally:
        shutil.rmtree(first["evidence_dir"], ignore_errors=True)
        shutil.rmtree(second["evidence_dir"], ignore_errors=True)


def _repaint_keeping_layers(object_name, face_index, tile_xy):
    """paint_faces moves the UVs (and the face layers); put every layer back so the data goes stale."""
    obj = bpy.data.objects[object_name]
    mesh = bmesh.new()
    mesh.from_mesh(obj.data)
    mesh.faces.ensure_lookup_table()
    layers = list(mesh.faces.layers.int)
    saved = {layer.name: mesh.faces[face_index][layer] for layer in layers}
    mesh.free()
    api.paint_faces(object_name, TILESET, [face_index], tile_xy)
    mesh = bmesh.new()
    mesh.from_mesh(obj.data)
    mesh.faces.ensure_lookup_table()
    for layer in mesh.faces.layers.int:
        mesh.faces[face_index][layer] = saved[layer.name]
    mesh.to_mesh(obj.data)
    mesh.free()


def test_a_face_whose_uvs_show_another_tile_is_a_mismatch(tmp_path):
    _board(COLOR_IMAGE)
    described = _tile_state(OBJECT)
    victim = next(f for f in described["faces"] if f["cell_xy"] == [1, 2])
    assert victim["tile_xy"] == [1, 2]
    _repaint_keeping_layers(OBJECT, victim["index"], (3, 0))
    # the data still claims tile (1, 2): the render does not
    assert _tile_state(OBJECT)["faces"][victim["index"]]["tile_xy"] == [1, 2]

    report = api.verify_tile_object(OBJECT, view="top", evidence_dir=str(tmp_path))
    assert report["ok"] is False
    assert report["measured"] == 16
    assert [m["index"] for m in report["mismatches"]] == [victim["index"]]
    mismatch = report["mismatches"][0]
    assert (mismatch["plane"], mismatch["cell_xy"], mismatch["tile_xy"]) == ("XY", [1, 2], [1, 2])
    assert mismatch["max_channel_delta"] > 12.0
    assert report["max_channel_delta"] == mismatch["max_channel_delta"]
    # a tolerance above the difference accepts it again
    lenient = api.verify_tile_object(OBJECT, view="top", tolerance=255.0, evidence_dir=str(tmp_path))
    assert lenient["ok"] is True and lenient["mismatches"] == []


def _set_face_layer(object_name, face_index, layer_name, value):
    obj = bpy.data.objects[object_name]
    mesh = bmesh.new()
    mesh.from_mesh(obj.data)
    mesh.faces.ensure_lookup_table()
    mesh.faces[face_index][mesh.faces.layers.int[layer_name]] = value
    mesh.to_mesh(obj.data)
    mesh.free()


def test_a_face_with_the_right_tile_but_the_wrong_orientation_is_a_mismatch(tmp_path):
    _board(ORIENTED_IMAGE, orientations=True)
    victim = next(f for f in _tile_state(OBJECT)["faces"] if f["cell_xy"] == [1, 0])
    assert (victim["rotation_deg"], victim["flip_x"], victim["flip_y"]) == (90, False, False)
    obj = bpy.data.objects[OBJECT]
    mesh = bmesh.new()
    mesh.from_mesh(obj.data)
    mesh.faces.ensure_lookup_table()
    settings = mesh.faces[victim["index"]][mesh.faces.layers.int["paint_settings"]]
    mesh.free()
    # the data now says "not turned" while the UVs still show the turned tile
    _set_face_layer(OBJECT, victim["index"], "paint_settings", settings & ~(3 << 10))
    stale = _tile_state(OBJECT)["faces"][victim["index"]]
    assert (stale["tile_xy"], stale["rotation_deg"]) == (victim["tile_xy"], 0)

    report = api.verify_tile_object(OBJECT, view="top", evidence_dir=str(tmp_path))
    assert report["ok"] is False and report["measured"] == 16
    assert [m["index"] for m in report["mismatches"]] == [victim["index"]]


def test_a_scene_that_outputs_video_still_verifies_and_keeps_its_output_settings(tmp_path):
    _board(COLOR_IMAGE)
    image_settings = bpy.context.scene.render.image_settings
    image_settings.media_type = "VIDEO"
    image_settings.file_format = "FFMPEG"
    try:
        before = _scene_state()
        report = api.verify_tile_object(OBJECT, view="top", evidence_dir=str(tmp_path))
        assert (report["ok"], report["measured"]) == (True, 16)
        assert _scene_state() == before
        assert (image_settings.media_type, image_settings.file_format) == ("VIDEO", "FFMPEG")
    finally:
        image_settings.media_type = "IMAGE"
        image_settings.file_format = "PNG"


def _stamp(scene):
    scene.render.use_stamp = True


def _border(scene):
    scene.render.use_border = True
    scene.render.border_min_x = scene.render.border_min_y = 0.25
    scene.render.border_max_x = scene.render.border_max_y = 0.75


def _border_cropped(scene):
    _border(scene)
    scene.render.use_crop_to_border = True


def _output_colour_override(scene):
    scene.render.image_settings.color_management = "OVERRIDE"
    scene.render.image_settings.view_settings.view_transform = "AgX"


def _curve_mapping(scene):
    scene.view_settings.use_curve_mapping = True


def _dither(scene):
    scene.render.dither_intensity = 2.0


def _film_transparent(scene):
    scene.render.film_transparent = True


def _compositing_and_sequencer(scene):
    scene.render.use_compositing = scene.render.use_sequencer = True


@pytest.mark.parametrize(
    "perturb",
    [_stamp, _border, _border_cropped, _output_colour_override, _curve_mapping, _dither, _film_transparent,
     _compositing_and_sequencer],
)
def test_user_render_settings_that_alter_the_image_do_not_fail_a_correct_board(tmp_path, perturb):
    _board(COLOR_IMAGE)
    perturb(bpy.context.scene)
    try:
        before = _scene_state()
        report = api.verify_tile_object(OBJECT, view="top", evidence_dir=str(tmp_path))
        assert (report["ok"], report["measured"], report["mismatches"]) == (True, 16, [])
        assert _scene_state() == before
    finally:
        render = bpy.context.scene.render
        render.use_stamp = render.use_border = render.use_crop_to_border = False
        render.film_transparent = render.use_compositing = render.use_sequencer = False
        render.dither_intensity = 1.0
        render.image_settings.color_management = "FOLLOW_SCENE"
        bpy.context.scene.view_settings.use_curve_mapping = False


def test_a_face_facing_away_from_the_camera_still_hides_the_faces_behind_it(tmp_path):
    obj = _board(COLOR_IMAGE)
    mesh = bmesh.new()
    mesh.from_mesh(obj.data)
    # a 2 m x 2 m quad above cells (0..1, 0..1), wound so its normal points down, away from the top camera
    corners = ((0.0, 0.0, 0.5), (0.0, 2.0, 0.5), (2.0, 2.0, 0.5), (2.0, 0.0, 0.5))
    mesh.faces.new([mesh.verts.new(c) for c in corners])
    mesh.to_mesh(obj.data)
    mesh.free()
    report = api.verify_tile_object(OBJECT, view="top", evidence_dir=str(tmp_path))
    assert (report["ok"], report["measured"], report["mismatches"]) == (True, 12, [])


def test_a_target_in_a_collection_hidden_from_render_is_refused_naming_it(tmp_path):
    obj = _board(COLOR_IMAGE)
    hidden = bpy.data.collections.new(PREFIX + "hidden")
    bpy.context.scene.collection.children.link(hidden)
    hidden.objects.link(obj)
    bpy.context.scene.collection.objects.unlink(obj)
    hidden.hide_render = True
    before = _scene_state()
    with pytest.raises(ValueError, match=f"Object '{OBJECT}' is not rendered: collection '{hidden.name}' has hide_render set"):
        api.verify_tile_object(OBJECT, view="top", evidence_dir=str(tmp_path))
    assert _scene_state() == before
    # linked into a second, rendered collection it renders again
    bpy.context.scene.collection.objects.link(obj)
    assert api.verify_tile_object(OBJECT, view="top", evidence_dir=str(tmp_path))["ok"] is True


def test_a_decal_hides_its_base_and_is_judged_instead(tmp_path):
    _board(COLOR_IMAGE)
    api.place_tiles(OBJECT, TILESET, [{"cell_xy": [0, 0], "tile_xy": [3, 3], "layer": "DECAL"}])
    assert _tile_state(OBJECT)["face_count"] == 17
    report = api.verify_tile_object(OBJECT, view="top", evidence_dir=str(tmp_path))
    assert (report["ok"], report["measured"], report["mismatches"]) == (True, 16, [])


def test_faces_that_are_not_whole_cells_are_skipped_not_judged(tmp_path):
    _board(COLOR_IMAGE)
    obj = bpy.data.objects[OBJECT]
    mesh = bmesh.new()
    mesh.from_mesh(obj.data)
    verts = [mesh.verts.new(v) for v in ((5.0, 0.0, 0.0), (6.0, 0.0, 0.0), (5.0, 1.0, 0.0))]
    mesh.faces.new(verts)
    mesh.to_mesh(obj.data)
    mesh.free()
    report = api.verify_tile_object(OBJECT, view="top", evidence_dir=str(tmp_path))
    assert (report["ok"], report["measured"]) == (True, 16)


def test_verify_works_from_edit_mode_and_restores_it(tmp_path):
    obj = _board(COLOR_IMAGE)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    report = api.verify_tile_object(OBJECT, view="top", evidence_dir=str(tmp_path))
    assert (report["ok"], report["measured"]) == (True, 16)
    assert obj.mode == "EDIT"
    bpy.ops.object.mode_set(mode="OBJECT")


def test_other_objects_do_not_cover_the_object(tmp_path):
    _board(COLOR_IMAGE)
    bpy.ops.mesh.primitive_plane_add(size=40.0, location=(2.0, 2.0, 0.5))
    cover = bpy.context.active_object
    cover.name = cover.data.name = PREFIX + "cover"
    bpy.context.view_layer.objects.active = bpy.data.objects[OBJECT]
    report = api.verify_tile_object(OBJECT, view="top", evidence_dir=str(tmp_path))
    assert (report["ok"], report["measured"]) == (True, 16)
    assert cover.hide_render is False


def test_a_render_failure_names_the_host_fallback_and_restores_the_scene(monkeypatch, tmp_path):
    _board(COLOR_IMAGE)
    before = _scene_state()

    def broken():
        raise RuntimeError("no GPU context")

    monkeypatch.setattr(api, "_render_still", broken)
    with pytest.raises(RuntimeError) as raised:
        api.verify_tile_object(OBJECT, view="top", evidence_dir=str(tmp_path))
    assert str(raised.value) == (
        "verify_tile_object could not render with Workbench: no GPU context; "
        "run `make test-visual` from the host instead"
    )
    assert _scene_state() == before


def test_bad_arguments_are_refused_before_touching_the_scene(tmp_path):
    _board(COLOR_IMAGE)
    before = _scene_state()
    with pytest.raises(ValueError, match=r"view must be one of \['auto', 'front', 'right', 'top'\]"):
        api.verify_tile_object(OBJECT, view="bottom")
    with pytest.raises(ValueError, match="tolerance must be >= 0"):
        api.verify_tile_object(OBJECT, tolerance=-1.0)
    with pytest.raises(ValueError, match="tolerance must be a finite number"):
        api.verify_tile_object(OBJECT, tolerance="loose")
    with pytest.raises(ValueError, match="evidence_dir must be an absolute path"):
        api.verify_tile_object(OBJECT, evidence_dir="relative/dir")
    with pytest.raises(ValueError, match="No object named 'verify_test_nope'"):
        api.verify_tile_object(PREFIX + "nope")
    assert _scene_state() == before


def test_an_object_without_faces_is_refused(tmp_path):
    api.create_tileset(TILESET, str(COLOR_IMAGE), (16, 16))
    api.create_tile_object(OBJECT, TILESET, PPU)
    with pytest.raises(ValueError, match="has no faces to verify; place tiles first"):
        api.verify_tile_object(OBJECT, evidence_dir=str(tmp_path))


def test_an_object_too_large_for_one_render_is_refused(tmp_path):
    api.create_tileset(TILESET, str(COLOR_IMAGE), (16, 16))
    api.create_tile_object(OBJECT, TILESET, PPU)
    api.place_tiles(OBJECT, TILESET, [
        {"cell_xy": [0, 0], "tile_xy": [0, 0]}, {"cell_xy": [400, 0], "tile_xy": [1, 0]},
    ])
    with pytest.raises(ValueError, match="Verify a smaller object"):
        api.verify_tile_object(OBJECT, view="top", evidence_dir=str(tmp_path))
