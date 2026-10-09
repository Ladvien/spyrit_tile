"""MCP server: the Spyrite Tile project's skills, scripts and docs, for an agent over MCP.

Runs on the host next to `spyrite-tile-gen`. It is read-mostly: resources and prompts expose
`.claude/skills`, `scripts/`, the API docstring and the example spec; the `run_script` tool runs
an allowlisted set of repository scripts. State-changing scripts (`bump_version`, `release`) are
deliberately not exposed.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

REPO = Path(os.environ.get("SPYRITE_REPO") or Path(__file__).resolve().parents[4])
BLENDED_PYTHON = "/Users/ladvien/blended/.venv/bin/python"
OUTPUT_LIMIT_BYTES = 200_000
BUILD_SKILL = "spyrite-agent-build"
REVIEW_SKILL = "blender-self-review"

mcp = FastMCP("spyrite-tile-dev")


def _make(target: str, args: list[str]) -> list[str]:
    command = ["make", target]
    if target == "test-live" and args:
        command.append("ARGS=" + " ".join(args))
    return command


def _gui_check(args: list[str]) -> list[str]:
    if not args:
        raise ValueError("gui_check needs args[0]: the check script name for tests/gui/run_gui_check.sh")
    return ["tests/gui/run_gui_check.sh", args[0]]


# name -> builder(args) -> argv. Exact allowlist; keep bump_version/release out.
ALLOWED_SCRIPTS: dict[str, Any] = {
    "visual_probe": lambda args: [".venv/bin/python", "scripts/visual_probe.py", *args],
    "live_smoke": lambda args: [BLENDED_PYTHON, "scripts/mcp_live_smoke.py", *args],
    "test_pure": lambda args: _make("test-pure", args),
    "test_blender": lambda args: _make("test-blender", args),
    "test_gui": lambda args: _make("test-gui", args),
    "test_visual": lambda args: _make("test-visual", args),
    "test_live": lambda args: _make("test-live", args),
    "gui_check": _gui_check,
}


def _skills_dir() -> Path:
    return REPO / ".claude" / "skills"


def _skill_names() -> list[str]:
    root = _skills_dir()
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if (p / "SKILL.md").is_file())


def _read_skill(name: str) -> str:
    if name not in _skill_names():
        raise ValueError(f"no skill named {name!r}; known skills: {_skill_names()}")
    directory = _skills_dir() / name
    text = (directory / "SKILL.md").read_text(encoding="utf-8")
    for extra in sorted(directory.glob("*.md")):
        if extra.name != "SKILL.md":
            text += f"\n\n---\n<!-- {extra.name} -->\n" + extra.read_text(encoding="utf-8")
    return text


def _script_paths() -> dict[str, Path]:
    root = REPO / "scripts"
    return {p.stem: p for p in sorted(root.iterdir()) if p.suffix in (".py", ".sh")} if root.is_dir() else {}


def _purpose(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".py":
        doc = ast.get_docstring(ast.parse(text)) or ""
        return doc.strip().splitlines()[0] if doc.strip() else ""
    for line in text.splitlines()[1:]:
        if line.startswith("#"):
            return line.lstrip("# ").strip()
    return ""


def _list_scripts() -> list[dict[str, str]]:
    return [
        {"name": name, "file": str(path.relative_to(REPO)), "purpose": _purpose(path),
         "runnable_as": next((k for k in ALLOWED_SCRIPTS if k == name), "")}
        for name, path in _script_paths().items()
    ]


def _readme_section(heading: str) -> str:
    text = (REPO / "README.md").read_text(encoding="utf-8")
    match = re.search(rf"^(#+) {re.escape(heading)}\s*$", text, re.M)
    if not match:
        return ""
    level = len(match.group(1))
    rest = text[match.end():]
    end = re.search(rf"^#{{1,{level}}} ", rest, re.M)
    return match.group(0) + "\n" + (rest[: end.start()] if end else rest)


@mcp.resource("spyrite://skills")
def skills_resource() -> str:
    """The project's skills (name per line)."""
    return "\n".join(_skill_names())


@mcp.resource("spyrite://skills/{name}")
def skill_resource(name: str) -> str:
    """Contents of `.claude/skills/<name>/SKILL.md` plus any sibling .md files."""
    return _read_skill(name)


@mcp.resource("spyrite://scripts")
def scripts_resource() -> str:
    """The repository scripts with a one-line purpose each."""
    return "\n".join(f"{s['name']}: {s['purpose']}" for s in _list_scripts())


@mcp.resource("spyrite://scripts/{name}")
def script_resource(name: str) -> str:
    """Source of `scripts/<name>`."""
    paths = _script_paths()
    if name not in paths:
        raise ValueError(f"no script named {name!r}; known scripts: {sorted(paths)}")
    return paths[name].read_text(encoding="utf-8")


@mcp.resource("spyrite://docs/api")
def api_docs_resource() -> str:
    """The `api.py` module docstring plus the README "Agent-driven building" section."""
    source = (REPO / "addon" / "spyrite_tile" / "api.py").read_text(encoding="utf-8")
    doc = ast.get_docstring(ast.parse(source)) or ""
    return doc + "\n\n" + _readme_section("Agent-driven building")


@mcp.resource("spyrite://specs/example")
def example_spec_resource() -> str:
    """The worked example scene spec, `tests/fixtures/room.spyrite.yaml`."""
    return (REPO / "tests" / "fixtures" / "room.spyrite.yaml").read_text(encoding="utf-8")


@mcp.prompt()
def spyrite_build_scene(brief: str) -> str:
    """Build a tile scene: the spyrite-agent-build skill followed by your brief."""
    return f"{_read_skill(BUILD_SKILL)}\n\n# Brief\n\n{brief}\n"


@mcp.prompt()
def spyrite_self_review(object_name: str) -> str:
    """Review a tile object: the blender-self-review skill with the object name substituted."""
    text = _read_skill(REVIEW_SKILL).replace("<name>", object_name)
    return f"Object under review: {object_name}\n\n{text}"


@mcp.tool()
def list_skills() -> list[str]:
    """List the project's skill names (read one with read_skill)."""
    return _skill_names()


@mcp.tool()
def read_skill(name: str) -> str:
    """Return the text of the named skill."""
    return _read_skill(name)


@mcp.tool()
def list_scripts() -> list[dict[str, str]]:
    """List repository scripts: name, file, one-line purpose, and the run_script name if runnable."""
    return _list_scripts()


def _cap(data: str) -> tuple[str, bool]:
    raw = data.encode("utf-8", "replace")
    if len(raw) <= OUTPUT_LIMIT_BYTES:
        return data, False
    return raw[:OUTPUT_LIMIT_BYTES].decode("utf-8", "ignore"), True


@mcp.tool()
def run_script(name: str, args: list[str] | None = None, timeout_s: int = 600) -> dict[str, Any]:
    """Run an allowlisted repository script from the repo root.

    Allowed: visual_probe, live_smoke, test_pure, test_blender, test_gui, test_visual,
    test_live (args become ARGS=...), gui_check (args[0] = check name).
    Returns command, exit_code, stdout, stderr (each capped at 200 kB) and truncated.
    """
    args = list(args or [])
    if name not in ALLOWED_SCRIPTS:
        raise ValueError(f"script {name!r} is not allowed; allowed scripts: {sorted(ALLOWED_SCRIPTS)}")
    command = ALLOWED_SCRIPTS[name](args)
    try:
        done = subprocess.run(command, cwd=REPO, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired as error:
        def _text(value: Any) -> str:
            return value.decode("utf-8", "replace") if isinstance(value, bytes) else (value or "")
        stdout, cut_out = _cap(_text(error.stdout))
        stderr, cut_err = _cap(_text(error.stderr) + f"\ntimed out after {timeout_s}s")
        return {"command": command, "exit_code": -1, "stdout": stdout, "stderr": stderr,
                "truncated": cut_out or cut_err}
    stdout, cut_out = _cap(done.stdout)
    stderr, cut_err = _cap(done.stderr)
    return {"command": command, "exit_code": done.returncode, "stdout": stdout, "stderr": stderr,
            "truncated": cut_out or cut_err}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
