"""spyrite-tile-dev: resources, prompts and the run_script allowlist."""

import asyncio
import importlib
import os
import subprocess
import time
from pathlib import Path

import pytest

from spyrite_tile_gen import devserver

REPO = Path(__file__).resolve().parents[2]


PYTHON = "/Users/ladvien/blended/.venv/bin/python"


@pytest.fixture(autouse=True)
def repo(request, monkeypatch):
    if "real_repo" not in request.keywords:
        monkeypatch.setattr(devserver, "REPO", REPO)


def run(name, args=None, **kwargs):
    return asyncio.run(devserver.run_script(name, args, **kwargs))


@pytest.mark.real_repo
def test_repo_resolves_to_checkout(monkeypatch):
    monkeypatch.delenv("SPYRITE_REPO", raising=False)
    reloaded = importlib.reload(devserver)
    try:
        assert reloaded.REPO == Path(reloaded.__file__).resolve().parents[4]
        assert (reloaded.REPO / "addon" / "spyrite_tile" / "api.py").is_file()
        monkeypatch.setenv("SPYRITE_REPO", "/some/other/checkout")
        assert importlib.reload(devserver).REPO == Path("/some/other/checkout")
    finally:
        monkeypatch.delenv("SPYRITE_REPO", raising=False)
        importlib.reload(devserver)


def test_skills_listed_and_readable():
    assert {"blender-self-review", "spyrite-agent-build"} <= set(devserver.list_skills())
    assert "Blender self-review" in devserver.read_skill("blender-self-review")
    assert devserver.skills_resource().splitlines() == devserver.list_skills()
    with pytest.raises(ValueError, match="known skills"):
        devserver.read_skill("nope")


def test_scripts_listed_with_purpose():
    names = {s["name"]: s for s in devserver.list_scripts()}
    assert {"visual_probe", "mcp_live_smoke", "run_blender_tests", "bump_version", "release"} <= set(names)
    assert names["visual_probe"]["purpose"].startswith("Visual oracle")
    assert "bump_version" in devserver.scripts_resource()
    assert "def " in devserver.script_resource("visual_probe")


def test_docs_and_example_spec():
    docs = devserver.api_docs_resource()
    assert "Agent-driven building" in docs
    assert "spyrite_spec" in devserver.example_spec_resource()


def test_prompts():
    assert "my brief" in devserver.spyrite_build_scene("my brief")
    assert "spyrite-agent-build" in devserver.spyrite_build_scene("x")
    review = devserver.spyrite_self_review("room")
    assert review.startswith("Object under review: room")
    assert "--object room" in review


def test_list_scripts_names_the_run_script_runner_of_each_script():
    runners = {s["name"]: s["runnable_as"] for s in devserver.list_scripts()}
    assert runners["visual_probe"] == "visual_probe"
    assert runners["mcp_live_smoke"] == "live_smoke"
    assert runners["run_blender_tests"] == "test_blender"
    assert runners["bump_version"] == runners["release"] == ""
    assert {r for r in runners.values() if r} <= set(devserver.ALLOWED_SCRIPTS)


def test_run_script_rejects_unknown_with_allowlist():
    with pytest.raises(ValueError) as error:
        run("nope")
    for name in ("visual_probe", "test_pure", "gui_check"):
        assert name in str(error.value)
    for hidden in ("bump_version", "release"):
        assert hidden not in devserver.ALLOWED_SCRIPTS


@pytest.fixture
def fake_execute(monkeypatch):
    calls = []

    def execute(command, timeout_s):
        calls.append((command, timeout_s))
        return {"command": command}

    monkeypatch.setattr(devserver, "_execute", execute)
    return calls


def test_run_script_commands(fake_execute):
    probe = ["--object", "room", "--tileset", "tests/fixtures/tiles_16px.png", "--tile-size", "16",
             "--view", "front", "--zoom-cell", "1", "-2.5", "--tolerance", "12", "--min-faces", "3",
             "--allow-uniform", "--out", "outputs/visual_probe/room_mcp"]
    tileset = str(REPO / "tests/fixtures/tiles_16px.png")
    assert run("visual_probe", probe)["command"] == [
        ".venv/bin/python", "scripts/visual_probe.py", "--object", "room", "--tileset", tileset,
        "--tile-size", "16", "--view", "front", "--zoom-cell", "1.0", "-2.5", "--tolerance", "12.0",
        "--min-faces", "3", "--allow-uniform", "--out", str(REPO / "outputs/visual_probe/room_mcp")]
    assert run("live_smoke")["command"] == [PYTHON, "scripts/mcp_live_smoke.py"]
    for name in ("live_smoke", "test_live"):
        assert run(name, ["--reload-api"])["command"] == [PYTHON, "scripts/mcp_live_smoke.py", "--reload-api"]
    for target in ("test_pure", "test_blender", "test_gui", "test_visual"):
        assert run(target)["command"] == ["make", target.replace("_", "-")]
    assert run("gui_check", ["check_issue_135"])["command"] == ["tests/gui/run_gui_check.sh", "tests/gui/check_issue_135.py"]
    for spelling in ("check_issue_135.py", "tests/gui/check_issue_135.py", str(REPO / "tests/gui/check_issue_135.py")):
        assert run("gui_check", [spelling])["command"][1] == "tests/gui/check_issue_135.py"
    assert fake_execute[0][1] == 600


@pytest.mark.parametrize("name, args", [
    ("test_live", ["--reload-api; echo INJECTED"]),
    ("test_live", ["--reload-api", "$(touch /tmp/x)"]),
    ("test_live", ["--help"]),
    ("live_smoke", ["--reload"]),
    ("test_pure", ["ARGS=x"]),
    ("test_blender", ["--help"]),
    ("test_gui", ["x"]),
    ("test_visual", ["x"]),
])
def test_run_script_rejects_arguments_it_does_not_define(fake_execute, name, args):
    with pytest.raises(ValueError):
        run(name, args)
    assert fake_execute == []


def test_test_live_never_reaches_a_shell(fake_execute):
    command = run("test_live", ["--reload-api"])["command"]
    assert command[0] != "make" and not any("ARGS=" in part for part in command)


GOOD_PROBE = ["--object", "room", "--tileset", "tests/fixtures/tiles_16px.png", "--tile-size", "16"]


@pytest.mark.parametrize("args, match", [
    (GOOD_PROBE + ["--out", "/tmp/anywhere"], "must be inside"),
    (GOOD_PROBE + ["--out", "outputs/../../elsewhere"], "must be inside"),
    (GOOD_PROBE + ["--out=outputs/x"], "unexpected argument"),
    (GOOD_PROBE + ["--help"], "unexpected argument"),
    (GOOD_PROBE + ["-h"], "unexpected argument"),
    (GOOD_PROBE + ["--ou", "outputs/x"], "unexpected argument"),
    (GOOD_PROBE + ["--placements", "/etc/passwd"], "must be inside"),
    (GOOD_PROBE + ["--placements", "tests/fixtures/missing.json"], "existing file"),
    (["--object", "room", "--tileset", "/etc/passwd", "--tile-size", "16"], "must be inside"),
    (["--object", "room", "--tileset", "tests", "--tile-size", "16"], "existing file"),
    (["--object", "-x", "--tileset", "tests/fixtures/tiles_16px.png", "--tile-size", "16"], "needs a value"),
    (["--object", "room", "--tileset", "tests/fixtures/tiles_16px.png"], "needs"),
    (GOOD_PROBE + ["--view", "side"], "one of"),
    (GOOD_PROBE + ["--tolerance", "nan"], "finite"),
    (GOOD_PROBE + ["--min-faces", "2.5"], "integer"),
    (GOOD_PROBE + ["--zoom-cell", "1"], "needs a value"),
    (GOOD_PROBE + ["room"], "unexpected argument"),
])
def test_visual_probe_arguments_are_checked(fake_execute, args, match):
    with pytest.raises(ValueError, match=match):
        run("visual_probe", args)
    assert fake_execute == []


def test_visual_probe_refuses_a_symlink_out_of_outputs(fake_execute, tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "outputs").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "tests" / "t.png").write_bytes(b"x")
    (tmp_path / "elsewhere").mkdir()
    (repo / "outputs" / "link").symlink_to(tmp_path / "elsewhere")
    monkeypatch.setattr(devserver, "REPO", repo)
    args = ["--object", "o", "--tileset", "tests/t.png", "--tile-size", "16"]
    with pytest.raises(ValueError, match="must be inside"):
        run("visual_probe", args + ["--out", "outputs/link/x"])
    assert run("visual_probe", args + ["--out", "outputs/ok"])["command"][-1] == str((repo / "outputs" / "ok").resolve())


@pytest.mark.parametrize("args", [
    [], ["/tmp/x.py"], ["../check_issue_135.py"], ["tests/gui/../../scripts/release.sh"], ["check_gui_smoke"],
    ["check_issue_999"], ["check_issue_135", "check_issue_58"], ["tests/gui/run_gui_check.sh"],
])
def test_gui_check_only_runs_checks_under_tests_gui(fake_execute, args):
    with pytest.raises(ValueError, match="check_issue_135.py"):
        run("gui_check", args)
    assert fake_execute == []


def test_gui_check_refuses_a_symlinked_check(fake_execute, tmp_path, monkeypatch):
    (tmp_path / "tests" / "gui").mkdir(parents=True)
    evil = tmp_path / "evil.py"
    evil.write_text("")
    (tmp_path / "tests" / "gui" / "check_issue_1.py").symlink_to(evil)
    monkeypatch.setattr(devserver, "REPO", tmp_path)
    with pytest.raises(ValueError, match="not a tests/gui/check_issue"):
        run("gui_check", ["check_issue_1"])


@pytest.mark.parametrize("timeout", [0, -1, 3601, 10**9, 1.5, "60", True])
def test_timeout_must_be_a_bounded_integer(fake_execute, timeout):
    with pytest.raises(ValueError, match="1 to 3600"):
        run("test_pure", timeout_s=timeout)
    assert fake_execute == []
    run("test_pure", timeout_s=3600)
    assert fake_execute[0][1] == 3600


def test_execute_caps_output_and_reports_exit_code():
    result = devserver._execute(["sh", "-c", "head -c 300000 /dev/zero | tr '\\0' x; echo err >&2; exit 3"], 30)
    assert result["exit_code"] == 3 and result["timed_out"] is False and result["truncated"] is True
    assert len(result["stdout"]) == devserver.OUTPUT_LIMIT_BYTES and result["stderr"] == "err\n"


def test_timeout_kills_the_whole_process_group(tmp_path):
    pidfile = tmp_path / "child.pid"
    started = time.monotonic()
    result = devserver._execute(["sh", "-c", f"sleep 300 & echo $! > {pidfile}; wait"], 1)
    assert result["timed_out"] is True and result["exit_code"] != 0
    assert "timed out after 1s" in result["stderr"] and time.monotonic() - started < 30
    grandchild = int(pidfile.read_text())
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(grandchild, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        os.kill(grandchild, 9)
        pytest.fail("the grandchild survived the timeout")


def test_run_script_does_not_block_the_event_loop(monkeypatch):
    def slow(command, timeout_s):
        time.sleep(0.5)
        return {"command": command}

    monkeypatch.setattr(devserver, "_execute", slow)

    async def scenario():
        ticks = 0

        async def ticker():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.05)
                ticks += 1

        task = asyncio.create_task(ticker())
        await devserver.run_script("test_pure")
        task.cancel()
        return ticks

    assert asyncio.run(scenario()) >= 5


def test_scripts_get_no_secrets_from_the_host_environment(monkeypatch):
    for key in ("RD_API_KEY", "GITHUB_TOKEN", "AWS_SECRET_ACCESS_KEY", "SPYRITE_REPO", "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(key, "hunter2")
    monkeypatch.setenv("BLENDER", "/b")
    monkeypatch.setenv("BLENDED_SRC", "/s")
    monkeypatch.setenv("LC_ALL", "C")
    env = devserver._child_env()
    assert {"PATH", "HOME", "BLENDER", "BLENDED_SRC", "LC_ALL"} <= set(env)
    assert not any(key in env for key in ("RD_API_KEY", "GITHUB_TOKEN", "AWS_SECRET_ACCESS_KEY", "SPYRITE_REPO", "ANTHROPIC_API_KEY"))
    seen = devserver._execute(["env"], 30)["stdout"]
    assert "hunter2" not in seen and "RD_API_KEY" not in seen
