<p align="center">
    <img src="sprytile-logo.png?raw=true" height="100px"/>
    <h1 align="center">Sprytile Painter</h1>
    <h4 align="center">
        A <img src="https://download.blender.org/institute/logos/blender-socket.png" height="20px"/> addon for creating tile based low spec 3D scenes. (Unofficial port for Blender 4.5+ / 5.x)
    </h4>
  <br>
</p>

### Features

* Tile building: Build your mesh directly with tiles, skip tedious UV mapping while quickly rotating and flipping tiles.
* UV painting: Create your mesh with other Blender tools, then quickly UV map them to your tiles. Spend less time in the UV editor.
* Pixel grid tools: Keep your mesh aligned to the grid with pixel translation, move vertices around with confidence.

### Demo:

![Timelapse](https://img.itch.io/aW1hZ2UvOTg5NjYvNTE3NTczLmdpZg==/250x600/mDFwN0.gif)

### Requirements:

Blender 4.5 LTS or newer, including the 5.x series. For Blender 2.8 - 4.4 use an
earlier release of this port.

### Install:

* **As an extension (Blender 4.2+):** `Edit > Preferences > Add-ons > Install from Disk`
  and pick the zip. Build one with
  `blender --command extension build --source-dir . --output-dir dist`.
* **As a legacy addon:** drop this folder into your Blender `scripts/addons`
  directory and enable "Sprytile Painter" in Preferences.

### Download:

Download from [releases](https://github.com/ologon/Sprytile/releases).

### Getting Started:

* [Sprytile Basics Tutorial](http://docs.sprytile.xyz/quick-start/) ([video](https://youtu.be/-ezYZgMp-R0))

### Community:

* Chat with the fellow users in the [Discord server](http://discord.sprytile.xyz/)
* Showcase your work or ask for support in the [forum](https://chemikhazi.itch.io/sprytile/community)

### Issue/Feature requests:

Bug reports for this port can be submitted to [GitHub issues](https://github.com/ologon/Sprytile/issues)

### Acknowledgments:

The bulk of Blender 2.8 porting work by was done by [Yonnji](https://github.com/Yonnji) and [ologon](https://github.com/ologon), with additional contributions by [brandy92](https://github.com/brandy92)

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
  semantics. Only five of its methods were ever used. This was housekeeping,
  not a porting requirement: RxPY 1.6 still imports fine on the Python versions
  Blender ships, and the addon loads with it either way. It does need the addon
  folder on `sys.path`, since it resolves its own modules with absolute
  `from rx...` imports, which puts a second `rx` into the global module
  namespace alongside any other addon that bundles one.
* The unused `addon_updater` was dropped, it was already disabled and would
  have tried to install the Blender 2.8 build over this one.