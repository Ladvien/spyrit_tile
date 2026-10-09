---
name: spyrite-agent-build
description: "Build, read back, verify and roll back Spyrite Tile pixel-art scenes through blended's MCP ops: declare_plan, scene_report, build_spec/fills/patterns/rooms, tile_object_report, verify_tile_object, render_views, checkpoint/rollback, reload_core. Use for any tile-scene building brief."
---

# Spyrite Tile: agent build loop

Reference project: `/Users/ladvien/spyrit_tile`. Blender ops come from the `blended` MCP server;
skills, scripts and docs come from the `spyrite-tile-dev` MCP server (resources below).

## The loop

1. `declare_plan` (once, before any scene-changing call).
2. `scene_report`: existing tilesets (with `tile_names`), tile objects, removed tilesets.
3. Tiles: generate with `spyrite-tile-gen` (`generate_tile`, `compose_atlas(names=[...])` writes the
   `<stem>.spyrite.yaml` name sidecar), then `import_tileset`. Pass the report's `material_name` as `tileset_name`
   to later ops (tilesets are idempotent: `reused_material`).
4. Build, smallest step first:
   - whole scene from YAML: `build_spec(spec_path)` (example: resource `spyrite://specs/example`);
     `export_spec(objects, spec_path)` writes a scene back out;
   - or `create_tile_object(name, tileset_name)`, then `place_tiles`, `fill_tiles`, `fill_pattern` (random/stamp/autotile),
     `build_room`, `extrude_edge` (`count` wall rows), `move_faces`, `paint_faces`, `remove_tiles`;
   - non-contiguous edits: `select_tile_faces(object_name, where)` then `paint_faces` on the indices;
   - decals: `create_overlay_object(name, base_object_name, tileset_name)` (child object lifted off the base plane).
   Tiles may be given by name or `[column, row]`.
5. Read back: `tile_object_report` (per face tile/name, span, rotation, flips, layer, plane, cell,
   offset, `on_grid`). Check every number the brief gave.
6. Prove it: `verify_tile_object(object_name, view)` renders with Workbench and compares every face
   to its tile pixels (`ok`, `mismatches`, `evidence_dir`).
7. Look: `set_pixel_art_view`, then `render_views`; read the images yourself.
8. Before risky or multi-step edits: `checkpoint(objects)`; on failure `rollback(checkpoint_id)`; finish with
   `discard_checkpoint`. `list_checkpoints` shows what exists (session-only).
9. After editing add-on code: `reload_core` (no Blender restart; registered classes still need one).

## Rules

- Plane conventions: XY floor, XZ front wall (normal -Y), YZ side wall (normal +X); rooms open toward -Y and +X.
- Errors name the fix; read them, change one thing, retry. Stop after the same failure twice.
- Done = executed, gate passed, verified, looks right. For a review of finished work use the
  `blender-self-review` skill (prompt `spyrite_self_review`).

## Resources and tools on `spyrite-tile-dev`

`spyrite://skills`, `spyrite://skills/{name}`, `spyrite://scripts`, `spyrite://scripts/{name}`,
`spyrite://docs/api`, `spyrite://specs/example`; tools `list_skills`, `read_skill`, `list_scripts`,
`run_script(name, args, timeout_s)` (allowlist: visual_probe, live_smoke, test_pure, test_blender,
test_gui, test_visual, test_live, gui_check).
