"""MCP server: generate pixel-art tiles, sprites and tilesets for Spyrite Tile.

Runs on the host, not inside Blender (the Blender side is `spyrite_tile_ops`,
exposed through the `blended` MCP server). Every generate tool returns a JSON
text block `{path, size_px, cost_usd, seed, normalize}` and a nearest-neighbour
preview of the saved PNG. An error is a tool error carrying the message.

MCP: DOI 10.48550/arXiv.2503.23278.
"""

from __future__ import annotations

import io
import json
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

import httpx
from mcp.server.fastmcp import FastMCP, Image
from PIL import Image as PilImage

from spyrite_tile_gen import store
from spyrite_tile_gen.atlas import compose_atlas as compose_atlas_png
from spyrite_tile_gen.backends.base import (
    BackendError,
    ImageRequest,
    PixelBackend,
    RawImage,
    TilesetRequest,
    describe_error,
)
from spyrite_tile_gen.backends.local_sdxl import LocalSdxlBackend
from spyrite_tile_gen.backends.retro_diffusion import (
    MAX_USD_ENVIRONMENT_VARIABLE,
    STYLE_TILE,
    RetroDiffusionBackend,
)
from spyrite_tile_gen.normalize import normalize

INSTRUCTIONS = (
    "Generates pixel-art tiles, sprites and tilesets as PNG files for Spyrite Tile "
    "(tile-based 3D scenes in Blender).\n"
    "Workflow:\n"
    "1. generate_tile / generate_sprite / generate_tileset. Each saves a PNG and "
    "returns its absolute path.\n"
    "2. Optional compose_atlas joins same-size tiles into one tileset image. "
    "tile_xy is (column from the left, row from the top).\n"
    "3. In Blender, through the blended server: declare_plan, "
    "import_tileset(image_path=<path>, tile_size_px=[n, n]), create_tile_object, "
    "then place_tiles / fill_tiles. Check with tile_object_report, then "
    "set_pixel_art_view (without it render_views draws untextured grey) and render_views.\n"
    "Backends: 'retro_diffusion' (paid, needs RD_API_KEY) and 'local' (free SDXL on "
    "the LAN GPU). There is no automatic fallback: a busy GPU or a missing key is an "
    "error for you to resolve, not to route around.\n"
    "Paid calls cost money. Call estimate_cost first; list_backends shows the balance. "
    f"A call priced above {MAX_USD_ENVIRONMENT_VARIABLE} (default $0.50) is refused.\n"
    "Sizes: retro_diffusion tile 16-64 px, sprite 64-384 px, tileset 16-32 px per "
    "tile. The local backend cannot do tilesets, transparency or styles."
)
assert len(INSTRUCTIONS) <= 2048

PREVIEW_MAX_PX = 512

mcp = FastMCP("spyrite-tile-gen", instructions=INSTRUCTIONS)
# Every tool is registered with structured_output=False: a `list[Any]` return
# would otherwise get an output schema, and validating an `Image` against it
# fails ("Unable to serialize unknown type"). Unstructured, the client receives
# the JSON as TextContent and the preview as ImageContent.

BACKENDS: dict[str, Callable[[], PixelBackend]] = {
    "retro_diffusion": RetroDiffusionBackend,
    "local": LocalSdxlBackend,
}
BackendName = Literal["retro_diffusion", "local"]


def _backend(name: str) -> PixelBackend:
    factory = BACKENDS.get(name)
    if factory is None:
        raise ValueError(f"unknown backend {name!r}; use one of {sorted(BACKENDS)}")
    return factory()


def _palette(palette_hex: list[str] | None) -> tuple[str, ...] | None:
    return tuple(palette_hex) if palette_hex else None


def _preview(png: bytes) -> Image:
    image = PilImage.open(io.BytesIO(png)).convert("RGBA")
    longest = max(image.size)
    if longest <= PREVIEW_MAX_PX:
        factor = PREVIEW_MAX_PX // longest
        image = image.resize((image.width * factor, image.height * factor), PilImage.Resampling.NEAREST)
    else:
        image = image.resize(
            (max(1, image.width * PREVIEW_MAX_PX // longest), max(1, image.height * PREVIEW_MAX_PX // longest)),
            PilImage.Resampling.NEAREST,
        )
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return Image(data=buffer.getvalue(), format="png")


def _finish(
    raw: RawImage,
    request: Any,
    width_px: int,
    height_px: int,
    palette_hex: tuple[str, ...] | None,
    max_colors: int | None,
    extra: dict[str, Any] | None = None,
) -> list[Any]:
    """Normalise, store and describe one backend result."""
    png, report = normalize(raw.png, width_px, height_px, palette_hex, max_colors)
    sidecar = {
        "provider_job_id": raw.provider_job_id,
        "request": request,
        "backend_params": raw.params,
        "seed": raw.seed,
        "cost_usd": raw.cost_usd,
        "normalize_report": report.as_dict(),
    }
    path = store.save_generated(png, raw.provider, sidecar)
    info = {
        "path": str(path),
        "size_px": list(report.size_px),
        "cost_usd": raw.cost_usd,
        "seed": raw.seed,
        "normalize": report.as_dict(),
        **(extra or {}),
    }
    return [json.dumps(info), _preview(png)]


@mcp.tool(structured_output=False)
async def list_backends() -> str:
    """Which backends are usable now: Retro Diffusion key and balance, local service health."""
    report: dict[str, Any] = {}
    retro = RetroDiffusionBackend()
    entry: dict[str, Any] = {"configured": retro.configured()}
    if retro.configured():
        try:
            entry["balance_usd"] = await retro.balance_usd()
        except BackendError as error:
            entry["error"] = str(error)
    report["retro_diffusion"] = entry
    local = LocalSdxlBackend()
    entry = {"url": local.base_url}
    try:
        entry["health"] = await local.health()
    except (httpx.HTTPError, ValueError) as error:
        entry["error"] = describe_error(error)
    report["local"] = entry
    return json.dumps(report)


@mcp.tool(structured_output=False)
async def estimate_cost(
    kind: Literal["tile", "sprite", "tileset"],
    backend: BackendName,
    prompt: str,
    size_px: int,
) -> str:
    """Price a generation in USD without generating or spending anything.

    size_px is the tile edge for 'tile' and 'tileset', and width = height for 'sprite'.
    """
    chosen = _backend(backend)
    request: ImageRequest | TilesetRequest
    if kind == "tileset":
        request = TilesetRequest(inside_prompt=prompt, tile_size_px=size_px)
    else:
        style = STYLE_TILE if kind == "tile" and backend == "retro_diffusion" else None
        request = ImageRequest(prompt=prompt, width_px=size_px, height_px=size_px, style=style)
    estimate = await chosen.estimate_usd(request)
    return json.dumps({"backend": backend, "kind": kind, "estimated_usd": estimate})


@mcp.tool(structured_output=False)
async def generate_tile(
    prompt: str,
    size_px: int = 16,
    backend: BackendName = "retro_diffusion",
    seamless: bool = True,
    palette_hex: list[str] | None = None,
    max_colors: int | None = 16,
    seed: int | None = None,
    style: str | None = None,
) -> list[Any]:
    """Generate one square seamless pixel-art tile, normalised to size_px and saved as PNG."""
    chosen = _backend(backend)
    palette = _palette(palette_hex)
    resolved_style = style or (STYLE_TILE if backend == "retro_diffusion" else None)
    request = ImageRequest(
        prompt=prompt,
        width_px=size_px,
        height_px=size_px,
        seed=seed,
        seamless_x=seamless,
        seamless_y=seamless,
        transparent=False,
        palette_hex=palette,
        style=resolved_style,
    )
    raw = await chosen.generate_image(request)
    return _finish(raw, {"kind": "tile", **asdict(request)}, size_px, size_px, palette, max_colors)


@mcp.tool(structured_output=False)
async def generate_sprite(
    prompt: str,
    width_px: int,
    height_px: int,
    backend: BackendName = "retro_diffusion",
    transparent: bool = True,
    palette_hex: list[str] | None = None,
    max_colors: int | None = None,
    seed: int | None = None,
    style: str | None = None,
) -> list[Any]:
    """Generate a pixel-art sprite at exactly width_px x height_px, with alpha if transparent."""
    chosen = _backend(backend)
    palette = _palette(palette_hex)
    request = ImageRequest(
        prompt=prompt,
        width_px=width_px,
        height_px=height_px,
        seed=seed,
        transparent=transparent,
        palette_hex=palette,
        style=style,
    )
    raw = await chosen.generate_image(request)
    return _finish(raw, {"kind": "sprite", **asdict(request)}, width_px, height_px, palette, max_colors)


@mcp.tool(structured_output=False)
async def generate_tileset(
    inside_prompt: str,
    outside_prompt: str | None = None,
    tile_size_px: int = 16,
    palette_hex: list[str] | None = None,
    seed: int | None = None,
) -> list[Any]:
    """Generate a Wang-style tileset sheet (Retro Diffusion only); reports its columns and rows."""
    chosen = _backend("retro_diffusion")
    palette = _palette(palette_hex)
    request = TilesetRequest(
        inside_prompt=inside_prompt,
        outside_prompt=outside_prompt,
        tile_size_px=tile_size_px,
        seed=seed,
        palette_hex=palette,
    )
    raw = await chosen.generate_tileset(request)
    with PilImage.open(io.BytesIO(raw.png)) as sheet:
        width_px, height_px = sheet.size
    return _finish(
        raw,
        {"kind": "tileset", **asdict(request)},
        width_px,
        height_px,
        palette,
        None,
        extra={
            "tile_size_px": tile_size_px,
            "columns": width_px // tile_size_px,
            "rows": height_px // tile_size_px,
        },
    )


@mcp.tool(structured_output=False)
def normalize_image(
    path: str,
    width_px: int,
    height_px: int,
    palette_hex: list[str] | None = None,
    max_colors: int | None = None,
) -> list[Any]:
    """Resize an existing PNG to exact pixels, binarise alpha, quantise; saves a new PNG."""
    source = Path(path)
    if not source.is_absolute() or not source.is_file():
        raise ValueError(f"path must be an absolute path to an existing file, got {path!r}")
    png, report = normalize(source.read_bytes(), width_px, height_px, _palette(palette_hex), max_colors)
    saved = store.save_generated(
        png,
        "normalized",
        {"request": {"source": str(source), "width_px": width_px, "height_px": height_px}, "normalize_report": report.as_dict()},
    )
    info = {"path": str(saved), "size_px": list(report.size_px), "normalize": report.as_dict()}
    return [json.dumps(info), _preview(png)]


@mcp.tool(structured_output=False)
def compose_atlas(
    paths: list[str], tile_size_px: int, columns: int, output_name: str
) -> list[Any]:
    """Join same-size tile PNGs into one tileset, row-major from the top-left; returns each tile_xy."""
    for entry in paths:
        if not Path(entry).is_absolute():
            raise ValueError(f"every path must be absolute, got {entry!r}")
    png, placed = compose_atlas_png(paths, tile_size_px, columns)
    saved = store.save_named(
        png,
        output_name,
        {
            "tile_size_px": tile_size_px,
            "columns": columns,
            "tiles": [{"path": p, "tile_xy": list(xy)} for p, xy in zip(paths, placed)],
        },
    )
    rows = -(-len(paths) // columns)
    info = {
        "path": str(saved),
        "size_px": [columns * tile_size_px, rows * tile_size_px],
        "tile_size_px": tile_size_px,
        "columns": columns,
        "rows": rows,
        "tile_xy": [list(xy) for xy in placed],
    }
    return [json.dumps(info), _preview(png)]


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
