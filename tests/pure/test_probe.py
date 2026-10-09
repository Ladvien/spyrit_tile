"""spyrite_probe: the pixel oracle shared by verify_tile_object and scripts/visual_probe.py (no bpy, no PIL)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "addon" / "spyrite_tile"))
import spyrite_probe as probe  # noqa: E402

TL, TR, BL, BR = (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0), (1.0, 1.0, 0.0)


def picture(size, quadrants):
    """size x size picture whose four quadrants are flat colours; top row first."""
    rgba = []
    for y in range(size):
        for x in range(size):
            colour = quadrants[(y >= size // 2) * 2 + (x >= size // 2)]
            rgba.extend((*colour, 1.0))
    return (size, size, rgba)


def means(quadrants):
    return [[[round(v) for v in colour] for colour in line] for line in quadrants]


TILE = picture(16, [TL, TR, BL, BR])


def test_unturned_quadrants_are_the_picture_in_reading_order():
    expected = probe.expected_quadrants(TILE, 0, False, False)
    assert means(expected) == [[[255, 0, 0], [0, 255, 0]], [[0, 0, 255], [255, 255, 0]]]


@pytest.mark.parametrize(
    "rotation, flip_x, flip_y, order",
    [
        (0, False, False, [TL, TR, BL, BR]),
        (90, False, False, [TR, BR, TL, BL]),  # counter-clockwise turn
        (180, False, False, [BR, BL, TR, TL]),
        (270, False, False, [BL, TL, BR, TR]),
        (0, True, False, [TR, TL, BR, BL]),
        (0, False, True, [BL, BR, TL, TR]),
        (90, True, False, [BR, TR, BL, TL]),  # mirrored as seen, after the turn
    ],
)
def test_turn_then_mirror_as_seen(rotation, flip_x, flip_y, order):
    expected = probe.expected_quadrants(TILE, rotation, flip_x, flip_y)
    flat = [q for line in means(expected) for q in line]
    assert flat == [[round(255 * v) for v in colour] for colour in order]


def test_observed_quadrants_of_a_screen_region_match_the_tile_it_shows():
    shot = picture(64, [TL, TR, BL, BR])
    observed = probe.observed_quadrants(shot, (0, 0, 64, 64))
    assert probe.compare(probe.expected_quadrants(TILE, 0, False, False), observed) == pytest.approx(0, abs=1e-6)
    sub = probe.observed_quadrants(picture(64, [TL] * 4), (10, 10, 30, 30))
    assert means(sub) == [[[255, 0, 0]] * 2] * 2


def test_compare_is_the_largest_channel_difference_on_the_255_scale():
    a = probe.expected_quadrants(TILE, 0, False, False)
    b = probe.expected_quadrants(TILE, 90, False, False)
    assert probe.compare(a, a) == 0
    assert probe.compare(a, b) == pytest.approx(255)
    nudged = [[list(q) for q in line] for line in a]
    nudged[1][0][2] -= 10.0
    assert probe.compare(a, nudged) == pytest.approx(10.0)


def test_crop_takes_a_sub_picture_and_refuses_boxes_outside_the_picture():
    width, height, rgba = probe.crop(TILE, (8, 0, 16, 8))
    assert (width, height) == (8, 8)
    assert means(probe.observed_quadrants((width, height, rgba), (0, 0, 8, 8))) == [[[0, 255, 0]] * 2] * 2
    with pytest.raises(ValueError, match="outside the 16x16 picture"):
        probe.crop(TILE, (8, 8, 17, 16))


def test_covered_is_true_only_when_a_nearer_box_overlaps_the_inner_half():
    box = (0, 0, 40, 40)  # inner half = (10, 10, 30, 30)
    assert probe.covered(box, [(20, 20, 60, 60)])
    assert not probe.covered(box, [(30, 0, 60, 40)])  # touches the edge of the inner half only
    assert not probe.covered(box, [(45, 0, 85, 40)])
    assert not probe.covered(box, [])
