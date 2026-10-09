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

Reading back
    :func:`describe_tile_object` reports, per face, everything the placement took: ``tile_xy``, ``tile_span``,
    ``rotation_deg``, ``flip_x``, ``flip_y``, ``layer``, ``plane``, ``facing``, ``plane_offset_m``, ``cell_xy``
    and ``on_grid`` (false for geometry that is not a whole-cell rectangle), so a scene can be rebuilt from
    its report. A decal reports its lifted ``plane_offset_m``. :func:`describe_scene` lists the tilesets, the
    tile objects and the scene settings.

Specs
    :func:`build_spec` builds a whole scene from a YAML file (tilesets, objects, fills, tiles; tiles may be
    given by name from the tileset's sidecar) and :func:`export_spec` writes tile objects back to one; see
    ``spyrite_spec.validate_spec`` for the schema and ``tests/fixtures/room.spyrite.yaml`` for an example.

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
"""

import math
import numbers
import os
from contextlib import contextmanager

import bmesh
import bpy
from mathutils import Vector

from . import spyrite_spec
from . import sprytile_utils
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
    "describe_tile_object",
    "describe_scene",
    "set_pixel_art_view",
]

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
_PLACEMENT_REQUIRED = frozenset({"cell_xy"})


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
        """Tile names from the sidecar next to the image (``{}`` when there is none), loaded once."""
        if self._names is None:
            self._names = {}
            path = self.sidecar
            if os.path.isfile(path):
                with open(path, encoding="utf-8") as handle:
                    names = spyrite_spec.load_tile_names(handle.read())
                for name, entry in names.items():
                    self.check_tile(f"{path}: tiles.{name}.xy", entry["xy"], (1, 1))
                self._names = names
        return self._names

    @property
    def sidecar(self):
        return spyrite_spec.sidecar_path(bpy.path.abspath(self.image.filepath))

    def tile_names(self):
        """``{name: {xy, planes, tags}}`` as plain copies, safe to hand to callers."""
        return {
            name: {"xy": list(e["xy"]), "planes": None if e["planes"] is None else list(e["planes"]), "tags": list(e["tags"])}
            for name, e in self.names.items()
        }


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
        return tuple(entry["xy"])
    tile_xy = _as_int_tuple(label, value, 2)
    tileset.check_tile(label, tile_xy, (1, 1))
    return tile_xy


def _find_tileset(material_name):
    _as_name("material_name", material_name)
    material = bpy.data.materials.get(material_name)
    if material is None:
        raise ValueError(f"No material named {material_name!r}; call create_tileset first")
    mat_data = sprytile_utils.get_mat_data(bpy.context, material.name)
    if mat_data is None or len(mat_data.grids) == 0:
        raise ValueError(f"Material {material_name!r} is not a tileset; call create_tileset first")
    image = sprytile_utils.get_material_texture(material)
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
        if material.session_uid in sprytile_utils._removed_tilesets:
            continue
        image = sprytile_utils.get_material_texture(material)
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
    reused_material}`` (``reused_material`` is ``None`` unless an existing tileset was reused).
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

    scene = bpy.context.scene
    sprytile_utils.ensure_scene_setup(scene)

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
    sprytile_utils.restore_removed_tileset(material)
    sprytile_utils.setup_tile_material(material, image)
    sprytile_utils.validate_grids(scene)

    mat_data = sprytile_utils.get_mat_data(bpy.context, material.name)
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
    sprytile_utils.ensure_scene_setup(scene)
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


def _apply_placements(obj, tileset, placements, clear=False):
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
            grid_origin[plane["axis"]] = placement["plane_offset_m"]
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
                work_layer_mask=sprytile_utils.get_work_layer_data(data),
                grid_origin=grid_origin,
            )
            faces_after = len(builder.bmesh.faces)
            if face_index is None and faces_after == faces_before:
                raise RuntimeError(
                    f"placements[{index}]: could not place tile at cell {placement['cell_xy']} on plane "
                    f"{placement['plane']} at offset {placement['plane_offset_m']} m: "
                    + (
                        "a DECAL needs a BASE tile in that cell, or an existing face there is not coplanar"
                        if decal
                        else "an existing face in that cell is not coplanar with the plane"
                    )
                )
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
    sprytile_utils.ensure_scene_setup(bpy.context.scene)
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


# ---------------------------------------------------------------------------
# Removing and repainting
# ---------------------------------------------------------------------------


def _object_grid(obj):
    grid = sprytile_utils.get_grid(bpy.context, obj.sprytile_gridid)
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
    offset = _as_float("plane_offset_m", plane_offset_m)
    if isinstance(cells, (str, bytes, dict)) or not hasattr(cells, "__iter__"):
        raise ValueError(f"cells must be a list of [x, y] cells, got {type(cells).__name__}")
    cell_set = {_as_int_tuple(f"cells[{i}]", cell, 2) for i, cell in enumerate(cells)}
    if not cell_set:
        raise ValueError("cells is empty")
    sprytile_utils.ensure_scene_setup(bpy.context.scene)
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
    sprytile_utils.ensure_scene_setup(bpy.context.scene)

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
# Reading back
# ---------------------------------------------------------------------------


def _cached_tileset(obj, grid_id, tileset_cache):
    """The _Tileset of a grid id (cached per call), or None when the grid or its image is gone."""
    if grid_id not in tileset_cache:
        grid = sprytile_utils.get_grid(bpy.context, grid_id)
        image = sprytile_utils.get_grid_texture(obj, grid) if grid is not None else None
        material = sprytile_utils.get_grid_material(grid) if image is not None else None
        mat_data = sprytile_utils.get_mat_data(bpy.context, material.name) if material is not None else None
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
    bit 8 ``uv_flip_y`` (``sprytile_utils.get_paint_settings``). Sprytile mirrors before turning, so for a
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

    Returns ``{object_name, face_count, truncated, faces}`` where each face (the first ``max_faces`` by index)
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
        "face_count": face_count,
        "truncated": face_count > len(faces),
        "faces": faces,
    }


def describe_scene():
    """Read back every tileset and tile object of the scene.

    Returns ``{tilesets, tile_objects, removed_tilesets, settings}``:

    - ``tilesets``: one per ``scene.sprytile_mats`` entry that has a grid and an image texture:
      ``{material_name, image_name, image_path (absolute), image_size_px, tile_size_px, padding_px,
      margin_px, columns, rows, grid_id, tile_names}`` (``tile_names`` as in :func:`create_tileset`);
    - ``tile_objects``: every mesh object of the view layer bound to a tileset:
      ``{object_name, material_name, grid_id, pixels_per_unit, face_count, location_m, overlay_of}``
      (``overlay_of`` is None until overlay objects exist);
    - ``removed_tilesets``: material names hidden with the grid "-" button this session;
    - ``settings``: ``{world_pixels, mesh_decal_offset, auto_merge}`` of ``scene.sprytile_data``.
    """
    context = bpy.context
    scene = context.scene
    sprytile_utils.ensure_scene_setup(scene)

    tilesets = []
    for mat_data in scene.sprytile_mats:
        material = bpy.data.materials.get(mat_data.mat_id)
        if material is None or len(mat_data.grids) == 0:
            continue
        image = sprytile_utils.get_material_texture(material)
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
        grid = sprytile_utils.get_grid(context, obj.sprytile_gridid)
        material = sprytile_utils.get_grid_material(grid) if grid is not None else None
        tile_objects.append(
            {
                "object_name": obj.name,
                "material_name": material.name if material is not None else "",
                "grid_id": obj.sprytile_gridid,
                "pixels_per_unit": _object_pixels_per_unit(obj, scene),
                "face_count": len(obj.data.polygons) if obj.mode != "EDIT" else len(bmesh.from_edit_mesh(obj.data).faces),
                "location_m": [round(c, 6) for c in obj.matrix_world.translation],
                "overlay_of": None,
            }
        )

    removed = [
        material.name
        for material in bpy.data.materials
        if material.session_uid in sprytile_utils._removed_tilesets
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


def _spec_placements(spec_object):
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
    return placements, origins


def build_spec(spec_path):
    """Build a scene from a YAML spec file (see ``spyrite_spec.validate_spec`` for the schema).

    ``spec_path`` must be absolute. Tileset ``image`` paths are relative to the spec file's directory (or
    absolute). Steps: parse and validate the spec, create every tileset (:func:`create_tileset`, idempotent
    per image), resolve every tile name and check every placement against its tileset, and only then create
    each object (:func:`create_tile_object`) and apply, in one edit session per object, ``clear`` (delete all
    its faces first), the ``fills`` (as :func:`fill_tiles`) and the ``tiles`` (as :func:`place_tiles`), in
    that order. A failure while placing rolls that object back to what it was; tilesets and objects built
    before the failure stay. Errors are ``ValueError`` (``SpecError`` for spec problems, starting with the
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
        placements, origins = _spec_placements(entry)
        normalized = []
        for index, placement in enumerate(placements):
            try:
                normalized.append(_normalize_placement(index, placement, tileset))
            except ValueError as error:
                message = str(error).replace(f"placements[{index}]", f"objects.{name}.{origins[index]}", 1)
                raise spyrite_spec.SpecError(message) from None
        plans.append((name, entry, tileset, normalized))

    sprytile_utils.ensure_scene_setup(bpy.context.scene)
    objects = []
    for name, entry, tileset, normalized in plans:
        report = create_tile_object(name, tileset.material.name, entry["pixels_per_unit"])
        obj = _mesh_object(report["object_name"])
        result = _apply_placements(obj, tileset, normalized, clear=entry["clear"])
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
    reported. ``spec_path`` must be absolute; missing parent directories are created.

    Returns ``{spec_path, objects: n, tiles: n, unexported_faces: {object_name: [face indices]}}``.
    """
    if isinstance(object_names, (str, bytes)) or not hasattr(object_names, "__iter__"):
        raise ValueError(f"object_names must be a list of object names, got {object_names!r}")
    object_names = list(object_names)
    if not object_names:
        raise ValueError("object_names is empty; name at least one tile object")
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
            offset = face["plane_offset_m"]
            if face["layer"] == "DECAL":
                offset = round(offset - lift * PLANES[face["plane"]]["normal"][PLANES[face["plane"]]["axis"]], 6)
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
    scene.display.shading.color_type = "TEXTURE"
    scene.display.shading.light = "FLAT"
    scene.display.render_aa = "OFF"
    scene.view_settings.view_transform = "Standard"
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
