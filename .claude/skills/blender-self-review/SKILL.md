---
name: blender-self-review
description: "Self-review and automated verification of Blender work (Spyrite Tile, blended, any add-on/agent change): gate ladder, live Blender over MCP, screenshots and zoom, pixel oracle, metamorphic tests, mutation checks, evidence report. Invoke before claiming any Blender change works."
---

# Blender self-review

Use this before you say a Blender change "works", after any change to an add-on, a blended op,
a tile scene, generated art, or a render path, and whenever a human would otherwise have to
look. Goal: the user never babysits. You produce evidence a skeptic accepts.

Reference project: `/Users/ladvien/spyrite_tile` (add-on `addon/spyrite_tile`, extension id
`spyrite_tile`; plugin `packages/spyrite_tile_ops`; generator `packages/spyrite_tile_gen`).
blended: `/Users/ladvien/blended`. Blender: `/Applications/Blender.app/Contents/MacOS/Blender` (5.2).

## 0. Rules that make evidence trustworthy

1. **Ground truth outside the derivation.** An oracle that reuses the code under test cannot
   fail. Expected values come from somewhere else: the tileset *image file*, Blender's own
   projection, a second run under a known transformation, a saved-and-reloaded copy.
2. **The probe must vary.** One colour / one value everywhere cannot tell right from constant.
   Use the 16-distinct-tile fixture `tests/fixtures/tiles_16px.png`; refuse uniform probes.
3. **Every new check gets a negative control.** Break the thing on purpose (mutant or corrupted
   data), watch the check fail *on the right item*, restore, watch it pass. A check never seen
   failing is unproven. Confirm the mutant actually changed behaviour (see §6).
4. **Look at the pictures yourself.** Use `read` on every PNG/WebP you produce and describe what
   is in it. A passing number with a wrong-looking image means the number is wrong.
5. **Leave the user's world as you found it.** Restore view, mode, selection, shading, hidden
   objects. Never write to `~/Library/Application Support/Blender` except via `make install-addon`
   when the user asked. Never kill the user's Blender. Isolated runs use temp HOME /
   `BLENDER_USER_RESOURCES` / `BLENDER_USER_EXTENSIONS`.
6. **Write the number, and the wrong number.** Report observed values, the failing ones too,
   with paths to evidence files.

## 1. Gate ladder (run bottom-up; stop and fix at the first failure)

| # | Gate | Command (in `~/spyrite_tile`) | Proves | Needs |
|---|---|---|---|---|
| 1 | static | `uvx pyflakes addon/spyrite_tile packages` | no undefined names | nothing |
| 2 | pure | `make test-pure` | generator, normalize, backends (mocked HTTP) | `.venv` |
| 3 | headless Blender | `make blender-test-deps` once, then `make test-blender` | API numbers (`test_api.py`), ops vs api (`test_ops.py`), relations (`test_metamorphic.py`), registration | nothing (isolated) |
| 4 | live MCP | `make test-live` (`ARGS=--reload-api` after `api.py` edits) | the agent path in the user's open Blender: MCP -> blended -> plugin -> api | user's Blender running with mcp add-on |
| 5 | pixel oracle | `make test-visual` (after 4) | what the viewport *draws* matches the mesh data | same |
| 6 | GUI interaction | `tests/gui/run_gui_smoke.sh` | palette overlay, Build/Paint/Fill clicks, keymaps, save/reopen (simulated input, own isolated window, ~80 s) | a display |
| 7 | render | blended `render_views` / `set_pixel_art_view` | contact sheet for art direction | live Blender |
| 8 | blended | `cd ~/blended && make test` | the plugin hook did not break core | nothing |

Which gates for which change:
- add-on API / builder / UV code: 1, 3, 4, 5 (+6 if modal/tool/keymap/gui code touched).
- `sprytile_gui.py`, modal, tools, keymaps, panels: 1, 3, 6, then a live screenshot.
- `spyrite_tile_ops`: 3 (`test_ops.py`), 4, blended V4 check (§8).
- generator/backends: 2 plus a real stdio MCP call (§7).
- pixel service on `big`: §7.

## 2. Reaching the user's live Blender

- Is it there? `lsof -nP -iTCP:9876 -sTCP:LISTEN` (blended mcp add-on default port 9876).
- Path: MCP stdio client -> `/Users/ladvien/blended/.venv/bin/blender-mcp`. If the session has
  the `blended` MCP tools attached (session started in `~/spyrite_tile`, `.omp/mcp.json`), call
  them directly; otherwise script it like `scripts/mcp_live_smoke.py` (copy its `_call`/`_returned`).
- blended rules you will hit: `declare_plan {steps:[...]}` first; every scene-changing call needs
  `plan_step`; `run_python` needs `source` (not `code`) and `reason` (what no op could do).
  Readers (`tile_object_report`, screenshots, `get_objects_summary`) need no plan.
- `run_python` output is truncated ("[N earlier characters dropped]"): write large JSON to a
  file under `/tmp` from inside Blender and read it on the host (Blender is on this machine).
- Code reload: blended + `spyrite_tile_ops` re-import automatically on source change. The add-on
  does not: `api.py` can be reloaded (`importlib.reload(sys.modules['bl_ext.user_default.spyrite_tile.api'])`,
  what `--reload-api` does). Class/property/keymap/gui changes need a Blender restart: ask the
  user to restart (one sentence), do not try to hot-reload registered classes.
- Useful MCP tools: `get_screenshot_of_area_as_image {area_ui_type:'VIEW_3D'}`,
  `get_screenshot_of_window_as_image`, `get_screenshot_of_window_as_json` (layout, active
  object, selection), `get_objects_summary`, `get_object_detail_summary`,
  `jump_to_view3d_object_by_name`, `render_views {object_name, look_for}`, `tile_object_report`.

## 3. Screenshots, zoom, and looking

Recipe (all inside one or two `run_python` calls; save state first, restore in `finally`):
1. Save `region_3d` (perspective, location, rotation, distance), `space.overlay.show_overlays`,
   `space.shading` (type, color_type, light), mode, selection, and which objects are visible.
2. Isolate the subject: `hide_set(True)` every other visible object. Coplanar or nearer objects
   are otherwise measured as the subject (this really happened: an old floor at z=0 z-fought
   the board and the probe saw olive instead of 16 tiles).
3. Look straight at it: `view_perspective='ORTHO'`, rotation Euler (0,0,0) top / (90,0,0) front
   / (90,0,90) right, `view_location` = bbox centre, `view_distance` ~ 1.5 x bbox diagonal.
   Zoom = smaller distance or centre on one cell (`visual_probe.py --zoom-cell X Y`).
4. Overlays off; `shading.type='SOLID'`, `color_type='TEXTURE'`, `light='FLAT'` (Material colour
   shows grey tiles; that was a real bug fixed by `set_pixel_art_view`).
5. Screenshot the VIEW_3D area. Pixel mapping from region coords (Blender, origin bottom-left)
   to image: `scale = image.width / area.width`; `x_img = (region.x - area.x + x) * scale`;
   `y_img = image.height - (region.y - area.y + y) * scale`. Skip points under other regions
   (header, toolbar, N-panel: `area.regions` rects) — the WINDOW region spans under them.
6. Zoom crops: crop a face box plus padding, `resize(..., Image.Resampling.NEAREST)` x8, save,
   and `read` it. Check texel edges are hard (no ramps) and colours flat.
7. Restore everything from step 1.
Perspective framing for humans: Euler (60, 0, 30) degrees, distance 1.6 x diagonal.

## 4. Pixel oracle: `scripts/visual_probe.py`

```
.venv/bin/python scripts/visual_probe.py --object <name> --tileset <png> --tile-size 16 \
    --view top|front|right [--zoom-cell X Y] [--min-faces N] [--tolerance 12] [--allow-uniform] --out <dir>
```
Expected colour = mean of the inner half of the tile in the image; observed = mean of the inner
half of the face's screen box (orientation independent). Prints `VISUAL_PROBE_OK measured=..
tiles=.. max_delta=..` or `VISUAL_PROBE_FAILED` with the worst face. Evidence: `screenshot.png`,
`annotated.png` (green ok / red mismatch), `zoom_*.png`, `faces.json`. Measured baseline on the
fixture: max_delta 1.0.

Failure reading: many faces red with one shared wrong colour -> occlusion or wrong shading;
one face red -> that face's UVs/tile; nothing measurable -> view framing or UI covering;
"expected colours do not vary" -> build a varied scene (`spyrite_probe_board`) first.

Negative control (do it whenever the probe or the drawing path changes):
shift one face's UVs by 0.5 via `run_python`, expect `1/N faces differ ... worst face <that index>`,
then repair with `place_tiles` on that cell and expect OK again.

## 5. Metamorphic tests

Write relations, not expected numbers. Pattern: `tests/blender/test_metamorphic.py`
(`build(placements)`, `faces_of(obj)` = {face centre: {vertex: uv}}, `colours_of(obj, image)` =
image texel under each face's mean UV). Existing relations: translation, ppu scaling, XY<->XZ swap,
rotate 180 == flip x+y, re-placement has no memory, order independence, fill == union of places,
remove inverts place, atlas permutation (shuffle tiles in the image and remap tile_xy -> same
colours), save/load round trip.

Candidates when touching other areas:
- YZ <-> XZ permutation; rotate 90 four times == identity; flip twice == identity.
- `paint_faces` on a built face == `place_tiles` with the same tile.
- multi-tile span (2,1) == two 1x1 tiles side by side in colour.
- camera/view: object moved by d and view moved by d -> identical screenshot (pixel diff 0).
- generator `normalize`: idempotent (`normalize(normalize(x)) == normalize(x)`); output colours
  are a subset of `palette_hex`; seam ratio unchanged under `np.roll` by the full width; atlas
  permutation of inputs permutes `tile_xy` only.
- interactive tools (GUI smoke): Build at a cell then Paint same tile == Build once.
Keep every relation able to fail: assert the probe varies inside the test (e.g. 16 distinct
colours) and add the mutant check below once.

## 6. Mutation check of a new test suite

1. `cp file /tmp/file.orig`. 2. Introduce one plausible bug with a python replace that asserts the
target string occurs (`assert s.count(old) == 1`); `sed -i ''` silently matches nothing on
special characters. 3. Run the gate; record which tests failed. 4. Restore; `diff -q` to prove it.
A surviving mutant means either the suite is blind or the mutated code is not on the effective
path — find out which before concluding. Measured example: in `api.place_tiles`,
`data.mesh_rotate` is stored state only; the UV rotation comes from `_rotated_frame(...)`. Mutating
`mesh_rotate` survived everything; mutating `_rotated_frame` failed 15 tests.

## 7. Non-Blender pieces

- Generator MCP server: real stdio session to `.venv/bin/spyrite-tile-gen` (see `mcp.client.stdio`),
  `list_tools`, call `compose_atlas`/`normalize_image` on Pillow-made PNGs; content must be
  TextContent JSON + ImageContent. `RD_API_KEY` missing must come back as a tool error. Paid
  Retro Diffusion calls only with the user's consent; always `estimate_cost` first.
- Pixel service: `curl -s http://192.168.1.110:8190/health`; one 16x16 seamless generate must
  return a 1024x1024 image; `ssh big gpu-tenant status` shows `spyrite-tile@big` while it runs.
  Never disturb other GPU tenants. Port blocked -> the user must change ufw (sudo); say exactly
  which command, then verify yourself.

## 8. Self-review of the diff (before committing)

- `git diff` read top to bottom: identifiers kept (`sprytile.*`, `scene.sprytile_data`, ...),
  no stray debug, no dead code left by a move, docstrings still true.
- blended op contract: params annotated, numeric names unit-suffixed, one-line docstring
  <= 120 chars; check `/Users/ladvien/blended/.venv/bin/python -c "from blended.agent.tools import TOOL_SCHEMAS; ..."` lists the op and
  `PLAN_REQUIRED_TOOLS` excludes only readers.
- New behaviour has a test that failed before the change (or a mutant proving it can).
- README/Makefile updated when a command or workflow changed.
- `git status` clean of scratch files; scratch lives in `/tmp` or `outputs/` (gitignored).

## 9. Report template

```
Verdict: <works / broken / partly> — one sentence.
Gates: <gate>: <command> -> <observed summary line>   (one row per gate run; say which were skipped and why)
Visual: <image path> — <what I see in it>
Negative controls: <what I broke> -> <how the check failed> -> restored -> <pass>
Not verified: <what and why>
User-visible side effects: <objects added to their scene, settings changed>
```
