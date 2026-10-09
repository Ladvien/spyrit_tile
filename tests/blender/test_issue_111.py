"""Issue #111: the trackpad delta accumulator behind palette scroll/pinch zoom.

The event handling itself needs a window and is covered by
tests/gui/check_issue_111.py; this pins the step arithmetic.
"""

import importlib

gui = importlib.import_module("bl_ext.user_default.spyrite_tile.sprytile_gui")
accumulate = gui.VIEW3D_OP_SprytileGui.accumulate_trackpad


def test_small_deltas_add_up_to_whole_steps():
    accum = 0.0
    seen = []
    for _ in range(7):
        steps, accum = accumulate(accum, 12.0, 30.0)
        seen.append(steps)
    # 12, 24, 36 -> 1 (6 left), 18, 30 -> 1 (0 left), 12, 24
    assert seen == [0, 0, 1, 0, 1, 0, 0]
    assert accum == 24.0


def test_one_large_delta_gives_several_steps_toward_zero():
    assert accumulate(0.0, 65.0, 30.0) == (2, 5.0)
    assert accumulate(0.0, -65.0, 30.0) == (-2, -5.0)


def test_change_of_direction_drops_the_leftover():
    steps, accum = accumulate(25.0, -10.0, 30.0)
    assert (steps, accum) == (0, -10.0)


def test_zero_delta_changes_nothing():
    assert accumulate(12.0, 0.0, 30.0) == (0, 12.0)
