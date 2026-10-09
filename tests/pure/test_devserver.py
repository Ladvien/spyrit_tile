"""spyrite-tile-dev: resources, prompts and the run_script allowlist."""

import subprocess
from pathlib import Path

import pytest

from spyrite_tile_gen import devserver

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def repo(monkeypatch):
    monkeypatch.setattr(devserver, "REPO", REPO)


def test_repo_resolves_to_checkout():
    assert (devserver.REPO / "addon" / "spyrite_tile" / "api.py").is_file()


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


def test_run_script_rejects_unknown_with_allowlist():
    with pytest.raises(ValueError) as error:
        devserver.run_script("nope")
    for name in ("visual_probe", "test_pure", "gui_check"):
        assert name in str(error.value)
    for hidden in ("bump_version", "release"):
        assert hidden not in devserver.ALLOWED_SCRIPTS


def test_run_script_commands(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 3, stdout="x" * 300_000, stderr="err")

    monkeypatch.setattr(devserver.subprocess, "run", fake_run)
    result = devserver.run_script("visual_probe", ["--object", "room"])
    assert result["command"] == [".venv/bin/python", "scripts/visual_probe.py", "--object", "room"]
    assert result["exit_code"] == 3 and result["truncated"] is True
    assert len(result["stdout"]) == devserver.OUTPUT_LIMIT_BYTES and result["stderr"] == "err"
    assert calls[0][1]["cwd"] == REPO
    result = devserver.run_script("test_live", ["--reload-api"])
    assert result["command"] == ["make", "test-live", "ARGS=--reload-api"]
    assert devserver.run_script("test_pure", ["ignored"])["command"] == ["make", "test-pure"]
    assert devserver.run_script("gui_check", ["check_x.py", "y"])["command"] == [
        "tests/gui/run_gui_check.sh", "check_x.py"]
    assert devserver.run_script("live_smoke")["command"] == [
        "/Users/ladvien/blended/.venv/bin/python", "scripts/mcp_live_smoke.py"]
