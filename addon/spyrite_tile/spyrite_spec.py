"""Pure-Python (no bpy) parsing of Spyrite Tile's YAML files.

Two file kinds: the scene spec (``load_spec`` / ``validate_spec`` / ``dump_spec``, built by
``api.build_spec``) and the tile-name sidecar that sits next to a tileset image
(``tiles.png`` -> ``tiles.spyrite.yaml``)::

    spyrite_tileset: 1
    tiles:
      grass: {xy: [0, 0], tags: [floor]}
      wall_top: {xy: [2, 0], planes: [XZ, YZ], tags: [wall]}
"""

import re

from ._vendor import yaml

SIDECAR_SUFFIX = ".spyrite.yaml"
PLANE_NAMES = ("XY", "XZ", "YZ")
TILE_NAME_PATTERN = re.compile(r"[A-Za-z0-9_]+")
_TILE_KEYS = ("xy", "planes", "tags")


class SpecError(ValueError):
    """A malformed Spyrite YAML file; the message starts with the dotted path of the bad value."""


def sidecar_path(image_path):
    """Path of the tile-name sidecar for an image path: the suffix is replaced by ``.spyrite.yaml``."""
    stem, _ = _split_suffix(str(image_path))
    return stem + SIDECAR_SUFFIX


def _split_suffix(path):
    slash = max(path.rfind("/"), path.rfind("\\"))
    dot = path.rfind(".")
    if dot > slash + 1:
        return path[:dot], path[dot:]
    return path, ""


def load_tile_names(text):
    """Parse sidecar text into ``{name: {"xy": [col, row], "planes": [...] | None, "tags": [...]}}``.

    ``planes`` is None when the tile is allowed on any plane. Whether ``xy`` lies inside the tileset is
    checked by the caller, which knows the tileset size.
    """
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise SpecError(f"spyrite_tileset: invalid YAML: {error}") from None
    if not isinstance(data, dict):
        raise SpecError("spyrite_tileset: the file must be a mapping with 'spyrite_tileset: 1' and 'tiles'")
    unknown = set(data) - {"spyrite_tileset", "tiles"}
    if unknown:
        raise SpecError(f"spyrite_tileset: unknown keys {sorted(map(str, unknown))}; valid keys are ['spyrite_tileset', 'tiles']")
    version = data.get("spyrite_tileset")
    if isinstance(version, bool) or version != 1:
        raise SpecError("spyrite_tileset: must be 1")
    tiles = data.get("tiles")
    if not isinstance(tiles, dict) or not tiles:
        raise SpecError("tiles: must be a non-empty mapping of tile name to {xy, planes, tags}")
    names = {}
    for name, entry in tiles.items():
        path = f"tiles.{name}"
        if not isinstance(name, str) or not TILE_NAME_PATTERN.fullmatch(name):
            raise SpecError(f"{path}: tile names may only contain letters, digits and '_', got {name!r}")
        if not isinstance(entry, dict):
            raise SpecError(f"{path}: must be a mapping with 'xy' (and optionally 'planes', 'tags'), got {entry!r}")
        unknown = set(entry) - set(_TILE_KEYS)
        if unknown:
            raise SpecError(f"{path}: unknown keys {sorted(map(str, unknown))}; valid keys are {list(_TILE_KEYS)}")
        if "xy" not in entry:
            raise SpecError(f"{path}.xy: required, a [column, row] pair")
        xy = entry["xy"]
        if (
            not isinstance(xy, (list, tuple))
            or len(xy) != 2
            or any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in xy)
        ):
            raise SpecError(f"{path}.xy: must be [column, row], two non-negative integers, got {xy!r}")
        planes = entry.get("planes")
        if planes is not None:
            if (
                not isinstance(planes, (list, tuple))
                or not planes
                or any(p not in PLANE_NAMES for p in planes)
                or len(set(planes)) != len(planes)
            ):
                raise SpecError(f"{path}.planes: must be a non-empty list without repeats from {list(PLANE_NAMES)}, got {planes!r}")
            planes = list(planes)
        tags = entry.get("tags", [])
        if not isinstance(tags, (list, tuple)) or any(not isinstance(t, str) or not t for t in tags):
            raise SpecError(f"{path}.tags: must be a list of non-empty strings, got {tags!r}")
        names[name] = {"xy": [xy[0], xy[1]], "planes": planes, "tags": list(tags)}
    return names


# ---------------------------------------------------------------------------
# Scene spec: build a scene from YAML, export a scene back to YAML
# ---------------------------------------------------------------------------

SPEC_VERSION = 1
ROTATIONS_DEG = (0, 90, 180, 270)
LAYER_NAMES = ("BASE", "DECAL")
_TOP_KEYS = ("spyrite_spec", "pixels_per_unit", "tilesets", "objects")
_TILESET_KEYS = ("image", "tile_size_px", "padding_px", "margin_px")
_OBJECT_KEYS = ("tileset", "pixels_per_unit", "clear", "fills", "tiles")
_FILL_KEYS = ("plane", "plane_offset_m", "cells", "tile", "rotation_deg", "flip_x", "flip_y", "layer")
_TILE_KEYS_IN_SPEC = (
    "plane", "plane_offset_m", "cell", "tile", "tile_span", "rotation_deg", "flip_x", "flip_y", "layer",
)


def load_spec(text):
    """Parse spec text (YAML) into a dict; the top level must be a mapping."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise SpecError(f"spyrite_spec: invalid YAML: {error}") from None
    if not isinstance(data, dict):
        raise SpecError("spyrite_spec: the file must be a mapping with 'spyrite_spec: 1'")
    return data


def dump_spec(spec):
    """YAML text of a spec dict, keys in insertion order."""
    return yaml.safe_dump(spec, sort_keys=False)


def _unknown_keys(path, mapping, valid):
    unknown = [k for k in mapping if k not in valid]
    if unknown:
        raise SpecError(f"{path}: unknown keys {sorted(map(str, unknown))}; valid keys are {list(valid)}")


def _mapping(path, value, what):
    if not isinstance(value, dict):
        raise SpecError(f"{path}: must be a mapping of {what}, got {value!r}")
    return value


def _int_list(path, value, length, minimum):
    if (
        not isinstance(value, (list, tuple))
        or len(value) != length
        or any(isinstance(v, bool) or not isinstance(v, int) or v < minimum for v in value)
    ):
        bound = "positive" if minimum == 1 else "non-negative" if minimum == 0 else "whole-number"
        raise SpecError(f"{path}: must be a list of {length} {bound} integers, got {value!r}")
    return list(value)


def _cell(path, value):
    return _int_list(path, value, 2, -(2**31))


def _bool(path, value, default):
    if value is None:
        return default
    if not isinstance(value, bool):
        raise SpecError(f"{path}: must be true or false, got {value!r}")
    return value


def _tile(path, value):
    if isinstance(value, str):
        if not TILE_NAME_PATTERN.fullmatch(value):
            raise SpecError(f"{path}: tile names may only contain letters, digits and '_', got {value!r}")
        return value
    return _int_list(path, value, 2, 0) if isinstance(value, (list, tuple)) else _bad_tile(path, value)


def _bad_tile(path, value):
    raise SpecError(f"{path}: must be a tile name or [column, row], got {value!r}")


def _orientation(path, entry):
    rotation = entry.get("rotation_deg", 0)
    if isinstance(rotation, bool) or not isinstance(rotation, (int, float)) or rotation not in ROTATIONS_DEG:
        raise SpecError(f"{path}.rotation_deg: must be one of {list(ROTATIONS_DEG)}, got {rotation!r}")
    layer = entry.get("layer", "BASE")
    if layer not in LAYER_NAMES:
        raise SpecError(f"{path}.layer: must be one of {list(LAYER_NAMES)}, got {layer!r}")
    plane = entry.get("plane", "XY")
    if plane not in PLANE_NAMES:
        raise SpecError(f"{path}.plane: must be one of {list(PLANE_NAMES)}, got {plane!r}")
    offset = entry.get("plane_offset_m", 0)
    if isinstance(offset, bool) or not isinstance(offset, (int, float)) or offset != offset or abs(offset) == float("inf"):
        raise SpecError(f"{path}.plane_offset_m: must be a finite number of metres, got {offset!r}")
    return {
        "plane": plane,
        "plane_offset_m": float(offset),
        "rotation_deg": int(rotation),
        "flip_x": _bool(f"{path}.flip_x", entry.get("flip_x"), False),
        "flip_y": _bool(f"{path}.flip_y", entry.get("flip_y"), False),
        "layer": layer,
    }


def _entries(path, value, valid, build):
    if value is None:
        return []
    if not isinstance(value, list):
        raise SpecError(f"{path}: must be a list of mappings, got {value!r}")
    out = []
    for i, entry in enumerate(value):
        here = f"{path}[{i}]"
        if not isinstance(entry, dict):
            raise SpecError(f"{here}: must be a mapping with keys {list(valid)}, got {entry!r}")
        _unknown_keys(here, entry, valid)
        out.append(build(here, entry))
    return out


def _fill(path, entry):
    for key in ("cells", "tile"):
        if key not in entry:
            raise SpecError(f"{path}.{key}: required")
    cells = entry["cells"]
    if not isinstance(cells, (list, tuple)) or len(cells) != 2:
        raise SpecError(f"{path}.cells: must be [[min_x, min_y], [max_x, max_y]], got {cells!r}")
    low = _cell(f"{path}.cells[0]", cells[0])
    high = _cell(f"{path}.cells[1]", cells[1])
    if high[0] < low[0] or high[1] < low[1]:
        raise SpecError(f"{path}.cells: the max corner {high} must be >= the min corner {low} in both components")
    normalized = _orientation(path, entry)
    normalized.update({"cell_min_xy": low, "cell_max_xy": high, "tile": _tile(f"{path}.tile", entry["tile"])})
    return normalized


def _placement(path, entry):
    for key in ("cell", "tile"):
        if key not in entry:
            raise SpecError(f"{path}.{key}: required")
    normalized = _orientation(path, entry)
    normalized.update(
        {
            "cell_xy": _cell(f"{path}.cell", entry["cell"]),
            "tile": _tile(f"{path}.tile", entry["tile"]),
            "tile_span": _int_list(f"{path}.tile_span", entry.get("tile_span", [1, 1]), 2, 1),
        }
    )
    return normalized


def validate_spec(spec):
    """Check a loaded spec and return it normalised: defaults filled, every value in its canonical type.

    The normalised spec is ``{"spyrite_spec": 1, "tilesets": {name: {image, tile_size_px, padding_px,
    margin_px}}, "objects": {name: {tileset, pixels_per_unit, clear, fills: [{plane, plane_offset_m,
    cell_min_xy, cell_max_xy, tile, rotation_deg, flip_x, flip_y, layer}], tiles: [{plane, plane_offset_m,
    cell_xy, tile, tile_span, rotation_deg, flip_x, flip_y, layer}]}}}``. Object ``pixels_per_unit`` falls back
    to the top-level one. Tile names are only syntax-checked here; the tileset's sidecar resolves them when
    the spec is built. Raises ``SpecError`` with the dotted path of the first bad value.
    """
    if not isinstance(spec, dict):
        raise SpecError("spyrite_spec: the spec must be a mapping with 'spyrite_spec: 1'")
    version = spec.get("spyrite_spec")
    if isinstance(version, bool) or version != SPEC_VERSION:
        raise SpecError("spyrite_spec: must be 1")
    _unknown_keys("spec", spec, _TOP_KEYS)
    default_ppu = spec.get("pixels_per_unit")
    if default_ppu is not None and (isinstance(default_ppu, bool) or not isinstance(default_ppu, int) or default_ppu < 1):
        raise SpecError(f"pixels_per_unit: must be a positive integer, got {default_ppu!r}")

    tilesets = {}
    for name, entry in _mapping("tilesets", spec.get("tilesets") or {}, "material name to tileset").items():
        path = f"tilesets.{name}"
        if not isinstance(name, str) or not name:
            raise SpecError(f"tilesets: material names must be non-empty strings, got {name!r}")
        _mapping(path, entry, "image, tile_size_px, padding_px, margin_px")
        _unknown_keys(path, entry, _TILESET_KEYS)
        for key in ("image", "tile_size_px"):
            if key not in entry:
                raise SpecError(f"{path}.{key}: required")
        if not isinstance(entry["image"], str) or not entry["image"]:
            raise SpecError(f"{path}.image: must be a path string, got {entry['image']!r}")
        tilesets[name] = {
            "image": entry["image"],
            "tile_size_px": _int_list(f"{path}.tile_size_px", entry["tile_size_px"], 2, 1),
            "padding_px": _int_list(f"{path}.padding_px", entry.get("padding_px", [0, 0]), 2, 0),
            "margin_px": _int_list(f"{path}.margin_px", entry.get("margin_px", [0, 0, 0, 0]), 4, 0),
        }

    objects = {}
    for name, entry in _mapping("objects", spec.get("objects") or {}, "object name to object").items():
        path = f"objects.{name}"
        if not isinstance(name, str) or not name:
            raise SpecError(f"objects: object names must be non-empty strings, got {name!r}")
        _mapping(path, entry, "tileset, pixels_per_unit, clear, fills, tiles")
        _unknown_keys(path, entry, _OBJECT_KEYS)
        if "tileset" not in entry:
            raise SpecError(f"{path}.tileset: required, one of {sorted(tilesets)}")
        if entry["tileset"] not in tilesets:
            raise SpecError(f"{path}.tileset: unknown tileset {entry['tileset']!r}; declared tilesets are {sorted(tilesets)}")
        ppu = entry.get("pixels_per_unit", default_ppu)
        if ppu is None:
            raise SpecError(f"{path}.pixels_per_unit: required (or set a top-level pixels_per_unit)")
        if isinstance(ppu, bool) or not isinstance(ppu, int) or ppu < 1:
            raise SpecError(f"{path}.pixels_per_unit: must be a positive integer, got {ppu!r}")
        objects[name] = {
            "tileset": entry["tileset"],
            "pixels_per_unit": ppu,
            "clear": _bool(f"{path}.clear", entry.get("clear"), False),
            "fills": _entries(f"{path}.fills", entry.get("fills"), _FILL_KEYS, _fill),
            "tiles": _entries(f"{path}.tiles", entry.get("tiles"), _TILE_KEYS_IN_SPEC, _placement),
        }
    return {"spyrite_spec": SPEC_VERSION, "tilesets": tilesets, "objects": objects}
