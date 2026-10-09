"""Overlay objects: child tile objects whose placements are lifted off the base's plane."""

import importlib
from pathlib import Path

import bpy
import pytest

api = importlib.import_module("bl_ext.user_default.spyrite_tile.api")
sprytile_core = importlib.import_module("bl_ext.user_default.spyrite_tile.sprytile_core")

FIXTURE_IMAGE = Path(__file__).resolve().parent.parent / "fixtures" / "tiles_16px.png"
PREFIX = "overlay_test_"
TILESET = PREFIX + "tiles"
BASE = PREFIX + "base"
OVERLAY = PREFIX + "overlay"
PPU = 16
TOL = 1e-5


def _remove_test_data():
    for obj in [o for o in bpy.data.objects if o.name.startswith(PREFIX)]:
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in [m for m in bpy.data.meshes if m.name.startswith(PREFIX)]:
        bpy.data.meshes.remove(mesh)
    for material in [m for m in bpy.data.materials if m.name.startswith(PREFIX)]:
        bpy.data.materials.remove(material)
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    sprytile_core.validate_grids(bpy.context.scene)


@pytest.fixture(autouse=True)
def clean_scene():
    _remove_test_data()
    yield
    _remove_test_data()


@pytest.fixture
def pair():
    assert api.create_tileset(TILESET, str(FIXTURE_IMAGE), (16, 16))["reused_material"] is None
    api.create_tile_object(BASE, TILESET, PPU)
    report = api.create_overlay_object(OVERLAY, BASE, TILESET)
    return report


def _world_coords(name, axis):
    obj = bpy.data.objects[name]
    return sorted({round((obj.matrix_world @ v.co)[axis], 6) for v in obj.data.vertices})


def test_create_overlay_object_report_and_parenting(pair):
    assert pair["overlay_of"] == BASE and pair["lift_m"] == 0.002
    assert pair["pixels_per_unit"] == PPU
    obj = bpy.data.objects[OVERLAY]
    assert obj.parent is bpy.data.objects[BASE]
    assert obj["spyrite_overlay_of"] == BASE
    assert obj["spyrite_overlay_lift_m"] == 0.002
    assert api.describe_tile_object(OVERLAY)["overlay_of"] == BASE
    assert api.describe_tile_object(BASE)["overlay_of"] is None
    objects = {o["object_name"]: o for o in api.describe_scene()["tile_objects"]}
    assert objects[OVERLAY]["overlay_of"] == BASE and objects[BASE]["overlay_of"] is None


def test_floor_overlay_lands_lifted(pair):
    api.place_tiles(BASE, TILESET, [{"cell_xy": (0, 0), "tile_xy": (0, 0)}])
    api.place_tiles(OVERLAY, TILESET, [{"cell_xy": (0, 0), "tile_xy": (1, 0)}])
    assert _world_coords(BASE, 2) == [0.0]
    assert _world_coords(OVERLAY, 2) == pytest.approx([0.002], abs=TOL)
    face = api.describe_tile_object(OVERLAY)["faces"][0]
    assert face["plane"] == "XY" and face["plane_offset_m"] == pytest.approx(0.002, abs=TOL)
    assert face["cell_xy"] == [0, 0] and face["on_grid"] is True


def test_wall_overlay_lands_lifted_toward_viewer(pair):
    api.place_tiles(BASE, TILESET, [{"cell_xy": (0, 0), "tile_xy": (0, 0), "plane": "XZ", "plane_offset_m": 6.0}])
    api.place_tiles(OVERLAY, TILESET, [{"cell_xy": (0, 0), "tile_xy": (1, 0), "plane": "XZ", "plane_offset_m": 6.0}])
    assert _world_coords(BASE, 1) == [6.0]
    assert _world_coords(OVERLAY, 1) == pytest.approx([5.998], abs=TOL)


def test_yz_overlay_and_custom_lift(pair):
    api.create_overlay_object(OVERLAY + "2", BASE, TILESET, lift_m=0.01)
    api.place_tiles(OVERLAY + "2", TILESET, [{"cell_xy": (0, 0), "tile_xy": (1, 0), "plane": "YZ", "plane_offset_m": 3.0}])
    assert _world_coords(OVERLAY + "2", 0) == pytest.approx([3.01], abs=TOL)


def test_remove_tiles_takes_base_offset_on_overlay(pair):
    api.fill_tiles(OVERLAY, TILESET, (0, 0), (1, 1), (1, 0), plane="XZ", plane_offset_m=4.0)
    assert api.remove_tiles(OVERLAY, "XZ", 4.0, [(0, 0)]) == {"removed": 1, "face_count": 3}


def test_recreating_an_overlay_keeps_it_attached_after_the_base_moved(pair):
    api.place_tiles(BASE, TILESET, [{"cell_xy": (0, 0), "tile_xy": (0, 0)}])
    api.place_tiles(OVERLAY, TILESET, [{"cell_xy": (0, 0), "tile_xy": (1, 0)}])
    bpy.data.objects[BASE].location.z = 1.0
    bpy.context.view_layer.update()
    assert _world_coords(OVERLAY, 2) == pytest.approx([1.002], abs=TOL)
    report = api.create_overlay_object(OVERLAY, BASE, TILESET)
    assert report["overlay_of"] == BASE
    bpy.context.view_layer.update()
    assert bpy.data.objects[OVERLAY].parent is bpy.data.objects[BASE]
    assert _world_coords(BASE, 2) == pytest.approx([1.0], abs=TOL)
    assert _world_coords(OVERLAY, 2) == pytest.approx([1.002], abs=TOL)


def test_overlay_errors(pair):
    with pytest.raises(ValueError, match="lift_m"):
        api.create_overlay_object(PREFIX + "x", BASE, TILESET, lift_m=0)
    with pytest.raises(ValueError, match="different from its base"):
        api.create_overlay_object(BASE, BASE, TILESET)


def test_fill_pattern_on_overlay_lands_lifted(pair):
    api.fill_pattern(
        OVERLAY,
        TILESET,
        {"kind": "random", "tiles": [[0, 0], [1, 0]], "seed": 7},
        cell_min_xy=(0, 0),
        cell_max_xy=(2, 2),
    )
    assert len(bpy.data.objects[OVERLAY].data.polygons) == 9
    assert _world_coords(OVERLAY, 2) == pytest.approx([0.002], abs=TOL)
    assert {f["plane_offset_m"] for f in api.describe_tile_object(OVERLAY)["faces"]} == {0.002}


def test_verify_base_passes_with_overlay_present_and_hidden(pair, tmp_path):
    api.fill_tiles(BASE, TILESET, (0, 0), (3, 3), (0, 0))
    api.fill_tiles(OVERLAY, TILESET, (0, 0), (3, 3), (1, 0))
    overlay = bpy.data.objects[OVERLAY]
    for hidden in (False, True):
        overlay.hide_render = hidden
        overlay.hide_viewport = hidden
        result = api.verify_tile_object(BASE, view="top", evidence_dir=str(tmp_path / f"base_{hidden}"))
        assert result["ok"] is True and result["measured"] == 16, result
    # the overlay itself verifies too: it is its own tile object, lifted 2 mm off the base
    overlay.hide_render = overlay.hide_viewport = False
    assert api.verify_tile_object(OVERLAY, view="top", evidence_dir=str(tmp_path / "overlay"))["ok"] is True
