"""Local SDXL pixel-art service for Spyrite Tile.

Stable Diffusion XL (DOI 10.48550/arXiv.2307.01952) with two LoRAs:
`nerijs/pixel-art-xl`, which draws on an 8x8-pixel grid, and the
LCM-LoRA for SDXL (DOI 10.48550/arXiv.2311.05556), which makes 8 sampling
steps enough. The settings are the pixel-art-xl model card's: LCM scheduler,
8 steps, guidance 1.5, LoRA weights 1.0 (LCM) and 1.2 (pixel art), the
fp16-fix VAE, negative prompt "3d render, realistic".

The service shares a 24 GB GPU with other jobs through `gpu-tenant`: it
claims the lease before a generation, renews it while it is used, and gives
the GPU back (dropping the model) after 300 s idle. A generation that cannot
get the lease fails with HTTP 503 `gpu_busy`, so the caller learns the GPU is
taken rather than waiting on a queue it cannot see.

Endpoints: `GET /health`, `POST /v1/generate`.
"""

from __future__ import annotations

import asyncio
import base64
import ctypes
import gc
import io
import math
import random
import subprocess
import time
from contextlib import asynccontextmanager, suppress
from typing import Any

from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

BASE_MODEL = "stabilityai/stable-diffusion-xl-base-1.0"
VAE_MODEL = "madebyollin/sdxl-vae-fp16-fix"
LCM_LORA = "latent-consistency/lcm-lora-sdxl"
PIXEL_LORA = "nerijs/pixel-art-xl"
PIXEL_LORA_FILE = "pixel-art-xl.safetensors"
LCM_WEIGHT = 1.0
PIXEL_WEIGHT = 1.2
STEPS = 8
GUIDANCE_SCALE = 1.5
NEGATIVE_PROMPT = "3d render, realistic"

SQUARE_SIDE_PX = 1024
SIDE_MULTIPLE_PX = 64

GPU_WHO = "spyrite-tile@big"
GPU_FOCUS = "3d"
LEASE_TTL = "15m"
LEASE_NOTE = "spyrite pixel gen"
IDLE_UNLOAD_S = 300.0
IDLE_CHECK_S = 30.0


class GpuBusy(Exception):
    """The lease could not be taken; carries `gpu-tenant status` output."""


class GenerateRequest(BaseModel):
    prompt: str = Field(min_length=1)
    width_px: int = Field(ge=8, le=2048)
    height_px: int = Field(ge=8, le=2048)
    seed: int | None = None
    seamless: bool = False


def generation_size(width_px: int, height_px: int) -> tuple[int, int]:
    """Square targets render at 1024 x 1024; others keep their aspect at about 1024^2 px."""
    if width_px == height_px:
        return SQUARE_SIDE_PX, SQUARE_SIDE_PX
    scale = math.sqrt(SQUARE_SIDE_PX * SQUARE_SIDE_PX / (width_px * height_px))

    def side(value: int) -> int:
        return max(SIDE_MULTIPLE_PX, round(value * scale / SIDE_MULTIPLE_PX) * SIDE_MULTIPLE_PX)

    return side(width_px), side(height_px)


# -- GPU lease -----------------------------------------------------------


def _tenant(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["gpu-tenant", *args], capture_output=True, text=True, check=False)


def lease_held() -> bool:
    return _tenant("check", "--who", GPU_WHO).returncode == 0


def ensure_lease() -> None:
    if lease_held():
        _tenant("renew", "--who", GPU_WHO, "--ttl", LEASE_TTL)
        return
    claim = _tenant("claim", GPU_FOCUS, "--who", GPU_WHO, "--ttl", LEASE_TTL, "--note", LEASE_NOTE)
    if claim.returncode != 0:
        raise GpuBusy(_tenant("status").stdout.strip() or claim.stdout.strip() or claim.stderr.strip())


def release_lease() -> None:
    if lease_held():
        _tenant("release", "--who", GPU_WHO)


# -- model ---------------------------------------------------------------


class Model:
    """The SDXL pipeline, loaded on first use and dropped when idle."""

    def __init__(self) -> None:
        self.pipe: Any = None
        self.leased = False  # this process took the lease and has not released it
        self.last_used = time.monotonic()
        self.lock = asyncio.Lock()

    def load(self) -> Any:
        if self.pipe is not None:
            return self.pipe
        import torch  # imported here: the module must import on a host without a GPU stack
        from diffusers import AutoencoderKL, LCMScheduler, StableDiffusionXLPipeline

        try:
            vae = AutoencoderKL.from_pretrained(VAE_MODEL, torch_dtype=torch.float16)
            pipe = StableDiffusionXLPipeline.from_pretrained(
                BASE_MODEL, vae=vae, torch_dtype=torch.float16
            ).to("cuda")
            pipe.scheduler = LCMScheduler.from_config(pipe.scheduler.config)
            pipe.load_lora_weights(LCM_LORA, adapter_name="lcm")
            pipe.load_lora_weights(PIXEL_LORA, weight_name=PIXEL_LORA_FILE, adapter_name="pixel")
            pipe.set_adapters(["lcm", "pixel"], adapter_weights=[LCM_WEIGHT, PIXEL_WEIGHT])
        except BaseException:
            # A half-loaded pipeline would hold VRAM until the idle timer; free it now.
            vae = pipe = None
            self.unload()
            raise
        self.pipe = pipe
        return pipe

    def unload(self) -> None:
        """Drop the pipeline and hand VRAM and (as far as glibc allows) host RAM back."""
        self.pipe = None
        gc.collect()
        import torch

        torch.cuda.empty_cache()
        with suppress(OSError, AttributeError):  # not glibc: nothing to trim
            ctypes.CDLL("libc.so.6").malloc_trim(0)

    def generate(self, req: GenerateRequest) -> tuple[bytes, int]:
        import torch

        pipe = self.load()
        seed = req.seed if req.seed is not None else random.randrange(2**31)
        width, height = generation_size(req.width_px, req.height_px)
        generator = torch.Generator("cuda").manual_seed(seed)
        convolutions = (
            [m for m in (*pipe.unet.modules(), *pipe.vae.modules()) if isinstance(m, torch.nn.Conv2d)]
            if req.seamless
            else []
        )
        previous = [m.padding_mode for m in convolutions]
        for m in convolutions:
            m.padding_mode = "circular"
        try:
            image = pipe(
                prompt=req.prompt,
                negative_prompt=NEGATIVE_PROMPT,
                width=width,
                height=height,
                num_inference_steps=STEPS,
                guidance_scale=GUIDANCE_SCALE,
                generator=generator,
            ).images[0]
        finally:
            for m, mode in zip(convolutions, previous):
                m.padding_mode = mode
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue(), seed


model = Model()


def _release_gpu() -> None:
    if model.pipe is not None:
        model.unload()
    if model.leased:
        release_lease()
        model.leased = False


async def _idle_watcher() -> None:
    while True:
        await asyncio.sleep(IDLE_CHECK_S)
        idle_s = time.monotonic() - model.last_used
        if model.leased and idle_s >= IDLE_UNLOAD_S and not model.lock.locked():
            async with model.lock:
                await run_in_threadpool(_release_gpu)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await run_in_threadpool(release_lease)  # a lease left by a previous process would block the GPU until its TTL
    watcher = asyncio.create_task(_idle_watcher())
    try:
        yield
    finally:
        watcher.cancel()
        with suppress(asyncio.CancelledError):
            await watcher
        await run_in_threadpool(_release_gpu)


app = FastAPI(title="spyrite-pixel-server", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "model_loaded": model.pipe is not None,
        "holds_gpu": await run_in_threadpool(lease_held),
    }


@app.post("/v1/generate")
async def generate(req: GenerateRequest):
    async with model.lock:
        try:
            await run_in_threadpool(ensure_lease)
        except GpuBusy as busy:
            return JSONResponse(status_code=503, content={"error": "gpu_busy", "detail": str(busy)})
        model.leased = True
        model.last_used = time.monotonic()
        try:
            png, seed = await run_in_threadpool(model.generate, req)
        finally:
            model.last_used = time.monotonic()  # the idle timer runs from the end of the last generation
    return {"images_b64": [base64.b64encode(png).decode("ascii")], "seed": seed}
