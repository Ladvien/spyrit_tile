"""Registration smoke test: runs first, so a register-time failure fails the run."""

import bpy


def test_scene_properties_registered():
    assert hasattr(bpy.types.Scene, "sprytile_data")
    assert hasattr(bpy.types.Scene, "sprytile_mats")
    scene = bpy.context.scene
    assert scene.sprytile_data is not None
    assert len(scene.sprytile_mats) >= 0


def test_operators_registered():
    assert hasattr(bpy.ops.sprytile, "props_setup")
    assert bpy.ops.sprytile.props_setup.get_rna_type() is not None
