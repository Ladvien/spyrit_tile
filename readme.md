<p align="center">
    <img src="sprytile-logo.png?raw=true" height="100px"/>
    <h1 align="center">Spyrite Tile</h1>
    <h4 align="center">
        Agent-driven 3D pixel-art tile building for <img src="https://download.blender.org/institute/logos/blender-socket.png" height="20px"/> Blender 5.2. A fork of <a href="https://github.com/Sprytile/Sprytile">Sprytile</a>.
    </h4>
  <br>
</p>

Spyrite Tile is a Blender add-on for creating tile based low spec 3D scenes, forked from Sprytile by Jeiel Aranal and ported to Blender 4.5+ / 5.x.

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

Blender 4.5 LTS or newer, including the 5.x series.

### Install:

* **As an extension (Blender 4.2+):** `Edit > Preferences > Add-ons > Install from Disk`
  and pick the zip. Build one with
  `blender --command extension build --source-dir . --output-dir dist`.
* **As a legacy addon:** drop this folder into your Blender `scripts/addons`
  directory and enable the add-on in Preferences.

### Getting Started:

* [Sprytile Basics Tutorial](http://docs.sprytile.xyz/quick-start/) ([video](https://youtu.be/-ezYZgMp-R0)) (upstream documentation; the workflow is the same)

### Issue/Feature requests:

Bug reports and feature requests: [GitHub issues](https://github.com/ladvien/spyrite_tile/issues)

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
