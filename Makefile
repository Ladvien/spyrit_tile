BLENDER ?= /Applications/Blender.app/Contents/MacOS/Blender
ADDON_DIR ?= addon/spyrite_tile
ADDON_ABS := $(abspath $(ADDON_DIR))

.PHONY: install-addon install-blended-plugin blender-test-deps test-pure test-blender

# Link the add-on (extension id `spyrite_tile`) into the user's Blender and
# enable it. A symlink, so the repo is what Blender loads; restart Blender
# after add-on edits. userpref.blend is snapshotted first because saving
# prefs is not reversible otherwise. The gate is Blender's exit status:
# --python-exit-code turns a refused enable or failed check into non-zero.
USERPREF ?= $(HOME)/Library/Application Support/Blender/5.2/config/userpref.blend
EXTENSION_DIR ?= $(HOME)/Library/Application Support/Blender/5.2/extensions/user_default/spyrite_tile
install-addon:
	test ! -e "$(USERPREF)" || cp "$(USERPREF)" "$(USERPREF).bak-$$(date +%Y%m%d%H%M%S)"
	mkdir -p "$$(dirname "$(EXTENSION_DIR)")"
	rm -rf "$(EXTENSION_DIR)"
	ln -s "$(ADDON_ABS)" "$(EXTENSION_DIR)"
	$(BLENDER) --background --python-exit-code 1 --python-expr \
		"import bpy, addon_utils; bpy.ops.preferences.addon_enable(module='bl_ext.user_default.spyrite_tile'); bpy.ops.wm.save_userpref(); check = addon_utils.check('bl_ext.user_default.spyrite_tile'); print('SPYRITE_CHECK', check); assert check == (True, True), 'spyrite_tile add-on not loaded and enabled: {}'.format(check)"

# Editable-install the Blender-side op plugin into blended's venv.
# NOTE: `uv sync` in /Users/ladvien/blended removes it; rerun this target afterwards.
install-blended-plugin:
	uv pip install --python /Users/ladvien/blended/.venv/bin/python -e packages/spyrite_tile_ops

# pytest for Blender's bundled Python 3.13, installed beside the repo.
blender-test-deps:
	uv pip install --target .blender-test-deps --python-version 3.13 pytest

test-pure:
	uv run --all-packages pytest tests/pure -q

# Run tests/blender inside a headless, isolated Blender: the add-on is
# symlinked into a throwaway BLENDER_USER_EXTENSIONS dir, so the user's real
# config is never touched. Exit status is Blender's (pytest's) status.
test-blender:
	tmp=$$(mktemp -d); \
	mkdir -p "$$tmp/user_default"; \
	ln -s "$(ADDON_ABS)" "$$tmp/user_default/spyrite_tile"; \
	BLENDER_USER_EXTENSIONS="$$tmp" $(BLENDER) --background --factory-startup --python-exit-code 1 --python scripts/run_blender_tests.py; \
	status=$$?; rm -rf "$$tmp"; exit $$status
