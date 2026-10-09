"""Operators callable as bpy.ops.sprytile.<id>() (EXEC_DEFAULT) in background Blender, no window/event."""

import bpy
import pytest


@pytest.fixture(autouse=True)
def restore_flags():
    data = bpy.context.scene.sprytile_data
    saved = (data.uv_flip_x, data.uv_flip_y, data.auto_reload)
    yield
    data.uv_flip_x, data.uv_flip_y, data.auto_reload = saved


def test_reload_images_headless():
    assert bpy.ops.sprytile.reload_imgs() == {"FINISHED"}


def test_reset_sprytile_headless():
    data = bpy.context.scene.sprytile_data
    data.auto_reload = True
    assert bpy.ops.sprytile.reset_sprytile() == {"FINISHED"}
    assert data.auto_reload is False


@pytest.mark.parametrize("axis", ["x", "y"])
def test_flip_toggle_headless(axis):
    data = bpy.context.scene.sprytile_data
    attr = f"uv_flip_{axis}"
    op = getattr(bpy.ops.sprytile, f"flip_{axis}_toggle")
    before = getattr(data, attr)
    assert op() == {"FINISHED"}
    assert getattr(data, attr) is (not before)
    assert op() == {"FINISHED"}
    assert getattr(data, attr) is before
