"""Named tiles: a `<image>.spyrite.yaml` sidecar next to the image lets every tile argument be a name.

Fixture: tests/fixtures/tiles_16px.png (64x64, 4x4 tiles of 16 px) copied next to a sidecar in tmp_path.
"""

import importlib
import shutil
from pathlib import Path

import bpy
import pytest

api = importlib.import_module("bl_ext.user_default.spyrite_tile.api")
sprytile_core = importlib.import_module("bl_ext.user_default.spyrite_tile.sprytile_core")

FIXTURE_IMAGE = Path(__file__).resolve().parent.parent / "fixtures" / "tiles_16px.png"
PREFIX = "names_test_"
TILESET = PREFIX + "tiles"
OBJECT = PREFIX + "obj"
OTHER = PREFIX + "other"
SIDECAR = """\
spyrite_tileset: 1
tiles:
  grass: {xy: [0, 0], tags: [floor]}
  stone: {xy: [1, 2]}
  wall_top: {xy: [2, 0], planes: [XZ, YZ], tags: [wall]}
"""


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
def named_image(tmp_path):
    image = tmp_path / "tiles.png"
    shutil.copy(FIXTURE_IMAGE, image)
    (tmp_path / "tiles.spyrite.yaml").write_text(SIDECAR, encoding="utf-8")
    return image


@pytest.fixture
def named_tileset(named_image):
    report = api.create_tileset(TILESET, str(named_image), (16, 16))
    assert report["reused_material"] is None  # a reused material would leave TILESET undefined
    return report


def _geometry(object_name):
    obj = bpy.data.objects[object_name]
    mesh = obj.data
    uv = mesh.uv_layers.active.data
    return [
        [
            (
                tuple(round(c, 5) for c in mesh.vertices[mesh.loops[i].vertex_index].co),
                tuple(round(c, 5) for c in uv[i].uv),
            )
            for i in poly.loop_indices
        ]
        for poly in mesh.polygons
    ]


def test_create_tileset_reports_tile_names(named_tileset):
    assert named_tileset["tile_names"] == {
        "grass": {"xy": [0, 0], "planes": None, "tags": ["floor"]},
        "stone": {"xy": [1, 2], "planes": None, "tags": []},
        "wall_top": {"xy": [2, 0], "planes": ["XZ", "YZ"], "tags": ["wall"]},
    }


@pytest.fixture
def plain_image(tmp_path):
    image = tmp_path / "plain.png"
    shutil.copy(FIXTURE_IMAGE, image)
    return image


def test_tileset_without_sidecar_has_no_names(plain_image):
    report = api.create_tileset(TILESET, str(plain_image), (16, 16))
    assert report["tile_names"] == {}


def test_placing_by_name_matches_placing_by_tile_xy(named_tileset):
    api.create_tile_object(OBJECT, TILESET, 16)
    api.create_tile_object(OTHER, TILESET, 16)
    api.place_tiles(
        OBJECT,
        TILESET,
        [
            {"cell_xy": [0, 0], "tile": "grass"},
            {"cell_xy": [1, 0], "tile": "stone", "rotation_deg": 90},
            {"cell_xy": [2, 0], "tile_xy": "wall_top", "plane": "XZ", "plane_offset_m": 2.0},
            {"cell_xy": [3, 0], "tile": [3, 3]},
        ],
    )
    api.place_tiles(
        OTHER,
        TILESET,
        [
            {"cell_xy": [0, 0], "tile_xy": [0, 0]},
            {"cell_xy": [1, 0], "tile_xy": [1, 2], "rotation_deg": 90},
            {"cell_xy": [2, 0], "tile_xy": [2, 0], "plane": "XZ", "plane_offset_m": 2.0},
            {"cell_xy": [3, 0], "tile_xy": [3, 3]},
        ],
    )
    assert _geometry(OBJECT) == _geometry(OTHER)


def test_fill_and_paint_accept_names(named_tileset):
    api.create_tile_object(OBJECT, TILESET, 16)
    api.fill_tiles(OBJECT, TILESET, [0, 0], [1, 1], "grass")
    api.paint_faces(OBJECT, TILESET, [0], "stone")
    tiles = [f["tile"] for f in api.describe_tile_object(OBJECT)["faces"]]
    assert sorted(t for t in tiles if t) == ["grass", "grass", "grass", "stone"]
    faces = api.describe_tile_object(OBJECT)["faces"]
    assert faces[0]["tile_xy"] == [1, 2] and faces[0]["tile"] == "stone"


def test_describe_tile_object_reports_the_tile_name_and_null_when_unnamed(named_tileset):
    api.create_tile_object(OBJECT, TILESET, 16)
    api.place_tiles(
        OBJECT,
        TILESET,
        [{"cell_xy": [0, 0], "tile": "grass"}, {"cell_xy": [1, 0], "tile": [3, 3]}],
    )
    faces = sorted(api.describe_tile_object(OBJECT)["faces"], key=lambda f: f["center_m"][0])
    assert faces[0]["tile"] == "grass"
    assert faces[1]["tile"] is None and faces[1]["tile_xy"] == [3, 3]


def test_plane_rule_rejects_a_tile_on_a_disallowed_plane(named_tileset):
    api.create_tile_object(OBJECT, TILESET, 16)
    with pytest.raises(ValueError) as error:
        api.place_tiles(OBJECT, TILESET, [{"cell_xy": [0, 0], "tile": "wall_top"}])
    assert str(error.value) == (
        "placements[0].tile: tile 'wall_top' is not allowed on plane XY (allowed: XZ, YZ)"
    )
    with pytest.raises(ValueError, match=r"not allowed on plane XY"):
        api.fill_tiles(OBJECT, TILESET, [0, 0], [1, 1], "wall_top")
    # paint_faces has no plane check
    api.fill_tiles(OBJECT, TILESET, [0, 0], [0, 0], "grass")
    api.paint_faces(OBJECT, TILESET, [0], "wall_top")
    face = api.describe_tile_object(OBJECT)["faces"][0]
    assert face["tile"] == "wall_top" and face["tile_xy"] == [2, 0]


def test_unknown_name_lists_the_known_names(named_tileset):
    api.create_tile_object(OBJECT, TILESET, 16)
    with pytest.raises(ValueError) as error:
        api.place_tiles(OBJECT, TILESET, [{"cell_xy": [0, 0], "tile": "lava"}])
    assert str(error.value) == (
        f"placements[0].tile: unknown tile name 'lava' in tileset {TILESET!r}; "
        "known names: ['grass', 'stone', 'wall_top']"
    )


def test_name_without_sidecar_says_so(plain_image):
    api.create_tileset(TILESET, str(plain_image), (16, 16))
    api.create_tile_object(OBJECT, TILESET, 16)
    sidecar = str(plain_image.with_suffix(".spyrite.yaml"))
    with pytest.raises(ValueError) as error:
        api.place_tiles(OBJECT, TILESET, [{"cell_xy": [0, 0], "tile": "grass"}])
    assert str(error.value) == (
        f"placements[0].tile: tileset {TILESET!r} has no tile names (no {sidecar} next to the image); "
        "use [column, row]"
    )


def test_exactly_one_of_tile_xy_or_tile(named_tileset):
    api.create_tile_object(OBJECT, TILESET, 16)
    with pytest.raises(ValueError, match=r"placements\[0\] needs exactly one of tile_xy or tile"):
        api.place_tiles(OBJECT, TILESET, [{"cell_xy": [0, 0]}])
    with pytest.raises(ValueError, match=r"placements\[1\] needs exactly one of tile_xy or tile"):
        api.place_tiles(
            OBJECT,
            TILESET,
            [{"cell_xy": [0, 0], "tile": "grass"}, {"cell_xy": [1, 0], "tile": "grass", "tile_xy": [0, 0]}],
        )


def test_sidecar_errors_name_the_file_and_the_dotted_path(tmp_path):
    image = tmp_path / "bad.png"
    shutil.copy(FIXTURE_IMAGE, image)
    sidecar = tmp_path / "bad.spyrite.yaml"
    api.create_tileset(TILESET, str(image), (16, 16))
    api.create_tile_object(OBJECT, TILESET, 16)

    def attempt(text, tile="a"):
        sidecar.write_text(text, encoding="utf-8")
        # a fresh tileset lookup rereads the file
        with pytest.raises(ValueError) as error:
            api.place_tiles(OBJECT, TILESET, [{"cell_xy": [0, 0], "tile": tile}])
        return str(error.value)

    prefix = f"{sidecar}: "
    assert attempt("spyrite_tileset: 2\ntiles: {a: {xy: [0, 0]}}\n") == prefix + "spyrite_tileset: must be 1"
    assert attempt("spyrite_tileset: 1\ntiles: {a: {tags: []}}\n").startswith(prefix + "tiles.a.xy: required")
    assert attempt("spyrite_tileset: 1\ntiles: {a: {xy: [0, 0], planes: [AB]}}\n").startswith(prefix + "tiles.a.planes:")
    assert attempt("spyrite_tileset: 1\ntiles: {a: {xy: [0, 0], color: red}}\n").startswith(prefix + "tiles.a: unknown keys")
    assert attempt("spyrite_tileset: 1\ntiles: {a: {xy: [0, 0]\n").startswith(prefix + "spyrite_tileset: invalid YAML")
    assert "outside tileset" in attempt("spyrite_tileset: 1\ntiles: {a: {xy: [9, 0]}}\n")


def test_malformed_sidecar_fails_create_tileset_before_touching_anything(tmp_path):
    image = tmp_path / "bad.png"
    shutil.copy(FIXTURE_IMAGE, image)
    (tmp_path / "bad.spyrite.yaml").write_text("spyrite_tileset: 2\ntiles: {a: {xy: [0, 0]}}\n", encoding="utf-8")
    images_before = set(bpy.data.images.keys())
    entries_before = len(bpy.context.scene.sprytile_mats)
    with pytest.raises(ValueError, match=r"bad\.spyrite\.yaml: spyrite_tileset: must be 1"):
        api.create_tileset(TILESET, str(image), (16, 16))
    assert bpy.data.materials.get(TILESET) is None
    assert set(bpy.data.images.keys()) == images_before
    assert len(bpy.context.scene.sprytile_mats) == entries_before


def test_sidecar_entries_outside_the_layout_do_not_break_the_tileset(named_image):
    """One image, one sidecar, two layouts: at 32 px the 2x2 grid has no (2, 0) tile, yet nothing may raise."""
    report = api.create_tileset(TILESET, str(named_image), (32, 32))
    assert (report["columns"], report["rows"]) == (2, 2)
    assert report["tile_names"]["wall_top"]["xy"] == [2, 0]  # listed as written, not filtered
    api.create_tile_object(OBJECT, TILESET, 16)
    api.place_tiles(OBJECT, TILESET, [{"cell_xy": [0, 0], "tile": "grass"}, {"cell_xy": [1, 0], "tile": [1, 1]}])
    # read-back is unaffected: names whose tile is not in the layout simply never match
    faces = sorted(api.describe_tile_object(OBJECT)["faces"], key=lambda f: f["center_m"][0])
    assert [f["tile"] for f in faces] == ["grass", None]
    described = [t for t in api.describe_scene()["tilesets"] if t["material_name"] == TILESET][0]
    assert described["tile_names"] == report["tile_names"]
    checkpoint = api.checkpoint(None)
    assert OBJECT in checkpoint["objects"]
    api.discard_checkpoint(checkpoint["checkpoint_id"])
    assert api.select_faces(OBJECT, {"tag": "floor"})["count"] == 1
    # only USING an out-of-range name fails, and the message names the sidecar that defines it
    with pytest.raises(ValueError) as error:
        api.place_tiles(OBJECT, TILESET, [{"cell_xy": [2, 0], "tile": "wall_top", "plane": "XZ"}])
    message = str(error.value)
    assert str(named_image.with_suffix(".spyrite.yaml")) in message
    assert "tile 'wall_top'" in message and "is outside tileset" in message
