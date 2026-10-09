"""spyrite_spec: scene-spec parsing and validation (pure Python, no bpy)."""

import copy
import importlib
import sys
import types
from pathlib import Path

import pytest

ADDON_DIR = Path(__file__).resolve().parents[2] / "addon" / "spyrite_tile"
FIXTURE_SPEC = Path(__file__).resolve().parents[1] / "fixtures" / "room.spyrite.yaml"


def _load_spyrite_spec():
    # The add-on's __init__ imports bpy, so mount the directory under a stub package instead.
    package = types.ModuleType("_spyrite_addon")
    package.__path__ = [str(ADDON_DIR)]
    sys.modules.setdefault("_spyrite_addon", package)
    return importlib.import_module("_spyrite_addon.spyrite_spec")


spec_module = _load_spyrite_spec()
SpecError = spec_module.SpecError

VALID = {
    "spyrite_spec": 1,
    "pixels_per_unit": 16,
    "tilesets": {"terrain": {"image": "./tiles.png", "tile_size_px": [16, 16]}},
    "objects": {
        "room": {
            "tileset": "terrain",
            "fills": [{"plane": "XY", "cells": [[0, 0], [5, 5]], "tile": "grass"}],
            "tiles": [
                {"plane": "XZ", "plane_offset_m": 6, "cell": [0, 0], "tile": [2, 0], "rotation_deg": 90, "flip_x": True}
            ],
        }
    },
}


def _autotile(extra=False):
    tiles = {str(k): "grass" for k in range(16)}
    if extra:
        tiles["16"] = "grass"
    return tiles


def _pattern(spec, **overrides):
    """Install (once) a valid random pattern on the room, apply overrides, and return it for mutation."""
    patterns = spec["objects"]["room"].setdefault("patterns", [])
    if not patterns:
        patterns.append({"kind": "random", "tiles": ["grass", "stone"], "seed": 1, "cells": [[0, 0]]})
    for key, value in overrides.items():
        patterns[0][key] = value
    if overrides.get("kind") in ("stamp", "autotile"):
        patterns[0].pop("tiles", None) if overrides["kind"] == "stamp" else None
        patterns[0].pop("seed", None)
    return patterns[0]


def _broken(mutate):
    spec = copy.deepcopy(VALID)
    mutate(spec)
    return spec


def _message(spec):
    with pytest.raises(SpecError) as error:
        spec_module.validate_spec(spec)
    return str(error.value)


def test_valid_spec_is_normalised_with_defaults():
    normal = spec_module.validate_spec(VALID)
    assert normal["tilesets"]["terrain"] == {
        "image": "./tiles.png", "tile_size_px": [16, 16], "padding_px": [0, 0], "margin_px": [0, 0, 0, 0],
    }
    room = normal["objects"]["room"]
    assert room["pixels_per_unit"] == 16 and room["clear"] is False
    assert room["fills"][0] == {
        "plane": "XY", "plane_offset_m": 0.0, "cell_min_xy": [0, 0], "cell_max_xy": [5, 5], "tile": "grass",
        "rotation_deg": 0, "flip_x": False, "flip_y": False, "layer": "BASE",
    }
    assert room["tiles"][0] == {
        "plane": "XZ", "plane_offset_m": 6.0, "cell_xy": [0, 0], "tile": [2, 0], "tile_span": [1, 1],
        "rotation_deg": 90, "flip_x": True, "flip_y": False, "layer": "BASE",
    }


def test_fixture_spec_validates():
    normal = spec_module.validate_spec(spec_module.load_spec(FIXTURE_SPEC.read_text(encoding="utf-8")))
    assert list(normal["objects"]) == ["room"] and len(normal["objects"]["room"]["fills"]) == 3


def test_dump_round_trips_and_keeps_key_order():
    text = spec_module.dump_spec(VALID)
    assert text.startswith("spyrite_spec: 1\npixels_per_unit: 16\n")
    assert spec_module.load_spec(text) == VALID


def test_load_spec_rejects_non_mappings_and_bad_yaml():
    with pytest.raises(SpecError, match=r"^spyrite_spec: the file must be a mapping"):
        spec_module.load_spec("- 1\n- 2\n")
    with pytest.raises(SpecError, match=r"^spyrite_spec: invalid YAML"):
        spec_module.load_spec("a: [1, 2\n")


@pytest.mark.parametrize("version", [None, 2, True, "1"])
def test_missing_or_wrong_version(version):
    def mutate(spec):
        if version is None:
            del spec["spyrite_spec"]
        else:
            spec["spyrite_spec"] = version

    assert _message(_broken(mutate)) == "spyrite_spec: must be 1"


@pytest.mark.parametrize(
    ("mutate", "prefix"),
    [
        (lambda s: s.update(extra=1), "spec: unknown keys ['extra']; valid keys are ['spyrite_spec', "),
        (lambda s: s["tilesets"]["terrain"].update(colour=1), "tilesets.terrain: unknown keys ['colour']; valid keys are ['image', "),
        (lambda s: s["objects"]["room"].update(bogus=[]), "objects.room: unknown keys ['bogus']; valid keys are ['tileset', "),
        (lambda s: s["objects"]["room"]["fills"][0].update(cell=[0, 0]), "objects.room.fills[0]: unknown keys ['cell']; valid keys are ['plane', "),
        (lambda s: s["objects"]["room"]["tiles"][0].update(cells=[0, 0]), "objects.room.tiles[0]: unknown keys ['cells']; valid keys are ['plane', "),
        (lambda s: s["tilesets"]["terrain"].pop("image"), "tilesets.terrain.image: required"),
        (lambda s: s["tilesets"]["terrain"].update(tile_size_px=[16, 0]), "tilesets.terrain.tile_size_px: must be a list of 2 positive integers"),
        (lambda s: s["tilesets"]["terrain"].update(margin_px=[0, 0]), "tilesets.terrain.margin_px: must be a list of 4 non-negative integers"),
        (lambda s: s["objects"]["room"].update(tileset="lava"), "objects.room.tileset: unknown tileset 'lava'; declared tilesets are ['terrain']"),
        (lambda s: s["objects"]["room"].pop("tileset"), "objects.room.tileset: required"),
        (lambda s: s.pop("pixels_per_unit"), "objects.room.pixels_per_unit: required"),
        (lambda s: s.update(pixels_per_unit=0), "pixels_per_unit: must be a positive integer"),
        (lambda s: s["objects"]["room"].update(clear="yes"), "objects.room.clear: must be true or false"),
        (lambda s: s["objects"]["room"].update(tiles={"a": 1}), "objects.room.tiles: must be a list of mappings"),
        (lambda s: s["objects"]["room"]["tiles"][0].update(tile="no spaces"), "objects.room.tiles[0].tile: tile names may only contain"),
        (lambda s: s["objects"]["room"]["tiles"][0].update(tile=[1]), "objects.room.tiles[0].tile: must be a list of 2 non-negative integers"),
        (lambda s: s["objects"]["room"]["tiles"][0].update(tile=3), "objects.room.tiles[0].tile: must be a tile name or [column, row]"),
        (lambda s: s["objects"]["room"]["tiles"][0].update(rotation_deg=45), "objects.room.tiles[0].rotation_deg: must be one of [0, 90, 180, 270]"),
        (lambda s: s["objects"]["room"]["tiles"][0].update(layer="TOP"), "objects.room.tiles[0].layer: must be one of ['BASE', 'DECAL']"),
        (lambda s: s["objects"]["room"]["tiles"][0].update(plane="AB"), "objects.room.tiles[0].plane: must be one of ['XY', 'XZ', 'YZ']"),
        (lambda s: s["objects"]["room"]["tiles"][0].update(flip_y=1), "objects.room.tiles[0].flip_y: must be true or false"),
        (lambda s: s["objects"]["room"]["tiles"][0].update(plane_offset_m="high"), "objects.room.tiles[0].plane_offset_m: must be a finite number"),
        (lambda s: s["objects"]["room"]["tiles"][0].update(tile_span=[0, 1]), "objects.room.tiles[0].tile_span: must be a list of 2 positive integers"),
        (lambda s: s["objects"]["room"]["tiles"][0].pop("cell"), "objects.room.tiles[0].cell: required"),
        (lambda s: s["objects"]["room"]["fills"][0].pop("tile"), "objects.room.fills[0].tile: required"),
        (lambda s: s["objects"]["room"]["fills"][0].update(cells=[[3, 3], [1, 1]]), "objects.room.fills[0].cells: the max corner [1, 1] must be >= the min corner [3, 3]"),
        (lambda s: s["objects"]["room"]["fills"][0].update(cells=[[0, 0]]), "objects.room.fills[0].cells: must be [[min_x, min_y], [max_x, max_y]]"),
        (lambda s: s["objects"]["room"].update(patterns={"a": 1}), "objects.room.patterns: must be a list of mappings"),
        (lambda s: _pattern(s, kind="nope"), "objects.room.patterns[0].kind: must be one of ['autotile', 'random', 'stamp']"),
        (lambda s: _pattern(s, bogus=1), "objects.room.patterns[0]: unknown keys ['bogus']; valid keys are ['kind', 'tiles', 'weights', 'seed', 'plane', "),
        (lambda s: _pattern(s).pop("seed"), "objects.room.patterns[0].seed: required, an integer"),
        (lambda s: _pattern(s).update(seed=True), "objects.room.patterns[0].seed: required, an integer"),
        (lambda s: _pattern(s).update(tiles=[]), "objects.room.patterns[0].tiles: must be a non-empty list of tiles"),
        (lambda s: _pattern(s).update(tiles=["grass", 3]), "objects.room.patterns[0].tiles[1]: must be a tile name or [column, row]"),
        (lambda s: _pattern(s).update(tiles=[{"tile": "grass", "x": 1}]), "objects.room.patterns[0].tiles[0]: unknown keys ['x']"),
        (lambda s: _pattern(s).update(tiles=[{"rotation_deg": 90}]), "objects.room.patterns[0].tiles[0].tile: required"),
        (lambda s: _pattern(s).update(tiles=[{"tile": "grass", "rotation_deg": 45}]), "objects.room.patterns[0].tiles[0].rotation_deg: must be one of"),
        (lambda s: _pattern(s).update(weights=[1]), "objects.room.patterns[0].weights: must be a list of 2 numbers > 0"),
        (lambda s: _pattern(s).update(weights=[1, 0]), "objects.room.patterns[0].weights: must be a list of 2 numbers > 0"),
        (lambda s: _pattern(s).pop("cells"), "objects.room.patterns[0]: needs exactly one of cells"),
        (lambda s: _pattern(s).update(cell_min_xy=[0, 0], cell_max_xy=[1, 1]), "objects.room.patterns[0]: needs exactly one of cells"),
        (lambda s: _pattern(s).update(cells=[]), "objects.room.patterns[0].cells: must be a non-empty list of [x, y]"),
        (lambda s: _pattern(s).update(cells=[[0]]), "objects.room.patterns[0].cells[0]: must be a list of 2 whole-number integers"),
        (lambda s: _pattern(s, kind="stamp", rows=[["grass"], ["a", "b"]]), "objects.room.patterns[0].rows: must be rectangular"),
        (lambda s: _pattern(s, kind="stamp", rows=[]), "objects.room.patterns[0].rows: must be a non-empty list"),
        (lambda s: _pattern(s, kind="autotile", mask="corners"), "objects.room.patterns[0].mask: must be 'edges4'"),
        (lambda s: _pattern(s, kind="autotile", mask="edges4", tiles={"0": "grass"}), "objects.room.patterns[0].tiles: missing autotile keys ['1', '2', '3',"),
        (lambda s: _pattern(s, kind="autotile", mask="edges4", tiles=_autotile(extra=True)), "objects.room.patterns[0].tiles: unknown keys ['16']"),
        (lambda s: (_pattern(s).pop("cells"), _pattern(s).update(cell_min_xy=[2, 2], cell_max_xy=[1, 1])), "objects.room.patterns[0].cell_max_xy: the max corner [1, 1] must be >="),
        (lambda s: _pattern(s).update(layer="TOP"), "objects.room.patterns[0].layer: must be one of"),
    ],
)
def test_error_messages_start_with_the_dotted_path(mutate, prefix):
    assert _message(_broken(mutate)).startswith(prefix)


def test_unknown_key_message_lists_every_valid_key():
    spec = _broken(lambda s: s["objects"]["room"]["tiles"][0].update(bogus=1))
    assert _message(spec) == (
        "objects.room.tiles[0]: unknown keys ['bogus']; valid keys are ['plane', 'plane_offset_m', 'cell', "
        "'tile', 'tile_span', 'rotation_deg', 'flip_x', 'flip_y', 'layer']"
    )


def test_patterns_normalise():
    spec = copy.deepcopy(VALID)
    spec["objects"]["room"]["patterns"] = [
        {"kind": "random", "tiles": ["grass", [1, 0], {"tile": "stone", "rotation_deg": 90}], "weights": [1, 2, 3], "seed": 7,
         "cell_min_xy": [0, 0], "cell_max_xy": [2, 1], "plane": "XZ", "plane_offset_m": 2, "layer": "DECAL"},
        {"kind": "stamp", "rows": [["grass", "stone"]], "cells": [[1, 1], [0, 0]]},
        {"kind": "autotile", "mask": "edges4", "tiles": _autotile(), "cell_min_xy": [0, 0], "cell_max_xy": [2, 2]},
    ]
    first, second, third = spec_module.validate_spec(spec)["objects"]["room"]["patterns"]
    assert first["pattern"] == {
        "kind": "random",
        "tiles": [
            "grass",
            [1, 0],
            {"tile": "stone", "rotation_deg": 90, "flip_x": False, "flip_y": False},
        ],
        "weights": [1.0, 2.0, 3.0],
        "seed": 7,
    }
    assert (first["plane"], first["plane_offset_m"], first["layer"]) == ("XZ", 2.0, "DECAL")
    assert first["cell_min_xy"] == [0, 0] and first["cell_max_xy"] == [2, 1] and "cells" not in first
    assert second["cells"] == [[1, 1], [0, 0]] and second["plane"] == "XY" and second["layer"] == "BASE"
    assert third["pattern"]["mask"] == "edges4" and sorted(third["pattern"]["tiles"], key=int) == [str(k) for k in range(16)]
    assert spec_module.validate_spec(VALID)["objects"]["room"]["patterns"] == []
