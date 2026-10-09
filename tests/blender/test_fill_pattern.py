"""fill_pattern: deterministic random, stamp and autotile fills.

Fixture: tests/fixtures/tiles_16px.png (64x64, 4x4 tiles of 16 px).
"""

import importlib
import random
import shutil
from pathlib import Path

import bpy
import pytest

api = importlib.import_module("bl_ext.user_default.spyrite_tile.api")
sprytile_core = importlib.import_module("bl_ext.user_default.spyrite_tile.sprytile_core")

FIXTURE_IMAGE = Path(__file__).resolve().parent.parent / "fixtures" / "tiles_16px.png"
PREFIX = "pattern_test_"
TILESET = PREFIX + "tiles"
OBJECT = PREFIX + "obj"
AUTOTILE = {str(k): [k % 4, k // 4] for k in range(16)}


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
def board():
    api.create_tileset(TILESET, str(FIXTURE_IMAGE), (16, 16))
    api.create_tile_object(OBJECT, TILESET, 16)


def _fill(pattern, **kwargs):
    return api.fill_pattern(OBJECT, TILESET, pattern, **kwargs)


def _by_cell(report):
    return {tuple(a["cell_xy"]): tuple(a["tile_xy"]) for a in report["assignments"]}


def _face_tiles():
    faces = api.describe_tile_object(OBJECT, max_faces=1000)["faces"]
    return {tuple(f["cell_xy"]): tuple(f["tile_xy"]) for f in faces}


def test_random_is_deterministic(board):
    pattern = {"kind": "random", "tiles": [[0, 0], [1, 0], [2, 0]], "seed": 7}
    first = _fill(pattern, cell_min_xy=[0, 0], cell_max_xy=[5, 5])
    assert first["cells"] == 36 and first["built"] == 36 and first["face_count"] == 36
    second = _fill(pattern, cell_min_xy=[0, 0], cell_max_xy=[5, 5])
    assert second["remapped"] == 36
    assert first["assignments"] == second["assignments"]
    assert _face_tiles() == _by_cell(first)
    assert len(set(_by_cell(first).values())) > 1


def test_random_follows_the_documented_algorithm(board):
    """One Random(seed), one choices(tiles, weights) draw per cell, y outer / x inner ascending."""
    tiles = [[0, 0], [1, 0], [2, 0]]
    weights = [1, 2, 3]
    cells = [(x, y) for y in range(6) for x in range(6)]
    rng = random.Random(7)
    expected = [rng.choices(tiles, weights)[0] for _ in cells]
    report = _fill({"kind": "random", "tiles": tiles, "weights": weights, "seed": 7}, cell_min_xy=[0, 0], cell_max_xy=[5, 5])
    assert [a["cell_xy"] for a in report["assignments"]] == [list(c) for c in cells]
    assert [a["tile_xy"] for a in report["assignments"]] == expected


def test_random_weights_bias(board):
    pattern = {"kind": "random", "tiles": [[0, 0], [1, 0]], "weights": [1, 0.0001], "seed": 3}
    report = _fill(pattern, cell_min_xy=[0, 0], cell_max_xy=[19, 19])
    assert report["cells"] == 400
    first = sum(1 for a in report["assignments"] if a["tile_xy"] == [0, 0])
    assert first >= 380


def test_assignments_truncated_above_500(board):
    report = _fill({"kind": "stamp", "rows": [[[0, 0]]]}, cell_min_xy=[0, 0], cell_max_xy=[24, 20])
    assert report["cells"] == 525
    assert report["assignments"] is None and report["truncated"] is True
    assert report["face_count"] == 525


def test_stamp_tiles_exactly_with_top_row_first(board):
    rows = [[[0, 0], [1, 0]], [[0, 1], [1, 1]]]
    report = _fill({"kind": "stamp", "rows": rows}, cell_min_xy=[2, 3], cell_max_xy=[5, 6])
    cells = _by_cell(report)
    for (x, y), tile in cells.items():
        assert tile == (( x - 2) % 2, (6 - y) % 2)
    assert cells[(2, 6)] == (0, 0) and cells[(3, 6)] == (1, 0)
    assert cells[(2, 5)] == (0, 1) and cells[(4, 4)] == (0, 0)
    assert _face_tiles() == cells


def test_stamp_with_explicit_cells_and_object_form(board):
    pattern = {"kind": "stamp", "rows": [[{"tile": [1, 1], "rotation_deg": 90, "flip_x": True}]]}
    report = _fill(pattern, cells=[[4, 4], [0, 0], [4, 4]])
    assert report["cells"] == 2 and [a["cell_xy"] for a in report["assignments"]] == [[0, 0], [4, 4]]
    faces = api.describe_tile_object(OBJECT)["faces"]
    assert all(f["rotation_deg"] == 90 and f["flip_x"] and f["tile_xy"] == [1, 1] for f in faces)


def test_autotile_block(board):
    report = _fill({"kind": "autotile", "mask": "edges4", "tiles": AUTOTILE}, cell_min_xy=[0, 0], cell_max_xy=[2, 2])
    cells = _by_cell(report)
    key = {tile: int(k) for k, tile in ((k, tuple(v)) for k, v in AUTOTILE.items())}
    assert key[cells[(1, 1)]] == 15
    assert key[cells[(0, 2)]] == 2 + 4  # top-left: E + S
    assert key[cells[(2, 2)]] == 4 + 8  # top-right: S + W
    assert key[cells[(0, 0)]] == 1 + 2  # bottom-left: N + E
    assert key[cells[(1, 0)]] == 1 + 2 + 8
    assert _face_tiles() == cells


def test_autotile_explicit_cells_respect_the_set(board):
    report = _fill({"kind": "autotile", "mask": "edges4", "tiles": AUTOTILE}, cells=[[0, 0], [1, 0], [5, 5]])
    key = {tuple(v): int(k) for k, v in AUTOTILE.items()}
    cells = _by_cell(report)
    assert key[cells[(0, 0)]] == 2 and key[cells[(1, 0)]] == 8 and key[cells[(5, 5)]] == 0


def test_autotile_missing_keys_error(board):
    tiles = {k: v for k, v in AUTOTILE.items() if k not in ("3", "9")}
    with pytest.raises(ValueError, match=r"missing autotile keys \['3', '9'\]"):
        _fill({"kind": "autotile", "mask": "edges4", "tiles": tiles}, cell_min_xy=[0, 0], cell_max_xy=[1, 1])
    assert api.describe_tile_object(OBJECT)["face_count"] == 0


@pytest.mark.parametrize(
    "pattern, kwargs, message",
    [
        ({"kind": "nope"}, {"cell_min_xy": [0, 0], "cell_max_xy": [1, 1]}, "pattern.kind must be one of"),
        ({"kind": "random", "tiles": [[0, 0]]}, {"cell_min_xy": [0, 0], "cell_max_xy": [1, 1]}, "pattern.seed is required"),
        ({"kind": "random", "tiles": [[0, 0]], "weights": [0], "seed": 1}, {"cell_min_xy": [0, 0], "cell_max_xy": [1, 1]}, "must all be > 0"),
        ({"kind": "random", "tiles": [[0, 0]], "weights": [1, 1], "seed": 1}, {"cell_min_xy": [0, 0], "cell_max_xy": [1, 1]}, "pattern.weights has 2"),
        ({"kind": "stamp", "rows": [[[0, 0]], [[0, 0], [1, 0]]]}, {"cell_min_xy": [0, 0], "cell_max_xy": [1, 1]}, "rectangular"),
        ({"kind": "stamp", "rows": [[[0, 0]]]}, {}, "exactly one of"),
        ({"kind": "stamp", "rows": [[[0, 0]]]}, {"cell_min_xy": [0, 0], "cell_max_xy": [1, 1], "cells": [[0, 0]]}, "exactly one of"),
        ({"kind": "stamp", "rows": [[[9, 9]]]}, {"cell_min_xy": [0, 0], "cell_max_xy": [1, 1]}, "outside"),
    ],
)
def test_errors_leave_the_object_untouched(board, pattern, kwargs, message):
    with pytest.raises(ValueError, match=message):
        _fill(pattern, **kwargs)
    assert api.describe_tile_object(OBJECT)["face_count"] == 0


def test_unused_autotile_entry_is_still_validated(board):
    tiles = dict(AUTOTILE, **{"15": "lava"})  # cells (0,0)-(2,0) only ever need keys 2, 10 and 8
    with pytest.raises(ValueError, match=r"pattern\.tiles\['15'\]: unknown tile name 'lava'"):
        _fill({"kind": "autotile", "mask": "edges4", "tiles": tiles}, cell_min_xy=[0, 0], cell_max_xy=[2, 0])
    assert api.describe_tile_object(OBJECT)["face_count"] == 0


def test_unreachable_random_tile_is_still_validated(board):
    pattern = {"kind": "random", "tiles": [[0, 0], [99, 99]], "weights": [1, 1e-9], "seed": 1}
    with pytest.raises(ValueError, match=r"pattern\.tiles\[1\]"):
        _fill(pattern, cell_min_xy=[0, 0], cell_max_xy=[0, 0])
    assert api.describe_tile_object(OBJECT)["face_count"] == 0


def test_bad_stamp_tile_names_its_position(board):
    pattern = {"kind": "stamp", "rows": [["grass", "lava"]]}
    with pytest.raises(ValueError, match=r"pattern\.rows\[0\]\[1\]: unknown tile name 'lava'"):
        _fill(pattern, cells=[[0, 0]])  # the single cell only ever shows rows[0][0]
    bad = {"kind": "stamp", "rows": [["grass", {"tile": "stone", "rotation_deg": 45}]]}
    with pytest.raises(ValueError, match=r"pattern\.rows\[0\]\[1\]: rotation_deg must be one of"):
        _fill(bad, cells=[[0, 0]])
    assert api.describe_tile_object(OBJECT)["face_count"] == 0


def test_plane_rule_of_an_unused_pattern_tile_is_enforced(tmp_path):
    image = tmp_path / "tiles.png"
    shutil.copy(FIXTURE_IMAGE, image)
    (tmp_path / "tiles.spyrite.yaml").write_text(
        "spyrite_tileset: 1\ntiles:\n  grass: {xy: [0, 0]}\n  wall: {xy: [2, 0], planes: [XZ]}\n", encoding="utf-8"
    )
    api.create_tileset(TILESET, str(image), (16, 16))
    api.create_tile_object(OBJECT, TILESET, 16)
    with pytest.raises(ValueError, match=r"pattern\.tiles\[1\]: tile 'wall' is not allowed on plane XY"):
        _fill({"kind": "random", "tiles": ["grass", "wall"], "weights": [1, 1e-9], "seed": 1}, cells=[[0, 0]])


SPEC = f"""\
spyrite_spec: 1
pixels_per_unit: 16
tilesets:
  {TILESET}:
    image: ./tiles.png
    tile_size_px: [16, 16]
objects:
  {OBJECT}:
    tileset: {TILESET}
    fills:
      - {{plane: XY, cells: [[0, 0], [0, 0]], tile: grass}}
    patterns:
      - kind: random
        tiles: [grass, stone]
        seed: 7
        cell_min_xy: [0, 0]
        cell_max_xy: [3, 3]
      - kind: stamp
        rows:
          - [grass, {{tile: stone, rotation_deg: 90}}]
          - [water, grass]
        plane: XZ
        plane_offset_m: 2
        cells: [[0, 0], [1, 0], [0, 1], [1, 1]]
"""


def _spec_file(tmp_path, text):
    shutil.copy(FIXTURE_IMAGE, tmp_path / "tiles.png")
    shutil.copy(FIXTURE_IMAGE.with_name("tiles_16px.spyrite.yaml"), tmp_path / "tiles.spyrite.yaml")
    path = tmp_path / "scene.spyrite.yaml"
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_spec_patterns_build_after_tiles(tmp_path):
    report = api.build_spec(_spec_file(tmp_path, SPEC))
    assert report["objects"][0]["face_count"] == 16 + 4
    faces = api.describe_tile_object(OBJECT, max_faces=100)["faces"]
    floor = {tuple(f["cell_xy"]): f["tile"] for f in faces if f["plane"] == "XY"}
    again = {}
    rng_pattern = {"kind": "random", "tiles": ["grass", "stone"], "seed": 7}
    check = api.fill_pattern(OBJECT, TILESET, rng_pattern, cell_min_xy=[0, 0], cell_max_xy=[3, 3])
    for a in check["assignments"]:
        again[tuple(a["cell_xy"])] = a["tile_xy"]
    assert check["remapped"] == 16 and check["built"] == 0
    assert {k: [0, 0] if v == "grass" else [1, 0] for k, v in floor.items()} == {k: v for k, v in again.items()}
    wall = {tuple(f["cell_xy"]): f for f in faces if f["plane"] == "XZ"}
    assert wall[(0, 1)]["tile"] == "grass" and wall[(1, 1)]["tile"] == "stone" and wall[(1, 1)]["rotation_deg"] == 90
    assert wall[(0, 0)]["tile"] == "water" and wall[(1, 0)]["tile"] == "grass"
    assert all(f["plane_offset_m"] == 2.0 for f in wall.values())


def test_spec_pattern_errors_name_the_object(tmp_path):
    bad = SPEC.replace("seed: 7", "seed: 7\n        weights: [1]")
    with pytest.raises(ValueError, match=rf"objects.{OBJECT}.patterns\[0\].weights: must be a list of 2"):
        api.build_spec(_spec_file(tmp_path, bad))
    bad = SPEC.replace("[grass, stone]", "[grass, lava]")
    with pytest.raises(ValueError, match=rf"objects.{OBJECT}.patterns\[0\]\.tiles\[1\]: unknown tile name .lava."):
        api.build_spec(_spec_file(tmp_path, bad))
    assert bpy.data.objects.get(OBJECT) is None
