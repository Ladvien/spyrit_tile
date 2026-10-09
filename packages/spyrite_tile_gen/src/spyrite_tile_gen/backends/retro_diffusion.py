"""Retro Diffusion cloud backend (https://www.retrodiffusion.ai/app/guide/api).

Every paid call is priced first with the API's free dry run
(`check_cost: true`) and refused when the price is above
`SPYRITE_MAX_USD_PER_CALL` (default 0.50). Generation then goes through
`POST /v2/inferences` with a fresh `Idempotency-Key` and polls
`GET /v2/inferences/tasks/{task_id}`. A rate-limited submit (HTTP 429) is
retried with the same key after `Retry-After`; a task that failed is never
resubmitted, because the caller, not this client, decides whether to pay
again.
"""

from __future__ import annotations

import asyncio
import base64
import io
import os
import time
import uuid
from typing import Any

import httpx
from PIL import Image

from spyrite_tile_gen.backends.base import (
    BackendError,
    BudgetExceeded,
    ImageRequest,
    RawImage,
    TilesetRequest,
    describe_error,
)

BASE_URL = "https://api.retrodiffusion.ai/v2"
API_KEY_ENVIRONMENT_VARIABLE = "RD_API_KEY"
MAX_USD_ENVIRONMENT_VARIABLE = "SPYRITE_MAX_USD_PER_CALL"
DEFAULT_MAX_USD_PER_CALL = 0.50

STYLE_TILE = "rd_tile__single_tile"
STYLE_SPRITE = "rd_plus__default"
STYLE_TILESET = "rd_tile__tileset"
STYLE_TILESET_ADVANCED = "rd_tile__tileset_advanced"

# Allowed edge length in pixels per style: (minimum, maximum). A style that is
# not listed (a custom style) is passed through unchecked.
STYLE_SIZE_RANGES_PX: dict[str, tuple[int, int]] = {
    STYLE_TILE: (16, 64),
    STYLE_SPRITE: (64, 384),
    STYLE_TILESET: (16, 32),
    STYLE_TILESET_ADVANCED: (16, 32),
}

POLL_INTERVAL_S = 2.0
TASK_TIMEOUT_S = 180.0
REQUEST_TIMEOUT_S = 60.0
MAX_RATE_LIMIT_RETRIES = 3
DEFAULT_RETRY_AFTER_S = 5.0
_PENDING_STATUSES = frozenset({"accepted", "pending", "running"})


def _check_size(style: str, width_px: int, height_px: int) -> None:
    allowed = STYLE_SIZE_RANGES_PX.get(style)
    if allowed is None:
        return
    low, high = allowed
    for axis, size in (("width_px", width_px), ("height_px", height_px)):
        if not low <= size <= high:
            raise ValueError(
                f"{axis}={size} is outside {low}-{high} px for style {style!r}"
            )


def palette_png_base64(palette_hex: tuple[str, ...]) -> str:
    """A 1xN PNG with one pixel per palette colour, as raw base64."""
    colours = []
    for entry in palette_hex:
        digits = entry.lstrip("#")
        if len(digits) != 6:
            raise ValueError(f"palette colour {entry!r} is not #rrggbb")
        colours.append(tuple(int(digits[i : i + 2], 16) for i in (0, 2, 4)))
    image = Image.new("RGB", (len(colours), 1))
    image.putdata(colours)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _error_text(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:300]
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            code, message = error.get("code"), error.get("message")
            return f"{code}: {message}" if code else str(message)
        if "detail" in body:
            return str(body["detail"])[:300]
    return str(body)[:300]


class RetroDiffusionBackend:
    """Retro Diffusion over HTTP; see the module docstring for the policy."""

    name = "retro_diffusion"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str = BASE_URL,
        max_usd_per_call: float | None = None,
        poll_interval_s: float = POLL_INTERVAL_S,
        task_timeout_s: float = TASK_TIMEOUT_S,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._max_usd_per_call = max_usd_per_call
        self._poll_interval_s = poll_interval_s
        self._task_timeout_s = task_timeout_s
        self._transport = transport

    # -- configuration ----------------------------------------------------

    def configured(self) -> bool:
        return bool(self._api_key or os.environ.get(API_KEY_ENVIRONMENT_VARIABLE))

    def _token(self) -> str:
        token = self._api_key or os.environ.get(API_KEY_ENVIRONMENT_VARIABLE)
        if not token:
            raise BackendError(f"{API_KEY_ENVIRONMENT_VARIABLE} is not set")
        return token

    def _budget_usd(self) -> float:
        if self._max_usd_per_call is not None:
            return self._max_usd_per_call
        return float(
            os.environ.get(MAX_USD_ENVIRONMENT_VARIABLE, DEFAULT_MAX_USD_PER_CALL)
        )

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url,
            timeout=REQUEST_TIMEOUT_S,
            transport=self._transport,
        )

    # -- payloads ---------------------------------------------------------

    @staticmethod
    def image_payload(req: ImageRequest) -> dict[str, Any]:
        style = req.style or STYLE_SPRITE
        _check_size(style, req.width_px, req.height_px)
        payload: dict[str, Any] = {
            "prompt": req.prompt,
            "prompt_style": style,
            "width": req.width_px,
            "height": req.height_px,
            "num_images": 1,
            "tile_x": req.seamless_x,
            "tile_y": req.seamless_y,
            "remove_bg": req.transparent,
            "upscale_output_factor": 1,
        }
        if req.seed is not None:
            payload["seed"] = req.seed
        if req.palette_hex:
            payload["input_palette"] = palette_png_base64(req.palette_hex)
        return payload

    @staticmethod
    def tileset_payload(req: TilesetRequest) -> dict[str, Any]:
        style = STYLE_TILESET_ADVANCED if req.outside_prompt else STYLE_TILESET
        _check_size(style, req.tile_size_px, req.tile_size_px)
        payload: dict[str, Any] = {
            "prompt": req.inside_prompt,
            "prompt_style": style,
            "width": req.tile_size_px,
            "height": req.tile_size_px,
            "num_images": 1,
        }
        if req.outside_prompt:
            payload["extra_prompt"] = req.outside_prompt
        if req.seed is not None:
            payload["seed"] = req.seed
        if req.palette_hex:
            payload["input_palette"] = palette_png_base64(req.palette_hex)
        return payload

    # -- HTTP -------------------------------------------------------------

    async def _submit(
        self, client: httpx.AsyncClient, payload: dict[str, Any], idempotency_key: str | None
    ) -> dict[str, Any]:
        headers = {"X-RD-Token": self._token()}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
            try:
                response = await client.post("/inferences", json=payload, headers=headers)
            except httpx.TransportError as error:
                paid = " The request may have been accepted and charged; it was not resent." if idempotency_key else ""
                raise BackendError(f"Retro Diffusion unreachable ({describe_error(error)}).{paid}") from error
            if response.status_code == 429 and attempt < MAX_RATE_LIMIT_RETRIES:
                try:
                    wait_s = float(response.headers.get("Retry-After", DEFAULT_RETRY_AFTER_S))
                except ValueError:
                    wait_s = DEFAULT_RETRY_AFTER_S
                await asyncio.sleep(wait_s)
                continue
            if response.status_code >= 400:
                raise BackendError(
                    f"Retro Diffusion HTTP {response.status_code}: {_error_text(response)}"
                )
            body = response.json()
            if not isinstance(body, dict):
                raise BackendError(f"Retro Diffusion returned {type(body).__name__}, expected an object")
            return body
        raise AssertionError("unreachable")

    async def _poll(self, client: httpx.AsyncClient, task_id: str) -> dict[str, Any]:
        headers = {"X-RD-Token": self._token()}
        deadline = time.monotonic() + self._task_timeout_s
        while True:
            try:
                response = await client.get(f"/inferences/tasks/{task_id}", headers=headers)
            except httpx.TransportError as error:
                raise BackendError(
                    f"Retro Diffusion unreachable while polling task {task_id} ({describe_error(error)}); "
                    f"the task may still complete and be charged"
                ) from error
            if response.status_code >= 400:
                raise BackendError(
                    f"Retro Diffusion HTTP {response.status_code} polling task {task_id}: "
                    f"{_error_text(response)}"
                )
            task = response.json()
            status = task.get("status")
            if status == "succeeded":
                result = task.get("result")
                if not isinstance(result, dict):
                    raise BackendError(f"Retro Diffusion task {task_id} succeeded without a result")
                return result
            if status == "failed":
                error = task.get("error")
                text = (
                    f"{error.get('code')}: {error.get('message')}"
                    if isinstance(error, dict)
                    else "no error detail"
                )
                raise BackendError(f"Retro Diffusion task {task_id} failed: {text}")
            if status not in _PENDING_STATUSES:
                raise BackendError(f"Retro Diffusion task {task_id} has status {status!r}")
            if time.monotonic() >= deadline:
                raise BackendError(
                    f"Retro Diffusion task {task_id} still {status} after {self._task_timeout_s:.0f} s; "
                    f"it may still complete and be charged"
                )
            await asyncio.sleep(self._poll_interval_s)

    async def _cost(self, client: httpx.AsyncClient, payload: dict[str, Any]) -> float:
        body = await self._submit(client, {**payload, "check_cost": True}, None)
        if "task_id" in body:
            body = await self._poll(client, str(body["task_id"]))
        if "balance_cost" in body:
            return float(body["balance_cost"])
        raise BackendError(f"cost check returned neither balance_cost nor task_id: {sorted(body)}")

    async def _run(self, payload: dict[str, Any], seed: int | None) -> RawImage:
        async with self._client() as client:
            cost_usd = await self._cost(client, payload)
            budget_usd = self._budget_usd()
            if cost_usd > budget_usd:
                raise BudgetExceeded(
                    f"estimated ${cost_usd:.3f} exceeds the ${budget_usd:.2f} per-call cap "
                    f"({MAX_USD_ENVIRONMENT_VARIABLE}); nothing was generated"
                )
            accepted = await self._submit(client, payload, str(uuid.uuid4()))
            task_id: str | None = None
            if accepted.get("status") == "accepted" or "task_id" in accepted:
                task_id = str(accepted["task_id"])
                result = await self._poll(client, task_id)
            else:
                result = accepted
            images = result.get("base64_images")
            if images:
                png = base64.b64decode(images[0])
            else:
                urls = result.get("output_urls")
                if not urls:
                    raise BackendError("Retro Diffusion result has no base64_images or output_urls")
                try:
                    download = await client.get(urls[0])
                except httpx.TransportError as error:
                    raise BackendError(
                        f"downloading the finished image failed ({describe_error(error)}); "
                        f"the task {task_id} was charged"
                    ) from error
                if download.status_code >= 400:
                    raise BackendError(f"downloading the result failed: HTTP {download.status_code}")
                png = download.content
            charged = float(result.get("balance_cost", cost_usd))
        params = {key: value for key, value in payload.items() if key != "input_palette"}
        if "input_palette" in payload:
            params["input_palette"] = "<1xN png>"
        return RawImage(
            png=png,
            provider=self.name,
            provider_job_id=task_id,
            cost_usd=charged,
            seed=seed,
            params=params,
        )

    # -- PixelBackend -----------------------------------------------------

    async def estimate_usd(self, req: ImageRequest | TilesetRequest) -> float:
        payload = (
            self.tileset_payload(req)
            if isinstance(req, TilesetRequest)
            else self.image_payload(req)
        )
        async with self._client() as client:
            return await self._cost(client, payload)

    async def generate_image(self, req: ImageRequest) -> RawImage:
        return await self._run(self.image_payload(req), req.seed)

    async def generate_tileset(self, req: TilesetRequest) -> RawImage:
        return await self._run(self.tileset_payload(req), req.seed)

    async def balance_usd(self) -> float:
        async with self._client() as client:
            try:
                response = await client.get(
                    "/inferences/credits", headers={"X-RD-Token": self._token()}
                )
            except httpx.TransportError as error:
                raise BackendError(f"Retro Diffusion unreachable ({describe_error(error)})") from error
            if response.status_code >= 400:
                raise BackendError(
                    f"Retro Diffusion HTTP {response.status_code}: {_error_text(response)}"
                )
            try:
                return float(response.json()["balance"])
            except (ValueError, KeyError, TypeError) as error:
                raise BackendError(
                    f"credits response has no numeric 'balance': {response.text[:200]}"
                ) from error
