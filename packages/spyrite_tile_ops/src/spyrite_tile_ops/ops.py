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
VERIFY_DEFAULT_TOLERANCE = 12.0

__all__ = [
    "import_tileset",
    "create_tile_object",
    "place_tiles",
    "fill_tiles",
    "fill_pattern",
    "remove_tiles",
    "paint_faces",
    "build_room",
    "extrude_edge",
    "move_faces",
    "tile_object_report",
    "scene_report",
    "select_tile_faces",
    "checkpoint",
    "rollback",
    "discard_checkpoint",
    "list_checkpoints",
    "set_pixel_art_view",
    "build_spec",
    "export_spec",
    "verify_tile_object",
    "reload_core",
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
    moved: int = 0


@dataclass(frozen=True)
class RoomReport:
    """What build_room built: the floor, each wall by name ('back', 'left'), the ceiling (None when not asked) and the object's face count.

    Each part reports its `built` and `remapped` faces; its `face_count` is the object's total after the room.
    """

    floor: TileEditReport
    walls: dict[str, TileEditReport]
    ceiling: TileEditReport | None
    face_count: int


@dataclass(frozen=True)
class PatternTile:
    """One tile of a pattern: a tile name or (column, row), with its own orientation."""

    tile: str | tuple[int, int]
    rotation_deg: float = 0.0
    flip_x: bool = False
    flip_y: bool = False


@dataclass(frozen=True)
class PatternSpec:
    """A fill pattern. random: tiles + seed (+ weights). stamp: rows. autotile: autotile_tiles, 16 entries."""

    kind: Literal["random", "stamp", "autotile"]
    tiles: list[PatternTile] | None = None
    weights: list[float] | None = None
    seed: int | None = None
    rows: list[list[PatternTile]] | None = None
    autotile_tiles: list[PatternTile] | None = None


def _pattern_tile_value(tile) -> dict:
    if isinstance(tile, dict):
        return tile
    value = {"tile": list(tile.tile) if isinstance(tile.tile, tuple) else tile.tile}
    value.update(rotation_deg=float(tile.rotation_deg), flip_x=tile.flip_x, flip_y=tile.flip_y)
    return value


def _pattern_value(pattern) -> dict:
    """The add-on api's pattern dict for a PatternSpec (or an already plain dict)."""
    if isinstance(pattern, dict):
        return pattern
    value: dict = {"kind": pattern.kind}
    if pattern.kind == "random":
        value["tiles"] = [_pattern_tile_value(t) for t in pattern.tiles or []]
        if pattern.weights is not None:
            value["weights"] = list(pattern.weights)
        if pattern.seed is not None:
            value["seed"] = pattern.seed
    elif pattern.kind == "stamp":
        value["rows"] = [[_pattern_tile_value(t) for t in row] for row in pattern.rows or []]
    else:
        value["mask"] = "edges4"
        entries = pattern.autotile_tiles or []
        if len(entries) != 16:
            raise ValueError(f"autotile_tiles needs 16 entries (index = key 0..15), got {len(entries)}")
        value["tiles"] = {str(i): _pattern_tile_value(t) for i, t in enumerate(entries)}
    return value


@dataclass(frozen=True)
class PatternFillReport:
    """What a pattern fill changed: the edit counters, the cells filled and, up to 500 cells, each cell's tile."""

    face_count: int = 0
    built: int = 0
    remapped: int = 0
    cells: int = 0
    assignments: tuple[dict, ...] | None = None
    truncated: bool = False


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
class TileFaceSelector:
    """Which faces `select_tile_faces` picks; every field is optional and all given fields must hold.

    `tile`/`tiles`: tile name (sidecar) or [column, row]; `tag`: any tile carrying the sidecar tag;
    `plane`, `plane_offset_m`, `layer` ('BASE'/'DECAL'), `facing` (1/-1); `cell_min_xy` + `cell_max_xy`: inclusive
    cell rectangle (give both); `connected_to_cell`: 4-connected region of same-tile faces flooding from that
    cell (needs `plane`).
    """

    tile: str | tuple[int, int] | None = None
    tiles: list[str | tuple[int, int]] | None = None
    tag: str | None = None
    plane: Literal["XY", "XZ", "YZ"] | None = None
    plane_offset_m: float | None = None
    cell_min_xy: tuple[int, int] | None = None
    cell_max_xy: tuple[int, int] | None = None
    layer: Literal["BASE", "DECAL"] | None = None
    facing: int | None = None
    connected_to_cell: tuple[int, int] | None = None


@dataclass(frozen=True)
class TileFaceSelection:
    """Sorted face indices matched by a selector, ready for `paint_faces`."""

    face_indices: tuple[int, ...]
    count: int

class CheckpointReport:
    """A checkpoint just taken: its id and the objects it covers."""

    checkpoint_id: int
    objects: tuple[str, ...]


@dataclass(frozen=True)
class RollbackReport:
    """The objects restored from a checkpoint (the checkpoint stays available)."""

    checkpoint_id: int
    restored: tuple[str, ...]


@dataclass(frozen=True)
class CheckpointSummary:
    """One live checkpoint; `created` is an ISO-8601 UTC timestamp."""

    checkpoint_id: int
    label: str
    created: str
    objects: tuple[str, ...]


@dataclass(frozen=True)
class CheckpointListing:
    """Every live checkpoint, oldest first."""

    checkpoints: tuple[CheckpointSummary, ...]


@dataclass(frozen=True)
class DiscardReport:
    """The checkpoint removed and the objects it covered."""

    checkpoint_id: int
    discarded: tuple[str, ...]


@dataclass(frozen=True)
class PixelArtViewReport:
    """The scene's Workbench render settings after set_pixel_art_view."""

    color_type: str
    light: str
    render_aa: str
    view_transform: str
    viewports_textured: int


@dataclass(frozen=True)
class FaceMismatch:
    """A face whose rendered quadrants differ from its tile by more than the tolerance (0-255 scale)."""

    index: int
    plane: str
    cell_xy: tuple[int, int]
    tile_xy: tuple[int, int]
    max_channel_delta: float


@dataclass(frozen=True)
class VerifyReport:
    """Result of verify_tile_object: `measured` faces judged, the mismatching ones, and where the render is."""

    ok: bool
    measured: int
    mismatches: tuple[FaceMismatch, ...]
    max_channel_delta: float
    evidence_dir: str
    render_path: str


@dataclass(frozen=True)
class ReloadReport:
    """Add-on modules reloaded by reload_core, in order."""

    reloaded: tuple[str, ...]


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
        moved=int(result.get("moved", 0)),
    )


@dataclass(frozen=True)
class SpecObjectReport:
    """One object a spec built: faces newly built, faces remapped in place, faces now on the object."""

    object_name: str
    built: int
    remapped: int
    face_count: int


@dataclass(frozen=True)
class SpecBuildReport:
    """What build_spec made: the spec file, one report per declared tileset and one per object."""

    spec_path: str
    tilesets: tuple[TilesetReport, ...]
    objects: tuple[SpecObjectReport, ...]


@dataclass(frozen=True)
class SpecExportReport:
    """What export_spec wrote; `unexported_faces` maps object name to the face indices that were left out."""

    spec_path: str
    objects: int
    tiles: int
    unexported_faces: dict[str, tuple[int, ...]] = field(default_factory=dict)


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


def fill_pattern(
    object_name: str,
    tileset_name: str,
    pattern: PatternSpec,
    plane: Literal["XY", "XZ", "YZ"] = "XY",
    plane_offset_m: float = 0.0,
    layer: Literal["BASE", "DECAL"] = "BASE",
    cell_min_xy: tuple[int, int] | None = None,
    cell_max_xy: tuple[int, int] | None = None,
    cells: list[tuple[int, int]] | None = None,
) -> PatternFillReport:
    """Fill cells with a seeded random, stamp or autotile pattern; give cell_min_xy+cell_max_xy or cells.

    random: tiles, optional weights, seed. stamp: rows (rows[0] is the top row). autotile: autotile_tiles, the
    16 tiles indexed by key N=1, E=2, S=4, W=8 (one bit per neighbour cell in the fill set).
    """
    result = _addon_api().fill_pattern(
        object_name=object_name,
        material_name=tileset_name,
        pattern=_pattern_value(pattern),
        plane=plane,
        plane_offset_m=plane_offset_m,
        layer=layer,
        cell_min_xy=cell_min_xy,
        cell_max_xy=cell_max_xy,
        cells=None if cells is None else [list(cell) for cell in cells],
    )
    assignments = result.get("assignments")
    return PatternFillReport(
        face_count=int(result["face_count"]),
        built=int(result["built"]),
        remapped=int(result["remapped"]),
        cells=int(result["cells"]),
        assignments=None if assignments is None else tuple(assignments),
        truncated=bool(result.get("truncated", False)),
    )


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


def build_room(
    object_name: str,
    tileset_name: str,
    size_cells: tuple[int, int, int],
    floor_tile: tuple[int, int] | str,
    wall_tile: tuple[int, int] | str,
    walls: tuple[Literal["back", "left"], ...] = ("back", "left"),
    origin_cell: tuple[int, int] = (0, 0),
    floor_offset_m: float = 0.0,
    ceiling_tile: tuple[int, int] | str | None = None,
) -> RoomReport:
    """Build a floor, a back (XZ) and a left (YZ) wall and an optional ceiling in one atomic call.

    size_cells is (width, depth, height) in cells; origin_cell the floor's minimum (x, y) cell. Only back/left walls exist
    because planes have fixed normals: build the room so its open sides face -Y and +X. A left wall needs square tiles.
    """
    result = _addon_api().build_room(
        object_name=object_name,
        material_name=tileset_name,
        size_cells=list(size_cells),
        floor_tile=floor_tile,
        wall_tile=wall_tile,
        walls=list(walls),
        origin_cell=list(origin_cell),
        floor_offset_m=floor_offset_m,
        ceiling_tile=ceiling_tile,
    )
    return RoomReport(
        floor=_edit_report(result["floor"]),
        walls={name: _edit_report(part) for name, part in result["walls"].items()},
        ceiling=None if result["ceiling"] is None else _edit_report(result["ceiling"]),
        face_count=int(result["face_count"]),
    )


def extrude_edge(
    object_name: str,
    tileset_name: str,
    from_cell: tuple[int, int],
    to_cell: tuple[int, int],
    side: Literal["N", "S", "E", "W"],
    count: int,
    tile: tuple[int, int] | str,
    plane: Literal["XY"] = "XY",
    plane_offset_m: float = 0.0,
    rotation_deg: float = 0.0,
    flip_x: bool = False,
    flip_y: bool = False,
) -> TileEditReport:
    """Raise a wall `count` cells high along the N or W edge of a run of floor cells.

    N puts the wall on XZ at (y + 1) cells, W on YZ at x cells (square tiles only). plane_offset_m is the floor's z, a
    whole number of cell heights. Sides S and E need walls facing +Y / -X, which no plane has.
    """
    _check_rotation_deg(rotation_deg)
    result = _addon_api().extrude_edge(
        object_name=object_name,
        material_name=tileset_name,
        plane=plane,
        plane_offset_m=plane_offset_m,
        from_cell=list(from_cell),
        to_cell=list(to_cell),
        side=side,
        height_cells=count,
        tile=tile,
        rotation_deg=float(rotation_deg),
        flip_x=flip_x,
        flip_y=flip_y,
    )
    return _edit_report(result)


def move_faces(
    object_name: str, face_indices: list[int], delta_px: tuple[int, int, int]
) -> TileEditReport:
    """Move faces (indices from tile_object_report) by whole world pixels and rebuild their UVs from their tile data.

    Vertices shared with faces that stay put move too, so neighbours deform.
    """
    result = _addon_api().move_faces(
        object_name=object_name, face_indices=list(face_indices), delta_px=list(delta_px)
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
def select_tile_faces(object_name: str, where: TileFaceSelector) -> TileFaceSelection:
    """Select faces of a tile object by tile, tag, plane, offset, cell rectangle, layer, facing or connectivity."""
    criteria = {
        key: (list(value) if isinstance(value, tuple) else value)
        for key, value in vars(where).items()
        if value is not None
    }
    if "tiles" in criteria:
        criteria["tiles"] = [list(t) if isinstance(t, tuple) else t for t in criteria["tiles"]]
    result = _addon_api().select_faces(object_name=object_name, where=criteria)
    return TileFaceSelection(face_indices=tuple(int(i) for i in result["face_indices"]), count=int(result["count"]))


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


def checkpoint(object_names: list[str] | None = None, label: str = "") -> CheckpointReport:
    """Snapshot tile objects (all tile objects when object_names is omitted) so rollback can restore them."""
    result = _addon_api().checkpoint(object_names=object_names, label=label)
    return CheckpointReport(
        checkpoint_id=int(result["checkpoint_id"]), objects=tuple(result["objects"])
    )


def rollback(checkpoint_id: int) -> RollbackReport:
    """Restore every object of a checkpoint (mesh, grid, materials, transform); the checkpoint stays usable."""
    result = _addon_api().rollback(checkpoint_id=checkpoint_id)
    return RollbackReport(
        checkpoint_id=int(result["checkpoint_id"]), restored=tuple(result["restored"])
    )


def discard_checkpoint(checkpoint_id: int) -> DiscardReport:
    """Forget a checkpoint and delete its hidden mesh copies."""
    result = _addon_api().discard_checkpoint(checkpoint_id=checkpoint_id)
    return DiscardReport(
        checkpoint_id=int(result["checkpoint_id"]), discarded=tuple(result["discarded"])
    )


@op(reads_only=True)
def list_checkpoints() -> CheckpointListing:
    """List the live checkpoints: id, label, ISO-8601 UTC creation time, objects."""
    return CheckpointListing(
        checkpoints=tuple(
            CheckpointSummary(
                checkpoint_id=int(c["checkpoint_id"]),
                label=c["label"],
                created=c["created"],
                objects=tuple(c["objects"]),
            )
            for c in _addon_api().list_checkpoints()
        )
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


def build_spec(spec_path: Path) -> SpecBuildReport:
    """Build a scene from an absolute-path YAML spec: tilesets, objects, fills and tiles (names allowed).

    Image paths in the spec are relative to the spec file. An object that fails to build is rolled back;
    tilesets and objects built before it stay. See tests/fixtures/room.spyrite.yaml for an example.
    """
    result = _addon_api().build_spec(spec_path=str(spec_path))
    return SpecBuildReport(
        spec_path=result["spec_path"],
        tilesets=tuple(
            TilesetReport(
                material_name=t["material_name"],
                image_name=t["image_name"],
                image_size_px=_pair(t["image_size_px"]),
                tile_size_px=_pair(t["tile_size_px"]),
                columns=int(t["columns"]),
                rows=int(t["rows"]),
                grid_id=int(t["grid_id"]),
                reused_material=t["reused_material"],
                tile_names=dict(t["tile_names"]),
            )
            for t in result["tilesets"]
        ),
        objects=tuple(
            SpecObjectReport(
                object_name=o["object_name"],
                built=int(o["built"]),
                remapped=int(o["remapped"]),
                face_count=int(o["face_count"]),
            )
            for o in result["objects"]
        ),
    )


@op(reads_only=True)
def export_spec(objects: list[str], spec_path: Path) -> SpecExportReport:
    """Write the tile objects named in `objects` as a YAML spec at an absolute path that build_spec rebuilds.

    One `tiles` entry per whole-cell face; faces that placements cannot rebuild (hand modelled, backwards,
    untextured) are left out and listed in `unexported_faces`. Writes a file; the scene is not changed.
    """
    result = _addon_api().export_spec(object_names=list(objects), spec_path=str(spec_path))
    return SpecExportReport(
        spec_path=result["spec_path"],
        objects=int(result["objects"]),
        tiles=int(result["tiles"]),
        unexported_faces={name: tuple(int(i) for i in faces) for name, faces in result["unexported_faces"].items()},
    )


@op(reads_only=True)
def verify_tile_object(
    object_name: str,
    view: Literal["auto", "top", "front", "right"] = "auto",
    tolerance: float | None = None,
    evidence_dir: Path | None = None,
) -> VerifyReport:
    """Render a tile object with Workbench and check every visible face shows its tile (pixel oracle).

    Reads only: the scene is unchanged afterwards; the render is written to `evidence_dir`/render.png.
    `tolerance` is the largest accepted colour difference per channel (0-255); None means 12.
    """
    result = _addon_api().verify_tile_object(
        object_name=object_name,
        view=view,
        tolerance=VERIFY_DEFAULT_TOLERANCE if tolerance is None else tolerance,
        evidence_dir=None if evidence_dir is None else str(evidence_dir),
    )
    return VerifyReport(
        ok=bool(result["ok"]),
        measured=int(result["measured"]),
        mismatches=tuple(
            FaceMismatch(
                index=int(m["index"]),
                plane=m["plane"],
                cell_xy=_pair(m["cell_xy"]),
                tile_xy=_pair(m["tile_xy"]),
                max_channel_delta=float(m["max_channel_delta"]),
            )
            for m in result["mismatches"]
        ),
        max_channel_delta=float(result["max_channel_delta"]),
        evidence_dir=result["evidence_dir"],
        render_path=result["render_path"],
    )


@op()
def reload_core() -> ReloadReport:
    """Reload the add-on's class-free modules (core, uv, builder, spec, probe, api) so edits apply without restarting Blender."""
    return ReloadReport(reloaded=tuple(_addon_api().reload_core()["reloaded"]))
