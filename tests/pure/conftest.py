"""Shared fixtures for the pure-Python suites."""

import importlib
import sys
import types
from pathlib import Path

import pytest

ADDON_DIR = Path(__file__).resolve().parents[2] / "addon" / "spyrite_tile"


@pytest.fixture(scope="session")
def addon_spec():
    """The add-on's own `spyrite_spec` module (its parsers), imported without bpy."""
    # The add-on's __init__ imports bpy, so mount the directory under a stub package instead.
    package = types.ModuleType("_spyrite_addon")
    package.__path__ = [str(ADDON_DIR)]
    sys.modules.setdefault("_spyrite_addon", package)
    return importlib.import_module("_spyrite_addon.spyrite_spec")
