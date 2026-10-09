"""normalize() and compose_atlas(): what makes a generated image safe as tile texels."""

import io
import math

import pytest
from PIL import Image

from spyrite_tile_gen.atlas import compose_atlas
from spyrite_tile_gen.normalize import normalize


def _png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _decode(png: bytes) -> Image.Image:
    return Image.open(io.BytesIO(png)).convert("RGBA")


def _pixels(image: Image.Image) -> list[tuple[int, int, int, int]]:
    data = image.convert("RGBA").tobytes()
    return list(zip(data[0::4], data[1::4], data[2::4], data[3::4]))


def _noise(size: int, seed: int = 1) -> Image.Image:
    image = Image.new("RGBA", (size, size))
    state = seed
    pixels = []
    for _ in range(size * size):
        state = (state * 1103515245 + 12345) & 0x7FFFFFFF
        pixels.append(((state >> 4) & 255, (state >> 12) & 255, (state >> 20) & 255, 255))
    image.putdata(pixels)
    return image


def test_a_large_image_is_reduced_to_the_exact_target_size():
    out, report = normalize(_png(_noise(128)), 16, 16)
    assert _decode(out).size == (16, 16)
    assert report.source_size_px == (128, 128)
    assert report.size_px == (16, 16)


def test_a_palette_restricts_every_pixel_to_its_colours():
    palette = ("#102030", "#ff0000", "#00ff00", "#ffffff")
    out, report = normalize(_png(_noise(32)), 32, 32, palette_hex=palette)
    allowed = {(0x10, 0x20, 0x30), (255, 0, 0), (0, 255, 0), (255, 255, 255)}
    assert {p[:3] for p in _pixels(_decode(out))} <= allowed
    assert report.colors_used <= 4


def test_unused_palette_slots_do_not_pull_dark_pixels_to_black():
    # A black image and a palette without black: PIL pads the palette with
    # zeros, which would turn the result black unless the padding repeats a
    # real colour.
    black = Image.new("RGBA", (4, 4), (0, 0, 0, 255))
    out, _ = normalize(_png(black), 4, 4, palette_hex=("#202020", "#ffffff"))
    assert {p[:3] for p in _pixels(_decode(out))} == {(0x20, 0x20, 0x20)}


def test_max_colors_limits_the_palette_to_the_opaque_pixels():
    out, report = normalize(_png(_noise(32)), 32, 32, max_colors=5)
    assert 1 <= report.colors_used <= 5
    assert len({p[:3] for p in _pixels(_decode(out))}) == report.colors_used


def test_alpha_is_binarised_and_transparent_pixels_carry_no_colour():
    image = Image.new("RGBA", (2, 1))
    image.putdata([(200, 10, 10, 100), (10, 200, 10, 200)])
    out, _ = normalize(_png(image), 2, 1)
    assert _pixels(_decode(out)) == [(0, 0, 0, 0), (10, 200, 10, 255)]


def test_transparent_pixels_do_not_claim_a_palette_colour():
    # Two opaque colours among fourteen transparent (black) pixels and a
    # two-colour budget: if the transparent pixels took part in the median cut,
    # one slot would go to black and the opaque greys would be merged or shifted.
    image = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
    image.putpixel((1, 1), (250, 250, 250, 255))
    image.putpixel((2, 2), (10, 10, 10, 255))
    out, report = normalize(_png(image), 4, 4, max_colors=2)
    decoded = _decode(out)
    assert decoded.getpixel((1, 1)) == (250, 250, 250, 255)
    assert decoded.getpixel((2, 2)) == (10, 10, 10, 255)
    assert decoded.getpixel((0, 0)) == (0, 0, 0, 0)
    assert report.colors_used == 2


def test_a_fully_transparent_image_stays_transparent_even_with_a_palette():
    out, report = normalize(_png(Image.new("RGBA", (4, 4), (9, 9, 9, 0))), 4, 4,
                            palette_hex=("#ff0000",))
    assert set(_pixels(_decode(out))) == {(0, 0, 0, 0)}
    assert report.colors_used == 0


def test_partial_alpha_survives_when_binarising_is_switched_off():
    image = Image.new("RGBA", (1, 1), (40, 50, 60, 100))
    out, _ = normalize(_png(image), 1, 1, binarize_alpha=False)
    assert _pixels(_decode(out)) == [(40, 50, 60, 100)]


def test_a_non_square_target_is_honoured_and_reported():
    out, report = normalize(_png(_noise(64)), 32, 8)
    assert _decode(out).size == (32, 8)
    assert report.size_px == (32, 8)


def test_a_bad_palette_entry_or_colour_count_is_rejected():
    with pytest.raises(ValueError, match="not #rrggbb"):
        normalize(_png(_noise(4)), 4, 4, palette_hex=("#fff",))
    with pytest.raises(ValueError, match="max_colors"):
        normalize(_png(_noise(4)), 4, 4, max_colors=0)
    with pytest.raises(ValueError, match="not positive"):
        normalize(_png(_noise(4)), 0, 4)


def test_a_wrapped_gradient_has_a_small_seam_ratio_and_the_same_ramp_cut_open_does_not():
    # A triangle wave with the image width as its period wraps without a jump:
    # the step across the seam is as big as any other step (ratio about 1).
    size = 32
    half = size // 2
    wave = [int(255 * (x / half if x <= half else (size - x) / half)) for x in range(size)]
    wrapped = Image.new("RGBA", (size, size))
    wrapped.putdata([(wave[x], 0, 0, 255) for _ in range(size) for x in range(size)])
    _, report = normalize(_png(wrapped), size, size)
    assert report.seam_ratio_x < 1.5

    # The same pixels as a single 0..255 ramp do jump from 255 back to 0 at the seam.
    ramp = Image.new("RGBA", (size, size))
    ramp.putdata([(x * 255 // (size - 1), 0, 0, 255) for _ in range(size) for x in range(size)])
    _, cut_open = normalize(_png(ramp), size, size)
    assert cut_open.seam_ratio_x > 10


def test_the_vertical_seam_is_measured_on_rows_not_columns():
    size = 16
    image = Image.new("RGBA", (size, size), (0, 0, 0, 255))
    image.paste((255, 255, 255, 255), (0, size // 2, size, size))  # black top, white bottom
    _, report = normalize(_png(image), size, size)
    assert report.seam_ratio_y > 3
    assert report.seam_ratio_x == 0.0


def test_a_hard_edge_has_a_large_seam_ratio_on_that_axis_only():
    size = 16
    image = Image.new("RGBA", (size, size), (0, 0, 0, 255))
    image.paste((255, 255, 255, 255), (size // 2, 0, size, size))  # black left, white right
    _, report = normalize(_png(image), size, size)
    assert report.seam_ratio_x > 3
    assert report.seam_ratio_y == 0.0


def test_compose_atlas_places_tiles_row_major_from_the_top_left(tmp_path):
    colours = [(255, 0, 0, 255), (0, 255, 0, 255), (0, 0, 255, 255), (255, 255, 0, 255)]
    paths = []
    for index, colour in enumerate(colours):
        path = tmp_path / f"t{index}.png"
        Image.new("RGBA", (16, 16), colour).save(path)
        paths.append(path)
    png, placed = compose_atlas(paths, 16, 2)
    atlas = _decode(png)
    assert atlas.size == (32, 32)
    assert placed == [(0, 0), (1, 0), (0, 1), (1, 1)]
    assert atlas.getpixel((24, 8)) == colours[1]  # second tile: column 1, row 0
    assert atlas.getpixel((24, 24)) == colours[3]


def test_compose_atlas_leaves_unused_cells_transparent_and_counts_rows_up(tmp_path):
    paths = []
    for index in range(3):
        path = tmp_path / f"t{index}.png"
        Image.new("RGBA", (8, 8), (255, 0, 0, 255)).save(path)
        paths.append(str(path))
    png, placed = compose_atlas(paths, 8, 2)
    atlas = _decode(png)
    assert atlas.size == (16, 16)  # 3 tiles in 2 columns need 2 rows
    assert placed == [(0, 0), (1, 0), (0, 1)]
    assert atlas.getpixel((12, 12)) == (0, 0, 0, 0)


def test_compose_atlas_rejects_nothing_to_place_and_nonsense_sizes(tmp_path):
    with pytest.raises(ValueError, match="at least one"):
        compose_atlas([], 16, 2)
    path = tmp_path / "t.png"
    Image.new("RGBA", (16, 16)).save(path)
    with pytest.raises(ValueError, match="positive"):
        compose_atlas([path], 16, 0)


def test_compose_atlas_names_the_file_with_the_wrong_size(tmp_path):
    good = tmp_path / "good.png"
    bad = tmp_path / "bad.png"
    Image.new("RGBA", (16, 16)).save(good)
    Image.new("RGBA", (8, 16)).save(bad)
    with pytest.raises(ValueError, match="bad.png"):
        compose_atlas([good, bad], 16, 2)


def test_sidecar_text_is_accepted_by_the_addons_parser(addon_spec):
    from spyrite_tile_gen.atlas import sidecar_text

    text = sidecar_text(["grass", "wall_top"], [(0, 0), (2, 1)], {"wall_top": ["XZ", "YZ"]})
    assert addon_spec.load_tile_names(text) == {
        "grass": {"xy": [0, 0], "planes": None, "tags": []},
        "wall_top": {"xy": [2, 1], "planes": ["XZ", "YZ"], "tags": []},
    }


def test_sidecar_text_rejects_bad_names_and_planes():
    from spyrite_tile_gen.atlas import sidecar_text

    placed = [(0, 0), (1, 0)]
    with pytest.raises(ValueError, match="unique"):
        sidecar_text(["a", "a"], placed)
    with pytest.raises(ValueError, match="letters, digits"):
        sidecar_text(["a", "b c"], placed)
    with pytest.raises(ValueError, match="one per|entries"):
        sidecar_text(["a"], placed)
    with pytest.raises(ValueError, match="planes_by_name"):
        sidecar_text(["a", "b"], placed, {"b": ["XX"]})
    with pytest.raises(ValueError, match="not in names"):
        sidecar_text(["a", "b"], placed, {"c": ["XY"]})

