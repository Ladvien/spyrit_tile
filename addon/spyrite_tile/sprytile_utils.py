import bpy
import blf
import bmesh
import math

import sys
from bpy_extras import view3d_utils
from bpy_extras.io_utils import ImportHelper
from bpy.props import StringProperty
from bmesh.types import BMVert, BMEdge, BMFace
from mathutils import Matrix, Vector, Quaternion
from mathutils.geometry import intersect_line_plane, distance_point_to_plane
from mathutils.bvhtree import BVHTree
from bpy.path import abspath
from datetime import datetime
from os import path
from . import PAINT_ALIGN_BY_NUMBER, PAINT_ALIGN_NUMBER
from . import sprytile_core
from . import sprytile_modal
from . import sprytile_builder
from . import sprytile_preview


def label_wrap(col, text, area="VIEW_3D", region_type="TOOL_PROPS", tab_str="    ", scale_y=0.55):
    a_id = -1
    r_id = -1
    new_line = "\n"
    tab = "\t"
    tabbing = False
    n_line = False
    col.scale_y = scale_y
    areas = bpy.context.screen.areas
    for i, a in enumerate(areas):
        if a.type == area:
            a_id = i
        reg = a.regions
        for ir, r in enumerate(reg):
            if r.type == region_type:
                r_id = ir
    if a_id < 0 or r_id < 0:
        return

    p_width = areas[a_id].regions[r_id].width
    char_width = 7  # approximate width of each character
    line_length = int(p_width / char_width)
    last_space = line_length  # current position of last space character in text
    while last_space > 0:
        split_point = line_length  # where to split the text
        if split_point > len(text):
            split_point = len(text) - 1

        cr = text.find(new_line, 0, len(text))

        if (cr > 0) and (cr <= split_point):
            n_line = True
            last_space = cr  # Position of new line symbol, if found
        else:
            tabp = text.find("\t", 0, split_point)
            if tabp >= 0:
                text = text.replace(tab, "", 1)
                tabbing = True
                n_line = False
            last_space = text.rfind(" ", 0, split_point)  # Position of last space character in text

        if (last_space == -1) or len(text) <= line_length:  # No more spaces found, or its the last line of text
            last_space = len(text)
        line = text[0:last_space]
        if tabbing:
            line = tab_str + line
        col.label(text=line)
        if n_line:
            tabbing = False
        text = text[last_space + 1:len(text)]


class UTIL_OP_SprytileAxisUpdate(bpy.types.Operator):
    bl_idname = "sprytile.axis_update"
    bl_label = "Update Spyrite Tile Axis"

    def execute(self, context):
        return self.invoke(context, None)

    def invoke(self, context, event):
        # Given the normal mode, find the direction of paint_normal_vector, paint_up_vector
        data = context.scene.sprytile_data
        region = context.region
        rv3d = context.region_data

        # Get the view ray from center of screen
        coord = Vector((int(region.width / 2), int(region.height / 2)))
        view_vector = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)

        # Get the up vector. The default scene view camera is pointed
        # downward, with up on Y axis. Apply view rotation to get current up
        view_up_vector = rv3d.view_rotation @ Vector((0.0, 1.0, 0.0))

        view_vector = sprytile_core.snap_vector_to_axis(view_vector, mirrored=True)
        view_up_vector = sprytile_core.snap_vector_to_axis(view_up_vector)

        # implicit X
        paint_normal = Vector((1.0, 0.0, 0.0))
        if data.normal_mode == 'Y':
            paint_normal = Vector((0.0, 1.0, 0.0))
        elif data.normal_mode == 'Z':
            paint_normal = Vector((0.0, 0.0, 1.0))

        view_dot = paint_normal.dot(view_up_vector)
        view_dot = abs(view_dot)
        paint_up = view_up_vector
        if view_dot > 0.9:
            paint_up = view_vector

        # print("View", view_vector, "View Up", view_up_vector)
        # print("Axis update, view dot:", view_dot)
        # print("mode", data.normal_mode, "paint normal", paint_normal, "paint up", paint_up)
        data.paint_normal_vector = paint_normal
        data.paint_up_vector = paint_up

        return {'FINISHED'}


class UTIL_OP_SprytileGridAdd(bpy.types.Operator):
    bl_idname = "sprytile.grid_add"
    bl_label = "Add New Grid"
    bl_description = "Add new tile grid"

    def execute(self, context):
        return self.invoke(context, None)

    def invoke(self, context, event):
        self.add_new_grid(context)
        return {'FINISHED'}

    @staticmethod
    def add_new_grid(context):
        mat_list = context.scene.sprytile_mats
        target_mat = None

        if len(mat_list) > 0:
            target_mat = mat_list[0]

        grid_id = context.object.sprytile_gridid

        target_grid = sprytile_core.get_grid(context, grid_id)
        if target_grid is not None:
            for mat in mat_list:
                if mat.mat_id == target_grid.mat_id:
                    target_mat = mat
                    break

        if target_mat is None:
            return

        grid_idx = -1
        for idx, grid in enumerate(target_mat.grids):
            if grid.id == grid_id:
                grid_idx = idx
                break

        new_idx = len(target_mat.grids)

        new_grid = target_mat.grids.add()
        new_grid.mat_id = target_mat.mat_id
        new_grid.id = sprytile_core.get_highest_grid_id(context) + 1

        addon_prefs = bpy.context.preferences.addons[__package__].preferences
        if addon_prefs:
            new_grid.grid = addon_prefs.default_grid
            new_grid.auto_pad_offset = addon_prefs.default_pad_offset

        if grid_idx > -1:
            new_grid.grid = target_mat.grids[grid_idx].grid
            target_mat.grids.move(new_idx, grid_idx + 1)

        bpy.ops.sprytile.build_grid_list()


class UTIL_OP_SprytileGridRemove(bpy.types.Operator):
    bl_idname = "sprytile.grid_remove"
    bl_label = "Remove Grid"
    bl_description = "Remove selected tile grid"

    def execute(self, context):
        return self.invoke(context, None)

    def invoke(self, context, event):
        level, message = self.delete_grid(context)
        self.report({level}, message)
        return {'CANCELLED'} if level == 'WARNING' else {'FINISHED'}

    @staticmethod
    def delete_grid(context):
        """Remove the selected grid; the last grid of a tileset removes the tileset from the list.

        The material and its image are left alone, objects may still use them.
        Returns (report level, message).
        """
        scene = context.scene
        obj = context.object
        grid_id = obj.sprytile_gridid

        target_grid = sprytile_core.get_grid(context, grid_id)
        if target_grid is None:
            return 'WARNING', "No tile grid selected"

        target_mat = sprytile_core.get_mat_data(context, target_grid.mat_id)
        if target_mat is None:
            for mat in scene.sprytile_mats:
                if any(grid.id == grid_id for grid in mat.grids):
                    target_mat = mat
                    break
        if target_mat is None:
            return 'WARNING', "Selected grid belongs to no tileset"

        if len(target_mat.grids) <= 1:
            return 'INFO', UTIL_OP_SprytileGridRemove.remove_tileset(context, target_mat)

        grid_idx = -1
        for idx, grid in enumerate(target_mat.grids):
            if grid.id == grid_id:
                grid_idx = idx
                break

        target_mat.grids.remove(grid_idx)
        bpy.ops.sprytile.build_grid_list()
        return 'INFO', "Removed grid {0} from tileset {1}".format(grid_id, target_mat.mat_id)

    @staticmethod
    def remove_tileset(context, mat_data):
        """Drop mat_data from scene.sprytile_mats and the list display; keep the material and image.

        Objects of the scene that used one of its grids are pointed at the
        first remaining grid, or -1 when none is left. sprytile_core.validate_grids is told
        not to add the material again.
        """
        scene = context.scene
        mat_id = mat_data.mat_id
        removed_ids = {grid.id for grid in mat_data.grids}

        for idx, mat in enumerate(scene.sprytile_mats):
            if mat == mat_data:
                scene.sprytile_mats.remove(idx)
                break

        material = bpy.data.materials.get(mat_id)
        if material is not None:
            sprytile_core._removed_tilesets.add(material.session_uid)

        fallback_id = -1
        for mat in scene.sprytile_mats:
            if len(mat.grids) > 0:
                fallback_id = mat.grids[0].id
                break
        for scene_obj in scene.objects:
            if scene_obj.sprytile_gridid in removed_ids:
                scene_obj.sprytile_gridid = fallback_id

        scene.sprytile_list["idx"] = 0
        sprytile_core.build_grid_list(scene, context.object)
        return "Removed tileset {0} from the Sprytile list (its material and image are kept)".format(mat_id)


class UTIL_OP_SprytileGridCycle(bpy.types.Operator):
    bl_idname = "sprytile.grid_cycle"
    bl_label = "Cycle grid settings"

    direction: bpy.props.IntProperty(default=1)

    def execute(self, context):
        return self.invoke(context, None)

    def invoke(self, context, event):
        self.cycle_grid(context)
        return {'FINISHED'}

    def cycle_grid(self, context):
        obj = context.object
        curr_grid = sprytile_core.get_grid(context, obj.sprytile_gridid)
        if curr_grid is None:
            return

        curr_mat = sprytile_core.get_mat_data(context, curr_grid.mat_id)
        if curr_mat is None:
            return

        idx = -1
        for grid in curr_mat.grids:
            idx += 1
            if grid.id == curr_grid.id:
                break

        idx += self.direction
        if idx < 0:
            idx = len(curr_mat.grids)-1
        if idx >= len(curr_mat.grids):
            idx = 0

        obj.sprytile_gridid = curr_mat.grids[idx].id
        bpy.ops.sprytile.build_grid_list()


class UTIL_OP_SprytileStartTool(bpy.types.Operator):
    bl_idname = "sprytile.start_tool"
    bl_label = "Start Spyrite Tile Paint"

    mode: bpy.props.IntProperty(default=3)

    def execute(self, context):
        return self.invoke(context, None)

    def invoke(self, context, event):
        if self.mode == 0:
            context.scene.sprytile_data.paint_mode = 'SET_NORMAL'
        if self.mode == 1:
            context.scene.sprytile_data.paint_mode = 'PAINT'
        if self.mode == 2:
            context.scene.sprytile_data.paint_mode = 'MAKE_FACE'
        bpy.ops.sprytile.modal_tool('INVOKE_REGION_WIN')
        return {'FINISHED'}


class UTIL_OP_SprytileGridMove(bpy.types.Operator):
    bl_idname = "sprytile.grid_move"
    bl_label = "Move Grid"
    bl_description = "Move selected tile grid up or down"

    direction : bpy.props.IntProperty(default=1)

    def execute(self, context):
        return self.invoke(context, None)

    def invoke(self, context, event):
        self.move_grid(context)
        return {'FINISHED'}

    def move_grid(self, context):
        obj = context.object
        curr_grid = sprytile_core.get_grid(context, obj.sprytile_gridid)
        if curr_grid is None:
            return

        curr_mat = sprytile_core.get_mat_data(context, curr_grid.mat_id)
        if curr_mat is None:
            return

        idx = -1
        for grid in curr_mat.grids:
            idx += 1
            if grid.id == curr_grid.id:
                break

        old_idx = idx
        idx = old_idx + self.direction
        if idx < 0:
            idx = len(curr_mat.grids)-1
        if idx >= len(curr_mat.grids):
            idx = 0

        curr_mat.grids.move(old_idx, idx)
        obj.sprytile_gridid = curr_mat.grids[idx].id
        bpy.ops.sprytile.build_grid_list()


class UTIL_OP_SprytileNewMaterial(bpy.types.Operator):
    bl_idname = "sprytile.add_new_material"
    bl_label = "New Shadeless Material"
    bl_description = "Create a new shadeless material"

    @classmethod
    def poll(cls, context):
        return context.object is not None

    def invoke(self, context, event):
        return self.execute(context)

    def execute(self, context):
        obj = context.object
        if obj.type != 'MESH':
            return {'FINISHED'}

        mat = bpy.data.materials.new(name="Material")

        set_idx = len(obj.material_slots)
        obj.data.materials.append(mat)
        obj.active_material_index = set_idx

        bpy.ops.sprytile.material_setup('INVOKE_DEFAULT')
        bpy.ops.sprytile.validate_grids('INVOKE_DEFAULT')
        return {'FINISHED'}


class UTIL_OP_SprytileSetupMaterial(bpy.types.Operator):
    bl_idname = "sprytile.material_setup"
    bl_label = "Set Material to Shadeless"
    bl_description = "Make current selected material shadeless, for pixel art texture purposes"

    @classmethod
    def poll(cls, context):
        return context.object is not None

    def execute(self, context):
        return self.invoke(context, None)

    def invoke(self, context, event):
        obj = context.object
        if obj.type != 'MESH' or len(obj.material_slots) == 0:
            return {'FINISHED'}

        # The interactive operator leaves interpolation alone: the separate
        # texture setup operator is what switches a tileset to Closest
        sprytile_core.setup_tile_material(obj.material_slots[obj.active_material_index].material, closest=False)
        return {'FINISHED'}


class UTIL_OP_SprytileSetupViewport(bpy.types.Operator):
    bl_idname = "sprytile.viewport_setup"
    bl_label = "Setup Pixel Viewport"
    bl_description = "Set optimal 3D viewport settings for pixel art"

    def execute(self, context):
        return self.invoke(context, None)

    def invoke(self, context, event):
        # Disable Eevee's TAA, which causes noticeable artefacts with pixel art
        context.scene.eevee.taa_samples = 1
        context.scene.eevee.use_taa_reprojection = False

        # Set view transform to standard, for correct texture brightness
        context.scene.view_settings.view_transform = 'Standard'

        # Reflect changes
        context.scene.update_tag()
        for area in context.screen.areas:
            area.tag_redraw()

        return {'FINISHED'}


class UTIL_OP_SprytileLoadTileset(bpy.types.Operator, ImportHelper):
    bl_idname = "sprytile.tileset_load"
    bl_label = "Load Tileset"
    bl_description = "Load a tileset into the current material"

    # For some reason this full list doesn't really work,
    # reordered the list to prioritize common file types
    # filter_ext = "*" + ";*".join(bpy.path.extensions_image.sort())

    filter_glob: bpy.props.StringProperty(
        default="*.bmp;*.psd;*.hdr;*.rgba;*.jpg;*.png;*.tiff;*.tga;*.jpeg;*.jp2;*.rgb;*.dds;*.exr;*.psb;*.j2c;*.dpx;*.tif;*.tx;*.cin;*.pdd;*.sgi",
        options={'HIDDEN'},
    )

    @classmethod
    def poll(cls, context):
        return context.object is not None

    def execute(self, context):
        if context.object.type != 'MESH':
            return {'FINISHED'}
        if UTIL_OP_SprytileLoadTileset.reuse_tileset_material(context, self.filepath):
            return {'FINISHED'}
        # Check object material count, if 0 create a new material before loading
        if len(context.object.material_slots.items()) < 1:
            bpy.ops.sprytile.add_new_material('INVOKE_DEFAULT')
        UTIL_OP_SprytileLoadTileset.load_tileset_file(context, self.filepath)
        return {'FINISHED'}

    @staticmethod
    def reuse_tileset_material(context, filepath):
        """Use the existing tileset material built from this image file, if there is one.

        Appends it to the active object's material slots (or selects its slot) and keeps the tileset's grid
        settings. Returns False when no tileset uses the file, so the caller creates a new material.
        """
        wanted = path.realpath(abspath(filepath))
        existing = None
        for mat_data in context.scene.sprytile_mats:
            material = bpy.data.materials.get(mat_data.mat_id)
            if material is None or len(mat_data.grids) == 0 or material.session_uid in sprytile_core._removed_tilesets:
                continue
            image = sprytile_core.get_material_texture(material)
            if image is not None and path.realpath(abspath(image.filepath)) == wanted:
                existing = material
                break
        if existing is None:
            return False
        obj = context.object
        slot_index = next((i for i, slot in enumerate(obj.material_slots) if slot.material == existing), None)
        if slot_index is None:
            obj.data.materials.append(existing)
            slot_index = len(obj.material_slots) - 1
        obj.active_material_index = slot_index
        obj.sprytile_gridid = sprytile_core.get_mat_data(context, existing.name).grids[0].id
        return True

    @staticmethod
    def load_tileset_file(context, filepath):
        obj = context.object

        texture_name = filepath[filepath.rindex(path.sep) + 1:]
        material_name = filepath[filepath.rindex(path.sep) + 1: filepath.rindex('.')]

        bpy.ops.sprytile.material_setup()

        target_mat = obj.material_slots[obj.active_material_index].material
        target_mat.name = material_name

        loaded_img = bpy.data.images.load(filepath)
        sprytile_core.set_material_texture(target_mat, loaded_img)

        bpy.ops.sprytile.texture_setup('INVOKE_DEFAULT')
        bpy.ops.sprytile.validate_grids('INVOKE_DEFAULT')

        addon_prefs = context.preferences.addons[__package__].preferences
        if addon_prefs:
            if addon_prefs.auto_pixel_viewport:
                bpy.ops.sprytile.viewport_setup('INVOKE_DEFAULT')
            if addon_prefs.auto_grid_setup:
                bpy.ops.sprytile.setup_grid('INVOKE_DEFAULT')


class UTIL_OP_SprytileNewTileset(bpy.types.Operator, ImportHelper):
    bl_idname = "sprytile.tileset_new"
    bl_label = "Add Tileset"
    bl_description = "Create a new material and load another tileset"

    # For some reason this full list doesn't really work,
    # reordered the list to prioritize common file types
    # filter_ext = "*" + ";*".join(bpy.path.extensions_image.sort())

    filter_glob: bpy.props.StringProperty(
        default="*.bmp;*.psd;*.hdr;*.rgba;*.jpg;*.png;*.tiff;*.tga;*.jpeg;*.jp2;*.rgb;*.dds;*.exr;*.psb;*.j2c;*.dpx;*.tif;*.tx;*.cin;*.pdd;*.sgi",
        options={'HIDDEN'},
    )

    @classmethod
    def poll(cls, context):
        return context.object is not None

    def execute(self, context):
        if context.object.type != 'MESH':
            return {'FINISHED'}
        if UTIL_OP_SprytileLoadTileset.reuse_tileset_material(context, self.filepath):
            return {'FINISHED'}
        bpy.ops.sprytile.add_new_material('INVOKE_DEFAULT')
        UTIL_OP_SprytileLoadTileset.load_tileset_file(context, self.filepath)
        return {'FINISHED'}


class UTIL_OP_SprytileSetupTexture(bpy.types.Operator):
    bl_idname = "sprytile.texture_setup"
    bl_label = "Setup Pixel Texture"
    bl_description = "Change texture settings for crunchy pixelart style"

    def execute(self, context):
        return self.invoke(context, None)

    def invoke(self, context, event):
        self.setup_tex(context)
        return {'FINISHED'}

    @staticmethod
    def setup_tex(context):
        """"""
        obj = context.object
        if obj.type != 'MESH':
            return
        material = obj.material_slots[obj.active_material_index].material

        #target_texture = None
        #target_img = None
        #target_slot = None
        # for texture_slot in material.texture_slots:
        #     if texture_slot is None:
        #         continue
        #     if texture_slot.texture is None:
        #         continue
        #     if texture_slot.texture.type == 'NONE':
        #         continue
        #     if texture_slot.texture.type == 'IMAGE':
        #         # Cannot use the texture slot image reference directly
        #         # Have to get it through bpy.data.images to be able to use with BGL
        #         target_texture = bpy.data.textures.get(texture_slot.texture.name)
        #         target_img = bpy.data.images.get(texture_slot.texture.image.name)
        #         target_slot = texture_slot
        #         break
        # if target_texture is None or target_img is None:
        #     return

        target_node = sprytile_core.get_material_texture_node(material)
        if not target_node:
            return

        target_node.interpolation = 'Closest'
        target_img = target_node.image

        # We don't have these in 2.8, but the behaviour with nodes and Closest filtering is equivalent.
        # However, 2.8 doesn't currently offer an option to disable mipmaps?
        # target_texture.use_preview_alpha = True
        # target_texture.use_alpha = True
        # target_texture.use_interpolation = False
        # target_texture.use_mipmap = False
        # target_texture.filter_type = 'BOX'
        # target_texture.filter_size = 0.10
        
        # target_slot.use_map_color_diffuse = True
        # target_slot.use_map_alpha = True
        # target_slot.alpha_factor = 1.0
        # target_slot.diffuse_color_factor = 1.0
        # target_slot.texture_coords = 'UV'


class UTIL_OP_SprytileValidateGridList(bpy.types.Operator):
    bl_idname = "sprytile.validate_grids"
    bl_label = "Validate Tile Grids"
    bl_description = "Press if tile grids are not displaying properly"

    @classmethod
    def poll(cls, context):
        return True

    def execute(self, context):
        return self.invoke(context, None)

    def invoke(self, context, event):
        sprytile_core.validate_grids(context.scene, context.object)
        return {'FINISHED'}


class UTIL_OP_SprytileBuildGridList(bpy.types.Operator):
    bl_idname = "sprytile.build_grid_list"
    bl_label = "Spyrite Tile Build Grid List"

    def execute(self, context):
        return self.invoke(context, None)

    def invoke(self, context, event):
        sprytile_core.build_grid_list(context.scene, context.object)
        return {'FINISHED'}


class UTIL_OP_SprytileRotateLeft(bpy.types.Operator):
    bl_idname = "sprytile.rotate_left"
    bl_label = "Rotate Spyrite Tile Left"
    bl_description = "Rotate the tile 90 degrees counter clockwise"

    def execute(self, context):
        return self.invoke(context, None)

    def invoke(self, context, event):
        curr_rotation = context.scene.sprytile_data.mesh_rotate
        curr_rotation += 1.5708
        if curr_rotation > 6.28319:
            curr_rotation = 0
        context.scene.sprytile_data.mesh_rotate = curr_rotation
        return {'FINISHED'}


class UTIL_OP_SprytileRotateRight(bpy.types.Operator):
    bl_idname = "sprytile.rotate_right"
    bl_label = "Rotate Spyrite Tile Right"
    bl_description = "Rotate the tile 90 degrees clockwise"

    def execute(self, context):
        return self.invoke(context, None)

    def invoke(self, context, event):
        curr_rotation = context.scene.sprytile_data.mesh_rotate
        curr_rotation -= 1.5708
        if curr_rotation < -6.28319:
            curr_rotation = 0
        context.scene.sprytile_data.mesh_rotate = curr_rotation
        return {'FINISHED'}


class UTIL_OP_SprytileReloadImages(bpy.types.Operator):
    bl_idname = "sprytile.reload_imgs"
    bl_label = "Reload All Images"
    bl_description = "Automatically reload images referenced by the scene"

    def invoke(self, context, event):
        for img in bpy.data.images:
            if img is None:
                continue
            img.reload()
        for window in context.window_manager.windows:
            for area in window.screen.areas:
                if area.type in {'VIEW_3D', 'IMAGE_EDITOR'}:
                    area.tag_redraw()
        return {'FINISHED'}

    def execute(self, context):
        return self.invoke(context, None)


class UTIL_OP_SprytileReloadImagesAuto(bpy.types.Operator):
    bl_idname = "sprytile.reload_auto"
    bl_label = "Reload All Images (Auto)"

    _timer = None
    last_check_time = None

    def modal(self, context, event):
        if event.type == 'TIMER':
            if context.scene.sprytile_data.auto_reload is False:
                self.cancel(context)
                return {'CANCELLED'}

            if self.check_files():
                for window in context.window_manager.windows:
                    for area in window.screen.areas:
                        if area.type in {'VIEW_3D', 'IMAGE_EDITOR'}:
                            area.tag_redraw()

        return {'PASS_THROUGH'}

    def check_files(self):
        did_reload = False
        for img in bpy.data.images:
            if img is None:
                continue
            filepath = abspath(img.filepath)
            if path.exists(filepath) is False:
                continue
            file_mod = path.getmtime(filepath)
            filetime = datetime.fromtimestamp(file_mod)
            if self.last_check_time is None or filetime > self.last_check_time:
                print("Reloading", img.filepath)
                img.reload()
                did_reload = True
        self.last_check_time = datetime.now()
        return did_reload

    def execute(self, context):
        return self.invoke(context, None)

    def invoke(self, context, event):
        self.last_check_time = None
        self.check_files()
        wm = context.window_manager
        self._timer = wm.event_timer_add(2, window=context.window)
        wm.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def cancel(self, context):
        wm = context.window_manager
        wm.event_timer_remove(self._timer)


class UTIL_OP_SprytileMakeDoubleSided(bpy.types.Operator):
    bl_idname = "sprytile.make_double_sided"
    bl_label = "Make Double Sided (Spyrite Tile)"
    bl_description = "Duplicate selected faces and flip normals"

    def execute(self, context):
        return self.invoke(context, None)

    def invoke(self, context, event):
        print("Invoked make double sided")
        if context.object is None or (context.object.type != 'MESH' or context.object.mode != 'EDIT'):
            print("Nope")
            return {'FINISHED'}
        mesh = bmesh.from_edit_mesh(context.object.data)
        double_face = []
        for face in mesh.faces:
            if not face.select:
                continue
            double_face.append(face)
        for face in double_face:
            face.copy(True, True)
            face.normal_flip()
            face.normal_update()

        mesh.faces.index_update()
        mesh.faces.ensure_lookup_table()
        bmesh.update_edit_mesh(context.object.data, loop_triangles=True, destructive=True)
        return {'FINISHED'}


class UTIL_OP_SprytileSetupGrid(bpy.types.Operator):
    bl_idname = "sprytile.setup_grid"
    bl_label = "Floor Grid To Pixels"
    bl_description = "Make floor grid display follow world pixel settings"

    @classmethod
    def description(cls, context, properties):
        return "Set grid scale to {} pixels".format(context.scene.sprytile_data.world_pixels)

    def execute(self, context):
        return self.invoke(context, None)

    def invoke(self, context, event):
        pixel_unit = (1 / context.scene.sprytile_data.world_pixels)
        for window in context.window_manager.windows:
            for area in window.screen.areas:
                if (area.type == 'VIEW_3D'):
                    for space in area.spaces:
                        if (space.type == 'VIEW_3D'):
                            space.overlay.grid_scale = pixel_unit
                            space.overlay.grid_subdivisions = 1
        
        context.scene.tool_settings.use_snap = True
        context.scene.tool_settings.snap_elements = {'INCREMENT'}
        return {'FINISHED'}


class UTIL_OP_SprytileGridTranslate(bpy.types.Operator):
    bl_idname = "sprytile.translate_grid"
    bl_label = "Pixel Translate (Spyrite Tile)"

    # Draw handlers outlive the addon module, keep a class level reference so
    # unregister() can drop one that is still installed
    active_draw_handle = None

    @staticmethod
    def draw_callback(self, context):
        if self.exec_counter != -1 or self.ref_pos is None:
            return None

        check_pos = self.get_ref_pos(context)
        measure_vec = check_pos - self.ref_pos
        pixel_unit = 1 / context.scene.sprytile_data.world_pixels
        for i in range(3):
            measure_vec[i] = int(round(measure_vec[i] / pixel_unit))

        font_id = 0
        font_size = 16
        padding = 5
        blf.size(font_id, font_size)

        # The WINDOW region runs underneath the headers, so a row drawn near the
        # top edge is hidden by them (the X row was). Draw in the bottom right
        # corner instead, clear of the sidebar if it is open.
        sidebar_width = 0
        for region in context.area.regions:
            if region.type == 'UI' and region.width > 1:
                sidebar_width = region.width
        row_height = font_size + padding
        screen_x = context.region.width - sidebar_width - 90
        screen_y = padding * 2 + row_height * 2

        readout_axis = ['X', 'Y', 'Z']
        for i in range(3):
            blf.position(font_id, screen_x, screen_y, 0)
            blf.draw(font_id, "%s : %d" % (readout_axis[i], measure_vec[i]))
            screen_y -= row_height

    def modal(self, context, event):
        # User cancelled transform
        if event.type == 'ESC':
            return self.exit_modal(context)
        if event.type == 'RIGHTMOUSE' and event.value == 'RELEASE':
            return self.exit_modal(context)
        # On the timer events, count down the frames and execute the
        # translate operator when reach 0
        if event.type == 'TIMER':
            if self.exec_counter > 0:
                self.exec_counter -= 1

            if self.exec_counter == 0:
                self.exec_counter -= 1
                up_vec, right_vec, norm_vec = sprytile_core.get_current_grid_vectors(context.scene)
                norm_vec = sprytile_core.snap_vector_to_axis(norm_vec)
                axis_constraint = [
                    abs(norm_vec.x) == 0,
                    abs(norm_vec.y) == 0,
                    abs(norm_vec.z) == 0
                ]
                tool_value = bpy.ops.transform.translate(
                    'INVOKE_DEFAULT',
                    constraint_axis=axis_constraint,
                    snap=self.restore_settings is not None
                )
                # Translate tool moved nothing, exit
                if 'CANCELLED' in tool_value:
                    return self.exit_modal(context)

        # When the active operator changes, we know that translate has been completed
        if context.active_operator != self.watch_operator:
            return self.exit_modal(context)

        return {'PASS_THROUGH'}

    def get_ref_pos(self, context):
        """World space position of the element the readout is measured from.

        The pixel unit is a world space length, so the measured positions must be
        too: an object with scale, rotation or a parent has a local vertex
        coordinate that differs from where the vertex is in the world."""
        if context.object.mode != 'EDIT':
            return None
        if self.bmesh is None:
            self.bmesh = bmesh.from_edit_mesh(context.object.data)
        local_pos = self.get_ref_local_pos()
        if local_pos is None:
            return None
        return context.object.matrix_world @ local_pos

    def get_ref_local_pos(self):
        if len(self.bmesh.select_history) <= 0:
            for vert in self.bmesh.verts:
                if vert.select:
                    return vert.co.copy()
            return None

        target = self.bmesh.select_history[0]
        if isinstance(target, BMFace):
            return target.verts[0].co.copy()
        if isinstance(target, BMEdge):
            return target.verts[0].co.copy()
        if isinstance(target, BMVert):
            return target.co.copy()
        return None

    def execute(self, context):
        return self.invoke(context, None)

    def invoke(self, context, event):
        # When this tool is invoked, change the grid settings so that snapping
        # is on pixel unit steps. Save settings to restore later
        self.restore_settings = None
        space_data = context.space_data
        if space_data.type == 'VIEW_3D':
            self.restore_settings = {
                "grid_scale": space_data.overlay.grid_scale,
                "grid_sub": space_data.overlay.grid_subdivisions,
                "show_floor": space_data.overlay.show_floor,
                "pivot": context.scene.tool_settings.transform_pivot_point,
                "orient": context.scene.transform_orientation_slots[0].type,
                "use_snap": context.scene.tool_settings.use_snap,
                "snap_elements": context.scene.tool_settings.snap_elements
            }
            pixel_unit = 1 / context.scene.sprytile_data.world_pixels
            space_data.overlay.grid_scale = pixel_unit
            space_data.overlay.grid_subdivisions = 1
            space_data.overlay.show_floor = False
            context.scene.transform_orientation_slots[0].type = 'GLOBAL'
            context.scene.tool_settings.transform_pivot_point = 'CURSOR'
            context.scene.tool_settings.use_snap = True
            context.scene.tool_settings.snap_elements = {'INCREMENT'}
        # Remember what the current active operator is, when it changes
        # we know that the translate operator is complete
        self.watch_operator = context.active_operator

        # Countdown the frames passed through the timer. For some reason
        # the translate tool will not use the new grid scale if we switch
        # over immediately to translate.
        self.exec_counter = 5

        if context.object.mode == 'OBJECT':
            view_axis = sprytile_modal.VIEW3D_OP_SprytileModalTool.find_view_axis(context)
            if view_axis is not None:
                context.scene.sprytile_data.normal_mode = view_axis

        # Save the bmesh, and reference position
        self.bmesh = None
        self.ref_pos = self.get_ref_pos(context)

        args = self, context
        self.draw_handle = bpy.types.SpaceView3D.draw_handler_add(self.draw_callback, args, 'WINDOW', 'POST_PIXEL')
        UTIL_OP_SprytileGridTranslate.active_draw_handle = self.draw_handle

        win_mgr = context.window_manager
        self.timer = win_mgr.event_timer_add(0.1, window=context.window)
        win_mgr.modal_handler_add(self)
        context.scene.sprytile_data.is_grid_translate = True
        # Now go up to modal function to read the rest
        return {'RUNNING_MODAL'}

    def exit_modal(self, context):
        context.scene.sprytile_data.is_grid_translate = False
        pixel_unit = 1 / context.scene.sprytile_data.world_pixels
        # Restore grid settings if changed
        if self.restore_settings is not None:
            context.space_data.overlay.grid_scale = self.restore_settings['grid_scale']
            context.space_data.overlay.grid_subdivisions = self.restore_settings['grid_sub']
            context.space_data.overlay.show_floor = self.restore_settings['show_floor']
            context.scene.tool_settings.transform_pivot_point = self.restore_settings['pivot']
            context.scene.transform_orientation_slots[0].type = self.restore_settings['orient']
            context.scene.tool_settings.use_snap = self.restore_settings['use_snap']
            context.scene.tool_settings.snap_elements = self.restore_settings['snap_elements']
        # Didn't snap to grid, force to grid by calculating what the snapped translate would be
        else:
            op = context.active_operator
            if op is not None and op.bl_idname == 'TRANSFORM_OT_translate':
                # Take the translated value and snap it to pixel units
                translation = op.properties.value.copy()
                for i in range(3):
                    translation[i] = int(round(translation[i] / pixel_unit))
                    translation[i] *= pixel_unit
                # Move selection to where snapped position would be
                offset = translation - op.properties.value
                bpy.ops.transform.translate(value=offset)

        # Loop through the selected of the bmesh
        # if context.object.mode == 'EDIT' and context.scene.sprytile_data.snap_translate:
        #     for sel in self.bmesh.select_history:
        #         vert_list = []
        #         if isinstance(sel, BMFace) or isinstance(sel, BMEdge):
        #             for vert in sel.verts:
        #                 vert_list.append(vert)
        #         if isinstance(sel, BMVert):
        #             vert_list.append(sel)
        #         cursor_pos = context.scene.cursor.location
        #         for vert in vert_list:
        #             vert_offset = vert.co - cursor_pos
        #             vert_int = Vector((
        #                         int(round(vert_offset.x / pixel_unit)),
        #                         int(round(vert_offset.y / pixel_unit)),
        #                         int(round(vert_offset.z / pixel_unit))
        #                         ))
        #             new_vert_pos = cursor_pos + (vert_int * pixel_unit)
        #             vert.co = new_vert_pos

        self.bmesh = None
        bpy.types.SpaceView3D.draw_handler_remove(self.draw_handle, 'WINDOW')
        UTIL_OP_SprytileGridTranslate.active_draw_handle = None
        context.window_manager.event_timer_remove(self.timer)
        return {'FINISHED'}


class UTIL_OP_SprytileSnapCursor(bpy.types.Operator):
    bl_idname = "sprytile.snap_cursor"
    bl_label = "Snap Cursor (Spyrite Tile)"

    def modal(self, context, event):
        # Leaving edit mode pulls the mesh out from under the raycast
        if context.object is None or context.object.mode != 'EDIT':
            context.scene.sprytile_data.is_snapping = False
            bpy.context.window.cursor_modal_restore()
            return {'FINISHED'}

        if event.type == 'S' and event.value == 'RELEASE':
            context.scene.sprytile_data.is_snapping = False
            bpy.context.window.cursor_modal_restore()
            return {'FINISHED'}

        # Hand navigation to the viewport (see sprytile_core.is_view_navigation_event). The
        # navigation modal swallows the S release, so end snapping here
        # instead of waiting for a release that never arrives.
        if sprytile_core.is_view_navigation_event(context, event):
            context.scene.sprytile_data.is_snapping = False
            bpy.context.window.cursor_modal_restore()
            return {'FINISHED', 'PASS_THROUGH'}

        self.snap_cursor(context, event)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        return self.invoke(context, None)

    def invoke(self, context, event):
        if event.type == 'S' and event.value == 'RELEASE':
            context.scene.sprytile_data.is_snapping = False
            return {'CANCELLED'}
        
        self.bmesh = bmesh.from_edit_mesh(context.object.data)
        self.tree = BVHTree.FromBMesh(self.bmesh)
        self.snap_cursor(context, event)
        context.scene.sprytile_data.is_snapping = True

        # Add actual modal handler
        context.window_manager.modal_handler_add(self)
        bpy.context.window.cursor_modal_set("CROSSHAIR")

        return {'RUNNING_MODAL'}

    def snap_cursor(self, context, event):
        if self.tree is None or context.scene.sprytile_ui.use_mouse is True:
            return

        # get the context arguments
        scene = context.scene
        region = context.region
        rv3d = context.region_data
        coord = event.mouse_region_x, event.mouse_region_y

        # get the ray from the viewport and mouse
        ray_vector = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
        ray_origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)

        up_vector, right_vector, plane_normal = sprytile_core.get_current_grid_vectors(scene)

        if event.ctrl and event.value == 'PRESS':
            if scene.sprytile_data.cursor_snap == 'GRID':
               scene.sprytile_data.cursor_snap = 'VERTEX'
            else:
               scene.sprytile_data.cursor_snap = 'GRID'
            return
        
        if event.type in {'WHEELUPMOUSE', 'WHEELDOWNMOUSE'}:
            move_step = -1 if event.type == 'WHEELUPMOUSE' else 1
            
            target_grid = sprytile_core.get_grid(context, context.object.sprytile_gridid)
            pixel_move = 1 if event.shift else math.floor(target_grid.grid[1] / 2)
            
            step_vec = scene.sprytile_data.paint_normal_vector * (pixel_move / scene.sprytile_data.world_pixels) * move_step
            scene.cursor.location = scene.cursor.location + step_vec
            return

        # Snap cursor, depending on setting
        if scene.sprytile_data.cursor_snap == 'GRID':
            location = intersect_line_plane(ray_origin, ray_origin + ray_vector, scene.cursor.location, plane_normal)
            if location is None:
                return
            world_pixels = scene.sprytile_data.world_pixels
            target_grid = sprytile_core.get_grid(context, context.object.sprytile_gridid)
            grid_x = target_grid.grid[0]
            grid_y = target_grid.grid[1]

            grid_position, x_vector, y_vector = sprytile_core.get_grid_pos(
                location, scene.cursor.location,
                right_vector.copy(), up_vector.copy(),
                world_pixels, grid_x, grid_y
            )
            scene.cursor.location = grid_position

        elif scene.sprytile_data.cursor_snap == 'VERTEX':
            # Get if user is holding down tile picker modifier
            check_modifier = event.alt

            location, normal, face_index, distance = sprytile_builder.TileBuilder.raycast_object(context.object, ray_origin, ray_vector)
            if location is None:
                if check_modifier:
                   scene.sprytile_data.lock_normal = False
                return
            # Location in world space, convert to object space
            matrix = context.object.matrix_world.copy()
            matrix_inv = matrix.inverted()
            location, normal, face_index, dist = self.tree.find_nearest(matrix_inv @ location)
            if location is None:
                return

            # Found the nearest face, go to BMesh to find the nearest vertex
            if self.bmesh is None:
                #self.refresh_mesh = True
                return
            if face_index >= len(self.bmesh.faces) or face_index < 0:
                return
            face = self.bmesh.faces[face_index]
            closest_vtx = -1
            closest_dist = float('inf')
            # positions are in object space
            for vtx_idx, vertex in enumerate(face.verts):
                test_dist = (location - vertex.co).magnitude
                if test_dist < closest_dist:
                    closest_vtx = vtx_idx
                    closest_dist = test_dist
            # convert back to world space
            if closest_vtx != -1:
                scene.cursor.location = matrix @ face.verts[closest_vtx].co

            # If find face tile button pressed, set work plane normal too
            if check_modifier:
               sprytile_data = context.scene.sprytile_data
               # Check if mouse is hitting object
               target_normal = context.object.matrix_world.to_quaternion() @ normal
               face_up_vector, face_right_vector = sprytile_builder.TileBuilder.get_face_up_vector(context.object, context, face_index, 0.4)
               if face_up_vector is not None:
                   sprytile_data.paint_normal_vector = target_normal
                   sprytile_data.paint_up_vector = face_up_vector
                   sprytile_data.lock_normal = True


class UTIL_OP_SprytileTilePicker(bpy.types.Operator):
    bl_idname = "sprytile.tile_picker"
    bl_label = "Tile Picker (Spyrite Tile)"

    def modal(self, context, event):
        # Alt came up, or Alt+click is navigation here (Industry Compatible
        # orbits with it) or was turned into a middle mouse press by Emulate 3
        # Button Mouse, which also strips the Alt flag. Either way the event is
        # not ours: finish and let it through, the viewport swallows the Alt
        # release that would end us otherwise.
        if not event.alt or sprytile_core.is_view_navigation_event(context, event):
            bpy.context.window.cursor_modal_restore()
            context.scene.sprytile_data.is_picking = False
            return {'FINISHED', 'PASS_THROUGH'}

        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            self.tile_pick(context, event)

        return {'RUNNING_MODAL'}

    def execute(self, context):
        return self.invoke(context, None)

    def invoke(self, context, event):
        if event.alt and event.value == 'RELEASE':
            context.scene.sprytile_data.is_picking = False
            return {'CANCELLED'}
        
        self.bmesh = bmesh.from_edit_mesh(context.object.data)
        self.tree = BVHTree.FromBMesh(self.bmesh)

        # Add actual modal handler
        context.window_manager.modal_handler_add(self)
        bpy.context.window.cursor_modal_set("EYEDROPPER")
        context.scene.sprytile_data.is_picking = True

        return {'RUNNING_MODAL'}

    def tile_pick(self, context, event):
        if self.tree is None or context.scene.sprytile_ui.use_mouse is True:
            return None

        # get the context arguments
        region = context.region
        rv3d = context.region_data
        coord = event.mouse_region_x, event.mouse_region_y

        # get the ray from the viewport and mouse
        ray_vector = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
        ray_origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)

        work_layer_mask = sprytile_core.get_work_layer_data(context.scene.sprytile_data)
        location, normal, face_index, distance = sprytile_builder.TileBuilder.raycast_object(context.object, ray_origin,
                                                                     ray_vector, work_layer_mask=work_layer_mask)
        if location is None:
            return None

        face = self.bmesh.faces[face_index]

        grid_id, tile_packed_id, width, height, origin_id = sprytile_modal.VIEW3D_OP_SprytileModalTool.get_face_tiledata(self.bmesh, face)
        if None in {grid_id, tile_packed_id}:
            return None

        tilegrid = sprytile_core.get_grid(context, grid_id)
        if tilegrid is None:
            return None

        texture = sprytile_core.get_grid_texture(context.object, tilegrid)
        if texture is None:
            return None

        paint_setting_layer = self.bmesh.faces.layers.int.get('paint_settings')
        if paint_setting_layer is not None:
            paint_setting = face[paint_setting_layer]
            sprytile_core.from_paint_settings(context.scene.sprytile_data, paint_setting)

        # Extract the tile orientation/selection data packed in paint settings
        row_size = math.ceil(texture.size[0] / tilegrid.grid[0])
        tile_y = math.floor(tile_packed_id / row_size)
        tile_x = tile_packed_id % row_size
        if event.ctrl:
            width = 1
            height = 1
        elif origin_id > -1:
            origin_y = math.floor(origin_id / row_size)
            origin_x = origin_id % row_size
            tile_x = min(origin_x, tile_x)
            tile_y = min(origin_y, tile_y)

        if width == 0:
            width = 1
        if height == 0:
            height = 1

        context.object.sprytile_gridid = grid_id
        tilegrid.tile_selection[0] = tile_x
        tilegrid.tile_selection[1] = tile_y
        tilegrid.tile_selection[2] = width
        tilegrid.tile_selection[3] = height

        bpy.ops.sprytile.build_grid_list()
        return face_index


class UTIL_OP_SprytileSetNormal(bpy.types.Operator):
    bl_idname = "sprytile.set_normal"
    bl_label = "Set Normal (Spyrite Tile)"

    def modal(self, context, event):
        sprytile_preview.clear_preview_data()
        if event.type == 'N' and event.value == 'RELEASE':
            bpy.context.window.cursor_modal_restore()
            context.scene.sprytile_data.is_picking = False
            return {'FINISHED'}

        # Hand navigation to the viewport (see sprytile_core.is_view_navigation_event). The
        # navigation modal swallows the N release, so end here instead of
        # waiting for a release that never arrives.
        if sprytile_core.is_view_navigation_event(context, event):
            bpy.context.window.cursor_modal_restore()
            context.scene.sprytile_data.is_picking = False
            return {'FINISHED', 'PASS_THROUGH'}

        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            # get the context arguments
            region = context.region
            rv3d = context.region_data
            coord = event.mouse_region_x, event.mouse_region_y
            no_data = rv3d is None

            if no_data is False:
                # get the ray from the viewport and mouse
                ray_vector = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
                ray_origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)

                hit_loc, hit_normal, face_index, distance = sprytile_builder.TileBuilder.raycast_object(context.object, ray_origin, ray_vector)
                if hit_loc is None:
                    return {'RUNNING_MODAL'}

                face_up_vector, face_right_vector = sprytile_builder.TileBuilder.get_face_up_vector(context.object, context, face_index)
                if face_up_vector is None:
                    return {'RUNNING_MODAL'}

                sprytile_data = context.scene.sprytile_data
                sprytile_data.paint_normal_vector = hit_normal
                sprytile_data.paint_up_vector = face_up_vector
                sprytile_data.lock_normal = True
                #sprytile_data.paint_mode = 'MAKE_FACE'

        return {'RUNNING_MODAL'}

    def execute(self, context):
        return self.invoke(context, None)

    def invoke(self, context, event):
        if event.type == 'N' and event.value == 'RELEASE':
            return {'CANCELLED'}

        # Add actual modal handler
        context.scene.sprytile_data.is_picking = True
        context.window_manager.modal_handler_add(self)
        bpy.context.window.cursor_modal_set("CROSSHAIR")

        return {'RUNNING_MODAL'}


class UTIL_OP_SprytileResetData(bpy.types.Operator):
    bl_idname = "sprytile.reset_sprytile"
    bl_label = "Reset Spyrite Tile"
    bl_description = "In case sprytile breaks…"

    def invoke(self, context, event):
        context.scene.sprytile_data.auto_reload = False
        return {'FINISHED'}

    def execute(self, context):
        return self.invoke(context, None)


class UTIL_OP_SprytileFlipXToggle(bpy.types.Operator):
    bl_idname = "sprytile.flip_x_toggle"
    bl_label = "Toggle Flip X"

    def invoke(self, context, event):
        context.scene.sprytile_data.uv_flip_x = not context.scene.sprytile_data.uv_flip_x
        return {'FINISHED'}

    def execute(self, context):
        return self.invoke(context, None)


class UTIL_OP_SprytileFlipYToggle(bpy.types.Operator):
    bl_idname = "sprytile.flip_y_toggle"
    bl_label = "Toggle Flip Y"

    def invoke(self, context, event):
        context.scene.sprytile_data.uv_flip_y = not context.scene.sprytile_data.uv_flip_y
        return {'FINISHED'}

    def execute(self, context):
        return self.invoke(context, None)


class VIEW3D_MT_SprytileObjectDropDown(bpy.types.Menu):
    bl_idname = 'VIEW3D_MT_SprytileObjectDropDown'
    bl_label = "Spyrite Tile Utilites"
    bl_description = "Spyrite Tile helper functions"

    def draw(self, context):
        layout = self.layout
        layout.operator("sprytile.reset_sprytile")
        layout.separator()
        layout.operator("sprytile.setup_grid")
        layout.separator()
        layout.operator("sprytile.texture_setup")
        layout.operator("sprytile.viewport_setup")
        layout.separator()
        layout.operator("sprytile.material_setup")
        layout.operator("sprytile.add_new_material")
        layout.separator()
        layout.operator("sprytile.props_teardown")


class VIEW3D_PT_SprytileObjectPanel(bpy.types.Panel):
    bl_label = "Spyrite Tile Tools"
    bl_idname = "VIEW3D_PT_SprytileObjectPanel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Spyrite Tile"

    @classmethod
    def poll(cls, context):
        if context.object and context.object.type == 'MESH':
            return context.object.mode == 'OBJECT'
        return True

    def draw(self, context):
        layout = self.layout

        if hasattr(context.scene, "sprytile_data") is False:
            box = layout.box()
            box.label(text="Spyrite Tile Data Empty")
            box.operator("sprytile.props_setup")
            return

        layout.menu('VIEW3D_MT_SprytileObjectDropDown')

        selection_enabled = True
        if context.object is None:
            selection_enabled = False
        elif context.object.type != 'MESH':
            selection_enabled = False

        layout.prop(context.scene.sprytile_data, "world_pixels")
        box = layout.box()
        box.label(text="Material Setup")
        if selection_enabled:
            box.operator("sprytile.tileset_load")
            box.operator("sprytile.tileset_new")
        else:
            box.label(text="Select a mesh object to use Spyrite Tile")

        layout.separator()
        help_text = "Enter edit mode to use Paint Tools"
        label_wrap(layout.column(), help_text)

        # layout.separator()
        # box = layout.box()
        # box.label(text="Pixel Translate Options")
        # box.prop(context.scene.sprytile_data, "snap_translate", toggle=True)

        layout.separator()
        box = layout.box()
        box.label(text="Image Utilities")
        split = box.split(factor=0.3, align=True)
        split.prop(context.scene.sprytile_data, "auto_reload", toggle=True)
        split.operator("sprytile.reload_imgs")


class VIEW3D_MT_SprytileWorkDropDown(bpy.types.Menu):
    bl_idname = 'VIEW3D_MT_SprytileWorkDropDown'
    bl_label = "Spyrite Tile Utilites"
    bl_description = "Spyrite Tile helper functions"

    def draw(self, context):
        layout = self.layout
        layout.operator("sprytile.reset_sprytile")
        layout.separator()
        layout.operator("sprytile.setup_grid")
        layout.separator()
        layout.operator("sprytile.texture_setup")
        layout.operator("sprytile.viewport_setup")
        layout.separator()
        layout.operator("sprytile.material_setup")
        layout.operator("sprytile.add_new_material")
        layout.separator()
        layout.operator("sprytile.make_double_sided")
        layout.separator()
        layout.operator("sprytile.props_teardown")


class VIEW3D_PT_SprytileLayerPanel(bpy.types.Panel):
    bl_label = "Layers"
    bl_idname = "VIEW3D_PT_SprytileLayerPanel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Spyrite Tile"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        if context.object and context.object.type == 'MESH':
            return context.object.mode == 'EDIT'

    def draw(self, context):
        if hasattr(context.scene, "sprytile_data") is False:
            return
        data = context.scene.sprytile_data
        layout = self.layout
        box = layout.box()
        col = box.column_flow(align=True)
        col.prop(data, "set_work_layer", index=1, text="Decal Layer", toggle=True, expand=True)
        col.prop(data, "set_work_layer", index=0, text="Base Layer", toggle=True, expand=True)
        layout.prop(data, "mesh_decal_offset")

        # layout.prop(data, "work_layer_mode")
        # if data.work_layer_mode == 'MESH_DECAL':


class VIEW3D_PT_SprytileWorkflowPanel(bpy.types.Panel):
    bl_label = "Workflow"
    bl_idname = "VIEW3D_PT_SprytileWorkflowPanel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Spyrite Tile"

    @classmethod
    def poll(cls, context):
        if context.object and context.object.type == 'MESH':
            return context.object.mode == 'EDIT'

    def draw(self, context):
        layout = self.layout

        if hasattr(context.scene, "sprytile_data") is False:
            box = layout.box()
            box.label(text="Spyrite Tile Data Empty")
            box.operator("sprytile.props_setup")
            return

        data = context.scene.sprytile_data

        row = layout.row(align=False)
        row.label(text="", icon="GRID")

        dropdown_icon = "TRIA_DOWN" if data.axis_plane_settings else "TRIA_RIGHT"

        sub_row = row.row(align=True)
        sub_row.prop(data, "axis_plane_settings", icon=dropdown_icon, emboss=False, text="")
        sub_row.prop(data, "axis_plane_display", expand=True)

        if data.axis_plane_settings:
            addon_prefs = context.preferences.addons[__package__].preferences
            layout.prop(addon_prefs, "preview_transparency")
            layout.prop(data, "axis_plane_color")
            layout.prop(data, "axis_plane_size")

        row = layout.row(align=True)
        row.prop(context.scene.tool_settings, "use_transform_correct_face_attributes", toggle=True, text="", icon="UV")
        row.separator()
        row.prop(data, "cursor_flow", toggle=True, text="", icon="PIVOT_CURSOR")
        #row.label(text="", icon="SNAP_ON")
        row.prop(data, "cursor_snap", expand=True)

        layout.prop(data, "world_pixels", text="World Pixels")
        
        layout.menu("VIEW3D_MT_SprytileWorkDropDown")

        split = layout.split(factor=0.3, align=True)
        split.prop(data, "auto_reload", toggle=True)
        split.operator("sprytile.reload_imgs")
        
# module classes
classes = (
    UTIL_OP_SprytileAxisUpdate,
    UTIL_OP_SprytileGridAdd,
    UTIL_OP_SprytileGridRemove,
    UTIL_OP_SprytileGridCycle,
    UTIL_OP_SprytileStartTool,
    UTIL_OP_SprytileGridMove,
    UTIL_OP_SprytileNewMaterial,
    UTIL_OP_SprytileSetupMaterial,
    UTIL_OP_SprytileLoadTileset,
    UTIL_OP_SprytileNewTileset,
    UTIL_OP_SprytileSetupTexture,
    UTIL_OP_SprytileSetupViewport,
    UTIL_OP_SprytileValidateGridList,
    UTIL_OP_SprytileBuildGridList,
    UTIL_OP_SprytileRotateLeft,
    UTIL_OP_SprytileRotateRight,
    UTIL_OP_SprytileReloadImages,
    UTIL_OP_SprytileReloadImagesAuto,
    UTIL_OP_SprytileMakeDoubleSided,
    UTIL_OP_SprytileSetupGrid,
    UTIL_OP_SprytileGridTranslate,
    UTIL_OP_SprytileResetData,
    UTIL_OP_SprytileSnapCursor,
    UTIL_OP_SprytileTilePicker,
    UTIL_OP_SprytileSetNormal,
    UTIL_OP_SprytileFlipXToggle,
    UTIL_OP_SprytileFlipYToggle,
    VIEW3D_MT_SprytileObjectDropDown,
    VIEW3D_PT_SprytileObjectPanel,
    VIEW3D_MT_SprytileWorkDropDown,
    #VIEW3D_PT_SprytileLayerPanel,
    VIEW3D_PT_SprytileWorkflowPanel
)

def register():
    for cl in classes:
        bpy.utils.register_class(cl)


def unregister():
    # See the comment on active_draw_handle, a leftover handler crashes Blender
    # once the module it points into is unloaded
    if UTIL_OP_SprytileGridTranslate.active_draw_handle is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(
                UTIL_OP_SprytileGridTranslate.active_draw_handle, 'WINDOW')
        except Exception as err:
            print("Sprytile: could not remove translate draw handler:", err)
        UTIL_OP_SprytileGridTranslate.active_draw_handle = None

    for cl in classes:
        bpy.utils.unregister_class(cl)


if __name__ == '__main__':
    register()
