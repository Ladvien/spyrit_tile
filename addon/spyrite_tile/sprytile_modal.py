import math
from collections import deque

import bmesh
import bpy
import numpy
from bpy_extras import view3d_utils
from mathutils import Vector, Matrix, Quaternion
from mathutils.geometry import intersect_line_plane

from .sprytile_event import EventSource
from .sprytile_tools.tool_build import ToolBuild
from .sprytile_tools.tool_paint import ToolPaint
from .sprytile_tools.tool_fill import ToolFill
from .sprytile_uv import UvDataLayers
from . import sprytile_utils
from . import sprytile_preview
from .sprytile_builder import TileBuilder


class DataObjectDict(dict):
    def __getattr__(self, name):
        if name in self:
            return self[name]
        else:
            raise AttributeError("No such attribute: " + name)

    def __setattr__(self, name, value):
        self[name] = value

    def __delattr__(self, name):
        if name in self:
            del self[name]
        else:
            raise AttributeError("No such attribute: " + name)


class VIEW3D_OP_SprytileModalTool(bpy.types.Operator):
    """Tile based mesh creation/UV layout tool"""
    bl_idname = "sprytile.modal_tool"
    bl_label = "Spyrite Tile Paint"
    bl_options = {'REGISTER'}

    no_undo = False

    addon_keymaps = []
    default_keymaps = []
    tool_keymaps = { 
        'MAKE_FACE' : "Sprytile Build Tool Map", 
        'PAINT' : "Sprytile Paint Tool Map", 
        'FILL' : "Sprytile Fill Tool Map"
        }

    @staticmethod
    def calculate_view_axis(context):
        if context.area.type != 'VIEW_3D':
            return None, None

        region = context.region
        rv3d = context.region_data
        if rv3d is None:
            return None, None

        # Get the view ray from center of screen
        coord = Vector((int(region.width / 2), int(region.height / 2)))
        view_vector = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)

        # Get the up vector. The default scene view camera is pointed
        # downward, with up on Y axis. Apply view rotation to get current up
        view_up_vector = rv3d.view_rotation @ Vector((0.0, 1.0, 0.0))

        plane_normal = sprytile_utils.snap_vector_to_axis(view_vector, mirrored=True)
        up_vector = sprytile_utils.snap_vector_to_axis(view_up_vector)

        # calculated vectors are not perpendicular, don't set data
        if plane_normal.dot(up_vector) != 0.0:
            return None, None

        return plane_normal, up_vector

    @staticmethod
    def find_view_axis(context):
        scene = context.scene
        if scene.sprytile_data.lock_normal is True:
            return
        plane_normal, up_vector = VIEW3D_OP_SprytileModalTool.calculate_view_axis(context)
        if plane_normal is None:
            return

        scene.sprytile_data.paint_normal_vector = plane_normal
        scene.sprytile_data.paint_up_vector = up_vector

        if abs(plane_normal.x) > 0:
            new_mode = 'X'
        elif abs(plane_normal.y) > 0:
            new_mode = 'Y'
        else:
            new_mode = 'Z'

        return new_mode

    def get_tiledata_from_index(self, face_index):
        return VIEW3D_OP_SprytileModalTool.get_face_tiledata(self.builder.bmesh, self.builder.bmesh.faces[face_index])

    @staticmethod
    def get_face_tiledata(bmesh, face):
        grid_id_layer = bmesh.faces.layers.int.get(UvDataLayers.GRID_INDEX)
        tile_id_layer = bmesh.faces.layers.int.get(UvDataLayers.GRID_TILE_ID)
        if grid_id_layer is None or tile_id_layer is None:
            return None, None, None, None, None

        grid_id = face[grid_id_layer]
        tile_packed_id = face[tile_id_layer]

        width = 1
        width_layer = bmesh.faces.layers.int.get(UvDataLayers.GRID_SEL_WIDTH)
        if width_layer is not None:
            width = face[width_layer]
            if width is None:
                width = 1

        height = 1
        height_layer = bmesh.faces.layers.int.get(UvDataLayers.GRID_SEL_HEIGHT)
        if height_layer is not None:
            height = face[height_layer]
            if height is None:
                height = 1

        origin = -1
        origin_layer = bmesh.faces.layers.int.get(UvDataLayers.GRID_SEL_ORIGIN)
        if origin_layer is not None:
            origin = face[origin_layer]
            if origin is None:
                origin = -1

        # For backwards compatibility. Origin/width/height
        # did not exist before 0.4.2
        if origin == 0 and height == 0 and width == 0:
            origin = tile_packed_id
        height = max(1, height)
        width = max(1, width)

        # print("get tile data - grid:{0}, tile_id:{1}, w:{2}, h:{3}, o:{4}"
        #       .format(grid_id, tile_packed_id, width, height, origin))
        return grid_id, tile_packed_id, width, height, origin

    def add_virtual_cursor(self, cursor_pos):
        cursor_len = len(self.virtual_cursor)
        if cursor_len == 0:
            self.virtual_cursor.append(cursor_pos)
            return

        last_pos = self.virtual_cursor[cursor_len - 1]
        last_vector = cursor_pos - last_pos
        if last_vector.magnitude < 0.1:
            return

        self.virtual_cursor.append(cursor_pos)

    def get_virtual_cursor_vector(self):
        cursor_direction = Vector((0.0, 0.0, 0.0))
        cursor_len = len(self.virtual_cursor)
        if cursor_len <= 1:
            return cursor_direction
        for idx in range(cursor_len - 1):
            segment = self.virtual_cursor[idx + 1] - self.virtual_cursor[idx]
            cursor_direction += segment
        cursor_direction /= cursor_len
        return cursor_direction

    def flow_cursor(self, context, face_index, virtual_cursor):
        """Move the cursor along the given face, using virtual_cursor direction"""
        world_verts = self.builder.face_to_world_verts(context, face_index)
        self.flow_cursor_verts(context, world_verts, virtual_cursor)

    def flow_cursor_verts(self, context, verts, virtual_cursor):

        cursor_len = len(self.virtual_cursor)
        if cursor_len <= 1:
            return None
        cursor_direction = self.get_virtual_cursor_vector()
        cursor_direction.normalize()

        max_dist = -1.0
        closest_pos = None

        for idx, vert in enumerate(verts):
            vert_vector = vert - virtual_cursor
            vert_dist = vert_vector.length
            vert_vector.normalize()
            vert_dot = vert_vector.dot(cursor_direction)
            if vert_dot > 0.5 and vert_dist > max_dist:
                closest_pos = vert
                max_dist = vert_dist

        return closest_pos

    @staticmethod
    def cursor_move_layer(context, direction):
        scene = context.scene
        target_grid = sprytile_utils.get_grid(context, context.object.sprytile_gridid)
        grid_x = target_grid.grid[0]
        grid_y = target_grid.grid[1]
        layer_move = min(grid_x, grid_y)
        layer_move = math.ceil(layer_move/2)
        layer_move *= (1 / context.scene.sprytile_data.world_pixels)
        plane_normal = scene.sprytile_data.paint_normal_vector.copy()
        plane_normal *= layer_move * direction
        grid_position = scene.cursor.location + plane_normal
        scene.cursor.location = grid_position


    def modal(self, context, event):
        do_exit = False
        sprytile_data = context.scene.sprytile_data

        # Check that the mouse is inside the region
        region = context.region
        coord = Vector((event.mouse_region_x, event.mouse_region_y))
        out_of_region = coord.x < 0 or coord.y < 0 or coord.x > region.width or coord.y > region.height

        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            do_exit = True
        if context.object.mode != 'EDIT':
            do_exit = True
        if do_exit:
            self.exit_modal(event, context)
            return {'CANCELLED'}

        if VIEW3D_OP_SprytileModalTool.no_undo and sprytile_data.is_grid_translate is False:
            VIEW3D_OP_SprytileModalTool.no_undo = False

        # Cursor over Blender's own toolbar, sidebar or headers. The window
        # region runs underneath them with region overlap on, so without this
        # the paint modal would swallow clicks meant for their buttons.
        if sprytile_utils.mouse_over_ui_region(context, event):
            sprytile_preview.clear_preview_data()
            return {'PASS_THROUGH'}

        # Mouse in Sprytile UI, eat this event without doing anything
        if context.scene.sprytile_ui.use_mouse:
            sprytile_preview.clear_preview_data()
            return {'RUNNING_MODAL'}

        # Mouse move triggers preview drawing
        draw_preview = sprytile_data.paint_mode in {'MAKE_FACE', 'FILL', 'PAINT'}
        if draw_preview:
            if (event.alt or context.scene.sprytile_ui.use_mouse) or sprytile_data.is_snapping:
                draw_preview = False

        # Refreshing the mesh, preview needs constantly refreshed
        # mesh or bad things seem to happen. This can potentially get expensive
        #if self.builder.refresh_mesh or self.builder.bmesh.is_valid is False or draw_preview:
        # @Blender 2.8 note: this now happens inside the GUI operator so no need to do it here
        if self.builder.refresh_mesh or self.builder.bmesh.is_valid is False:
            self.builder.update_bmesh_tree(context, True)
            self.builder.refresh_mesh = False

        # Potentially expensive, test if there is a selected mesh element
        if event.type == 'MOUSEMOVE':
            sprytile_data.has_selection = False
            for v in self.builder.bmesh.verts:
                if v.select:
                    sprytile_data.has_selection = True
                    break

        context.area.tag_redraw()

        # If outside the region, pass through
        if out_of_region:
            # If preview data exists, clear it
            if sprytile_preview.preview_verts is not None:
                sprytile_preview.clear_preview_data()
            return {'PASS_THROUGH'}

        modal_return = {'PASS_THROUGH'}

        # Process keyboard events, if returned something end here
        key_return = self.handle_keys(context, event)
        if key_return is not None:
            sprytile_preview.clear_preview_data()
            modal_return = key_return
        # Didn't process keyboard, process mouse now
        else:
            mouse_return = self.handle_mouse(context, event, draw_preview)
            if mouse_return is not None:
                modal_return = mouse_return

        # Signals tools to draw preview
        self.draw_preview = draw_preview and self.builder.refresh_mesh is False
        # Clear preview data if not drawing preview
        if not self.draw_preview:
            sprytile_preview.preview_verts = None
            sprytile_preview.preview_uvs = None

        # Build the data that will be used by tool observers
        region = context.region
        rv3d = context.region_data
        coord = event.mouse_region_x, event.mouse_region_y
        no_data = self.builder.tree is None or rv3d is None

        if no_data is False:
            # get the ray from the viewport and mouse
            ray_vector = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
            ray_origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
            self.rx_data = DataObjectDict(
                context=context,
                ray_vector=ray_vector,
                ray_origin=ray_origin
            )
        else:
            self.rx_data = None

        self.call_tool(event, True, context)

        return modal_return


    def call_tool(self, event, left_down, context):
        # Push the event data out through rx_observer for tool observers
        sprytile_data = bpy.context.scene.sprytile_data

        # If the selected object does not own the painting material, add a slot for it here
        if left_down:
            grid = sprytile_utils.get_grid(context, context.object.sprytile_gridid)
            if grid is not None:
                grid_mat = sprytile_utils.get_grid_material(grid)
                if not sprytile_utils.has_material(context.object, grid_mat):
                    bpy.ops.object.material_slot_add()
                    context.object.active_material = grid_mat

        if self.rx_observer is not None:
            self.rx_observer.on_next(
                DataObjectDict(
                    paint_mode=sprytile_data.paint_mode,
                    event=event,
                    left_down=left_down,
                    build_preview=self.draw_preview,
                )
            )


    def handle_mouse(self, context, event, draw_preview):
        """"""
        # Eat any tweak mouse events, default blender keymap has a translate command on tweak
        if event.type in {'EVT_TWEAK_L', 'EVT_TWEAK_M', 'EVT_TWEAK_R'}:
            return {'RUNNING_MODAL'}
        if 'MOUSE' not in event.type:
            return None
        
        #if event.type in {'WHEELUPMOUSE', 'WHEELDOWNMOUSE'}:
        #    if context.scene.sprytile_data.is_snapping:
        #        direction = -1 if event.type == 'WHEELUPMOUSE' else 1
        #        self.cursor_move_layer(context, direction)
        #        return {'RUNNING_MODAL'}
        # no_undo flag is up, process no other mouse events until it is cleared
        if VIEW3D_OP_SprytileModalTool.no_undo:
            # print("No undo flag is on", event.type, event.value)
            clear_types = {'LEFTMOUSE', 'RIGHTMOUSE'}
            if event.type in clear_types and event.value == 'RELEASE':
                print("Clearing no undo")
                self.builder.refresh_mesh = True
                VIEW3D_OP_SprytileModalTool.no_undo = False
            return {'PASS_THROUGH'} if VIEW3D_OP_SprytileModalTool.no_undo else {'RUNNING_MODAL'}
        elif event.type == 'LEFTMOUSE':
        #    check_modifier = False
            # TODO: Support preferences
            #addon_prefs = context.preferences.addons[__package__].preferences
            #if addon_prefs.tile_picker_key == 'Alt':
            #    check_modifier = event.alt
            #if addon_prefs.tile_picker_key == 'Ctrl':
            #    check_modifier = event.ctrl
            #if addon_prefs.tile_picker_key == 'Shift':
            #    check_modifier = event.shift

        #    if event.value == 'PRESS' and check_modifier is True:
        #        self.find_face_tile(context, event)
            return {'RUNNING_MODAL'}
        elif event.type == 'MOUSEMOVE':
            if draw_preview and not VIEW3D_OP_SprytileModalTool.no_undo and event.type not in self.is_keyboard_list:
                self.draw_preview = True
            #if context.scene.sprytile_data.is_snapping:
            #    self.cursor_snap(context, event)

        return None

    def handle_keys(self, context, event):
        """Process keyboard presses"""
        if event.type not in self.is_keyboard_list:
            return None

        def keymap_is_evt(kmi, evt):
            is_mapped_key = kmi.type == event.type and \
                            kmi.value in {event.value, 'ANY'} and \
                            kmi.ctrl is event.ctrl and \
                            kmi.alt is event.alt and \
                            kmi.shift is event.shift
            return is_mapped_key

        # Process intercepts for special keymaps
        for key_intercept in self.intercept_keys:
            key = key_intercept[0]
            arg = key_intercept[1]
            if not keymap_is_evt(key, event):
                continue
            # print("Special key is", arg)
            if arg == 'move_sel':
                sprytile_preview.preview_uvs = None
                sprytile_preview.preview_verts = None
                VIEW3D_OP_SprytileModalTool.no_undo = True
                bpy.ops.sprytile.translate_grid('INVOKE_REGION_WIN')
                return {'RUNNING_MODAL'}
            if arg == 'sel_mesh':
                return {'PASS_THROUGH'}
        #sprytile_data = context.scene.sprytile_data
        #if event.shift and context.scene.sprytile_data.is_snapping:
        #    self.cursor_snap(context, event)
        #    return {'RUNNING_MODAL'}
        # Pass through every key event we don't handle ourselves
        return {'PASS_THROUGH'}

    def execute(self, context):
        return self.invoke(context, None)

    def invoke(self, context, event):
        if context.space_data.type != 'VIEW_3D':
            self.report({'WARNING'}, "Active space must be a View3d: {0}".format(context.space_data.type))
            return {'CANCELLED'}

        obj = context.object
        if not obj.visible_get() or obj.type != 'MESH':
            self.report({'WARNING'}, "Active object must be a visible mesh")
            return {'CANCELLED'}
        if len(context.scene.sprytile_mats) < 1:
            bpy.ops.sprytile.validate_grids()
        if len(context.scene.sprytile_mats) < 1:
            self.report({'WARNING'}, "No valid materials")
            return {'CANCELLED'}

        use_default_grid_id = obj.sprytile_gridid == -1
        if sprytile_utils.get_grid(context, obj.sprytile_gridid) is None:
            use_default_grid_id = True

        if use_default_grid_id:
            obj.sprytile_gridid = context.scene.sprytile_mats[0].grids[0].id


        addon_prefs = context.preferences.addons[__package__].preferences
        auto_adjust = addon_prefs.auto_adjust_viewport_shading
        if auto_adjust:
            cur_space = context.area.spaces.active
            if cur_space.shading.type != 'MATERIAL':
                cur_space.shading.type = 'MATERIAL'

        self.virtual_cursor = deque([], 3)
        VIEW3D_OP_SprytileModalTool.no_undo = False
        self.builder = TileBuilder(context, context.object)

        # modal() assigns both, but it can return early (mouse over the Sprytile
        # UI, outside the region) or exit before ever reaching those lines, and
        # exit_modal reads them on the way out
        self.draw_preview = False
        self.rx_data = None

        # Setup event observer and source
        self.rx_observer = None
        # Multi casting source, hands out its observer on the first subscribe
        self.rx_source = EventSource(self.setup_rx_observer)

        # Tools receive events from the source
        self.tools = {
            "build": ToolBuild(self, self.rx_source),
            "paint": ToolPaint(self, self.rx_source),
            "fill": ToolFill(self, self.rx_source)
        }

        win_mgr = context.window_manager

        self.setup_user_keys(context)
        win_mgr.modal_handler_add(self)

        sprytile_data = context.scene.sprytile_data
        sprytile_data.is_snapping = False

        context.scene.sprytile_ui.is_dirty = True
        #bpy.ops.sprytile.gui_win('INVOKE_REGION_WIN') #TODO: Renable once ui works

        #Update view axis
        view_axis = self.find_view_axis(context)
        if view_axis is not None:
            if view_axis != sprytile_data.normal_mode:
                sprytile_data.normal_mode = view_axis
                sprytile_data.lock_normal = False

        self.builder.update_bmesh_tree(context, True)
        self.modal(context, event)

        return {'RUNNING_MODAL'}

    def setup_rx_observer(self, observer):
        self.rx_observer = observer

    def setup_user_keys(self, context):
        """Find the keymaps to pass through to Blender"""
        self.is_keyboard_list = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J', 'K', 'L', 'M', 'N', 'O', 'P', 'Q',
                                 'R', 'S', 'T', 'U', 'V', 'W', 'X', 'Y', 'Z',
                                 'ZERO', 'ONE', 'TWO', 'THREE', 'FOUR', 'FIVE', 'SIX', 'SEVEN', 'EIGHT', 'NINE',
                                 'LEFT_CTRL', 'LEFT_ALT', 'LEFT_SHIFT', 'RIGHT_ALT',
                                 'RIGHT_CTRL', 'RIGHT_SHIFT', 'OSKEY', 'GRLESS', 'ESC', 'TAB', 'RET', 'SPACE',
                                 'LINE_FEED', 'BACK_SPACE', 'DEL', 'SEMI_COLON', 'PERIOD', 'COMMA', 'QUOTE',
                                 'ACCENT_GRAVE', 'MINUS', 'SLASH', 'BACK_SLASH', 'EQUAL', 'LEFT_BRACKET',
                                 'RIGHT_BRACKET', 'LEFT_ARROW', 'DOWN_ARROW', 'RIGHT_ARROW', 'UP_ARROW',
                                 'NUMPAD_2', 'NUMPAD_4', 'NUMPAD_6', 'NUMPAD_8', 'NUMPAD_1', 'NUMPAD_3', 'NUMPAD_5',
                                 'NUMPAD_7', 'NUMPAD_9', 'NUMPAD_PERIOD', 'NUMPAD_SLASH', 'NUMPAD_ASTERIX', 'NUMPAD_0',
                                 'NUMPAD_MINUS', 'NUMPAD_ENTER', 'NUMPAD_PLUS',
                                 'F1', 'F2', 'F3', 'F4', 'F5', 'F6', 'F7', 'F8', 'F9', 'F10', 'F11', 'F12', 'F13',
                                 'F14', 'F15', 'F16', 'F17', 'F18', 'F19', 'PAUSE', 'INSERT', 'HOME', 'PAGE_UP',
                                 'PAGE_DOWN', 'END', 'MEDIA_PLAY', 'MEDIA_STOP', 'MEDIA_FIRST', 'MEDIA_LAST']

        self.intercept_keys = []

        user_keymaps = context.window_manager.keyconfigs.user.keymaps

        def get_keymap_entry(keymap_name, command):
            keymap = user_keymaps[keymap_name]
            if keymap is None:
                return False, None
            key_list = keymap.keymap_items
            cmd_idx = key_list.find(command)
            if cmd_idx < 0:
                return True, None
            return True, key_list[cmd_idx]

        # These keymaps intercept existing shortcuts and repurpose them
        keymap_intercept = {
            '3D View': [
                ('view3d.select_circle', 'sel_mesh'),
                ('transform.translate', 'move_sel')
            ]
        }
        for keymap_id in keymap_intercept:
            cmd_list = keymap_intercept[keymap_id]
            for cmd_data in cmd_list:
                cmd = cmd_data[0]
                arg = cmd_data[1]
                has_map, cmd_entry = get_keymap_entry(keymap_id, cmd)
                if not has_map:
                    break
                if cmd_entry is None:
                    continue
                self.intercept_keys.append((cmd_entry, arg))

    def exit_modal(self, event, context):
        self.call_tool(event, False, context)
        if self.rx_observer is not None:
            self.rx_observer.on_completed()
        self.builder.tree = None
        self.tools = None
        if context.object.mode == 'EDIT':
            bmesh.update_edit_mesh(context.object.data, loop_triangles=True, destructive=True)


# module classes
classes = (
    VIEW3D_OP_SprytileModalTool,
)

def register():
    for cl in classes:
        bpy.utils.register_class(cl)


def unregister():
    for cl in classes:
        bpy.utils.unregister_class(cl)


if __name__ == '__main__':
    register()
