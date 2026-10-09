"""GUI check for issue #143: the Build tool must start and build on every render engine.

Workbench does not accept shading.type = 'MATERIAL' (TypeError "enum MATERIAL not found in
('WIREFRAME', 'SOLID', 'RENDERED')"), and invoke() wrote it unguarded when the
auto_adjust_viewport_shading preference (default on) is set. For EEVEE, Workbench, Cycles and a
custom render engine this activates the Build tool, clicks in the viewport and asserts the tool
runs, a face is built, the shading type is MATERIAL where the engine allows it and unchanged
where it does not. Run with tests/gui/run_gui_check.sh tests/gui/check_issue_143.py.
Exit codes: 0 pass, 1 failed checks, 2 driver error, 3 watchdog.
"""
import json
import os
import sys
import time
import traceback

import bpy
import bmesh
import numpy

OUT = os.environ["SPYRITE_GUI_OUT"]
PKG = "bl_ext.user_default.spyrite_tile"
FIXTURE = os.path.join(OUT, "tiles_16px.png")
FIXTURE_COLOURS = {}
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
    with open(os.path.join(OUT, "status_issue143.json"), "w") as f:
        json.dump(RESULTS, f, indent=1)
    L("FINISH", code)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


def watchdog():
    L("WATCHDOG fired")
    finish(3)


bpy.app.timers.register(watchdog, first_interval=120, persistent=True)


class CustomEngine(bpy.types.RenderEngine):
    bl_idname = 'SPRY_TEST_ENGINE'
    bl_label = 'Spyrite test engine'

    def render(self, depsgraph):
        pass


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


def move(x, y):
    win, area, region = view3d()
    win.event_simulate('MOUSEMOVE', 'NOTHING', x=int(region.x + x), y=int(region.y + y))


def press(x, y, key='LEFTMOUSE', **mods):
    win, area, region = view3d()
    win.event_simulate(key, 'PRESS', x=int(region.x + x), y=int(region.y + y), **mods)


def release(x, y, key='LEFTMOUSE', **mods):
    win, area, region = view3d()
    win.event_simulate(key, 'RELEASE', x=int(region.x + x), y=int(region.y + y), **mods)


def faces_info():
    obj = bpy.context.view_layer.objects.active
    if obj.mode == 'EDIT':
        bm = bmesh.from_edit_mesh(obj.data)
        free = False
    else:
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        free = True
    uvl = bm.loops.layers.uv.active
    tile_l = bm.faces.layers.int.get("grid_tile_id")
    grid_l = bm.faces.layers.int.get("grid_index")
    res = []
    for f in bm.faces:
        us = [l[uvl].uv[0] for l in f.loops] if uvl else []
        vs = [l[uvl].uv[1] for l in f.loops] if uvl else []
        c = f.calc_center_median()
        res.append({
            "center": tuple(round(v, 4) for v in c),
            "normal": tuple(round(v, 3) for v in f.normal),
            "u": (round(min(us), 4), round(max(us), 4)) if us else None,
            "v": (round(min(vs), 4), round(max(vs), 4)) if vs else None,
            "tile_id": f[tile_l] if tile_l else None,
            "grid": f[grid_l] if grid_l else None,
        })
    if free:
        bm.free()
    return res


def make_fixture():
    """64x64 PNG, 4x4 solid 16 px tiles in 16 distinct saturated colours."""
    import colorsys
    w = h = 64
    pixels = numpy.zeros((h, w, 4), dtype=numpy.float32)
    for row in range(4):
        for col in range(4):
            i = row * 4 + col
            rgb = colorsys.hsv_to_rgb((i * 7 % 16) / 16.0, 1.0, 0.55 + 0.45 * (i % 2))
            FIXTURE_COLOURS[(col, row)] = tuple(int(round(c * 255)) for c in rgb)
            y0 = (3 - row) * 16   # image rows count from the bottom
            pixels[y0:y0 + 16, col * 16:(col + 1) * 16] = (*rgb, 1.0)
    image = bpy.data.images.new("tiles_16px_gen", w, h, alpha=True)
    image.pixels.foreach_set(pixels.reshape(-1))
    image.filepath_raw = FIXTURE
    image.file_format = 'PNG'
    image.save()
    bpy.data.images.remove(image)
    L("fixture written", FIXTURE, os.path.getsize(FIXTURE))


def sd():
    return bpy.context.scene.sprytile_data


def gui_cls():
    import importlib
    return importlib.import_module(PKG + ".sprytile_gui").VIEW3D_OP_SprytileGui


def region_center():
    win, area, region = view3d()
    return region.width // 2, region.height // 2


ENGINES = ['BLENDER_EEVEE', 'BLENDER_WORKBENCH', 'CYCLES', 'SPRY_TEST_ENGINE']


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
    prefs = bpy.context.preferences.addons[PKG].preferences
    check("auto_adjust_viewport_shading is on (default)", prefs.auto_adjust_viewport_shading)
    bpy.utils.register_class(CustomEngine)
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
    yield 1.0
    G = gui_cls()
    cx, cy = region_center()
    for i, engine in enumerate(ENGINES):
        win, area, region = view3d()
        shading = area.spaces.active.shading
        # leave any running Sprytile tool before changing the setup
        with ov():
            bpy.ops.wm.tool_set_by_id(name='builtin.select_box')
        yield 1.0
        bpy.context.scene.render.engine = engine
        shading.type = 'SOLID'
        # which values does this engine accept? (bl_rna enum_items is static and lists all four)
        accepted = []
        for t in ('WIREFRAME', 'SOLID', 'MATERIAL', 'RENDERED'):
            try:
                shading.type = t
                accepted.append(t)
            except TypeError:
                pass
        shading.type = 'SOLID'
        L("engine", engine, "accepted shading types", accepted)
        material_ok = 'MATERIAL' in accepted
        with ov():
            r = bpy.ops.wm.tool_set_by_id(name='sprytile.tool_build')
        yield 2.0
        check("%s: Build tool started (gui running)" % engine, G.is_running, "is_running=%s tool_set=%s" % (G.is_running, r))
        n0 = len(faces_info())
        x, y = cx + 3 + i * 130, cy + 3 - i * 70
        move(x, y)
        yield 0.4
        press(x, y)
        yield 0.4
        release(x, y)
        yield 0.6
        # the modal tool is invoked by the first click, which is where the shading is set
        want = 'MATERIAL' if material_ok else 'SOLID'
        check("%s: viewport shading is %s" % (engine, want), shading.type == want, shading.type)
        if not material_ok:
            check("%s: Solid shading uses Texture colours" % engine, shading.color_type == 'TEXTURE', shading.color_type)
        fi = faces_info()
        check("%s: build click created a face" % engine, len(fi) == n0 + 1, "%d -> %d" % (n0, len(fi)))
        if i in (1, 2):
            yield from shot("143_%s" % engine.lower())


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
