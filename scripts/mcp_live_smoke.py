"""Drive the Blender you have open, through blended's MCP server, and check the result.

The same path an agent uses: MCP client -> blender-mcp (stdio) -> the `mcp` add-on's
socket in your running Blender -> blended's dispatch -> spyrite_tile_ops -> the add-on API.
It builds `spyrite_smoke_room` (a 6x6 floor and a 6x3 wall from the 16 px fixture), turns on
texture shading, frames it, saves a window screenshot and asserts the face count and tiles.

Needs: Blender open with the `mcp` add-on server running (blended's `make install-mcp-addon`)
and Spyrite Tile enabled (`make install-addon`); the plugin installed into blended's venv
(`make install-blended-plugin`). Run with `make test-live`. Exit code 0 means every check passed.

It edits the open scene (adds or rebuilds `spyrite_smoke_room`); nothing else is touched.
Edits to the add-on need a Blender restart, or reload `api` the way `--reload-api` does.
"""

import asyncio
import base64
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPOSITORY = Path(__file__).resolve().parent.parent
BLENDED_MCP = "/Users/ladvien/blended/.venv/bin/blender-mcp"
FIXTURE = REPOSITORY / "tests" / "fixtures" / "tiles_16px.png"
OUTPUT = REPOSITORY / "outputs" / "live_smoke"
ROOM = "spyrite_smoke_room"
TILESET = "spyrite_smoke_tiles"
FLOOR_CELLS = 6 * 6
WALL_CELLS = 6 * 3
FLOOR_TILE = [1, 1]
WALL_TILE = [2, 0]

REMOVE_ROOM = f"""import bpy
o = bpy.data.objects.get({ROOM!r})
if o is not None:
    if bpy.context.object is o and o.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    bpy.data.objects.remove(o)
"""
RELOAD_API = """import importlib, sys
importlib.reload(sys.modules['bl_ext.user_default.spyrite_tile.api'])
"""
FRAME_ROOM = f"""import bpy, math
from mathutils import Euler, Vector
if bpy.context.object is not None and bpy.context.object.mode != 'OBJECT':
    bpy.ops.object.mode_set(mode='OBJECT')
for o in bpy.context.selected_objects:
    o.select_set(False)
room = bpy.data.objects[{ROOM!r}]
room.select_set(True)
bpy.context.view_layer.objects.active = room
corners = [room.matrix_world @ Vector(c) for c in room.bound_box]
centre = sum(corners, Vector()) / len(corners)
size = max((a - b).length for a in corners for b in corners)
for area in bpy.context.screen.areas:
    if area.type == 'VIEW_3D':
        view = area.spaces.active.region_3d
        view.view_perspective = 'PERSP'
        view.view_location = centre
        view.view_rotation = Euler((math.radians(60), 0, math.radians(30))).to_quaternion()
        view.view_distance = size * 1.6
        area.tag_redraw()
"""


class SmokeFailure(AssertionError):
    pass


async def _call(session, name, arguments):
    result = await session.call_tool(name, arguments)
    texts = [c.text for c in result.content if c.type == "text"]
    if result.isError:
        raise SmokeFailure(f"{name} failed: {' | '.join(texts)[:2000]}")
    return result, "\n".join(texts)


def _returned(text):
    for line in text.splitlines():
        if line.strip().startswith("returned:"):
            return json.loads(line.split("returned:", 1)[1])
    raise SmokeFailure(f"no 'returned:' line in {text[:500]!r}")


async def main(reload_api: bool) -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    server = StdioServerParameters(command=BLENDED_MCP, args=[])
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            steps = ["reset the smoke room", "import tileset", "create object",
                     "fill floor and wall", "texture shading", "frame and inspect"]
            await _call(session, "declare_plan", {"steps": steps})
            setup = REMOVE_ROOM + (RELOAD_API if reload_api else "")
            await _call(session, "run_python", {
                "source": setup, "plan_step": 1,
                "reason": "no op deletes an object or reloads an add-on module"})
            tileset = _returned((await _call(session, "import_tileset", {
                "name": TILESET, "image_path": str(FIXTURE),
                "tile_size_px": [16, 16], "plan_step": 2}))[1])
            if (tileset["columns"], tileset["rows"]) != (4, 4):
                raise SmokeFailure(f"fixture should be 4x4 tiles, got {tileset}")
            await _call(session, "create_tile_object", {
                "name": ROOM, "tileset_name": TILESET, "plan_step": 3})
            await _call(session, "fill_tiles", {
                "object_name": ROOM, "tileset_name": TILESET, "cell_min_xy": [0, 0],
                "cell_max_xy": [5, 5], "tile_xy": FLOOR_TILE, "plan_step": 4})
            await _call(session, "fill_tiles", {
                "object_name": ROOM, "tileset_name": TILESET, "cell_min_xy": [0, 0],
                "cell_max_xy": [5, 2], "tile_xy": WALL_TILE, "plane": "XZ", "plan_step": 4})
            view = _returned((await _call(session, "set_pixel_art_view", {"plan_step": 5}))[1])
            if view["viewports_textured"] < 1:
                raise SmokeFailure(f"no open Solid viewport was switched to textures: {view}")
            await _call(session, "run_python", {
                "source": FRAME_ROOM, "plan_step": 6,
                "reason": "no op frames the 3D viewport on an object"})
            report = _returned((await _call(session, "tile_object_report", {"object_name": ROOM}))[1])
            faces = report["faces"]
            floor = [f for f in faces if f["normal"][2] > 0.5]
            wall = [f for f in faces if f["normal"][1] < -0.5]
            problems = []
            if report["face_count"] != FLOOR_CELLS + WALL_CELLS:
                problems.append(f"face_count {report['face_count']} != {FLOOR_CELLS + WALL_CELLS}")
            if len(floor) != FLOOR_CELLS or any(f["tile_xy"] != FLOOR_TILE for f in floor):
                problems.append(f"floor: {len(floor)} faces, tiles {sorted({tuple(f['tile_xy']) for f in floor})}")
            if len(wall) != WALL_CELLS or any(f["tile_xy"] != WALL_TILE for f in wall):
                problems.append(f"wall: {len(wall)} faces, tiles {sorted({tuple(f['tile_xy']) for f in wall})}")
            shot, _ = await _call(session, "get_screenshot_of_window_as_image", {})
            for block in shot.content:
                if block.type == "image":
                    path = OUTPUT / f"window.{block.mimeType.split('/')[1]}"
                    path.write_bytes(base64.b64decode(block.data))
                    print("screenshot:", path)
            if problems:
                raise SmokeFailure("; ".join(problems))
            print(f"LIVE_SMOKE_OK faces={report['face_count']} floor={len(floor)} wall={len(wall)} "
                  f"viewports_textured={view['viewports_textured']}")


if __name__ == "__main__":
    try:
        asyncio.run(main(reload_api="--reload-api" in sys.argv))
    except SmokeFailure as failure:
        print("LIVE_SMOKE_FAILED:", failure)
        sys.exit(1)
