"""Visual oracle for tile objects in the Blender you have open, over blended's MCP server.

What it proves, per face, from pixels on screen: the colour the viewport actually draws over the
middle of a tile face matches the colour of the tile the mesh data says is there. That is ground
truth outside the code under test: the expected colour comes from the tileset IMAGE file, the observed
colour from a SCREENSHOT, and the link between them from Blender's own projection
(`view3d_utils.location_3d_to_region_2d`). Neither side reuses the UV/tile maths being tested.

Procedure (each step is an MCP call; the user's view is restored at the end):
1. `tile_object_report` -> faces with world centre, normal and `tile_xy`.
2. `run_python` -> save the viewport state, hide every other visible object, switch to an orthographic view straight down the chosen
   axis (`--view top|front|right`), frame the object (or `--zoom-cell X Y` to fill the view with one cell
   and its neighbours), hide overlays, textured flat shading.
3. `run_python` -> project the 4 corners of every face that faces the camera into region pixels; also
   report the area/region rectangles and every UI region that covers the viewport.
4. `get_screenshot_of_area_as_image VIEW_3D` -> PNG.
5. `run_python` -> restore the saved viewport state.
6. On the host (Pillow): map region pixels to screenshot pixels (scale = image width / area width,
   y flipped, region offset inside the area), take the mean colour of the inner half of each face's
   screen box, compare with the mean colour of the inner half of its tile in the tileset image.
   Write `faces.json`, the screenshot, an annotated copy (boxes green = match, red = mismatch) and
   nearest-neighbour zoom crops of the worst faces into `--out`.

Checks that make the oracle itself trustworthy (fail loudly when violated):
- at least `--min-faces` faces were measurable (on screen, not under a UI panel, >= 6 px boxes);
- the expected colours VARY across measured faces (>= 2 distinct tiles) unless `--allow-uniform`:
  one colour everywhere cannot distinguish a right answer from a constant one;
- the observed colours vary when the expected ones do.

Exit 0 when every measured face is within `--tolerance` (max channel difference, 0-255). Prints
`VISUAL_PROBE_OK ...` or `VISUAL_PROBE_FAILED ...`.

Example:
  /Users/ladvien/blended/.venv/bin/python scripts/visual_probe.py --object spyrite_smoke_room \\
      --tileset tests/fixtures/tiles_16px.png --tile-size 16 --view top
"""

import argparse
import asyncio
import base64
import io
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from PIL import Image, ImageDraw

REPOSITORY = Path(__file__).resolve().parent.parent
BLENDED_MCP = "/Users/ladvien/blended/.venv/bin/blender-mcp"
LAYOUT_PATH = "/tmp/spyrite_visual_probe_layout.json"  # Blender runs on this host; blended truncates long prints
MIN_BOX_PX = 6
INNER_FRACTION = 0.5
WORST_CROPS = 4
CROP_UPSCALE = 8

# view name -> (Euler XYZ degrees of the view rotation, world axis the camera looks along)
VIEWS = {"top": ((0, 0, 0), (0, 0, -1)), "front": ((90, 0, 0), (0, 1, 0)), "right": ((90, 0, 90), (-1, 0, 0))}

SETUP_VIEW = """import bpy, json, math
from mathutils import Euler, Vector
obj = bpy.data.objects[{object_name!r}]
area = next(a for a in bpy.context.screen.areas if a.type == 'VIEW_3D')
space = area.spaces.active
view = space.region_3d
bpy.app.driver_namespace['spyrite_probe_saved'] = dict(
    perspective=view.view_perspective, location=tuple(view.view_location),
    rotation=tuple(view.view_rotation), distance=view.view_distance,
    overlays=space.overlay.show_overlays, shading=space.shading.type,
    color_type=space.shading.color_type, light=space.shading.light,
    hidden=[o.name for o in bpy.context.view_layer.objects if o is not obj and o.visible_get()])
# Isolate the object: anything else drawn in front of it (or coplanar with it) would be
# measured as if it were the tile.
for name in bpy.app.driver_namespace['spyrite_probe_saved']['hidden']:
    bpy.data.objects[name].hide_set(True)
corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
centre = sum(corners, Vector()) / len(corners)
size = max((a - b).length for a in corners for b in corners)
zoom_cell = {zoom_cell!r}
if zoom_cell is not None:
    cell = {cell_size_m!r}
    centre = Vector(zoom_cell) * cell + Vector((cell / 2, cell / 2, 0))
    centre = obj.matrix_world @ centre if {view!r} == 'top' else centre
    size = cell * 3
view.view_perspective = 'ORTHO'
view.view_rotation = Euler([math.radians(v) for v in {euler!r}]).to_quaternion()
view.view_location = centre
view.view_distance = size * 1.5
space.overlay.show_overlays = False
space.shading.type = 'SOLID'
space.shading.color_type = 'TEXTURE'
space.shading.light = 'FLAT'
area.tag_redraw()
"""

PROJECT = """import bpy, json
from bpy_extras.view3d_utils import location_3d_to_region_2d
from mathutils import Vector
obj = bpy.data.objects[{object_name!r}]
area = next(a for a in bpy.context.screen.areas if a.type == 'VIEW_3D')
region = next(r for r in area.regions if r.type == 'WINDOW')
view = area.spaces.active.region_3d
look = Vector({look!r})
mesh = obj.data
world = obj.matrix_world
normal_matrix = world.to_3x3().inverted_safe().transposed()
faces = []
for poly in mesh.polygons:
    normal = (normal_matrix @ poly.normal).normalized()
    if normal.dot(look) > -0.9:
        continue
    pts = [location_3d_to_region_2d(region, view, world @ mesh.vertices[i].co) for i in poly.vertices]
    if any(p is None for p in pts):
        continue
    faces.append(dict(index=poly.index, corners=[[p.x, p.y] for p in pts]))
covers = [dict(type=r.type, x=r.x - area.x, y=r.y - area.y, w=r.width, h=r.height)
          for r in area.regions if r.type != 'WINDOW' and r.width > 1 and r.height > 1]
open({layout_path!r}, 'w').write(json.dumps(dict(area=dict(w=area.width, h=area.height),
      region=dict(x=region.x - area.x, y=region.y - area.y, w=region.width, h=region.height),
      covers=covers, faces=faces)))
print('layout written')
"""

RESTORE = """import bpy
from mathutils import Quaternion, Vector
saved = bpy.app.driver_namespace.pop('spyrite_probe_saved')
area = next(a for a in bpy.context.screen.areas if a.type == 'VIEW_3D')
space = area.spaces.active
view = space.region_3d
view.view_perspective = saved['perspective']
view.view_location = Vector(saved['location'])
view.view_rotation = Quaternion(saved['rotation'])
view.view_distance = saved['distance']
space.overlay.show_overlays = saved['overlays']
space.shading.type = saved['shading']
space.shading.color_type = saved['color_type']
space.shading.light = saved['light']
for name in saved['hidden']:
    if name in bpy.data.objects:
        bpy.data.objects[name].hide_set(False)
area.tag_redraw()
"""


class ProbeFailure(AssertionError):
    pass


async def _call(session, name, arguments):
    result = await session.call_tool(name, arguments)
    texts = [c.text for c in result.content if c.type == "text"]
    if result.isError:
        raise ProbeFailure(f"{name} failed: {' | '.join(texts)[:2000]}")
    return result, "\n".join(texts)


def _returned(text):
    for line in text.splitlines():
        if line.strip().startswith("returned:"):
            return json.loads(line.split("returned:", 1)[1])
    raise ProbeFailure(f"no 'returned:' line in {text[:400]!r}")


def _layout(text):
    if "layout written" not in text:
        raise ProbeFailure(f"projection did not run: {text[:400]!r}")
    return json.loads(Path(LAYOUT_PATH).read_text())


def _mean(image, box):
    crop = image.crop(tuple(int(round(v)) for v in box)).convert("RGB")
    pixels = list(crop.get_flattened_data())
    return [sum(p[c] for p in pixels) / len(pixels) for c in range(3)]


def _inner(box, fraction=INNER_FRACTION):
    x0, y0, x1, y1 = box
    dx, dy = (x1 - x0) * (1 - fraction) / 2, (y1 - y0) * (1 - fraction) / 2
    return (x0 + dx, y0 + dy, x1 - dx, y1 - dy)


def _covered(point, covers):
    x, y = point
    return any(c["x"] <= x <= c["x"] + c["w"] and c["y"] <= y <= c["y"] + c["h"] for c in covers)


def measure(layout, shot, tileset, tile_size, tiles_by_index, tolerance):
    scale = shot.width / layout["area"]["w"]
    region = layout["region"]
    results = []
    for face in layout["faces"]:
        area_pts = [(region["x"] + x, region["y"] + y) for x, y in face["corners"]]
        if any(_covered(p, layout["covers"]) for p in area_pts):
            continue
        xs = [p[0] * scale for p in area_pts]
        ys = [shot.height - p[1] * scale for p in area_pts]
        box = (min(xs), min(ys), max(xs), max(ys))
        if box[0] < 0 or box[1] < 0 or box[2] > shot.width or box[3] > shot.height:
            continue
        if box[2] - box[0] < MIN_BOX_PX or box[3] - box[1] < MIN_BOX_PX:
            continue
        tile_xy = tiles_by_index.get(face["index"])
        if tile_xy is None or tile_xy == [-1, -1]:
            continue
        col, row = tile_xy
        tile_box = (col * tile_size, row * tile_size, (col + 1) * tile_size, (row + 1) * tile_size)
        expected = _mean(tileset, _inner(tile_box))
        observed = _mean(shot, _inner(box))
        delta = max(abs(e - o) for e, o in zip(expected, observed))
        results.append(dict(index=face["index"], tile_xy=tile_xy, screen_box=[round(v, 1) for v in box],
                            expected_rgb=[round(v, 1) for v in expected],
                            observed_rgb=[round(v, 1) for v in observed],
                            max_channel_delta=round(delta, 1), ok=delta <= tolerance))
    return results


def write_evidence(out, shot, results):
    out.mkdir(parents=True, exist_ok=True)
    for stale in out.glob("zoom_*.png"):
        stale.unlink()
    shot.save(out / "screenshot.png")
    annotated = shot.convert("RGB")
    draw = ImageDraw.Draw(annotated)
    for r in results:
        draw.rectangle(r["screen_box"], outline=(0, 255, 0) if r["ok"] else (255, 0, 0), width=2)
    annotated.save(out / "annotated.png")
    for rank, r in enumerate(sorted(results, key=lambda r: -r["max_channel_delta"])[:WORST_CROPS]):
        x0, y0, x1, y1 = r["screen_box"]
        pad = (x1 - x0) / 2
        crop = shot.crop((int(max(0, x0 - pad)), int(max(0, y0 - pad)),
                          int(min(shot.width, x1 + pad)), int(min(shot.height, y1 + pad))))
        crop.resize((crop.width * CROP_UPSCALE, crop.height * CROP_UPSCALE), Image.Resampling.NEAREST).save(
            out / f"zoom_{rank}_face{r['index']}.png")
    (out / "faces.json").write_text(json.dumps(results, indent=1))


async def main(args):
    look = VIEWS[args.view][1]
    async with stdio_client(StdioServerParameters(command=BLENDED_MCP, args=[])) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            report = _returned((await _call(session, "tile_object_report",
                                            {"object_name": args.object}))[1])
            tiles_by_index = {f["index"]: f["tile_xy"] for f in report["faces"]}
            await _call(session, "declare_plan", {"steps": ["set probe view", "measure", "restore view"]})
            setup = SETUP_VIEW.format(object_name=args.object, zoom_cell=args.zoom_cell,
                                      cell_size_m=args.cell_size_m, view=args.view,
                                      euler=VIEWS[args.view][0])
            reason = "no op sets an orthographic probe view, projects faces to pixels or restores the view"
            await _call(session, "run_python", {"source": setup, "plan_step": 1, "reason": reason})
            try:
                Path(LAYOUT_PATH).unlink(missing_ok=True)
                _, text = await _call(session, "run_python", {
                    "source": PROJECT.format(object_name=args.object, look=look, layout_path=LAYOUT_PATH),
                    "plan_step": 2, "reason": reason})
                layout = _layout(text)
                shot_result, _ = await _call(session, "get_screenshot_of_area_as_image",
                                             {"area_ui_type": "VIEW_3D"})
            finally:
                await _call(session, "run_python", {"source": RESTORE, "plan_step": 3, "reason": reason})
    image_block = next(c for c in shot_result.content if c.type == "image")
    shot = Image.open(io.BytesIO(base64.b64decode(image_block.data))).convert("RGB")
    tileset = Image.open(args.tileset).convert("RGB")
    results = measure(layout, shot, tileset, args.tile_size, tiles_by_index, args.tolerance)
    out = Path(args.out)
    write_evidence(out, shot, results)

    problems = []
    if len(results) < args.min_faces:
        problems.append(f"only {len(results)} faces measurable (need {args.min_faces}); "
                        f"{len(layout['faces'])} faced the camera")
    expected_tiles = {tuple(r["tile_xy"]) for r in results}
    if len(expected_tiles) < 2 and not args.allow_uniform:
        problems.append(f"expected colours do not vary (tiles {sorted(expected_tiles)}): the probe "
                        f"cannot tell a right answer from a constant one; place >= 2 different tiles "
                        f"or pass --allow-uniform")
    observed = {tuple(round(v / 8) for v in r["observed_rgb"]) for r in results}
    if len(expected_tiles) >= 2 and len(observed) < 2:
        problems.append("observed colours are uniform although different tiles are placed")
    bad = [r for r in results if not r["ok"]]
    if bad:
        worst = max(bad, key=lambda r: r["max_channel_delta"])
        problems.append(f"{len(bad)}/{len(results)} faces differ by > {args.tolerance}; worst face "
                        f"{worst['index']} tile {worst['tile_xy']} expected {worst['expected_rgb']} "
                        f"observed {worst['observed_rgb']}")
    summary = (f"measured={len(results)} tiles={len(expected_tiles)} "
               f"max_delta={max((r['max_channel_delta'] for r in results), default=0)} evidence={out}")
    if problems:
        raise ProbeFailure(summary + " :: " + " | ".join(problems))
    print("VISUAL_PROBE_OK", summary)


def _parse(argv):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--object", required=True)
    parser.add_argument("--tileset", required=True, help="tileset image file the object uses")
    parser.add_argument("--tile-size", type=int, required=True, help="tile edge in px")
    parser.add_argument("--view", choices=sorted(VIEWS), default="top")
    parser.add_argument("--zoom-cell", type=float, nargs=2, default=None, metavar=("X", "Y"),
                        help="zoom on one grid cell (top view) instead of framing the object")
    parser.add_argument("--cell-size-m", type=float, default=1.0)
    parser.add_argument("--tolerance", type=float, default=12.0, help="max channel delta, 0-255")
    parser.add_argument("--min-faces", type=int, default=1)
    parser.add_argument("--allow-uniform", action="store_true")
    parser.add_argument("--out", default=str(REPOSITORY / "outputs" / "visual_probe"))
    return parser.parse_args(argv)


if __name__ == "__main__":
    arguments = _parse(sys.argv[1:])
    arguments.zoom_cell = tuple(arguments.zoom_cell) if arguments.zoom_cell else None
    try:
        asyncio.run(main(arguments))
    except* ProbeFailure as group:  # the MCP client wraps errors raised inside its task group
        for failure in group.exceptions:
            print("VISUAL_PROBE_FAILED", failure)
        sys.exit(1)
