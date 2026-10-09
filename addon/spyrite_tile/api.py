"""Headless tile API for Spyrite Tile: build Sprytile tile geometry from plain values.

Everything here takes and returns plain Python values (no operators, no UI
context, no mouse) so an agent or a script can build a tile scene exactly the
way the interactive Build/Paint tools would. Only ``bpy`` is required.

Conventions
-----------
Cells
    The build plane is divided into integer grid cells of one tile each. A
    cell's world size is ``tile_size_px / pixels_per_unit`` metres per axis
    (``tile_size_px`` is the tileset grid's tile size, ``pixels_per_unit`` is
    the object's pixel density, stored on the object by
    :func:`create_tile_object` and mirrored into the scene's ``world_pixels``).
    Cell ``(i, j)`` spans ``[i, i + 1) * cell_width`` along the plane's right
    axis and ``[j, j + 1) * cell_height`` along its up axis, measured from the
    grid origin.

Planes
    ===========  =====  ====  ======  ===========================
    plane        right  up    normal  ``plane_offset_m`` is
    ===========  =====  ====  ======  ===========================
    ``XY``       +X     +Y    +Z      z of the plane (floor)
    ``XZ``       +X     +Z    -Y      y of the plane (front wall)
    ``YZ``       +Y     +Z    +X      x of the plane (side wall)
    ===========  =====  ====  ======  ===========================

    The grid origin is the world origin moved ``plane_offset_m`` along the
    axis the plane is perpendicular to. ``right x up == normal`` for all three.

``tile_xy``
    ``(column from the left, row from the top)`` of the tile in the tileset
    image, like an image editor or a generated sheet. Sprytile itself counts
    tile rows from the bottom of the image; this module converts with
    ``row_sprytile = rows - 1 - row`` (for a ``tile_span`` taller than one
    tile, the span's bottom row is ``rows - (row + span_y)``).

``tile_span``
    ``(columns, rows)`` of tiles one face covers. The tile at ``tile_xy`` is the
    span's top-left tile and ``cell_xy`` is the span's bottom-left cell, so a
    ``(2, 1)`` span is a single quad two cells wide textured with two
    neighbouring tiles.

``rotation_deg``
    One of 0, 90, 180, 270: the tile picture is turned counter-clockwise by
    this much as seen from the face's normal side, which is Sprytile's
    "rotate left" (``mesh_rotate`` is stored as these degrees in radians). The
    face keeps covering its cell; only the UV orientation turns. (The
    interactive Build tool rotates the whole grid about the cursor instead, so
    its footprint moves; the API does not.) ``flip_x``/``flip_y`` then mirror the
    turned picture left-right/top-bottom as seen from the normal side, so a
    flag means the same on screen whatever the rotation (as in Tiled; Sprytile's
    own flags act before the turn, see ``_sprytile_flips``).

Layers
    ``BASE`` builds the face on the plane. ``DECAL`` builds a mesh decal one
    ``mesh_decal_offset`` (default 0.002 m) above the plane along its normal
    and requires a BASE face in that cell. Decal faces therefore sit at a
    different ``plane_offset_m`` than their base: pass the shifted offset to
    :func:`remove_tiles` to remove them.

Overlays
    :func:`create_overlay_object` makes a child tile object of a base object. Placements, fills, patterns and
    ``remove_tiles`` on it take the BASE's ``plane_offset_m``; the geometry is built ``lift_m`` (default
    0.002 m) toward the viewer (XY +, XZ -, YZ +). Read-back reports the lifted offsets, so filter an
    overlay's faces by the lifted value.

Reading back
    :func:`describe_tile_object` reports, per face, everything the placement took: ``tile_xy``, ``tile_span``,
    ``rotation_deg``, ``flip_x``, ``flip_y``, ``layer``, ``plane``, ``facing``, ``plane_offset_m``, ``cell_xy``
    and ``on_grid`` (false for geometry that is not a whole-cell rectangle), so a scene can be rebuilt from
    its report. A decal reports its lifted ``plane_offset_m``. :func:`describe_scene` lists the tilesets, the
    tile objects and the scene settings.

Verifying
    :func:`verify_tile_object` renders an object with Workbench from the plane's side and compares the four
    quadrants of every visible face with the quadrants of its tile in the tileset image (turned and mirrored
    per the face's orientation), so UVs that disagree with the face's tile data are caught from pixels. It
    leaves the scene unchanged and writes ``render.png`` into an evidence directory.

Specs
    :func:`build_spec` builds a whole scene from a YAML file (tilesets, objects, fills, tiles; tiles may be
    given by name from the tileset's sidecar) and :func:`export_spec` writes tile objects back to one; see
    ``spyrite_spec.validate_spec`` for the schema and ``tests/fixtures/room.spyrite.yaml`` for an example.

Checkpoints
    :func:`checkpoint` copies tile objects' meshes (and grid id, pixels per unit, material slots, local
    transform, parent) so a multi-step plan can :func:`rollback` after a failure; :func:`list_checkpoints` and
    :func:`discard_checkpoint` manage them. Checkpoints are session state: the registry lives in this module
    and survives ``reload_core`` (ids keep counting). The copies are hidden ``.spyrite_ckpt_*`` meshes with a
    fake user, which stay in the file if it is saved while checkpoints are unreleased; after a file reload
    the registry still holds the dead references, and the next ``list_checkpoints``/``rollback``/
    ``discard_checkpoint`` drops those checkpoints (rollback says so in a ``ValueError``).

Behaviour shared by all editing functions
    * Inputs are validated before anything is touched; errors are
      ``ValueError`` naming the valid range.
    * The target object is put in EDIT mode for the call. Mode, object
      selection and active object of every object in the view layer are
      restored afterwards (also on failure).
    * The ``scene.sprytile_data`` fields and grid selection used are set for the
      call and restored afterwards.
    * On failure the mesh is rolled back to what it was before the call.
    * ``scene.sprytile_data.auto_merge`` is honoured as the scene has it.
    * UVs are built in Build mode (``paint_mode='MAKE_FACE'``): pixel-snapped,
      no stretch, centre aligned. Sprytile's auto-pad subpixel inset of the
      grid (default 0.05 px) shows up as UVs ~0.0004 inside the tile edge on a
      64 px sheet.

Tileset layout. A tileset is a material with one image texture node and a
Sprytile grid. ``padding_px`` is Sprytile's per-tile padding (the grid's tile
size excludes it) and ``margin_px`` is (top, right, bottom, left). Cell pitch
in the image is ``tile + 2 * padding + margins`` and ``columns`` / ``rows`` are
the number of whole pitches in the image, the way the palette lays out tiles.

Tile names
    A tileset image may have a sidecar ``<image stem>.spyrite.yaml`` (``tiles.png`` -> ``tiles.spyrite.yaml``)
    naming tiles: ``tiles: {grass: {xy: [0, 0], tags: [floor]}, wall_top: {xy: [2, 0], planes: [XZ, YZ]}}``.
    Wherever a tile is given (placement ``tile`` / ``tile_xy``, ``fill_tiles``, ``paint_faces``) a name works
    as well as ``[column, row]``; a name whose ``planes`` exclude the placement plane is refused.
    ``create_tileset`` reports ``tile_names`` and ``describe_tile_object`` faces carry ``tile``.

Selecting
    :func:`select_faces` returns the indices of the faces matching a ``where`` filter (tile, tiles, tag, plane,
    plane_offset_m, a cell rectangle, layer, facing, or a region connected to a cell); it reads only. Feed the
    indices to :func:`paint_faces` or :func:`move_faces`. The blended op is ``select_tile_faces``.

Reloading
    :func:`reload_core` re-executes ``sprytile_core, sprytile_uv, sprytile_builder, spyrite_spec,
    spyrite_probe`` and ``api`` in place so edits to them apply without restarting Blender (the blended op is
    ``reload_core``); registered-class modules still need a restart. Session state held in those modules
    (removed-tileset set, checkpoint registry) is forgotten.

Pattern fills
    :func:`fill_pattern` fills a rectangle or an explicit cell list with a ``random`` (seeded), ``stamp``
    or ``autotile`` (``edges4``, keys ``"0".."15"``) pattern through one :func:`place_tiles` call.

Composite builders
    :func:`build_room` places a floor, a "back" (XZ) and a "left" (YZ) wall and an optional ceiling in one
    call; :func:`extrude_edge` raises a wall along a run of floor cells (sides N and W). Only walls whose
    normal faces the room are expressible with the fixed plane normals (XZ faces -Y, YZ faces +X), so build
    rooms whose open sides face -Y and +X. :func:`move_faces` shifts faces by whole pixels and rebuilds
    their UVs from the tile data stored on them.
"""

import array
import importlib
import itertools
import math
import numbers
import os
import tempfile
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone

import bmesh
import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Matrix, Vector

from . import spyrite_probe
from . import spyrite_spec
from . import sprytile_core
from . import sprytile_uv
from .sprytile_builder import TileBuilder
from .sprytile_uv import UvDataLayers

__all__ = [
    "create_tileset",
    "create_tile_object",
    "place_tiles",
    "fill_tiles",
    "remove_tiles",
    "paint_faces",
    "build_room",
    "extrude_edge",
    "move_faces",
    "describe_tile_object",
    "describe_scene",
    "set_pixel_art_view",
    "verify_tile_object",
    "reload_core",
    "create_overlay_object",
    "fill_pattern",
    "select_faces",
    "build_spec",
    "export_spec",
    "checkpoint",
    "rollback",
    "discard_checkpoint",
    "list_checkpoints",
]

# Modules reload_core re-executes, in dependency order (api last).
RELOADABLE_MODULES = (
    "sprytile_core",
    "sprytile_uv",
    "sprytile_builder",
    "spyrite_spec",
    "spyrite_probe",
    "api",
)


def reload_core():
    """Reload the add-on modules that register no Blender classes, without a restart.

    Reloads ``sprytile_core, sprytile_uv, sprytile_builder, spyrite_spec,
    spyrite_probe, api`` in that order (a module not yet imported, because this
    Blender predates it, is imported instead). Edits to the operator modules
    (``sprytile_utils``, ``sprytile_modal``, ...) still need a restart: their
    classes are registered with Blender.

    ``sprytile_core._removed_tilesets`` is session state and is reset by the
    reload: tilesets removed with the grid "-" button are listed again by the
    next validate_grids. The checkpoint registry is kept (``api`` carries it
    over), so checkpoints taken before the reload can still be rolled back. Call this function through ``api`` only (modules held
    from before the reload keep the old code for names rebound by ``import``).

    Returns ``{"reloaded": [module names]}``.
    """
    import sys

    done = []
    for name in RELOADABLE_MODULES:
        full = f"{__package__}.{name}"
        if full in sys.modules:
            importlib.reload(sys.modules[full])
        else:
            importlib.import_module(full)
        done.append(name)
    return {"reloaded": done}

PLANES = {
    "XY": {"right": (1.0, 0.0, 0.0), "up": (0.0, 1.0, 0.0), "normal": (0.0, 0.0, 1.0), "axis": 2},
    "XZ": {"right": (1.0, 0.0, 0.0), "up": (0.0, 0.0, 1.0), "normal": (0.0, -1.0, 0.0), "axis": 1},
    "YZ": {"right": (0.0, 1.0, 0.0), "up": (0.0, 0.0, 1.0), "normal": (1.0, 0.0, 0.0), "axis": 0},
}
ROTATIONS_DEG = (0, 90, 180, 270)
LAYERS = ("BASE", "DECAL")
# scene.sprytile_data.world_pixels hard range
PIXELS_PER_UNIT_MIN = 8
PIXELS_PER_UNIT_MAX = 2048
# Custom property on tile objects: the pixel density their geometry was built with
PIXELS_PER_UNIT_PROP = "spyrite_pixels_per_unit"
# Custom properties on overlay objects: the base object's name and the lift (metres) toward the viewer
OVERLAY_OF_PROP = "spyrite_overlay_of"
OVERLAY_LIFT_PROP = "spyrite_overlay_lift_m"
# Distance from the plane within which a face counts as lying on it
PLANE_TOLERANCE_M = 1e-4
# Slack when turning face extents into cell indices
CELL_EDGE_EPSILON = 1e-6
NO_TILE_XY = [-1, -1]

# (cos, sin) of the rotation, exact so vectors stay exactly axis aligned
_ROTATION_COS_SIN = {0: (1, 0), 90: (0, 1), 180: (-1, 0), 270: (0, -1)}
_MODE_SET_NAME = {
    "PAINT_VERTEX": "VERTEX_PAINT",
    "PAINT_WEIGHT": "WEIGHT_PAINT",
    "PAINT_TEXTURE": "TEXTURE_PAINT",
}
# scene.sprytile_data fields the API sets while it works and puts back afterwards
_DATA_FIELDS = (
    "uv_flip_x",
    "uv_flip_y",
    "mesh_rotate",
    "paint_align",
    "paint_mode",
    "paint_uv_snap",
    "paint_edge_snap",
    "paint_stretch_x",
    "paint_stretch_y",
    "work_layer",
    "work_layer_mode",
    "world_pixels",
)
_PLACEMENT_KEYS = frozenset(
    {
        "cell_xy",
        "tile_xy",
        "tile",
        "tile_span",
        "plane",
        "plane_offset_m",
        "rotation_deg",
        "flip_x",
        "flip_y",
        "layer",
    }
)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _as_int(name, value):
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        raise ValueError(f"{name} must be an integer, got {value!r}")
    if isinstance(value, float) and not value.is_integer():
        raise ValueError(f"{name} must be an integer, got {value!r}")
    return int(value)


def _as_int_tuple(name, value, length, minimum=None):
    try:
        items = list(value)
    except TypeError:
        raise ValueError(f"{name} must be a sequence of {length} integers, got {value!r}") from None
    if len(items) != length:
        raise ValueError(f"{name} must have exactly {length} integers, got {value!r}")
    ints = tuple(_as_int(f"{name}[{i}]", item) for i, item in enumerate(items))
    if minimum is not None and any(i < minimum for i in ints):
        raise ValueError(f"{name} must be >= {minimum} in every component, got {value!r}")
    return ints


def _as_float(name, value):
    if isinstance(value, bool) or not isinstance(value, numbers.Real) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number, got {value!r}")
    return float(value)


def _as_bool(name, value):
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be true or false, got {value!r}")
    return value


def _as_plane(value):
    if value not in PLANES:
        raise ValueError(f"plane must be one of {sorted(PLANES)}, got {value!r}")
    return value


def _as_rotation(value):
    rotation = _as_float("rotation_deg", value)
    if rotation not in ROTATIONS_DEG:
        raise ValueError(f"rotation_deg must be one of {list(ROTATIONS_DEG)}, got {value!r}")
    return int(rotation)


def _as_layer(value):
    if value not in LAYERS:
        raise ValueError(f"layer must be one of {list(LAYERS)}, got {value!r}")
    return value


def _as_pixels_per_unit(value):
    ppu = _as_int("pixels_per_unit", value)
    if not PIXELS_PER_UNIT_MIN <= ppu <= PIXELS_PER_UNIT_MAX:
        raise ValueError(
            f"pixels_per_unit must be in {PIXELS_PER_UNIT_MIN}..{PIXELS_PER_UNIT_MAX}, got {value!r}"
        )
    return ppu


def _as_name(label, value):
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string, got {value!r}")
    return value


def _mesh_object(object_name):
    _as_name("object_name", object_name)
    obj = bpy.data.objects.get(object_name)
    if obj is None:
        raise ValueError(f"No object named {object_name!r}")
    if obj.type != "MESH":
        raise ValueError(f"Object {object_name!r} is a {obj.type}, not a MESH")
    view_layer = bpy.context.view_layer
    if obj.name not in view_layer.objects:
        raise ValueError(f"Object {object_name!r} is not in the active view layer")
    if not obj.visible_get():
        raise ValueError(f"Object {object_name!r} is hidden; unhide it before editing")
    return obj


# ---------------------------------------------------------------------------
# Tilesets
# ---------------------------------------------------------------------------


class _Tileset:
    """A material's Sprytile grid and image, with the tile layout derived from them."""

    def __init__(self, material, mat_data, grid, image):
        self.material = material
        self.mat_data = mat_data
        self.grid = grid
        self.image = image
        self._names = None
        width, height = image.size
        pitch_x = grid.grid[0] + 2 * grid.padding[0] + grid.margin[1] + grid.margin[3]
        pitch_y = grid.grid[1] + 2 * grid.padding[1] + grid.margin[0] + grid.margin[2]
        self.image_size = (width, height)
        self.pitch = (pitch_x, pitch_y)
        self.columns = width // pitch_x
        self.rows = height // pitch_y
        # Sprytile packs tile ids with this row length (sprytile_uv.apply_uvs)
        self.row_size = math.ceil(width / grid.grid[0])

    def check_tile(self, label, tile_xy, tile_span):
        column, row = tile_xy
        span_x, span_y = tile_span
        if column < 0 or row < 0 or column + span_x > self.columns or row + span_y > self.rows:
            raise ValueError(
                f"{label}: tile_xy {tuple(tile_xy)} with tile_span {tuple(tile_span)} is outside tileset "
                f"{self.material.name!r}: valid columns 0-{self.columns - 1}, "
                f"rows 0-{self.rows - 1} ({self.columns}x{self.rows} tiles)"
            )

    def sprytile_origin(self, tile_xy, tile_span):
        """Bottom-left tile of a span in Sprytile's bottom-origin rows."""
        return tile_xy[0], self.rows - (tile_xy[1] + tile_span[1])

    @property
    def names(self):
        """Tile names from the sidecar next to the image (``{}`` when there is none), loaded once.

        Entries are kept as written: the sidecar belongs to the image, not to one tile layout, so entries
        outside this tileset's grid are not an error here; :func:`_as_tile` range-checks a name when it is used.
        """
        if self._names is None:
            self._names = _load_sidecar(bpy.path.abspath(self.image.filepath))
        return self._names

    @property
    def sidecar(self):
        return spyrite_spec.sidecar_path(bpy.path.abspath(self.image.filepath))

    def tile_names(self):
        """``{name: {xy, planes, tags}}`` as plain copies, safe to hand to callers.

        Lists every sidecar entry, including entries whose ``xy`` lies outside this tileset's layout (a
        sidecar describes the image, which may be loaded with several layouts); using such a name fails.
        """
        return {
            name: {"xy": list(e["xy"]), "planes": None if e["planes"] is None else list(e["planes"]), "tags": list(e["tags"])}
            for name, e in self.names.items()
        }


def _load_sidecar(image_path):
    """Parse the tile-name sidecar of ``image_path`` (``{}`` when there is none); errors name the file."""
    path = spyrite_spec.sidecar_path(image_path)
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as handle:
        try:
            return spyrite_spec.load_tile_names(handle.read())
        except spyrite_spec.SpecError as error:
            raise spyrite_spec.SpecError(f"{path}: {error}") from None


def _as_tile(tileset, label, value, plane=None):
    """Resolve a tile given as ``[column, row]`` or as a name from the tileset's sidecar to a checked tile_xy."""
    if isinstance(value, str):
        names = tileset.names
        if not names:
            raise ValueError(
                f"{label}: tileset {tileset.material.name!r} has no tile names (no {tileset.sidecar} "
                "next to the image); use [column, row]"
            )
        entry = names.get(value)
        if entry is None:
            raise ValueError(
                f"{label}: unknown tile name {value!r} in tileset {tileset.material.name!r}; "
                f"known names: {sorted(names)}"
            )
        if plane is not None and entry["planes"] is not None and plane not in entry["planes"]:
            raise ValueError(
                f"{label}: tile {value!r} is not allowed on plane {plane} (allowed: {', '.join(entry['planes'])})"
            )
        tileset.check_tile(f"{label} (tile {value!r} from {tileset.sidecar})", entry["xy"], (1, 1))
        return tuple(entry["xy"])
    tile_xy = _as_int_tuple(label, value, 2)
    tileset.check_tile(label, tile_xy, (1, 1))
    return tile_xy


def _find_tileset(material_name):
    _as_name("material_name", material_name)
    material = bpy.data.materials.get(material_name)
    if material is None:
        raise ValueError(f"No material named {material_name!r}; call create_tileset first")
    mat_data = sprytile_core.get_mat_data(bpy.context, material.name)
    if mat_data is None or len(mat_data.grids) == 0:
        raise ValueError(f"Material {material_name!r} is not a tileset; call create_tileset first")
    image = sprytile_core.get_material_texture(material)
    if image is None:
        raise ValueError(f"Tileset {material_name!r} has no image texture")
    return _Tileset(material, mat_data, mat_data.grids[0], image)


def _find_tileset_for_image(abs_path, tile_size_px, padding_px, margin_px):
    """The registered tileset built from this image file with this exact layout, or ``None``.

    Compares the resolved path of each tileset material's image texture and its first grid's tile size,
    padding and margin. Tilesets removed with the grid "-" button are skipped, so reuse never resurrects one.
    """
    wanted = os.path.realpath(abs_path)
    layout = (tuple(tile_size_px), tuple(padding_px), tuple(margin_px))
    for mat_data in bpy.context.scene.sprytile_mats:
        material = bpy.data.materials.get(mat_data.mat_id)
        if material is None or len(mat_data.grids) == 0:
            continue
        if material.session_uid in sprytile_core._removed_tilesets:
            continue
        image = sprytile_core.get_material_texture(material)
        if image is None or os.path.realpath(bpy.path.abspath(image.filepath)) != wanted:
            continue
        grid = mat_data.grids[0]
        if (tuple(grid.grid), tuple(grid.padding), tuple(grid.margin)) == layout:
            return _Tileset(material, mat_data, grid, image)
    return None


def create_tileset(material_name, image_path, tile_size_px, padding_px=(0, 0), margin_px=(0, 0, 0, 0)):
    """Register an image as a tileset: a shadeless pixel-art material plus a Sprytile grid.

    A material named ``material_name`` is created (or reused and rebuilt) around the image and given a fake
    user so the tileset survives before any object uses it. Re-running with the same name updates the image
    and grid. Idempotent per image: when no material named ``material_name`` exists but another tileset already
    uses the same image file with the same tile size, padding and margin, no material is created and that
    tileset is returned with ``reused_material`` set to its name. Always use the returned ``material_name``
    afterwards. Returns ``{material_name, image_name, image_size_px, tile_size_px, columns, rows, grid_id,
    reused_material, tile_names}`` (``reused_material`` is ``None`` unless an existing tileset was reused;
    ``tile_names`` is every entry of the image's ``<image>.spyrite.yaml`` sidecar, ``{}`` without one).
    A malformed sidecar raises ``SpecError`` naming the file before anything is created; sidecar entries
    outside this layout are listed but fail only when used (see ``place_tiles``).
    """
    _as_name("material_name", material_name)
    if not isinstance(image_path, (str, os.PathLike)):
        raise ValueError(f"image_path must be a path, got {image_path!r}")
    image_path = os.fspath(image_path)
    if not os.path.isabs(image_path):
        raise ValueError(f"image_path must be absolute, got {image_path!r}")
    if not os.path.isfile(image_path):
        raise ValueError(f"image_path does not exist or is not a file: {image_path!r}")
    tile_size = _as_int_tuple("tile_size_px", tile_size_px, 2, minimum=1)
    padding = _as_int_tuple("padding_px", padding_px, 2, minimum=0)
    margin = _as_int_tuple("margin_px", margin_px, 4, minimum=0)
    _load_sidecar(image_path)  # a bad sidecar must fail before any datablock exists

    scene = bpy.context.scene
    sprytile_core.ensure_scene_setup(scene)

    already_loaded = set(bpy.data.images.keys())
    image = bpy.data.images.load(image_path, check_existing=True)
    if image.name in already_loaded:
        image.reload()
    if image.size[0] == 0 or image.size[1] == 0:
        raise ValueError(f"Blender could not read {image_path!r} as an image")

    pitch = (
        tile_size[0] + 2 * padding[0] + margin[1] + margin[3],
        tile_size[1] + 2 * padding[1] + margin[0] + margin[2],
    )
    columns, rows = image.size[0] // pitch[0], image.size[1] // pitch[1]
    if columns < 1 or rows < 1:
        raise ValueError(
            f"A tile pitch of {pitch[0]}x{pitch[1]} px (tile + 2*padding + margins) does not fit in the "
            f"{image.size[0]}x{image.size[1]} px image"
        )
    if image.size[1] % pitch[1]:
        raise ValueError(
            f"Image height {image.size[1]} px is not a multiple of the tile pitch {pitch[1]} px, so tile rows "
            f"counted from the top would not line up with Sprytile's rows counted from the bottom"
        )

    material = bpy.data.materials.get(material_name)
    if material is None:
        existing = _find_tileset_for_image(image_path, tile_size, padding, margin)
        if existing is not None:
            return {
                "material_name": existing.material.name,
                "image_name": existing.image.name,
                "image_size_px": [image.size[0], image.size[1]],
                "tile_size_px": [tile_size[0], tile_size[1]],
                "columns": columns,
                "rows": rows,
                "grid_id": existing.grid.id,
                "reused_material": existing.material.name,
                "tile_names": existing.tile_names(),
            }
        material = bpy.data.materials.new(material_name)
    material.use_fake_user = True
    sprytile_core.restore_removed_tileset(material)
    sprytile_core.setup_tile_material(material, image)
    sprytile_core.validate_grids(scene)

    mat_data = sprytile_core.get_mat_data(bpy.context, material.name)
    if mat_data is None or len(mat_data.grids) == 0:
        raise RuntimeError(f"Sprytile did not register a grid for material {material.name!r}")
    grid = mat_data.grids[0]
    # The padding setter resizes the grid by the padding delta, so clear it, set the size it will shrink
    # from, then apply the padding: the grid ends up exactly tile_size.
    grid.padding = (0, 0)
    grid.grid = (tile_size[0] + 2 * padding[0], tile_size[1] + 2 * padding[1])
    grid.padding = padding
    grid.margin = margin
    grid.tile_selection = (0, 0, 1, 1)
    if tuple(grid.grid) != tile_size or tuple(grid.padding) != padding:
        raise RuntimeError(
            f"Could not set grid {tile_size} with padding {padding}; Sprytile kept "
            f"{tuple(grid.grid)} / {tuple(grid.padding)}"
        )

    return {
        "material_name": material.name,
        "image_name": image.name,
        "image_size_px": [image.size[0], image.size[1]],
        "tile_size_px": [tile_size[0], tile_size[1]],
        "columns": columns,
        "rows": rows,
        "grid_id": grid.id,
        "reused_material": None,
        "tile_names": _find_tileset(material.name).tile_names(),
    }


def create_tile_object(object_name, material_name, pixels_per_unit):
    """Create an empty mesh object (or reuse an existing mesh object) that builds tiles from a tileset.

    The object gets the tileset's material slot and grid and remembers ``pixels_per_unit``; the scene's
    ``world_pixels`` is set to it too so interactive Sprytile tools agree. Raises ``ValueError`` if the name
    belongs to a non-mesh object. Returns ``{object_name, material_name, grid_id, pixels_per_unit}``.
    """
    _as_name("object_name", object_name)
    ppu = _as_pixels_per_unit(pixels_per_unit)
    scene = bpy.context.scene
    sprytile_core.ensure_scene_setup(scene)
    tileset = _find_tileset(material_name)

    obj = bpy.data.objects.get(object_name)
    if obj is not None and obj.type != "MESH":
        raise ValueError(f"Object {object_name!r} already exists and is a {obj.type}, not a MESH")
    if obj is None:
        obj = bpy.data.objects.new(object_name, bpy.data.meshes.new(object_name))
    if obj.name not in scene.objects:
        scene.collection.objects.link(obj)
        # A freshly linked object only enters the view layer once it has been evaluated
        bpy.context.view_layer.update()

    if tileset.material not in [slot.material for slot in obj.material_slots]:
        obj.data.materials.append(tileset.material)
    obj.sprytile_gridid = tileset.grid.id
    obj[PIXELS_PER_UNIT_PROP] = ppu
    scene.sprytile_data.world_pixels = ppu

    return {
        "object_name": obj.name,
        "material_name": tileset.material.name,
        "grid_id": tileset.grid.id,
        "pixels_per_unit": ppu,
    }


def create_overlay_object(name, base_object_name, material_name, lift_m=0.002):
    """Create a child tile object that overlays ``base_object_name`` (a decal layer as its own object).

    Like :func:`create_tile_object` (same pixel density as the base) but the object is parented to the base
    (keeping the base's current world transform) and remembers ``spyrite_overlay_of`` /
    ``spyrite_overlay_lift_m``. Afterwards ``place_tiles``, ``fill_tiles``, ``fill_pattern`` and
    ``remove_tiles`` on the overlay take the BASE's ``plane_offset_m`` and build ``lift_m`` metres off the
    plane (XY +lift, XZ -lift, YZ +lift: toward the viewer) to avoid z-fighting. Read-back
    (:func:`describe_tile_object`, ``select_faces``) reports the raw lifted offsets, so filter an overlay by
    ``plane_offset_m`` using the lifted value.
    Returns the :func:`create_tile_object` report plus ``overlay_of`` and ``lift_m``.
    """
    _as_name("name", name)
    base = _mesh_object(base_object_name)
    lift = _as_float("lift_m", lift_m)
    if lift <= 0:
        raise ValueError(f"lift_m must be > 0, got {lift}")
    if name == base.name:
        raise ValueError("an overlay needs a name different from its base object")
    existing = bpy.data.objects.get(name)
    if existing is not None and existing.get(OVERLAY_OF_PROP) not in (None, base.name):
        raise ValueError(f"Object {name!r} is already an overlay of {existing.get(OVERLAY_OF_PROP)!r}")
    report = create_tile_object(name, material_name, _object_pixels_per_unit(base, bpy.context.scene))
    obj = bpy.data.objects[report["object_name"]]
    if obj.parent is not base:
        # Re-running on an attached overlay must not reset the parent inverse: the base may have moved
        obj.parent = base
        obj.matrix_parent_inverse = base.matrix_world.inverted()
    obj[OVERLAY_OF_PROP] = base.name
    obj[OVERLAY_LIFT_PROP] = lift
    report["overlay_of"] = base.name
    report["lift_m"] = lift
    return report


def _overlay_lift(obj, plane_name):
    """Metres added to a placement's ``plane_offset_m`` on an overlay object (0.0 for ordinary objects)."""
    if not obj.get(OVERLAY_OF_PROP):
        return 0.0
    plane = PLANES[plane_name]
    return float(obj.get(OVERLAY_LIFT_PROP, 0.002)) * plane["normal"][plane["axis"]]


# ---------------------------------------------------------------------------
# Edit session plumbing
# ---------------------------------------------------------------------------


def _object_pixels_per_unit(obj, scene):
    stored = obj.get(PIXELS_PER_UNIT_PROP)
    if stored is None:
        return scene.sprytile_data.world_pixels
    return int(stored)


def _switch_mode(obj, mode):
    bpy.context.view_layer.objects.active = obj
    if obj.mode != mode:
        bpy.ops.object.mode_set(mode=_MODE_SET_NAME.get(mode, mode))


@contextmanager
def _edit_session(obj):
    """Put obj in EDIT mode; restore modes, selection and active object; roll the mesh back on error."""
    view_layer = bpy.context.view_layer
    previous_active = view_layer.objects.active
    previous_selection = {o.name for o in view_layer.objects if o.select_get()}
    previous_modes = {o.name: o.mode for o in view_layer.objects if o.mode != "OBJECT"}
    backup = bmesh.new()
    failed = False
    try:
        for name in previous_modes:
            _switch_mode(bpy.data.objects[name], "OBJECT")
        backup.from_mesh(obj.data)
        for other in view_layer.objects:
            other.select_set(False)
        obj.select_set(True)
        _switch_mode(obj, "EDIT")
        yield
        bmesh.update_edit_mesh(obj.data, loop_triangles=True, destructive=True)
    except BaseException:
        failed = True
        raise
    finally:
        try:
            if obj.mode != "OBJECT":
                _switch_mode(obj, "OBJECT")
            if failed:
                backup.to_mesh(obj.data)
                obj.data.update()
        finally:
            backup.free()
            # Entering edit mode takes every selected object along, so select one at a time
            for name, mode in previous_modes.items():
                restored = bpy.data.objects.get(name)
                if restored is not None:
                    for other in view_layer.objects:
                        other.select_set(other is restored)
                    _switch_mode(restored, mode)
            for other in view_layer.objects:
                other.select_set(other.name in previous_selection)
            view_layer.objects.active = previous_active


@contextmanager
def _preserved_settings(obj, grid):
    """Yield scene.sprytile_data; put back every field, the grid selection and the object's grid id."""
    data = bpy.context.scene.sprytile_data
    saved = {field: getattr(data, field) for field in _DATA_FIELDS}
    selection = tuple(grid.tile_selection)
    grid_id = obj.sprytile_gridid
    try:
        yield data
    finally:
        for field, value in saved.items():
            setattr(data, field, value)
        grid.tile_selection = selection
        obj.sprytile_gridid = grid_id


def _sprytile_flips(rotation_deg, flip_x, flip_y):
    """Sprytile's (uv_flip_x, uv_flip_y) for the documented orientation.

    The contract mirrors the picture as seen, after turning it. Sprytile mirrors in the tile's own
    frame before turning it. For a quarter turn the tile's horizontal axis is the viewer's vertical
    one, so the two flags trade places; for 0 and 180 degrees they coincide.
    """
    if rotation_deg % 180 == 90:
        return flip_y, flip_x
    return flip_x, flip_y


def _rotated_frame(right, up, rotation_deg):
    """UV-side (right, up) after turning the tile picture counter-clockwise by rotation_deg."""
    cos, sin = _ROTATION_COS_SIN[rotation_deg]
    return right * cos + up * sin, up * cos - right * sin


def _ensure_material_slot(obj, material):
    if material not in [slot.material for slot in obj.material_slots]:
        obj.data.materials.append(material)


def _prepare_builder(obj):
    context = bpy.context
    builder = TileBuilder(context, obj)
    # Creates the Sprytile face layers on a fresh mesh
    builder.update_bmesh_tree(context, True)
    return context, builder


# ---------------------------------------------------------------------------
# Placing tiles
# ---------------------------------------------------------------------------


def _normalize_placement(index, placement, tileset):
    label = f"placements[{index}]"
    if not isinstance(placement, dict):
        raise ValueError(f"{label} must be a dict, got {placement!r}")
    unknown = set(placement) - _PLACEMENT_KEYS
    if unknown:
        raise ValueError(f"{label} has unknown keys {sorted(unknown)}; valid keys are {sorted(_PLACEMENT_KEYS)}")
    if "cell_xy" not in placement:
        raise ValueError(f"{label} is missing required keys ['cell_xy']")
    if ("tile_xy" in placement) == ("tile" in placement):
        raise ValueError(f"{label} needs exactly one of tile_xy or tile")
    plane = _as_plane(placement.get("plane", "XY"))
    tile_key = "tile_xy" if "tile_xy" in placement else "tile"
    normalized = {
        "cell_xy": _as_int_tuple(f"{label}.cell_xy", placement["cell_xy"], 2),
        "tile_xy": _as_tile(tileset, f"{label}.{tile_key}", placement[tile_key], plane),
        "tile_span": _as_int_tuple(f"{label}.tile_span", placement.get("tile_span", (1, 1)), 2, minimum=1),
        "plane": plane,
        "plane_offset_m": _as_float(f"{label}.plane_offset_m", placement.get("plane_offset_m", 0.0)),
        "rotation_deg": _as_rotation(placement.get("rotation_deg", 0.0)),
        "flip_x": _as_bool(f"{label}.flip_x", placement.get("flip_x", False)),
        "flip_y": _as_bool(f"{label}.flip_y", placement.get("flip_y", False)),
        "layer": _as_layer(placement.get("layer", "BASE")),
    }
    tileset.check_tile(label, normalized["tile_xy"], normalized["tile_span"])
    return normalized


class PlacementError(RuntimeError):
    """A placement that passed validation but could not be built; ``placement_index`` is its position in the call."""

    def __init__(self, message, placement_index):
        super().__init__(message)
        self.placement_index = placement_index


def _apply_placements(obj, tileset, placements, clear=False, outcomes=None):
    scene = bpy.context.scene
    ppu = _object_pixels_per_unit(obj, scene)
    grid = tileset.grid
    _ensure_material_slot(obj, tileset.material)

    built = 0
    remapped = 0
    with _preserved_settings(obj, grid) as data, _edit_session(obj):
        context, builder = _prepare_builder(obj)
        obj.sprytile_gridid = grid.id
        data.world_pixels = ppu
        data.paint_mode = "MAKE_FACE"
        data.paint_align = "CENTER"
        data.work_layer_mode = "MESH_DECAL"
        if clear:
            bmesh.ops.delete(builder.bmesh, geom=list(builder.bmesh.faces), context="FACES")
        for index, placement in enumerate(placements):
            plane = PLANES[placement["plane"]]
            decal = placement["layer"] == "DECAL"
            span_x, span_y = placement["tile_span"]
            origin_x, origin_y = tileset.sprytile_origin(placement["tile_xy"], placement["tile_span"])

            data.uv_flip_x, data.uv_flip_y = _sprytile_flips(
                placement["rotation_deg"], placement["flip_x"], placement["flip_y"]
            )
            data.mesh_rotate = math.radians(placement["rotation_deg"])
            data.work_layer = "DECAL_1" if decal else "BASE"
            grid.tile_selection = (origin_x, origin_y, span_x, span_y)

            right = Vector(plane["right"])
            up = Vector(plane["up"])
            normal = Vector(plane["normal"])
            grid_right = right * (grid.grid[0] / ppu)
            grid_up = up * (grid.grid[1] / ppu)
            uv_right, uv_up = _rotated_frame(right, up, placement["rotation_deg"])
            grid_origin = Vector((0.0, 0.0, 0.0))
            grid_origin[plane["axis"]] = placement["plane_offset_m"] + _overlay_lift(obj, placement["plane"])
            # Sprytile joins a multi tile selection into one face whose tile coordinate is the end of the
            # selection; a single tile is passed as is
            if span_x == 1 and span_y == 1:
                tile_coord = (origin_x, origin_y)
            else:
                tile_coord = (origin_x + span_x, origin_y + span_y)

            faces_before = len(builder.bmesh.faces)
            face_index = builder.construct_face(
                context,
                placement["cell_xy"],
                [span_x, span_y],
                tile_coord,
                (origin_x, origin_y),
                grid_up,
                grid_right,
                uv_up,
                uv_right,
                normal,
                require_base_layer=decal,
                work_layer_mask=sprytile_core.get_work_layer_data(data),
                grid_origin=grid_origin,
            )
            faces_after = len(builder.bmesh.faces)
            if face_index is None and faces_after == faces_before:
                raise PlacementError(
                    f"placements[{index}]: could not place tile at cell {placement['cell_xy']} on plane "
                    f"{placement['plane']} at offset {placement['plane_offset_m']} m: "
                    + (
                        "a DECAL needs a BASE tile in that cell, or an existing face there is not coplanar"
                        if decal
                        else "an existing face in that cell is not coplanar with the plane"
                    ),
                    index,
                )
            if outcomes is not None:
                outcomes.append(faces_after > faces_before)
            if faces_after > faces_before:
                built += 1
            else:
                remapped += 1
        face_count = len(builder.bmesh.faces)
    return {"built": built, "remapped": remapped, "face_count": face_count}


def place_tiles(object_name, material_name, placements):
    """Place tiles; each placement builds a new quad at its cell or remaps the coplanar face already there.

    A placement is a dict with ``cell_xy`` and exactly one of ``tile_xy`` / ``tile`` (required; either may
    be ``[column, row]`` or a tile name from the tileset's sidecar, see :func:`_as_tile`) and optionally
    ``tile_span`` (1, 1), ``plane`` 'XY', ``plane_offset_m`` 0.0, ``rotation_deg`` 0, ``flip_x`` False,
    ``flip_y`` False and ``layer`` 'BASE'. A named tile restricted to other planes is refused.
    Placements apply in order; a failure rolls the whole call back.
    Returns ``{built, remapped, face_count}``.
    """
    obj = _mesh_object(object_name)
    tileset = _find_tileset(material_name)
    if isinstance(placements, (str, bytes, dict)) or not hasattr(placements, "__iter__"):
        raise ValueError(f"placements must be a list of dicts, got {type(placements).__name__}")
    placements = list(placements)
    if not placements:
        raise ValueError("placements is empty")
    normalized = [_normalize_placement(i, p, tileset) for i, p in enumerate(placements)]
    sprytile_core.ensure_scene_setup(bpy.context.scene)
    return _apply_placements(obj, tileset, normalized)


def fill_tiles(
    object_name,
    material_name,
    cell_min_xy,
    cell_max_xy,
    tile_xy,
    plane="XY",
    plane_offset_m=0.0,
    rotation_deg=0.0,
    flip_x=False,
    flip_y=False,
    layer="BASE",
):
    """Place the same tile on every cell of the inclusive rectangle ``cell_min_xy..cell_max_xy``.

    Returns ``{built, remapped, face_count}`` like :func:`place_tiles`.
    """
    cell_min = _as_int_tuple("cell_min_xy", cell_min_xy, 2)
    cell_max = _as_int_tuple("cell_max_xy", cell_max_xy, 2)
    if cell_max[0] < cell_min[0] or cell_max[1] < cell_min[1]:
        raise ValueError(f"cell_max_xy {cell_max} must be >= cell_min_xy {cell_min} in both components")
    placements = [
        {
            "cell_xy": (x, y),
            "tile_xy": tile_xy,
            "plane": plane,
            "plane_offset_m": plane_offset_m,
            "rotation_deg": rotation_deg,
            "flip_x": flip_x,
            "flip_y": flip_y,
            "layer": layer,
        }
        for y in range(cell_min[1], cell_max[1] + 1)
        for x in range(cell_min[0], cell_max[0] + 1)
    ]
    return place_tiles(object_name, material_name, placements)


_PATTERN_KINDS = {
    "random": {"kind", "tiles", "weights", "seed"},
    "stamp": {"kind", "rows"},
    "autotile": {"kind", "mask", "tiles"},
}
_TILE_ENTRY_KEYS = {"tile", "rotation_deg", "flip_x", "flip_y"}
_AUTOTILE_BITS = (("N", 1, (0, 1)), ("E", 2, (1, 0)), ("S", 4, (0, -1)), ("W", 8, (-1, 0)))
_PATTERN_ASSIGNMENT_LIMIT = 500


def _pattern_checker(tileset, plane):
    """Validator for every tile a pattern declares, used or not: name/bounds/plane rule, rotation and flips."""

    def check(label, entry):
        _as_tile(tileset, label, entry["tile"], plane)
        try:
            _as_rotation(entry["rotation_deg"])
            _as_bool("flip_x", entry["flip_x"])
            _as_bool("flip_y", entry["flip_y"])
        except ValueError as error:
            raise ValueError(f"{label}: {error}") from None

    return check


def _pattern_tile(label, entry, check):
    """Normalise one pattern tile (a name, ``[c, r]`` or ``{tile, rotation_deg, flip_x, flip_y}``) and validate it."""
    normalized = _pattern_tile_shape(label, entry)
    check(label, normalized)
    return normalized


def _pattern_tile_shape(label, entry):
    if isinstance(entry, dict):
        unknown = set(entry) - _TILE_ENTRY_KEYS
        if unknown or "tile" not in entry:
            raise ValueError(
                f"{label}: a tile object needs 'tile' and may have only {sorted(_TILE_ENTRY_KEYS)}; "
                f"got keys {sorted(entry)}"
            )
        return {
            "tile": entry["tile"],
            "rotation_deg": entry.get("rotation_deg", 0.0),
            "flip_x": entry.get("flip_x", False),
            "flip_y": entry.get("flip_y", False),
        }
    return {"tile": entry, "rotation_deg": 0.0, "flip_x": False, "flip_y": False}


def _pattern_cells(cell_min_xy, cell_max_xy, cells):
    """Fill set as a row-major (y outer, x inner, ascending) list of unique ``(x, y)``."""
    rect = cell_min_xy is not None or cell_max_xy is not None
    if rect == (cells is not None):
        raise ValueError("fill_pattern needs exactly one of (cell_min_xy and cell_max_xy) or cells")
    if rect:
        if cell_min_xy is None or cell_max_xy is None:
            raise ValueError("cell_min_xy and cell_max_xy must be given together")
        lo = _as_int_tuple("cell_min_xy", cell_min_xy, 2)
        hi = _as_int_tuple("cell_max_xy", cell_max_xy, 2)
        if hi[0] < lo[0] or hi[1] < lo[1]:
            raise ValueError(f"cell_max_xy {hi} must be >= cell_min_xy {lo} in both components")
        return [(x, y) for y in range(lo[1], hi[1] + 1) for x in range(lo[0], hi[0] + 1)]
    if isinstance(cells, (str, bytes, dict)) or not hasattr(cells, "__iter__"):
        raise ValueError(f"cells must be a list of [x, y], got {type(cells).__name__}")
    unique = {_as_int_tuple(f"cells[{i}]", c, 2) for i, c in enumerate(cells)}
    if not unique:
        raise ValueError("cells is empty")
    return sorted(unique, key=lambda c: (c[1], c[0]))


def _pattern_assign(pattern, cells, check):
    """One normalised tile entry per cell (same order as ``cells``).

    ``check(label, entry)`` validates EVERY tile the pattern declares (all random tiles, stamp cells and the
    16 autotile entries), so a typo is reported even when the fill never reaches it.
    """
    import random

    if not isinstance(pattern, dict):
        raise ValueError(f"pattern must be a dict, got {type(pattern).__name__}")
    kind = pattern.get("kind")
    if kind not in _PATTERN_KINDS:
        raise ValueError(f"pattern.kind must be one of {sorted(_PATTERN_KINDS)}, got {kind!r}")
    unknown = set(pattern) - _PATTERN_KINDS[kind]
    if unknown:
        raise ValueError(
            f"pattern has unknown keys {sorted(unknown)} for kind {kind!r}; valid keys are "
            f"{sorted(_PATTERN_KINDS[kind])}"
        )
    if kind == "random":
        raw = pattern.get("tiles")
        if isinstance(raw, (str, bytes, dict)) or not hasattr(raw, "__iter__") or not list(raw):
            raise ValueError("pattern.tiles must be a non-empty list of tiles")
        tiles = [_pattern_tile(f"pattern.tiles[{i}]", t, check) for i, t in enumerate(raw)]
        weights = pattern.get("weights")
        if weights is not None:
            if isinstance(weights, (str, bytes, dict)) or not hasattr(weights, "__iter__"):
                raise ValueError("pattern.weights must be a list of numbers")
            weights = [_as_float(f"pattern.weights[{i}]", w) for i, w in enumerate(weights)]
            if len(weights) != len(tiles):
                raise ValueError(f"pattern.weights has {len(weights)} entries but pattern.tiles has {len(tiles)}")
            if any(w <= 0 for w in weights):
                raise ValueError(f"pattern.weights must all be > 0, got {weights}")
        if "seed" not in pattern:
            raise ValueError("pattern.seed is required for kind 'random' (an int; same seed gives the same result)")
        seed = _as_int("pattern.seed", pattern["seed"])
        rng = random.Random(seed)
        return [rng.choices(tiles, weights)[0] for _ in cells]
    if kind == "stamp":
        rows = pattern.get("rows")
        if isinstance(rows, (str, bytes, dict)) or not hasattr(rows, "__iter__"):
            raise ValueError("pattern.rows must be a non-empty list of lists of tiles")
        rows = [list(r) if hasattr(r, "__iter__") and not isinstance(r, (str, bytes, dict)) else r for r in rows]
        if not rows or not all(isinstance(r, list) and r for r in rows):
            raise ValueError("pattern.rows must be a non-empty list of non-empty lists of tiles")
        width = len(rows[0])
        if any(len(r) != width for r in rows):
            raise ValueError(f"pattern.rows must be rectangular; row lengths are {[len(r) for r in rows]}")
        grid = [[_pattern_tile(f"pattern.rows[{j}][{i}]", t, check) for i, t in enumerate(r)] for j, r in enumerate(rows)]
        x0 = min(c[0] for c in cells)
        y1 = max(c[1] for c in cells)
        return [grid[(y1 - y) % len(grid)][(x - x0) % width] for x, y in cells]
    # autotile
    if pattern.get("mask") != "edges4":
        raise ValueError(f"pattern.mask must be 'edges4', got {pattern.get('mask')!r}")
    mapping = pattern.get("tiles")
    if not isinstance(mapping, dict):
        raise ValueError("pattern.tiles must be a dict with the keys '0'..'15'")
    mapping = {str(k): v for k, v in mapping.items()}
    missing = [str(k) for k in range(16) if str(k) not in mapping]
    if missing:
        raise ValueError(f"pattern.tiles is missing autotile keys {missing}; all of '0'..'15' are required")
    extra = sorted(set(mapping) - {str(k) for k in range(16)})
    if extra:
        raise ValueError(f"pattern.tiles has unknown keys {extra}; valid keys are '0'..'15'")
    tiles = {k: _pattern_tile(f"pattern.tiles[{k!r}]", v, check) for k, v in mapping.items()}
    cell_set = set(cells)
    out = []
    for x, y in cells:
        key = sum(bit for _, bit, (dx, dy) in _AUTOTILE_BITS if (x + dx, y + dy) in cell_set)
        out.append(tiles[str(key)])
    return out


def fill_pattern(
    object_name,
    material_name,
    pattern,
    plane="XY",
    plane_offset_m=0.0,
    layer="BASE",
    cell_min_xy=None,
    cell_max_xy=None,
    cells=None,
):
    """Fill cells with a deterministic pattern, applied through one :func:`place_tiles` call.

    The fill set is exactly one of the inclusive rectangle ``cell_min_xy..cell_max_xy`` or the explicit
    ``cells`` list of ``[x, y]``. Cells are visited row-major: ``y`` ascending outer, ``x`` ascending inner.
    Anywhere a tile is accepted in ``pattern`` it may be a tile name, ``[column, row]`` or an object
    ``{tile, rotation_deg, flip_x, flip_y}``. ``pattern["kind"]``:

    ``"random"``: ``tiles`` (>= 1), optional ``weights`` (same length, all > 0), required int ``seed``. One
    ``random.Random(seed)`` is created per call and ``rng.choices(tiles, weights)[0]`` is drawn once per cell
    in visiting order, so the same inputs always give the same result.

    ``"stamp"``: ``rows`` is a rectangular list of lists of tiles. The stamp tiles the fill set anchored at
    its top-left: column ``(x - min_x) % w``, and ``rows[0]`` is the TOP row, i.e. the row with the highest
    ``y`` (``rows[(max_y - y) % h]``), so the stamp reads on the plane as written.

    ``"autotile"``: ``mask`` ``"edges4"`` and ``tiles`` mapping all of ``"0".."15"`` to tiles; the key is the
    sum of N=1 (y+1), E=2 (x+1), S=4 (y-1), W=8 (x-1) for each 4-neighbour inside the fill set. The gen
    server's Wang sheets publish no such key; the ``tiles`` map (names from the sidecar) supplies it.

    Returns ``{built, remapped, face_count, cells, assignments}`` where ``assignments`` is a list of
    ``{cell_xy, tile_xy}`` (the resolved tile; ``null`` with ``truncated: true`` above 500 cells).
    """
    fill_cells = _pattern_cells(cell_min_xy, cell_max_xy, cells)
    _as_plane(plane)
    _as_layer(layer)
    tileset = _find_tileset(material_name)
    entries = _pattern_assign(pattern, fill_cells, _pattern_checker(tileset, plane))
    placements = [
        {
            "cell_xy": cell,
            "tile": entry["tile"],
            "plane": plane,
            "plane_offset_m": plane_offset_m,
            "rotation_deg": entry["rotation_deg"],
            "flip_x": entry["flip_x"],
            "flip_y": entry["flip_y"],
            "layer": layer,
        }
        for cell, entry in zip(fill_cells, entries)
    ]
    report = place_tiles(object_name, material_name, placements)
    report["cells"] = len(fill_cells)
    if len(fill_cells) > _PATTERN_ASSIGNMENT_LIMIT:
        report["assignments"] = None
        report["truncated"] = True
    else:
        report["assignments"] = [
            {
                "cell_xy": list(cell),
                "tile_xy": list(_as_tile(tileset, "tile", entry["tile"], plane)),
            }
            for cell, entry in zip(fill_cells, entries)
        ]
    return report


# ---------------------------------------------------------------------------
# Removing and repainting
# ---------------------------------------------------------------------------


def _object_grid(obj):
    grid = sprytile_core.get_grid(bpy.context, obj.sprytile_gridid)
    if grid is None:
        raise ValueError(f"Object {obj.name!r} has no tileset grid (sprytile_gridid {obj.sprytile_gridid}); "
                         "create it with create_tile_object")
    return grid


def remove_tiles(object_name, plane, plane_offset_m, cells):
    """Delete the faces that cover any of ``cells`` on the plane at ``plane_offset_m``.

    A face is on the plane when all its vertices are within 1e-4 m of it and its normal is along the plane
    normal; it covers the cells its extent overlaps (a single tile covers one). Cell size comes from the
    object's tileset grid and pixel density. Returns ``{removed, face_count}``.
    """
    obj = _mesh_object(object_name)
    plane_name = _as_plane(plane)
    offset = _as_float("plane_offset_m", plane_offset_m) + _overlay_lift(obj, plane_name)
    if isinstance(cells, (str, bytes, dict)) or not hasattr(cells, "__iter__"):
        raise ValueError(f"cells must be a list of [x, y] cells, got {type(cells).__name__}")
    cell_set = {_as_int_tuple(f"cells[{i}]", cell, 2) for i, cell in enumerate(cells)}
    if not cell_set:
        raise ValueError("cells is empty")
    sprytile_core.ensure_scene_setup(bpy.context.scene)
    grid = _object_grid(obj)
    ppu = _object_pixels_per_unit(obj, bpy.context.scene)
    plane_def = PLANES[plane_name]
    right = Vector(plane_def["right"])
    up = Vector(plane_def["up"])
    axis = plane_def["axis"]
    cell_w = grid.grid[0] / ppu
    cell_h = grid.grid[1] / ppu

    removed = 0
    with _edit_session(obj):
        _, builder = _prepare_builder(obj)
        mesh = builder.bmesh
        world = obj.matrix_world
        normal_matrix = world.to_3x3().inverted_safe().transposed()
        doomed = []
        for face in mesh.faces:
            normal = (normal_matrix @ face.normal).normalized()
            if abs(normal[axis]) < 1.0 - 1e-3:
                continue
            verts = [world @ vert.co for vert in face.verts]
            if any(abs(v[axis] - offset) > PLANE_TOLERANCE_M for v in verts):
                continue
            us = [right.dot(v) for v in verts]
            vs = [up.dot(v) for v in verts]
            first_x = math.floor(min(us) / cell_w + CELL_EDGE_EPSILON)
            last_x = math.ceil(max(us) / cell_w - CELL_EDGE_EPSILON) - 1
            first_y = math.floor(min(vs) / cell_h + CELL_EDGE_EPSILON)
            last_y = math.ceil(max(vs) / cell_h - CELL_EDGE_EPSILON) - 1
            if any((x, y) in cell_set for x in range(first_x, last_x + 1) for y in range(first_y, last_y + 1)):
                doomed.append(face)
        removed = len(doomed)
        if doomed:
            bmesh.ops.delete(mesh, geom=doomed, context="FACES")
        for elements in (mesh.faces, mesh.verts, mesh.edges):
            elements.index_update()
            elements.ensure_lookup_table()
        face_count = len(mesh.faces)
    return {"removed": removed, "face_count": face_count}


def _face_frame(world_normal):
    """(right, up) for painting a face of any orientation: up follows world +Z (+Y for floors/ceilings)."""
    reference = Vector((0.0, 0.0, 1.0)) if abs(world_normal.z) < 0.999 else Vector((0.0, 1.0, 0.0))
    up = reference - world_normal * reference.dot(world_normal)
    up.normalize()
    return up.cross(world_normal), up


def paint_faces(object_name, material_name, face_indices, tile_xy, rotation_deg=0.0, flip_x=False, flip_y=False):
    """Remap the UVs of existing faces to one tile of the tileset (and give them its material).

    Each face is oriented by its own normal: the tile's up points along world +Z (or +Y on floors and
    ceilings). ``face_indices`` are mesh face indices as listed by :func:`describe_tile_object`.
    Returns ``{painted, face_count}``.
    """
    obj = _mesh_object(object_name)
    tileset = _find_tileset(material_name)
    if isinstance(face_indices, (str, bytes, dict)) or not hasattr(face_indices, "__iter__"):
        raise ValueError(f"face_indices must be a list of integers, got {type(face_indices).__name__}")
    indices = [_as_int(f"face_indices[{i}]", value) for i, value in enumerate(face_indices)]
    if not indices:
        raise ValueError("face_indices is empty")
    tile = _as_tile(tileset, "paint_faces", tile_xy)
    rotation = _as_rotation(rotation_deg)
    flip_x = _as_bool("flip_x", flip_x)
    flip_y = _as_bool("flip_y", flip_y)
    sprytile_core.ensure_scene_setup(bpy.context.scene)

    scene = bpy.context.scene
    ppu = _object_pixels_per_unit(obj, scene)
    grid = tileset.grid
    origin_x, origin_y = tileset.sprytile_origin(tile, (1, 1))
    _ensure_material_slot(obj, tileset.material)

    with _preserved_settings(obj, grid) as data, _edit_session(obj):
        context, builder = _prepare_builder(obj)
        mesh = builder.bmesh
        face_count = len(mesh.faces)
        for index in indices:
            if not 0 <= index < face_count:
                raise ValueError(f"face index {index} is out of range 0-{face_count - 1}")
        obj.sprytile_gridid = grid.id
        data.world_pixels = ppu
        data.paint_mode = "MAKE_FACE"
        data.paint_align = "CENTER"
        data.work_layer_mode = "MESH_DECAL"
        data.uv_flip_x, data.uv_flip_y = _sprytile_flips(rotation, flip_x, flip_y)
        data.mesh_rotate = math.radians(rotation)
        grid.tile_selection = (origin_x, origin_y, 1, 1)
        normal_matrix = obj.matrix_world.to_3x3().inverted_safe().transposed()
        for index in indices:
            world_normal = (normal_matrix @ mesh.faces[index].normal).normalized()
            right, up = _face_frame(world_normal)
            uv_right, uv_up = _rotated_frame(right, up, rotation)
            painted_index, _ = sprytile_uv.uv_map_face(
                context, uv_up, uv_right, (origin_x, origin_y), (origin_x, origin_y), index, mesh, (1, 1)
            )
            if painted_index is None:
                raise RuntimeError(f"Could not paint face {index}; is it hidden?")
    return {"painted": len(indices), "face_count": face_count}


# ---------------------------------------------------------------------------
# Composite builders
# ---------------------------------------------------------------------------

ROOM_WALLS = ("back", "left")
_ROOM_WALL_PLANE = {"back": "XZ", "left": "YZ"}
_ROOM_WALLS_ERROR = (
    "walls may contain only 'back' and 'left': planes XZ (normal -Y) and YZ (normal +X) are the only wall "
    "planes; build the room so its open sides face -Y and +X"
)
_EXTRUDE_SIDES = ("N", "S", "E", "W")
_WHOLE_CELL_TOLERANCE = 1e-6


def _cell_size_m(obj, tileset):
    """(width, height) in metres of one cell of the tileset on this object."""
    ppu = _object_pixels_per_unit(obj, bpy.context.scene)
    return tileset.grid.grid[0] / ppu, tileset.grid.grid[1] / ppu


def _whole_cells(label, value_m, cell_m):
    """``value_m`` as a whole number of cells of ``cell_m`` metres, or a ValueError naming the fix."""
    cells = value_m / cell_m
    whole = round(cells)
    if abs(cells - whole) > _WHOLE_CELL_TOLERANCE:
        raise ValueError(
            f"{label} {value_m} m is not a whole number of cells ({cell_m} m each); walls start on a cell "
            f"row, so use a multiple of {cell_m}"
        )
    return whole


def _require_square_cells(label, obj, tileset):
    cell_w, cell_h = _cell_size_m(obj, tileset)
    if abs(cell_w - cell_h) > PLANE_TOLERANCE_M:
        raise ValueError(
            f"{label} needs square tiles: on the YZ plane cells are {cell_w} m wide but floor rows are "
            f"{cell_h} m deep, so the wall would not meet the floor; use a tileset with square tiles"
        )


def _placement_parts(obj, tileset, parts):
    """Apply ``parts`` = [(key, [placement dict, ...]), ...] as one rolled-back-on-failure call.

    Returns ``({key: {built, remapped, face_count}}, face_count)``.
    """
    flat = [placement for _, placements in parts for placement in placements]
    normalized = [_normalize_placement(i, p, tileset) for i, p in enumerate(flat)]
    sprytile_core.ensure_scene_setup(bpy.context.scene)
    outcomes = []
    total = _apply_placements(obj, tileset, normalized, outcomes=outcomes)
    reports = {}
    start = 0
    for key, placements in parts:
        built = sum(outcomes[start : start + len(placements)])
        reports[key] = {
            "built": built,
            "remapped": len(placements) - built,
            "face_count": total["face_count"],
        }
        start += len(placements)
    return reports, total["face_count"]


def build_room(
    object_name,
    material_name,
    size_cells,
    floor_tile,
    wall_tile,
    walls=ROOM_WALLS,
    origin_cell=(0, 0),
    floor_offset_m=0.0,
    ceiling_tile=None,
):
    """Build a floor, up to two walls and an optional ceiling in one call.

    ``size_cells`` is ``(width, depth, height)`` in cells (x, y, z); ``origin_cell`` the floor's minimum cell
    ``(ox, oy)``. The floor is the XY rectangle ``[ox, oy]..[ox+width-1, oy+depth-1]`` at ``floor_offset_m``.
    ``"back"`` is the XZ wall at ``y = (oy + depth) * cell_height`` (its -Y normal faces the viewer) over
    x cells ``ox..ox+width-1``; ``"left"`` is the YZ wall at ``x = ox * cell_width`` (normal +X) over y cells
    ``oy..oy+depth-1``. Both span z rows from ``floor_offset_m / cell_height`` (must be a whole number)
    for ``height`` rows. ``ceiling_tile`` adds an XY layer at ``floor_offset_m + height * cell_height``.
    Only those two walls exist because the planes have fixed normals: build the room so its open sides face
    -Y and +X. A ``"left"`` wall needs square tiles. Tiles are ``[column, row]`` or names; ``wall_tile``
    is checked against the XZ and YZ plane rules of each wall built.
    Everything is placed in one call, so a failure rolls the whole room back.
    Returns ``{floor, walls: {name: part}, ceiling: part | None, face_count}`` where a part is
    ``{built, remapped, face_count}`` as for :func:`place_tiles`.
    """
    obj = _mesh_object(object_name)
    tileset = _find_tileset(material_name)
    width, depth, height = _as_int_tuple("size_cells", size_cells, 3, minimum=1)
    ox, oy = _as_int_tuple("origin_cell", origin_cell, 2)
    floor_offset = _as_float("floor_offset_m", floor_offset_m)
    if isinstance(walls, str):
        walls = (walls,)
    try:
        wall_names = list(walls)
    except TypeError:
        raise ValueError(_ROOM_WALLS_ERROR) from None
    if any(name not in ROOM_WALLS for name in wall_names):
        raise ValueError(_ROOM_WALLS_ERROR)
    wall_names = [name for name in ROOM_WALLS if name in wall_names]

    floor = _as_tile(tileset, "floor_tile", floor_tile, "XY")
    wall_tiles = {name: _as_tile(tileset, "wall_tile", wall_tile, _ROOM_WALL_PLANE[name]) for name in wall_names}
    if not wall_names:
        _as_tile(tileset, "wall_tile", wall_tile)
    ceiling = None if ceiling_tile is None else _as_tile(tileset, "ceiling_tile", ceiling_tile, "XY")
    cell_w, cell_h = _cell_size_m(obj, tileset)
    base_row = _whole_cells("floor_offset_m", floor_offset, cell_h) if wall_names else 0
    if "left" in wall_names:
        _require_square_cells("A 'left' wall", obj, tileset)

    def layer(plane, offset, cells, tile):
        return [
            {"cell_xy": cell, "tile_xy": tile, "plane": plane, "plane_offset_m": offset}
            for cell in cells
        ]

    floor_cells = [(x, y) for y in range(oy, oy + depth) for x in range(ox, ox + width)]
    parts = [("floor", layer("XY", floor_offset, floor_cells, floor))]
    for name in wall_names:
        rows = range(base_row, base_row + height)
        if name == "back":
            cells = [(x, z) for z in rows for x in range(ox, ox + width)]
            offset = (oy + depth) * cell_h
        else:
            cells = [(y, z) for z in rows for y in range(oy, oy + depth)]
            offset = ox * cell_w
        parts.append((name, layer(_ROOM_WALL_PLANE[name], offset, cells, wall_tiles[name])))
    if ceiling is not None:
        parts.append(("ceiling", layer("XY", floor_offset + height * cell_h, floor_cells, ceiling)))

    reports, face_count = _placement_parts(obj, tileset, parts)
    return {
        "floor": reports["floor"],
        "walls": {name: reports[name] for name in wall_names},
        "ceiling": reports.get("ceiling"),
        "face_count": face_count,
    }


def extrude_edge(
    object_name,
    material_name,
    plane,
    plane_offset_m,
    from_cell,
    to_cell,
    side,
    height_cells,
    tile,
    rotation_deg=0,
    flip_x=False,
    flip_y=False,
):
    """Raise a wall of ``height_cells`` rows along one edge of a run of floor cells.

    ``plane`` must be ``"XY"`` and ``plane_offset_m`` is the floor's z (a whole number of cell heights): the
    wall's rows start there. ``from_cell`` / ``to_cell`` (inclusive, any order) share a row for side ``"N"``
    (wall on XZ at ``(y + 1) * cell_height``) or a column for side ``"W"`` (wall on YZ at
    ``x * cell_width``, which needs square tiles). ``"S"`` and ``"E"`` would need walls facing +Y / -X, which
    the planes cannot express; build so the open sides face -Y and +X. ``tile`` is ``[column, row]`` or a
    name allowed on the wall plane. Returns ``{built, remapped, face_count, wall_plane, wall_offset_m}``.
    """
    obj = _mesh_object(object_name)
    tileset = _find_tileset(material_name)
    if _as_plane(plane) != "XY":
        raise ValueError(f"plane must be 'XY' (the floor the wall rises from), got {plane!r}")
    offset = _as_float("plane_offset_m", plane_offset_m)
    from_x, from_y = _as_int_tuple("from_cell", from_cell, 2)
    to_x, to_y = _as_int_tuple("to_cell", to_cell, 2)
    if side not in _EXTRUDE_SIDES:
        raise ValueError(f"side must be one of {list(_EXTRUDE_SIDES)}, got {side!r}")
    if side in ("S", "E"):
        facing = "+Y" if side == "S" else "-X"
        raise ValueError(
            f"side {side} needs a {facing}-facing wall, which planes XY/XZ/YZ cannot express; "
            "build so the open sides face -Y and +X"
        )
    height = _as_int("height_cells", height_cells)
    if height < 1:
        raise ValueError(f"height_cells must be >= 1, got {height_cells!r}")
    rotation = _as_rotation(rotation_deg)
    flip_x = _as_bool("flip_x", flip_x)
    flip_y = _as_bool("flip_y", flip_y)
    cell_w, cell_h = _cell_size_m(obj, tileset)
    base_row = _whole_cells("plane_offset_m", offset, cell_h)
    rows = range(base_row, base_row + height)
    if side == "N":
        if from_y != to_y:
            raise ValueError(f"side N needs from_cell and to_cell in the same row, got y {from_y} and {to_y}")
        wall_plane = "XZ"
        wall_offset = (from_y + 1) * cell_h
        cells = [(x, z) for z in rows for x in range(min(from_x, to_x), max(from_x, to_x) + 1)]
    else:
        if from_x != to_x:
            raise ValueError(f"side W needs from_cell and to_cell in the same column, got x {from_x} and {to_x}")
        _require_square_cells("A 'W' wall", obj, tileset)
        wall_plane = "YZ"
        wall_offset = from_x * cell_w
        cells = [(y, z) for z in rows for y in range(min(from_y, to_y), max(from_y, to_y) + 1)]
    tile_xy = _as_tile(tileset, "tile", tile, wall_plane)
    placements = [
        {
            "cell_xy": cell,
            "tile_xy": tile_xy,
            "plane": wall_plane,
            "plane_offset_m": wall_offset,
            "rotation_deg": rotation,
            "flip_x": flip_x,
            "flip_y": flip_y,
        }
        for cell in cells
    ]
    reports, face_count = _placement_parts(obj, tileset, [("wall", placements)])
    return {
        "built": reports["wall"]["built"],
        "remapped": reports["wall"]["remapped"],
        "face_count": face_count,
        "wall_plane": wall_plane,
        "wall_offset_m": round(wall_offset, 6),
    }


# Low byte of paint_settings that sprytile_core.get_paint_settings writes in MAKE_FACE mode: centre align (5)
# and the four toggle bits (4-7) all on
_MAKE_FACE_SETTINGS = 5 + sum(1 << bit for bit in range(4, 8))


def move_faces(object_name, face_indices, delta_px):
    """Translate faces by whole world pixels and re-apply their UVs, so the textures stay consistent.

    ``delta_px`` is ``(dx, dy, dz)`` in integer pixels of the object's pixel density: each moved vertex
    shifts by ``delta_px / pixels_per_unit`` metres in world space. Vertices shared with faces that are not
    moved move too (no edge is split), so neighbours deform as with Blender's G. Each moved face's UVs are
    rebuilt from the tile data stored on it (grid, tile and span, orientation, layer) through the path
    :func:`paint_faces` uses, so ``tile_xy``, ``rotation_deg``, ``flip_x``, ``flip_y`` and ``layer`` read
    back unchanged. A face without tile data is refused (the error names its indices): give it a tile with
    :func:`paint_faces` first. ``face_indices`` are mesh face indices as listed by :func:`describe_tile_object`.

    A face on an axis plane is rebuilt in that plane's fixed frame, as :func:`place_tiles` builds it, whichever
    way its normal points; any other face uses the :func:`paint_faces` frame. The paint mode stored on the
    face is restored: faces whose settings are Sprytile's make-face signature (centre align, every toggle on;
    everything this API places) are rebuilt like placements, any other settings like the Paint tool painted
    them (alignment, UV and edge snap, stretch). A Paint-tool face painted with exactly the make-face
    signature cannot be told apart and is rebuilt as a placement.
    Returns ``{moved, face_count}``.
    """
    obj = _mesh_object(object_name)
    if isinstance(face_indices, (str, bytes, dict)) or not hasattr(face_indices, "__iter__"):
        raise ValueError(f"face_indices must be a list of integers, got {type(face_indices).__name__}")
    indices = [_as_int(f"face_indices[{i}]", value) for i, value in enumerate(face_indices)]
    if not indices:
        raise ValueError("face_indices is empty")
    delta = _as_int_tuple("delta_px", delta_px, 3)
    sprytile_core.ensure_scene_setup(bpy.context.scene)

    scene = bpy.context.scene
    ppu = _object_pixels_per_unit(obj, scene)
    world_delta = Vector(delta) / ppu
    local_delta = obj.matrix_world.to_3x3().inverted_safe() @ world_delta
    normal_matrix = obj.matrix_world.to_3x3().inverted_safe().transposed()
    unique = sorted(set(indices))

    with _edit_session(obj):
        context, builder = _prepare_builder(obj)
        mesh = builder.bmesh
        face_count = len(mesh.faces)
        for index in unique:
            if not 0 <= index < face_count:
                raise ValueError(f"face index {index} is out of range 0-{face_count - 1}")
        grid_layer = mesh.faces.layers.int.get(UvDataLayers.GRID_INDEX)
        tile_layer = mesh.faces.layers.int.get(UvDataLayers.GRID_TILE_ID)
        width_layer = mesh.faces.layers.int.get(UvDataLayers.GRID_SEL_WIDTH)
        height_layer = mesh.faces.layers.int.get(UvDataLayers.GRID_SEL_HEIGHT)
        origin_layer = mesh.faces.layers.int.get(UvDataLayers.GRID_SEL_ORIGIN)
        paint_layer = mesh.faces.layers.int.get(UvDataLayers.PAINT_SETTINGS)
        work_layer = mesh.faces.layers.int.get(UvDataLayers.WORK_LAYER)
        tileset_cache = {}
        plans = []
        untiled = []
        for index in unique:
            face = mesh.faces[index]
            tileset = _cached_tileset(obj, face[grid_layer], tileset_cache)
            tile_id = face[tile_layer]
            origin_id = face[origin_layer]
            stored = face[paint_layer]
            # Layers a face never had read as zeros (grid 0 is a real grid, so the grid alone proves nothing)
            if tileset is None or (
                stored == 0 and face[width_layer] == 0 and face[height_layer] == 0 and tile_id == 0 and origin_id in (0, -1)
            ):
                untiled.append(index)
                continue
            span_x = max(1, face[width_layer])
            span_y = max(1, face[height_layer])
            # Same fallbacks as _face_tile_xy: data from before origin/width/height existed
            if origin_id == -1 or (origin_id == 0 and face[height_layer] == 0 and face[width_layer] == 0):
                origin_id = tile_id
            row_size = tileset.row_size
            origin = (origin_id % row_size, origin_id // row_size)
            rotation, orient_x, orient_y = _decode_orientation(stored)
            # Same tile coordinate as _apply_placements: a span's is the end of the selection, which can
            # run past the last column, so it cannot be read back from the stored (wrapped) tile id
            if span_x == 1 and span_y == 1:
                tile_coord = origin
            else:
                tile_coord = (origin[0] + span_x, origin[1] + span_y)
            plans.append(
                {
                    "index": index,
                    "tileset": tileset,
                    "tile_coord": tile_coord,
                    "origin": origin,
                    "span": (span_x, span_y),
                    "rotation": rotation,
                    "flips": _sprytile_flips(rotation, orient_x, orient_y),
                    "decal": face[work_layer] != 0,
                    "paint_settings": stored,
                }
            )
        if untiled:
            raise ValueError(
                f"faces {untiled} have no tile data (no tileset grid); give them a tile with paint_faces first"
            )

        with ExitStack() as stack:
            grids = {}
            for plan in plans:
                grids.setdefault(plan["tileset"].grid.id, plan["tileset"].grid)
            for grid in grids.values():
                data = stack.enter_context(_preserved_settings(obj, grid))
            data.world_pixels = ppu
            data.work_layer_mode = "MESH_DECAL"

            moved_verts = {vert for index in unique for vert in mesh.faces[index].verts}
            for vert in moved_verts:
                vert.co += local_delta
            mesh.normal_update()

            for plan in plans:
                grid = plan["tileset"].grid
                span_x, span_y = plan["span"]
                origin_x, origin_y = plan["origin"]
                obj.sprytile_gridid = grid.id
                data.paint_align = "CENTER"
                if plan["paint_settings"] & 0xFF == _MAKE_FACE_SETTINGS:
                    data.paint_mode = "MAKE_FACE"
                else:
                    data.paint_mode = "PAINT"
                    sprytile_core.from_paint_settings(data, plan["paint_settings"])
                data.uv_flip_x, data.uv_flip_y = plan["flips"]
                data.mesh_rotate = math.radians(plan["rotation"])
                data.work_layer = "DECAL_1" if plan["decal"] else "BASE"
                grid.tile_selection = (origin_x, origin_y, span_x, span_y)
                world_normal = (normal_matrix @ mesh.faces[plan["index"]].normal).normalized()
                face_plane, _ = _face_plane(world_normal)
                if face_plane is None:
                    right, up = _face_frame(world_normal)
                else:
                    right, up = Vector(PLANES[face_plane]["right"]), Vector(PLANES[face_plane]["up"])
                uv_right, uv_up = _rotated_frame(right, up, plan["rotation"])
                painted_index, _ = sprytile_uv.uv_map_face(
                    context,
                    uv_up,
                    uv_right,
                    plan["tile_coord"],
                    plan["origin"],
                    plan["index"],
                    mesh,
                    plan["span"],
                )
                if painted_index is None:
                    raise RuntimeError(f"Could not remap the UVs of face {plan['index']}; is it hidden?")
    return {"moved": len(unique), "face_count": face_count}



# ---------------------------------------------------------------------------
# Reading back
# ---------------------------------------------------------------------------


def _cached_tileset(obj, grid_id, tileset_cache):
    """The _Tileset of a grid id (cached per call), or None when the grid or its image is gone."""
    if grid_id not in tileset_cache:
        grid = sprytile_core.get_grid(bpy.context, grid_id)
        image = sprytile_core.get_grid_texture(obj, grid) if grid is not None else None
        material = sprytile_core.get_grid_material(grid) if image is not None else None
        mat_data = sprytile_core.get_mat_data(bpy.context, material.name) if material is not None else None
        if grid is None or image is None or material is None or mat_data is None:
            tileset_cache[grid_id] = None
        else:
            tileset_cache[grid_id] = _Tileset(material, mat_data, grid, image)
    return tileset_cache[grid_id]


def _face_tile_xy(obj, mesh, face, layers, tileset_cache):
    """(column from left, row from top) of a face's tile, or NO_TILE_XY when it carries no tile data."""
    grid_id_layer, tile_id_layer, width_layer, height_layer, origin_layer = layers[:5]
    if grid_id_layer is None or tile_id_layer is None:
        return list(NO_TILE_XY)
    tileset = _cached_tileset(obj, face[grid_id_layer], tileset_cache)
    if tileset is None:
        return list(NO_TILE_XY)

    tile_id = face[tile_id_layer]
    width = face[width_layer] if width_layer is not None else 1
    height = face[height_layer] if height_layer is not None else 1
    origin_id = face[origin_layer] if origin_layer is not None else -1
    # Same fallbacks as sprytile_modal get_face_tiledata: data from before origin/width/height existed
    if origin_id == -1 or (origin_id == 0 and height == 0 and width == 0):
        origin_id = tile_id
    height = max(1, height)
    row_from_bottom = origin_id // tileset.row_size
    column = origin_id % tileset.row_size
    return [column, tileset.rows - 1 - (row_from_bottom + height - 1)]


def _decode_orientation(paint_settings):
    """(rotation_deg, flip_x, flip_y) of a face's ``paint_settings`` bitmask; inverse of ``_sprytile_flips``.

    Bits 10-11 hold the turn (0, 3, 2, 1 for 0, 90, 180, 270 degrees), bit 9 Sprytile's ``uv_flip_x`` and
    bit 8 ``uv_flip_y`` (``sprytile_core.get_paint_settings``). Sprytile mirrors before turning, so for a
    quarter turn the two flags trade places back to the documented mirror-as-seen flips.
    """
    rotation = {0: 0, 3: 90, 2: 180, 1: 270}[(paint_settings >> 10) & 3]
    sprytile_x = bool((paint_settings >> 9) & 1)
    sprytile_y = bool((paint_settings >> 8) & 1)
    if rotation % 180 == 90:
        return rotation, sprytile_y, sprytile_x
    return rotation, sprytile_x, sprytile_y


def _face_plane(normal):
    """(plane, facing) of a world normal: 'XY'/'XZ'/'YZ' and +1 along the plane's normal, -1 against it.

    (None, 0) when the normal is not axis aligned.
    """
    axis = max(range(3), key=lambda k: abs(normal[k]))
    if abs(normal[axis]) < 0.999:
        return None, 0
    plane = {2: "XY", 1: "XZ", 0: "YZ"}[axis]
    return plane, 1 if normal[axis] * PLANES[plane]["normal"][axis] > 0 else -1


def _face_cell(verts_world, plane, cell_w, cell_h):
    """((column, row), plane_offset_m, on_grid) of a face's world vertices on ``plane``.

    The cell is the one holding the face's minimum corner. ``on_grid`` means the face is a rectangle whose
    corners all sit on cell boundaries, all on one plane offset: what ``place_tiles`` builds.
    """
    spec = PLANES[plane]
    axis = spec["axis"]
    right = [sum(v[i] * spec["right"][i] for i in range(3)) for v in verts_world]
    up = [sum(v[i] * spec["up"][i] for i in range(3)) for v in verts_world]
    offsets = [v[axis] for v in verts_world]
    offset = round(sum(offsets) / len(offsets), 6)
    min_right, max_right = min(right), max(right)
    min_up, max_up = min(up), max(up)
    cell = (
        math.floor(min_right / cell_w + CELL_EDGE_EPSILON),
        math.floor(min_up / cell_h + CELL_EDGE_EPSILON),
    )

    def on_boundary(low, high, size):
        extent = high - low
        count = round(extent / size)
        return (
            count >= 1
            and abs(extent - count * size) <= PLANE_TOLERANCE_M
            and abs(low - round(low / size) * size) <= PLANE_TOLERANCE_M
        )

    corners = all(
        (abs(r - min_right) <= PLANE_TOLERANCE_M or abs(r - max_right) <= PLANE_TOLERANCE_M)
        and (abs(u - min_up) <= PLANE_TOLERANCE_M or abs(u - max_up) <= PLANE_TOLERANCE_M)
        for r, u in zip(right, up)
    )
    on_grid = (
        len(verts_world) == 4
        and corners
        and on_boundary(min_right, max_right, cell_w)
        and on_boundary(min_up, max_up, cell_h)
        and all(abs(o - offset) <= PLANE_TOLERANCE_M for o in offsets)
    )
    return cell, offset, on_grid


def _tile_name(tileset, tile_xy):
    """Name of the tile at ``tile_xy`` (first sidecar entry whose ``xy`` equals it), or None."""
    if tileset is None:
        return None
    for name, entry in tileset.names.items():
        if entry["xy"] == list(tile_xy):
            return name
    return None


def describe_tile_object(object_name, max_faces=500):
    """Read back the geometry and orientation of a tile object.

    Returns ``{object_name, overlay_of, face_count, truncated, faces}`` where each face (the first ``max_faces`` by index)
    is a dict in world space with:

    - ``index``, ``center_m``, ``normal`` and ``material`` (the face's material name, '' if none);
    - ``tile_xy``: (column from left, row from top) of the face's tile (of the top-left tile for multi tile
      faces) or [-1, -1] for a face without tile data; ``tile_span`` [columns, rows] of the tiles it shows;
    - ``rotation_deg``, ``flip_x``, ``flip_y``: the orientation exactly as given to ``place_tiles`` (0, False,
      False when the face has no paint settings);
    - ``layer``: 'BASE' or 'DECAL';
    - ``plane`` ('XY', 'XZ', 'YZ', or None when the face is not axis aligned), ``facing`` (+1 along the
      plane's normal, -1 against it, 0 without plane), ``plane_offset_m`` (the plane coordinate the face lies
      at; a decal reports its lifted offset), ``cell_xy`` (cell holding the face's minimum corner, None
      without plane) and ``on_grid`` (true for a rectangle aligned to whole cells on one offset, i.e. what
      ``place_tiles`` builds; false for hand modelled geometry);
    - ``tileset``: material name of the face's tileset ('' if none); ``tile``: the tile's name from the
      tileset's sidecar (None when unnamed).
    Works in any mode without changing it.
    """
    obj = _mesh_object(object_name)
    max_faces = _as_int("max_faces", max_faces)
    if max_faces < 0:
        raise ValueError(f"max_faces must be >= 0, got {max_faces}")

    if obj.mode == "EDIT":
        mesh = bmesh.from_edit_mesh(obj.data)
        free = False
    else:
        mesh = bmesh.new()
        mesh.from_mesh(obj.data)
        free = True
    try:
        layers = (
            mesh.faces.layers.int.get(UvDataLayers.GRID_INDEX),
            mesh.faces.layers.int.get(UvDataLayers.GRID_TILE_ID),
            mesh.faces.layers.int.get(UvDataLayers.GRID_SEL_WIDTH),
            mesh.faces.layers.int.get(UvDataLayers.GRID_SEL_HEIGHT),
            mesh.faces.layers.int.get(UvDataLayers.GRID_SEL_ORIGIN),
        )
        paint_layer = mesh.faces.layers.int.get(UvDataLayers.PAINT_SETTINGS)
        work_layer = mesh.faces.layers.int.get(UvDataLayers.WORK_LAYER)
        world = obj.matrix_world
        normal_matrix = world.to_3x3().inverted_safe().transposed()
        slots = obj.material_slots
        ppu = _object_pixels_per_unit(obj, bpy.context.scene)
        tileset_cache = {}
        object_tileset = _cached_tileset(obj, obj.sprytile_gridid, tileset_cache)
        faces = []
        face_count = len(mesh.faces)
        for face in mesh.faces:
            if len(faces) >= max_faces:
                break
            center = world @ face.calc_center_bounds()
            normal = (normal_matrix @ face.normal).normalized()
            slot_material = slots[face.material_index].material if face.material_index < len(slots) else None
            tileset = None
            if layers[0] is not None:
                tileset = _cached_tileset(obj, face[layers[0]], tileset_cache)
            width = max(1, face[layers[2]]) if layers[2] is not None else 1
            height = max(1, face[layers[3]]) if layers[3] is not None else 1
            if paint_layer is not None:
                rotation_deg, flip_x, flip_y = _decode_orientation(face[paint_layer])
            else:
                rotation_deg, flip_x, flip_y = 0, False, False
            layer = "DECAL" if work_layer is not None and face[work_layer] != 0 else "BASE"
            plane, facing = _face_plane(normal)
            cell_xy = None
            plane_offset_m = None
            on_grid = False
            if plane is not None:
                grid = (tileset or object_tileset).grid if (tileset or object_tileset) is not None else None
                verts_world = [world @ v.co for v in face.verts]
                if grid is not None:
                    cell, plane_offset_m, on_grid = _face_cell(
                        verts_world, plane, grid.grid[0] / ppu, grid.grid[1] / ppu
                    )
                    cell_xy = list(cell)
                else:
                    plane_offset_m = round(
                        sum(v[PLANES[plane]["axis"]] for v in verts_world) / len(verts_world), 6
                    )
            tile_xy = _face_tile_xy(obj, mesh, face, layers, tileset_cache)
            faces.append(
                {
                    "index": face.index,
                    "center_m": [round(c, 6) for c in center],
                    "normal": [round(c, 6) for c in normal],
                    "tile_xy": tile_xy,
                    "tile_span": [width, height],
                    "rotation_deg": rotation_deg,
                    "flip_x": flip_x,
                    "flip_y": flip_y,
                    "layer": layer,
                    "plane": plane,
                    "facing": facing,
                    "plane_offset_m": plane_offset_m,
                    "cell_xy": cell_xy,
                    "on_grid": on_grid,
                    "tileset": tileset.material.name if tileset is not None else "",
                    "tile": _tile_name(tileset, tile_xy),
                    "material": slot_material.name if slot_material is not None else "",
                }
            )
    finally:
        if free:
            mesh.free()
    return {
        "object_name": obj.name,
        "overlay_of": obj.get(OVERLAY_OF_PROP) or None,
        "face_count": face_count,
        "truncated": face_count > len(faces),
        "faces": faces,
    }


_SELECT_KEYS = (
    "tile", "tiles", "tag", "plane", "plane_offset_m", "cell_min_xy", "cell_max_xy", "layer", "facing",
    "connected_to_cell",
)


def select_faces(object_name, where):
    """Select faces of a tile object by what they show and where they are (read-only).

    ``where`` is a dict whose keys are all optional and ANDed; an empty dict selects every face:

    - ``tile`` (a name from the tileset's sidecar or ``[column, row]``) / ``tiles`` (a list of either): faces
      whose tile (the span origin for multi tile faces) is one of them in the object's tileset (names resolve
      there, and ``[column, row]`` is a tile of that same tileset: faces of another tileset on the object
      never match, even at the same coordinates);
    - ``tag``: faces whose tile is any sidecar entry carrying that tag;
    - ``plane`` ('XY', 'XZ', 'YZ'), ``plane_offset_m`` (within 1e-4 m), ``layer`` ('BASE'/'DECAL'),
      ``facing`` (1 or -1);
    - ``cell_min_xy`` + ``cell_max_xy`` (both or neither): faces whose ``cell_xy`` is inside the inclusive
      rectangle (cells are on the plane the face lies on, so combine with ``plane``);
    - ``connected_to_cell`` ``[x, y]``: the 4-connected region of on-grid faces on ``plane`` (required) at one
      offset that show the same tile as the face at that cell, flooding from it (a multi tile face counts as
      its minimum cell). Without ``plane_offset_m`` the offset of the face found at the cell is used (an error
      when faces at several offsets sit there); ``layer`` and ``facing`` narrow the start face and are kept
      by the flood. The region is then ANDed with the other keys.

    Works from :func:`describe_tile_object` output in any mode. The result feeds ``paint_faces`` (a non
    contiguous fill). Returns ``{"face_indices": sorted list, "count"}``.
    """
    if not isinstance(where, dict):
        raise ValueError(f"where must be a dict with keys from {list(_SELECT_KEYS)}, got {type(where).__name__}")
    unknown = sorted(set(where) - set(_SELECT_KEYS))
    if unknown:
        raise ValueError(f"where: unknown keys {unknown}; valid keys: {list(_SELECT_KEYS)}")
    obj = _mesh_object(object_name)
    object_tileset = _cached_tileset(obj, obj.sprytile_gridid, {})

    def tile_of(label, value):
        if object_tileset is None:
            raise ValueError(f"{label}: object {obj.name!r} has no tileset; create it with create_tile_object")
        return list(_as_tile(object_tileset, label, value))

    wanted_tiles = None
    if where.get("tile") is not None:
        wanted_tiles = [tile_of("where.tile", where["tile"])]
    if where.get("tiles") is not None:
        if not isinstance(where["tiles"], (list, tuple)):
            raise ValueError("where.tiles must be a list of tile names or [column, row]")
        tiles = [tile_of(f"where.tiles[{i}]", v) for i, v in enumerate(where["tiles"])]
        wanted_tiles = tiles if wanted_tiles is None else [t for t in wanted_tiles if t in tiles]
    tag = where.get("tag")
    if tag is not None:
        _as_name("where.tag", tag)
    plane = _as_plane(where["plane"]) if where.get("plane") is not None else None
    offset = _as_float("where.plane_offset_m", where["plane_offset_m"]) if where.get("plane_offset_m") is not None else None
    layer = _as_layer(where["layer"]) if where.get("layer") is not None else None
    facing = None
    if where.get("facing") is not None:
        facing = _as_int("where.facing", where["facing"])
        if facing not in (1, -1):
            raise ValueError(f"where.facing must be 1 or -1, got {facing}")
    rect = None
    if (where.get("cell_min_xy") is None) != (where.get("cell_max_xy") is None):
        raise ValueError("where: cell_min_xy and cell_max_xy must be given together")
    if where.get("cell_min_xy") is not None:
        low = _as_int_tuple("where.cell_min_xy", where["cell_min_xy"], 2)
        high = _as_int_tuple("where.cell_max_xy", where["cell_max_xy"], 2)
        if high[0] < low[0] or high[1] < low[1]:
            raise ValueError(f"where: cell_max_xy {list(high)} must be >= cell_min_xy {list(low)}")
        rect = (low, high)
    start = None
    if where.get("connected_to_cell") is not None:
        if plane is None:
            raise ValueError("where.connected_to_cell needs where.plane (the plane the cell lies on)")
        start = _as_int_tuple("where.connected_to_cell", where["connected_to_cell"], 2)

    face_count = describe_tile_object(object_name, max_faces=0)["face_count"]
    faces = describe_tile_object(object_name, max_faces=face_count)["faces"]

    def near(a, b):
        return a is not None and abs(a - b) <= PLANE_TOLERANCE_M

    region = None
    if start is not None:
        at_start = [
            f for f in faces
            if f["plane"] == plane and f["on_grid"] and tuple(f["cell_xy"]) == start
            and (offset is None or near(f["plane_offset_m"], offset))
            and (layer is None or f["layer"] == layer)
            and (facing is None or f["facing"] == facing)
        ]
        if not at_start:
            raise ValueError(f"where.connected_to_cell: no on-grid face at cell {list(start)} on plane {plane}")
        offsets = sorted({f["plane_offset_m"] for f in at_start})
        merged = [o for i, o in enumerate(offsets) if i == 0 or not near(o, offsets[i - 1])]
        if len(merged) > 1:
            raise ValueError(
                f"where.connected_to_cell: faces at cell {list(start)} lie at several plane offsets {offsets}; "
                "pass plane_offset_m (and layer) to choose one"
            )
        first = at_start[0]
        by_cell = {}
        for f in faces:
            if (f["plane"] == plane and f["on_grid"] and f["tile_xy"] == first["tile_xy"]
                    and f["tileset"] == first["tileset"] and f["layer"] == first["layer"]
                    and f["facing"] == first["facing"] and near(f["plane_offset_m"], first["plane_offset_m"])):
                by_cell.setdefault(tuple(f["cell_xy"]), []).append(f["index"])
        seen = {start}
        queue = [start]
        while queue:
            x, y = queue.pop()
            for neighbour in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if neighbour in by_cell and neighbour not in seen:
                    seen.add(neighbour)
                    queue.append(neighbour)
        region = {i for cell in seen for i in by_cell[cell]}

    tags_cache = {}

    def has_tag(face):
        material = face["tileset"]
        if material not in tags_cache:
            tags_cache[material] = _find_tileset(material).names if material else {}
        return any(tag in e["tags"] and e["xy"] == face["tile_xy"] for e in tags_cache[material].values())

    selected = []
    for f in faces:
        if region is not None and f["index"] not in region:
            continue
        if wanted_tiles is not None and (f["tileset"] != object_tileset.material.name or f["tile_xy"] not in wanted_tiles):
            continue
        if tag is not None and not has_tag(f):
            continue
        if plane is not None and f["plane"] != plane:
            continue
        if offset is not None and not near(f["plane_offset_m"], offset):
            continue
        if layer is not None and f["layer"] != layer:
            continue
        if facing is not None and f["facing"] != facing:
            continue
        if rect is not None:
            cell = f["cell_xy"]
            if cell is None or not (rect[0][0] <= cell[0] <= rect[1][0] and rect[0][1] <= cell[1] <= rect[1][1]):
                continue
        selected.append(f["index"])
    return {"face_indices": sorted(selected), "count": len(selected)}


def describe_scene():
    """Read back every tileset and tile object of the scene.

    Returns ``{tilesets, tile_objects, removed_tilesets, settings}``:

    - ``tilesets``: one per ``scene.sprytile_mats`` entry that has a grid and an image texture:
      ``{material_name, image_name, image_path (absolute), image_size_px, tile_size_px, padding_px,
      margin_px, columns, rows, grid_id, tile_names}`` (``tile_names`` as in :func:`create_tileset`);
    - ``tile_objects``: every mesh object of the view layer bound to a tileset:
      ``{object_name, material_name, grid_id, pixels_per_unit, face_count, location_m, overlay_of}``
      (``overlay_of`` is the base object's name for overlays made by :func:`create_overlay_object`, else None);
    - ``removed_tilesets``: material names hidden with the grid "-" button this session;
    - ``settings``: ``{world_pixels, mesh_decal_offset, auto_merge}`` of ``scene.sprytile_data``.
    """
    context = bpy.context
    scene = context.scene
    sprytile_core.ensure_scene_setup(scene)

    tilesets = []
    for mat_data in scene.sprytile_mats:
        material = bpy.data.materials.get(mat_data.mat_id)
        if material is None or len(mat_data.grids) == 0:
            continue
        image = sprytile_core.get_material_texture(material)
        if image is None:
            continue
        tileset = _Tileset(material, mat_data, mat_data.grids[0], image)
        grid = tileset.grid
        tilesets.append(
            {
                "material_name": material.name,
                "image_name": image.name,
                "image_path": bpy.path.abspath(image.filepath),
                "image_size_px": list(tileset.image_size),
                "tile_size_px": list(grid.grid),
                "padding_px": list(grid.padding),
                "margin_px": list(grid.margin),
                "columns": tileset.columns,
                "rows": tileset.rows,
                "grid_id": grid.id,
                "tile_names": tileset.tile_names(),
            }
        )

    tile_objects = []
    for obj in context.view_layer.objects:
        if obj.type != "MESH" or obj.sprytile_gridid == -1:
            continue
        grid = sprytile_core.get_grid(context, obj.sprytile_gridid)
        material = sprytile_core.get_grid_material(grid) if grid is not None else None
        tile_objects.append(
            {
                "object_name": obj.name,
                "material_name": material.name if material is not None else "",
                "grid_id": obj.sprytile_gridid,
                "pixels_per_unit": _object_pixels_per_unit(obj, scene),
                "face_count": len(obj.data.polygons) if obj.mode != "EDIT" else len(bmesh.from_edit_mesh(obj.data).faces),
                "location_m": [round(c, 6) for c in obj.matrix_world.translation],
                "overlay_of": obj.get(OVERLAY_OF_PROP) or None,
            }
        )

    removed = [
        material.name
        for material in bpy.data.materials
        if material.session_uid in sprytile_core._removed_tilesets
    ]
    data = scene.sprytile_data
    return {
        "tilesets": tilesets,
        "tile_objects": tile_objects,
        "removed_tilesets": removed,
        "settings": {
            "world_pixels": data.world_pixels,
            "mesh_decal_offset": data.mesh_decal_offset,
            "auto_merge": data.auto_merge,
        },
    }


# ---------------------------------------------------------------------------
# Declarative specs
# ---------------------------------------------------------------------------


def _read_spec_file(spec_path):
    if not isinstance(spec_path, (str, os.PathLike)):
        raise ValueError(f"spec_path must be a path, got {spec_path!r}")
    spec_path = os.fspath(spec_path)
    if not os.path.isabs(spec_path):
        raise ValueError(f"spec_path must be absolute, got {spec_path!r}")
    if not os.path.isfile(spec_path):
        raise ValueError(f"spec_path does not exist or is not a file: {spec_path!r}")
    with open(spec_path, encoding="utf-8") as handle:
        return spec_path, handle.read()


def _spec_placements(spec_object, tileset):
    """(placements, origins) of an object spec: fills expanded cell by cell, then tiles; origins name each in the spec."""
    placements = []
    origins = []
    for i, fill in enumerate(spec_object["fills"]):
        low, high = fill["cell_min_xy"], fill["cell_max_xy"]
        for y in range(low[1], high[1] + 1):
            for x in range(low[0], high[0] + 1):
                placements.append(
                    {"cell_xy": (x, y), "tile": fill["tile"]}
                    | {k: fill[k] for k in ("plane", "plane_offset_m", "rotation_deg", "flip_x", "flip_y", "layer")}
                )
                origins.append(f"fills[{i}]")
    for i, tile in enumerate(spec_object["tiles"]):
        placements.append({k: v for k, v in tile.items() if k != "cell_xy"} | {"cell_xy": tuple(tile["cell_xy"])})
        origins.append(f"tiles[{i}]")
    for i, entry in enumerate(spec_object.get("patterns", ())):
        try:
            cells = _pattern_cells(entry.get("cell_min_xy"), entry.get("cell_max_xy"), entry.get("cells"))
            assigned = _pattern_assign(entry["pattern"], cells, _pattern_checker(tileset, entry["plane"]))
        except ValueError as error:
            message = str(error)
            if message.startswith("pattern."):
                message = message.replace("pattern.", f"patterns[{i}].", 1)
            else:
                message = f"patterns[{i}]: {message}"
            raise spyrite_spec.SpecError(message) from None
        for cell, tile in zip(cells, assigned):
            placements.append(
                {"cell_xy": cell, "tile": tile["tile"]}
                | {k: tile[k] for k in ("rotation_deg", "flip_x", "flip_y")}
                | {k: entry[k] for k in ("plane", "plane_offset_m", "layer")}
            )
            origins.append(f"patterns[{i}]")
    return placements, origins


@contextmanager
def _object_restored_on_failure(object_name):
    """On error put the named object back as it was before the block, then re-raise.

    An object the block created is removed (with its mesh); an existing mesh object gets its pixel density,
    grid id and material slots back, and the scene's ``world_pixels`` is restored either way. The mesh itself
    is rolled back by :func:`_edit_session`.
    """
    data = bpy.context.scene.sprytile_data
    world_pixels = data.world_pixels
    obj = bpy.data.objects.get(object_name)
    existing = None
    if obj is not None and obj.type == "MESH":
        existing = {
            "ppu": obj.get(PIXELS_PER_UNIT_PROP),
            "grid_id": obj.sprytile_gridid,
            "slots": len(obj.data.materials),
            "linked": obj.name in bpy.context.scene.objects,
        }
    try:
        yield
    except BaseException:
        data.world_pixels = world_pixels
        obj = bpy.data.objects.get(object_name)
        if obj is not None:
            if existing is None:
                mesh = obj.data
                bpy.data.objects.remove(obj, do_unlink=True)
                if mesh is not None and mesh.users == 0:
                    bpy.data.meshes.remove(mesh)
            else:
                while len(obj.data.materials) > existing["slots"]:
                    obj.data.materials.pop(index=len(obj.data.materials) - 1)
                obj.sprytile_gridid = existing["grid_id"]
                if existing["ppu"] is None:
                    obj.pop(PIXELS_PER_UNIT_PROP, None)
                else:
                    obj[PIXELS_PER_UNIT_PROP] = existing["ppu"]
                if not existing["linked"] and obj.name in bpy.context.scene.objects:
                    bpy.context.scene.collection.objects.unlink(obj)
        raise


def build_spec(spec_path):
    """Build a scene from a YAML spec file (see ``spyrite_spec.validate_spec`` for the schema).

    ``spec_path`` must be absolute. Tileset ``image`` paths are relative to the spec file's directory (or
    absolute). Steps: parse and validate the spec, create every tileset (:func:`create_tileset`, idempotent
    per image), resolve every tile name and check every placement against its tileset, and only then create
    each object (:func:`create_tile_object`) and apply, in one edit session per object, ``clear`` (delete all
    its faces first), the ``fills`` (as :func:`fill_tiles`), the ``tiles`` (as :func:`place_tiles`) and the ``patterns`` (as
    :func:`fill_pattern`), in that order. A failure while placing restores that object to what it was (its faces, pixel density, grid,
    material slots and the scene's ``world_pixels``; an object this call created is removed); tilesets and
    objects built before the failure stay. A placement that validates but cannot be built raises
    ``RuntimeError`` naming its spec entry (``objects.room.tiles[0]: could not place tile ...``). Errors are ``ValueError`` (``SpecError`` for spec problems, starting with the
    dotted path of the bad value, e.g. ``objects.room.tiles[3].tile: ...``).

    Returns ``{spec_path, tilesets: [create_tileset report per tileset], objects: [{object_name, built,
    remapped, face_count}]}``.
    """
    spec_path, text = _read_spec_file(spec_path)
    spec = spyrite_spec.validate_spec(spyrite_spec.load_spec(text))
    base_dir = os.path.dirname(spec_path)

    for name, entry in spec["tilesets"].items():
        image = os.path.normpath(os.path.join(base_dir, os.path.expanduser(entry["image"])))
        if not os.path.isfile(image):
            raise spyrite_spec.SpecError(f"tilesets.{name}.image: no such file {image!r} (relative paths start at {base_dir!r})")
        entry["image_path"] = image
    for name, entry in spec["objects"].items():
        try:
            _as_pixels_per_unit(entry["pixels_per_unit"])
        except ValueError as error:
            raise spyrite_spec.SpecError(f"objects.{name}.pixels_per_unit: {error}") from None

    tileset_reports = {}
    for name, entry in spec["tilesets"].items():
        tileset_reports[name] = create_tileset(
            name, entry["image_path"], entry["tile_size_px"], entry["padding_px"], entry["margin_px"]
        )

    plans = []
    for name, entry in spec["objects"].items():
        material_name = tileset_reports[entry["tileset"]]["material_name"]
        tileset = _find_tileset(material_name)
        try:
            placements, origins = _spec_placements(entry, tileset)
        except spyrite_spec.SpecError as error:
            raise spyrite_spec.SpecError(f"objects.{name}.{error}") from None
        normalized = []
        for index, placement in enumerate(placements):
            try:
                normalized.append(_normalize_placement(index, placement, tileset))
            except ValueError as error:
                message = str(error).replace(f"placements[{index}]", f"objects.{name}.{origins[index]}", 1)
                raise spyrite_spec.SpecError(message) from None
        plans.append((name, entry, tileset, normalized, origins))

    sprytile_core.ensure_scene_setup(bpy.context.scene)
    objects = []
    for name, entry, tileset, normalized, origins in plans:
        with _object_restored_on_failure(name):
            report = create_tile_object(name, tileset.material.name, entry["pixels_per_unit"])
            obj = _mesh_object(report["object_name"])
            try:
                result = _apply_placements(obj, tileset, normalized, clear=entry["clear"])
            except PlacementError as error:
                message = str(error).replace(
                    f"placements[{error.placement_index}]", f"objects.{name}.{origins[error.placement_index]}", 1
                )
                raise RuntimeError(message) from None
        objects.append({"object_name": obj.name, **result})
    return {"spec_path": spec_path, "tilesets": list(tileset_reports.values()), "objects": objects}


def _relative_image(spec_dir, image_path):
    """``./rel/path`` when the image lies under the spec's directory, else the absolute path."""
    relative = os.path.relpath(image_path, spec_dir)
    if relative.startswith("..") or os.path.isabs(relative):
        return image_path
    return "./" + relative.replace(os.sep, "/")


def export_spec(object_names, spec_path):
    """Write the named tile objects as a YAML spec that :func:`build_spec` rebuilds.

    Each exported face becomes one ``tiles`` entry (``plane``, ``plane_offset_m``, ``cell``, ``tile`` as a
    sidecar name allowed on the plane when there is one else ``[column, row]``, ``tile_span``,
    ``rotation_deg``, ``flip_x``, ``flip_y``, ``layer``). A decal's ``plane_offset_m`` is written as its
    base's offset (the lift is undone); base faces are listed before decals. Only the tilesets the objects
    use are written, keyed by material name, with ``image`` relative to the spec's directory when under it.
    ``clear`` is not written. Faces that cannot be rebuilt from placements (not a whole-cell rectangle, facing
    against the plane's normal, no tile data, or from a tileset other than the object's) are left out and
    reported. An overlay object (:func:`create_overlay_object`) is written at its base's offsets (the
    overlay lift is undone like the decal lift); a spec cannot express overlays, so :func:`build_spec` into
    the same scene re-applies the lift on the existing overlay, while a fresh scene gets an ordinary object
    at the base's offsets (recreate it with :func:`create_overlay_object` first to keep the lift).
    Repeated ``object_names`` are exported once. ``spec_path`` must be absolute; missing parent directories
    are created.

    Returns ``{spec_path, objects: n, tiles: n, unexported_faces: {object_name: [face indices]}}``.
    """
    if isinstance(object_names, (str, bytes)) or not hasattr(object_names, "__iter__"):
        raise ValueError(f"object_names (op: objects) must be a list of object names, got {object_names!r}")
    object_names = list(dict.fromkeys(object_names))
    if not object_names:
        raise ValueError("object_names (op: objects) is empty; name at least one tile object")
    if not isinstance(spec_path, (str, os.PathLike)):
        raise ValueError(f"spec_path must be a path, got {spec_path!r}")
    spec_path = os.fspath(spec_path)
    if not os.path.isabs(spec_path):
        raise ValueError(f"spec_path must be absolute, got {spec_path!r}")
    spec_dir = os.path.dirname(spec_path)

    scene = describe_scene()
    lift = scene["settings"]["mesh_decal_offset"]
    tile_objects = {o["object_name"]: o for o in scene["tile_objects"]}
    tilesets = {t["material_name"]: t for t in scene["tilesets"]}
    for name in object_names:
        _as_name("object_names[]", name)
        if name not in tile_objects:
            raise ValueError(f"No tile object named {name!r}; tile objects are {sorted(tile_objects)}")

    spec_tilesets = {}
    spec_objects = {}
    unexported = {}
    exported_tiles = 0
    for name in object_names:
        info = tile_objects[name]
        tileset = tilesets.get(info["material_name"])
        if tileset is None:
            raise ValueError(f"Object {name!r} has no tileset with an image; nothing to export")
        described = describe_tile_object(name, max_faces=info["face_count"])
        exported_object = _mesh_object(name)
        entries = []
        skipped = []
        for face in described["faces"]:
            tile_xy = face["tile_xy"]
            if (
                not face["on_grid"]
                or face["facing"] != 1
                or tile_xy[0] < 0
                or face["tileset"] != tileset["material_name"]
            ):
                skipped.append(face["index"])
                continue
            tile = list(tile_xy)
            for tile_name, entry in tileset["tile_names"].items():
                if entry["xy"] == tile_xy and (entry["planes"] is None or face["plane"] in entry["planes"]):
                    tile = tile_name
                    break
            offset = face["plane_offset_m"] - _overlay_lift(exported_object, face["plane"])
            if face["layer"] == "DECAL":
                offset -= lift * PLANES[face["plane"]]["normal"][PLANES[face["plane"]]["axis"]]
            offset = round(offset, 6)
            entries.append(
                (
                    face["layer"] == "DECAL",
                    {
                        "plane": face["plane"],
                        "plane_offset_m": offset,
                        "cell": list(face["cell_xy"]),
                        "tile": tile,
                        "tile_span": list(face["tile_span"]),
                        "rotation_deg": face["rotation_deg"],
                        "flip_x": face["flip_x"],
                        "flip_y": face["flip_y"],
                        "layer": face["layer"],
                    },
                )
            )
        entries.sort(key=lambda item: item[0])
        if skipped:
            unexported[name] = skipped
        exported_tiles += len(entries)
        spec_tilesets.setdefault(
            tileset["material_name"],
            {
                "image": _relative_image(spec_dir, tileset["image_path"]),
                "tile_size_px": list(tileset["tile_size_px"]),
                "padding_px": list(tileset["padding_px"]),
                "margin_px": list(tileset["margin_px"]),
            },
        )
        spec_objects[name] = {
            "tileset": tileset["material_name"],
            "pixels_per_unit": info["pixels_per_unit"],
            "tiles": [entry for _, entry in entries],
        }

    spec = {"spyrite_spec": spyrite_spec.SPEC_VERSION, "tilesets": spec_tilesets, "objects": spec_objects}
    spyrite_spec.validate_spec(spec)
    os.makedirs(spec_dir, exist_ok=True)
    with open(spec_path, "w", encoding="utf-8") as handle:
        handle.write(spyrite_spec.dump_spec(spec))
    return {
        "spec_path": spec_path,
        "objects": len(spec_objects),
        "tiles": exported_tiles,
        "unexported_faces": unexported,
    }


def _render_still():
    bpy.ops.render.render(write_still=True)


def _apply_pixel_art_scene_settings(scene):
    scene.display.shading.color_type = "TEXTURE"
    scene.display.shading.light = "FLAT"
    scene.display.render_aa = "OFF"
    scene.view_settings.view_transform = "Standard"


# view -> plane the camera looks at (down the plane's -normal)
_VERIFY_VIEW_PLANE = {"top": "XY", "front": "XZ", "right": "YZ"}
_VERIFY_PLANE_VIEW = {plane: view for view, plane in _VERIFY_VIEW_PLANE.items()}
VERIFY_CELL_PX = 32  # on-screen pixels per tile cell the verify render aims for
VERIFY_MIN_CELL_PX = 8  # below this a cell has no inner quadrants worth measuring
VERIFY_MAX_RESOLUTION = 2048
VERIFY_FRAME_MARGIN = 1.05


def _image_picture(image):
    """(width, height, rgba floats, top row first) of a Blender image; Blender stores rows bottom first."""
    width, height = image.size
    if width == 0 or height == 0:
        raise ValueError(f"Image {image.name!r} has no pixels; reload it or check its file path")
    flat = array.array("f", bytes(width * height * 16))
    image.pixels.foreach_get(flat)
    stride = width * 4
    rows = array.array("f")
    for y in range(height - 1, -1, -1):
        rows.extend(flat[y * stride:(y + 1) * stride])
    return (width, height, rows)


def _tile_pixel_box(tileset, tile_xy, tile_span):
    """Pixel box (x0, y0, x1, y1; y down) of a tile or span in the tileset image, the way the UVs address it."""
    grid = tileset.grid
    pitch_x, pitch_y = tileset.pitch
    _, height = tileset.image_size
    column, bottom_row = tileset.sprytile_origin(tile_xy, tile_span)
    width_px = (tile_span[0] - 1) * pitch_x + grid.grid[0]
    height_px = (tile_span[1] - 1) * pitch_y + grid.grid[1]
    x0 = column * pitch_x + grid.padding[0]
    y_bottom = bottom_row * pitch_y + grid.padding[1]
    y0 = height - y_bottom - height_px
    return (x0, y0, x0 + width_px, y0 + height_px)


def _render_blocking_collection(scene, obj):
    """Name of the collection keeping ``obj`` out of renders, or None when some collection path renders it.

    A collection with ``hide_render`` hides everything under it; an object linked into several collections
    still renders through any path without one.
    """
    reasons = []

    def walk(collection, blocker):
        if blocker is None and collection.hide_render:
            blocker = collection.name
        if obj.name in collection.objects:
            reasons.append(blocker)
        for child in collection.children:
            walk(child, blocker)

    walk(scene.collection, None)
    if reasons and all(reason is not None for reason in reasons):
        return reasons[0]
    return None


def verify_tile_object(object_name, view="auto", tolerance=12.0, evidence_dir=None):
    """Render a tile object with Workbench and check every visible face shows the tile its data claims.

    The pixel oracle of ``scripts/visual_probe.py`` run from inside Blender: for each face looking at the
    camera, the colours of its four quadrants in the render must match the four quadrants of its tile in the
    tileset image, turned and mirrored per the face's ``rotation_deg``/``flip_x``/``flip_y``. The expected
    side comes from the face's tile data (``describe_tile_object``) and the tileset image's pixels, never
    from its UVs, so a face whose UVs show another tile than its data says is reported.

    ``view`` is ``'top'`` (plane XY, camera above), ``'front'`` (XZ, camera on the -Y side), ``'right'`` (YZ,
    camera on the +X side) or ``'auto'``: the plane holding most faces. ``tolerance`` is the largest accepted
    channel difference, on a 0-255 scale. Faces are judged when they look at the camera, are whole-cell
    rectangles with tile data (``on_grid``) and are not hidden under a nearer face (a decal hides its base;
    so does a nearer face of the object that looks away from the camera or is slanted).
    The scene is rendered with ``BLENDER_WORKBENCH`` using the ``set_pixel_art_view`` values, an orthographic
    camera framing the object (1.05 x the larger view extent) and every other object hidden from the render,
    at a resolution of at least 32 px per cell (at most 2048 px; a cell must still get 8 px, else a
    ``ValueError`` asks you to verify a smaller object). Output settings that would change the picture are
    neutralised for the render: image output (also a video ``media_type``), colour management of the output,
    view transform, look, exposure, gamma, curve mapping, display device, stamp, render border, compositor,
    sequencer, dither and transparent film. A target excluded from renders by its collection's ``hide_render``
    raises a ``ValueError`` naming the collection (the object's own ``hide_render`` is overridden).

    Everything touched (render and shading settings, camera, hidden objects, mode) is put back and the
    temporary camera and image datablocks are removed, so the scene is unchanged; the render is written to
    ``evidence_dir/render.png`` (a new temporary directory when ``evidence_dir`` is None; must be absolute).

    Returns ``{ok, measured, mismatches, max_channel_delta, evidence_dir, render_path}`` where ``measured``
    counts the judged faces, ``mismatches`` lists ``{index, plane, cell_xy, tile_xy, max_channel_delta}`` of
    the faces over tolerance, and ``ok`` is true only when at least one face was judged and none mismatched.
    Raises ``RuntimeError`` when Blender cannot render with Workbench (for example without a GPU); run
    ``make test-visual`` from the host then.
    """
    obj = _mesh_object(object_name)
    if view != "auto" and view not in _VERIFY_VIEW_PLANE:
        raise ValueError(f"view must be one of ['auto', 'front', 'right', 'top'], got {view!r}")
    tolerance = _as_float("tolerance", tolerance)
    if tolerance < 0:
        raise ValueError(f"tolerance must be >= 0, got {tolerance}")
    if evidence_dir is not None:
        _as_name("evidence_dir", evidence_dir)
        if not os.path.isabs(evidence_dir):
            raise ValueError(f"evidence_dir must be an absolute path, got {evidence_dir!r}")
    grid = sprytile_core.get_grid(bpy.context, obj.sprytile_gridid)
    if grid is None:
        raise ValueError(f"Object {obj.name!r} has no tileset grid (sprytile_gridid {obj.sprytile_gridid}); "
                         f"call create_tile_object first")
    blocker = _render_blocking_collection(bpy.context.scene, obj)
    if blocker is not None:
        raise ValueError(
            f"Object {obj.name!r} is not rendered: collection {blocker!r} has hide_render set; "
            f"enable it in the render (or move the object to a rendered collection) before verifying"
        )

    scene = bpy.context.scene
    view_layer = bpy.context.view_layer
    previous_active = view_layer.objects.active
    previous_mode = obj.mode
    if previous_mode != "OBJECT":
        _switch_mode(obj, "OBJECT")
    try:
        report = describe_tile_object(object_name, max_faces=len(obj.data.polygons))
        if not report["faces"]:
            raise ValueError(f"Object {obj.name!r} has no faces to verify; place tiles first")
        faces = report["faces"]
        if view == "auto":
            counts = {plane: sum(1 for f in faces if f["plane"] == plane) for plane in PLANES}
            view = _VERIFY_PLANE_VIEW[max(PLANES, key=lambda plane: counts[plane])]
        plane = PLANES[_VERIFY_VIEW_PLANE[view]]
        toward_camera = Vector(plane["normal"])
        right, up = Vector(plane["right"]), Vector(plane["up"])

        ppu = _object_pixels_per_unit(obj, scene)
        cell_m = min(grid.grid[0], grid.grid[1]) / ppu
        world = obj.matrix_world
        corners = [world @ Vector(c) for c in obj.bound_box]
        centre = sum(corners, Vector()) / len(corners)
        extent_right = max(c.dot(right) for c in corners) - min(c.dot(right) for c in corners)
        extent_up = max(c.dot(up) for c in corners) - min(c.dot(up) for c in corners)
        depth = [c.dot(toward_camera) for c in corners]
        ortho_scale = max(extent_right, extent_up, cell_m) * VERIFY_FRAME_MARGIN
        resolution = max(64, math.ceil(VERIFY_CELL_PX * ortho_scale / cell_m))
        resolution = min(resolution, VERIFY_MAX_RESOLUTION)
        if resolution * cell_m / ortho_scale < VERIFY_MIN_CELL_PX:
            raise ValueError(
                f"Object {obj.name!r} spans {ortho_scale / cell_m:.0f} cells across the {view} view; "
                f"at {VERIFY_MAX_RESOLUTION} px a cell gets under {VERIFY_MIN_CELL_PX} px. "
                f"Verify a smaller object"
            )

        if evidence_dir is None:
            evidence_dir = tempfile.mkdtemp(prefix="spyrite_verify_")
        else:
            os.makedirs(evidence_dir, exist_ok=True)
        render_path = os.path.join(evidence_dir, "render.png")

        shading, render, view_settings = scene.display.shading, scene.render, scene.view_settings
        saved = {
            "scene": {name: getattr(scene, name) for name in ("camera",)},
            "shading": {name: getattr(shading, name) for name in (
                "color_type", "light", "show_object_outline", "show_cavity", "show_shadows", "show_xray")},
            "display": {"render_aa": scene.display.render_aa},
            "render": {name: getattr(render, name) for name in (
                "engine", "resolution_x", "resolution_y", "resolution_percentage", "pixel_aspect_x",
                "pixel_aspect_y", "filepath", "use_file_extension", "film_transparent", "use_compositing",
                "use_sequencer", "use_stamp", "use_border", "dither_intensity")},
            # media_type first: a video scene only accepts the video file formats until it is set to IMAGE
            "image_settings": {name: getattr(render.image_settings, name) for name in (
                "media_type", "file_format", "color_mode", "color_depth", "color_management")},
            "view_settings": {name: getattr(view_settings, name) for name in (
                "view_transform", "look", "exposure", "gamma", "use_curve_mapping")},
            "display_settings": {"display_device": scene.display_settings.display_device},
        }
        targets = {
            "scene": scene, "shading": shading, "display": scene.display, "render": render,
            "image_settings": render.image_settings, "view_settings": view_settings,
            "display_settings": scene.display_settings,
        }
        hidden_before = {other: other.hide_render for other in scene.objects}
        camera_data = camera_object = image = None
        try:
            for other in scene.objects:
                other.hide_render = other is not obj
            render.engine = "BLENDER_WORKBENCH"
            _apply_pixel_art_scene_settings(scene)
            shading.show_object_outline = False
            shading.show_cavity = False
            shading.show_shadows = False
            shading.show_xray = False
            view_settings.look = "None"
            view_settings.exposure = 0.0
            view_settings.gamma = 1.0
            view_settings.use_curve_mapping = False
            scene.display_settings.display_device = "sRGB"
            render.resolution_x = render.resolution_y = resolution
            render.resolution_percentage = 100
            render.pixel_aspect_x = render.pixel_aspect_y = 1.0
            render.film_transparent = False
            render.use_compositing = False
            render.use_sequencer = False
            render.use_stamp = False
            render.use_border = False
            render.dither_intensity = 0.0
            render.use_file_extension = False
            render.filepath = render_path
            render.image_settings.media_type = "IMAGE"
            render.image_settings.file_format = "PNG"
            render.image_settings.color_management = "FOLLOW_SCENE"
            render.image_settings.color_mode = "RGB"
            render.image_settings.color_depth = "8"

            camera_data = bpy.data.cameras.new(".spyrite_verify")
            camera_data.type = "ORTHO"
            camera_data.ortho_scale = ortho_scale
            camera_data.sensor_fit = "AUTO"
            camera_data.clip_start = 0.01
            camera_data.clip_end = (max(depth) - min(depth)) + 4.0
            camera_object = bpy.data.objects.new(".spyrite_verify", camera_data)
            scene.collection.objects.link(camera_object)
            # Camera looks down -normal with the plane's up as its up: image x = plane right, y = plane up
            frame = Matrix((right, up, toward_camera)).transposed().to_4x4()
            eye = centre + toward_camera * (max(depth) - centre.dot(toward_camera) + 2.0)
            camera_object.matrix_world = Matrix.Translation(eye) @ frame
            scene.camera = camera_object

            try:
                _render_still()
                if not os.path.exists(render_path):
                    raise RuntimeError(f"no image was written to {render_path}")
            except Exception as err:
                raise RuntimeError(
                    f"verify_tile_object could not render with Workbench: {err}; "
                    f"run `make test-visual` from the host instead"
                ) from err

            image = bpy.data.images.load(render_path, check_existing=False)
            if tuple(image.size) != (resolution, resolution):
                raise RuntimeError(
                    f"verify_tile_object could not render with Workbench: wrote a {tuple(image.size)} image, "
                    f"expected {resolution}x{resolution}; run `make test-visual` from the host instead"
                )
            shot = _image_picture(image)

            # Nearest face first: a face whose measured region lies under a nearer one is not judged. Every
            # face covers what is behind it (Workbench draws back faces too); only faces looking at the
            # camera are judged themselves.
            mesh = obj.data
            candidates = []
            for face in faces:
                points = [
                    world_to_camera_view(scene, camera_object, world @ mesh.vertices[i].co)
                    for i in mesh.polygons[face["index"]].vertices
                ]
                xs = [p.x * resolution for p in points]
                ys = [(1.0 - p.y) * resolution for p in points]
                box = (min(xs), min(ys), max(xs), max(ys))
                faces_camera = Vector(face["normal"]).dot(toward_camera) > 0.9
                candidates.append((Vector(face["center_m"]).dot(toward_camera), face, box, faces_camera))
            candidates.sort(key=lambda item: -item[0])

            tilesets, tile_pictures = {}, {}
            nearer = []  # (depth, box)
            measured = 0
            max_delta = 0.0
            mismatches = []
            for face_depth, face, box, faces_camera in candidates:
                hidden = spyrite_probe.covered(box, [b for d, b in nearer if d > face_depth + 1e-6])
                nearer.append((face_depth, box))
                if not faces_camera or hidden or not face["on_grid"] or face["tile_xy"] == NO_TILE_XY or not face["tileset"]:
                    continue
                if box[0] < 0 or box[1] < 0 or box[2] > resolution or box[3] > resolution:
                    continue
                if face["tileset"] not in tilesets:
                    tilesets[face["tileset"]] = _find_tileset(face["tileset"])
                    tile_pictures[face["tileset"]] = _image_picture(tilesets[face["tileset"]].image)
                tileset = tilesets[face["tileset"]]
                tile_box = _tile_pixel_box(tileset, face["tile_xy"], face["tile_span"])
                tile_picture = spyrite_probe.crop(tile_pictures[face["tileset"]], tile_box)
                expected = spyrite_probe.expected_quadrants(
                    tile_picture, face["rotation_deg"], face["flip_x"], face["flip_y"]
                )
                delta = spyrite_probe.compare(expected, spyrite_probe.observed_quadrants(shot, box))
                measured += 1
                max_delta = max(max_delta, delta)
                if delta > tolerance:
                    mismatches.append(
                        {
                            "index": face["index"],
                            "plane": face["plane"],
                            "cell_xy": face["cell_xy"],
                            "tile_xy": face["tile_xy"],
                            "max_channel_delta": round(delta, 1),
                        }
                    )
        finally:
            for key, values in saved.items():
                for name, value in values.items():
                    setattr(targets[key], name, value)
            for other, hide in hidden_before.items():
                other.hide_render = hide
            if camera_object is not None:
                bpy.data.objects.remove(camera_object)
            if camera_data is not None:
                bpy.data.cameras.remove(camera_data)
            if image is not None:
                bpy.data.images.remove(image)
    finally:
        if obj.mode != previous_mode:
            _switch_mode(obj, previous_mode)
        if previous_active is not None and view_layer.objects.active is not previous_active:
            view_layer.objects.active = previous_active

    mismatches.sort(key=lambda item: item["index"])
    return {
        "ok": measured > 0 and not mismatches,
        "measured": measured,
        "mismatches": mismatches,
        "max_channel_delta": round(max_delta, 1),
        "evidence_dir": evidence_dir,
        "render_path": render_path,
    }

# A reload re-executes this module in its existing namespace, so these keep their value across
# ``reload_core``: the registry is plain data plus references to the hidden meshes, which survive too.
_CHECKPOINTS = globals().get("_CHECKPOINTS", {})
_CHECKPOINT_IDS = globals().get("_CHECKPOINT_IDS", itertools.count(1))


def _mesh_alive(mesh):
    """False once Blender freed the datablock (deleted by hand, or the file was reloaded)."""
    try:
        mesh.name
    except ReferenceError:
        return False
    return True


def _drop_checkpoint(checkpoint_id):
    """Forget a checkpoint and remove the hidden meshes of it that still exist; returns its object names."""
    entry = _CHECKPOINTS.pop(checkpoint_id)
    for saved in entry["objects"].values():
        if _mesh_alive(saved["mesh"]):
            bpy.data.meshes.remove(saved["mesh"])
    return list(entry["objects"])


def _checkpoint_lost_objects(entry):
    """Names of the objects of a checkpoint whose saved mesh copy no longer exists."""
    return [name for name, saved in entry["objects"].items() if not _mesh_alive(saved["mesh"])]


def _prune_lost_checkpoints():
    """Drop every checkpoint that lost a mesh copy, so listings only show checkpoints that can be restored."""
    for checkpoint_id in [i for i, entry in _CHECKPOINTS.items() if _checkpoint_lost_objects(entry)]:
        _drop_checkpoint(checkpoint_id)


def _checkpoint_entry(checkpoint_id, restorable=True):
    if isinstance(checkpoint_id, bool) or not isinstance(checkpoint_id, numbers.Integral):
        raise ValueError(f"checkpoint_id must be an integer, got {checkpoint_id!r}")
    entry = _CHECKPOINTS.get(int(checkpoint_id))
    if entry is None:
        raise ValueError(
            f"No checkpoint {checkpoint_id}; existing checkpoint ids: {sorted(_CHECKPOINTS)} "
            "(call checkpoint first)"
        )
    lost = _checkpoint_lost_objects(entry)
    if lost and restorable:
        _drop_checkpoint(int(checkpoint_id))
        raise ValueError(
            f"checkpoint {checkpoint_id}: the saved mesh of {lost} no longer exists (deleted, or the file was "
            f"reloaded); the checkpoint was dropped and nothing was restored; call checkpoint again"
        )
    return entry


@contextmanager
def _objects_in_object_mode():
    """Put every object in the view layer in OBJECT mode for the block; restore their modes afterwards."""
    view_layer = bpy.context.view_layer
    previous_active = view_layer.objects.active
    previous_modes = {o.name: o.mode for o in view_layer.objects if o.mode != "OBJECT"}
    for name in previous_modes:
        _switch_mode(bpy.data.objects[name], "OBJECT")
    try:
        yield
    finally:
        for name, mode in previous_modes.items():
            restored = bpy.data.objects.get(name)
            if restored is not None:
                _switch_mode(restored, mode)
        if previous_active is not None and previous_active.name in view_layer.objects:
            view_layer.objects.active = previous_active


def checkpoint(object_names=None, label=""):
    """Snapshot tile objects so :func:`rollback` can restore them.

    ``object_names=None`` snapshots every tile object of :func:`describe_scene`. Each object's mesh is
    copied into a hidden fake-user mesh ``.spyrite_ckpt_<id>_<object>`` (the copy carries the material
    slots); the grid id, pixels per unit, local transform and parent are recorded. A name listed twice is
    snapshotted once. Checkpoints are session state (see the module docstring).
    Returns ``{checkpoint_id, objects: [names]}``.
    """
    if not isinstance(label, str):
        raise ValueError(f"label must be a string, got {label!r}")
    if object_names is None:
        object_names = [o["object_name"] for o in describe_scene()["tile_objects"]]
    elif isinstance(object_names, str) or not isinstance(object_names, (list, tuple)):
        raise ValueError(f"object_names (op: objects) must be a list of object names or None, got {object_names!r}")
    object_names = list(dict.fromkeys(object_names))
    if not object_names:
        raise ValueError("No objects to checkpoint: the scene has no tile objects; call create_tile_object first")
    objects = [_mesh_object(name) for name in object_names]

    checkpoint_id = next(_CHECKPOINT_IDS)
    recorded = {}
    try:
        with _objects_in_object_mode():
            for obj in objects:
                mesh_copy = obj.data.copy()
                mesh_copy.name = f".spyrite_ckpt_{checkpoint_id}_{obj.name}"
                mesh_copy.use_fake_user = True
                recorded[obj.name] = {
                    "mesh": mesh_copy,
                    "gridid": int(obj.sprytile_gridid),
                    "pixels_per_unit": obj.get(PIXELS_PER_UNIT_PROP),
                    # local transform and parenting, not matrix_world: restoring a child's world matrix
                    # against a parent that has not been restored yet would bake the parent's move into it
                    "matrix_basis": obj.matrix_basis.copy(),
                    "parent": obj.parent.name if obj.parent else None,
                    "parent_type": obj.parent_type,
                    "parent_bone": obj.parent_bone,
                    "matrix_parent_inverse": obj.matrix_parent_inverse.copy(),
                }
    except BaseException:
        for entry in recorded.values():
            bpy.data.meshes.remove(entry["mesh"])
        raise
    _CHECKPOINTS[checkpoint_id] = {
        "label": label,
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "objects": recorded,
    }
    return {"checkpoint_id": checkpoint_id, "objects": list(recorded)}


def rollback(checkpoint_id):
    """Restore every object of a checkpoint to its mesh, grid, pixels per unit, materials, parent and transform.

    All objects (and the parents they had) must still exist and every saved mesh copy must be intact, else
    nothing is restored; a checkpoint whose mesh copy is gone is dropped. The checkpoint stays valid, so it
    can be rolled back to again. Returns ``{checkpoint_id, restored: [names]}``.
    """
    entry = _checkpoint_entry(checkpoint_id)
    checkpoint_id = int(checkpoint_id)
    missing = [name for name in entry["objects"] if bpy.data.objects.get(name) is None]
    if missing:
        raise ValueError(f"checkpoint {checkpoint_id}: objects missing: {missing}; nothing was restored")
    parentless = {
        name: saved["parent"] for name, saved in entry["objects"].items()
        if saved["parent"] is not None and bpy.data.objects.get(saved["parent"]) is None
    }
    if parentless:
        raise ValueError(
            f"checkpoint {checkpoint_id}: parents missing: {parentless} (object: parent); nothing was restored"
        )
    with _objects_in_object_mode():
        for name, saved in entry["objects"].items():
            obj = bpy.data.objects[name]
            old = obj.data
            restored = saved["mesh"].copy()
            restored.use_fake_user = False
            obj.data = restored
            if old.users == 0:
                bpy.data.meshes.remove(old)
            restored.name = name
            obj.sprytile_gridid = saved["gridid"]
            if saved["pixels_per_unit"] is None:
                obj.pop(PIXELS_PER_UNIT_PROP, None)
            else:
                obj[PIXELS_PER_UNIT_PROP] = saved["pixels_per_unit"]
            obj.parent = bpy.data.objects[saved["parent"]] if saved["parent"] else None
            if saved["parent"]:
                obj.parent_type = saved["parent_type"]
                obj.parent_bone = saved["parent_bone"]
            obj.matrix_parent_inverse = saved["matrix_parent_inverse"].copy()
            obj.matrix_basis = saved["matrix_basis"].copy()
    return {"checkpoint_id": checkpoint_id, "restored": list(entry["objects"])}


def discard_checkpoint(checkpoint_id):
    """Forget a checkpoint and remove its hidden mesh copies. Returns ``{checkpoint_id, discarded: [names]}``."""
    _checkpoint_entry(checkpoint_id, restorable=False)
    checkpoint_id = int(checkpoint_id)
    return {"checkpoint_id": checkpoint_id, "discarded": _drop_checkpoint(checkpoint_id)}


def list_checkpoints():
    """Read-only: ``[{checkpoint_id, label, created (ISO-8601 UTC), objects}]``, oldest first.

    Checkpoints that lost a mesh copy (deleted by hand, or the file was reloaded) are dropped first, along
    with their remaining copies, so every listed checkpoint can be rolled back to.
    """
    _prune_lost_checkpoints()
    return [
        {
            "checkpoint_id": checkpoint_id,
            "label": entry["label"],
            "created": entry["created"],
            "objects": list(entry["objects"]),
        }
        for checkpoint_id, entry in sorted(_CHECKPOINTS.items())
    ]


def set_pixel_art_view():
    """Make the scene's Workbench renders show tile textures as crisp, unlit texels.

    Workbench draws a material's viewport colour (flat grey) unless its colour type is ``TEXTURE``, so
    ``render_views`` and viewport renders of a tile object show no pixel art by default. This sets the
    scene's Workbench shading to ``color_type='TEXTURE'`` and ``light='FLAT'`` (no lighting), turns
    anti-aliasing off (``display.render_aa='OFF'``) and the view transform to ``Standard`` (AgX would shift
    the colours). Tileset images already use ``Closest`` interpolation. Every open 3D viewport in Solid
    shading gets the same texture colour type, since a person looking at the scene otherwise sees grey
    tiles too. The previous values are not restored; returns ``{color_type, light, render_aa,
    view_transform, viewports_textured}`` as they are now.
    """
    scene = bpy.context.scene
    _apply_pixel_art_scene_settings(scene)
    viewports_textured = 0
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type != "VIEW_3D":
                continue
            shading = area.spaces.active.shading
            if shading.type == "SOLID":
                shading.color_type = "TEXTURE"
                viewports_textured += 1
                area.tag_redraw()
    return {
        "viewports_textured": viewports_textured,
        "color_type": scene.display.shading.color_type,
        "light": scene.display.shading.light,
        "render_aa": scene.display.render_aa,
        "view_transform": scene.view_settings.view_transform,
    }
