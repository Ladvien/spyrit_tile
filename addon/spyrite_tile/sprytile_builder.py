import bmesh
import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.geometry import distance_point_to_plane

from . import sprytile_uv
from . import sprytile_utils
from .sprytile_uv import UvDataLayers


class TileBuilder:
    """Builds and remaps tile faces in the edit-mesh bmesh of one object.

    Holds the bmesh, its BVH tree and a flag telling the owner that the mesh
    needs a refresh. Owned by the paint modal operator; no operator state is
    needed here, so the same code can build tiles without a modal running.
    """

    def __init__(self, context, obj):
        self.bmesh = bmesh.from_edit_mesh(obj.data)
        self.tree = BVHTree.FromBMesh(self.bmesh)
        self.refresh_mesh = False

    def face_to_world_verts(self, context, face_index):
        if face_index is None:
            pass
        face = self.bmesh.faces[face_index]
        world_verts = []
        for idx, vert in enumerate(face.verts):
            vert_world_pos = context.object.matrix_world @ vert.co
            world_verts.append(vert_world_pos)
        return world_verts

    def raycast_grid_coord(self, context, x, y, up_vector, right_vector, normal, work_layer_mask=0, *, grid_origin):
        """
        Raycast agains the object using grid coordinates around grid_origin
        :param context:
        :param x:
        :param y:
        :param up_vector:
        :param right_vector:
        :param normal:
        :param work_layer_mask:
        :param grid_origin: World position of the grid origin (Vector)
        :return:
        """
        obj = context.object

        ray_origin = Vector(grid_origin.copy())
        ray_origin += (x + 0.5) * right_vector
        ray_origin += (y + 0.5) * up_vector

        ray_offset = 0.01
        ray_origin += normal * ray_offset

        ray_direction = -normal

        return TileBuilder.raycast_object(obj, ray_origin, ray_direction, ray_dist=ray_offset*2,
                                   work_layer_mask=work_layer_mask)

    @staticmethod
    def raycast_object(obj, ray_origin, ray_direction, ray_dist=1000.0,
                       world_normal=False, work_layer_mask=0, pass_dist=0.001):
        matrix = obj.matrix_world.copy()
        # get the ray relative to the object
        matrix_inv = matrix.inverted()
        ray_origin_obj = matrix_inv @ ray_origin
        ray_target_obj = matrix_inv @ (ray_origin + ray_direction)
        ray_direction_obj = ray_target_obj - ray_origin_obj
        mesh = bmesh.from_edit_mesh(obj.data)
        tree = BVHTree.FromBMesh(mesh)

        location, normal, face_index, distance = tree.ray_cast(ray_origin_obj, ray_direction_obj, ray_dist)
        if face_index is None:
            return None, None, None, None

        face = mesh.faces[face_index]

        work_layer_id = mesh.faces.layers.int.get(UvDataLayers.WORK_LAYER)
        if work_layer_id is None:
            return None, None, None, None
        work_layer_value = face[work_layer_id]

        # Pass through faces under certain conditions
        do_pass_through = False
        # Layer mask not matching
        if work_layer_value != work_layer_mask:
            do_pass_through = True
        # Hit face is backface
        if face.normal.dot(ray_direction) > 0:
            do_pass_through = not bpy.context.scene.sprytile_data.allow_backface
        # Hit face is hidden
        if face.hide:
            do_pass_through = True

        # Translate location back to world space
        location = matrix @ location

        if do_pass_through:
            # add shift offset if passing through
            shift_vec = ray_direction.normalized() * pass_dist
            new_ray_origin = location + shift_vec
            return TileBuilder.raycast_object(obj, new_ray_origin, ray_direction, work_layer_mask=work_layer_mask)

        if world_normal:
            normal = matrix @ normal
        return location, normal, face_index, distance

    def update_bmesh_tree(self, context, update_index=False):
        self.bmesh = bmesh.from_edit_mesh(context.object.data)
        if update_index:
            # Verify layers are created
            TileBuilder.verify_bmesh_layers(self.bmesh)
            self.bmesh = bmesh.from_edit_mesh(context.object.data)
        self.tree = BVHTree.FromBMesh(self.bmesh)

    @staticmethod
    def verify_bmesh_layers(bmesh):
        # Verify layers are created
        for layer_name in UvDataLayers.LAYER_NAMES:
            layer_data = bmesh.faces.layers.int.get(layer_name)
            if layer_data is None:
                print('Creating face layer:', layer_name)
                bmesh.faces.layers.int.new(layer_name)

            for el in [bmesh.faces, bmesh.verts, bmesh.edges]:
                el.index_update()
                el.ensure_lookup_table()

            bmesh.loops.layers.uv.verify()

    def construct_face(self, context, grid_coord, grid_size,
                       tile_xy, tile_origin,
                       grid_up, grid_right,
                       up_vector, right_vector, plane_normal,
                       require_base_layer=False,
                       work_layer_mask=0,
                       threshold=None,
                       *, grid_origin):
        """
        Create a new face at grid_coord or remap the existing face
        :type work_layer_mask: bitmask integer
        :param context:
        :param grid_coord: Grid coordinate to create at
        :param grid_size: Tile unit size of face
        :param tile_xy: Tilegrid coordinate to map
        :param tile_origin: Origin of tilegrid coordinate, for mapping data
        :param grid_up:
        :param grid_right:
        :param up_vector:
        :param right_vector:
        :param plane_normal:
        :param require_base_layer:
        :param grid_origin: World position of the grid origin (Vector), before any decal offset
        :param threshold:
        :return:
        """
        scene = context.scene
        data = scene.sprytile_data

        # Run a raycast on target work layer mask
        hit_loc, hit_normal, face_index, hit_dist = self.raycast_grid_coord(
            context, grid_coord[0], grid_coord[1],
            grid_up, grid_right, plane_normal,
            work_layer_mask=work_layer_mask,
            grid_origin=grid_origin
        )

        # Didn't hit target layer, and require base layer
        if face_index is None and require_base_layer:
            # Check if there is a base layer underneath
            base_hit_loc, hit_normal, base_face_index, base_hit_dist = self.raycast_grid_coord(
                    context, grid_coord[0], grid_coord[1],
                    grid_up, grid_right, plane_normal,
                    grid_origin=grid_origin
                )
            # Didn't hit required base layer, do nothing
            if base_face_index is None:
                return None

        # Calculate where the origin of the grid is
        grid_origin = grid_origin.copy()
        # If doing mesh decal, offset the grid origin
        if data.work_layer == 'DECAL_1':
            grid_origin += plane_normal * data.mesh_decal_offset

        did_build = False
        # No face index, assume build face
        if face_index is None or face_index < 0:
            face_position = grid_origin + grid_coord[0] * grid_right + grid_coord[1] * grid_up

            face_verts = sprytile_utils.get_build_vertices(face_position,
                                                 grid_right * grid_size[0], grid_up * grid_size[1],
                                                 up_vector, right_vector)
            face_index = self.create_face(context, face_verts)
            did_build = True

        if face_index is None or face_index < 0:
            return None

        # Didn't create face, only want to remap face. Check for coplanarity and dot
        if did_build is False:
            check_dot = abs(plane_normal.dot(hit_normal))
            check_dot -= 1
            check_coplanar = distance_point_to_plane(hit_loc, grid_origin, plane_normal)

            check_coplanar = abs(check_coplanar) < 0.05
            check_dot = abs(check_dot) < 0.05
            # Can't remap face
            if not check_coplanar or not check_dot:
                return None

        sprytile_uv.uv_map_face(context, up_vector, right_vector,
                                tile_xy, tile_origin, face_index,
                                self.bmesh, grid_size)

        if did_build and data.auto_merge:
            if threshold is None:
                threshold = (1 / data.world_pixels) * 1.25

            face = self.bmesh.faces[face_index]

            face_position += grid_right * 0.5 + grid_up * 0.5
            face_position += plane_normal * 0.01
            face_index = self.merge_doubles(context, face, face_position, -plane_normal, threshold)

        # Auto merge refreshes the mesh automatically
        self.refresh_mesh = not data.auto_merge

        return face_index

    def merge_doubles(self, context, face, ray_origin, ray_direction, threshold):
        face.select = True
        work_layer_id = self.bmesh.faces.layers.int.get(UvDataLayers.WORK_LAYER)
        work_layer_value = face[work_layer_id]
        for check_face in self.bmesh.faces:
            check_face.select = check_face[work_layer_id] == work_layer_value

        merge_threshold = 0.00
        if context.scene.sprytile_data.work_layer != 'BASE':
            merge_threshold = 0.01
        bpy.ops.mesh.remove_doubles(threshold=merge_threshold, use_unselected=False)

        for el in [self.bmesh.faces, self.bmesh.verts, self.bmesh.edges]:
            el.index_update()
            el.ensure_lookup_table()

        self.bmesh.select_flush_mode()

        for iter_face in self.bmesh.faces:
            iter_face.select = False

        # Modified the mesh, refresh and raycast to find the new face index
        self.update_bmesh_tree(context)
        hit_loc, norm, new_face_idx, hit_dist = self.raycast_object(
            context.object,
            ray_origin,
            ray_direction,
            0.02
        )
        if new_face_idx is not None:
            self.bmesh.faces[new_face_idx].select = False
        return new_face_idx

    def create_face(self, context, world_vertices):
        """
        Create a face in the bmesh using the given world space vertices
        :param context:
        :param world_vertices: Vector array of world space positions
        :return:
        """
        face_vertices = []
        # Convert world space position to object space
        world_inv = context.object.matrix_world.copy().inverted()
        for face_vtx in world_vertices:
            vtx = self.bmesh.verts.new(face_vtx)
            vtx.co = world_inv @ vtx.co
            face_vertices.append(vtx)

        face = self.bmesh.faces.new(face_vertices)
        face.normal_update()

        for el in [self.bmesh.faces, self.bmesh.verts, self.bmesh.edges]:
            el.index_update()
            el.ensure_lookup_table()

        bmesh.update_edit_mesh(context.object.data, loop_triangles=True, destructive=True)

        # Update the collision BVHTree with new data
        self.refresh_mesh = True
        return face.index

    @staticmethod
    def get_face_up_vector(obj, context, face_index, sensitivity=0.1, bias_right=False):
        """
        Find the edge of the given face that most closely matches view up vector
        :param context:
        :param face_index:
        :param sensitivity:
        :param bias_right:
        :return:
        """
        # Get the view up vector. The default scene view camera is pointed
        # downward, with up on Y axis. Apply view rotation to get current up

        rv3d = context.region_data
        view_up_vector = rv3d.view_rotation @ Vector((0.0, 1.0, 0.0))
        view_right_vector = rv3d.view_rotation @ Vector((1.0, 0.0, 0.0))
        data = context.scene.sprytile_data
        mesh = bmesh.from_edit_mesh(obj.data)

        #if mesh is None or mesh.faces is None:
        #    self.refresh_mesh = True
        #    return None, None

        world_matrix = context.object.matrix_world
        face = mesh.faces[face_index]

        # Convert the face normal to world space
        normal_inv = context.object.matrix_world.copy().inverted().transposed()
        face_normal = normal_inv @ face.normal.copy()

        def calc_up_sel_vectors(vtx1, vtx2):
            edge_center = (vtx1 + vtx2) / 2
            face_center = world_matrix @ face.calc_center_bounds()
            # Get the rough heading of the up vector
            estimated_up = face_center - edge_center
            estimated_up.normalize()

            sel_vector = vtx2 - vtx1
            sel_vector.normalize()

            # Cross the face normal and hint vector to get the up vector
            view_up_vector = face_normal.cross(sel_vector)
            view_up_vector.normalize()

            # If the calculated up faces away from rough up, reverse it
            if view_up_vector.dot(estimated_up) < 0:
                view_up_vector *= -1
                sel_vector *= -1
            return view_up_vector, sel_vector

        do_hint = data.paint_mode in {'PAINT', 'SET_NORMAL'} and data.paint_hinting
        if do_hint:
            for edge in face.edges:
                if not edge.select:
                    continue
                vtx1 = world_matrix @ edge.verts[0].co
                vtx2 = world_matrix @ edge.verts[1].co
                view_up_vector, sel_vector = calc_up_sel_vectors(vtx1, vtx2)
                return view_up_vector, sel_vector
            # if face didn't have any selected edges, use the active edge selection
            selection = mesh.select_history.active
            if isinstance(selection, bmesh.types.BMEdge):
                vtx1 = world_matrix @ selection.verts[0].co.copy()
                vtx2 = world_matrix @ selection.verts[1].co.copy()
                view_up_vector, sel_vector = calc_up_sel_vectors(vtx1, vtx2)
                return view_up_vector, sel_vector
            # No edges or edge selection, use normal face up vector finding

        # Find the edge of the hit face that most closely matches
        # the view up / view right vectors
        closest_up = None
        closest_up_dot = 2.0
        closest_right = None
        closest_right_dot = 2.0
        idx = -1
        for edge in face.edges:
            idx += 1
            # Move vertices to world space
            vtx1 = world_matrix @ edge.verts[0].co
            vtx2 = world_matrix @ edge.verts[1].co
            edge_vec = vtx2 - vtx1
            edge_vec.normalize()
            edge_up_dot = 1 - abs(edge_vec.dot(view_up_vector))
            edge_right_dot = 1 - abs(edge_vec.dot(view_right_vector))
            # print(idx, edge_vec, "up dot", edge_up_dot, "right dot", edge_right_dot)
            if edge_up_dot < sensitivity and edge_up_dot < closest_up_dot:
                closest_up_dot = edge_up_dot
                closest_up = edge_vec
                # print("Setting", idx, "as closest up")
            if edge_right_dot < sensitivity and edge_right_dot < closest_right_dot:
                closest_right_dot = edge_right_dot
                closest_right = edge_vec
                # print("Setting", idx, "as closest right")

        # print("Closest indices: up", closest_up, "right", closest_right)
        chosen_up = None

        if closest_up is not None and not bias_right:
            if closest_up.dot(view_up_vector) < 0:
                closest_up *= -1
            chosen_up = closest_up
        elif closest_right is not None:
            if closest_right.dot(view_right_vector) < 0:
                closest_right *= -1
            chosen_up = face_normal.cross(closest_right)

        if do_hint and closest_right is not None:
            if closest_right.dot(view_right_vector) < 0:
                closest_right *= -1
            chosen_up = face_normal.cross(closest_right)

        # print("Chosen up", chosen_up)
        return chosen_up, closest_right

