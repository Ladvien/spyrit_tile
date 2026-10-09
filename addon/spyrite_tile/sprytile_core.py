"""Pure-ish grid/material/paint-settings helpers split out of sprytile_utils.

Kept separate so api.reload_core() can importlib.reload them: operator classes
live in sprytile_utils and are registered with Blender, so that module cannot
be reloaded in place. Never import sprytile_utils/sprytile_modal/sprytile_gui
from here. Callers must use attribute access (sprytile_core.<name>), never
`from .sprytile_core import name`, so a reload is picked up.
"""
import bpy
import bmesh
import math
import sys

from mathutils import Matrix, Vector, Quaternion
from mathutils.geometry import intersect_line_plane, distance_point_to_plane
from mathutils.bvhtree import BVHTree
from bpy_extras import view3d_utils
from bpy.path import abspath
from os import path
from . import PAINT_ALIGN_BY_NUMBER, PAINT_ALIGN_NUMBER

def get_build_vertices(position, x_vector, y_vector, up_vector, right_vector):
    """Get the world position vertices for a new face, at the given position"""
    x_dot = right_vector.dot(x_vector.normalized())
    y_dot = up_vector.dot(y_vector.normalized())
    x_positive = x_dot > 0
    y_positive = y_dot > 0

    # These are in world positions
    vtx1 = position
    vtx2 = position + y_vector
    vtx3 = position + x_vector + y_vector
    vtx4 = position + x_vector

    # Quadrant II, IV
    face_order = (vtx1, vtx2, vtx3, vtx4)
    # Quadrant I, III
    if x_positive == y_positive:
        face_order = (vtx1, vtx4, vtx3, vtx2)

    return face_order


def prune_textureless_grids(context):
    """Drop Sprytile entries for materials that have no image texture node.

    Those cannot act as a tileset, so the entry is dead weight the user cannot
    get rid of by hand: the "add missing materials" pass in validate_grids
    recreated it every time. Blender's default "Material" is the usual case.

    Kept separate from validate_grids so it can run on tool start without that
    operator's side effect of reassigning the object's active grid.
    """
    mat_list = bpy.data.materials
    mat_data_list = context.scene.sprytile_mats

    remove_idx = []
    for idx, mat_data in enumerate(mat_data_list.values()):
        mat_idx = mat_list.find(mat_data.mat_id)
        if mat_idx < 0:
            continue
        if get_material_texture_node(mat_list[mat_idx]) is None:
            remove_idx.append(idx)

    if not remove_idx:
        return False

    for idx in reversed(remove_idx):
        mat_data_list.remove(idx)
    bpy.ops.sprytile.build_grid_list()
    return True


def mouse_over_ui_region(context, event):
    """True when the cursor sits over one of Blender's own panels.

    With region overlap the WINDOW region runs underneath the toolbar, sidebar
    and headers, so anything Sprytile draws or handles there would otherwise
    swallow the clicks meant for their buttons. Window space coordinates are
    used because every region shares them.
    """
    area = context.area
    if area is None or event is None:
        return False

    mouse_x, mouse_y = event.mouse_x, event.mouse_y
    for region in area.regions:
        if region.type == 'WINDOW':
            continue
        # Collapsed regions report a size of 1
        if region.width <= 1 or region.height <= 1:
            continue
        if (region.x <= mouse_x < region.x + region.width and
                region.y <= mouse_y < region.y + region.height):
            return True
    return False


# Event types that only ever mean "move the view", whatever the keymap says
NAVIGATION_EVENT_TYPES = {
    'MIDDLEMOUSE',
    'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'WHEELINMOUSE', 'WHEELOUTMOUSE',
    'WHEELLEFTMOUSE', 'WHEELRIGHTMOUSE',
    'TRACKPADPAN', 'TRACKPADZOOM', 'MOUSEROTATE', 'MOUSESMARTZOOM',
    'NDOF_MOTION',
}

# 3D View keymap items that move the view, matched by operator name
NAVIGATION_OPERATORS = {
    'view3d.rotate', 'view3d.move', 'view3d.zoom', 'view3d.dolly',
    'view3d.view_orbit', 'view3d.view_pan', 'view3d.view_roll',
    'view3d.navigate', 'view3d.view_center_pick',
    'view3d.ndof_orbit', 'view3d.ndof_orbit_zoom', 'view3d.ndof_pan', 'view3d.ndof_all',
}


def is_view_navigation_event(context, event):
    """True when this event starts or continues moving the viewport.

    Sprytile's Alt (tile picker), S (snap cursor) and N (set normal) modals
    stay alive while their key is held and used to swallow every event. That
    broke orbit, pan and zoom in keymaps that navigate with a modifier, such
    as Industry Compatible (Alt+LMB/MMB/RMB) and Emulate 3 Button Mouse
    (Alt+LMB arrives as a middle mouse press). The check goes by the active
    keymap, so a user remapped navigation is honoured too.
    """
    if event.type in NAVIGATION_EVENT_TYPES:
        return True
    # Mouse moves (value NOTHING), releases and key repeats never start a navigation
    if event.value not in {'PRESS', 'CLICK', 'CLICK_DRAG', 'DOUBLE_CLICK'}:
        return False

    keyconfig = context.window_manager.keyconfigs.active
    keymap = keyconfig.keymaps.get('3D View') if keyconfig is not None else None
    if keymap is None:
        return False

    def modifier_matches(kmi_state, event_state):
        # -1 is "any state"
        return kmi_state == -1 or bool(kmi_state) == bool(event_state)

    for kmi in keymap.keymap_items:
        if kmi.idname not in NAVIGATION_OPERATORS or not kmi.active or kmi.type != event.type:
            continue
        # Click and drag items start with a plain press
        if kmi.value not in {event.value, 'ANY', 'CLICK', 'CLICK_DRAG', 'DOUBLE_CLICK'}:
            continue
        if kmi.any or (modifier_matches(kmi.shift, event.shift) and
                       modifier_matches(kmi.ctrl, event.ctrl) and
                       modifier_matches(kmi.alt, event.alt) and
                       modifier_matches(kmi.oskey, event.oskey)):
            return True
    return False



def get_ortho2D_matrix(left, right, bottom, top):
    rl = right - left
    rl2 = right + left
    tb = top - bottom
    tb2 = top + bottom
    
    return Matrix([(2.0 / rl, 0, 0, -(rl2 / rl)), (0, 2.0 / tb, 0, -(tb2 / tb)), (0, 0, -1, 0), (0, 0, 0, 1)])

def get_current_grid_vectors(scene, with_rotation=True):
    """Returns the current grid X/Y/Z vectors from scene data
    :param scene: scene data
    :param with_rotation: bool, rotate the grid vectors by sprytile_data
    :return: up_vector, right_vector, normal_vector
    """
    data_normal = scene.sprytile_data.paint_normal_vector
    data_up_vector = scene.sprytile_data.paint_up_vector

    normal_vector = Vector((data_normal[0], data_normal[1], data_normal[2]))
    up_vector = Vector((data_up_vector[0], data_up_vector[1], data_up_vector[2]))

    normal_vector.normalize()
    up_vector.normalize()
    right_vector = up_vector.cross(normal_vector)

    if with_rotation:
        rotation = Quaternion(-normal_vector, scene.sprytile_data.mesh_rotate)
        up_vector = rotation @ up_vector
        right_vector = rotation @ right_vector

    return up_vector, right_vector, normal_vector


def grid_is_single_pixel(grid):
    is_pixel = grid.grid[0] == 1 and grid.grid[1] == 1 and grid_no_spacing(grid)
    return is_pixel


def grid_no_spacing(grid):
    no_spacing = grid.padding[0] == 0 and grid.padding[0] == 0 and \
                 grid.margin[0] == 0 and grid.margin[1] == 0 and \
                 grid.margin[2] == 0 and grid.margin[3] == 0
    return no_spacing


def get_grid_ids(context, grid, select_coords):
    """Convert an array of selection X/Y coordinates to grid ids"""
    target_img = get_grid_texture(context.object, grid)
    if target_img is None:
        return None

    row_size = math.ceil(target_img.size[0] / grid.grid[0])
    grid_ids = []
    for x, y in select_coords:
        tile_id = (y * row_size) + x
        grid_ids.append(tile_id)
    return grid_ids


def get_grid_selection_coords(grid):
    tile_sel = grid.tile_selection
    selection_array = []
    for y in range(tile_sel[3]):
        for x in range(tile_sel[2]):
            coord = (tile_sel[0] + x, tile_sel[1] + y)
            selection_array.append(coord)
    return selection_array


def get_grid_selection_ids(context, grid):
    coords = get_grid_selection_coords(grid)
    sel_size = (grid.tile_selection[2], grid.tile_selection[3])
    grid_ids = get_grid_ids(context, grid, coords)
    return coords, sel_size, grid_ids


def snap_vector_to_axis(vector, mirrored=False):
    """Snaps a vector to the closest world axis"""
    norm_vector = vector.normalized()

    x = Vector((1.0, 0.0, 0.0))
    y = Vector((0.0, 1.0, 0.0))
    z = Vector((0.0, 0.0, 1.0))

    x_dot = 1 - abs(norm_vector.dot(x))
    y_dot = 1 - abs(norm_vector.dot(y))
    z_dot = 1 - abs(norm_vector.dot(z))
    dot_array = [x_dot, y_dot, z_dot]
    closest = min(dot_array)

    if closest is dot_array[0]:
        snapped_vector = x
    elif closest is dot_array[1]:
        snapped_vector = y
    else:
        snapped_vector = z

    vector_dot = norm_vector.dot(snapped_vector)
    if mirrored is False and vector_dot < 0:
        snapped_vector *= -1
    elif mirrored is True and vector_dot > 0:
        snapped_vector *= -1

    return snapped_vector


def get_grid_pos(position, grid_center, right_vector, up_vector, world_pixels, grid_x, grid_y, as_coord=False):
    """Snaps a world position to the given grid settings"""
    position_vector = position - grid_center
    pos_vector_normalized = position.normalized()

    if not as_coord:
        if right_vector.dot(pos_vector_normalized) < 0:
            right_vector *= -1
        if up_vector.dot(pos_vector_normalized) < 0:
            up_vector *= -1

    x_magnitude = position_vector.dot(right_vector)
    y_magnitude = position_vector.dot(up_vector)

    x_unit = grid_x / world_pixels
    y_unit = grid_y / world_pixels

    x_snap = math.floor(x_magnitude / x_unit)
    y_snap = math.floor(y_magnitude / y_unit)

    right_vector *= x_unit
    up_vector *= y_unit

    if as_coord:
        return Vector((x_snap, y_snap)), right_vector, up_vector

    grid_pos = grid_center + (right_vector * x_snap) + (up_vector * y_snap)

    return grid_pos, right_vector, up_vector


def get_grid_right_up(right_vector, up_vector, world_pixels, grid_x, grid_y):
    x_unit = grid_x / world_pixels
    y_unit = grid_y / world_pixels
    right_vector *= x_unit
    up_vector *= y_unit
    return right_vector, up_vector


def get_workplane_area(width, height):
    offset_ids, offset_grid, coord_min, coord_max = get_grid_area(width, height)
    return [coord_min[0] - 1, coord_min[1] - 1], coord_max


def get_grid_area(width, height, flip_x=False, flip_y=False):
    """
    Get the grid and tile ID offset, for a given dimension
    :param width:
    :param height:
    :param flip_x:
    :param flip_y:
    :return: offset_tile_ids, offset_grid
    """
    offset_x = int(width/2)
    offset_y = int(height/2)
    if width % 2 == 0:
        offset_x -= 1
    if height % 2 == 0:
        offset_y -= 1

    offset_x *= -1
    offset_y *= -1

    offset_tile_ids = []
    offset_grid = []
    coords_min = [sys.maxsize, sys.maxsize]
    coords_max = [-sys.maxsize, -sys.maxsize]
    for y in range(height):
        for x in range(width):
            # Calculate tile offset
            tile_offset = (width - 1 - x if flip_x else x,
                           height - 1 - y if flip_y else y)
            offset_tile_ids.append(tile_offset)

            # Calculate grid offset
            grid_offset = (x + offset_x, y + offset_y)

            coords_min[0] = min(grid_offset[0], coords_min[0])
            coords_min[1] = min(grid_offset[1], coords_min[1])
            coords_max[0] = max(grid_offset[0], coords_max[0])
            coords_max[1] = max(grid_offset[1], coords_max[1])

            offset_grid.append(grid_offset)
    return offset_tile_ids, offset_grid, coords_min, coords_max


def raycast_grid(scene, context, up_vector, right_vector, plane_normal, ray_origin, ray_vector, as_coord=False):
    """
    Raycast to a plane on the scene cursor, and return the grid snapped position
    :param scene:
    :param context:
    :param up_vector:
    :param right_vector:
    :param plane_normal:
    :param ray_origin:
    :param ray_vector:
    :param as_coord: If position should be returned as world position or grid coordinate
    :return: grid_position, x_vector, y_vector, plane_pos
    """

    plane_pos = intersect_line_plane(ray_origin, ray_origin + ray_vector, scene.cursor.location, plane_normal)
    # Didn't hit the plane exit
    if plane_pos is None:
        return None, None, None, None

    world_pixels = scene.sprytile_data.world_pixels
    target_grid = get_grid(context, context.object.sprytile_gridid)
    grid_x = target_grid.grid[0]
    grid_y = target_grid.grid[1]

    grid_position, x_vector, y_vector = get_grid_pos(
                                            plane_pos, scene.cursor.location,
                                            right_vector.copy(), up_vector.copy(),
                                            world_pixels, grid_x, grid_y, as_coord
                                        )
    if x_vector.normalized().dot(right_vector) < 0:
        x_vector *= -1
        grid_position -= x_vector
    if y_vector.normalized().dot(up_vector) < 0:
        y_vector *= -1
        grid_position -= y_vector
    return grid_position, x_vector, y_vector, plane_pos


def get_grid_matrix(sprytile_grid):
    """Returns the transform matrix of a sprytile grid"""
    offset_mtx = Matrix.Translation((sprytile_grid.offset[0], sprytile_grid.offset[1], 0))
    rotate_mtx = Matrix.Rotation(sprytile_grid.rotate, 4, 'Z')
    return offset_mtx @ rotate_mtx


def get_material_texture_node(mat):
    """
    Returns the first image texture node applied to a material
    :param mat: Material
    :return: ShaderNodeImageTexImage or None
    """
    if mat.node_tree is None:
        return None
        
    for node in mat.node_tree.nodes:
        if node.bl_static_type == 'TEX_IMAGE':
            return node

    return None


def get_material_texture(mat):
    """
    Returns the texture applied to a material
    :param mat: Material
    :return: Texture or None
    """
    texture_img = get_material_texture_node(mat)

    if texture_img:
        return texture_img.image
    else:
        return None


def set_material_texture(mat, texture):
    """
    Apply texture (if possible) to a material
    :param mat: Material
    :param mat: Texture image to apply
    :return: True if successful
    """
    texture_img = get_material_texture_node(mat)

    if texture_img:
        texture_img.image = texture
        return True
    else:
        return False


def get_grid_material(sprytile_grid):
    """
    Given the sprytile_grid, returns the corresponding material
    :param sprytile_grid: the sprytile grid applied to the object
    :return: Material or None
    """
    mat_idx = bpy.data.materials.find(sprytile_grid.mat_id)
    if mat_idx != -1 and bpy.data.materials[mat_idx] is not None:
        return bpy.data.materials[mat_idx]
    
    return None

def get_grid_texture(obj, sprytile_grid):
    """
    Returns the texture applied to an object, given the sprytile_grid
    :param obj: the Blender mesh object
    :param sprytile_grid: the sprytile grid applied to the object
    :return: Texture or None
    """
    material = get_grid_material(sprytile_grid)

    if material is None:
        return None
    
    return get_material_texture(material) or None

def has_material(obj, material):
    """
    Checks if the given object has the given material
    :param obj: the Blender mesh object
    :param material: the material to search
    :return: True or False
    """
    for slot in obj.material_slots:
        if slot.material == material:
            return True
    
    return False

def get_selected_grid(context):
    """
    Returns the sprytile_grid currently selected
    :param context: Blender tool context
    :return: sprytile_grid or None
    """
    obj = context.object
    scene = context.scene

    mat_list = scene.sprytile_mats
    # The selected mesh object has the current sprytile_grid id
    grid_id = obj.sprytile_gridid

    return get_grid(context, grid_id)


def get_grid(context, grid_id):
    """
    Returns the sprytile_grid with the given id
    :param context: Blender tool context
    :param grid_id: grid id
    :return: sprytile_grid or None
    """
    mat_list = context.scene.sprytile_mats
    for mat_data in mat_list:
        for grid in mat_data.grids:
            if grid.id == grid_id:
                return grid
    return None


def get_highest_grid_id(context):
    return get_scene_highest_grid_id(context.scene)


def get_scene_highest_grid_id(scene):
    highest_id = -1
    for mat_data in scene.sprytile_mats:
        for grid in mat_data.grids:
            highest_id = max(grid.id, highest_id)
    return highest_id


def ensure_scene_setup(scene):
    """Make sure Spyrite Tile's scene and object properties exist, return scene.sprytile_data.

    The properties are registered with the add-on and are removed again by the
    "Remove Sprytile data" operator. Registering them is what the
    sprytile.props_setup operator does, so this is a no-op unless they were
    torn down.
    """
    if not hasattr(bpy.types.Scene, "sprytile_data") or not hasattr(bpy.types.Object, "sprytile_gridid"):
        from . import PROP_OP_SprytilePropsSetup
        PROP_OP_SprytilePropsSetup.props_setup()
    return scene.sprytile_data


def get_mat_data(context, mat_id):
    mat_list = context.scene.sprytile_mats
    for mat_data in mat_list:
        if mat_data.mat_id == mat_id:
            return mat_data
    return None

def get_current_tool(context):
    '''
    Returns the active tool in edit mode
    '''
    cur_tool = context.workspace.tools.from_space_view3d_mode('EDIT_MESH', create=False)
    if cur_tool is None:
        return None
    return cur_tool.idname


def get_paint_settings(sprytile_data):
    '''
    Returns the paint settings bitmask from a sprytile_data instance
    :param sprytile_data: sprytile_data instance
    :return: A bitmask representing the paint settings in the sprytile_data
    '''
    # Rotation and UV flip are always included
    paint_settings = 0
    # Flip x/y are toggles
    paint_settings += (1 if sprytile_data.uv_flip_x else 0) << 9
    paint_settings += (1 if sprytile_data.uv_flip_y else 0) << 8
    # Rotation is encoded as 0-3 clockwise, bit shifted by 10
    degree_rotation = round(math.degrees(sprytile_data.mesh_rotate), 0)
    if degree_rotation < 0:
        degree_rotation += 360
    rot_val = 0
    if degree_rotation <= 1:
        rot_val = 0
    elif degree_rotation <= 90:
        rot_val = 3
    elif degree_rotation <= 180:
        rot_val = 2
    elif degree_rotation <= 270:
        rot_val = 1
    paint_settings += rot_val << 10

    if sprytile_data.paint_mode == 'MAKE_FACE':
        paint_settings += 5  # Default center align
        for x in range(4, 8):  # All toggles on
            paint_settings += 1 << x
    if sprytile_data.paint_mode == 'PAINT':
        paint_settings += PAINT_ALIGN_NUMBER[sprytile_data.paint_align]
        paint_settings += (1 if sprytile_data.paint_uv_snap else 0) << 7
        paint_settings += (1 if sprytile_data.paint_edge_snap else 0) << 6
        paint_settings += (1 if sprytile_data.paint_stretch_x else 0) << 5
        paint_settings += (1 if sprytile_data.paint_stretch_y else 0) << 4
    return paint_settings


def from_paint_settings(sprytile_data, paint_settings):
    """
    Sets the paint settings of a sprytile_data using the paint settings bitmask
    :param sprytile_data: sprytile_data instance to set
    :param paint_settings: Painting settings bitmask
    :return: None
    """
    if paint_settings == 0:
        return
    align_value = paint_settings & 15  # First four bits
    rot_value = (paint_settings & 3072) >> 10  # 11th and 12th bit, shifted back
    rot_radian = 0
    if rot_value == 1:
        rot_radian = math.radians(270)
    if rot_value == 2:
        rot_radian = math.radians(180)
    if rot_value == 3:
        rot_radian = math.radians(90)

    if align_value in PAINT_ALIGN_BY_NUMBER:
        sprytile_data.paint_align = PAINT_ALIGN_BY_NUMBER[align_value]
    sprytile_data.mesh_rotate = rot_radian
    sprytile_data.uv_flip_x = (paint_settings & 1 << 9) > 0
    sprytile_data.uv_flip_y = (paint_settings & 1 << 8) > 0
    sprytile_data.paint_uv_snap = (paint_settings & 1 << 7) > 0
    sprytile_data.paint_edge_snap = (paint_settings & 1 << 6) > 0
    sprytile_data.paint_stretch_x = (paint_settings & 1 << 5) > 0
    sprytile_data.paint_stretch_y = (paint_settings & 1 << 4) > 0


def get_work_layer_data(sprytile_data):
    """
    Returns the work layer bitmask from the given sprytile data
    """
    # Bits 0-4 are reserved for storing layer numbers
    # Bit 5 = Face is using decal mode
    # Bit 6 = Face is using UV mode

    # When face is using UV mode, there may be multiple
    # UV layers, to find which layers it is using,
    # Mask against bits 0-4

    # This is only for 1 layer decals, figure out multi layer later
    out_data = 0
    if sprytile_data.work_layer != 'BASE':
        out_data += (1 << 0)
        if sprytile_data.work_layer_mode == 'MESH_DECAL':
            out_data += (1 << 5)
        else:
            out_data += (1 << 6)
    return out_data


def from_work_layer_data(sprytile_data, layer_data):
    pass


def setup_tile_material(material, image=None, closest=True):
    """Turn a material into the shadeless, alpha-cut one Sprytile tiles use.

    Rebuilds the node tree around a single image texture node. With image
    None the material's existing texture is kept. closest sets the node's
    interpolation to Closest, which pixel art needs for crisp texels.
    """
    # Make material equivalent to a shadeless transparent one in Blender 2.7
    material.surface_render_method = 'DITHERED'

    # Keep the material's current texture unless a new one was given
    if image is None:
        image = get_material_texture(material)

    # Setup nodes
    nodes = material.node_tree.nodes
    nodes.clear()
    output_n = nodes.new(type = 'ShaderNodeOutputMaterial')
    light_path_n = nodes.new(type = 'ShaderNodeLightPath')
    transparent_n = nodes.new(type = 'ShaderNodeBsdfTransparent')
    emission_n = nodes.new(type = 'ShaderNodeEmission')
    mix_cam_ray_n = nodes.new(type = 'ShaderNodeMixShader')
    mix_alpha_n = nodes.new(type = 'ShaderNodeMixShader')
    texture_n = nodes.new(type = 'ShaderNodeTexImage')

    # link
    links = material.node_tree.links
    links.new(texture_n.outputs['Color'], emission_n.inputs['Color'])
    links.new(texture_n.outputs['Alpha'], mix_alpha_n.inputs['Fac'])
    links.new(transparent_n.outputs['BSDF'], mix_alpha_n.inputs[1])
    links.new(transparent_n.outputs['BSDF'], mix_cam_ray_n.inputs[1])
    links.new(emission_n.outputs['Emission'], mix_alpha_n.inputs[2])
    links.new(mix_alpha_n.outputs['Shader'], mix_cam_ray_n.inputs[2])
    links.new(light_path_n.outputs['Is Camera Ray'], mix_cam_ray_n.inputs['Fac'])
    links.new(mix_cam_ray_n.outputs['Shader'], output_n.inputs['Surface'])

    # reorder
    output_n.location = (400, 0)
    mix_cam_ray_n.location = (200, 0)
    light_path_n.location = (0, 250)
    mix_alpha_n.location = (0, -100)
    transparent_n.location = (-200, -100)
    emission_n.location = (-200, -200)
    texture_n.location = (-500, 100)

    if image:
        texture_n.image = image
    if closest:
        texture_n.interpolation = 'Closest'


# session_uid of the materials the user removed from the tileset list with
# the grid "-" button. validate_grids does not add them back; api.create_tileset
# lifts the mark. Only valid within one Blender session / loaded file.
_removed_tilesets = set()


def restore_removed_tileset(material):
    """Let validate_grids list the material again (it was removed with the grid "-" button)."""
    _removed_tilesets.discard(material.session_uid)


def validate_grids(scene, active_object=None):
    """Reconcile scene.sprytile_mats with the materials in the file.

    Re-points renamed materials, drops entries without a usable image
    texture node or without users, and adds a grid entry for every material
    that carries an image texture, except tilesets removed with the grid "-"
    button. When active_object is given its
    sprytile_gridid is moved to the newest grid. Then rebuilds the grid list.
    """
    mat_list = bpy.data.materials
    mat_data_list = scene.sprytile_mats

    # Validate the material IDs in scene.sprytile_mats
    for check_mat_data in mat_data_list:
        mat_idx = mat_list.find(check_mat_data.mat_id)
        if mat_idx > -1:
            continue

        # This mat data id not found in materials
        # Loop through materials looking for one
        # that doesn't appear in sprytile_mats list
        for check_mat in mat_list:
            if check_mat.session_uid in _removed_tilesets:
                continue
            mat_unused = True
            for mat_data in mat_data_list:
                if mat_data.mat_id == check_mat.name:
                    mat_unused = False
                    break

            if mat_unused:
                target_mat_id = check_mat_data.mat_id
                check_mat_data.mat_id = check_mat.name
                for grid in check_mat_data.grids:
                    grid.mat_id = check_mat.name
                for list_display in scene.sprytile_list.display:
                    if list_display.mat_id == target_mat_id:
                        list_display.mat_id = check_mat.name
                break

    remove_idx = []

    # Filter out mat data with invalid IDs or users
    for idx, mat in enumerate(mat_data_list.values()):
        mat_idx = mat_list.find(mat.mat_id)
        if mat_idx < 0:
            remove_idx.append(idx)
            continue
        if (mat.mat_id == "Dots Stroke"):
            remove_idx.append(idx)
            continue
        # A material with no image texture node cannot act as a tileset, so
        # an entry for it is dead weight the user cannot get rid of: it was
        # recreated by the loop below on every validate.
        if get_material_texture_node(mat_list[mat_idx]) is None:
            remove_idx.append(idx)
            continue
        if mat_list[mat_idx].users == 0:
            remove_idx.append(idx)
        for grid in mat.grids:
            grid.mat_id = mat.mat_id
    remove_idx.reverse()
    for idx in remove_idx:
        mat_data_list.remove(idx)

    # Loop through available materials, checking if mat_data_list has
    # at least one entry for each material
    for mat in mat_list:
        if mat.users == 0 or mat.session_uid in _removed_tilesets:
            continue
        is_mat_valid = False
        for mat_data in mat_data_list:
            if mat_data.mat_id == mat.name:
                is_mat_valid = True
                break
        # Only materials that carry an image texture node can be tilesets.
        # Without this every material in the file, including Blender's
        # default "Material", showed up in the Sprytile list permanently.
        if get_material_texture_node(mat) is None:
            continue
        if is_mat_valid is False and mat.name != "Dots Stroke":
            mat_data_entry = mat_data_list.add()
            mat_data_entry.mat_id = mat.name
            mat_grid = mat_data_entry.grids.add()
            mat_grid.mat_id = mat.name
            mat_grid.id = get_scene_highest_grid_id(scene) + 1

            addon_prefs = bpy.context.preferences.addons[__package__].preferences
            if addon_prefs:
                mat_grid.grid = addon_prefs.default_grid
                mat_grid.auto_pad_offset = addon_prefs.default_pad_offset

    if active_object is not None:
        active_object.sprytile_gridid = get_scene_highest_grid_id(scene)
    build_grid_list(scene, active_object)


def build_grid_list(scene, active_object=None):
    """Build the scene.sprytile_list.display from scene.sprytile_mats

    active_object picks the highlighted list entry; None leaves it alone.
    """
    display_list = scene.sprytile_list.display
    mat_list = scene.sprytile_mats

    display_list.clear()
    for mat_data in mat_list:
        mat_display = display_list.add()
        mat_display.mat_id = mat_data.mat_id
        if mat_data.is_expanded is False:
            continue
        for mat_grid in mat_data.grids:
            idx = len(display_list)
            grid_display = display_list.add()
            grid_display.grid_id = mat_grid.id
            grid_display.parent_mat_name = mat_display.mat_name
            grid_display.parent_mat_id = mat_display.mat_id
            if active_object is not None and active_object.sprytile_gridid == grid_display.grid_id:
                scene.sprytile_list.idx = idx
