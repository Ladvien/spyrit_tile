"""Where generated images go: a PNG and a JSON sidecar that says how it was made."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

OUTPUT_DIRECTORY_ENVIRONMENT_VARIABLE = "SPYRITE_OUTPUT_DIR"
_NAME_PATTERN = re.compile(r"[A-Za-z0-9_-]+")
_DIGEST_CHARACTERS = 12
# packages/spyrite_tile_gen/src/spyrite_tile_gen/store.py -> repository root.
_REPOSITORY_ROOT = Path(__file__).resolve().parents[4]


def output_directory() -> Path:
    configured = os.environ.get(OUTPUT_DIRECTORY_ENVIRONMENT_VARIABLE)
    directory = Path(configured) if configured else _REPOSITORY_ROOT / "outputs" / "generated"
    directory.mkdir(parents=True, exist_ok=True)
    return directory.resolve()


def save_generated(png: bytes, provider: str, sidecar: dict[str, Any]) -> Path:
    """Write `<timestamp>-<provider>-<sha256[:12]>.png` and its `.json`; return the PNG path."""
    digest = hashlib.sha256(png).hexdigest()[:_DIGEST_CHARACTERS]
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = output_directory() / f"{stamp}-{provider}-{digest}.png"
    path.write_bytes(png)
    path.with_suffix(".json").write_text(
        json.dumps({"provider": provider, **sidecar}, indent=2, default=str), encoding="utf-8"
    )
    return path


def save_named(png: bytes, name: str, sidecar: dict[str, Any]) -> Path:
    """Write `<name>.png` and `<name>.json`, replacing earlier ones; return the PNG path."""
    if not _NAME_PATTERN.fullmatch(name):
        raise ValueError(f"output_name {name!r} may only contain letters, digits, '_' and '-'")
    path = output_directory() / f"{name}.png"
    path.write_bytes(png)
    path.with_suffix(".json").write_text(
        json.dumps(sidecar, indent=2, default=str), encoding="utf-8"
    )
    return path
