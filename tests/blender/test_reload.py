"""api.reload_core reloads the class-free add-on modules and the API keeps working."""

import importlib
import inspect
import sys
from pathlib import Path

import bpy
import pytest

PKG = "bl_ext.user_default.spyrite_tile"
api = importlib.import_module(PKG + ".api")
sprytile_core = importlib.import_module(PKG + ".sprytile_core")

FIXTURE_IMAGE = Path(__file__).resolve().parent.parent / "fixtures" / "tiles_16px.png"
PREFIX = "reload_test_"
TILESET = PREFIX + "tiles"
OBJECT = PREFIX + "obj"
EXPECTED = ["sprytile_core", "sprytile_uv", "sprytile_builder", "spyrite_spec", "spyrite_probe", "api"]


def _remove_test_data():
    for obj in [o for o in bpy.data.objects if o.name.startswith(PREFIX)]:
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in [m for m in bpy.data.meshes if m.name.startswith(PREFIX)]:
        bpy.data.meshes.remove(mesh)
    for material in [m for m in bpy.data.materials if m.name.startswith(PREFIX)]:
        bpy.data.materials.remove(material)
    importlib.import_module(PKG + ".sprytile_core").validate_grids(bpy.context.scene)


@pytest.fixture(autouse=True)
def clean_scene():
    _remove_test_data()
    yield
    _remove_test_data()


def _own_function(module):
    """A function or class defined in `module` itself; its def/class statement re-runs on reload."""
    for name, value in vars(module).items():
        if (inspect.isfunction(value) or inspect.isclass(value)) and value.__module__ == module.__name__ and name != "reload_core":
            return name
    raise AssertionError(f"{module.__name__} defines no function or class to probe")


def test_reload_core_reexecutes_every_module_and_rebinds_api():
    modules = {name: sys.modules[f"{PKG}.{name}"] for name in EXPECTED}
    probes = {}
    for name, module in modules.items():
        attr = _own_function(module)
        sentinel = object()
        setattr(module, attr, sentinel)  # a re-executed def overwrites this; a no-op reload leaves it
        probes[name] = (attr, sentinel)
    old_builder = sys.modules[PKG + ".sprytile_builder"].TileBuilder

    result = api.reload_core()
    assert result == {"reloaded": EXPECTED}

    for name, module in modules.items():
        attr, sentinel = probes[name]
        assert sys.modules[f"{PKG}.{name}"] is module
        assert getattr(module, attr) is not sentinel, f"{name} was not re-executed"
    builder = sys.modules[PKG + ".sprytile_builder"]
    uv = sys.modules[PKG + ".sprytile_uv"]
    assert builder.TileBuilder is not old_builder
    # api and builder hold the reloaded classes, not stale copies from before the reload
    assert api.TileBuilder is builder.TileBuilder
    assert api.UvDataLayers is uv.UvDataLayers
    assert builder.UvDataLayers is uv.UvDataLayers

    api.create_tileset(TILESET, str(FIXTURE_IMAGE), (16, 16))
    api.create_tile_object(OBJECT, TILESET, 16)
    report = api.place_tiles(OBJECT, TILESET, [{"cell_xy": [0, 0], "tile_xy": [1, 2], "plane": "XY"}])
    assert report["face_count"] == 1


def test_reload_resets_removed_tilesets():
    sprytile_core._removed_tilesets.add(123456789)
    api.reload_core()
    assert 123456789 not in importlib.import_module(PKG + ".sprytile_core")._removed_tilesets
