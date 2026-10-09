"""Run tests/blender inside Blender's Python (headless).

Invoked by `make test-blender` as `blender -b --factory-startup --python
scripts/run_blender_tests.py` with BLENDER_USER_EXTENSIONS pointing at a
directory whose user_default/spyrite_tile links to the add-on. The blended
source tree defaults to /Users/ladvien/blended/src; override with BLENDED_SRC.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BLENDED_SRC = os.environ.get("BLENDED_SRC", "/Users/ladvien/blended/src")
for p in (ROOT / ".blender-test-deps", ROOT / "packages" / "spyrite_tile_ops" / "src", Path(BLENDED_SRC)):
    sys.path.insert(0, str(p))

import addon_utils  # noqa: E402
import bpy  # noqa: E402

MODULE = "bl_ext.user_default.spyrite_tile"
try:
    bpy.ops.preferences.addon_enable(module=MODULE)
except Exception as exc:  # noqa: BLE001 - report any enable failure loudly
    sys.stderr.write(f"FATAL: enabling {MODULE} raised {exc!r}\n")
    sys.exit(2)
check = addon_utils.check(MODULE)
if check != (True, True):
    sys.stderr.write(f"FATAL: {MODULE} did not load and enable: addon_utils.check -> {check}\n")
    sys.exit(2)

import pytest  # noqa: E402

os.chdir(ROOT)
sys.exit(pytest.main(["tests/blender", "-q", "-p", "no:cacheprovider"]))
