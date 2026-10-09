"""spyrite_tile.api checkpoint / rollback / discard_checkpoint / list_checkpoints, inside Blender."""

import importlib
from pathlib import Path

import bpy
import pytest

api = importlib.import_module("bl_ext.user_default.spyrite_tile.api")
sprytile_core = importlib.import_module("bl_ext.user_default.spyrite_tile.sprytile_core")

FIXTURE_IMAGE = Path(__file__).resolve().parent.parent / "fixtures" / "tiles_16px.png"
PREFIX = "ckpt_test_"
TILESET = PREFIX + "tiles"
OBJECT = PREFIX + "obj"
OTHER = PREFIX + "other"
OVERLAY = PREFIX + "overlay"


def _clean():
    for checkpoint in api.list_checkpoints():
        api.discard_checkpoint(checkpoint["checkpoint_id"])
    for obj in [o for o in bpy.data.objects if o.name.startswith(PREFIX)]:
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in [m for m in bpy.data.meshes if m.name.startswith(PREFIX)]:
        bpy.data.meshes.remove(mesh)
    for material in [m for m in bpy.data.materials if m.name.startswith(PREFIX)]:
        bpy.data.materials.remove(material)
    sprytile_core.validate_grids(bpy.context.scene)


@pytest.fixture(autouse=True)
def clean_scene():
    _clean()
    yield
    _clean()


@pytest.fixture
def tile_object():
    assert api.create_tileset(TILESET, str(FIXTURE_IMAGE), (16, 16))["reused_material"] is None
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
    grid_id = tile_object.sprytile_gridid
    made = api.checkpoint(None)
    assert made["objects"] == [OBJECT]
    for _ in range(2):
        api.fill_tiles(OBJECT, TILESET, (0, 0), (5, 5), (1, 1))
        tile_object["spyrite_pixels_per_unit"] = 99
        tile_object.location = (3, 4, 5)
        tile_object.data.materials.append(bpy.data.materials.new(PREFIX + "extra"))
        tile_object.sprytile_gridid = -1
        api.rollback(made["checkpoint_id"])
        assert len(tile_object.data.polygons) == 4
        assert tuple(tile_object.location) == (0, 0, 0)
        assert tile_object.get(api.PIXELS_PER_UNIT_PROP) == 16
        assert [s.material.name for s in tile_object.material_slots] == [TILESET]
        assert tile_object.sprytile_gridid == grid_id


def test_rollback_keeps_a_renamed_material_in_its_slot(tile_object):
    made = api.checkpoint([OBJECT])
    bpy.data.materials[TILESET].name = PREFIX + "renamed"
    api.rollback(made["checkpoint_id"])
    assert [s.material.name for s in tile_object.material_slots] == [PREFIX + "renamed"]
    assert len(tile_object.data.polygons) == 4


def test_rollback_with_a_child_listed_before_its_moved_parent(tile_object):
    api.create_overlay_object(OVERLAY, OBJECT, TILESET)
    overlay = bpy.data.objects[OVERLAY]
    api.fill_tiles(OVERLAY, TILESET, (0, 0), (0, 0), (1, 1))
    bpy.context.view_layer.update()
    before = overlay.matrix_world.translation.copy()
    made = api.checkpoint([OVERLAY, OBJECT])
    assert made["objects"] == [OVERLAY, OBJECT]
    tile_object.location = (5, 0, 0)
    bpy.context.view_layer.update()
    api.rollback(made["checkpoint_id"])
    bpy.context.view_layer.update()
    assert overlay.parent == tile_object
    assert tuple(tile_object.matrix_world.translation) == (0, 0, 0)
    assert tuple(overlay.matrix_world.translation) == pytest.approx(tuple(before))


def test_rollback_restores_a_changed_parent(tile_object):
    api.create_tile_object(OTHER, TILESET, 16)
    other = bpy.data.objects[OTHER]
    api.fill_tiles(OTHER, TILESET, (0, 0), (0, 0), (0, 0))
    other.parent = tile_object
    other.matrix_parent_inverse = tile_object.matrix_world.inverted()
    made = api.checkpoint([OTHER])
    other.parent = None
    other.location = (2, 2, 2)
    api.rollback(made["checkpoint_id"])
    assert other.parent == tile_object and tuple(other.location) == (0, 0, 0)


def test_a_deleted_checkpoint_mesh_is_a_named_error_and_leaves_the_listing(tile_object):
    api.create_tile_object(OTHER, TILESET, 16)
    api.fill_tiles(OTHER, TILESET, (0, 0), (0, 0), (0, 0))
    made = api.checkpoint([OBJECT, OTHER])
    keep = api.checkpoint([OBJECT], label="keep")
    bpy.data.meshes.remove(bpy.data.meshes[f".spyrite_ckpt_{made['checkpoint_id']}_{OTHER}"])
    with pytest.raises(ValueError, match=f"checkpoint {made['checkpoint_id']}: .*no longer exists.*call checkpoint again"):
        api.rollback(made["checkpoint_id"])
    # the broken checkpoint is gone, with its surviving copy; the intact one is untouched
    assert [c["checkpoint_id"] for c in api.list_checkpoints()] == [keep["checkpoint_id"]]
    assert _hidden_meshes() == [f".spyrite_ckpt_{keep['checkpoint_id']}_{OBJECT}"]

    broken = api.checkpoint([OBJECT, OTHER])
    bpy.data.meshes.remove(bpy.data.meshes[f".spyrite_ckpt_{broken['checkpoint_id']}_{OBJECT}"])
    assert [c["checkpoint_id"] for c in api.list_checkpoints()] == [keep["checkpoint_id"]]
    assert _hidden_meshes() == [f".spyrite_ckpt_{keep['checkpoint_id']}_{OBJECT}"]

    broken = api.checkpoint([OBJECT, OTHER])
    bpy.data.meshes.remove(bpy.data.meshes[f".spyrite_ckpt_{broken['checkpoint_id']}_{OTHER}"])
    assert api.discard_checkpoint(broken["checkpoint_id"])["discarded"] == [OBJECT, OTHER]
    assert [c["checkpoint_id"] for c in api.list_checkpoints()] == [keep["checkpoint_id"]]
    assert _hidden_meshes() == [f".spyrite_ckpt_{keep['checkpoint_id']}_{OBJECT}"]


def test_duplicate_object_names_are_snapshotted_once(tile_object):
    made = api.checkpoint([OBJECT, OBJECT])
    assert made["objects"] == [OBJECT]
    assert len(_hidden_meshes()) == 1
    api.discard_checkpoint(made["checkpoint_id"])
    assert _hidden_meshes() == []


def test_checkpoints_survive_reload_core_and_ids_do_not_restart(tile_object):
    first = api.checkpoint([OBJECT], label="before reload")
    api.fill_tiles(OBJECT, TILESET, (0, 0), (5, 5), (1, 1))
    api.reload_core()
    assert [(c["checkpoint_id"], c["label"]) for c in api.list_checkpoints()] == [
        (first["checkpoint_id"], "before reload")
    ]
    api.rollback(first["checkpoint_id"])
    assert len(tile_object.data.polygons) == 4
    second = api.checkpoint([OBJECT])
    assert second["checkpoint_id"] == first["checkpoint_id"] + 1
    api.discard_checkpoint(first["checkpoint_id"])
    api.discard_checkpoint(second["checkpoint_id"])
    assert _hidden_meshes() == []


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
