"""Local SDXL pixel-art backend: the service in `services/pixel_server`.

The server runs SDXL with the pixel-art LoRA on a LAN GPU host and returns
1024-px-class images; `spyrite_tile_gen.normalize` reduces them to the
requested size. It costs nothing, cannot produce alpha, has no styles and
cannot build Wang tilesets, so those requests fail with `CapabilityError`
instead of returning something other than what was asked. A leased GPU
(HTTP 503 `gpu_busy`) fails with `BackendBusy`; there is no fallback to the
paid backend.
"""

from __future__ import annotations

import base64
import os

import httpx

from spyrite_tile_gen.backends.base import (
    BackendBusy,
    BackendError,
    CapabilityError,
    ImageRequest,
    RawImage,
    TilesetRequest,
    describe_error,
)

URL_ENVIRONMENT_VARIABLE = "SPYRITE_LOCAL_URL"
DEFAULT_URL = "http://192.168.1.110:8190"
# One generation is 8 LCM steps at 1024 px, plus a cold model load of about a
# minute the first time after the service released the GPU.
GENERATE_TIMEOUT_S = 300.0
HEALTH_TIMEOUT_S = 5.0


class LocalSdxlBackend:
    """HTTP client for the local pixel service."""

    name = "local"

    def __init__(
        self,
        *,
        base_url: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = (
            base_url or os.environ.get(URL_ENVIRONMENT_VARIABLE) or DEFAULT_URL
        ).rstrip("/")
        self._transport = transport

    @property
    def base_url(self) -> str:
        return self._base_url

    def _client(self, timeout_s: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url, timeout=timeout_s, transport=self._transport
        )

    async def health(self) -> dict:
        async with self._client(HEALTH_TIMEOUT_S) as client:
            response = await client.get("/health")
            response.raise_for_status()
            return response.json()

    async def estimate_usd(self, req: ImageRequest | TilesetRequest) -> float:
        return 0.0

    async def generate_image(self, req: ImageRequest) -> RawImage:
        if req.seamless_x != req.seamless_y:
            raise CapabilityError(
                "local backend tiles both axes or neither; one-axis seamless is only "
                "available with backend='retro_diffusion'"
            )
        if req.transparent:
            raise CapabilityError(
                "local backend cannot produce transparency; use transparent=False "
                "or backend='retro_diffusion'"
            )
        if req.style:
            raise CapabilityError(
                "local backend has no styles; omit style or use backend='retro_diffusion'"
            )
        payload = {
            "prompt": req.prompt,
            "width_px": req.width_px,
            "height_px": req.height_px,
            "seed": req.seed,
            "seamless": req.seamless_x and req.seamless_y,
        }
        try:
            async with self._client(GENERATE_TIMEOUT_S) as client:
                response = await client.post("/v1/generate", json=payload)
        except httpx.TransportError as error:
            raise BackendError(
                f"local pixel service at {self._base_url} is unreachable ({describe_error(error)})"
            ) from error
        if response.status_code == 503:
            try:
                body = response.json()
            except ValueError:
                body = None
            if isinstance(body, dict) and body.get("error") == "gpu_busy":
                raise BackendBusy(f"local GPU is busy: {body.get('detail')}")
        if response.status_code >= 400:
            raise BackendError(
                f"local pixel service HTTP {response.status_code}: {response.text[:300]}"
            )
        try:
            data = response.json()
            png = base64.b64decode(data["images_b64"][0])
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise BackendError(
                f"local pixel service returned no image: {response.text[:200]}"
            ) from error
        return RawImage(
            png=png,
            provider=self.name,
            provider_job_id=None,
            cost_usd=0.0,
            seed=data.get("seed"),
            params=payload,
        )

    async def generate_tileset(self, req: TilesetRequest) -> RawImage:
        raise CapabilityError(
            "local backend cannot generate Wang tilesets; use backend='retro_diffusion'"
        )
