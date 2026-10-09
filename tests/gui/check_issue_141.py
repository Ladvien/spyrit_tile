"""Issue #141: viewport navigation must keep working while a Spyrite Tile tool is active.

Run with tests/gui/run_gui_check.sh tests/gui/check_issue_141.py (own isolated
Blender window, simulated input, no human input needed).

Industry Compatible navigates with Alt+LMB (orbit), Alt+MMB (pan), Alt+RMB (zoom),
and "Emulate 3 Button Mouse" turns Alt+LMB into a middle mouse press. The tool
keymap starts sprytile.tile_picker on the Alt key, snap_cursor on S and
set_normal on N, and those modals used to answer RUNNING_MODAL to every event
while their key was down, so the viewport never saw the drag.

Every navigation case asserts that region_3d.view_matrix changed, with the
Build tool active in Edit mode and the key that starts the modal held. Control:
the tile picker still picks with Alt+click where Alt+LMB is not navigation.
Ends with os._exit(code): 0 pass, 1 failed checks, 2 driver raised, 3 watchdog.
"""
import json
import os
import sys
import time
import traceback

import bpy
import bmesh
import numpy
from bpy_extras.view3d_utils import location_3d_to_region_2d
from mathutils import Vector

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
    with open(os.path.join(OUT, "status_issue_141.json"), "w") as f:
        json.dump(RESULTS, f, indent=1)
    L("FINISH", code)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


def watchdog():
    L("WATCHDOG fired")
    finish(3)


bpy.app.timers.register(watchdog, first_interval=150, persistent=True)


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


def ev(t, v, x, y, **kw):
    win, area, region = view3d()
    win.event_simulate(t, v, x=int(region.x + x), y=int(region.y + y), **kw)


def sd():
    return bpy.context.scene.sprytile_data


def r3d():
    return view3d()[1].spaces.active.region_3d


def view_state():
    rv = r3d()
    return [round(c, 5) for row in rv.view_matrix for c in row] + [round(rv.view_distance, 5)]


def view_delta(a, b):
    return max(abs(x - y) for x, y in zip(a, b))


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


def reset_view():
    with ov():
        bpy.ops.view3d.view_axis(type='TOP')


def drag(button, start, end, hold=None, alt=False, steps=6, strip_alt=False):
    """Hold `hold` (a key such as LEFT_ALT/S/N, or None), drag `button` start -> end.

    strip_alt: the button events carry no Alt flag although the Alt key is held,
    which is what Blender hands to handlers after Emulate 3 Button Mouse turned
    Alt+LMB into a middle mouse press (event_simulate bypasses that translation).
    """
    key_mods = {"alt": True} if alt else {}
    mods = {} if strip_alt else key_mods
    sx, sy = start
    ex, ey = end
    ev('MOUSEMOVE', 'NOTHING', sx, sy, **mods)
    yield 0.25
    if hold:
        ev(hold, 'PRESS', sx, sy, **key_mods)
        yield 0.3
    if button:
        ev(button, 'PRESS', sx, sy, **mods)
        yield 0.2
    for i in range(1, steps + 1):
        ev('MOUSEMOVE', 'NOTHING', sx + (ex - sx) * i // steps, sy + (ey - sy) * i // steps, **mods)
        yield 0.1
    yield 0.2
    if button:
        ev(button, 'RELEASE', ex, ey, **mods)
        yield 0.25
    if hold:
        ev(hold, 'RELEASE', ex, ey)
        yield 0.3


def nav_case(name, button, hold, alt, start, end, strip_alt=False):
    """Run one drag and check the view moved. Returns the delta through RESULTS."""
    reset_view()
    yield 0.4
    before = view_state()
    yield from drag(button, start, end, hold=hold, alt=alt, strip_alt=strip_alt)
    after = view_state()
    d = view_delta(before, after)
    check("view moves: " + name, d > 1e-3, "delta=%.5f" % d)
    # the modal that the held key started must be gone again afterwards
    check("modal flags clear after: " + name, not sd().is_picking and not sd().is_snapping,
          "is_picking=%s is_snapping=%s" % (sd().is_picking, sd().is_snapping))


def wheel_case(name, hold, at):
    reset_view()
    yield 0.4
    before = view_state()
    x, y = at
    ev('MOUSEMOVE', 'NOTHING', x, y)
    yield 0.25
    if hold:
        ev(hold, 'PRESS', x, y)
        yield 0.3
    for _ in range(3):
        ev('WHEELUPMOUSE', 'PRESS', x, y)
        yield 0.2
    if hold:
        ev(hold, 'RELEASE', x, y)
        yield 0.3
    d = view_delta(before, view_state())
    check("view moves: " + name, d > 1e-3, "delta=%.5f" % d)
    check("modal flags clear after: " + name, not sd().is_picking and not sd().is_snapping)


def set_keyconfig(name):
    """Activate a keyconfig preset by name, as the Preferences > Keymap dropdown does."""
    paths = [p for d in bpy.utils.preset_paths('keyconfig') for p in [os.path.join(d, name + ".py")]
             if os.path.exists(p)]
    L("keyconfig preset", name, paths)
    with ov():
        bpy.ops.preferences.keyconfig_activate(filepath=paths[0])
    return bpy.context.preferences.keymap.active_keyconfig


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
        r = bpy.ops.preferences.addon_enable(module=PKG)
    check("addon_enable", PKG in bpy.context.preferences.addons, r)
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
    g = bpy.context.scene.sprytile_mats[0].grids[0]
    g.grid = (16, 16)
    sd().world_pixels = 16
    with ov():
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.view3d.view_axis(type='TOP')
        area.spaces.active.show_region_ui = True
    yield 1.0
    with ov():
        bpy.ops.wm.tool_set_by_id(name='sprytile.tool_build')
    yield 2.0

    # build one face carrying tile (2,1) with a real click
    g.tile_selection = (2, 1, 1, 1)
    cx, cy = region.width // 2, region.height // 2
    ev('MOUSEMOVE', 'NOTHING', cx, cy)
    yield 0.4
    ev('MOUSEMOVE', 'NOTHING', cx + 3, cy + 3)
    yield 0.4
    ev('LEFTMOUSE', 'PRESS', cx + 3, cy + 3)
    yield 0.4
    ev('LEFTMOUSE', 'RELEASE', cx + 3, cy + 3)
    yield 0.6
    bm = bmesh.from_edit_mesh(obj.data)
    check("setup: Build click made a face", len(bm.faces) == 1, len(bm.faces))
    centre = obj.matrix_world @ bm.faces[0].calc_center_median()
    g.tile_selection = (0, 0, 1, 1)
    yield 0.3
    yield from shot("141_00_scene")

    # keep the drags clear of the toolbar and sidebar, away from the palette
    p0 = (cx + 150, cy + 60)
    p1 = (cx + 300, cy + 140)
    centre_px = location_3d_to_region_2d(region, r3d(), centre)
    L("face on screen", centre_px, "drag", p0, p1)

    # ---- default keymap, no emulation: the control
    cfg = bpy.context.preferences.keymap.active_keyconfig
    L("active keyconfig", cfg)
    bpy.context.preferences.inputs.use_mouse_emulate_3_button = False
    yield 0.3
    yield from nav_case("default MMB drag, no key held", 'MIDDLEMOUSE', None, False, p0, p1)
    yield from nav_case("default MMB drag with S held (snap cursor modal active)", 'MIDDLEMOUSE', 'S', False, p0, p1)
    yield from nav_case("default MMB drag with N held (set normal modal active)", 'MIDDLEMOUSE', 'N', False, p0, p1)
    yield from wheel_case("default wheel zoom with S held", 'S', p0)
    yield from wheel_case("default wheel zoom with N held", 'N', p0)
    yield from wheel_case("default wheel zoom with Alt held", 'LEFT_ALT', p0)
    yield from shot("141_01_default_after_nav")

    # ---- default keymap, Emulate 3 Button Mouse: Alt+LMB is a middle press
    bpy.context.preferences.inputs.use_mouse_emulate_3_button = True
    yield 0.3
    # Emulate 3 Button Mouse hands handlers MMB press without the Alt flag
    yield from nav_case("emulate 3 button: Alt+LMB drag arrives as MMB without Alt", 'MIDDLEMOUSE', 'LEFT_ALT', True, p0, p1,
                        strip_alt=True)
    bpy.context.preferences.inputs.use_mouse_emulate_3_button = False
    yield 0.3

    # ---- Industry Compatible
    active = set_keyconfig("Industry_Compatible")
    check("Industry Compatible keyconfig is active", active == "Industry_Compatible", active)
    yield 1.0
    yield from nav_case("industry: Alt+LMB orbit", 'LEFTMOUSE', 'LEFT_ALT', True, p0, p1)
    yield from nav_case("industry: Alt+MMB pan", 'MIDDLEMOUSE', 'LEFT_ALT', True, p0, p1)
    yield from nav_case("industry: Alt+RMB zoom", 'RIGHTMOUSE', 'LEFT_ALT', True, p0, p1)
    yield from wheel_case("industry: wheel zoom with S held", 'S', p0)
    yield from shot("141_02_industry_after_nav")
    # the Alt+LMB press that orbited must not have touched the paint state
    check("industry: Alt+LMB orbit did not paint a face", len(bmesh.from_edit_mesh(obj.data).faces) == 1,
          len(bmesh.from_edit_mesh(obj.data).faces))
    check("industry: Alt+LMB orbit did not pick a tile", tuple(g.tile_selection) == (0, 0, 1, 1), tuple(g.tile_selection))

    # ---- back to the default keymap: Alt+click still picks a tile (the feature the fix must keep)
    set_keyconfig("Blender")
    yield 1.0
    reset_view()
    yield 0.5
    fp = location_3d_to_region_2d(region, r3d(), centre)
    fx, fy = int(fp.x), int(fp.y)
    g.tile_selection = (0, 0, 1, 1)
    ev('MOUSEMOVE', 'NOTHING', fx, fy)
    yield 0.3
    ev('LEFT_ALT', 'PRESS', fx, fy, alt=True)
    yield 0.3
    check("default: tile picker modal started on Alt", sd().is_picking, sd().is_picking)
    ev('MOUSEMOVE', 'NOTHING', fx + 1, fy + 1, alt=True)
    yield 0.3
    ev('LEFTMOUSE', 'PRESS', fx + 1, fy + 1, alt=True)
    yield 0.3
    ev('LEFTMOUSE', 'RELEASE', fx + 1, fy + 1, alt=True)
    yield 0.3
    ev('LEFT_ALT', 'RELEASE', fx + 1, fy + 1)
    yield 0.5
    check("default: Alt+click picks tile (2,1) from the face", tuple(g.tile_selection) == (2, 1, 1, 1), tuple(g.tile_selection))
    check("default: tile picker finished", not sd().is_picking, sd().is_picking)
    yield from shot("141_03_default_after_pick")


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
