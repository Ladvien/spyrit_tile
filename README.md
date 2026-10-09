<p align="center">
    <img src="sprytile-logo.png?raw=true" height="100px"/>
    <h1 align="center">Spyrite Tile</h1>
    <h4 align="center">
        Agent-driven 3D pixel-art tile building for <img src="https://download.blender.org/institute/logos/blender-socket.png" height="20px"/> Blender 5.2. A fork of <a href="https://github.com/Sprytile/Sprytile">Sprytile</a>.
    </h4>
  <br>
</p>

Spyrite Tile is a Blender add-on for creating tile based low spec 3D scenes, forked from Sprytile by Jeiel Aranal and ported to Blender 5.2. Besides the interactive tools, it can be driven by an agent over MCP: an agent generates pixel art, then builds tile scenes through [blended](https://github.com/ladvien/blended).

> **Compatibility note.** Spyrite Tile keeps all of Sprytile's internal identifiers (`sprytile.*` operators, `scene.sprytile_data`, `scene.sprytile_mats`, `object.sprytile_gridid`, keymap and mesh layer names) so that existing data keeps working. Only user-visible names are rebranded. As a consequence:
>
> * Spyrite Tile **cannot be enabled alongside the original Sprytile add-on**. Disable or remove Sprytile first.
> * Tile data in existing Sprytile 0.5.x `.blend` files keeps working.

### Features

* Tile building: Build your mesh directly with tiles, skip tedious UV mapping while quickly rotating and flipping tiles.
* UV painting: Create your mesh with other Blender tools, then quickly UV map them to your tiles. Spend less time in the UV editor.
* Pixel grid tools: Keep your mesh aligned to the grid with pixel translation, move vertices around with confidence.

### Demo:

![Timelapse](https://img.itch.io/aW1hZ2UvOTg5NjYvNTE3NTczLmdpZg==/250x600/mDFwN0.gif)

### Requirements:

Blender 5.2 LTS or newer (`blender_version_min` in `addon/spyrite_tile/blender_manifest.toml`).

### Install:

* **From a zip:** build one with
  `blender --command extension build --source-dir addon/spyrite_tile --output-dir dist`,
  then `Edit > Preferences > Get Extensions > Install from Disk` and pick it.
* **From this checkout (development):** `make install-addon` symlinks
  `addon/spyrite_tile` into Blender 5.2's `extensions/user_default` and enables it
  (it backs up `userpref.blend` first). Restart Blender after add-on edits.

### Agent-driven building

Three pieces, all in this repository:

| Piece | Where it runs | What it does |
|---|---|---|
| `addon/spyrite_tile/api.py` | inside Blender | Headless tile API: `create_tileset`, `create_tile_object`, `place_tiles`, `fill_tiles`, `fill_pattern` (seeded random, stamp, autotile), `remove_tiles`, `paint_faces`, `build_room`, `extrude_edge`, `move_faces`, `describe_tile_object` (per face: tile, span, rotation, flips, layer, plane, cell, offset, `on_grid`), `describe_scene`, `checkpoint` / `rollback` / `discard_checkpoint` / `list_checkpoints` (session-only snapshots of tile objects), `select_faces` (filter faces by tile/tag/plane/offset/cell rect/layer/facing or flood a connected region; feed the indices to `paint_faces`), `build_spec`, `export_spec`, `verify_tile_object` (renders with Workbench and checks every visible face's pixels against its tile) |
| `packages/spyrite_tile_ops` | inside Blender, via blended | blended op plugin (entry point `blended.ops`) exposing the API as the tools `import_tileset`, `create_tile_object`, `place_tiles`, `fill_tiles`, `fill_pattern` (seeded random, stamp, autotile), `remove_tiles`, `paint_faces`, `build_room`, `extrude_edge`, `move_faces`, `tile_object_report`, `scene_report`, `select_tile_faces`, `verify_tile_object`, `checkpoint`, `rollback`, `discard_checkpoint`, `list_checkpoints`, `set_pixel_art_view`, `build_spec`, `export_spec` |
| `packages/spyrite_tile_gen` | on the host | MCP server `spyrite-tile-gen`: `generate_tile`, `generate_sprite`, `generate_tileset` (Retro Diffusion, paid, needs `RD_API_KEY`; or the local SDXL service), `normalize_image`, `compose_atlas`, `estimate_cost`, `list_backends` |
| `services/pixel_server` | GPU host | FastAPI SDXL + pixel-art LoRA service behind the `local` backend (systemd user unit `spyrite-pixel.service`, port 8190) |

Setup: `uv sync --all-packages` (creates `.venv` with `spyrite-tile-gen`), `make install-addon`,
`make install-blended-plugin` (re-run after any `uv sync` in blended), and blended's own
`make install-mcp-addon`. `.mcp.json` / `.omp/mcp.json` register the MCP servers.

Dev MCP server `spyrite-tile-dev` (`packages/spyrite_tile_gen/.../devserver.py`): resources
`spyrite://skills[/{name}]`, `spyrite://scripts[/{name}]`, `spyrite://docs/api`, `spyrite://specs/example`;
prompts `spyrite_build_scene(brief)` and `spyrite_self_review(object_name)`; tools `list_skills`, `read_skill`,
`list_scripts`, `run_script(name, args, timeout_s)` over an allowlist (`visual_probe`, `live_smoke`, `test_pure`,
`test_blender`, `test_gui`, `test_visual`, `test_live`, `gui_check`; `bump_version` and `release` are not exposed).
The loop an agent follows is the `spyrite-agent-build` skill.

Conventions: `tile_xy` is (column from the left, row from the top) of the tileset image;
cells are whole tiles, one cell = `tile_size_px / pixels_per_unit_px` metres; planes are `XY`
(floor), `XZ` (front wall, normal -Y) and `YZ` (side wall, normal +X), offset along the normal
by `plane_offset_m`. Tilesets are idempotent: `import_tileset` with an image and layout that already
backs a tileset under another name creates nothing and returns that tileset (`reused_material`), so
always use the report's `material_name`. There is no automatic fallback between generation backends.

Tile names: put `<image stem>.spyrite.yaml` next to a tileset image (`tiles.png` -> `tiles.spyrite.yaml`):
`spyrite_tileset: 1` and `tiles: {grass: {xy: [0, 0], tags: [floor]}, wall_top: {xy: [2, 0], planes: [XZ, YZ]}}`.
Then any tile argument (`tile` / `tile_xy` in placements, `fill_tiles`, `paint_faces`) may be a name, `planes`
restricts where a name may be placed, and `tile_object_report` / `import_tileset` report the names.
`compose_atlas(names=[...], planes_by_name={...})` writes the sidecar for you. The YAML parser is
vendored (`addon/spyrite_tile/_vendor/yaml`, PyYAML 6.0.3, MIT) because Blender's Python has none.

Specs: `build_spec(spec_path)` builds a whole scene from a YAML file and `export_spec(object_names, spec_path)`
writes tile objects back to one (faces that are not whole-cell rectangles are listed, not written).
Both paths are absolute; tileset `image` paths are relative to the spec. Schema (see
`tests/fixtures/room.spyrite.yaml`, which builds a 50-face room with named tiles):

```yaml
spyrite_spec: 1
pixels_per_unit: 16              # default for objects
tilesets:
  terrain: {image: ./tiles.png, tile_size_px: [16, 16]}   # padding_px / margin_px optional
objects:
  room:
    tileset: terrain
    clear: false                 # true deletes the object's faces first
    fills:                       # each is fill_tiles; cells = [[min_x, min_y], [max_x, max_y]]
      - {plane: XY, plane_offset_m: 0, cells: [[0, 0], [5, 5]], tile: grass}
    tiles:                       # each is one placement; tile is a name or [col, row]
      - {plane: XZ, plane_offset_m: 6, cell: [0, 0], tile: wall_top, rotation_deg: 90, layer: BASE}
```

Unknown keys are errors listing the valid ones; error messages start with the dotted path
(`objects.room.tiles[3].tile: ...`). A failed object is rolled back; earlier tilesets and objects stay.

Composites: `build_room(size_cells=(w, d, h), floor_tile, wall_tile, walls=("back", "left"), ceiling_tile=...)` builds a
floor, a back wall (XZ, normal -Y) and a left wall (YZ, normal +X) and an optional ceiling in one atomic call;
`extrude_edge(plane="XY", from_cell, to_cell, side="N"|"W", height_cells, tile)` raises a wall along a run of floor
cells; `move_faces(face_indices, delta_px=(dx, dy, dz))` shifts faces by whole pixels and rebuilds their UVs from the
tile data stored on them. The planes have fixed normals, so only back/left walls and N/W edges exist: build rooms
whose open sides face -Y and +X.

Overlays: `create_overlay_object(name, base_object_name, tileset_name, lift_m=0.002)` makes a child tile object of
a base object. Place on it with the BASE's `plane_offset_m`; its faces are built `lift_m` toward the viewer
(XY +, XZ -, YZ +), so read-back reports the lifted offsets and `tile_object_report`/`scene_report` give `overlay_of`.

Tests: `make test-pure` (generation package), `make blender-test-deps && make test-blender`
(add-on API and blended ops inside Blender 5.2, isolated from your user config), and
`tests/gui/run_gui_smoke.sh` (drives the interactive tools in a GUI Blender with simulated input).
`make test-live` drives the Blender you have open through blended's MCP server (the agent's own path),
builds `spyrite_smoke_room`, asserts its faces and tiles and saves a window screenshot to
`outputs/live_smoke/`; `ARGS=--reload-api` picks up `sprytile_core.py`, `sprytile_uv.py`, `sprytile_builder.py`, `spyrite_spec.py`, `spyrite_probe.py` and `api.py` edits (an agent does the same with the `reload_core` op) without a restart.
`make test-visual` (after `make test-live`) is a pixel oracle: it isolates a tile object, looks at it
orthographically, screenshots the viewport and checks every visible face's colour against its tile in
the tileset image (`scripts/visual_probe.py`; evidence in `outputs/visual_probe/`). The same comparison
(`addon/spyrite_tile/spyrite_probe.py`, pure Python) runs from inside Blender as the `verify_tile_object`
op: it renders the object with Workbench from the plane's side (scene and viewport untouched) and
returns `ok`, the number of faces judged and the mismatching faces; the render is kept in the evidence dir.
`tests/blender/test_metamorphic.py` checks relations between runs (translation, scale, plane swap,
rotation vs flips, order and history independence, fill vs place, remove vs place, atlas permutation,
save/load) instead of hand-computed values.

### Getting Started:

* [Sprytile Basics Tutorial](http://docs.sprytile.xyz/quick-start/) ([video](https://youtu.be/-ezYZgMp-R0)) (upstream documentation; the workflow is the same)

### Issue/Feature requests:

Bug reports and feature requests: [GitHub issues](https://github.com/ladvien/spyrit_tile/issues)

## Origin and credit

Spyrite Tile is a fork of [Sprytile](https://github.com/Sprytile/Sprytile) by Jeiel Aranal, released under the MIT license. Sprytile's original copyright notice is preserved in `license.txt`.

* This repository was cloned from upstream commit `6b68d00` (v0.5.20, "Merge branch 'dev_28' for version 0.5.2 release").
* The Blender 4.5+/5.x port is upstream PR #154 by denischernitsyn, merged here as #154.
* Issues and PRs #1-#154 were imported from Sprytile/Sprytile, and each one links to its original.
* Upstream contributors, from `git shortlog -sn 6b68d00` (commits):

  | Commits | Contributor |
  |---:|---|
  | 611 | Jeiel Aranal |
  | 76 | Spadafina Alfredo |
  | 11 | Yonnji |
  | 7 | Alfredo Spadafina |
  | 2 | Spadafina |
  | 2 | ologon |
  | 1 | Andrea Faulds |
  | 1 | Cezary Kopias |
  | 1 | cg-cnu |
  | 1 | dani |
  | 1 | lindor |

  (Some of these are the same person under different git identities.) The Blender 2.8 port was done by [Yonnji](https://github.com/Yonnji) and [ologon](https://github.com/ologon), with additional contributions by [brandy92](https://github.com/brandy92).

### Blender 4.5+ / 5.x port notes:

* All `bgl` drawing was rewritten against the `gpu` module (`bgl` was removed in
  Blender 4.0). The tile GUI shaders are built with `GPUShaderCreateInfo` and
  sample through `texelFetch`, which keeps the nearest neighbour + repeat
  behaviour that used to come from GL sampler state.
* Tools register through `bpy.utils.register_tool` instead of splicing private
  `bl_ui` tool lists.
* Modules use relative imports, so the addon no longer puts itself on
  `sys.path` and installs cleanly as a Blender extension.
* The bundled RxPY 1.6 copy (245 files, ~12.7k lines) was replaced by
  `sprytile_event.py`, a small synchronous event source with the same
  semantics. Only five of its methods were ever used.
* The unused `addon_updater` was dropped.
