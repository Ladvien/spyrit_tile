"""Backend contract for pixel-art generation.

A backend turns a request into raw PNG bytes plus provenance. Normalisation
(exact size, alpha, palette) happens afterwards in `spyrite_tile_gen.normalize`,
never inside a backend, so every backend is judged by the same pipeline.

There is no automatic fallback between backends. A call names its backend;
when that backend is busy, unconfigured, over budget or unable to do the
job, the call fails with the reason and the caller decides what to do next.
A silent switch from a free local model to a paid cloud one would spend money
the caller did not agree to, and a switch the other way would return a
different art style than the one requested.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class BackendError(Exception):
    """A backend could not produce the image."""


class BackendBusy(BackendError):
    """The backend is occupied (for example the GPU is leased to another job)."""


class CapabilityError(BackendError):
    """The backend cannot do what was asked, however often it is retried."""


class BudgetExceeded(BackendError):
    """The estimated cost is above the per-call spending cap."""


def describe_error(error: Exception) -> str:
    """`type: message`; httpx timeouts have an empty message, so the type must always show."""
    return f"{type(error).__name__}: {error}" if str(error) else type(error).__name__


@dataclass(frozen=True)
class ImageRequest:
    """One image: a seamless tile or a sprite."""

    prompt: str
    width_px: int
    height_px: int
    seed: int | None = None
    seamless_x: bool = False
    seamless_y: bool = False
    transparent: bool = False
    palette_hex: tuple[str, ...] | None = None
    style: str | None = None


@dataclass(frozen=True)
class TilesetRequest:
    """One Wang-style tileset: an inside texture, optionally an outside one."""

    inside_prompt: str
    outside_prompt: str | None = None
    tile_size_px: int = 16
    seed: int | None = None
    palette_hex: tuple[str, ...] | None = None


@dataclass(frozen=True)
class RawImage:
    """What a backend returned, before normalisation."""

    png: bytes
    provider: str
    provider_job_id: str | None
    cost_usd: float
    seed: int | None
    params: dict[str, Any] = field(default_factory=dict)


class PixelBackend(Protocol):
    """What the MCP server needs from a backend."""

    name: str

    async def estimate_usd(self, req: ImageRequest | TilesetRequest) -> float:
        """The cost of `req` in USD, without generating anything."""
        ...

    async def generate_image(self, req: ImageRequest) -> RawImage: ...

    async def generate_tileset(self, req: TilesetRequest) -> RawImage: ...
