"""GUI smoke test for the interactive parts of Spyrite Tile, in a real Blender window.

A background Blender has no window, regions or GPU context, so the palette
overlay, the modal Build/Paint/Fill tools, the toolbar tools and the keymaps
cannot be exercised by tests/blender. This drives them with simulated input
(Window.event_simulate, which needs --enable-event-simulate) and asserts on
the resulting bmesh, UVs, operator state and on a screenshot of the viewport.

Do not run this file directly, use tests/gui/run_gui_smoke.sh: it launches two
isolated GUI Blenders (phase 1 builds and saves a scene, phase 2 reopens it) and
fails on a non-zero exit code or on a Python traceback in either log. The
window is visible for about a minute per phase and takes no human input.

Blender's own quit operator cannot set an exit code, so the script ends with
os._exit(code) after flushing its results: 0 pass, 1 failed checks, 2 the
driver raised, 3 the watchdog fired.
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

PHASE = int(os.environ["SPYRITE_GUI_PHASE"])
OUT = os.environ["SPYRITE_GUI_OUT"]
PKG = "bl_ext.user_default.spyrite_tile"
FIXTURE = os.path.join(OUT, "tiles_16px.png")
BLEND = os.path.join(OUT, "smoke.blend")
# Colours of the 16 fixture tiles, (column, row from the top) -> RGB bytes
FIXTURE_COLOURS = {}
RESULTS = {"phase": PHASE, "checks": {}, "notes": []}
START = time.time()


def L(*a):
    print("SPYRITE_GUI", *a, flush=True)


def check(name, ok, detail=""):
    RESULTS["checks"][name] = {"ok": bool(ok), "detail": str(detail)}
    L(("PASS " if ok else "FAIL ") + name, detail)


def finish(code):
    RESULTS["exit_code"] = code
    RESULTS["elapsed_s"] = round(time.time() - START, 1)
    with open(os.path.join(OUT, "status_phase%d.json" % PHASE), "w") as f:
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


def screenshot_pixels(name):
    """The last screenshot as an (h, w, 4) array of 0..255 bytes, row 0 at the bottom."""
    image = bpy.data.images.load(os.path.join(OUT, name + ".png"))
    image.colorspace_settings.name = 'Non-Color'
    w, h = image.size
    data = numpy.empty(w * h * 4, dtype=numpy.float32)
    image.pixels.foreach_get(data)
    bpy.data.images.remove(image)
    return numpy.rint(data.reshape(h, w, 4) * 255).astype(numpy.int16)


def check_palette_colours(name):
    """The palette must show the tileset's own bytes, not a re-encoded version of them.

    Every fixture colour has to cover a good part of its tile in the screenshot.
    The Metal double-encoding bug showed (140, 0, 0) as (194, 0, 0), so none of
    the 16 colours was present any more.
    """
    img = screenshot_pixels(name)[:, :, :3]
    missing = []
    for (col, row), rgb in sorted(FIXTURE_COLOURS.items()):
        close = (numpy.abs(img - numpy.array(rgb, dtype=numpy.int16)).max(axis=2) <= 8).sum()
        if close < 5000:
            missing.append(((col, row), rgb, int(close)))
    check("palette shows the tileset's exact colours (16 of 16 tiles)", not missing, missing or "all present")
    # Nearest filtering: along a row through the middle of a tile row the colour
    # only steps at tile borders (a linear filter would ramp over ~8 px each)
    ui = bpy.context.scene.sprytile_ui
    px0, py0 = palette_origin()
    tile = ui.zoom * 64 / 4.0
    y = int(py0 + tile * 3.5)
    row = img[y, int(px0) + 4:int(px0 + tile * 4) - 4]
    distinct = len({tuple(p) for p in row.tolist()})
    check("palette is nearest filtered (few distinct colours along a row)", distinct <= 12, "%d distinct colours across 4 tiles" % distinct)


def sd():
    return bpy.context.scene.sprytile_data


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


def region_center():
    win, area, region = view3d()
    return region.width // 2, region.height // 2


# --------------------------------------------------------------------------
def steps_phase1():
    yield 2.5
    win, area, region = view3d()
    # dismiss the splash popup: move the pointer far away from it and press ESC
    win.event_simulate('MOUSEMOVE', 'NOTHING', x=region.x + 200, y=region.y + 100)
    yield 0.5
    win.event_simulate('MOUSEMOVE', 'NOTHING', x=region.x + 220, y=region.y + 120)
    yield 0.5
    win.event_simulate('ESC', 'PRESS', x=region.x + 220, y=region.y + 120)
    yield 0.2
    win.event_simulate('ESC', 'RELEASE', x=region.x + 220, y=region.y + 120)
    yield 0.8
    yield from shot("000_after_splash_dismiss")
    L("window", win.width, win.height, "region", region.x, region.y, region.width, region.height)
    L("regions", [(r.type, r.x, r.y, r.width, r.height) for r in area.regions])
    # --- enable extension
    with ov():
        r = bpy.ops.preferences.addon_enable(module=PKG)
    check("addon_enable", PKG in bpy.context.preferences.addons, r)
    check("props registered", hasattr(bpy.types.Scene, "sprytile_data"))
    yield 0.5
    make_fixture()
    # --- object
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    mesh = bpy.data.meshes.new("tilemesh")
    obj = bpy.data.objects.new("tileobj", mesh)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    yield 0.3
    yield from shot("00_object_mode")
    # --- panel setup op (what the 'Load Tileset' button calls)
    with ov():
        r = bpy.ops.sprytile.tileset_load('EXEC_DEFAULT', filepath=FIXTURE)
    check("tileset_load", r == {'FINISHED'}, r)
    yield 0.5
    scene = bpy.context.scene
    mats = scene.sprytile_mats
    check("sprytile_mats populated", len(mats) == 1 and len(mats[0].grids) >= 1,
          [(m.mat_id, [(g.id, tuple(g.grid), tuple(g.tile_selection)) for g in m.grids]) for m in mats])
    mat = obj.material_slots[0].material if obj.material_slots else None
    check("material slot", mat is not None, mat and mat.name)
    if mat:
        tex = [n for n in mat.node_tree.nodes if n.type == 'TEX_IMAGE']
        check("tex node image", bool(tex) and tex[0].image is not None and tuple(tex[0].image.size) == (64, 64),
              tex and tex[0].image and tuple(tex[0].image.size))
        check("interpolation Closest", bool(tex) and tex[0].interpolation == 'Closest', tex and tex[0].interpolation)
        check("surface_render_method DITHERED", mat.surface_render_method == 'DITHERED', mat.surface_render_method)
    g = mats[0].grids[0]
    g.grid = (16, 16)
    sd().world_pixels = 16
    RESULTS["notes"].append("grid=%s padding=%s margin=%s world_pixels=%s" % (
        tuple(g.grid), tuple(g.padding), tuple(g.margin), sd().world_pixels))
    L("grid", tuple(g.grid), tuple(g.padding), tuple(g.margin), "world_pixels", sd().world_pixels,
      "gridid", obj.sprytile_gridid)
    # --- enter edit mode, top view, sidebar open
    with ov():
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.view3d.view_axis(type='TOP')
        space = area.spaces.active
        space.show_region_ui = True
    yield 1.0
    check("edit mode", bpy.context.view_layer.objects.active.mode == 'EDIT')
    yield from shot("01_edit_mode_no_tool")
    L("regions", [(r.type, r.x, r.y, r.width, r.height) for r in area.regions])

    # --- BUILD tool
    with ov():
        r = bpy.ops.wm.tool_set_by_id(name='sprytile.tool_build')
    L("tool_set_by_id build", r)
    yield 2.0
    G = gui_cls()
    check("gui running after Build tool", G.is_running, "is_running=%s" % G.is_running)
    check("paint_mode MAKE_FACE", sd().paint_mode == 'MAKE_FACE', sd().paint_mode)
    ui = bpy.context.scene.sprytile_ui
    L("ui palette_pos", tuple(ui.palette_pos), "zoom", ui.zoom, "display_size", G.display_size, "tex_size", G.tex_size)
    yield from shot("02_build_tool_palette")
    check_palette_colours("02_build_tool_palette")
    yield 0.3
    # --- Spyrite Tile sidebar panel
    win, area, region = view3d()
    ui_region = next(r for r in area.regions if r.type == 'UI')
    try:
        ui_region.active_panel_category = 'Spyrite Tile'
        L("active_panel_category ->", ui_region.active_panel_category)
    except Exception as e:
        L("could not set active_panel_category", repr(e))
    yield 0.5
    yield from shot("02b_sidebar_spyrite_panel")
    ui_region.active_panel_category = 'Item'
    yield 0.3
    # --- palette interaction: click a tile, then drag a 2x2 selection
    g = bpy.context.scene.sprytile_mats[0].grids[0]
    ts = ui.zoom * 64 / 4.0   # displayed tile size in region px
    px0, py0 = palette_origin()
    def tile_pt(col, row):
        return int(px0 + ts * (col + 0.5)), int(py0 + ts * (row + 0.5))
    L("palette tile size", ts)
    tx, ty = tile_pt(3, 2)
    move(tx, ty)
    yield 0.4
    press(tx, ty)
    yield 0.3
    release(tx, ty)
    yield 0.5
    check("palette click selects tile (3,2)", tuple(g.tile_selection) == (3, 2, 1, 1), tuple(g.tile_selection))
    ax, ay = tile_pt(1, 1)
    bx, by = tile_pt(2, 2)
    move(ax, ay)
    yield 0.3
    press(ax, ay)
    yield 0.3
    move((ax + bx) // 2, (ay + by) // 2)
    yield 0.3
    move(bx, by)
    yield 0.3
    release(bx, by)
    yield 0.5
    check("palette drag selects 2x2 from (1,1)", tuple(g.tile_selection) == (1, 1, 2, 2), tuple(g.tile_selection))
    yield from shot("02c_palette_multi_select")
    # wheel zoom over the palette
    z0 = ui.zoom
    win.event_simulate('WHEELDOWNMOUSE', 'PRESS', x=region.x + tx, y=region.y + ty)
    yield 0.5
    check("wheel over palette changes zoom", ui.zoom != z0, "%s -> %s" % (z0, ui.zoom))
    win.event_simulate('WHEELUPMOUSE', 'PRESS', x=region.x + tx, y=region.y + ty)
    yield 0.5
    L("zoom after wheel up", ui.zoom)
    # back to the single bottom-left tile
    tx, ty = tile_pt(0, 0)
    move(tx, ty)
    yield 0.3
    press(tx, ty)
    yield 0.3
    release(tx, ty)
    yield 0.5
    check("palette click selects tile (0,0)", tuple(g.tile_selection) == (0, 0, 1, 1), tuple(g.tile_selection))
    # keys over the viewport: Q rotates, Shift+Q flips X
    cx0, cy0 = region_center()
    move(cx0 + 300, cy0 + 300)
    yield 0.4
    r0 = sd().mesh_rotate
    win.event_simulate('Q', 'PRESS', x=region.x + cx0 + 300, y=region.y + cy0 + 300)
    yield 0.2
    win.event_simulate('Q', 'RELEASE', x=region.x + cx0 + 300, y=region.y + cy0 + 300)
    yield 0.5
    check("Q key rotates grid (keymap sprytile.rotate_left)", abs(sd().mesh_rotate - r0) > 1e-4, "%s -> %s" % (r0, sd().mesh_rotate))
    win.event_simulate('E', 'PRESS', x=region.x + cx0 + 300, y=region.y + cy0 + 300)
    yield 0.2
    win.event_simulate('E', 'RELEASE', x=region.x + cx0 + 300, y=region.y + cy0 + 300)
    yield 0.5
    check("E key rotates back", abs(sd().mesh_rotate - r0) < 1e-4, "-> %s" % sd().mesh_rotate)
    fx0 = sd().uv_flip_x
    win.event_simulate('Q', 'PRESS', x=region.x + cx0 + 300, y=region.y + cy0 + 300, shift=True)
    yield 0.2
    win.event_simulate('Q', 'RELEASE', x=region.x + cx0 + 300, y=region.y + cy0 + 300, shift=True)
    yield 0.5
    check("Shift+Q toggles uv_flip_x", sd().uv_flip_x != fx0, "%s -> %s" % (fx0, sd().uv_flip_x))
    sd().uv_flip_x = fx0
    yield 0.3
    # click build in region (avoid toolbar/sidebar): centre
    cx, cy = region_center()
    L("click target", cx, cy)
    n0 = len(faces_info())
    move(cx, cy)
    yield 0.4
    move(cx + 3, cy + 3)
    yield 0.4
    yield from shot("03_build_hover_preview")
    move(cx + 120, cy - 100)
    yield 0.5
    yield from shot("03b_build_hover_preview_offset")
    move(cx + 3, cy + 3)
    yield 0.4
    press(cx + 3, cy + 3)
    yield 0.4
    release(cx + 3, cy + 3)
    yield 0.6
    fi = faces_info()
    check("build created a face", len(fi) == n0 + 1, fi)
    yield from shot("04_after_build")

    # second build click offset
    ppu = sd().world_pixels
    g = bpy.context.scene.sprytile_mats[0].grids[0]
    L("tile_selection", tuple(g.tile_selection))
    # --- save state for debugging
    RESULTS["faces_after_build"] = fi
    # --- PAINT
    with ov():
        bpy.ops.wm.tool_set_by_id(name='sprytile.tool_paint')
    yield 1.5
    check("paint_mode PAINT", sd().paint_mode == 'PAINT', sd().paint_mode)
    check("gui running with Paint", G.is_running)
    yield from shot("05_paint_tool_palette")
    g.tile_selection = (2, 1, 1, 1)
    yield 0.3
    before = faces_info()
    # click on the built face
    obj = bpy.context.view_layer.objects.active
    from bpy_extras.view3d_utils import location_3d_to_region_2d
    win, area, region = view3d()
    r3d = area.spaces.active.region_3d
    if before:
        wc = obj.matrix_world @ __import__("mathutils").Vector(before[0]["center"])
        p = location_3d_to_region_2d(region, r3d, wc)
        L("face centre on screen", p)
        px, py = int(p.x), int(p.y)
        move(px, py)
        yield 0.4
        press(px, py)
        yield 0.4
        release(px, py)
        yield 0.6
    after = faces_info()
    check("paint remapped uv", before and after and (before[0]["u"], before[0]["v"], before[0]["tile_id"]) != (after[0]["u"], after[0]["v"], after[0]["tile_id"]),
          "before=%s after=%s" % (before, after))
    yield from shot("06_after_paint")
    RESULTS["faces_after_paint"] = after

    # --- FILL
    with ov():
        bpy.ops.wm.tool_set_by_id(name='sprytile.tool_fill')
    yield 1.5
    check("paint_mode FILL", sd().paint_mode == 'FILL', sd().paint_mode)
    g.tile_selection = (3, 3, 1, 1)
    yield 0.3
    nf0 = len(faces_info())
    move(cx + 40, cy + 40)
    yield 0.4
    press(cx + 40, cy + 40)
    yield 0.4
    release(cx + 40, cy + 40)
    yield 0.8
    fi2 = faces_info()
    check("fill created faces", len(fi2) > nf0, "before=%d after=%d fill_plane_size=%s" % (nf0, len(fi2), tuple(sd().fill_plane_size)) if hasattr(sd().fill_plane_size, '__len__') else "%d -> %d size=%s" % (nf0, len(fi2), sd().fill_plane_size))
    yield from shot("07_after_fill")
    RESULTS["faces_after_fill"] = fi2

    # --- save
    with ov():
        bpy.ops.object.mode_set(mode='OBJECT')
    yield 0.5
    bpy.ops.wm.save_as_mainfile(filepath=BLEND)
    check("saved blend", os.path.exists(BLEND), BLEND)
    RESULTS["saved_faces"] = faces_info()
    # --- extras: tile picker (Alt+click), snap cursor (S), set normal (N)
    with ov():
        bpy.ops.object.mode_set(mode='EDIT')
    yield 0.5
    with ov():
        bpy.ops.wm.tool_set_by_id(name='sprytile.tool_build')
    yield 1.5
    g.tile_selection = (0, 0, 1, 1)
    win, area, region = view3d()
    r3d = area.spaces.active.region_3d
    from mathutils import Vector
    obj = bpy.context.view_layer.objects.active
    fp = location_3d_to_region_2d(region, r3d, obj.matrix_world @ Vector((0.5, -0.5, 0.0)))
    fx, fy = int(fp.x), int(fp.y)
    def ev(t, v, x, y, **kw):
        win.event_simulate(t, v, x=region.x + x, y=region.y + y, **kw)
    ev('MOUSEMOVE', 'NOTHING', fx, fy)
    yield 0.3
    ev('LEFT_ALT', 'PRESS', fx, fy, alt=True)
    yield 0.3
    check("tile picker modal started (is_picking)", sd().is_picking, sd().is_picking)
    ev('MOUSEMOVE', 'NOTHING', fx + 1, fy + 1, alt=True)
    yield 0.3
    ev('LEFTMOUSE', 'PRESS', fx + 1, fy + 1, alt=True)
    yield 0.3
    ev('LEFTMOUSE', 'RELEASE', fx + 1, fy + 1, alt=True)
    yield 0.3
    ev('LEFT_ALT', 'RELEASE', fx + 1, fy + 1)
    yield 0.5
    check("tile picker picked tile (2,1) from face", tuple(g.tile_selection) == (2, 1, 1, 1), tuple(g.tile_selection))
    check("tile picker finished (is_picking False)", not sd().is_picking, sd().is_picking)
    g.tile_selection = (0, 0, 1, 1)
    # snap cursor
    c0 = tuple(bpy.context.scene.cursor.location)
    ev('MOUSEMOVE', 'NOTHING', cx + 200, cy + 100)
    yield 0.3
    ev('S', 'PRESS', cx + 200, cy + 100)
    yield 0.3
    check("snap cursor modal started (is_snapping)", sd().is_snapping, sd().is_snapping)
    ev('MOUSEMOVE', 'NOTHING', cx + 205, cy + 105)
    yield 0.4
    c1 = tuple(round(v, 4) for v in bpy.context.scene.cursor.location)
    ev('S', 'RELEASE', cx + 205, cy + 105)
    yield 0.5
    check("snap cursor moved cursor to a grid point", c1 != c0 and abs(c1[0] - round(c1[0])) < 1e-3 and abs(c1[1] - round(c1[1])) < 1e-3, "%s -> %s" % (c0, c1))
    check("snap cursor finished", not sd().is_snapping, sd().is_snapping)
    yield from shot("07a_after_snap_cursor")
    bpy.context.scene.cursor.location = (0, 0, 0)
    yield 0.3
    # set normal
    sd().lock_normal = False
    ev('MOUSEMOVE', 'NOTHING', fx, fy)
    yield 0.3
    ev('N', 'PRESS', fx, fy)
    yield 0.3
    check("set normal modal started (is_picking)", sd().is_picking, sd().is_picking)
    ev('LEFTMOUSE', 'PRESS', fx, fy)
    yield 0.3
    ev('LEFTMOUSE', 'RELEASE', fx, fy)
    yield 0.3
    ev('N', 'RELEASE', fx, fy)
    yield 0.5
    check("set normal locked the normal to the face", sd().lock_normal and tuple(round(v, 3) for v in sd().paint_normal_vector) == (0.0, 0.0, 1.0), "%s %s" % (sd().lock_normal, tuple(sd().paint_normal_vector)))
    sd().lock_normal = False
    # pixel translate (what the modal tool's G intercept starts)
    bm = bmesh.from_edit_mesh(obj.data)
    for v in bm.verts:
        v.select = False
    bm.faces[0].select = True
    bmesh.update_edit_mesh(obj.data)
    gs0 = area.spaces.active.overlay.grid_scale
    ev('MOUSEMOVE', 'NOTHING', cx, cy)
    yield 0.3
    with ov():
        r = bpy.ops.sprytile.translate_grid('INVOKE_REGION_WIN')
    L("translate_grid ->", r)
    yield 0.4
    check("translate grid started", sd().is_grid_translate, sd().is_grid_translate)
    check("translate grid sets grid_scale to pixel unit", abs(area.spaces.active.overlay.grid_scale - 1 / 16) < 1e-6, area.spaces.active.overlay.grid_scale)
    yield 1.2
    ev('ESC', 'PRESS', cx, cy)
    yield 0.3
    ev('ESC', 'RELEASE', cx, cy)
    yield 0.5
    ev('ESC', 'PRESS', cx, cy)
    yield 0.3
    ev('ESC', 'RELEASE', cx, cy)
    yield 0.6
    check("translate grid exited and restored grid scale", (not sd().is_grid_translate) and abs(area.spaces.active.overlay.grid_scale - gs0) < 1e-6,
          "is_grid_translate=%s grid_scale=%s (was %s)" % (sd().is_grid_translate, area.spaces.active.overlay.grid_scale, gs0))
    with ov():
        bpy.ops.object.mode_set(mode='OBJECT')
    yield 0.5
    # --- DECAL layer build (exercises get_face_up_vector callsites in tool_build)
    with ov():
        bpy.ops.object.mode_set(mode='EDIT')
    yield 0.5
    with ov():
        bpy.ops.wm.tool_set_by_id(name='sprytile.tool_build')
    yield 1.5
    sd().set_work_layer = (False, True)
    g.tile_selection = (1, 1, 1, 1)
    yield 0.3
    check("work_layer DECAL_1", sd().work_layer == 'DECAL_1', sd().work_layer)
    nd0 = len(faces_info())
    dx, dy = cx + 120, cy - 100
    move(dx - 5, dy - 5)
    yield 0.4
    move(dx, dy)
    yield 0.6
    check("gui still running after decal-layer hover", G.is_running, "is_running=%s" % G.is_running)
    yield from shot("07b_decal_hover")
    press(dx, dy)
    yield 0.4
    release(dx, dy)
    yield 0.8
    nd1 = len(faces_info())
    check("decal build adds a face", nd1 == nd0 + 1, "%d -> %d" % (nd0, nd1))
    yield from shot("07c_decal_built")
    sd().set_work_layer = (True, False)
    yield 0.3
    with ov():
        bpy.ops.object.mode_set(mode='OBJECT')
    yield 0.5
    # --- cleanup paths: leave edit mode with the tool active, switch tool, disable add-on
    with ov():
        bpy.ops.object.mode_set(mode='EDIT')
    yield 0.5
    with ov():
        bpy.ops.wm.tool_set_by_id(name='sprytile.tool_build')
    yield 1.5
    check("gui running again", G.is_running)
    with ov():
        bpy.ops.wm.tool_set_by_id(name='builtin.select_box')
    yield 1.5
    check("gui stops when switching to a non-sprytile tool", not G.is_running, "is_running=%s" % G.is_running)
    yield from shot("08_after_switch_to_select")
    with ov():
        bpy.ops.wm.tool_set_by_id(name='sprytile.tool_paint')
    yield 1.5
    check("gui running with paint again", G.is_running)
    with ov():
        bpy.ops.object.mode_set(mode='OBJECT')
    yield 1.0
    check("gui stops leaving edit mode", not G.is_running, "is_running=%s" % G.is_running)
    with ov():
        bpy.ops.object.mode_set(mode='EDIT')
    yield 0.8
    check("gui restarts re-entering edit mode with tool still set", G.is_running, "is_running=%s" % G.is_running)
    yield from shot("09_reentered_edit_mode")
    with ov():
        bpy.ops.preferences.addon_disable(module=PKG)
    yield 1.0
    check("addon disabled", PKG not in bpy.context.preferences.addons)
    yield from shot("09b_after_disable")
    with ov():
        bpy.ops.preferences.addon_enable(module=PKG)
    yield 1.0
    check("addon re-enabled", PKG in bpy.context.preferences.addons)
    yield 0.2


def steps_phase2():
    yield 2.5
    with ov():
        bpy.ops.preferences.addon_enable(module=PKG)
    yield 0.5
    bpy.ops.wm.open_mainfile(filepath=BLEND)
    L("opened", bpy.data.filepath)
    yield 2.0
    check("addon enabled on reopen", PKG in bpy.context.preferences.addons)
    check("props registered", hasattr(bpy.types.Scene, "sprytile_data"))
    scene = bpy.context.scene
    obj = bpy.data.objects.get("tileobj")
    check("object persisted", obj is not None)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    fi = faces_info()
    check("faces persisted", len(fi) > 0, len(fi))
    mats = scene.sprytile_mats
    check("sprytile_mats persisted", len(mats) == 1 and len(mats[0].grids) >= 1, len(mats))
    L("faces", fi)
    f0 = [f for f in fi if f["center"] == (0.5, -0.5, 0.0)]
    check("painted face data persisted (tile_id 6, uv 0.5..0.75 x 0.25..0.5)",
          bool(f0) and f0[0]["tile_id"] == 6 and f0[0]["u"] == (0.5004, 0.7496) and f0[0]["v"] == (0.2504, 0.4996), f0)
    g0 = mats[0].grids[0]
    mat0 = obj.material_slots[0].material if obj.material_slots else None
    check("grid + material persisted", tuple(g0.grid) == (16, 16) and mat0 is not None and mat0.name == "tiles_16px" and
          any(n.type == 'TEX_IMAGE' and n.image and tuple(n.image.size) == (64, 64) and n.interpolation == 'Closest' for n in mat0.node_tree.nodes),
          (tuple(g0.grid), mat0 and mat0.name))
    check("world_pixels persisted", scene.sprytile_data.world_pixels == 16, scene.sprytile_data.world_pixels)
    with ov():
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.view3d.view_axis(type='TOP')
        win, area, region = view3d()
        area.spaces.active.show_region_ui = True
    yield 1.0
    with ov():
        bpy.ops.wm.tool_set_by_id(name='sprytile.tool_build')
    yield 2.0
    G = gui_cls()
    check("gui running after reopen", G.is_running)
    yield from shot("10_reopen_build_tool")
    cx, cy = region_center()
    n0 = len(faces_info())
    move(cx + 450, cy + 250)
    yield 0.4
    press(cx + 450, cy + 250)
    yield 0.4
    release(cx + 450, cy + 250)
    yield 0.6
    check("build on existing mesh", len(faces_info()) > n0, "%d -> %d" % (n0, len(faces_info())))
    yield from shot("11_reopen_after_build")
    # paint tool on the reloaded mesh
    with ov():
        bpy.ops.wm.tool_set_by_id(name='sprytile.tool_paint')
    yield 1.5
    check("gui running with paint after reopen", G.is_running)
    g0.tile_selection = (3, 0, 1, 1)
    yield 0.3
    win, area, region = view3d()
    from mathutils import Vector
    r3d = area.spaces.active.region_3d
    p = location_3d_to_region_2d(region, r3d, obj.matrix_world @ Vector((0.5, -0.5, 0.0)))
    px, py = int(p.x), int(p.y)
    move(px, py)
    yield 0.4
    press(px, py)
    yield 0.4
    release(px, py)
    yield 0.6
    f1 = [f for f in faces_info() if f["center"] == (0.5, -0.5, 0.0)]
    check("paint works on reloaded mesh (tile_id 3, u 0.75..1, v 0..0.25)", bool(f1) and f1[0]["tile_id"] == 3 and f1[0]["u"] == (0.7504, 0.9996) and f1[0]["v"] == (0.0004, 0.2496), f1)
    yield from shot("12_reopen_after_paint")
    yield 0.2


def runner():
    gen = steps_phase1() if PHASE == 1 else steps_phase2()
    state = {"gen": gen}

    def tick():
        try:
            delay = next(state["gen"])
            return delay
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
