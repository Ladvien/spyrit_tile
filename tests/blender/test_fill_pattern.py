"""fill_pattern: deterministic random, stamp and autotile fills.

Fixture: tests/fixtures/tiles_16px.png (64x64, 4x4 tiles of 16 px).
"""

import importlib
from pathlib import Path

import bpy
import pytest

api = importlib.import_module("bl_ext.user_default.spyrite_tile.api")
sprytile_utils = importlib.import_module("bl_ext.user_default.spyrite_tile.sprytile_utils")

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
    sprytile_utils.validate_grids(bpy.context.scene)


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
