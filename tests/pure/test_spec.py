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
        (lambda s: s["objects"]["room"].update(patterns=[]), "objects.room: unknown keys ['patterns']; valid keys are ['tileset', "),
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
