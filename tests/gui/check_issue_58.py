"""GUI check for issue #58: Pixel Translate (G while a Spyrite Tile tool is active).

Drives the real operator with simulated input (Window.event_simulate) in a real,
isolated Blender window: Build tool active, one vertex selected in Edit Mode, press G,
drag the mouse, click. Run it with tests/gui/run_gui_check.sh.

For objects with identity, scale (2,2,2), a 90 degree Z rotation, a non-uniform
scale and a parent with scale+rotation, in the Top, Front and Right views (normal
mode Z, Y, X), it measures the selected vertex in WORLD space before and after and
asserts:
  * the vertex moved only inside the constrained plane,
  * the world delta is a whole number of world pixels (multiples of 1/world_pixels),
  * the on-screen readout drawn by the operator equals that world delta in pixels.

Ends the process itself with os._exit: 0 pass, 1 failed checks, 2 driver error, 3 watchdog.
"""
import math
import os
import re
import sys
import time
import traceback

import bpy
import bmesh
import blf
import numpy
from bpy_extras.view3d_utils import location_3d_to_region_2d
from mathutils import Euler, Matrix, Vector

OUT = os.environ["SPYRITE_GUI_OUT"]
PKG = "bl_ext.user_default.spyrite_tile"
FIXTURE = os.path.join(OUT, "tiles_16px.png")
WORLD_PIXELS = 16
UNIT = 1.0 / WORLD_PIXELS
NAME = "issue_58"
RESULTS = {"checks": {}, "notes": []}
START = time.time()


def L(*a):
    print("SPYRITE_GUI", *a, flush=True)


def check(name, ok, detail=""):
    RESULTS["checks"][name] = bool(ok)
    L(("PASS " if ok else "FAIL ") + name, detail)


def finish(code):
    L("FINISH", code, "elapsed", round(time.time() - START, 1))
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


bpy.app.timers.register(lambda: (L("WATCHDOG fired"), finish(3)), first_interval=150, persistent=True)


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
    with bpy.context.temp_override(window=win, area=area, region=region):
        bpy.ops.screen.screenshot_area(filepath=path)
    L("screenshot", path, os.path.exists(path))


def ev(kind, value, x, y, **mods):
    win, area, region = view3d()
    win.event_simulate(kind, value, x=int(region.x + x), y=int(region.y + y), **mods)


def sd():
    return bpy.context.scene.sprytile_data


def make_fixture():
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


# --------------------------------------------------------------------------
# The readout the operator draws is the thing under test, record it as it is drawn
READOUT = []
_orig_blf_draw = blf.draw


READOUT_POS = []
_orig_blf_position = blf.position
_last_pos = [None]


def _recording_position(font_id, x, y, z):
    _last_pos[0] = (x, y)
    return _orig_blf_position(font_id, x, y, z)


def _recording_draw(font_id, text):
    READOUT.append(text)
    READOUT_POS.append((text, _last_pos[0]))
    return _orig_blf_draw(font_id, text)


blf.position = _recording_position
blf.draw = _recording_draw


def last_readout():
    """(x, y, z) ints from the most recent complete "X : n / Y : n / Z : n" draw, or None."""
    vals = {}
    for text in reversed(READOUT):
        m = re.fullmatch(r"([XYZ]) : (-?\d+)", text)
        if m and m.group(1) not in vals:
            vals[m.group(1)] = int(m.group(2))
        if len(vals) == 3:
            return tuple(vals[a] for a in "XYZ")
    return None


# --------------------------------------------------------------------------
def grid_error(world_delta):
    """Largest distance of any component of a world delta from a whole number of pixels."""
    return max(abs(c / UNIT - round(c / UNIT)) for c in world_delta) * UNIT


# (name, object matrix parameters, parent matrix parameters or None, local position of the moved vertex)
# Local vertex positions are chosen so the vertex starts exactly on the world pixel grid.
CONFIGS = [
    dict(name="identity", loc=(0, 0, 0), rot=(0, 0, 0), scale=(1, 1, 1), parent=None,
         vert=(0.1875, 0.25, 0.0625)),
    dict(name="scale2", loc=(0.5, -0.25, 0.125), rot=(0, 0, 0), scale=(2, 2, 2), parent=None,
         vert=(0.125, 0.25, 0.0625)),
    dict(name="rotz90", loc=(0.25, 0.5, 0), rot=(0, 0, math.pi / 2), scale=(1, 1, 1), parent=None,
         vert=(0.25, 0.125, 0.0625)),
    dict(name="nonuniform", loc=(-0.25, 0.125, 0.5), rot=(0, 0, 0), scale=(2, 1, 0.5), parent=None,
         vert=(0.125, 0.1875, 0.125)),
    dict(name="parented", loc=(0.0625, 0, 0.0625), rot=(0, 0, 0), scale=(1, 1, 1),
         parent=dict(loc=(0.5, -0.25, 0.125), rot=(0, 0, math.pi / 2), scale=(2, 2, 2)),
         vert=(0.125, 0.0625, 0.09375)),
]
# view, expected normal mode, plane axes that may change (indices into xyz), drag in px
VIEWS = [
    ("TOP", 'Z', (0, 1), (+90, +61)),
    ("FRONT", 'Y', (0, 2), (-77, +43)),
    ("RIGHT", 'X', (1, 2), (+55, -88)),
]


def build_object(cfg):
    mesh = bpy.data.meshes.new(NAME + "_" + cfg["name"])
    x, y, z = cfg["vert"]
    # vertex 0 is the one that gets moved; the rest only give the mesh some body
    verts = [(x, y, z), (x + 0.5, y, z), (x + 0.5, y + 0.5, z), (x, y + 0.5, z + 0.25)]
    mesh.from_pydata(verts, [], [(0, 1, 2, 3)])
    obj = bpy.data.objects.new(NAME + "_" + cfg["name"], mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.location = cfg["loc"]
    obj.rotation_euler = cfg["rot"]
    obj.scale = cfg["scale"]
    if cfg["parent"]:
        p = cfg["parent"]
        empty = bpy.data.objects.new(NAME + "_parent", None)
        bpy.context.scene.collection.objects.link(empty)
        empty.location = p["loc"]
        empty.rotation_euler = p["rot"]
        empty.scale = p["scale"]
        obj.parent = empty
    bpy.context.view_layer.update()
    return obj


def world_vert0(obj):
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    return obj.matrix_world @ bm.verts[0].co


def select_vert0(obj):
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.select_mode = {'VERT'}
    for v in bm.verts:
        v.select = False
    bm.select_history.clear()
    bm.verts[0].select = True
    bm.select_history.add(bm.verts[0])
    bm.select_flush_mode()
    bmesh.update_edit_mesh(obj.data)


def steps():
    yield 2.5
    win, area, region = view3d()
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
    sd().world_pixels = WORLD_PIXELS

    # negative control for the grid checker itself: an off-grid delta must be flagged
    check("control: grid_error flags an off-grid delta", grid_error((UNIT * 2.5, 0, 0)) > UNIT * 0.4,
          grid_error((UNIT * 2.5, 0, 0)))
    check("control: grid_error accepts an on-grid delta", grid_error((UNIT * 3, -UNIT * 7, 0)) < 1e-9)

    summary = []
    for cfg in CONFIGS:
        obj = build_object(cfg)
        for o in bpy.context.view_layer.objects:
            o.select_set(False)
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        with ov():
            r = bpy.ops.sprytile.tileset_load('EXEC_DEFAULT', filepath=FIXTURE)
        check("[%s] tileset_load" % cfg["name"], r == {'FINISHED'}, r)
        mat0 = bpy.context.scene.sprytile_mats[len(bpy.context.scene.sprytile_mats) - 1]
        mat0.grids[0].grid = (16, 16)
        sd().world_pixels = WORLD_PIXELS
        yield 0.3
        with ov():
            bpy.ops.object.mode_set(mode='EDIT')
        area.spaces.active.show_region_ui = False
        yield 0.5
        with ov():
            bpy.ops.wm.tool_set_by_id(name='sprytile.tool_build')
        yield 1.5
        for view, expect_mode, plane, drag in VIEWS:
            tag = "[%s/%s]" % (cfg["name"], view)
            select_vert0(obj)
            w0 = world_vert0(obj)
            on_grid0 = grid_error(w0)
            check(tag + " precondition: vertex starts on world pixel grid", on_grid0 < 1e-6, "world=%s err=%g" % (tuple(w0), on_grid0))
            with ov():
                bpy.ops.view3d.view_axis(type=view)
                r3d = area.spaces.active.region_3d
                r3d.view_perspective = 'ORTHO'
                r3d.view_location = w0
                r3d.view_distance = 2.0
            yield 0.6
            win, area, region = view3d()
            r3d = area.spaces.active.region_3d
            p = location_3d_to_region_2d(region, r3d, w0)
            px, py = int(p.x), int(p.y)
            ev('MOUSEMOVE', 'NOTHING', px - 4, py - 4)
            yield 0.3
            ev('MOUSEMOVE', 'NOTHING', px, py)
            yield 0.4
            check(tag + " normal mode follows the view", sd().normal_mode == expect_mode, sd().normal_mode)
            READOUT.clear()
            READOUT_POS.clear()
            ts = bpy.context.scene.tool_settings
            snap0 = (ts.use_snap, set(ts.snap_elements), ts.transform_pivot_point)
            ev('G', 'PRESS', px, py)
            yield 0.15
            ev('G', 'RELEASE', px, py)
            yield 0.9
            check(tag + " pixel translate started", sd().is_grid_translate, sd().is_grid_translate)
            # drag in a few steps
            steps_n = 4
            for i in range(1, steps_n + 1):
                ev('MOUSEMOVE', 'NOTHING', px + drag[0] * i // steps_n, py + drag[1] * i // steps_n)
                yield 0.2
            yield 0.5
            readout = last_readout()
            # every row must be inside the part of the region no header covers
            win, area, region = view3d()
            top_cover = sum(r.height for r in area.regions
                            if r.type in {'HEADER', 'TOOL_HEADER'} and r.alignment == 'TOP' and r.height > 1)
            rows = READOUT_POS[-3:]
            row_ok = len(rows) == 3 and all(
                pos is not None and 0 <= pos[0] and pos[0] + 60 <= region.width and
                0 <= pos[1] and pos[1] + 16 <= region.height - top_cover for _t, pos in rows)
            check(tag + " all three readout rows are on screen, not under a header", row_ok,
                  "rows=%s region=%dx%d header cover=%d" % (rows, region.width, region.height, top_cover))
            if view == "TOP":
                yield from shot("issue58_drag_" + cfg["name"] + "_" + view)
            ev('LEFTMOUSE', 'PRESS', px + drag[0], py + drag[1])
            yield 0.2
            ev('LEFTMOUSE', 'RELEASE', px + drag[0], py + drag[1])
            yield 0.9
            check(tag + " pixel translate finished", not sd().is_grid_translate, sd().is_grid_translate)
            w1 = world_vert0(obj)
            delta = w1 - w0
            moved = max(abs(c) for c in delta)
            err = grid_error(delta)
            out_of_plane = max(abs(delta[i]) for i in range(3) if i not in plane)
            dpx = tuple(round(c / UNIT, 3) for c in delta)
            L(tag, "world0", tuple(round(c, 5) for c in w0), "world1", tuple(round(c, 5) for c in w1),
              "delta_px", dpx, "readout", readout)
            summary.append((tag, dpx, readout))
            check(tag + " vertex moved", moved > UNIT * 0.5, "delta_px=%s" % (dpx,))
            check(tag + " moved only inside the constrained plane", out_of_plane < 1e-6,
                  "off-plane world delta %g" % out_of_plane)
            check(tag + " world delta is a whole number of world pixels", err < 1e-6,
                  "delta_px=%s err=%g" % (dpx, err))
            expect_readout = tuple(int(round(c / UNIT)) for c in delta)
            check(tag + " readout equals the world delta in pixels", readout == expect_readout,
                  "readout=%s world delta px=%s" % (readout, expect_readout))
            ts = bpy.context.scene.tool_settings
            snap1 = (ts.use_snap, set(ts.snap_elements), ts.transform_pivot_point)
            check(tag + " snap settings restored", snap1 == snap0, "%s -> %s" % (snap0, snap1))
        with ov():
            bpy.ops.object.mode_set(mode='OBJECT')
        yield 0.5
    # G must start the pixel translate from each of the three tools, and Esc must undo it
    obj = bpy.context.view_layer.objects.active
    with ov():
        bpy.ops.object.mode_set(mode='EDIT')
    yield 0.5
    for tool in ('sprytile.tool_build', 'sprytile.tool_paint', 'sprytile.tool_fill'):
        with ov():
            bpy.ops.wm.tool_set_by_id(name=tool)
        yield 1.5
        select_vert0(obj)
        w0 = world_vert0(obj)
        win, area, region = view3d()
        p = location_3d_to_region_2d(region, area.spaces.active.region_3d, w0)
        px, py = int(p.x), int(p.y)
        ev('MOUSEMOVE', 'NOTHING', px, py)
        yield 0.4
        ev('G', 'PRESS', px, py)
        yield 0.15
        ev('G', 'RELEASE', px, py)
        yield 0.9
        check("[%s] G starts pixel translate" % tool, sd().is_grid_translate, sd().is_grid_translate)
        ev('MOUSEMOVE', 'NOTHING', px + 30, py + 20)
        yield 0.4
        for _ in range(2):
            ev('ESC', 'PRESS', px + 30, py + 20)
            yield 0.3
            ev('ESC', 'RELEASE', px + 30, py + 20)
            yield 0.5
        w1 = world_vert0(obj)
        check("[%s] Esc ends it and leaves the vertex alone" % tool,
              (not sd().is_grid_translate) and (w1 - w0).length < 1e-6, "%s -> %s" % (tuple(w0), tuple(w1)))
    with ov():
        bpy.ops.object.mode_set(mode='OBJECT')
    yield 0.5
    RESULTS["notes"].append(summary)
    yield from shot("issue58_done")


def runner():
    state = {"gen": steps()}

    def tick():
        try:
            return next(state["gen"])
        except StopIteration:
            bad = [k for k, v in RESULTS["checks"].items() if not v]
            L("FAILED checks:", len(bad))
            finish(0 if not bad else 1)
        except Exception:
            traceback.print_exc()
            finish(2)
        return None

    return tick


bpy.app.timers.register(runner(), first_interval=1.0, persistent=True)
