"""MCP server: the Spyrite Tile project's skills, scripts and docs, for an agent over MCP.

Runs on the host next to `spyrite-tile-gen`. It is read-mostly: resources and prompts expose
`.claude/skills`, `scripts/`, the API docstring and the example spec; the `run_script` tool runs
an allowlisted set of repository scripts. State-changing scripts (`bump_version`, `release`) are
deliberately not exposed.

`run_script` never goes through a shell: every script is an argv list, `make` targets take no
arguments, and the arguments of the two scripts that accept some are checked against an explicit
option set (see `run_script`).
"""

from __future__ import annotations

import ast
import asyncio
import math
import os
import re
import signal
import subprocess
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

REPO = Path(os.environ.get("SPYRITE_REPO") or Path(__file__).resolve().parents[4])
BLENDED_PYTHON = "/Users/ladvien/blended/.venv/bin/python"
OUTPUT_LIMIT_BYTES = 200_000
MAX_TIMEOUT_S = 3600
KILL_GRACE_S = 5
BUILD_SKILL = "spyrite-agent-build"
REVIEW_SKILL = "blender-self-review"

mcp = FastMCP("spyrite-tile-dev")


_FLAG, _WORD, _INT, _FLOAT, _FLOAT_PAIR, _FILE, _OUT_DIR = "flag", "word", "int", "float", "float2", "file", "outdir"
_VIEWS = ("top", "front", "right")  # scripts/visual_probe.py VIEWS

# Options each argument-taking script accepts: option -> kind (or a tuple of allowed values).
# Anything else, including `--help` and abbreviations or `--opt=value` spellings, is refused.
_VISUAL_PROBE_OPTIONS: dict[str, Any] = {
    "--object": _WORD, "--tileset": _FILE, "--tile-size": _INT, "--view": _VIEWS,
    "--zoom-cell": _FLOAT_PAIR, "--cell-size-m": _FLOAT, "--tolerance": _FLOAT, "--min-faces": _INT,
    "--allow-uniform": _FLAG, "--placements": _FILE, "--out": _OUT_DIR,
}
_VISUAL_PROBE_REQUIRED = ("--object", "--tileset", "--tile-size")
_LIVE_SMOKE_OPTIONS: dict[str, Any] = {"--reload-api": _FLAG}


def _under(value: str, root: Path, option: str, *, file: bool) -> str:
    """Resolve `value` (relative to REPO) and require it inside `root`; returns the absolute path."""
    root = root.resolve()
    resolved = (REPO / value).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"{option} {value!r} must be inside {root}")
    if file and not resolved.is_file():
        raise ValueError(f"{option} {value!r} must be an existing file inside {root}")
    if not file and resolved.exists() and not resolved.is_dir():
        raise ValueError(f"{option} {value!r} must be a directory path inside {root}")
    return str(resolved)


def _number(kind: str, option: str, value: str) -> str:
    try:
        number = int(value) if kind == _INT else float(value)
    except ValueError:
        raise ValueError(f"{option} needs {'an integer' if kind == _INT else 'a number'}, got {value!r}") from None
    if kind == _FLOAT and not math.isfinite(number):
        raise ValueError(f"{option} needs a finite number, got {value!r}")
    return str(number)


def _checked_options(script: str, args: list[str], options: dict[str, Any], required: tuple[str, ...] = ()) -> list[str]:
    """Validate `args` against `options` and return the argv tail (paths made absolute)."""
    allowed = f"allowed options for {script}: {sorted(options)}"
    out: list[str] = []
    seen: set[str] = set()
    index = 0

    def take(option: str) -> str:
        nonlocal index
        index += 1
        if index >= len(args):
            raise ValueError(f"{option} needs a value; {allowed}")
        value = args[index]
        if value.startswith("-") and not _is_number(value):
            raise ValueError(f"{option} needs a value, got {value!r}; {allowed}")
        return value

    while index < len(args):
        option = args[index]
        if option not in options:
            raise ValueError(f"{script}: unexpected argument {option!r}; {allowed}")
        kind = options[option]
        seen.add(option)
        out.append(option)
        if kind == _FLAG:
            pass
        elif kind == _FLOAT_PAIR:
            out += [_number(_FLOAT, option, take(option)), _number(_FLOAT, option, take(option))]
        else:
            value = take(option)
            if kind == _WORD:
                if not value:
                    raise ValueError(f"{option} needs a non-empty value")
                out.append(value)
            elif kind in (_INT, _FLOAT):
                out.append(_number(kind, option, value))
            elif kind == _FILE:
                out.append(_under(value, REPO, option, file=True))
            elif kind == _OUT_DIR:
                out.append(_under(value, REPO / "outputs", option, file=False))
            else:  # tuple of allowed values
                if value not in kind:
                    raise ValueError(f"{option} must be one of {list(kind)}, got {value!r}")
                out.append(value)
        index += 1
    missing = [option for option in required if option not in seen]
    if missing:
        raise ValueError(f"{script} needs {missing}; {allowed}")
    return out


def _is_number(text: str) -> bool:
    try:
        float(text)
    except ValueError:
        return False
    return True


def _make(target: str, args: list[str]) -> list[str]:
    if args:
        raise ValueError(f"{target.replace('-', '_')} takes no args (got {args}); it runs `make {target}` as is")
    return ["make", target]


def _gui_checks() -> dict[str, Path]:
    root = (REPO / "tests" / "gui").resolve()
    return {p.name: p for p in sorted(root.glob("check_issue_*.py")) if p.is_file()} if root.is_dir() else {}


def _gui_check(args: list[str]) -> list[str]:
    checks = _gui_checks()
    available = f"available checks: {sorted(checks)}"
    if len(args) != 1:
        raise ValueError(f"gui_check needs exactly one arg, a check from tests/gui; {available}")
    relative = Path(args[0])
    if len(relative.parts) == 1:  # bare name: check_issue_135 or check_issue_135.py
        relative = Path("tests") / "gui" / relative
    if relative.suffix != ".py":
        relative = relative.with_name(relative.name + ".py")
    resolved = (REPO / relative).resolve()
    if checks.get(resolved.name) != resolved:
        raise ValueError(f"gui_check: {args[0]!r} is not a tests/gui/check_issue_*.py check; {available}")
    return ["tests/gui/run_gui_check.sh", f"tests/gui/{resolved.name}"]


# name -> builder(args) -> argv. Exact allowlist; keep bump_version/release out. No builder
# produces a shell command line: arguments only ever reach a script as separate argv items.
ALLOWED_SCRIPTS: dict[str, Any] = {
    "visual_probe": lambda args: [
        ".venv/bin/python", "scripts/visual_probe.py",
        *_checked_options("visual_probe", args, _VISUAL_PROBE_OPTIONS, _VISUAL_PROBE_REQUIRED)],
    "live_smoke": lambda args: [
        BLENDED_PYTHON, "scripts/mcp_live_smoke.py", *_checked_options("live_smoke", args, _LIVE_SMOKE_OPTIONS)],
    "test_pure": lambda args: _make("test-pure", args),
    "test_blender": lambda args: _make("test-blender", args),
    "test_gui": lambda args: _make("test-gui", args),
    "test_visual": lambda args: _make("test-visual", args),
    # Same argv as the Makefile's test-live recipe, run directly so no shell ever sees the args.
    "test_live": lambda args: [
        BLENDED_PYTHON, "scripts/mcp_live_smoke.py", *_checked_options("test_live", args, _LIVE_SMOKE_OPTIONS)],
    "gui_check": _gui_check,
}

# scripts/ stem (or tests/gui runner) -> the run_script name that runs it.
SCRIPT_RUNNERS = {
    "visual_probe": "visual_probe",
    "mcp_live_smoke": "live_smoke",
    "run_blender_tests": "test_blender",
    "run_gui_check": "gui_check",
    "run_gui_smoke": "test_gui",
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
         "runnable_as": SCRIPT_RUNNERS.get(name, "")}
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


def _child_env() -> dict[str, str]:
    """The environment scripts run with: no API keys or other host secrets."""
    keep = ("PATH", "HOME", "TMPDIR", "LANG", "BLENDER", "BLENDED_SRC")
    return {k: v for k, v in os.environ.items() if k in keep or k.startswith("LC_")}


def _execute(command: list[str], timeout_s: int) -> dict[str, Any]:
    """Run `command` in its own session; on timeout kill the whole process group."""
    process = subprocess.Popen(
        command, cwd=REPO, env=_child_env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, errors="replace", start_new_session=True)
    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            stdout, stderr = process.communicate(timeout=KILL_GRACE_S)
        except subprocess.TimeoutExpired as error:  # something outside the group still holds the pipes
            def _text(value: Any) -> str:
                return value.decode("utf-8", "replace") if isinstance(value, bytes) else (value or "")
            stdout, stderr = _text(error.stdout), _text(error.stderr)
            process.stdout.close()
            process.stderr.close()
    stdout, cut_out = _cap(stdout or "")
    if timed_out:
        stderr = (stderr or "") + f"\ntimed out after {timeout_s}s; process group killed"
    stderr, cut_err = _cap(stderr or "")
    return {"command": command, "exit_code": process.returncode, "timed_out": timed_out,
            "stdout": stdout, "stderr": stderr, "truncated": cut_out or cut_err}


@mcp.tool()
async def run_script(name: str, args: list[str] | None = None, timeout_s: int = 600) -> dict[str, Any]:
    """Run an allowlisted repository script from the repo root (never through a shell).

    Allowed: visual_probe, live_smoke, test_live (same as live_smoke), test_pure, test_blender,
    test_gui, test_visual, gui_check. Argument policy: test_pure/test_blender/test_gui/test_visual take
    no args; live_smoke/test_live take only `--reload-api`; visual_probe takes only its own options
    (--object, --tileset, --tile-size, --view, --zoom-cell, --cell-size-m, --tolerance, --min-faces,
    --allow-uniform, --placements, --out) as separate `--option value` items with --object, --tileset
    and --tile-size required, --tileset/--placements existing files inside the repo and --out a directory
    inside `outputs/`; gui_check takes one arg, a `tests/gui/check_issue_*.py` check (bare name accepted).
    Anything else is refused with the allowed set. Scripts get a minimal environment (PATH, HOME, TMPDIR,
    LANG/LC_*, BLENDER, BLENDED_SRC; no API keys). `timeout_s` is 1-3600; on timeout the script's whole
    process group is killed and `timed_out` is true.
    Returns command, exit_code, timed_out, stdout, stderr (each capped at 200 kB) and truncated.
    """
    args = list(args or [])
    if name not in ALLOWED_SCRIPTS:
        raise ValueError(f"script {name!r} is not allowed; allowed scripts: {sorted(ALLOWED_SCRIPTS)}")
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, int) or not 1 <= timeout_s <= MAX_TIMEOUT_S:
        raise ValueError(f"timeout_s must be an integer from 1 to {MAX_TIMEOUT_S}, got {timeout_s!r}")
    command = ALLOWED_SCRIPTS[name](args)
    return await asyncio.to_thread(_execute, command, timeout_s)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
