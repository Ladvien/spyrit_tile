"""#143: invoke() assigned shading.type = 'MATERIAL' unguarded; Workbench rejects it with TypeError."""

import sys


class FakeShading:
    """Mimics View3DShading.type: assigning a value outside `accepted` raises TypeError like RNA."""

    def __init__(self, accepted, start="SOLID"):
        self._accepted = accepted
        self._type = start
        self.color_type = "MATERIAL"

    @property
    def type(self):
        return self._type

    @type.setter
    def type(self, value):
        if value not in self._accepted:
            raise TypeError('bpy_struct: item.attr = val: enum "%s" not found in %s' % (value, self._accepted))
        self._type = value


def _modal():
    name = next(n for n in sys.modules if n.endswith("spyrite_tile.sprytile_modal"))
    return sys.modules[name]


def test_material_offered_is_set():
    s = FakeShading(("WIREFRAME", "SOLID", "MATERIAL", "RENDERED"))
    assert _modal().set_material_preview_shading(s) is True
    assert s.type == "MATERIAL"
    assert s.color_type == "MATERIAL"


def test_material_rejected_leaves_shading_alone():
    for start in ("SOLID", "WIREFRAME", "RENDERED"):
        s = FakeShading(("WIREFRAME", "SOLID", "RENDERED"), start)
        assert _modal().set_material_preview_shading(s) is False
        assert s.type == start
        assert s.color_type == ("TEXTURE" if start == "SOLID" else "MATERIAL")


def test_already_material_is_untouched():
    s = FakeShading(("MATERIAL",), "MATERIAL")
    assert _modal().set_material_preview_shading(s) is True
