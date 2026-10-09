"""spyrite_tile.api checkpoint / rollback / discard_checkpoint / list_checkpoints, inside Blender."""

import importlib
from pathlib import Path

import bpy
import pytest

api = importlib.import_module("bl_ext.user_default.spyrite_tile.api")
sprytile_utils = importlib.import_module("bl_ext.user_default.spyrite_tile.sprytile_utils")

FIXTURE_IMAGE = Path(__file__).resolve().parent.parent / "fixtures" / "tiles_16px.png"
PREFIX = "ckpt_test_"
TILESET = PREFIX + "tiles"
OBJECT = PREFIX + "obj"
OTHER = PREFIX + "other"


def _clean():
    for checkpoint in api.list_checkpoints():
        api.discard_checkpoint(checkpoint["checkpoint_id"])
    for obj in [o for o in bpy.data.objects if o.name.startswith(PREFIX)]:
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in [m for m in bpy.data.meshes if m.name.startswith(PREFIX)]:
        bpy.data.meshes.remove(mesh)
    for material in [m for m in bpy.data.materials if m.name.startswith(PREFIX)]:
        bpy.data.materials.remove(material)
    sprytile_utils.validate_grids(bpy.context.scene)


@pytest.fixture(autouse=True)
def clean_scene():
    _clean()
    yield
    _clean()


@pytest.fixture
def tile_object():
    api.create_tileset(TILESET, str(FIXTURE_IMAGE), (16, 16))
    api.create_tile_object(OBJECT, TILESET, 16)
    api.fill_tiles(OBJECT, TILESET, (0, 0), (1, 1), (0, 0))
    return bpy.data.objects[OBJECT]


def _hidden_meshes():
    return [m.name for m in bpy.data.meshes if m.name.startswith(".spyrite_ckpt_")]


def test_rollback_restores_faces_and_readback(tile_object):
    before = api.describe_tile_object(OBJECT, max_faces=0)
    made = api.checkpoint([OBJECT], label="before fill")
    assert made["objects"] == [OBJECT]
    api.fill_tiles(OBJECT, TILESET, (0, 0), (5, 5), (1, 1))
    assert len(tile_object.data.polygons) == 36
    restored = api.rollback(made["checkpoint_id"])
    assert restored == {"checkpoint_id": made["checkpoint_id"], "restored": [OBJECT]}
    assert len(bpy.data.objects[OBJECT].data.polygons) == before["face_count"] == 4
    assert api.describe_tile_object(OBJECT, max_faces=0) == before
    assert bpy.data.objects[OBJECT].data.name == OBJECT


def test_rollback_twice_and_restores_properties(tile_object):
    made = api.checkpoint(None)
    assert made["objects"] == [OBJECT]
    for _ in range(2):
        api.fill_tiles(OBJECT, TILESET, (0, 0), (5, 5), (1, 1))
        tile_object["spyrite_pixels_per_unit"] = 99
        tile_object.location = (3, 4, 5)
        api.rollback(made["checkpoint_id"])
        assert len(tile_object.data.polygons) == 4
        assert tuple(tile_object.location) == (0, 0, 0)
        assert tile_object.get(api.PIXELS_PER_UNIT_PROP) == 16
        assert [s.material.name for s in tile_object.material_slots] == [TILESET]


def test_missing_object_error_is_exact_and_restores_nothing(tile_object):
    api.create_tile_object(OTHER, TILESET, 16)
    api.fill_tiles(OTHER, TILESET, (0, 0), (0, 0), (0, 0))
    made = api.checkpoint([OBJECT, OTHER])
    api.fill_tiles(OBJECT, TILESET, (0, 0), (5, 5), (1, 1))
    bpy.data.objects.remove(bpy.data.objects[OTHER], do_unlink=True)
    with pytest.raises(ValueError) as error:
        api.rollback(made["checkpoint_id"])
    assert str(error.value) == (
        f"checkpoint {made['checkpoint_id']}: objects missing: ['{OTHER}']; nothing was restored"
    )
    assert len(tile_object.data.polygons) == 36


def test_list_and_discard(tile_object):
    first = api.checkpoint([OBJECT], label="a")
    second = api.checkpoint([OBJECT], label="b")
    listing = api.list_checkpoints()
    assert [(c["checkpoint_id"], c["label"], c["objects"]) for c in listing] == [
        (first["checkpoint_id"], "a", [OBJECT]),
        (second["checkpoint_id"], "b", [OBJECT]),
    ]
    assert listing[0]["created"].endswith("+00:00")
    assert len(_hidden_meshes()) == 2
    api.discard_checkpoint(first["checkpoint_id"])
    assert len(_hidden_meshes()) == 1
    api.discard_checkpoint(second["checkpoint_id"])
    assert _hidden_meshes() == [] and api.list_checkpoints() == []
    with pytest.raises(ValueError, match="No checkpoint .*existing checkpoint ids: \\[\\]"):
        api.rollback(first["checkpoint_id"])


def test_checkpoint_validates_before_touching_data(tile_object):
    with pytest.raises(ValueError, match="No object named 'nope'"):
        api.checkpoint([OBJECT, "nope"])
    assert _hidden_meshes() == [] and api.list_checkpoints() == []


def test_checkpoint_from_edit_mode_restores_mode(tile_object):
    bpy.context.view_layer.objects.active = tile_object
    bpy.ops.object.mode_set(mode="EDIT")
    made = api.checkpoint([OBJECT])
    assert tile_object.mode == "EDIT"
    api.rollback(made["checkpoint_id"])
    assert tile_object.mode == "EDIT"
    bpy.ops.object.mode_set(mode="OBJECT")
