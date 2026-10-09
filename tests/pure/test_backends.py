"""Backend policy: budget cap, idempotent submit, no fallback, capability errors.

The HTTP layer is replaced by httpx.MockTransport; nothing here touches a network.
"""

import asyncio
import base64
import io
import json

import httpx
import pytest
from PIL import Image

from spyrite_tile_gen import server, store
from spyrite_tile_gen.backends.base import (
    BackendBusy,
    BackendError,
    BudgetExceeded,
    CapabilityError,
    ImageRequest,
    RawImage,
    TilesetRequest,
)
from spyrite_tile_gen.backends.local_sdxl import LocalSdxlBackend
from spyrite_tile_gen.backends.retro_diffusion import RetroDiffusionBackend


def _png_b64(size: int = 16, colour=(10, 20, 30, 255)) -> str:
    buffer = io.BytesIO()
    Image.new("RGBA", (size, size), colour).save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode()


def _rd(handler, **kwargs) -> RetroDiffusionBackend:
    return RetroDiffusionBackend(
        api_key="rdpk-test",
        poll_interval_s=0.0,
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


class _Rd:
    """A scripted Retro Diffusion: records requests, answers cost / submit / poll."""

    def __init__(self, cost=0.02, polls=("pending", "succeeded"), submit_statuses=(200,)):
        self.cost = cost
        self.polls = list(polls)
        self.submit_statuses = list(submit_statuses)
        self.posts: list[tuple[dict, dict]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            body = json.loads(request.content)
            self.posts.append((body, dict(request.headers)))
            if body.get("check_cost"):
                return httpx.Response(200, json={"balance_cost": self.cost})
            status = self.submit_statuses.pop(0) if self.submit_statuses else 200
            if status == 429:
                return httpx.Response(429, headers={"Retry-After": "0"}, json={"error": {"code": "rate_limited", "message": "slow down"}})
            return httpx.Response(200, json={"status": "accepted", "task_id": "task-1"})
        state = self.polls.pop(0)
        if state == "succeeded":
            return httpx.Response(
                200,
                json={"status": "succeeded", "task_id": "task-1",
                      "result": {"base64_images": [_png_b64()], "balance_cost": self.cost}},
            )
        if state == "failed":
            return httpx.Response(
                200,
                json={"status": "failed", "task_id": "task-1",
                      "error": {"code": "inference_failed", "message": "model crashed"}},
            )
        return httpx.Response(200, json={"status": state, "task_id": "task-1"})


def _tile_request(**kwargs) -> ImageRequest:
    defaults = dict(prompt="mossy cobblestone", width_px=16, height_px=16, seamless_x=True,
                    seamless_y=True, style="rd_tile__single_tile")
    return ImageRequest(**{**defaults, **kwargs})


def test_a_call_priced_over_the_cap_is_refused_before_anything_is_generated():
    rd = _Rd(cost=0.90)
    backend = _rd(rd, max_usd_per_call=0.50)
    with pytest.raises(BudgetExceeded, match="0.900.*0.50"):
        asyncio.run(backend.generate_image(_tile_request()))
    assert [body.get("check_cost") for body, _ in rd.posts] == [True]


def test_the_cap_comes_from_the_environment_when_not_given(monkeypatch):
    monkeypatch.setenv("SPYRITE_MAX_USD_PER_CALL", "0.01")
    backend = _rd(_Rd(cost=0.02))
    with pytest.raises(BudgetExceeded):
        asyncio.run(backend.generate_image(_tile_request()))


def test_a_priced_call_generates_once_with_an_idempotency_key_and_returns_the_image():
    rd = _Rd(cost=0.02)
    raw = asyncio.run(_rd(rd).generate_image(_tile_request(seed=7)))
    assert raw.provider == "retro_diffusion" and raw.provider_job_id == "task-1"
    assert raw.cost_usd == 0.02 and raw.seed == 7
    assert Image.open(io.BytesIO(raw.png)).size == (16, 16)
    cost_body, cost_headers = rd.posts[0]
    run_body, run_headers = rd.posts[1]
    assert "idempotency-key" not in cost_headers and "idempotency-key" in run_headers
    assert "check_cost" not in run_body
    assert {k: run_body[k] for k in ("prompt_style", "width", "height", "tile_x", "tile_y", "seed")} == {
        "prompt_style": "rd_tile__single_tile", "width": 16, "height": 16,
        "tile_x": True, "tile_y": True, "seed": 7,
    }
    assert {k: v for k, v in cost_body.items() if k != "check_cost"} == run_body


def test_a_rate_limited_submit_is_retried_with_the_same_idempotency_key():
    rd = _Rd(submit_statuses=(429, 429, 200), polls=("succeeded",))
    asyncio.run(_rd(rd).generate_image(_tile_request()))
    run_keys = [h["idempotency-key"] for body, h in rd.posts if not body.get("check_cost")]
    assert len(run_keys) == 3 and len(set(run_keys)) == 1


def test_a_failed_task_is_reported_and_never_resubmitted():
    rd = _Rd(polls=("running", "failed"))
    with pytest.raises(BackendError, match="inference_failed.*model crashed"):
        asyncio.run(_rd(rd).generate_image(_tile_request()))
    assert sum(1 for body, _ in rd.posts if not body.get("check_cost")) == 1


def test_a_missing_key_fails_before_any_request(monkeypatch):
    monkeypatch.delenv("RD_API_KEY", raising=False)
    calls = []
    backend = RetroDiffusionBackend(transport=httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(500)))
    with pytest.raises(BackendError, match="RD_API_KEY is not set"):
        asyncio.run(backend.generate_image(_tile_request()))
    assert calls == []


def test_a_size_outside_the_styles_range_names_the_range():
    rd = _Rd()
    with pytest.raises(ValueError, match="16-64"):
        asyncio.run(_rd(rd).generate_image(_tile_request(width_px=8, height_px=8)))
    with pytest.raises(ValueError, match="16-32"):
        asyncio.run(_rd(rd).generate_tileset(TilesetRequest(inside_prompt="stone", tile_size_px=48)))
    assert rd.posts == []


def test_a_tileset_with_an_outside_prompt_uses_the_advanced_style():
    rd = _Rd(polls=("succeeded",))
    asyncio.run(_rd(rd).generate_tileset(
        TilesetRequest(inside_prompt="grey stones", outside_prompt="lush grass", tile_size_px=16)))
    body = rd.posts[1][0]
    assert body["prompt_style"] == "rd_tile__tileset_advanced"
    assert body["extra_prompt"] == "lush grass" and body["width"] == 16


def _local(handler) -> LocalSdxlBackend:
    return LocalSdxlBackend(base_url="http://local.test", transport=httpx.MockTransport(handler))


def test_local_refuses_one_axis_seamless_transparency_styles_and_tilesets():
    backend = _local(lambda r: httpx.Response(500))
    for kwargs in ({"seamless_x": True}, {"transparent": True}, {"style": "x"}):
        with pytest.raises(CapabilityError):
            asyncio.run(backend.generate_image(ImageRequest("p", 16, 16, **kwargs)))
    with pytest.raises(CapabilityError, match="retro_diffusion"):
        asyncio.run(backend.generate_tileset(TilesetRequest("stone")))


def test_local_busy_gpu_surfaces_the_servers_detail_and_does_not_fall_back():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(503, json={"error": "gpu_busy", "detail": "held by hunyuan3d@big"})

    with pytest.raises(BackendBusy, match="hunyuan3d@big"):
        asyncio.run(_local(handler).generate_image(ImageRequest("p", 16, 16)))
    assert seen == ["http://local.test/v1/generate"]


def test_local_generate_posts_the_request_and_decodes_the_image():
    captured = {}

    def handler(request):
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"images_b64": [_png_b64(64)], "seed": 99})

    raw = asyncio.run(_local(handler).generate_image(
        ImageRequest("p", 16, 16, seed=5, seamless_x=True, seamless_y=True)))
    assert captured == {"prompt": "p", "width_px": 16, "height_px": 16, "seed": 5, "seamless": True}
    assert raw.cost_usd == 0.0 and raw.seed == 99
    assert Image.open(io.BytesIO(raw.png)).size == (64, 64)


class _FakeBackend:
    name = "fake"

    def __init__(self):
        self.requests = []

    async def estimate_usd(self, req):
        return 0.0

    async def generate_image(self, req):
        self.requests.append(req)
        buffer = io.BytesIO()
        Image.new("RGBA", (64, 64), (200, 40, 40, 255)).save(buffer, format="PNG")
        return RawImage(buffer.getvalue(), "fake", "job-1", 0.0, 3, {"echo": True})


def test_generate_tile_saves_a_normalised_png_with_a_sidecar(monkeypatch, tmp_path):
    monkeypatch.setenv("SPYRITE_OUTPUT_DIR", str(tmp_path))
    backend = _FakeBackend()
    monkeypatch.setitem(server.BACKENDS, "local", lambda: backend)

    content = asyncio.run(server.generate_tile("red brick", size_px=16, backend="local"))

    info = json.loads(content[0])
    assert info["size_px"] == [16, 16] and info["seed"] == 3
    saved = tmp_path / info["path"].rsplit("/", 1)[1]
    assert Image.open(saved).size == (16, 16)
    sidecar = json.loads(saved.with_suffix(".json").read_text())
    assert sidecar["provider"] == "fake" and sidecar["request"]["seamless_x"] is True
    assert backend.requests[0].style is None  # RD's default style is not sent to the local backend



# -- Retro Diffusion wire format ---------------------------------------------

# Property names of `ExternalInferenceInputNeo` in Retro Diffusion's v2 OpenAPI
# document: a field outside this set would be rejected or silently ignored.
RD_INPUT_FIELDS = {
    "async", "async_process", "bypass_prompt_expansion", "check_cost", "extra_input_image",
    "extra_prompt", "extra_strength", "frames_duration", "height", "include_downloadable_data",
    "include_extra_data", "input_image", "input_palette", "model", "negative", "num_images",
    "output_bg_color", "prompt", "prompt_style", "reference_images", "remove_bg",
    "return_non_bg_removed", "return_pre_palette", "return_spritesheet", "seed", "strength",
    "tile_x", "tile_y", "upload_outputs", "upscale_output_factor", "width",
}
RD_REQUIRED_FIELDS = {"width", "height", "num_images"}


def test_every_payload_uses_only_fields_of_the_rd_input_schema_and_all_required_ones():
    palette = ("#102030", "#ff0000")
    payloads = [
        RetroDiffusionBackend.image_payload(_tile_request(seed=3, palette_hex=palette, transparent=True)),
        RetroDiffusionBackend.image_payload(ImageRequest("hero", 64, 96)),
        RetroDiffusionBackend.tileset_payload(TilesetRequest("stone", "grass", 16, 3, palette)),
        RetroDiffusionBackend.tileset_payload(TilesetRequest("stone")),
    ]
    for payload in payloads:
        assert set(payload) <= RD_INPUT_FIELDS, set(payload) - RD_INPUT_FIELDS
        assert RD_REQUIRED_FIELDS <= set(payload)
        assert payload["num_images"] == 1


def test_the_default_styles_are_tile_sprite_and_tileset():
    assert RetroDiffusionBackend.image_payload(ImageRequest("hero", 64, 64))["prompt_style"] == "rd_plus__default"
    assert RetroDiffusionBackend.tileset_payload(TilesetRequest("stone"))["prompt_style"] == "rd_tile__tileset"
    assert "extra_prompt" not in RetroDiffusionBackend.tileset_payload(TilesetRequest("stone"))


def test_transparency_and_tiling_map_to_remove_bg_tile_x_and_tile_y():
    payload = RetroDiffusionBackend.image_payload(
        ImageRequest("hero", 64, 64, seamless_x=True, seamless_y=False, transparent=True))
    assert (payload["remove_bg"], payload["tile_x"], payload["tile_y"]) == (True, True, False)


def test_the_palette_is_sent_as_a_one_row_png_of_the_colours_in_order():
    payload = RetroDiffusionBackend.image_payload(
        _tile_request(palette_hex=("#102030", "#ff0000", "#00ff00")))
    swatch = Image.open(io.BytesIO(base64.b64decode(payload["input_palette"]))).convert("RGB")
    assert swatch.size == (3, 1)
    assert [swatch.getpixel((x, 0)) for x in range(3)] == [(16, 32, 48), (255, 0, 0), (0, 255, 0)]


def test_each_generation_gets_its_own_idempotency_key():
    rd = _Rd(polls=("succeeded", "succeeded"))
    backend = _rd(rd)
    asyncio.run(backend.generate_image(_tile_request()))
    asyncio.run(backend.generate_image(_tile_request()))
    keys = [h["idempotency-key"] for body, h in rd.posts if not body.get("check_cost")]
    assert len(keys) == 2 and keys[0] != keys[1]


def test_the_token_header_is_sent_on_every_request():
    seen = []

    def handler(request):
        seen.append(request.headers.get("x-rd-token"))
        if request.method == "POST":
            body = json.loads(request.content)
            if body.get("check_cost"):
                return httpx.Response(200, json={"balance_cost": 0.01})
            return httpx.Response(200, json={"status": "accepted", "task_id": "t"})
        return httpx.Response(200, json={"status": "succeeded", "task_id": "t",
                                         "result": {"base64_images": [_png_b64()], "balance_cost": 0.01}})

    asyncio.run(_rd(handler).generate_image(_tile_request()))
    assert seen == ["rdpk-test"] * 3


def test_a_cost_check_that_is_itself_an_async_task_is_polled_for_its_price():
    posts, gets = [], []

    def handler(request):
        if request.method == "POST":
            body = json.loads(request.content)
            posts.append(body)
            if body.get("check_cost"):
                return httpx.Response(200, json={"status": "accepted", "task_id": "cost-task"})
            return httpx.Response(200, json={"status": "accepted", "task_id": "run-task"})
        gets.append(request.url.path)
        if request.url.path.endswith("cost-task"):
            return httpx.Response(200, json={"status": "succeeded", "task_id": "cost-task",
                                             "result": {"balance_cost": 0.04}})
        return httpx.Response(200, json={"status": "succeeded", "task_id": "run-task",
                                         "result": {"base64_images": [_png_b64()], "balance_cost": 0.04}})

    assert asyncio.run(_rd(handler).estimate_usd(_tile_request())) == 0.04
    raw = asyncio.run(_rd(handler).generate_image(_tile_request()))
    assert raw.provider_job_id == "run-task" and raw.cost_usd == 0.04
    assert sum(1 for b in posts if not b.get("check_cost")) == 1


def test_estimate_usd_never_submits_a_paid_request():
    rd = _Rd(cost=0.07)
    assert asyncio.run(_rd(rd).estimate_usd(_tile_request())) == 0.07
    assert [body.get("check_cost") for body, _ in rd.posts] == [True]


def test_a_result_delivered_as_a_url_is_downloaded():
    downloaded = []

    def handler(request):
        if request.url.host == "cdn.test":
            downloaded.append(str(request.url))
            buffer = io.BytesIO()
            Image.new("RGBA", (16, 16), (1, 2, 3, 255)).save(buffer, format="PNG")
            return httpx.Response(200, content=buffer.getvalue())
        if request.method == "POST":
            if json.loads(request.content).get("check_cost"):
                return httpx.Response(200, json={"balance_cost": 0.01})
            return httpx.Response(200, json={"status": "accepted", "task_id": "t"})
        return httpx.Response(200, json={"status": "succeeded", "task_id": "t", "result": {
            "base64_images": [], "output_urls": ["https://cdn.test/out.png"], "balance_cost": 0.01}})

    raw = asyncio.run(_rd(handler).generate_image(_tile_request()))
    assert downloaded == ["https://cdn.test/out.png"]
    assert Image.open(io.BytesIO(raw.png)).getpixel((0, 0)) == (1, 2, 3, 255)


def test_rate_limiting_gives_up_after_three_retries():
    rd = _Rd(submit_statuses=(429, 429, 429, 429, 200), polls=("succeeded",))
    with pytest.raises(BackendError, match="HTTP 429.*rate_limited"):
        asyncio.run(_rd(rd).generate_image(_tile_request()))
    assert sum(1 for body, _ in rd.posts if not body.get("check_cost")) == 4  # 1 try + 3 retries


def test_the_retry_after_header_sets_the_wait(monkeypatch):
    waits = []

    async def fake_sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr("spyrite_tile_gen.backends.retro_diffusion.asyncio.sleep", fake_sleep)

    rd_after = _Rd(polls=("succeeded",))

    def routed(request):
        if request.method == "POST" and not json.loads(request.content).get("check_cost") and not waits:
            return httpx.Response(429, headers={"Retry-After": "7"}, json={})
        return rd_after(request)

    asyncio.run(_rd(routed).generate_image(_tile_request()))
    assert waits == [7.0]


def test_a_task_that_never_finishes_times_out_without_a_second_submit():
    rd = _Rd(polls=["running"] * 1000)
    backend = RetroDiffusionBackend(api_key="k", poll_interval_s=0.0, task_timeout_s=0.05,
                                    transport=httpx.MockTransport(rd))
    with pytest.raises(BackendError, match="may still complete"):
        asyncio.run(backend.generate_image(_tile_request()))
    assert sum(1 for body, _ in rd.posts if not body.get("check_cost")) == 1


def test_a_connection_failure_on_the_paid_call_is_reported_not_resent():
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body.get("check_cost", False))
        if body.get("check_cost"):
            return httpx.Response(200, json={"balance_cost": 0.01})
        raise httpx.ReadTimeout("slow")

    with pytest.raises(BackendError, match="ReadTimeout.*may have been accepted"):
        asyncio.run(_rd(handler).generate_image(_tile_request()))
    assert calls == [True, False]


def test_an_api_error_body_is_reported_with_its_code():
    def handler(request):
        return httpx.Response(402, json={"error": {"code": "not_enough_balance", "message": "top up"}})

    with pytest.raises(BackendError, match="HTTP 402: not_enough_balance: top up"):
        asyncio.run(_rd(handler).estimate_usd(_tile_request()))


def test_balance_reads_the_credits_endpoint():
    seen = []

    def handler(request):
        seen.append(request.url.path)
        return httpx.Response(200, json={"balance": 4.25})

    assert asyncio.run(_rd(handler).balance_usd()) == 4.25
    assert seen == ["/v2/inferences/credits"]
    with pytest.raises(BackendError, match="no numeric 'balance'"):
        asyncio.run(_rd(lambda r: httpx.Response(200, json={"credits": 3})).balance_usd())


# -- Local backend failures ---------------------------------------------------


def test_local_service_down_is_a_backend_error_naming_the_url():
    def handler(request):
        raise httpx.ConnectTimeout("")

    with pytest.raises(BackendError, match=r"http://local\.test is unreachable \(ConnectTimeout\)"):
        asyncio.run(_local(handler).generate_image(ImageRequest("p", 16, 16)))


def test_local_server_errors_and_malformed_replies_are_backend_errors_not_crashes():
    with pytest.raises(BackendError, match="HTTP 500: boom"):
        asyncio.run(_local(lambda r: httpx.Response(500, text="boom")).generate_image(ImageRequest("p", 16, 16)))
    with pytest.raises(BackendError, match="no image"):
        asyncio.run(_local(lambda r: httpx.Response(200, json={"images_b64": []})).generate_image(
            ImageRequest("p", 16, 16)))
    # A 503 that is not gpu_busy is a plain error, not BackendBusy.
    with pytest.raises(BackendError) as caught:
        asyncio.run(_local(lambda r: httpx.Response(503, text="down")).generate_image(ImageRequest("p", 16, 16)))
    assert not isinstance(caught.value, BackendBusy)


# -- Server tools --------------------------------------------------------------


def _call(name: str, arguments: dict):
    """Run a tool through FastMCP, the same path a client request takes."""
    return asyncio.run(server.mcp.call_tool(name, arguments))


def test_image_tools_return_a_json_text_block_and_a_png_image_block(tmp_path, monkeypatch):
    monkeypatch.setenv("SPYRITE_OUTPUT_DIR", str(tmp_path))
    paths = []
    for index in range(4):
        path = tmp_path / f"in{index}.png"
        Image.new("RGBA", (16, 16), (index * 60, 0, 0, 255)).save(path)
        paths.append(str(path))

    content = _call("compose_atlas", {"paths": paths, "tile_size_px": 16, "columns": 2,
                                      "output_name": "atlas"})

    assert [block.type for block in content] == ["text", "image"]
    info = json.loads(content[0].text)
    assert info["tile_xy"] == [[0, 0], [1, 0], [0, 1], [1, 1]] and info["size_px"] == [32, 32]
    assert (tmp_path / "atlas.png").is_file() and info["path"] == str((tmp_path / "atlas.png").resolve())
    preview = Image.open(io.BytesIO(base64.b64decode(content[1].data)))
    assert content[1].mimeType == "image/png" and preview.size == (512, 512)  # nearest upscale, 32 px -> 512


def test_compose_atlas_refuses_relative_paths_and_unsafe_output_names(tmp_path, monkeypatch):
    monkeypatch.setenv("SPYRITE_OUTPUT_DIR", str(tmp_path))
    with pytest.raises(Exception, match="absolute"):
        _call("compose_atlas", {"paths": ["a.png"], "tile_size_px": 16, "columns": 1, "output_name": "x"})
    good = tmp_path / "g.png"
    Image.new("RGBA", (16, 16)).save(good)
    with pytest.raises(Exception, match="output_name"):
        _call("compose_atlas", {"paths": [str(good)], "tile_size_px": 16, "columns": 1,
                                "output_name": "../escape"})


def test_normalize_image_writes_a_new_file_and_leaves_the_source_alone(tmp_path, monkeypatch):
    monkeypatch.setenv("SPYRITE_OUTPUT_DIR", str(tmp_path / "out"))
    source = tmp_path / "big.png"
    Image.new("RGBA", (128, 128), (200, 100, 50, 255)).save(source)
    before = source.read_bytes()

    content = _call("normalize_image", {"path": str(source), "width_px": 16, "height_px": 16})

    info = json.loads(content[0].text)
    assert info["size_px"] == [16, 16] and info["path"] != str(source)
    assert Image.open(info["path"]).size == (16, 16)
    assert source.read_bytes() == before
    with pytest.raises(Exception, match="absolute path to an existing file"):
        _call("normalize_image", {"path": "nope.png", "width_px": 16, "height_px": 16})


class _TilesetBackend(_FakeBackend):
    async def generate_tileset(self, req):
        self.requests.append(req)
        buffer = io.BytesIO()
        Image.new("RGBA", (64, 48), (10, 160, 10, 255)).save(buffer, format="PNG")
        return RawImage(buffer.getvalue(), "fake", "job-2", 0.06, 1, {})


def test_generate_tileset_reports_columns_and_rows_and_uses_only_retro_diffusion(monkeypatch, tmp_path):
    monkeypatch.setenv("SPYRITE_OUTPUT_DIR", str(tmp_path))
    backend = _TilesetBackend()
    monkeypatch.setitem(server.BACKENDS, "retro_diffusion", lambda: backend)
    monkeypatch.setitem(server.BACKENDS, "local", lambda: pytest.fail("tilesets must not touch the local backend"))

    content = _call("generate_tileset", {"inside_prompt": "grey stones", "outside_prompt": "grass",
                                         "tile_size_px": 16})

    info = json.loads(content[0].text)
    assert (info["columns"], info["rows"], info["size_px"], info["cost_usd"]) == (4, 3, [64, 48], 0.06)
    assert backend.requests[0].outside_prompt == "grass"
    assert Image.open(info["path"]).size == (64, 48)  # a sheet is stored at its own size, not resized to a tile


def test_an_unknown_backend_is_an_error_listing_the_valid_ones():
    with pytest.raises(Exception, match="retro_diffusion.*local|local.*retro_diffusion"):
        server._backend("midjourney")


def test_a_backend_failure_reaches_the_caller_with_its_message_and_nothing_is_written(monkeypatch, tmp_path):
    monkeypatch.setenv("SPYRITE_OUTPUT_DIR", str(tmp_path))
    monkeypatch.delenv("RD_API_KEY", raising=False)
    with pytest.raises(Exception, match="RD_API_KEY is not set"):
        _call("generate_tile", {"prompt": "x", "backend": "retro_diffusion"})
    assert list(tmp_path.iterdir()) == []


def test_list_backends_reports_an_unset_key_and_an_unreachable_service_as_data(monkeypatch):
    monkeypatch.delenv("RD_API_KEY", raising=False)

    def down(request):
        raise httpx.ConnectTimeout("")

    monkeypatch.setattr(server, "LocalSdxlBackend", lambda: LocalSdxlBackend(
        base_url="http://local.test", transport=httpx.MockTransport(down)))

    report = json.loads(_call("list_backends", {})[0].text)

    assert report["retro_diffusion"] == {"configured": False}
    assert report["local"] == {"url": "http://local.test", "error": "ConnectTimeout"}


def test_list_backends_reports_the_rd_balance_and_the_local_health(monkeypatch):
    monkeypatch.setenv("RD_API_KEY", "rdpk-test")
    monkeypatch.setattr(server, "RetroDiffusionBackend", lambda: RetroDiffusionBackend(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"balance": 1.5}))))
    monkeypatch.setattr(server, "LocalSdxlBackend", lambda: LocalSdxlBackend(
        base_url="http://local.test",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"status": "ok", "holds_gpu": False}))))

    report = json.loads(_call("list_backends", {})[0].text)

    assert report["retro_diffusion"] == {"configured": True, "balance_usd": 1.5}
    assert report["local"]["health"] == {"status": "ok", "holds_gpu": False}


def test_estimate_cost_prices_a_tile_with_the_tile_style_and_does_not_generate(monkeypatch):
    rd = _Rd(cost=0.03)
    monkeypatch.setattr(server, "RetroDiffusionBackend", lambda: _rd(rd))
    monkeypatch.setitem(server.BACKENDS, "retro_diffusion", lambda: _rd(rd))

    info = json.loads(_call("estimate_cost", {"kind": "tile", "backend": "retro_diffusion",
                                              "prompt": "x", "size_px": 16})[0].text)

    assert info["estimated_usd"] == 0.03
    assert [(b["check_cost"], b["prompt_style"]) for b, _ in rd.posts] == [(True, "rd_tile__single_tile")]


def test_the_instructions_fit_the_limit_and_name_the_blended_workflow():
    assert len(server.INSTRUCTIONS) <= 2048
    for word in ("declare_plan", "import_tileset", "create_tile_object", "fill_tiles", "estimate_cost"):
        assert word in server.INSTRUCTIONS


# -- Storage ---------------------------------------------------------------------


def test_save_generated_names_the_file_by_provider_and_content_hash(tmp_path, monkeypatch):
    import hashlib
    monkeypatch.setenv("SPYRITE_OUTPUT_DIR", str(tmp_path))
    png = b"\x89PNG not really"
    path = store.save_generated(png, "local", {"request": {"a": 1}, "cost_usd": 0.0})
    assert path.parent == tmp_path.resolve()
    assert path.name.endswith(f"-local-{hashlib.sha256(png).hexdigest()[:12]}.png")
    assert path.read_bytes() == png
    assert json.loads(path.with_suffix(".json").read_text()) == {
        "provider": "local", "request": {"a": 1}, "cost_usd": 0.0}


def test_the_default_output_directory_is_outputs_generated_in_the_repository(monkeypatch, tmp_path):
    monkeypatch.delenv("SPYRITE_OUTPUT_DIR", raising=False)
    # The real root is found by counting path components up from store.py.
    assert (store._REPOSITORY_ROOT / "packages" / "spyrite_tile_gen" / "pyproject.toml").is_file()
    monkeypatch.setattr(store, "_REPOSITORY_ROOT", tmp_path)
    assert store.output_directory() == (tmp_path / "outputs" / "generated").resolve()
    assert (tmp_path / "outputs" / "generated").is_dir()


def test_over_stdio_the_tools_are_listed_and_an_image_arrives_as_image_content(tmp_path):
    """The real transport: a subprocess server and an MCP client session."""
    import sys

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    tile = tmp_path / "t.png"
    Image.new("RGBA", (16, 16), (9, 9, 9, 255)).save(tile)
    environment = {"SPYRITE_OUTPUT_DIR": str(tmp_path / "out"), "PATH": "/usr/bin:/bin"}
    parameters = StdioServerParameters(
        command=sys.executable, args=["-m", "spyrite_tile_gen.server"], env=environment)

    async def session():
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as client:
                initialised = await client.initialize()
                tools = await client.list_tools()
                atlas = await client.call_tool("compose_atlas", {
                    "paths": [str(tile)], "tile_size_px": 16, "columns": 1, "output_name": "a"})
                failed = await client.call_tool("generate_tile", {"prompt": "x"})
                return initialised, tools, atlas, failed

    initialised, tools, atlas, failed = asyncio.run(session())

    assert {t.name for t in tools.tools} == {
        "list_backends", "estimate_cost", "generate_tile", "generate_sprite",
        "generate_tileset", "normalize_image", "compose_atlas"}
    assert initialised.instructions == server.INSTRUCTIONS
    assert not atlas.isError and [c.type for c in atlas.content] == ["text", "image"]
    assert failed.isError and "RD_API_KEY is not set" in failed.content[0].text
