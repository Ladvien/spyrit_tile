"""api.reload_core reloads the class-free add-on modules and the API keeps working."""

import importlib
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


def test_reload_core_names_and_place_tiles_still_works():
    result = api.reload_core()
    assert result == {"reloaded": EXPECTED}
    # module objects are reloaded in place, so fresh lookups see working code
    fresh = importlib.import_module(PKG + ".api")
    fresh.create_tileset(TILESET, str(FIXTURE_IMAGE), (16, 16))
    fresh.create_tile_object(OBJECT, TILESET, 16)
    report = fresh.place_tiles(
        OBJECT, TILESET, [{"cell_xy": [0, 0], "tile_xy": [1, 2], "plane": "XY"}]
    )
    assert report["face_count"] == 1


def test_reload_resets_removed_tilesets():
    sprytile_core._removed_tilesets.add(123456789)
    api.reload_core()
    assert 123456789 not in importlib.import_module(PKG + ".sprytile_core")._removed_tilesets
