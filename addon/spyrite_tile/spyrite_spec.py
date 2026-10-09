"""Pure-Python (no bpy) parsing of Spyrite Tile's YAML files.

Currently: the tile-name sidecar that sits next to a tileset image
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
