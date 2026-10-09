"""Spyrite Tile ops: build tile-based 3D pixel-art scenes from a blended agent.

These functions are blended facade ops. blended finds this module through
the `blended.ops` entry point (see `blended.plugins`), checks every function
in `__all__` against the op contract (`blended.ops._contract`), and exposes
them as agent tools over MCP (DOI 10.48550/arXiv.2503.23278).

Each op is a thin adapter over the `api` module of the Spyrite Tile add-on,
which does the real work in Blender (`addon/spyrite_tile/api.py`). The add-on
is imported only inside `_addon_api()`, so this module imports without
`bpy`: the blended server process (Python 3.11) imports it to build tool
schemas, and Blender's Python 3.13 imports it to run the tools.

Conventions, shared with the add-on api:
- A cell is an integer grid cell, one tile big. Its world size is
  `tile_size_px / pixels_per_unit_px`, from the tileset and the object.
- `tile_xy` is (column from the left, row from the top) in the tileset image.
- Planes: XY floor (right +X, up +Y, normal +Z, offset = z); XZ front wall
  (right +X, up +Z, normal -Y, offset = y); YZ side wall (right +Y, up +Z,
  normal +X, offset = x).
- `rotation_deg` is one of 0, 90, 180, 270.

No op here returns an object name, so blended's manifold mesh gate, which an
open tile mesh would always fail, does not apply.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from blended.ops._contract import op

ADDON_MODULE_NAME = "spyrite_tile"
ADDON_API_MODULE_NAME = "api"
ADDON_NOT_ENABLED_MESSAGE = (
    "The Spyrite Tile add-on is not enabled in this Blender; run "
    "`make install-addon` in /Users/ladvien/spyrit_tile and restart Blender."
)
ALLOWED_ROTATIONS_DEG = (0, 90, 180, 270)

__all__ = [
    "import_tileset",
    "create_tile_object",
    "place_tiles",
    "fill_tiles",
    "remove_tiles",
    "paint_faces",
    "tile_object_report",
    "scene_report",
    "set_pixel_art_view",
]


# --- placements and reports ----------------------------------------------


@dataclass(frozen=True)
class TilePlacement:
    """Tile at a cell; give exactly one of tile_xy=(col from left, row from top) or tile (a name from the tileset's sidecar, or a (col, row) pair); picture turned CCW by rotation_deg, then mirrored as seen."""

    cell_xy: tuple[int, int]
    tile_xy: tuple[int, int] | None = None
    tile: str | tuple[int, int] | None = None
    tile_span: tuple[int, int] = (1, 1)
    plane: Literal["XY", "XZ", "YZ"] = "XY"
    plane_offset_m: float = 0.0
    rotation_deg: float = 0.0
    flip_x: bool = False
    flip_y: bool = False
    layer: Literal["BASE", "DECAL"] = "BASE"


@dataclass(frozen=True)
class TilesetReport:
    """A tileset the add-on registered from an image."""

    material_name: str
    image_name: str
    image_size_px: tuple[int, int]
    tile_size_px: tuple[int, int]
    columns: int
    rows: int
    grid_id: int
    reused_material: str | None = None
    tile_names: dict[str, dict] = field(default_factory=dict)


@dataclass(frozen=True)
class TileObjectReport:
    """A mesh object bound to a tileset, ready for placements."""

    object_name: str
    material_name: str
    grid_id: int
    pixels_per_unit: int


@dataclass(frozen=True)
class TileEditReport:
    """What a tile edit changed; a counter the edit does not report is 0."""

    face_count: int = 0
    built: int = 0
    remapped: int = 0
    removed: int = 0
    painted: int = 0


@dataclass(frozen=True)
class TileFaceReading:
    """One face: world center and normal, the tile its UVs show (column from left, row from top) and how it was placed.

    `rotation_deg`, `flip_x`, `flip_y`, `layer`, `plane`, `plane_offset_m` and `cell_xy` read back exactly what
    place_tiles was given; `facing` is +1 along the plane's normal, -1 against it, 0 without plane; `on_grid` is
    false for faces that are not whole-cell rectangles (then `cell_xy` is only the cell of the minimum corner).
    `tile` is the tile's name from the tileset's sidecar, None when the tile is unnamed.
    """

    index: int
    center_m: tuple[float, float, float]
    normal: tuple[float, float, float]
    tile_xy: tuple[int, int]
    tile: str | None
    material: str
    tile_span: tuple[int, int]
    rotation_deg: int
    flip_x: bool
    flip_y: bool
    layer: Literal["BASE", "DECAL"]
    plane: Literal["XY", "XZ", "YZ"] | None
    facing: int
    plane_offset_m: float | None
    cell_xy: tuple[int, int] | None
    on_grid: bool
    tileset: str
    tile: str | None


@dataclass(frozen=True)
class TileObjectReading:
    """A tile object's faces; `truncated` is true when the face list was capped."""

    object_name: str
    face_count: int
    truncated: bool
    faces: tuple[TileFaceReading, ...]


@dataclass(frozen=True)
class TilesetReading:
    """A tileset of the scene: its image and tile layout."""

    material_name: str
    image_name: str
    image_path: str
    image_size_px: tuple[int, int]
    tile_size_px: tuple[int, int]
    padding_px: tuple[int, int]
    margin_px: tuple[int, int, int, int]
    columns: int
    rows: int
    grid_id: int
    tile_names: dict[str, dict]


@dataclass(frozen=True)
class TileObjectSummary:
    """A tile object of the scene; `overlay_of` names the base object of an overlay, else None."""

    object_name: str
    material_name: str
    grid_id: int
    pixels_per_unit: int
    face_count: int
    location_m: tuple[float, float, float]
    overlay_of: str | None


@dataclass(frozen=True)
class SceneReading:
    """Every tileset and tile object of the scene, the tilesets removed this session and the scene settings."""

    tilesets: tuple[TilesetReading, ...]
    tile_objects: tuple[TileObjectSummary, ...]
    removed_tilesets: tuple[str, ...]
    world_pixels: int
    mesh_decal_offset: float
    auto_merge: bool


@dataclass(frozen=True)
class PixelArtViewReport:
    """The scene's Workbench render settings after set_pixel_art_view."""

    color_type: str
    light: str
    render_aa: str
    view_transform: str
    viewports_textured: int


# --- plumbing -------------------------------------------------------------


def _addon_api():
    """The enabled add-on's `api` module, or a RuntimeError naming the fix."""
    import bpy

    for key in bpy.context.preferences.addons.keys():
        if key.rpartition(".")[2] == ADDON_MODULE_NAME:
            return importlib.import_module(f"{key}.{ADDON_API_MODULE_NAME}")
    raise RuntimeError(ADDON_NOT_ENABLED_MESSAGE)


def _check_rotation_deg(rotation_deg: float) -> None:
    if rotation_deg not in ALLOWED_ROTATIONS_DEG:
        raise ValueError(
            f"rotation_deg must be one of {ALLOWED_ROTATIONS_DEG}, got {rotation_deg!r}"
        )


def _pair(values) -> tuple[int, int]:
    first, second = values
    return (int(first), int(second))


def _triple(values) -> tuple[float, float, float]:
    first, second, third = values
    return (float(first), float(second), float(third))


def _placement_dict(placement: TilePlacement) -> dict:
    result = {
        "cell_xy": list(placement.cell_xy),
        "tile_span": list(placement.tile_span),
        "plane": placement.plane,
        "plane_offset_m": placement.plane_offset_m,
        "rotation_deg": float(placement.rotation_deg),
        "flip_x": placement.flip_x,
        "flip_y": placement.flip_y,
        "layer": placement.layer,
    }
    if placement.tile_xy is not None:
        result["tile_xy"] = list(placement.tile_xy)
    if placement.tile is not None:
        result["tile"] = placement.tile if isinstance(placement.tile, str) else list(placement.tile)
    return result


def _edit_report(result: dict) -> TileEditReport:
    return TileEditReport(
        face_count=int(result.get("face_count", 0)),
        built=int(result.get("built", 0)),
        remapped=int(result.get("remapped", 0)),
        removed=int(result.get("removed", 0)),
        painted=int(result.get("painted", 0)),
    )


# --- ops -------------------------------------------------------------------


def import_tileset(
    name: str,
    image_path: Path,
    tile_size_px: tuple[int, int],
    padding_px: tuple[int, int] = (0, 0),
    margin_px: tuple[int, int, int, int] = (0, 0, 0, 0),
) -> TilesetReport:
    """Register an absolute-path image as tileset `name`; tiles are tile_size_px, counted from the top-left.

    Idempotent: if the same image with the same layout is already a tileset under another name, nothing is
    created and the report names it in `reused_material`; use the report's `material_name` afterwards.
    """
    result = _addon_api().create_tileset(
        material_name=name,
        image_path=str(image_path),
        tile_size_px=tile_size_px,
        padding_px=padding_px,
        margin_px=margin_px,
    )
    return TilesetReport(
        material_name=result["material_name"],
        image_name=result["image_name"],
        image_size_px=_pair(result["image_size_px"]),
        tile_size_px=_pair(result["tile_size_px"]),
        columns=int(result["columns"]),
        rows=int(result["rows"]),
        grid_id=int(result["grid_id"]),
        reused_material=result["reused_material"],
        tile_names=result["tile_names"],
    )


def create_tile_object(
    name: str, tileset_name: str, pixels_per_unit_px: int = 16
) -> TileObjectReport:
    """Create an empty tile mesh object `name` using tileset `tileset_name`; one metre is pixels_per_unit_px texels."""
    result = _addon_api().create_tile_object(
        object_name=name,
        material_name=tileset_name,
        pixels_per_unit=pixels_per_unit_px,
    )
    return TileObjectReport(
        object_name=result["object_name"],
        material_name=result["material_name"],
        grid_id=int(result["grid_id"]),
        pixels_per_unit=int(result["pixels_per_unit"]),
    )


def place_tiles(
    object_name: str, tileset_name: str, placements: list[TilePlacement]
) -> TileEditReport:
    """Place tiles on cells of planes XY/XZ/YZ; a placed cell is re-textured, not duplicated."""
    for placement in placements:
        _check_rotation_deg(placement.rotation_deg)
    result = _addon_api().place_tiles(
        object_name=object_name,
        material_name=tileset_name,
        placements=[_placement_dict(placement) for placement in placements],
    )
    return _edit_report(result)


def fill_tiles(
    object_name: str,
    tileset_name: str,
    cell_min_xy: tuple[int, int],
    cell_max_xy: tuple[int, int],
    tile_xy: tuple[int, int] | str,
    plane: Literal["XY", "XZ", "YZ"] = "XY",
    plane_offset_m: float = 0.0,
    rotation_deg: float = 0.0,
    flip_x: bool = False,
    flip_y: bool = False,
) -> TileEditReport:
    """Fill the inclusive cell rectangle cell_min_xy..cell_max_xy of one plane with one tile."""
    _check_rotation_deg(rotation_deg)
    result = _addon_api().fill_tiles(
        object_name=object_name,
        material_name=tileset_name,
        cell_min_xy=cell_min_xy,
        cell_max_xy=cell_max_xy,
        tile_xy=tile_xy,
        plane=plane,
        plane_offset_m=plane_offset_m,
        rotation_deg=float(rotation_deg),
        flip_x=flip_x,
        flip_y=flip_y,
    )
    return _edit_report(result)


def remove_tiles(
    object_name: str,
    cells: list[tuple[int, int]],
    plane: Literal["XY", "XZ", "YZ"] = "XY",
    plane_offset_m: float = 0.0,
) -> TileEditReport:
    """Delete the tile faces on the listed cells of one plane."""
    result = _addon_api().remove_tiles(
        object_name=object_name,
        plane=plane,
        plane_offset_m=plane_offset_m,
        cells=[list(cell) for cell in cells],
    )
    return _edit_report(result)


def paint_faces(
    object_name: str,
    tileset_name: str,
    face_indices: list[int],
    tile_xy: tuple[int, int] | str,
    rotation_deg: float = 0.0,
    flip_x: bool = False,
    flip_y: bool = False,
) -> TileEditReport:
    """Re-texture existing faces, by index from tile_object_report, with one tile; geometry is unchanged."""
    _check_rotation_deg(rotation_deg)
    result = _addon_api().paint_faces(
        object_name=object_name,
        material_name=tileset_name,
        face_indices=list(face_indices),
        tile_xy=tile_xy,
        rotation_deg=float(rotation_deg),
        flip_x=flip_x,
        flip_y=flip_y,
    )
    return _edit_report(result)


@op(reads_only=True)
def tile_object_report(object_name: str) -> TileObjectReading:
    """List a tile object's faces: tile_xy, orientation, plane, cell, layer, offset, on_grid, tileset, tile name."""
    result = _addon_api().describe_tile_object(object_name=object_name)
    return TileObjectReading(
        object_name=result["object_name"],
        face_count=int(result["face_count"]),
        truncated=bool(result["truncated"]),
        faces=tuple(
            TileFaceReading(
                index=int(face["index"]),
                center_m=_triple(face["center_m"]),
                normal=_triple(face["normal"]),
                tile_xy=_pair(face["tile_xy"]),
                material=face["material"],
                tile_span=_pair(face["tile_span"]),
                rotation_deg=int(face["rotation_deg"]),
                flip_x=bool(face["flip_x"]),
                flip_y=bool(face["flip_y"]),
                layer=face["layer"],
                plane=face["plane"],
                facing=int(face["facing"]),
                plane_offset_m=None if face["plane_offset_m"] is None else float(face["plane_offset_m"]),
                cell_xy=None if face["cell_xy"] is None else _pair(face["cell_xy"]),
                on_grid=bool(face["on_grid"]),
                tileset=face["tileset"],
                tile=face["tile"],
            )
            for face in result["faces"]
        ),
    )


@op(reads_only=True)
def scene_report() -> SceneReading:
    """List every tileset (image, tile layout, names) and tile object (counts, location) of the scene."""
    result = _addon_api().describe_scene()
    settings = result["settings"]
    return SceneReading(
        tilesets=tuple(
            TilesetReading(
                material_name=t["material_name"],
                image_name=t["image_name"],
                image_path=t["image_path"],
                image_size_px=_pair(t["image_size_px"]),
                tile_size_px=_pair(t["tile_size_px"]),
                padding_px=_pair(t["padding_px"]),
                margin_px=tuple(int(v) for v in t["margin_px"]),
                columns=int(t["columns"]),
                rows=int(t["rows"]),
                grid_id=int(t["grid_id"]),
                tile_names=dict(t["tile_names"]),
            )
            for t in result["tilesets"]
        ),
        tile_objects=tuple(
            TileObjectSummary(
                object_name=o["object_name"],
                material_name=o["material_name"],
                grid_id=int(o["grid_id"]),
                pixels_per_unit=int(o["pixels_per_unit"]),
                face_count=int(o["face_count"]),
                location_m=_triple(o["location_m"]),
                overlay_of=o["overlay_of"],
            )
            for o in result["tile_objects"]
        ),
        removed_tilesets=tuple(result["removed_tilesets"]),
        world_pixels=int(settings["world_pixels"]),
        mesh_decal_offset=float(settings["mesh_decal_offset"]),
        auto_merge=bool(settings["auto_merge"]),
    )


def set_pixel_art_view() -> PixelArtViewReport:
    """Show tile textures as crisp unlit texels in Workbench renders and open Solid viewports."""
    result = _addon_api().set_pixel_art_view()
    return PixelArtViewReport(
        color_type=result["color_type"],
        light=result["light"],
        render_aa=result["render_aa"],
        view_transform=result["view_transform"],
        viewports_textured=result["viewports_textured"],
    )
