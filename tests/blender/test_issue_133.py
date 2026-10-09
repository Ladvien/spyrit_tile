"""Issue #133: the grid "-" button could not remove a loaded tileset.

`sprytile.grid_remove` on the last grid of a tileset used to return silently, so the tileset
stayed in scene.sprytile_mats and the list; the only way out was deleting the material and
pressing Validate. Now it removes the tileset from Sprytile's list (the material and image
stay: objects may still use them), repoints objects that used it, reports what it did, and
validate_grids does not list the still-used material again.
"""

import importlib
from pathlib import Path

import bpy
import pytest

api = importlib.import_module("bl_ext.user_default.spyrite_tile.api")
sprytile_utils = importlib.import_module("bl_ext.user_default.spyrite_tile.sprytile_utils")
sprytile_core = importlib.import_module("bl_ext.user_default.spyrite_tile.sprytile_core")

FIXTURE_IMAGE = Path(__file__).resolve().parent.parent / "fixtures" / "tiles_16px.png"
OTHER_IMAGE = FIXTURE_IMAGE.with_name("tiles_oriented_16px.png")
PREFIX = "i133_"
TILESET_A = PREFIX + "a"
TILESET_B = PREFIX + "b"
OBJECT_A = PREFIX + "obj_a"
OBJECT_B = PREFIX + "obj_b"


def _remove_test_data():
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


def _make(tileset_name, object_name):
    # distinct images: the same image + layout would reuse one tileset
    image = OTHER_IMAGE if tileset_name == TILESET_B else FIXTURE_IMAGE
    api.create_tileset(tileset_name, str(image), (16, 16))
    api.create_tile_object(object_name, tileset_name, 16)
    return bpy.data.objects[object_name]


def _activate(obj):
    bpy.context.view_layer.objects.active = obj


def _mat_ids():
    return [m.mat_id for m in bpy.context.scene.sprytile_mats]


def _display():
    """(tileset material id, grid_id) per list row, in order; grid_id is -1 on the tileset header rows."""
    return [(row.mat_id or row.parent_mat_id, row.grid_id) for row in bpy.context.scene.sprytile_list.display]


def test_removing_the_last_grid_removes_the_tileset_but_keeps_material_and_image():
    obj = _make(TILESET_A, OBJECT_A)
    _activate(obj)
    material = bpy.data.materials[TILESET_A]
    image = sprytile_core.get_material_texture(material)
    assert _mat_ids().count(TILESET_A) == 1
    assert (TILESET_A, -1) in _display()

    assert bpy.ops.sprytile.grid_remove() == {'FINISHED'}

    assert TILESET_A not in _mat_ids()
    assert all(mat_id != TILESET_A for mat_id, _ in _display())
    assert _display() == []
    assert obj.sprytile_gridid == -1
    assert sprytile_core.get_grid(bpy.context, obj.sprytile_gridid) is None
    # nothing of the user's data is deleted
    assert bpy.data.materials.get(TILESET_A) is material
    assert bpy.data.images.get(image.name) is image
    assert obj.material_slots[0].material is material


def test_validate_does_not_list_the_removed_tileset_again():
    obj = _make(TILESET_A, OBJECT_A)
    _activate(obj)
    material = bpy.data.materials[TILESET_A]
    # The object still uses the material and it has an image texture: exactly what made
    # validate_grids re-add it before.
    assert material.users > 0 and sprytile_core.get_material_texture_node(material) is not None
    bpy.ops.sprytile.grid_remove()

    sprytile_core.validate_grids(bpy.context.scene)
    assert TILESET_A not in _mat_ids()
    assert bpy.ops.sprytile.validate_grids() == {'FINISHED'}
    assert TILESET_A not in _mat_ids()
    assert all(mat_id != TILESET_A for mat_id, _ in _display())


def test_create_tileset_lists_a_removed_tileset_again():
    obj = _make(TILESET_A, OBJECT_A)
    _activate(obj)
    bpy.ops.sprytile.grid_remove()
    assert TILESET_A not in _mat_ids()

    api.create_tileset(TILESET_A, str(FIXTURE_IMAGE), (16, 16))
    assert _mat_ids().count(TILESET_A) == 1
    api.create_tile_object(OBJECT_A, TILESET_A, 16)  # builds on the re-listed grid
    assert sprytile_core.get_grid(bpy.context, obj.sprytile_gridid).mat_id == TILESET_A


def test_other_tilesets_and_objects_survive_and_dangling_grid_ids_are_repointed():
    obj_a = _make(TILESET_A, OBJECT_A)
    obj_b = _make(TILESET_B, OBJECT_B)
    grid_a = obj_a.sprytile_gridid
    grid_b = obj_b.sprytile_gridid
    assert grid_a != grid_b
    # a second object on tileset A that is not the active one
    other_a = bpy.data.objects.new(PREFIX + "other_a", bpy.data.meshes.new(PREFIX + "other_a"))
    bpy.context.scene.collection.objects.link(other_a)
    other_a.sprytile_gridid = grid_a

    _activate(obj_a)
    assert bpy.ops.sprytile.grid_remove() == {'FINISHED'}

    assert TILESET_A not in _mat_ids()
    assert TILESET_B in _mat_ids()
    assert (TILESET_B, -1) in _display()
    assert (TILESET_B, grid_b) in _display()
    for obj in (obj_a, other_a):
        assert obj.sprytile_gridid == grid_b
    assert obj_b.sprytile_gridid == grid_b
    # the list selection is the grid row of the remaining tileset
    scene = bpy.context.scene
    assert scene.sprytile_list.display[scene.sprytile_list.idx].grid_id == grid_b


def test_removing_one_of_several_grids_keeps_the_tileset():
    obj = _make(TILESET_A, OBJECT_A)
    _activate(obj)
    mat_data = sprytile_core.get_mat_data(bpy.context, TILESET_A)
    first_id = mat_data.grids[0].id
    assert bpy.ops.sprytile.grid_add() == {'FINISHED'}
    mat_data = sprytile_core.get_mat_data(bpy.context, TILESET_A)
    assert len(mat_data.grids) == 2
    second_id = mat_data.grids[1].id
    obj.sprytile_gridid = second_id

    assert bpy.ops.sprytile.grid_remove() == {'FINISHED'}

    mat_data = sprytile_core.get_mat_data(bpy.context, TILESET_A)
    assert mat_data is not None
    assert [g.id for g in mat_data.grids] == [first_id]
    assert (TILESET_A, -1) in _display() and (TILESET_A, first_id) in _display()


def test_nothing_selected_removes_nothing_and_reports_a_warning():
    obj = _make(TILESET_A, OBJECT_A)
    _activate(obj)
    obj.sprytile_gridid = -1
    before = (_mat_ids(), _display())

    assert bpy.ops.sprytile.grid_remove() == {'CANCELLED'}
    assert (_mat_ids(), _display()) == before

    level, message = sprytile_utils.UTIL_OP_SprytileGridRemove.delete_grid(bpy.context)
    assert level == 'WARNING' and "No tile grid selected" in message


def test_the_report_says_what_was_removed():
    obj = _make(TILESET_A, OBJECT_A)
    _activate(obj)
    level, message = sprytile_utils.UTIL_OP_SprytileGridRemove.delete_grid(bpy.context)
    assert level == 'INFO'
    assert TILESET_A in message and "material and image are kept" in message
