import bpy
import gpu
import blf
import bmesh
from bpy_extras import view3d_utils
from math import floor, ceil, copysign
from bpy.props import *
from mathutils import Vector, Matrix
from . import sprytile_utils, sprytile_modal, sprytile_builder
from gpu_extras.batch import batch_for_shader
from .sprytile_tools.tool_build import ToolBuild
from .sprytile_tools.tool_paint import ToolPaint
from . import sprytile_preview


# Shaders
#
# Blender 4.0 dropped the `bgl` module and 3.x deprecated raw GLSL sources for
# `gpu.types.GPUShader`, so the shaders are built through GPUShaderCreateInfo.
#
# The two custom shaders below only ever render into the palette's own
# offscreen buffer (plain RGBA8, written as sRGB values). Texture sampling there
# goes through texelFetch so a texel is read exactly, with repeat wrapping done
# by hand.
#
# Everything drawn straight into the viewport goes through Blender's built-in
# shaders instead. The window framebuffer is not a plain sRGB buffer on every
# backend: on Metal it is an sRGB-format target the hardware encodes into, so a
# colour written raw by a custom shader comes out brighter (tile 140 showed as
# 194). Only the built-in shaders know the target and convert for it, and the
# Python API gives custom shaders no way to ask. Nearest filtering for the
# palette quad is a sampler setting on the offscreen texture, GPUTexture.filter_mode.

flat_vertex_shader = '''
    void main()
    {
        v_color = i_color;
        gl_Position = u_modelViewProjectionMatrix * vec4(i_position, 0.0, 1.0);
    }
'''

flat_fragment_shader = '''
    void main()
    {
        frag_color = v_color;
    }
'''

image_vertex_shader = '''
    void main()
    {
        v_uv = i_uv;
        v_color = i_color;
        gl_Position = u_modelViewProjectionMatrix * vec4(i_position, 0.0, 1.0);
    }
'''

image_fragment_shader = '''
    void main()
    {
        ivec2 tex_size = textureSize(u_image, 0);
        // mod() keeps negative UVs positive, matching GL_REPEAT wrapping
        vec2 texel = mod(floor(v_uv * vec2(tex_size)), vec2(tex_size));
        vec4 col = texelFetch(u_image, ivec2(texel), 0) * v_color;

        // gpu.texture.from_image() hands back an SRGB8_A8 texture, so a fetch
        // decodes to linear. The old bgl path bound a plain RGBA8 texture and
        // got the stored bytes straight back, which is what the viewport wants.
        // Re-encode to undo the decode, exactly rather than with pow(1/2.2).
        if (u_encode_srgb != 0) {
            vec3 low = col.rgb * 12.92;
            vec3 high = 1.055 * pow(max(col.rgb, vec3(0.0)), vec3(1.0 / 2.4)) - 0.055;
            col.rgb = mix(low, high, step(vec3(0.0031308), col.rgb));
        }
        frag_color = col;
    }
'''

flat_shader = None
image_shader = None


def build_flat_shader():
    interface = gpu.types.GPUStageInterfaceInfo("sprytile_flat_interface")
    interface.smooth('VEC4', "v_color")

    info = gpu.types.GPUShaderCreateInfo()
    info.push_constant('MAT4', "u_modelViewProjectionMatrix")
    info.vertex_in(0, 'VEC2', "i_position")
    info.vertex_in(1, 'VEC4', "i_color")
    info.vertex_out(interface)
    info.fragment_out(0, 'VEC4', "frag_color")
    info.vertex_source(flat_vertex_shader)
    info.fragment_source(flat_fragment_shader)
    return gpu.shader.create_from_info(info)


def build_image_shader():
    interface = gpu.types.GPUStageInterfaceInfo("sprytile_image_interface")
    interface.smooth('VEC4', "v_color")
    interface.smooth('VEC2', "v_uv")

    info = gpu.types.GPUShaderCreateInfo()
    info.push_constant('MAT4', "u_modelViewProjectionMatrix")
    info.push_constant('INT', "u_encode_srgb")
    info.sampler(0, 'FLOAT_2D', "u_image")
    info.vertex_in(0, 'VEC2', "i_position")
    info.vertex_in(1, 'VEC4', "i_color")
    info.vertex_in(2, 'VEC2', "i_uv")
    info.vertex_out(interface)
    info.fragment_out(0, 'VEC4', "frag_color")
    info.vertex_source(image_vertex_shader)
    info.fragment_source(image_fragment_shader)
    return gpu.shader.create_from_info(info)


def ensure_shaders():
    """Build the shaders on first draw.

    Shaders cannot be compiled at import time, there is no GPU context yet when
    the addon is registered (and none at all in background mode).
    """
    global flat_shader, image_shader
    if flat_shader is None:
        flat_shader = build_flat_shader()
    if image_shader is None:
        image_shader = build_image_shader()
    return flat_shader is not None and image_shader is not None


def selection_outline(sel_min, sel_max, adjust=1):
    """The closed line strip around a tile selection, 5 points."""
    return [
        (sel_min[0] + adjust, sel_min[1] + adjust),
        (sel_max[0], sel_min[1]),
        (sel_max[0], sel_max[1]),
        (sel_min[0], sel_max[1]),
        (sel_min[0] + adjust, sel_min[1])
    ]


def draw_flat_viewport(primitive, positions, colors, matrix=None):
    """Draw coloured geometry into the viewport with the built-in FLAT_COLOR shader.

    positions are region pixels (a POST_PIXEL handler already has that matrix).
    matrix is an optional model matrix applied on top, for the palette's grid
    overlay.
    """
    shader = gpu.shader.from_builtin('FLAT_COLOR')
    batch = batch_for_shader(shader, primitive, {"pos": positions, "color": colors})
    with gpu.matrix.push_pop():
        if matrix is not None:
            gpu.matrix.multiply_matrix(matrix)
        batch.draw(shader)


def draw_image_viewport(primitive, positions, uvs, texture, color=(1, 1, 1, 1), indices=None, matrix=None):
    """Draw a texture into the viewport with the built-in IMAGE_COLOR shader, nearest filtered."""
    shader = gpu.shader.from_builtin('IMAGE_COLOR')
    # Pixel art: magnify by repeating texels instead of blurring them
    texture.filter_mode(False)
    batch = batch_for_shader(shader, primitive, {"pos": positions, "texCoord": uvs}, indices=indices)
    shader.bind()
    shader.uniform_sampler("image", texture)
    shader.uniform_float("color", color)
    with gpu.matrix.push_pop():
        if matrix is not None:
            gpu.matrix.multiply_matrix(matrix)
        batch.draw(shader)


class SprytileGuiData(bpy.types.PropertyGroup):
    zoom : FloatProperty(
        name="Spyrite Tile UI zoom",
        default=1.0
    )
    init_zoom_flag: BoolProperty(name="Spyrite Tile Initial Zoom Calc", default=False)
    use_mouse : BoolProperty(name="GUI use mouse")
    middle_btn : BoolProperty(name="GUI middle mouse")
    is_dirty : BoolProperty(name="Srpytile GUI redraw flag")
    palette_pos: IntVectorProperty(
        name="Spyrite Tile tile palette position",
        size=2,
        default=(0,0)
    )


class VIEW3D_OP_SprytileGui(bpy.types.Operator):
    bl_idname = "sprytile.gui_win"
    bl_label = "Spyrite Tile GUI"

    mouse_pt = None
    label_frames = 30
    is_selecting = False
    is_moving = False
    sel_start = None
    sel_origin = None
    is_running = False
    tile_ui_active = False
    out_of_region = False

    # These are filled in by setup_gpu_offscreen and handle_ui. They used to be
    # created on first assignment only, so a draw that happened before the
    # matching assignment raised AttributeError inside the draw handler and the
    # tile palette silently never appeared. cursor_grid_pos was the worst of
    # them: it is only set once the mouse moves over the palette, while
    # draw_offscreen reads it whenever sprytile_ui.use_mouse is on, which is a
    # value restored from the .blend file.
    cursor_grid_pos = None
    draw_callback = None
    offscreen = None
    texture = None
    texture_grid = None
    loaded_grid = None
    current_grid = -1
    tex_size = (128, 128)
    display_size = (128, 128)

    # Largest share of the viewport the tile palette may cover, on either axis
    max_palette_fraction = 0.9
    # Tileset size the stored zoom was calculated for, so a different sized
    # tileset gets a fresh zoom instead of inheriting one meant for the old one
    zoom_display_size = None
    # Margin preference values the stored palette position already accounts for
    applied_margin_prefs = None
    # Whether the palette has been anchored to its corner in this session
    anchored_this_session = False

    # macOS delivers Magic Mouse / trackpad scrolling as TRACKPADPAN and a pinch
    # as TRACKPADZOOM instead of WHEELUPMOUSE/WHEELDOWNMOUSE. Their amount is
    # the move from event.mouse_prev_x/y to event.mouse_x/y, in pixels for a
    # scroll. Events are small and frequent, so they accumulate until a whole
    # zoom step (or grid cycle) is reached.
    trackpad_pan_step = 30.0
    trackpad_zoom_step = 10.0
    trackpad_accum = 0.0

    @staticmethod
    def accumulate_trackpad(accum, delta, step_size):
        """Add delta to accum, return (whole steps, remaining accum), steps toward zero.

        A change of direction drops what was left of the opposite direction.
        """
        if accum * delta < 0:
            accum = 0.0
        accum += delta
        steps = int(accum / step_size)
        return steps, accum - steps * step_size

    build_previews = {
        'MAKE_FACE' : ToolBuild,
        'PAINT' : ToolPaint,
        'SET_NORMAL' : None,
        'FILL' : None
    } 

    # ================
    # Modal functions
    # ================
    @classmethod
    def poll(cls, context):
        return context.area.type == 'VIEW_3D'

    def invoke(self, context, event):
        if context.space_data.type != 'VIEW_3D':
            return {'CANCELLED'}
        if len(context.scene.sprytile_mats) < 1:
            return {'CANCELLED'}

        # Try to setup offscreen
        setup_off_return = VIEW3D_OP_SprytileGui.setup_offscreen(self, context)
        if setup_off_return is not None:
            return setup_off_return

        self.label_counter = 0
        self.get_zoom_level(context)
        self.prev_in_region = False
        self.handle_ui(context, event)

        # Add the draw handler call back, for drawing into viewport
        VIEW3D_OP_SprytileGui.handler_add(self, context, context.region)

        if context.area:
            context.area.tag_redraw()
        context.scene.sprytile_ui.is_dirty = True
        VIEW3D_OP_SprytileGui.is_running = True

        # Add actual modal handler
        context.window_manager.modal_handler_add(self)

        # Add timer event
        win_mgr = context.window_manager
        self.win_timer = win_mgr.event_timer_add(0.1, window=context.window)

        # Update view axis
        self.update_view_axis(context)

        return {'RUNNING_MODAL'}

    def update_view_axis(self, context):
        sprytile_data = context.scene.sprytile_data
        view_axis = sprytile_modal.VIEW3D_OP_SprytileModalTool.find_view_axis(context)
        if view_axis is not None:
            if view_axis != sprytile_data.normal_mode:
                sprytile_data.normal_mode = view_axis
                sprytile_data.lock_normal = False

    def modal(self, context, event):
        """Wrapper around the real modal body.

        An exception escaping modal() kills the operator without Blender ever
        calling exit(), which leaves is_running stuck True. The gizmo group only
        starts the GUI when is_running is False, so a single error used to make
        the tile palette disappear for the rest of the session.
        """
        try:
            return self.modal_run(context, event)
        except ReferenceError:
            # Operator data already freed, nothing left to clean up
            VIEW3D_OP_SprytileGui.is_running = False
            VIEW3D_OP_SprytileGui.tile_ui_active = False
            return {'CANCELLED'}
        except Exception:
            import traceback
            traceback.print_exc()
            try:
                self.exit(context)
            except Exception:
                VIEW3D_OP_SprytileGui.is_running = False
                VIEW3D_OP_SprytileGui.tile_ui_active = False
            return {'CANCELLED'}

    def modal_run(self, context, event):
        if context.area is None:
            self.exit(context)
            return {'CANCELLED'}

        cur_tool = sprytile_utils.get_current_tool(context)
        if cur_tool is None or not cur_tool.startswith("sprytile"):
            self.exit(context)
            return {'CANCELLED'}

        if context.mode != 'EDIT_MESH':
            self.exit(context)
            return {'CANCELLED'}
        elif not VIEW3D_OP_SprytileGui.is_running:
            VIEW3D_OP_SprytileGui.is_running = True

        # Check that the mouse is inside the region
        region = context.region
        coord = Vector((event.mouse_region_x, event.mouse_region_y))
        VIEW3D_OP_SprytileGui.out_of_region = coord.x < 0 or coord.y < 0 or coord.x > region.width or coord.y > region.height

        if event.type == 'TIMER':
            self.update_view_axis(context)

            if self.label_counter > 0:
                self.label_counter -= 1

        # Check if current_grid is different from current sprytile grid
        if context.object.sprytile_gridid != VIEW3D_OP_SprytileGui.current_grid:
            # Setup the offscreen texture for the new grid
            setup_off_return = VIEW3D_OP_SprytileGui.setup_offscreen(self, context)
            if setup_off_return is not None:
                return setup_off_return
            # Skip redrawing on this frame
            return {'PASS_THROUGH'}

        ret_val = self.handle_ui(context, event)
        VIEW3D_OP_SprytileGui.tile_ui_active = ret_val == 'RUNNING_MODAL'

        # Build the data that will be used by tool observers
        rv3d = context.region_data
        coord = event.mouse_region_x, event.mouse_region_y
        no_data = rv3d is None

        if no_data is False:
            # get the ray from the viewport and mouse
            ray_vector = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
            ray_origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)

            mode = bpy.context.scene.sprytile_data.paint_mode
            if VIEW3D_OP_SprytileGui.build_previews[mode]:
                sprytile_builder.TileBuilder.verify_bmesh_layers(bmesh.from_edit_mesh(context.object.data))
                VIEW3D_OP_SprytileGui.build_previews[mode].build_preview(context, context.scene, ray_origin, ray_vector)
            else:
                sprytile_preview.set_preview_data(None, None)

        context.scene.sprytile_ui.is_dirty = False
        context.area.tag_redraw()
        return {ret_val}

    def cancel(self, context):
        """Blender calls this, not modal(), when it drops a running modal operator.

        Loading another file (open, revert, new) does that to the palette without
        ever reaching exit(). is_running then stayed True, the gizmo group only
        starts the GUI when it is False, and the draw handler kept the old
        tileset, so the palette stayed gone until Blender was restarted (#135).
        """
        self.exit(context)

    def exit(self, context):
        VIEW3D_OP_SprytileGui.handler_remove(self, context)
        VIEW3D_OP_SprytileGui.free_offscreen()
        VIEW3D_OP_SprytileGui.is_running = False
        VIEW3D_OP_SprytileGui.tile_ui_active = False
        if hasattr(self, "win_timer"):
            context.window_manager.event_timer_remove(self.win_timer)
        if context.area is not None:
            context.area.tag_redraw()

    def set_zoom_level(self, context, zoom_shift):
        region = context.region
        zoom_level = context.scene.sprytile_ui.zoom
        zoom_level = self.calc_zoom(context, zoom_level, zoom_shift)
        display_size = VIEW3D_OP_SprytileGui.display_size

        calc_size = round(display_size[0] * zoom_level), round(display_size[1] * zoom_level)
        height_min = min(128, display_size[1])
        while calc_size[1] < height_min:
            zoom_level = self.calc_zoom(context, zoom_level, 1)
            calc_size = round(display_size[0] * zoom_level), round(display_size[1] * zoom_level)

        while calc_size[0] > region.width or calc_size[1] > region.height:
            zoom_level = self.calc_zoom(context, zoom_level, -1)
            calc_size = round(display_size[0] * zoom_level), round(display_size[1] * zoom_level)

        # Before setting new zoom, calculate palette position
        display_offset, display_size, size_half, display_min, display_max = self.calc_palette_pos(context)
        # Record if snapping to edges
        is_snap_min_x = display_offset.x == display_min.x
        is_snap_min_y = display_offset.y == display_min.y
        is_snap_max_x = display_offset.x == display_max.x
        is_snap_max_y = display_offset.y == display_max.y

        # Set zoom level, then recalculate palette position
        context.scene.sprytile_ui.zoom = zoom_level
        display_offset, display_size, size_half, display_min, display_max = self.calc_palette_pos(context)

        # Snap to edges if previously snapped
        if is_snap_min_x:
            display_offset.x = display_min.x
        if is_snap_min_y:
            display_offset.y = display_min.y
        if is_snap_max_x:
            display_offset.x = display_max.x
        if is_snap_max_y:
            display_offset.y = display_max.y
        
        context.scene.sprytile_ui.palette_pos[0] = int(display_offset.x)
        context.scene.sprytile_ui.palette_pos[1] = int(display_offset.y)

    def calc_zoom(self, context, zoom, steps):
        if steps == 0:
            return zoom

        step = copysign(1, steps)
        count = 0
        while count != steps:
            # Zooming in
            if steps > 0:
                if zoom >= 2.0:
                    zoom += 0.5
                elif zoom >= 0.25:
                    zoom += 0.25
                else:
                    zoom *= 2
            # Zooming out
            else:
                if zoom <= 0.25:
                    zoom *= 0.5
                elif zoom <= 2.0:
                    zoom -= 0.25
                else:
                    zoom -= 0.5
            count += step

        return VIEW3D_OP_SprytileGui.clamp_zoom(context, zoom)

    @staticmethod
    def mouse_over_ui_region(context, event):
        """See sprytile_utils.mouse_over_ui_region, kept here for convenience."""
        return sprytile_utils.mouse_over_ui_region(context, event)

    @staticmethod
    def get_region_padding(context):
        """Space the palette has to stay clear of, per edge.

        With region overlap on (the default) the WINDOW region spans the whole
        area, so the toolbar, sidebar and headers are drawn on top of it. The
        palette used to be positioned against the raw region edges and ended up
        underneath them, swallowing clicks meant for those buttons. Measure the
        real regions and keep out of their way. Region alignment is used rather
        than region type so a header moved to the bottom is handled too.
        """
        edge = 5
        padding = {'left': edge, 'right': edge, 'top': edge, 'bottom': edge}

        area = context.area
        if area is None or not context.preferences.system.use_region_overlap:
            return padding

        for region in area.regions:
            if region.type == 'WINDOW':
                continue
            # Collapsed regions report a size of 1
            if region.width <= 1 or region.height <= 1:
                continue

            if region.alignment == 'LEFT':
                padding['left'] = max(padding['left'], region.width + edge)
            elif region.alignment == 'RIGHT':
                padding['right'] = max(padding['right'], region.width + edge)
            elif region.alignment == 'TOP':
                padding['top'] += region.height
            elif region.alignment == 'BOTTOM':
                padding['bottom'] += region.height

        # User configurable extra room, on top of the measured panels
        try:
            addon_prefs = context.preferences.addons[__package__].preferences
            padding['left'] += max(0, addon_prefs.palette_pad_left)
            padding['top'] += max(0, addon_prefs.palette_pad_top)
        except (KeyError, AttributeError):
            pass

        return padding

    @staticmethod
    def get_available_area(context):
        """Viewport space left for the palette once the panels are accounted for."""
        region = context.region
        padding = VIEW3D_OP_SprytileGui.get_region_padding(context)
        return (region.width - padding['left'] - padding['right'],
                region.height - padding['top'] - padding['bottom'])

    @staticmethod
    def clamp_zoom(context, zoom):
        """Cap the zoom so the palette fits in the space left by the panels.

        Applies to both axes, so a wide tileset cannot run off the sides the
        way the old height only check allowed.
        """
        display_size = VIEW3D_OP_SprytileGui.display_size
        if display_size[0] <= 0 or display_size[1] <= 0:
            return zoom

        avail_width, avail_height = VIEW3D_OP_SprytileGui.get_available_area(context)
        if avail_width <= 0 or avail_height <= 0:
            return zoom

        limit = VIEW3D_OP_SprytileGui.max_palette_fraction
        max_zoom = min(avail_width * limit / display_size[0],
                       avail_height * limit / display_size[1])
        return min(zoom, max(max_zoom, 0.001))

    def get_zoom_level(self, context):
        region = context.region
        sprytile_ui = context.scene.sprytile_ui
        display_size = VIEW3D_OP_SprytileGui.display_size

        if display_size[0] <= 0 or display_size[1] <= 0:
            return

        # Refit on the first call of a session and whenever the tileset size
        # changes, then leave the zoom alone so scrolling sticks. init_zoom_flag
        # alone used to make this a once per file decision, so a new tileset
        # inherited the zoom picked for the previous one.
        #
        # zoom_display_size is runtime only on purpose, which means the refit
        # also happens on every file load. That is wanted: a zoom saved from a
        # small window would otherwise keep the palette tiny forever, and files
        # written before the clamp write-back was fixed carry exactly that.
        if sprytile_ui.init_zoom_flag and \
                VIEW3D_OP_SprytileGui.zoom_display_size == tuple(display_size):
            return

        # Fit against the smaller free dimension, not the region height. On a
        # landscape monitor the height is the short side, so the old height only
        # target made a square atlas come out tiny.
        avail_width, avail_height = VIEW3D_OP_SprytileGui.get_available_area(context)
        limit = min(max(avail_width, 1), max(avail_height, 1))
        try:
            addon_prefs = context.preferences.addons[__package__].preferences
            fraction = max(10, min(90, addon_prefs.palette_size_percent)) / 100.0
        except (KeyError, AttributeError):
            fraction = 0.5
        target = limit * fraction

        # Solve for the zoom directly instead of stepping down through
        # calc_zoom. That ladder subtracts 0.25 below a zoom of 2, which is a
        # huge step for a 2048 atlas whose useful range is roughly 0.05 to 0.4,
        # so stepping overshot the target badly. The ladder stays in use for
        # scroll wheel zooming, where its feel is the point.
        longest = max(display_size[0], display_size[1])
        zoom_level = target / longest

        sprytile_ui.zoom = zoom_level
        sprytile_ui.init_zoom_flag = True
        VIEW3D_OP_SprytileGui.zoom_display_size = tuple(display_size)

    @staticmethod
    def get_margin_prefs(context):
        """The user's palette margin preferences, as (left, top)."""
        try:
            addon_prefs = context.preferences.addons[__package__].preferences
            return (max(0, addon_prefs.palette_pad_left),
                    max(0, addon_prefs.palette_pad_top))
        except (KeyError, AttributeError):
            return (0, 0)

    def apply_margin_change(self, context, sprytile_ui):
        """Shift the palette by however much the margin preferences moved.

        Clamping alone only ever pushes the palette inwards, so lowering a
        margin again would leave it where it was. Tracking the applied values
        and moving by the difference makes the setting work both ways.
        """
        margins = VIEW3D_OP_SprytileGui.get_margin_prefs(context)
        applied = VIEW3D_OP_SprytileGui.applied_margin_prefs

        if applied is None:
            VIEW3D_OP_SprytileGui.applied_margin_prefs = margins
            return
        if applied == margins:
            return

        # Top margin grows downwards, the stored position counts up from the bottom
        sprytile_ui.palette_pos[0] += margins[0] - applied[0]
        sprytile_ui.palette_pos[1] -= margins[1] - applied[1]
        VIEW3D_OP_SprytileGui.applied_margin_prefs = margins

    def calc_palette_pos(self, context):
        sprytile_ui = context.scene.sprytile_ui

        # palette_pos is region relative, so a value saved on one monitor is
        # meaningless on another. Dragging the window used to clamp it inwards
        # and that clamped value was persisted, so the palette crept towards the
        # middle of the viewport and never returned to its corner. Anchor once
        # per session and let an explicit drag move it from there.
        if not VIEW3D_OP_SprytileGui.anchored_this_session:
            VIEW3D_OP_SprytileGui.anchored_this_session = True
            sprytile_ui.palette_pos = (0, 0)
            # Files saved before the texture node filter carry entries for
            # materials that cannot be tilesets, clear them without waiting for
            # the user to press Validate Tile Grids
            sprytile_utils.prune_textureless_grids(context)

        self.apply_margin_change(context, sprytile_ui)

        # The size cap is presentation only and must NOT be written back.
        # clamp_zoom only ever lowers the value, so persisting it made a
        # temporarily small viewport permanent: dragging the Blender window to a
        # smaller monitor shrank the palette and moving back never restored it.
        # sprytile_ui.zoom stays the zoom the user picked, this is what fits.
        display_scale = VIEW3D_OP_SprytileGui.clamp_zoom(context, sprytile_ui.zoom)
        display_size = VIEW3D_OP_SprytileGui.display_size
        display_size = round(display_size[0] * display_scale), round(display_size[1] * display_scale)
        
        padding = VIEW3D_OP_SprytileGui.get_region_padding(context)

        size_half = Vector((int(display_size[0]/2), int(display_size[1]/2)))

        display_min = Vector((padding['left'] + size_half.x,
                              padding['bottom'] + size_half.y))
        display_max = Vector((context.region.width - padding['right'] - size_half.x,
                              context.region.height - padding['top'] - size_half.y))

        display_offset = Vector((context.scene.sprytile_ui.palette_pos[0], context.scene.sprytile_ui.palette_pos[1]))

        display_offset.x = max(display_offset.x, display_min.x)
        display_offset.x = min(display_offset.x, display_max.x)
        display_offset.y = max(display_offset.y, display_min.y)
        display_offset.y = min(display_offset.y, display_max.y)

        # Deliberately NOT written back. Clamping only ever moves the palette
        # inwards, so persisting it made a temporarily smaller viewport permanent
        # and the palette drifted towards the centre. Explicit drags and
        # set_zoom_level are what store a position.

        return display_offset, display_size, size_half, display_min, display_max

    def handle_ui(self, context, event):
        if event.type in {'LEFTMOUSE', 'MOUSEMOVE'}:
            self.mouse_pt = Vector((event.mouse_region_x, event.mouse_region_y))

        mouse_pt = self.mouse_pt

        region = context.region
        obj = context.object
        ret_val = 'RUNNING_MODAL'

        tilegrid = sprytile_utils.get_grid(context, obj.sprytile_gridid)
        tex_size = VIEW3D_OP_SprytileGui.tex_size
        
        display_offset, display_size, size_half, display_min, display_max = self.calc_palette_pos(context)
        
        gui_min = display_offset - size_half
        gui_max = display_offset + size_half

        self.gui_min = gui_min
        self.gui_max = gui_max
        
        reject_region = context.space_data.type != 'VIEW_3D' or region.type != 'WINDOW'
        if event is None or reject_region:
            ret_val = 'PASS_THROUGH'
            return ret_val

        if event.type == 'MIDDLEMOUSE':
            context.scene.sprytile_ui.middle_btn = event.value != 'RELEASE'
        elif context.scene.sprytile_ui.middle_btn and not VIEW3D_OP_SprytileGui.is_moving:
            # Orbiting or zooming hands the events to Blender's own navigation
            # modal, which consumes the middle mouse release. The flag then
            # stayed on forever and draw_to_viewport kept skipping the preview
            # tile. Any plain mouse move means navigation is over.
            if event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'} or event.value == 'RELEASE':
                context.scene.sprytile_ui.middle_btn = False

        # Blender's own panels win over the palette, whatever is drawn beneath
        if VIEW3D_OP_SprytileGui.mouse_over_ui_region(context, event):
            context.scene.sprytile_ui.use_mouse = False
            return 'PASS_THROUGH'

        if mouse_pt is not None and event.type in {'MOUSEMOVE'}:
            mouse_in_region = 0 <= mouse_pt.x <= region.width and 0 <= mouse_pt.y <= region.height
            mouse_in_gui = gui_min.x <= mouse_pt.x <= gui_max.x and gui_min.y <= mouse_pt.y <= gui_max.y

            context.scene.sprytile_ui.use_mouse = mouse_in_gui
            self.prev_in_region = mouse_in_region

        if mouse_pt is not None and context.scene.sprytile_ui.middle_btn and VIEW3D_OP_SprytileGui.is_moving:
           context.scene.sprytile_ui.use_mouse = True 

        if event.type in {'TRACKPADPAN', 'TRACKPADZOOM'}:
            # use_mouse is only refreshed by MOUSEMOVE, a trackpad gesture is
            # judged by where the pointer is now so it can never be swallowed
            # when the pointer is outside the palette (it then navigates the 3D view)
            gesture_pt = Vector((event.mouse_region_x, event.mouse_region_y))
            over_gui = gui_min.x <= gesture_pt.x <= gui_max.x and gui_min.y <= gesture_pt.y <= gui_max.y
            context.scene.sprytile_ui.use_mouse = over_gui
            if not over_gui:
                VIEW3D_OP_SprytileGui.trackpad_accum = 0.0

        if context.scene.sprytile_ui.use_mouse is False:
            ret_val = 'PASS_THROUGH'
            return ret_val

        if event.type in {'WHEELUPMOUSE', 'WHEELDOWNMOUSE'}:
            if event.ctrl is False:
                zoom_shift = 1 if event.type == 'WHEELUPMOUSE' else -1
                self.set_zoom_level(context, zoom_shift)
            else:
                direction = 1 if 'DOWN' in event.type else -1
                bpy.ops.sprytile.grid_cycle('INVOKE_REGION_WIN', direction=direction)
                self.label_counter = VIEW3D_OP_SprytileGui.label_frames

        if event.type in {'TRACKPADPAN', 'TRACKPADZOOM'}:
            delta_x = event.mouse_x - event.mouse_prev_x
            delta_y = event.mouse_y - event.mouse_prev_y
            if event.type == 'TRACKPADPAN':
                # Scrolling up zooms in and ctrl+scroll cycles grids, as the wheel does
                steps, VIEW3D_OP_SprytileGui.trackpad_accum = self.accumulate_trackpad(
                    VIEW3D_OP_SprytileGui.trackpad_accum, delta_y, self.trackpad_pan_step)
            else:
                # Pinching outwards zooms in
                steps, VIEW3D_OP_SprytileGui.trackpad_accum = self.accumulate_trackpad(
                    VIEW3D_OP_SprytileGui.trackpad_accum, delta_x + delta_y, self.trackpad_zoom_step)
            if steps != 0:
                if event.ctrl is False:
                    self.set_zoom_level(context, steps)
                elif event.type == 'TRACKPADPAN':
                    for _ in range(abs(steps)):
                        bpy.ops.sprytile.grid_cycle('INVOKE_REGION_WIN', direction=-1 if steps > 0 else 1)
                    self.label_counter = VIEW3D_OP_SprytileGui.label_frames

        if mouse_pt is not None and event.type in {'LEFTMOUSE', 'MIDDLEMOUSE', 'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
            click_pos = Vector((mouse_pt.x - gui_min.x, mouse_pt.y - gui_min.y))
            ratio_pos = Vector((click_pos.x / display_size[0], click_pos.y / display_size[1]))
            tex_pos = Vector((ratio_pos.x * tex_size[0], ratio_pos.y * tex_size[1], 0))
            # Apply grid matrix to tex_pos
            grid_matrix = sprytile_utils.get_grid_matrix(VIEW3D_OP_SprytileGui.loaded_grid)
            tex_pos = grid_matrix.inverted() @ tex_pos

            grid_max = Vector((ceil(tex_size[0]/tilegrid.grid[0])-1, ceil(tex_size[1]/tilegrid.grid[1])-1))
            cell_size = Vector((
                tilegrid.grid[0] + (tilegrid.padding[0] * 2) + tilegrid.margin[1] + tilegrid.margin[3],
                tilegrid.grid[1] + (tilegrid.padding[1] * 2) + tilegrid.margin[0] + tilegrid.margin[2]
            ))
            grid_pos = Vector((tex_pos.x / cell_size.x, tex_pos.y / cell_size.y))
            grid_pos.x = max(0, min(grid_max.x, floor(grid_pos.x)))
            grid_pos.y = max(0, min(grid_max.y, floor(grid_pos.y)))

            VIEW3D_OP_SprytileGui.cursor_grid_pos = grid_pos

            # Code for moving tile selection around
            if event.type == 'LEFTMOUSE' and event.value == 'PRESS' and VIEW3D_OP_SprytileGui.is_selecting is False:
                addon_prefs = context.preferences.addons[__package__].preferences
                move_mod_pressed = False
                #if addon_prefs.tile_sel_move_key == 'Alt':
                #    move_mod_pressed = event.alt
                #if addon_prefs.tile_sel_move_key == 'Ctrl':
                #    move_mod_pressed = event.ctrl
                #if addon_prefs.tile_sel_move_key == 'Shift':
                #    move_mod_pressed = event.shift

                VIEW3D_OP_SprytileGui.is_selecting = move_mod_pressed is False
                VIEW3D_OP_SprytileGui.is_moving = move_mod_pressed is True
                if VIEW3D_OP_SprytileGui.is_selecting or VIEW3D_OP_SprytileGui.is_moving:
                    VIEW3D_OP_SprytileGui.sel_start = grid_pos
                    VIEW3D_OP_SprytileGui.sel_origin = (tilegrid.tile_selection[0], tilegrid.tile_selection[1])

            if VIEW3D_OP_SprytileGui.is_moving and event.type == 'LEFTMOUSE':
                move_delta = Vector((grid_pos.x - VIEW3D_OP_SprytileGui.sel_start.x, grid_pos.y - VIEW3D_OP_SprytileGui.sel_start.y))
                # Restrict movement inside tile grid
                move_min = (VIEW3D_OP_SprytileGui.sel_origin[0] + move_delta.x,
                            VIEW3D_OP_SprytileGui.sel_origin[1] + move_delta.y)
                if move_min[0] < 0:
                    move_delta.x -= move_min[0]
                if move_min[1] < 0:
                    move_delta.y -= move_min[1]

                move_max = (move_min[0] + tilegrid.tile_selection[2] - 1,
                            move_min[1] + tilegrid.tile_selection[3] - 1)
                if move_max[0] > grid_max.x:
                    move_delta.x -= (move_max[0] - grid_max.x)
                if move_max[1] > grid_max.y:
                    move_delta.y -= (move_max[1] - grid_max.y)

                tilegrid.tile_selection[0] = int(VIEW3D_OP_SprytileGui.sel_origin[0] + move_delta.x)
                tilegrid.tile_selection[1] = int(VIEW3D_OP_SprytileGui.sel_origin[1] + move_delta.y)
            # End tile selection movement code

            # Code for moving tile palette around
            if context.scene.sprytile_ui.middle_btn:
                if event.shift and not VIEW3D_OP_SprytileGui.is_moving:
                    VIEW3D_OP_SprytileGui.is_moving = True
                    VIEW3D_OP_SprytileGui.sel_origin = display_offset - mouse_pt
                if VIEW3D_OP_SprytileGui.is_moving:
                    display_offset = mouse_pt + VIEW3D_OP_SprytileGui.sel_origin
                    
                    display_offset.x = max(display_offset.x, display_min.x) 
                    display_offset.x = min(display_offset.x, display_max.x)
                    display_offset.y = max(display_offset.y, display_min.y)
                    display_offset.y = min(display_offset.y, display_max.y) 
                    
                    context.scene.sprytile_ui.palette_pos[0] = int(display_offset.x)
                    context.scene.sprytile_ui.palette_pos[1] = int(display_offset.y)
            # End tile palette movement code

            if VIEW3D_OP_SprytileGui.is_selecting:
                sel_min = Vector((
                    min(grid_pos.x, VIEW3D_OP_SprytileGui.sel_start.x),
                    min(grid_pos.y, VIEW3D_OP_SprytileGui.sel_start.y)
                ))
                sel_max = Vector((
                    max(grid_pos.x, VIEW3D_OP_SprytileGui.sel_start.x),
                    max(grid_pos.y, VIEW3D_OP_SprytileGui.sel_start.y)
                ))

                tilegrid.tile_selection[0] = int(sel_min.x)
                tilegrid.tile_selection[1] = int(sel_min.y)
                tilegrid.tile_selection[2] = int(sel_max.x - sel_min.x) + 1
                tilegrid.tile_selection[3] = int(sel_max.y - sel_min.y) + 1

            do_release = event.type in {'LEFTMOUSE', 'MIDDLEMOUSE'} and event.value == 'RELEASE'
            if do_release and (VIEW3D_OP_SprytileGui.is_selecting or VIEW3D_OP_SprytileGui.is_moving):
                VIEW3D_OP_SprytileGui.is_selecting = False
                VIEW3D_OP_SprytileGui.is_moving = False
                VIEW3D_OP_SprytileGui.sel_start = None
                VIEW3D_OP_SprytileGui.sel_origin = None
        # End mouse processing

        # Cycle through grids on same material when right click
        if event.type == 'RIGHTMOUSE' and event.value == 'PRESS':
            bpy.ops.sprytile.grid_cycle()
            self.label_counter = VIEW3D_OP_SprytileGui.label_frames

        return ret_val

    # ==================
    # Actual GUI drawing
    # ==================
    @staticmethod
    def setup_offscreen(self, context):
        # A new offscreen is built every time the grid changes, release the old one
        VIEW3D_OP_SprytileGui.free_offscreen()
        VIEW3D_OP_SprytileGui.offscreen = VIEW3D_OP_SprytileGui.setup_gpu_offscreen(self, context)
        if VIEW3D_OP_SprytileGui.offscreen:
            VIEW3D_OP_SprytileGui.texture = VIEW3D_OP_SprytileGui.offscreen.texture_color
        else:
            self.report({'ERROR'}, "Error initializing offscreen buffer. More details in the console")
            return {'CANCELLED'}
        return None

    @staticmethod
    def setup_gpu_offscreen(self, context):
        obj = context.object

        sprytile_list = context.scene.sprytile_list
        grid_id = sprytile_list.display[sprytile_list.idx].grid_id

        # set current object grid_id to selected grid_id
        if grid_id != obj.sprytile_gridid:
            obj.sprytile_gridid = grid_id

        # Get the current tile grid, to fetch the texture size to render to
        tilegrid = sprytile_utils.get_grid(context, grid_id)
        target_img = None

        tex_size = 128, 128
        if tilegrid is not None:
            target_img = sprytile_utils.get_grid_texture(obj, tilegrid)
            if target_img is not None:
                tex_size = target_img.size[0], target_img.size[1]

        try:
            offscreen = gpu.types.GPUOffScreen(tex_size[0], tex_size[1])
        except Exception as e:
            print(e)
            VIEW3D_OP_SprytileGui.clear_offscreen(self)
            offscreen = None

        if target_img is None:
            VIEW3D_OP_SprytileGui.texture_grid = None
        else:
            VIEW3D_OP_SprytileGui.texture_grid = target_img.name
        VIEW3D_OP_SprytileGui.tex_size = tex_size
        VIEW3D_OP_SprytileGui.display_size = tex_size
        VIEW3D_OP_SprytileGui.current_grid = grid_id
        VIEW3D_OP_SprytileGui.loaded_grid = tilegrid
        self.get_zoom_level(context)
        return offscreen

    @staticmethod
    def clear_offscreen(self):
        VIEW3D_OP_SprytileGui.texture = None

    @staticmethod
    def free_offscreen():
        """Release the offscreen buffer and the texture that views into it"""
        offscreen = getattr(VIEW3D_OP_SprytileGui, "offscreen", None)
        # Drop the texture first, it is a view into the offscreen colour buffer
        VIEW3D_OP_SprytileGui.texture = None
        VIEW3D_OP_SprytileGui.offscreen = None
        if offscreen is not None:
            try:
                offscreen.free()
            except Exception as err:
                print("Sprytile: could not free offscreen buffer:", err)

    @staticmethod
    def handler_add(self, context, region):
        space = bpy.types.SpaceView3D
        VIEW3D_OP_SprytileGui.draw_callback = space.draw_handler_add(self.draw_callback_handler,
                                                           (self, context, region),
                                                           'WINDOW', 'POST_PIXEL')

    @staticmethod
    def handler_remove(self, context):
        if hasattr(VIEW3D_OP_SprytileGui, "draw_callback") and VIEW3D_OP_SprytileGui.draw_callback is not None:
            bpy.types.SpaceView3D.draw_handler_remove(VIEW3D_OP_SprytileGui.draw_callback, 'WINDOW')
        VIEW3D_OP_SprytileGui.draw_callback = None

    @staticmethod
    def draw_callback_handler(self, context, region):
        """Callback handler"""
        if not VIEW3D_OP_SprytileGui.is_running:
            return

        if not ensure_shaders():
            return

        sprytile_data = context.scene.sprytile_data
        show_extra = sprytile_data.show_extra or sprytile_data.show_overlay
        tilegrid = sprytile_utils.get_selected_grid(context)

        if tilegrid is None or VIEW3D_OP_SprytileGui.loaded_grid is None or VIEW3D_OP_SprytileGui.texture_grid is None or bpy.data.images.find(VIEW3D_OP_SprytileGui.texture_grid) < 0:
            return

        # Nothing to draw into or from if the offscreen buffer failed to build
        if VIEW3D_OP_SprytileGui.offscreen is None or VIEW3D_OP_SprytileGui.texture is None:
            return

        try:
            gui_min = self.gui_min
            gui_max = self.gui_max
            label_counter = self.label_counter
        except ReferenceError:
            # The operator is gone but its draw handler is still registered,
            # drop the handler instead of raising on every redraw
            VIEW3D_OP_SprytileGui.is_running = False
            VIEW3D_OP_SprytileGui.handler_remove(None, context)
            return

        region = context.region
        rv3d = context.region_data

        middle_btn = context.scene.sprytile_ui.middle_btn

        VIEW3D_OP_SprytileGui.draw_offscreen(context)
        VIEW3D_OP_SprytileGui.draw_to_viewport(gui_min, gui_max, show_extra,
                                     label_counter, tilegrid, sprytile_data,
                                     context.scene.cursor.location, region, rv3d,
                                     middle_btn, context)

    @staticmethod
    def draw_selection(mvpMat, color, sel_min, sel_max, adjust=1):
        """Selection outline into the offscreen buffer (custom flat shader)."""
        flat_shader.bind()

        sel_vtx = selection_outline(sel_min, sel_max, adjust)
        vercol = (color,)*5

        batch = batch_for_shader(flat_shader, 'LINE_STRIP', { "i_position": sel_vtx, "i_color": vercol})
        flat_shader.uniform_float("u_modelViewProjectionMatrix", mvpMat)
        batch.draw(flat_shader)

    @staticmethod
    def draw_full_tex_quad(pos, mvpMat, texture, encodeSrgb = False, uvs = None, color = (1, 1, 1, 1)):
        image_shader.bind()

        vercol = (color,)*4
        if not uvs:
            uvs = ((0,0),(1,0),(0,1),(1,1))
        batch = batch_for_shader(image_shader, 'TRI_STRIP', { "i_position": pos, "i_color": vercol, "i_uv": uvs})
        image_shader.uniform_float("u_modelViewProjectionMatrix", mvpMat)
        image_shader.uniform_sampler("u_image", texture)
        image_shader.uniform_int("u_encode_srgb", 1 if encodeSrgb else 0)
        batch.draw(image_shader)

    @staticmethod
    def draw_offscreen(context):
        """Draw the GUI into the offscreen texture"""
        offscreen = VIEW3D_OP_SprytileGui.offscreen
        target_img = VIEW3D_OP_SprytileGui.texture_grid
        tex_size = VIEW3D_OP_SprytileGui.tex_size
        projection_mat = sprytile_utils.get_ortho2D_matrix(0, tex_size[0], 0, tex_size[1])

        offscreen.bind()
        framebuffer = gpu.state.active_framebuffer_get()
        framebuffer.clear(color=(0, 0, 0, 0.5))
        gpu.state.depth_test_set('NONE')
        gpu.state.blend_set('ALPHA')

        target_img = bpy.data.images[bpy.data.images.find(target_img)]
        # gpu.texture.from_image replaces Image.gl_load()/bindcode, both removed in 4.0.
        # Blender keeps the GPU texture cached on the image, so this is cheap per draw
        # and picks up image reloads on its own.
        grid_texture = gpu.texture.from_image(target_img)
        quad_pos = ((0, 0), (tex_size[0], 0), (0, tex_size[1]), (tex_size[0], tex_size[1]))

        VIEW3D_OP_SprytileGui.draw_full_tex_quad(quad_pos, projection_mat, grid_texture, True)

        # Translate the gl context by grid matrix
        grid_matrix = sprytile_utils.get_grid_matrix(VIEW3D_OP_SprytileGui.loaded_grid)
        matrix_vals = [(grid_matrix[i][0], grid_matrix[i][1], grid_matrix[i][2], grid_matrix[i][3]) for i in range(4)]
        mvp_mat = projection_mat @ Matrix(matrix_vals)

        # Get data for drawing additional overlays
        grid_size = VIEW3D_OP_SprytileGui.loaded_grid.grid
        padding = VIEW3D_OP_SprytileGui.loaded_grid.padding
        margin = VIEW3D_OP_SprytileGui.loaded_grid.margin
        curr_sel = VIEW3D_OP_SprytileGui.loaded_grid.tile_selection
        is_pixel_grid = sprytile_utils.grid_is_single_pixel(VIEW3D_OP_SprytileGui.loaded_grid)
        is_use_mouse = context.scene.sprytile_ui.use_mouse
        is_selecting = VIEW3D_OP_SprytileGui.is_selecting

        gpu.state.line_width_set(1)

        # Draw box for currently selected tile(s)
        # Pixel grid selection is drawn in draw_tile_select_ui
        sprytile_data = context.scene.sprytile_data
        is_not_base_layer = sprytile_data.work_layer != "BASE"
        draw_outline = sprytile_data.outline_preview or is_not_base_layer
        if draw_outline and is_selecting is False and not is_pixel_grid:
            if is_not_base_layer:
                sel_color = (0.98, 0.94, 0.12, 1.0)
            elif VIEW3D_OP_SprytileGui.is_moving:
                sel_color = (1.0, 0.0, 0.0, 1.0)
            else:
                sel_color = (1.0, 1.0, 1.0, 1.0)
            curr_sel_min, curr_sel_max = VIEW3D_OP_SprytileGui.get_sel_bounds(
                                                    grid_size, padding, margin,
                                                    curr_sel[0], curr_sel[1],
                                                    curr_sel[2], curr_sel[3]
                                                )
            VIEW3D_OP_SprytileGui.draw_selection(mvp_mat, sel_color, curr_sel_min, curr_sel_max)

        # Inside gui, draw appropriate selection for under mouse
        if is_use_mouse and is_selecting is False and VIEW3D_OP_SprytileGui.cursor_grid_pos is not None:

            cursor_pos = VIEW3D_OP_SprytileGui.cursor_grid_pos
            # In pixel grid, draw cross hair
            if is_pixel_grid and VIEW3D_OP_SprytileGui.is_moving is False:
                flat_shader.bind()
                flat_shader.uniform_float("u_modelViewProjectionMatrix", mvp_mat)
                vtx_pos = ((0, int(cursor_pos.y + 1)), (tex_size[0], int(cursor_pos.y + 1)))
                vtx_col = ((1.0, 1.0, 1.0, 0.5),)*2
                batch = batch_for_shader(flat_shader, 'LINES', { "i_position": vtx_pos, "i_color": vtx_col})
                flat_shader.uniform_float("u_modelViewProjectionMatrix", mvp_mat)
                batch.draw(flat_shader)

                vtx_pos = ((int(cursor_pos.x + 1), 0), (int(cursor_pos.x + 1), tex_size[1]))
                batch = batch_for_shader(flat_shader, 'LINES', { "i_position": vtx_pos, "i_color": vtx_col})
                batch.draw(flat_shader)
            # Draw box around selection
            elif VIEW3D_OP_SprytileGui.is_moving is False:
                cursor_min, cursor_max = VIEW3D_OP_SprytileGui.get_sel_bounds(grid_size, padding, margin,
                                                                    int(cursor_pos.x), int(cursor_pos.y),)
                VIEW3D_OP_SprytileGui.draw_selection(mvp_mat, (1.0, 0.0, 0.0, 1.0), cursor_min, cursor_max)

        offscreen.unbind()

    @staticmethod
    def get_sel_bounds(grid_size, padding, margin, x, y, size_x=1, size_y=1):
        total_size = Vector(
            (
                grid_size[0] + (padding[0]*2) + margin[1] + margin[3],
                grid_size[1] + (padding[1]*2) + margin[0] + margin[2]
             )
        )
        sel_min = [
            int(total_size[0]) * x,
            int(total_size[1]) * y
        ]
        sel_max = [
            sel_min[0] + total_size[0] * size_x,
            sel_min[1] + total_size[1] * size_y
        ]
        sel_min[0] += padding[0] + margin[3]
        sel_min[1] += padding[1] + margin[0]
        sel_max[0] -= padding[0] + margin[1]
        sel_max[1] -= padding[1] + margin[2]
        sel_min = int(sel_min[0]), int(sel_min[1])
        sel_max = int(sel_max[0]), int(sel_max[1])
        return sel_min, sel_max

    @staticmethod
    def draw_work_plane(grid_size, sprytile_data, cursor_loc, region, rv3d, middle_btn):
        display_grid = (grid_size[0], grid_size[1])
        # For single pixel grids, use world pixel density
        if grid_size[0] == 1 or grid_size[1] == 1:
            display_grid = (
                VIEW3D_OP_SprytileGui.loaded_grid.tile_selection[2],
                VIEW3D_OP_SprytileGui.loaded_grid.tile_selection[3]
            )
        if display_grid[0] == 1 or display_grid[1] == 1:
            return

        force_draw = sprytile_data.paint_mode == 'FILL' or sprytile_data.lock_normal
        # Decide if should draw, only draw if middle mouse?
        if force_draw is False:
            if sprytile_data.axis_plane_display == 'OFF':
                return
            if sprytile_data.axis_plane_display == 'MIDDLE_MOUSE':
                if middle_btn is False and sprytile_data.is_snapping is False:
                    return

        # First, draw the world grid size overlay
        paint_up_vector = sprytile_data.paint_up_vector
        paint_right_vector = sprytile_data.paint_normal_vector.cross(paint_up_vector)

        pixel_unit = 1 / sprytile_data.world_pixels
        paint_up_vector = paint_up_vector * pixel_unit * display_grid[1]
        paint_right_vector = paint_right_vector * pixel_unit * display_grid[0]

        plane_size = sprytile_data.axis_plane_size
        if sprytile_data.paint_mode == "FILL":
            plane_size = sprytile_data.fill_plane_size

        grid_min, grid_max = sprytile_utils.get_workplane_area(plane_size[0], plane_size[1])

        def draw_world_line(world_start, world_end, color):
            start = view3d_utils.location_3d_to_region_2d(region, rv3d, world_start)
            end = view3d_utils.location_3d_to_region_2d(region, rv3d, world_end)
            if start is None or end is None:
                return

            vcol = (color,)*2
            vpos = ((start.x, start.y), (end.x, end.y))
            draw_flat_viewport('LINES', vpos, vcol)

        plane_col = sprytile_data.axis_plane_color
        color = (plane_col[0], plane_col[1], plane_col[2], 1)
        gpu.state.line_width_set(2)

        for x in range(grid_min[0] + 1, grid_max[0]):
            draw_start = cursor_loc + (paint_right_vector * x) + (paint_up_vector * grid_min[1])
            draw_end = draw_start + paint_up_vector * plane_size[1]
            draw_world_line(draw_start, draw_end, color)
        for y in range(grid_min[1] + 1, grid_max[1]):
            draw_start = cursor_loc + (paint_right_vector * grid_min[0]) + (paint_up_vector * y)
            draw_end = draw_start + paint_right_vector * plane_size[0]
            draw_world_line(draw_start, draw_end, color)

        x_offset_min = paint_right_vector * grid_min[0]
        x_offset_max = paint_right_vector * grid_max[0]
        y_offset_min = paint_up_vector * grid_min[1]
        y_offset_max = paint_up_vector * grid_max[1]

        p0 = view3d_utils.location_3d_to_region_2d(region, rv3d, cursor_loc + x_offset_min + y_offset_min)
        p1 = view3d_utils.location_3d_to_region_2d(region, rv3d, cursor_loc + x_offset_min + y_offset_max)
        p2 = view3d_utils.location_3d_to_region_2d(region, rv3d, cursor_loc + x_offset_max + y_offset_max)
        p3 = view3d_utils.location_3d_to_region_2d(region, rv3d, cursor_loc + x_offset_max + y_offset_min)

        if p0 is None or p1 is None or p2 is None or p3 is None:
            return

        vcol = (color,)*5
        vpos = ((p0.x, p0.y), (p1.x, p1.y), (p2.x, p2.y), (p3.x, p3.y), (p0.x, p0.y))
        draw_flat_viewport('LINE_STRIP', vpos, vcol)

    @staticmethod
    def draw_tile_select_ui(view_min, view_max, view_size,
                            tex_size, grid_size, tile_selection,
                            padding, margin, show_extra, is_pixel):
        # Draw the texture quad
        quad_pos = ((view_min.x, view_min.y), (view_max.x, view_min.y),
               (view_min.x, view_max.y), (view_max.x, view_max.y))
        draw_image_viewport('TRI_STRIP', quad_pos, ((0, 0), (1, 0), (0, 1), (1, 1)),
                            VIEW3D_OP_SprytileGui.texture)

        # Translate the gl context by grid matrix
        scale_factor = (view_size[0] / tex_size[0], view_size[1] / tex_size[1])

        # Setup to draw grid into viewport
        offset_matrix = Matrix.Translation((view_min.x, view_min.y, 0))
        grid_matrix = sprytile_utils.get_grid_matrix(VIEW3D_OP_SprytileGui.loaded_grid)
        grid_matrix = Matrix.Scale(scale_factor[0], 4, Vector((1, 0, 0))) @ Matrix.Scale(scale_factor[1], 4, Vector((0, 1, 0))) @ grid_matrix
        grid_mat = offset_matrix @ grid_matrix

        gpu.state.line_width_set(1)

        # Draw tileset grid, if not pixel size and show extra is on
        if show_extra and is_pixel is False:
            color = (0.0, 0.0, 0.0, 0.5)
            # Draw the grid
            cell_size = (
                grid_size[0] + padding[0] * 2 + margin[1] + margin[3],
                grid_size[1] + padding[1] * 2 + margin[0] + margin[2]
            )
            x_divs = ceil(tex_size[0] / cell_size[0])
            y_divs = ceil(tex_size[1] / cell_size[1])
            x_end = x_divs * cell_size[0]
            y_end = y_divs * cell_size[1]

            for x in range(x_divs + 1):
                x_pos = (x * cell_size[0])
                vtxs = ((x_pos, 0), (x_pos, y_end))
                vcol = (color,)*2
                draw_flat_viewport('LINES', vtxs, vcol, grid_mat)
            for y in range(y_divs + 1):
                y_pos = (y * cell_size[1])
                vtxs = ((0, y_pos), (x_end, y_pos))
                vcol = (color,)*2
                draw_flat_viewport('LINES', vtxs, vcol, grid_mat)

        # Draw selected tile outline
        sel_min, sel_max = VIEW3D_OP_SprytileGui.get_sel_bounds(grid_size, padding, margin,
                                                      tile_selection[0], tile_selection[1],
                                                      tile_selection[2], tile_selection[3])
        draw_flat_viewport('LINE_STRIP', selection_outline(sel_min, sel_max, 0), ((1, 1, 1, 1),)*5, grid_mat)

    @staticmethod
    def draw_preview_tile(context, region, rv3d):
        if sprytile_modal.VIEW3D_OP_SprytileModalTool.no_undo is True:
            return
        if sprytile_preview.preview_verts is None:
            return
        if sprytile_preview.preview_uvs is None:
            return
        if context.scene.sprytile_data.is_snapping:
            return
        if VIEW3D_OP_SprytileGui.tile_ui_active:
            return
        if context.scene.sprytile_data.is_picking:
            return
        if VIEW3D_OP_SprytileGui.out_of_region:
            return

        uv = sprytile_preview.preview_uvs
        world_verts = sprytile_preview.preview_verts
        is_quads = sprytile_preview.preview_is_quads

        # Turn the world vert positions into screen positions
        screen_verts = []
        for world_vtx in world_verts:
            screen_vtx = view3d_utils.location_3d_to_region_2d(region, rv3d, world_vtx)
            if screen_vtx is None:
                return
            screen_verts.append(screen_vtx)

        addon_prefs = context.preferences.addons[__package__].preferences
        preview_alpha = addon_prefs.preview_transparency
        sprytile_data = context.scene.sprytile_data

        if sprytile_data.has_selection:
            preview_alpha *= 0.25
        if sprytile_data.paint_mode == 'PAINT':
            preview_alpha = 0.9

        color = (1.0, 1.0, 1.0, preview_alpha)
        uvs = []
        vtxs = []

        # paint preview only draws one polygon
        for i in range(len(uv)):
            mod = i % 4

            uvs.append((uv[i].x, uv[i].y))
            vtxs.append((screen_verts[i][0], screen_verts[i][1]))

            if mod == 3 and is_quads:
                draw_image_viewport('TRI_STRIP', (vtxs[0], vtxs[3], vtxs[1], vtxs[2]),
                                    (uvs[0], uvs[3], uvs[1], uvs[2]),
                                    VIEW3D_OP_SprytileGui.texture, color)
                uvs.clear()
                vtxs.clear()

        if not is_quads:
            # Draw polygon. TRI_FAN is deprecated on the Metal/Vulkan backends,
            # so the fan is expanded into an indexed triangle list.
            indices = [(0, i, i + 1) for i in range(1, len(vtxs) - 1)]
            draw_image_viewport('TRIS', vtxs, uvs, VIEW3D_OP_SprytileGui.texture, color, indices=indices)

    @staticmethod
    def draw_to_viewport(view_min, view_max, show_extra, label_counter, tilegrid, sprytile_data,
                         cursor_loc, region, rv3d, middle_btn, context):
        """Draw the offscreen texture into the viewport"""
        # POST_PIXEL handlers draw in region pixels, which is what the built-in
        # shaders get from the matrix stack.

        gpu.state.blend_set('ALPHA')
        gpu.state.depth_test_set('NONE')

        # Prepare some data that will be used for drawing
        grid_size = VIEW3D_OP_SprytileGui.loaded_grid.grid
        tile_sel = VIEW3D_OP_SprytileGui.loaded_grid.tile_selection
        padding = VIEW3D_OP_SprytileGui.loaded_grid.padding
        margin = VIEW3D_OP_SprytileGui.loaded_grid.margin
        is_pixel = sprytile_utils.grid_is_single_pixel(VIEW3D_OP_SprytileGui.loaded_grid)

        # Draw work plane
        VIEW3D_OP_SprytileGui.draw_work_plane(grid_size, sprytile_data, cursor_loc, region, rv3d, middle_btn)

        # Nearest filtering is set on the texture when it is drawn, see
        # draw_image_viewport. Wrapping never matters, the UVs stay inside 0..1.

        # Draw the preview tile
        if middle_btn is False:
            VIEW3D_OP_SprytileGui.draw_preview_tile(context, region, rv3d)

        # Calculate actual view size
        view_size = int(view_max.x - view_min.x), int(view_max.y - view_min.y)

        # Save the original scissor box, and then set new scissor setting
        scissor_box = gpu.state.scissor_get()
        gpu.state.scissor_set(int(view_min.x) + scissor_box[0] - 1, int(view_min.y) + scissor_box[1] - 1,
                              view_size[0] + 1, view_size[1] + 1)
        gpu.state.scissor_test_set(True)

        # Draw the tile select UI
        VIEW3D_OP_SprytileGui.draw_tile_select_ui(view_min, view_max, view_size, VIEW3D_OP_SprytileGui.tex_size,
                                       grid_size, tile_sel, padding, margin, show_extra, is_pixel)

        # restore gpu state
        gpu.state.scissor_set(scissor_box[0], scissor_box[1], scissor_box[2], scissor_box[3])
        gpu.state.scissor_test_set(False)
        gpu.state.line_width_set(1)

        # Draw label
        font_id = 0
        font_size = 16
        pad = 5
        if label_counter > 0:
            import math

            def ease_out_circ(t, b, c, d):
                t /= d
                t -= 1
                return c * math.sqrt(1 - t * t) + b
            box_pad = font_size + (pad * 2)
            fade = label_counter
            fade = ease_out_circ(fade, 0, VIEW3D_OP_SprytileGui.label_frames, VIEW3D_OP_SprytileGui.label_frames)
            fade /= VIEW3D_OP_SprytileGui.label_frames

            color = (0.0, 0.0, 0.0, 0.75 * fade)
            vtx = [(view_min.x, view_max.y + box_pad), (view_min.x, view_max.y), (view_max.x, view_max.y + +box_pad), (view_max.x, view_max.y)]
            draw_flat_viewport('TRI_STRIP', vtx, (color,)*4)

            blf.color(font_id, 1.0, 1.0, 1.0, 1.0 * fade)
            blf.size(font_id, font_size)

            x_pos = view_min.x + pad
            y_pos = view_max.y + pad

            label_string = "%dx%d" % (tilegrid.grid[0], tilegrid.grid[1])
            if tilegrid.name != "":
                label_string = "%s - %s" % (label_string, tilegrid.name)
            blf.position(font_id, x_pos, y_pos, 0)
            blf.draw(font_id, label_string)
        if tilegrid.grid[0] == 1 and tilegrid.grid[1] == 1:
            size_text = "%dx%d" % (tile_sel[2], tile_sel[3])
            blf.size(font_id, font_size)
            size = blf.dimensions(font_id, size_text)
            x_pos = view_max.x - size[0] - pad
            y_pos = view_max.y + pad
            blf.position(font_id, x_pos, y_pos, 0)
            blf.draw(font_id, size_text)

        gpu.state.blend_set('NONE')


# Dummy widget to detect when sprytile tool is selected
class SprytileGuiWidgetGroup(bpy.types.GizmoGroup):
    bl_idname = "VIEW3D_GGT_sprytile_gui"
    bl_label = "Spyrite Tile GUI"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'WINDOW'
    bl_options = {'3D'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH'

    def setup(self, context):
        # Get current selected tool
        cur_tool = sprytile_utils.get_current_tool(context)
        sprytile_data = context.scene.sprytile_data
        # Remember where the tool was activated. Timers run with a restricted
        # context, so the operator has to be called under a temp_override.
        window = context.window
        area = context.area
        region = context.region

        def call_gui_op():
            # Set paint mode
            if cur_tool == 'sprytile.tool_build':
               sprytile_data.paint_mode = 'MAKE_FACE'
            elif cur_tool == 'sprytile.tool_paint':
                sprytile_data.paint_mode = 'PAINT'
            elif cur_tool == 'sprytile.tool_fill':
                sprytile_data.paint_mode = 'FILL'

            if not VIEW3D_OP_SprytileGui.is_running:
                # The area may be gone by the time the timer fires
                try:
                    with bpy.context.temp_override(window=window, area=area, region=region):
                        bpy.ops.sprytile.gui_win('INVOKE_REGION_WIN')
                except (RuntimeError, ReferenceError, TypeError) as err:
                    print("Sprytile: could not start GUI operator:", err)
            return None

        # Differ call to timer because operators cannot be called here
        bpy.app.timers.register(call_gui_op)

# module classes
classes = (
    SprytileGuiData,
    SprytileGuiWidgetGroup,
    VIEW3D_OP_SprytileGui
)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    # A draw handler lives on bpy.types.SpaceView3D, not on the addon, so it
    # outlives unregister(). Disabling or reinstalling the addon while the tile
    # GUI is up would leave Blender calling into the unloaded module and crash
    # on the next redraw.
    VIEW3D_OP_SprytileGui.handler_remove(None, None)
    VIEW3D_OP_SprytileGui.free_offscreen()
    VIEW3D_OP_SprytileGui.is_running = False
    VIEW3D_OP_SprytileGui.tile_ui_active = False
    VIEW3D_OP_SprytileGui.anchored_this_session = False

    for c in classes:
        bpy.utils.unregister_class(c)


if __name__ == '__main__':
    register()
