"""Pixel oracle shared by the in-Blender ``verify_tile_object`` and the host-side ``scripts/visual_probe.py``.

Pure Python: no ``bpy``, no PIL, no relative imports (``scripts/visual_probe.py`` imports this file straight
from the add-on directory). Pictures are ``(width, height, rgba)`` tuples: ``rgba`` is any indexable
sequence of ``width * height * 4`` floats in 0..1 (a list, an ``array.array('f')``), rows stored top row
first, like an image file. Colours coming out of this module are on the 0..255 scale, and so is the
``max_channel_delta`` that :func:`compare` returns and callers hold against a tolerance.

A face is judged by its four quadrants: the mean colour of the inner half of each quadrant of the tile
(``expected_quadrants``, turned and mirrored the way the placement asked) against the same on the picture
(``observed_quadrants``). Quadrants are ``[[top-left, top-right], [bottom-left, bottom-right]]``.
"""

INNER_FRACTION = 0.5
CHANNELS = 3  # RGB; alpha is not judged


def _turn(quadrants, rotation_deg, flip_x, flip_y):
    """Contract: turn the 2x2 picture counter-clockwise, then mirror it as seen."""
    m = [list(line) for line in quadrants]
    for _ in range(int(rotation_deg) // 90):
        m = [[m[j][1 - i] for j in range(2)] for i in range(2)]
    if flip_x:
        m = [line[::-1] for line in m]
    if flip_y:
        m = m[::-1]
    return m


def _quadrant_boxes(box):
    x0, y0, x1, y1 = box
    xm, ym = (x0 + x1) / 2, (y0 + y1) / 2
    return [[(x0, y0, xm, ym), (xm, y0, x1, ym)], [(x0, ym, xm, y1), (xm, ym, x1, y1)]]  # top row first


def _inner(box, fraction=INNER_FRACTION):
    x0, y0, x1, y1 = box
    dx, dy = (x1 - x0) * (1 - fraction) / 2, (y1 - y0) * (1 - fraction) / 2
    return (x0 + dx, y0 + dy, x1 - dx, y1 - dy)


def _mean(picture, box):
    """Mean RGB (0..255) of the pixels in ``box`` (x0, y0, x1, y1; rounded, at least one pixel, clipped)."""
    width, height, rgba = picture
    x0 = max(0, min(width - 1, int(round(box[0]))))
    y0 = max(0, min(height - 1, int(round(box[1]))))
    x1 = max(x0 + 1, min(width, int(round(box[2]))))
    y1 = max(y0 + 1, min(height, int(round(box[3]))))
    sums = [0.0] * CHANNELS
    for y in range(y0, y1):
        base = (y * width + x0) * 4
        for x in range(x1 - x0):
            offset = base + x * 4
            for c in range(CHANNELS):
                sums[c] += rgba[offset + c]
    count = (x1 - x0) * (y1 - y0)
    return [255.0 * s / count for s in sums]


def crop(picture, box):
    """The ``(width, height, rgba)`` sub-picture ``box`` = (x0, y0, x1, y1) in whole pixels."""
    width, height, rgba = picture
    x0, y0, x1, y1 = (int(round(v)) for v in box)
    if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
        raise ValueError(f"crop box {(x0, y0, x1, y1)} is outside the {width}x{height} picture")
    out = []
    for y in range(y0, y1):
        out.extend(rgba[(y * width + x0) * 4:(y * width + x1) * 4])
    return (x1 - x0, y1 - y0, out)


def observed_quadrants(shot_rgba, box):
    """Quadrant colours of the picture region ``box`` (x0, y0, x1, y1 in pixels, y down)."""
    return [[_mean(shot_rgba, _inner(b)) for b in line] for line in _quadrant_boxes(box)]


def expected_quadrants(tile_rgba, rotation_deg, flip_x, flip_y):
    """Quadrant colours a face must show for a tile picture placed with this orientation."""
    width, height, _ = tile_rgba
    quadrants = observed_quadrants(tile_rgba, (0, 0, width, height))
    return _turn(quadrants, rotation_deg, flip_x, flip_y)


def compare(expected, observed):
    """Largest absolute RGB difference (0..255) over the four quadrants."""
    return max(
        abs(e - o)
        for expected_line, observed_line in zip(expected, observed)
        for expected_quadrant, observed_quadrant in zip(expected_line, observed_line)
        for e, o in zip(expected_quadrant, observed_quadrant)
    )


def covered(box, closer_boxes):
    """True when the inner half of ``box`` overlaps (by more than a pixel) any of ``closer_boxes``.

    The occlusion test for faces seen from one side: a face whose measured region lies under a nearer
    face cannot be judged, because the picture shows the nearer one.
    """
    x0, y0, x1, y1 = _inner(box)
    for bx0, by0, bx1, by1 in closer_boxes:
        if min(x1, bx1) - max(x0, bx0) > 1.0 and min(y1, by1) - max(y0, by0) > 1.0:
            return True
    return False
