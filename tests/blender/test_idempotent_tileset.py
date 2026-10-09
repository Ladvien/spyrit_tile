"""Idempotent tilesets: the same image with the same layout never yields a second material."""

import importlib
from pathlib import Path

import bpy
import pytest

api = importlib.import_module("bl_ext.user_default.spyrite_tile.api")
sprytile_utils = importlib.import_module("bl_ext.user_default.spyrite_tile.sprytile_utils")

FIXTURE_IMAGE = Path(__file__).resolve().parent.parent / "fixtures" / "tiles_16px.png"
PREFIX = "idem_"


def _remove_test_data():
    for obj in [o for o in bpy.data.objects if o.name.startswith(PREFIX)]:
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in [m for m in bpy.data.meshes if m.name.startswith(PREFIX)]:
        bpy.data.meshes.remove(mesh)
    for material in [m for m in bpy.data.materials if m.name.startswith(PREFIX) or m.name == FIXTURE_IMAGE.stem]:
        bpy.data.materials.remove(material)
    sprytile_utils.validate_grids(bpy.context.scene)


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


def test_different_layout_gets_its_own_material(tmp_path):
    # a copy without the fixture's tile-name sidecar, which only fits 16 px tiles
    image = tmp_path / "tiles.png"
    image.write_bytes(FIXTURE_IMAGE.read_bytes())
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
