"""select_faces: contiguous and non-contiguous face selection from read-back data.

Fixture: tests/fixtures/tiles_16px.png (64x64, 4x4 tiles of 16 px); a 6x6 floor of tile A (0,0) with a 2x2
island of tile B (1,0) at cells 2..3 and a separate B cell at (5,5).
"""

import importlib
import shutil
from pathlib import Path

import bpy
import pytest

api = importlib.import_module("bl_ext.user_default.spyrite_tile.api")
sprytile_core = importlib.import_module("bl_ext.user_default.spyrite_tile.sprytile_core")

FIXTURE_IMAGE = Path(__file__).resolve().parent.parent / "fixtures" / "tiles_16px.png"
PREFIX = "select_test_"
TILESET = PREFIX + "tiles"
OBJECT = PREFIX + "obj"
A = (0, 0)
B = (1, 0)
SIDECAR = """\
spyrite_tileset: 1
tiles:
  grass: {xy: [0, 0], tags: [floor]}
  flower: {xy: [1, 0], tags: [floor, decor]}
  wall: {xy: [2, 0], tags: [wall]}
"""


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


def _board(image=FIXTURE_IMAGE):
    assert api.create_tileset(TILESET, str(image), (16, 16))["reused_material"] is None
    api.create_tile_object(OBJECT, TILESET, 16)
    api.fill_tiles(OBJECT, TILESET, (0, 0), (5, 5), A)
    api.fill_tiles(OBJECT, TILESET, (2, 2), (3, 3), B)
    api.fill_tiles(OBJECT, TILESET, (5, 5), (5, 5), B)


def _tiles_by_index():
    return {f["index"]: f["tile_xy"] for f in api.describe_tile_object(OBJECT, max_faces=1000)["faces"]}


def test_tile_selects_island_and_separate_cell():
    _board()
    result = api.select_faces(OBJECT, {"tile": list(B)})
    assert result["count"] == 5 == len(result["face_indices"])
    assert result["face_indices"] == sorted(result["face_indices"])
    assert all(t == list(B) for i, t in _tiles_by_index().items() if i in result["face_indices"])
    assert api.select_faces(OBJECT, {"tiles": [list(A), list(B)]})["count"] == 36
    assert api.select_faces(OBJECT, {})["count"] == 36


def test_connected_to_cell_floods_only_the_island():
    _board()
    island = api.select_faces(OBJECT, {"plane": "XY", "connected_to_cell": [2, 2]})
    assert island["count"] == 4
    lone = api.select_faces(OBJECT, {"plane": "XY", "connected_to_cell": [5, 5]})
    assert lone["count"] == 1
    background = api.select_faces(OBJECT, {"plane": "XY", "connected_to_cell": [0, 0]})
    assert background["count"] == 31
    anded = api.select_faces(
        OBJECT, {"plane": "XY", "connected_to_cell": [0, 0], "cell_min_xy": [0, 0], "cell_max_xy": [1, 1]}
    )
    assert anded["count"] == 4
    with pytest.raises(ValueError, match="no on-grid face at cell"):
        api.select_faces(OBJECT, {"plane": "XY", "connected_to_cell": [9, 9]})
    with pytest.raises(ValueError, match="needs where.plane"):
        api.select_faces(OBJECT, {"connected_to_cell": [0, 0]})


def test_connected_to_cell_errors_when_offsets_are_ambiguous():
    _board()
    api.fill_tiles(OBJECT, TILESET, (0, 0), (0, 0), A, plane_offset_m=1.0)
    with pytest.raises(ValueError, match="several plane offsets"):
        api.select_faces(OBJECT, {"plane": "XY", "connected_to_cell": [0, 0]})
    chosen = api.select_faces(OBJECT, {"plane": "XY", "plane_offset_m": 1.0, "connected_to_cell": [0, 0]})
    assert chosen["count"] == 1


def test_rect_plane_offset_layer_and_facing_filters():
    _board()
    api.fill_tiles(OBJECT, TILESET, (0, 0), (1, 0), (2, 0), plane="XZ", plane_offset_m=6.0)
    assert api.select_faces(OBJECT, {"plane": "XY"})["count"] == 36
    assert api.select_faces(OBJECT, {"plane": "XZ"})["count"] == 2
    assert api.select_faces(OBJECT, {"plane": "YZ"})["count"] == 0
    rect = api.select_faces(OBJECT, {"plane": "XY", "cell_min_xy": [1, 1], "cell_max_xy": [2, 3]})
    assert rect["count"] == 6
    assert api.select_faces(OBJECT, {"plane": "XZ", "plane_offset_m": 6.0})["count"] == 2
    assert api.select_faces(OBJECT, {"plane": "XZ", "plane_offset_m": 5.0})["count"] == 0
    assert api.select_faces(OBJECT, {"layer": "DECAL"})["count"] == 0
    assert api.select_faces(OBJECT, {"layer": "BASE"})["count"] == 38
    facing = api.describe_tile_object(OBJECT, max_faces=1)["faces"][0]["facing"]
    assert api.select_faces(OBJECT, {"plane": "XY", "facing": facing})["count"] == 36
    assert api.select_faces(OBJECT, {"plane": "XY", "facing": -facing})["count"] == 0


def test_decal_layer_selected():
    _board()
    api.place_tiles(OBJECT, TILESET, [{"cell_xy": [0, 0], "tile_xy": list(B), "layer": "DECAL"}])
    decal = api.select_faces(OBJECT, {"layer": "DECAL"})
    assert decal["count"] == 1


def test_validation_errors():
    _board()
    with pytest.raises(ValueError, match=r"unknown keys \['colour'\]; valid keys: \['tile', 'tiles'"):
        api.select_faces(OBJECT, {"colour": 1})
    with pytest.raises(ValueError, match="given together"):
        api.select_faces(OBJECT, {"cell_min_xy": [0, 0]})
    with pytest.raises(ValueError, match="must be >="):
        api.select_faces(OBJECT, {"cell_min_xy": [3, 3], "cell_max_xy": [0, 0]})
    with pytest.raises(ValueError, match="facing must be 1 or -1"):
        api.select_faces(OBJECT, {"facing": 0})
    with pytest.raises(ValueError, match="plane must be one of"):
        api.select_faces(OBJECT, {"plane": "AB"})
    with pytest.raises(ValueError, match="No object named"):
        api.select_faces(PREFIX + "missing", {})


def test_name_without_sidecar_errors(tmp_path):
    image = tmp_path / "plain.png"
    shutil.copy(FIXTURE_IMAGE, image)
    _board(image)
    with pytest.raises(ValueError, match="has no tile names"):
        api.select_faces(OBJECT, {"tile": "grass"})


def test_names_and_tags_use_the_sidecar(tmp_path):
    image = tmp_path / "tiles.png"
    shutil.copy(FIXTURE_IMAGE, image)
    (tmp_path / "tiles.spyrite.yaml").write_text(SIDECAR, encoding="utf-8")
    _board(image)
    assert api.select_faces(OBJECT, {"tile": "flower"})["count"] == 5
    assert api.select_faces(OBJECT, {"tiles": ["flower", [0, 0]]})["count"] == 36
    assert api.select_faces(OBJECT, {"tag": "floor"})["count"] == 36
    assert api.select_faces(OBJECT, {"tag": "decor"})["count"] == 5
    assert api.select_faces(OBJECT, {"tag": "wall"})["count"] == 0
    with pytest.raises(ValueError, match="unknown tile name 'nope'"):
        api.select_faces(OBJECT, {"tile": "nope"})


def test_paint_faces_repaints_exactly_the_selection():
    _board()
    selection = api.select_faces(OBJECT, {"tile": list(B)})["face_indices"]
    before = _tiles_by_index()
    api.paint_faces(OBJECT, TILESET, selection, (3, 3))
    after = _tiles_by_index()
    assert len(selection) == 5
    for index, tile in after.items():
        assert tile == ([3, 3] if index in selection else before[index])
    assert api.select_faces(OBJECT, {"tile": [3, 3]})["face_indices"] == selection
