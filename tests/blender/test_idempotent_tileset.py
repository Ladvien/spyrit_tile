"""Idempotent tilesets: the same image with the same layout never yields a second material."""

import importlib
from pathlib import Path

import bpy
import pytest

api = importlib.import_module("bl_ext.user_default.spyrite_tile.api")
sprytile_core = importlib.import_module("bl_ext.user_default.spyrite_tile.sprytile_core")

FIXTURE_IMAGE = Path(__file__).resolve().parent.parent / "fixtures" / "tiles_16px.png"
PREFIX = "idem_"


def _remove_test_data():
    for obj in [o for o in bpy.data.objects if o.name.startswith(PREFIX)]:
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in [m for m in bpy.data.meshes if m.name.startswith(PREFIX)]:
        bpy.data.meshes.remove(mesh)
    for material in [m for m in bpy.data.materials if m.name.startswith(PREFIX) or m.name == FIXTURE_IMAGE.stem]:
        bpy.data.materials.remove(material)
    sprytile_core.validate_grids(bpy.context.scene)


@pytest.fixture(autouse=True)
def clean_scene():
    _remove_test_data()
    yield
    _remove_test_data()


def _entries():
    return [m.mat_id for m in bpy.context.scene.sprytile_mats]


def test_same_image_and_layout_reuses_the_first_material():
    first = api.create_tileset(PREFIX + "a", str(FIXTURE_IMAGE), (16, 16))
    second = api.create_tileset(PREFIX + "b", str(FIXTURE_IMAGE), (16, 16))
    assert first["reused_material"] is None
    assert second["reused_material"] == PREFIX + "a"
    assert second["material_name"] == PREFIX + "a"
    assert second["grid_id"] == first["grid_id"]
    assert bpy.data.materials.get(PREFIX + "b") is None
    assert [e for e in _entries() if e.startswith(PREFIX)] == [PREFIX + "a"]
    # the returned name is directly usable
    api.create_tile_object(PREFIX + "obj", second["material_name"], 16)


def test_same_file_under_a_different_path_spelling_reuses_the_material(tmp_path):
    first = api.create_tileset(PREFIX + "a", str(FIXTURE_IMAGE), (16, 16))
    dotted = FIXTURE_IMAGE.parent / ".." / FIXTURE_IMAGE.parent.name / FIXTURE_IMAGE.name
    link = tmp_path / "linked.png"
    link.symlink_to(FIXTURE_IMAGE)
    for index, spelling in enumerate((dotted, link)):
        again = api.create_tileset(PREFIX + f"v{index}", str(spelling), (16, 16))
        assert again["reused_material"] == PREFIX + "a", spelling
        assert again["grid_id"] == first["grid_id"]
        assert bpy.data.materials.get(PREFIX + f"v{index}") is None
    assert [e for e in _entries() if e.startswith(PREFIX)] == [PREFIX + "a"]


def test_different_layout_gets_its_own_material():
    # the fixture's sidecar describes 16 px tiles; loading the image at 32 px must still work
    image = FIXTURE_IMAGE
    api.create_tileset(PREFIX + "a", str(image), (16, 16))
    other = api.create_tileset(PREFIX + "b", str(image), (32, 32))
    assert other["reused_material"] is None
    assert bpy.data.materials.get(PREFIX + "b") is not None
    padded = api.create_tileset(PREFIX + "c", str(image), (14, 14), padding_px=(1, 1))
    assert padded["reused_material"] is None
    assert [e for e in _entries() if e.startswith(PREFIX)] == [PREFIX + "a", PREFIX + "b", PREFIX + "c"]


def test_existing_material_name_is_not_redirected():
    api.create_tileset(PREFIX + "a", str(FIXTURE_IMAGE), (16, 16))
    again = api.create_tileset(PREFIX + "a", str(FIXTURE_IMAGE), (16, 16))
    assert again["reused_material"] is None
    assert again["material_name"] == PREFIX + "a"


def test_removed_tileset_is_not_resurrected_by_reuse():
    api.create_tileset(PREFIX + "a", str(FIXTURE_IMAGE), (16, 16))
    obj = bpy.data.objects.new(PREFIX + "obj", bpy.data.meshes.new(PREFIX + "obj"))
    bpy.context.scene.collection.objects.link(obj)
    api.create_tile_object(PREFIX + "obj", PREFIX + "a", 16)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.sprytile.grid_remove()
    assert PREFIX + "a" not in _entries()
    report = api.create_tileset(PREFIX + "b", str(FIXTURE_IMAGE), (16, 16))
    assert report["reused_material"] is None
    assert report["material_name"] == PREFIX + "b"


def _make_active_object(name):
    obj = bpy.data.objects.new(name, bpy.data.meshes.new(name))
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    return obj


def test_gui_load_tileset_twice_on_two_objects_makes_one_material():
    """Calls the operator itself (EXEC_DEFAULT works headless for tileset_load)."""
    stem = FIXTURE_IMAGE.stem
    first = _make_active_object(PREFIX + "one")
    assert bpy.ops.sprytile.tileset_load(filepath=str(FIXTURE_IMAGE)) == {'FINISHED'}
    material = first.material_slots[0].material
    assert material.name == stem
    grid_id = first.sprytile_gridid

    second = _make_active_object(PREFIX + "two")
    assert bpy.ops.sprytile.tileset_load(filepath=str(FIXTURE_IMAGE)) == {'FINISHED'}
    assert [s.material for s in second.material_slots] == [material]
    assert second.sprytile_gridid == grid_id
    assert [m.name for m in bpy.data.materials if m.name.startswith(stem)] == [stem]
    assert _entries().count(stem) == 1

    # tileset_new on an object that already has the slot just selects it
    assert bpy.ops.sprytile.tileset_new(filepath=str(FIXTURE_IMAGE)) == {'FINISHED'}
    assert len(second.material_slots) == 1
    assert [m.name for m in bpy.data.materials if m.name.startswith(stem)] == [stem]


    # the sidecar names a tile outside the 32 px layout (a 2x2 grid); reading back must not fail
    object_name = second.name
    api.create_tile_object(object_name, material.name, 16)
    api.place_tiles(object_name, material.name, [{"cell_xy": [0, 0], "tile_xy": [1, 1]}])
    assert api.describe_tile_object(object_name)["faces"][0]["tile_xy"] == [1, 1]
    tileset = next(t for t in api.describe_scene()["tilesets"] if t["material_name"] == stem)
    assert tileset["tile_size_px"] == [32, 32] and "wall_top" in tileset["tile_names"]


def test_gui_reuse_syncs_the_grid_list_highlight(tmp_path):
    other_image = tmp_path / "idem_other.png"
    other_image.write_bytes(FIXTURE_IMAGE.read_bytes())
    obj = _make_active_object(PREFIX + "one")
    scene = bpy.context.scene
    assert bpy.ops.sprytile.tileset_load(filepath=str(FIXTURE_IMAGE)) == {'FINISHED'}
    assert bpy.ops.sprytile.tileset_new(filepath=str(other_image)) == {'FINISHED'}
    assert bpy.ops.sprytile.tileset_load(filepath=str(FIXTURE_IMAGE)) == {'FINISHED'}
    display = scene.sprytile_list.display
    assert display[scene.sprytile_list.idx].grid_id == obj.sprytile_gridid
    bpy.data.materials.remove(bpy.data.materials["idem_other"])
