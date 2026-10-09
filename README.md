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
| `addon/spyrite_tile/api.py` | inside Blender | Headless tile API: `create_tileset`, `create_tile_object`, `place_tiles`, `fill_tiles`, `remove_tiles`, `paint_faces`, `describe_tile_object` (per face: tile, span, rotation, flips, layer, plane, cell, offset, `on_grid`), `describe_scene` |
| `packages/spyrite_tile_ops` | inside Blender, via blended | blended op plugin (entry point `blended.ops`) exposing the API as the tools `import_tileset`, `create_tile_object`, `place_tiles`, `fill_tiles`, `remove_tiles`, `paint_faces`, `tile_object_report`, `scene_report`, `set_pixel_art_view` |
| `packages/spyrite_tile_gen` | on the host | MCP server `spyrite-tile-gen`: `generate_tile`, `generate_sprite`, `generate_tileset` (Retro Diffusion, paid, needs `RD_API_KEY`; or the local SDXL service), `normalize_image`, `compose_atlas`, `estimate_cost`, `list_backends` |
| `services/pixel_server` | GPU host | FastAPI SDXL + pixel-art LoRA service behind the `local` backend (systemd user unit `spyrite-pixel.service`, port 8190) |

Setup: `uv sync --all-packages` (creates `.venv` with `spyrite-tile-gen`), `make install-addon`,
`make install-blended-plugin` (re-run after any `uv sync` in blended), and blended's own
`make install-mcp-addon`. `.mcp.json` / `.omp/mcp.json` register both MCP servers.

Conventions: `tile_xy` is (column from the left, row from the top) of the tileset image;
cells are whole tiles, one cell = `tile_size_px / pixels_per_unit_px` metres; planes are `XY`
(floor), `XZ` (front wall, normal -Y) and `YZ` (side wall, normal +X), offset along the normal
by `plane_offset_m`. There is no automatic fallback between generation backends.

Tests: `make test-pure` (generation package), `make blender-test-deps && make test-blender`
(add-on API and blended ops inside Blender 5.2, isolated from your user config), and
`tests/gui/run_gui_smoke.sh` (drives the interactive tools in a GUI Blender with simulated input).
`make test-live` drives the Blender you have open through blended's MCP server (the agent's own path),
builds `spyrite_smoke_room`, asserts its faces and tiles and saves a window screenshot to
`outputs/live_smoke/`; `ARGS=--reload-api` picks up `sprytile_uv.py`, `sprytile_builder.py` and `api.py` edits without a restart.
`make test-visual` (after `make test-live`) is a pixel oracle: it isolates a tile object, looks at it
orthographically, screenshots the viewport and checks every visible face's colour against its tile in
the tileset image (`scripts/visual_probe.py`; evidence in `outputs/visual_probe/`).
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
