"""Issue #111: the palette must zoom with macOS trackpad / Magic Mouse input.

On macOS a Magic Mouse or trackpad scroll arrives as TRACKPADPAN (precise
deltas), a pinch as TRACKPADZOOM; neither is WHEELUPMOUSE/WHEELDOWNMOUSE, so the
palette ignored them. This drives simulated TRACKPADPAN / TRACKPADZOOM events
(Window.event_simulate, the delta is the move from the previous pointer position
to the event position) over the palette and over the viewport, and asserts:

* over the palette: pan and pinch change scene.sprytile_ui.zoom, in both
  directions, and the palette consumes the event (the 3D view does not move);
* over the viewport (not the palette): zoom unchanged and the event passes
  through to Blender's own navigation (the 3D view does move).

Run it with tests/gui/run_gui_check.sh tests/gui/check_issue_111.py
(exit 0 pass, 1 failed checks, 2 driver error, 3 watchdog).
"""
import json
import os
import sys
import time
import traceback

import bpy
import numpy

OUT = os.environ["SPYRITE_GUI_OUT"]
PKG = "bl_ext.user_default.spyrite_tile"
FIXTURE = os.path.join(OUT, "tiles_16px.png")
RESULTS = {"checks": {}, "notes": []}
START = time.time()


def L(*a):
    print("SPYRITE_GUI", *a, flush=True)


def check(name, ok, detail=""):
    RESULTS["checks"][name] = {"ok": bool(ok), "detail": str(detail)}
    L(("PASS " if ok else "FAIL ") + name, detail)


def finish(code):
    RESULTS["exit_code"] = code
    RESULTS["elapsed_s"] = round(time.time() - START, 1)
    with open(os.path.join(OUT, "status_issue_111.json"), "w") as f:
        json.dump(RESULTS, f, indent=1)
    L("FINISH", code)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


def watchdog():
    L("WATCHDOG fired")
    finish(3)


bpy.app.timers.register(watchdog, first_interval=110, persistent=True)


def view3d():
    win = bpy.context.window_manager.windows[0]
    for area in win.screen.areas:
        if area.type == 'VIEW_3D':
            region = next(r for r in area.regions if r.type == 'WINDOW')
            return win, area, region


def ov():
    win, area, region = view3d()
    return bpy.context.temp_override(window=win, area=area, region=region)


def shot(name):
    win, area, region = view3d()
    for r in area.regions:
        r.tag_redraw()
    area.tag_redraw()
    yield 0.5
    path = os.path.join(OUT, name + ".png")
    win, area, region = view3d()
    try:
        with bpy.context.temp_override(window=win, area=area, region=region):
            bpy.ops.screen.screenshot_area(filepath=path)
        L("screenshot", path, os.path.exists(path))
    except Exception as e:
        L("screenshot failed", repr(e))


def sim(kind, x, y, value='NOTHING', **mods):
    """Simulate one event at region pixel (x, y)."""
    win, area, region = view3d()
    win.event_simulate(kind, value, x=int(region.x + x), y=int(region.y + y), **mods)


def gui_cls():
    import importlib
    return importlib.import_module(PKG + ".sprytile_gui").VIEW3D_OP_SprytileGui


def palette_origin():
    """Bottom-left of the palette in region pixels: clear of the toolbar, plus the 5 px edge."""
    win, area, region = view3d()
    left = 5
    bottom = 5
    for r in area.regions:
        if r.type == 'WINDOW' or r.width <= 1 or r.height <= 1:
            continue
        if r.alignment == 'LEFT':
            left = max(left, r.width + 5)
        elif r.alignment == 'BOTTOM':
            bottom += r.height
    return left, bottom


def make_fixture():
    """64x64 PNG, 4x4 solid 16 px tiles in 16 distinct saturated colours."""
    import colorsys
    w = h = 64
    pixels = numpy.zeros((h, w, 4), dtype=numpy.float32)
    for row in range(4):
        for col in range(4):
            i = row * 4 + col
            rgb = colorsys.hsv_to_rgb((i * 7 % 16) / 16.0, 1.0, 0.55 + 0.45 * (i % 2))
            y0 = (3 - row) * 16
            pixels[y0:y0 + 16, col * 16:(col + 1) * 16] = (*rgb, 1.0)
    image = bpy.data.images.new("tiles_16px_gen", w, h, alpha=True)
    image.pixels.foreach_set(pixels.reshape(-1))
    image.filepath_raw = FIXTURE
    image.file_format = 'PNG'
    image.save()
    bpy.data.images.remove(image)


def view_distance():
    win, area, region = view3d()
    return area.spaces.active.region_3d.view_distance


def steps():
    yield 2.5
    win, area, region = view3d()
    # dismiss the splash popup
    win.event_simulate('MOUSEMOVE', 'NOTHING', x=region.x + 200, y=region.y + 100)
    yield 0.5
    win.event_simulate('MOUSEMOVE', 'NOTHING', x=region.x + 220, y=region.y + 120)
    yield 0.5
    win.event_simulate('ESC', 'PRESS', x=region.x + 220, y=region.y + 120)
    yield 0.2
    win.event_simulate('ESC', 'RELEASE', x=region.x + 220, y=region.y + 120)
    yield 0.8
    with ov():
        bpy.ops.preferences.addon_enable(module=PKG)
    check("addon_enable", PKG in bpy.context.preferences.addons)
    yield 0.5
    make_fixture()
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    mesh = bpy.data.meshes.new("tilemesh")
    obj = bpy.data.objects.new("tileobj", mesh)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    yield 0.3
    with ov():
        r = bpy.ops.sprytile.tileset_load('EXEC_DEFAULT', filepath=FIXTURE)
    check("tileset_load", r == {'FINISHED'}, r)
    yield 0.5
    scene = bpy.context.scene
    scene.sprytile_mats[0].grids[0].grid = (16, 16)
    with ov():
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.view3d.view_axis(type='TOP')
    yield 1.0
    with ov():
        bpy.ops.wm.tool_set_by_id(name='sprytile.tool_build')
    yield 2.0
    G = gui_cls()
    check("palette running", G.is_running, "is_running=%s" % G.is_running)
    ui = scene.sprytile_ui
    win, area, region = view3d()
    px0, py0 = palette_origin()
    ts = ui.zoom * 64 / 4.0
    # a point inside the palette, and one well clear of it (top right of the viewport)
    inx, iny = int(px0 + ts * 2), int(py0 + ts * 2)
    outx, outy = region.width - 150, region.height - 150
    L("palette zoom", ui.zoom, "inside", (inx, iny), "outside", (outx, outy), "region", region.width, region.height)
    yield from shot("111_00_start")

    rv3d = area.spaces.active.region_3d

    def settle():
        yield 0.4

    def over(dx, dy):
        """Pointer at (inx+dx, iny+dy); the event then moves it to (inx, iny): delta = (-dx, -dy)."""
        sim('MOUSEMOVE', inx + dx, iny + dy)

    view0 = (rv3d.view_distance, tuple(rv3d.view_location))
    z0 = ui.zoom

    # ---- scroll over the palette: down zooms out, up zooms back in (30 px = one step)
    over(0, 65)
    yield 0.3
    sim('TRACKPADPAN', inx, iny)          # delta y = -65 -> 2 steps out (>2.0: 0.5 per step)
    yield 0.4
    z1 = ui.zoom
    check("TRACKPADPAN down over palette zooms out by 2 steps", abs(z1 - (z0 - 1.0)) < 1e-6, "%s -> %s" % (z0, z1))
    yield from shot("111_01_scrolled_out")
    over(0, -65)
    yield 0.3
    sim('TRACKPADPAN', inx, iny)          # delta y = +65 -> 2 steps in
    yield 0.4
    z2 = ui.zoom
    check("TRACKPADPAN up over palette zooms back in", z1 != z0 and abs(z2 - z0) < 1e-6, "%s -> %s" % (z1, z2))

    # ---- small deltas accumulate: 12 px three times is 36 px, one step, on the third event only
    zs = []
    for _ in range(3):
        over(0, -12)
        yield 0.3
        sim('TRACKPADPAN', inx, iny)
        yield 0.3
        zs.append(ui.zoom)
    check("small TRACKPADPAN deltas accumulate to one step on the 3rd event",
          zs[0] == z0 and zs[1] == z0 and abs(zs[2] - (z0 + 0.5)) < 1e-6, "zoom after each event %s (start %s)" % (zs, z0))
    over(0, 30)
    yield 0.3
    sim('TRACKPADPAN', inx, iny)          # one step back out
    yield 0.4
    check("scroll down by 30 px is exactly one step out", zs[2] != z0 and abs(ui.zoom - z0) < 1e-6, "%s -> %s" % (zs[2], ui.zoom))

    # ---- pinch over the palette: inwards zooms out, outwards zooms in (10 px = one step)
    sim('MOUSEMOVE', inx + 13, iny + 12)
    yield 0.3
    sim('TRACKPADZOOM', inx, iny)         # delta (-13, -12) = -25 -> 2 steps out
    yield 0.4
    z3 = ui.zoom
    check("TRACKPADZOOM inwards over palette zooms out by 2 steps", abs(z3 - (z0 - 1.0)) < 1e-6, "%s -> %s" % (z0, z3))
    sim('MOUSEMOVE', inx - 13, iny - 12)
    yield 0.3
    sim('TRACKPADZOOM', inx, iny)         # delta (+13, +12) -> 2 steps in
    yield 0.4
    check("TRACKPADZOOM outwards over palette zooms back in", z3 != z0 and abs(ui.zoom - z0) < 1e-6, "%s -> %s" % (z3, ui.zoom))

    # ---- the palette consumed all of that, the 3D view did not move
    check("palette gestures do not navigate the 3D view",
          abs(rv3d.view_distance - view0[0]) < 1e-6 and tuple(rv3d.view_location) == view0[1],
          "distance %s -> %s, location %s -> %s" % (view0[0], rv3d.view_distance, view0[1], tuple(rv3d.view_location)))

    # ---- the same gestures over the viewport, clear of the palette: zoom untouched, view navigates
    # (the pointer was last over the palette: the gesture position decides, not the last MOUSEMOVE)
    over(0, 0)
    yield 0.3
    sim('TRACKPADZOOM', outx, outy)       # first gesture after the pointer was over the palette (a stray jump)
    yield 0.3
    check("TRACKPADZOOM jumping out of the palette leaves the palette zoom alone", ui.zoom == z0, "%s" % ui.zoom)
    rv3d.view_distance = view0[0]        # the jump above zoomed the view to its limit
    sim('MOUSEMOVE', outx - 13, outy - 12)
    yield 0.3
    sim('TRACKPADZOOM', outx, outy)       # a pinch of the same size as the palette one, far from it
    yield 0.5
    check("TRACKPADZOOM over the viewport leaves the palette zoom alone", ui.zoom == z0, "%s" % ui.zoom)
    d1 = rv3d.view_distance
    check("TRACKPADZOOM over the viewport reaches Blender's view zoom", abs(d1 - view0[0]) > 1e-6,
          "view_distance %s -> %s" % (view0[0], d1))
    sim('MOUSEMOVE', outx - 40, outy - 60)
    yield 0.3
    rot0 = tuple(rv3d.view_rotation)
    sim('TRACKPADPAN', outx, outy)        # a 60 px scroll, which would be 2 palette steps
    yield 0.5
    check("TRACKPADPAN over the viewport leaves the palette zoom alone", ui.zoom == z0, "%s" % ui.zoom)
    # a plain trackpad scroll orbits in Blender's default keymap
    check("TRACKPADPAN over the viewport reaches Blender's view orbit", tuple(rv3d.view_rotation) != rot0,
          "view_rotation %s -> %s" % (tuple(round(v, 4) for v in rot0), tuple(round(v, 4) for v in rv3d.view_rotation)))
    yield from shot("111_02_viewport_gestures")
    check("palette still running", G.is_running)


def runner():
    state = {"gen": steps()}

    def tick():
        try:
            return next(state["gen"])
        except StopIteration:
            bad = [k for k, v in RESULTS["checks"].items() if not v["ok"]]
            finish(0 if not bad else 1)
        except Exception:
            traceback.print_exc()
            RESULTS["notes"].append("driver exception: " + traceback.format_exc())
            finish(2)
        return None

    return tick


bpy.app.timers.register(runner(), first_interval=1.0, persistent=True)
