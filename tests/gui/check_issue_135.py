"""GUI check for issue #135: the tile palette disappears after reopening a file.

The sequence from the issue, in ONE Blender process (a fresh process cannot show
it: the stale state is class-level and dies with the process). Two Spyrite Tile
projects with different tilesets are built and saved in edit mode with the Build
tool active (A: tiles_a16px, B: tiles_b16px, 16 distinct colours each, the two
sets do not overlap), then, starting from B open in the window:

  1. open_mainfile the SAME file (B) while its palette is up,
  2. open the other Spyrite Tile project (A), then B again,
  3. open a project without Spyrite Tile data, then A,
  4. object mode -> edit mode with the tool still selected,
  5. File > Revert,
  6. File > New, then A again.

After every step the palette must be backed by a live modal operator
(Window.modal_operators, owned by Blender, not the add-on's own flag), the flag
has to agree with it, and the viewport screenshot must show the palette: all 16
colours of the tileset that is open now and none of the other tileset's. The
negative control is the first screenshot of each project, edit mode without the
Sprytile tool: none of the colours.

Run with tests/gui/run_gui_check.sh tests/gui/check_issue_135.py. Blender's own
quit operator cannot set an exit code, so the script ends with os._exit(code):
0 pass, 1 failed checks, 2 the driver raised, 3 the watchdog fired.
"""
import colorsys
import importlib
import json
import os
import subprocess
import sys
import time
import traceback

import bpy
import numpy

OUT = os.environ["SPYRITE_GUI_OUT"]
PKG = "bl_ext.user_default.spyrite_tile"
TILES_A = os.path.join(OUT, "tiles_a16px.png")
TILES_B = os.path.join(OUT, "tiles_b16px.png")
BLEND_A = os.path.join(OUT, "issue135_a.blend")
BLEND_B = os.path.join(OUT, "issue135_b.blend")
BLEND_OTHER = os.path.join(OUT, "issue135_other.blend")
COLOURS_A = {}
COLOURS_B = {}
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
    with open(os.path.join(OUT, "status_issue135.json"), "w") as f:
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


def make_tileset(path, colours, hue_offset):
    """64x64 PNG, 4x4 solid 16 px tiles in 16 distinct saturated colours (also stored in `colours`)."""
    w = h = 64
    pixels = numpy.zeros((h, w, 4), dtype=numpy.float32)
    for row in range(4):
        for col in range(4):
            i = row * 4 + col
            rgb = colorsys.hsv_to_rgb(((i * 7 % 16) + hue_offset) / 16.0, 1.0, 0.55 + 0.45 * (i % 2))
            colours[(col, row)] = tuple(int(round(c * 255)) for c in rgb)
            y0 = (3 - row) * 16   # image rows count from the bottom
            pixels[y0:y0 + 16, col * 16:(col + 1) * 16] = (*rgb, 1.0)
    image = bpy.data.images.new("gen_" + os.path.basename(path), w, h, alpha=True)
    image.pixels.foreach_set(pixels.reshape(-1))
    image.filepath_raw = path
    image.file_format = 'PNG'
    image.save()
    bpy.data.images.remove(image)


def screenshot_pixels(name):
    """The last screenshot as an (h, w, 4) array of 0..255 bytes, row 0 at the bottom."""
    image = bpy.data.images.load(os.path.join(OUT, name + ".png"))
    image.colorspace_settings.name = 'Non-Color'
    w, h = image.size
    data = numpy.empty(w * h * 4, dtype=numpy.float32)
    image.pixels.foreach_get(data)
    bpy.data.images.remove(image)
    return numpy.rint(data.reshape(h, w, 4) * 255).astype(numpy.int16)


def colours_found(img, colours, need):
    """Per-colour pixel counts (within 8 on every channel) and how many reach `need`."""
    counts = {}
    for key, rgb in sorted(colours.items()):
        counts[key] = int((numpy.abs(img[:, :, :3] - numpy.array(rgb, dtype=numpy.int16)).max(axis=2) <= 8).sum())
    return sum(1 for c in counts.values() if c >= need), counts


def gui_cls():
    return importlib.import_module(PKG + ".sprytile_gui").VIEW3D_OP_SprytileGui


def current_tool():
    utils = importlib.import_module(PKG + ".sprytile_utils")
    with ov():
        return utils.get_current_tool(bpy.context)


def gui_modals():
    """Palette modal operators Blender itself reports as running, as (window index, pointer)."""
    out = []
    for i, w in enumerate(bpy.context.window_manager.windows):
        for op in w.modal_operators:
            if op.bl_idname == 'SPRYTILE_OT_gui_win':
                out.append((i, op.as_pointer()))
    return out


def state(label):
    G = gui_cls()
    obj = bpy.context.view_layer.objects.active
    s = {
        "label": label,
        "is_running": G.is_running,
        "modals": gui_modals(),
        "draw_handler": G.draw_callback is not None,
        "texture_grid": G.texture_grid,
        "use_mouse": bpy.context.scene.sprytile_ui.use_mouse,
        "cursor_grid_pos": tuple(G.cursor_grid_pos) if G.cursor_grid_pos is not None else None,
        "mode": obj.mode if obj else None,
        "tool": current_tool(),
        "file": os.path.basename(bpy.data.filepath),
    }
    L("STATE", json.dumps(s))
    RESULTS["notes"].append(s)
    return s


def trace(label, seconds, step=0.1):
    """Log every change of the palette's observable state for `seconds` (evidence of ordering)."""
    last = None
    t0 = time.time()
    while time.time() - t0 < seconds:
        G = gui_cls()
        cur = (G.is_running, tuple(m[1] for m in gui_modals()), G.draw_callback is not None)
        if cur != last:
            L("TRACE", label, "t=%.2f" % (time.time() - t0), "is_running=%s modals=%s draw_handler=%s" % cur)
            last = cur
        yield step


def expect_palette(tag, shot_name, own, other, must_show=True):
    """The palette modal runs, its flag agrees, and the screenshot shows `own` colours and not `other`."""
    yield from shot(shot_name)
    s = state(tag)
    win, area, region = view3d()
    img = screenshot_pixels(shot_name)
    scale = img.shape[1] / float(area.width)
    # A palette tile is zoom * 64 / 4 region px wide; a colour is present when a quarter of a tile is
    tile_px = bpy.context.scene.sprytile_ui.zoom * 64 / 4.0 * scale
    need = 0.25 * tile_px * tile_px
    found, counts = colours_found(img, own, need)
    found_other, _ = colours_found(img, other, need)
    detail = "own %d of 16, other %d of 16, need %d px per colour, own counts %s" % (
        found, found_other, need, sorted(counts.values()))
    if must_show:
        check(tag + ": palette modal running (Window.modal_operators)", len(s["modals"]) == 1, s["modals"])
        check(tag + ": palette flag agrees with the live modal", s["is_running"] == bool(s["modals"]),
              "is_running=%s modals=%s" % (s["is_running"], s["modals"]))
        check(tag + ": palette drawn, all 16 colours of this tileset and none of the other", found == 16 and found_other == 0, detail)
    else:
        check(tag + ": no palette pixels (negative control)", found == 0 and found_other == 0, detail)


def build_project(obj_name, tileset, own, other, shot_prefix):
    """Empty scene -> tileset on a mesh -> edit mode, top view -> Build tool, palette checked."""
    win, area, region = view3d()
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    obj = bpy.data.objects.new(obj_name, bpy.data.meshes.new(obj_name))
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    yield 0.3
    with ov():
        r = bpy.ops.sprytile.tileset_load('EXEC_DEFAULT', filepath=tileset)
    check(shot_prefix + " tileset_load", r == {'FINISHED'}, r)
    yield 0.5
    bpy.context.scene.sprytile_mats[0].grids[0].grid = (16, 16)
    bpy.context.scene.sprytile_data.world_pixels = 16
    with ov():
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.view3d.view_axis(type='TOP')
        view3d()[1].spaces.active.show_region_ui = True
    yield 1.0
    check(shot_prefix + " edit mode", bpy.context.view_layer.objects.active.mode == 'EDIT')
    # Negative control: edit mode without the Sprytile tool has no palette
    yield from expect_palette(shot_prefix + " no tool", shot_prefix + "_0_no_tool", own, other, must_show=False)
    with ov():
        bpy.ops.wm.tool_set_by_id(name='sprytile.tool_build')
    yield 2.0
    yield from expect_palette(shot_prefix + " build tool", shot_prefix + "_1_build_tool", own, other)


def reopen(path, label, seconds=3.0):
    bpy.ops.wm.open_mainfile(filepath=path)
    L("opened", bpy.data.filepath)
    yield from trace(label, seconds)


def steps():
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
    with ov():
        r = bpy.ops.preferences.addon_enable(module=PKG)
    check("addon_enable", PKG in bpy.context.preferences.addons, r)
    yield 0.5
    make_tileset(TILES_A, COLOURS_A, 0.0)
    make_tileset(TILES_B, COLOURS_B, 0.5)
    gap = min(max(abs(a - b) for a, b in zip(ca, cb)) for ca in COLOURS_A.values() for cb in COLOURS_B.values())
    check("the two tilesets share no colour (probe can tell them apart)", gap > 16, "smallest channel gap %d" % gap)
    check("each tileset has 16 distinct colours", len(set(COLOURS_A.values())) == 16 and len(set(COLOURS_B.values())) == 16)
    # A project without Spyrite Tile data (made by a background Blender)
    subprocess.run([bpy.app.binary_path, '-b', '--factory-startup', '--python-expr',
                    "import bpy; bpy.ops.wm.save_as_mainfile(filepath=%r)" % BLEND_OTHER],
                   check=True, timeout=60, stdout=subprocess.DEVNULL)
    check("plain project written", os.path.exists(BLEND_OTHER), BLEND_OTHER)

    # --- project A, saved in edit mode with the tool active, as in the issue
    yield from build_project("tileobj_a", TILES_A, COLOURS_A, COLOURS_B, "A")
    with ov():
        r = bpy.ops.wm.save_as_mainfile(filepath=BLEND_A)
    check("saved A in edit mode", r == {'FINISHED'} and os.path.exists(BLEND_A), r)

    # --- project B, built in a fresh file with the add-on cycled so no state of A is carried over
    with ov():
        bpy.ops.preferences.addon_disable(module=PKG)
    yield 0.5
    bpy.ops.wm.open_mainfile(filepath=BLEND_OTHER)
    yield 1.0
    with ov():
        bpy.ops.preferences.addon_enable(module=PKG)
    yield 1.0
    yield from build_project("tileobj_b", TILES_B, COLOURS_B, COLOURS_A, "B")
    with ov():
        r = bpy.ops.wm.save_as_mainfile(filepath=BLEND_B)
    check("saved B in edit mode", r == {'FINISHED'} and os.path.exists(BLEND_B), r)

    # --- 1. the same file again, palette up while Blender replaces the window manager
    yield from trace("B palette up before reopen", 0.3)
    yield from reopen(BLEND_B, "reopen B")
    yield from expect_palette("1 B reopened", "1_reopen_same_file", COLOURS_B, COLOURS_A)

    # --- 2. the other Spyrite Tile project, then back
    yield from reopen(BLEND_A, "open A")
    yield from expect_palette("2 A opened from B", "2_open_other_project", COLOURS_A, COLOURS_B)
    yield from reopen(BLEND_B, "back to B")
    yield from expect_palette("2 B opened from A", "2_back_to_first", COLOURS_B, COLOURS_A)

    # --- 3. a project without Spyrite Tile, then A
    yield from reopen(BLEND_OTHER, "plain project")
    s = state("3 plain project")
    check("3 plain project: no palette modal", not s["modals"], s["modals"])
    check("3 plain project: palette flag agrees with the live modal", s["is_running"] == bool(s["modals"]),
          "is_running=%s modals=%s" % (s["is_running"], s["modals"]))
    yield from reopen(BLEND_A, "A after plain")
    yield from expect_palette("3 A opened from plain", "3_after_plain_project", COLOURS_A, COLOURS_B)

    # --- 4. leave edit mode and come back with the tool still selected
    with ov():
        bpy.ops.object.mode_set(mode='OBJECT')
    yield 1.0
    s = state("4 object mode")
    check("4 object mode: palette modal stopped", not s["modals"] and not s["is_running"],
          "is_running=%s modals=%s" % (s["is_running"], s["modals"]))
    with ov():
        bpy.ops.object.mode_set(mode='EDIT')
    yield 2.0
    yield from expect_palette("4 edit mode again", "4_edit_mode_again", COLOURS_A, COLOURS_B)

    # --- 5. File > Revert with the palette up
    yield from trace("palette up before revert", 0.3)
    bpy.ops.wm.revert_mainfile()
    L("reverted", bpy.data.filepath)
    yield from trace("revert", 3.0)
    yield from expect_palette("5 A reverted", "5_revert", COLOURS_A, COLOURS_B)

    # --- 6. File > New with the palette up, then A again
    bpy.ops.wm.read_homefile()
    L("new file", repr(bpy.data.filepath))
    yield from trace("new file", 2.0)
    s = state("6 new file")
    check("6 new file: add-on still enabled", PKG in bpy.context.preferences.addons)
    check("6 new file: no palette modal", not s["modals"], s["modals"])
    check("6 new file: palette flag agrees with the live modal", s["is_running"] == bool(s["modals"]),
          "is_running=%s modals=%s" % (s["is_running"], s["modals"]))
    yield from reopen(BLEND_A, "A after new file")
    yield from expect_palette("6 A opened from new file", "6_after_new_file", COLOURS_A, COLOURS_B)
    yield 0.2


def runner():
    state_ = {"gen": steps()}

    def tick():
        try:
            return next(state_["gen"])
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
