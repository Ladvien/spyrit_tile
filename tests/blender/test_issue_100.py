"""#100: tileset_load / tileset_new must work without a window event, and not raise with no active object."""

import importlib
from pathlib import Path

import bpy
import pytest

sprytile_utils = importlib.import_module("bl_ext.user_default.spyrite_tile.sprytile_utils")
FIXTURE = str(Path(__file__).resolve().parent.parent / "fixtures" / "tiles_16px.png")


@pytest.fixture(autouse=True)
def clean_up():
    yield
    for ob in [o for o in bpy.data.objects if o.name.startswith("i100_")]:
        bpy.data.objects.remove(ob, do_unlink=True)
    for coll, prefix in ((bpy.data.meshes, "i100_"), (bpy.data.materials, "tiles_16px"),
                         (bpy.data.images, "tiles_16px")):
        for block in [b for b in coll if b.name.startswith(prefix)]:
            coll.remove(block)

    sprytile_utils.validate_grids(bpy.context.scene)

def _object(name):
    me = bpy.data.meshes.new(name)
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    me.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [], [(0, 1, 2, 3)])
    bpy.context.view_layer.objects.active = ob
    ob.select_set(True)
    return ob


def _check_loaded(ob):
    assert len(ob.material_slots) == 1
    mat = ob.material_slots[0].material
    assert mat.name.startswith("tiles_16px")
    images = [n.image for n in mat.node_tree.nodes if n.type == "TEX_IMAGE"]
    assert len(images) == 1 and images[0] is not None
    assert images[0].size[0] == 64
    assert len(bpy.context.scene.sprytile_mats) >= 1


def test_tileset_load_on_material_less_object():
    ob = _object("i100_load")
    assert len(ob.material_slots) == 0
    assert bpy.ops.sprytile.tileset_load(filepath=FIXTURE) == {"FINISHED"}
    _check_loaded(ob)


def test_tileset_new_on_material_less_object():
    ob = _object("i100_new")
    assert len(ob.material_slots) == 0
    assert bpy.ops.sprytile.tileset_new(filepath=FIXTURE) == {"FINISHED"}
    _check_loaded(ob)


def test_tileset_load_without_active_object():
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    assert bpy.context.object is None
    assert not bpy.ops.sprytile.tileset_load.poll()
    assert not bpy.ops.sprytile.tileset_new.poll()
