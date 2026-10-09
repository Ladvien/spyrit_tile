"""build_spec / export_spec: a scene from a YAML file and back, inside Blender.

Fixtures: tests/fixtures/tiles_16px.png (64x64, 4x4 tiles of 16 px) and its sidecar
tiles_16px.spyrite.yaml (grass 0,0; stone 1,0; wall_top 2,0 on XZ/YZ; water 0,1), copied to tmp_path
by the `workdir` fixture, plus tests/fixtures/room.spyrite.yaml (the worked example) used in place.
"""

import importlib
import shutil
from pathlib import Path

import bpy
import pytest

api = importlib.import_module("bl_ext.user_default.spyrite_tile.api")
sprytile_utils = importlib.import_module("bl_ext.user_default.spyrite_tile.sprytile_utils")
spyrite_spec = importlib.import_module("bl_ext.user_default.spyrite_tile.spyrite_spec")

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
FIXTURE_SPEC = FIXTURES / "room.spyrite.yaml"
PREFIX = "spec_test_"
TERRAIN = PREFIX + "terrain"
ROOM = PREFIX + "room"
YARD = PREFIX + "yard"


def _remove_test_data():
    for obj in [o for o in bpy.data.objects if o.name.startswith(PREFIX) or o.name == "room"]:
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in [m for m in bpy.data.meshes if m.name.startswith(PREFIX) or m.name == "room"]:
        bpy.data.meshes.remove(mesh)
    for material in [m for m in bpy.data.materials if m.name.startswith(PREFIX) or m.name == "terrain"]:
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
def workdir(tmp_path):
    shutil.copy(FIXTURES / "tiles_16px.png", tmp_path / "tiles.png")
    shutil.copy(FIXTURES / "tiles_16px.spyrite.yaml", tmp_path / "tiles.spyrite.yaml")
    return tmp_path


TWO_OBJECTS = f"""\
spyrite_spec: 1
pixels_per_unit: 16
tilesets:
  {TERRAIN}:
    image: ./tiles.png
    tile_size_px: [16, 16]
objects:
  {ROOM}:
    tileset: {TERRAIN}
    fills:
      - {{plane: XY, cells: [[0, 0], [2, 1]], tile: grass}}
    tiles:
      - {{plane: XZ, plane_offset_m: 2, cell: [1, 0], tile: wall_top, rotation_deg: 90, flip_x: true}}
      - {{plane: XY, cell: [0, 0], tile: water, layer: DECAL, rotation_deg: 180}}
  {YARD}:
    tileset: {TERRAIN}
    pixels_per_unit: 32
    tiles:
      - {{cell: [4, 4], tile: [3, 3], tile_span: [1, 1]}}
"""


def _write(workdir, text, name="scene.spyrite.yaml"):
    path = workdir / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def _signature(object_name):
    faces = api.describe_tile_object(object_name, max_faces=10_000)["faces"]
    return sorted(
        (
            f["plane"], f["plane_offset_m"], tuple(f["cell_xy"]), tuple(f["tile_xy"]), tuple(f["tile_span"]),
            f["rotation_deg"], f["flip_x"], f["flip_y"], f["layer"],
        )
        for f in faces
    )


def test_build_two_objects(workdir):
    report = api.build_spec(_write(workdir, TWO_OBJECTS))
    assert report["spec_path"] == str(workdir / "scene.spyrite.yaml")
    assert [t["material_name"] for t in report["tilesets"]] == [TERRAIN]
    assert report["tilesets"][0]["tile_names"]["grass"]["xy"] == [0, 0]
    by_name = {o["object_name"]: o for o in report["objects"]}
    assert by_name[ROOM] == {"object_name": ROOM, "built": 8, "remapped": 0, "face_count": 8}
    assert by_name[YARD]["face_count"] == 1

    faces = {(f["plane"], tuple(f["cell_xy"]), f["layer"]): f for f in api.describe_tile_object(ROOM)["faces"]}
    wall = faces[("XZ", (1, 0), "BASE")]
    assert (wall["tile"], wall["tile_xy"], wall["plane_offset_m"]) == ("wall_top", [2, 0], 2.0)
    assert (wall["rotation_deg"], wall["flip_x"], wall["flip_y"]) == (90, True, False)
    decal = faces[("XY", (0, 0), "DECAL")]
    assert (decal["tile"], decal["rotation_deg"]) == ("water", 180)
    assert faces[("XY", (2, 1), "BASE")]["tile"] == "grass"

    yard = api.describe_tile_object(YARD)["faces"][0]
    assert yard["tile_xy"] == [3, 3] and yard["cell_xy"] == [4, 4]
    scene_objects = {o["object_name"]: o for o in api.describe_scene()["tile_objects"]}
    assert scene_objects[YARD]["pixels_per_unit"] == 32 and scene_objects[ROOM]["pixels_per_unit"] == 16


def test_fixture_room_builds_50_faces():
    report = api.build_spec(str(FIXTURE_SPEC))
    assert [o["object_name"] for o in report["objects"]] == ["room"]
    assert report["objects"][0]["face_count"] == 36 + 8 + 6 == 50
    faces = api.describe_tile_object("room", max_faces=100)["faces"]
    floor = [f for f in faces if f["plane"] == "XY"]
    assert len(floor) == 36 and all(f["tile"] == "grass" for f in floor)
    assert {f["tile"] for f in faces if f["plane"] != "XY"} == {"wall_top"}
    assert sum(f["plane"] == "XZ" for f in faces) == 8 and sum(f["plane"] == "YZ" for f in faces) == 6


def test_rebuilding_is_idempotent(workdir):
    path = _write(workdir, TWO_OBJECTS)
    api.build_spec(path)
    before = _signature(ROOM)
    report = api.build_spec(path)
    assert {o["object_name"]: o["face_count"] for o in report["objects"]}[ROOM] == 8
    assert report["objects"][0]["built"] == 0 and report["objects"][0]["remapped"] == 8
    assert _signature(ROOM) == before


def test_clear_removes_prior_faces(workdir):
    path = _write(workdir, TWO_OBJECTS)
    api.build_spec(path)
    api.fill_tiles(ROOM, TERRAIN, (10, 10), (12, 12), (1, 1))
    assert api.describe_tile_object(ROOM)["face_count"] == 8 + 9
    kept = api.build_spec(path)
    assert kept["objects"][0]["face_count"] == 17
    cleared = api.build_spec(_write(workdir, TWO_OBJECTS.replace(f"  {ROOM}:\n", f"  {ROOM}:\n    clear: true\n")))
    assert cleared["objects"][0] == {"object_name": ROOM, "built": 8, "remapped": 0, "face_count": 8}
    assert not [f for f in api.describe_tile_object(ROOM)["faces"] if f["cell_xy"] == [10, 10]]


def test_round_trip_reproduces_the_scene(workdir):
    api.build_spec(_write(workdir, TWO_OBJECTS))
    expected = {name: _signature(name) for name in (ROOM, YARD)}
    exported = workdir / "out" / "exported.spyrite.yaml"
    report = api.export_spec([ROOM, YARD], str(exported))
    assert report == {"spec_path": str(exported), "objects": 2, "tiles": 9, "unexported_faces": {}}
    text = exported.read_text(encoding="utf-8")
    assert "image: ../tiles.png" in text or str(workdir / "tiles.png") in text
    spec = spyrite_spec.validate_spec(spyrite_spec.load_spec(text))
    decal = [t for t in spec["objects"][ROOM]["tiles"] if t["layer"] == "DECAL"]
    assert len(decal) == 1 and decal[0]["plane_offset_m"] == 0.0 and decal[0]["tile"] == "water"
    assert spec["objects"][ROOM]["tiles"][-1]["layer"] == "DECAL"

    _remove_test_data()
    assert not api.describe_scene()["tile_objects"]
    api.build_spec(str(exported))
    assert {name: _signature(name) for name in (ROOM, YARD)} == expected


def test_export_writes_image_relative_to_the_spec(workdir):
    api.build_spec(_write(workdir, TWO_OBJECTS))
    api.export_spec([ROOM], str(workdir / "exported.spyrite.yaml"))
    spec = spyrite_spec.load_spec((workdir / "exported.spyrite.yaml").read_text(encoding="utf-8"))
    assert spec["tilesets"][TERRAIN]["image"] == "./tiles.png"
    assert list(spec["tilesets"]) == [TERRAIN] and list(spec["objects"]) == [ROOM]
    assert "clear" not in spec["objects"][ROOM]


def test_export_reports_faces_it_cannot_rebuild(workdir):
    api.build_spec(_write(workdir, TWO_OBJECTS))
    obj = bpy.data.objects[ROOM]
    import bmesh

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.faces.new([bm.verts.new(v) for v in ((9, 9, 5), (10, 9, 5), (9, 10, 5))])
    bm.to_mesh(obj.data)
    bm.free()
    report = api.export_spec([ROOM], str(workdir / "partial.spyrite.yaml"))
    assert report["unexported_faces"] == {ROOM: [8]}
    assert report["tiles"] == 8


def test_export_errors(workdir):
    with pytest.raises(ValueError, match="object_names is empty"):
        api.export_spec([], str(workdir / "x.yaml"))
    with pytest.raises(ValueError, match="spec_path must be absolute"):
        api.export_spec([ROOM], "x.yaml")
    with pytest.raises(ValueError, match=r"No tile object named 'nope'"):
        api.export_spec(["nope"], str(workdir / "x.yaml"))
    assert not (workdir / "x.yaml").exists()


def test_build_spec_path_errors(workdir):
    with pytest.raises(ValueError, match="spec_path must be absolute"):
        api.build_spec("scene.yaml")
    with pytest.raises(ValueError, match="spec_path does not exist"):
        api.build_spec(str(workdir / "missing.yaml"))


def _build_error(workdir, text):
    with pytest.raises(spyrite_spec.SpecError) as error:
        api.build_spec(_write(workdir, text))
    return str(error.value)


def test_spec_errors_are_raised_before_anything_is_built(workdir):
    assert _build_error(workdir, TWO_OBJECTS.replace("spyrite_spec: 1", "spyrite_spec: 2")) == "spyrite_spec: must be 1"
    assert _build_error(workdir, "spyrite_spec: 1\nbogus: 1\n").startswith("spec: unknown keys ['bogus']; valid keys are [")
    assert _build_error(workdir, TWO_OBJECTS.replace("    fills:", "    bogus: []\n    fills:", 1)).startswith(
        f"objects.{ROOM}: unknown keys ['bogus']"
    )
    assert _build_error(workdir, TWO_OBJECTS.replace("tile: grass", "tile: lava")) == (
        f"objects.{ROOM}.fills[0].tile: unknown tile name 'lava' in tileset {TERRAIN!r}; "
        "known names: ['grass', 'stone', 'wall_top', 'water']"
    )
    message = _build_error(workdir, TWO_OBJECTS.replace("tile: grass", "tile: wall_top"))
    assert message == (
        f"objects.{ROOM}.fills[0].tile: tile 'wall_top' is not allowed on plane XY (allowed: XZ, YZ)"
    )
    assert _build_error(workdir, TWO_OBJECTS.replace("tile: [3, 3]", "tile: [9, 9]")).startswith(f"objects.{YARD}.tiles[0]")
    missing = TWO_OBJECTS.replace("./tiles.png", "./nothing.png")
    assert _build_error(workdir, missing).startswith(f"tilesets.{TERRAIN}.image: no such file")
    assert _build_error(workdir, TWO_OBJECTS.replace("pixels_per_unit: 32", "pixels_per_unit: 100000")).startswith(
        f"objects.{YARD}.pixels_per_unit: "
    )
    assert bpy.data.objects.get(ROOM) is None and bpy.data.objects.get(YARD) is None


def test_failed_placement_rolls_the_object_back(workdir):
    path = _write(workdir, TWO_OBJECTS)
    api.build_spec(path)
    before = _signature(ROOM)
    # A decal in a cell without a BASE face passes validation but cannot be built
    broken = TWO_OBJECTS.replace("cell: [0, 0], tile: water", "cell: [9, 9], tile: water")
    with pytest.raises(RuntimeError, match="DECAL needs a BASE tile"):
        api.build_spec(_write(workdir, broken.replace(f"  {ROOM}:\n", f"  {ROOM}:\n    clear: true\n"), "broken.yaml"))
    assert _signature(ROOM) == before
