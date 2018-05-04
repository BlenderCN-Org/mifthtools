# ***** BEGIN GPL LICENSE BLOCK *****
#
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.    See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software Foundation,
# Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.
#
# ***** END GPL LICENCE BLOCK *****


import bpy

import bgl
import blf

from bpy_extras import view3d_utils
import math
import mathutils as mathu
import random
from mathutils import Vector, Matrix
from bpy.props import EnumProperty, BoolProperty, IntProperty, CollectionProperty, BoolVectorProperty, PointerProperty

from . import mi_utils_base as ut_base
#from . import mi_color_manager as col_man
from . import mi_inputs
from . import add_mesh_capsule


class MI_MakePrimitive(bpy.types.Operator):
    """Modal object selection with a ray cast"""
    bl_idname = "mi_prims.mifth_make_prim"
    bl_label = "Make Primitive"
    bl_options = {'REGISTER', 'UNDO'}

    prim_type = EnumProperty(
        items=(('Plane', 'Plane', ''),
               ('Cube', 'Cube', ''),
               ('Circle', 'Circle', ''),
               ('Sphere', 'Sphere', ''),
               ('Cylinder', 'Cylinder', ''),
               ('Cone', 'Cone', ''),
               ('Capsule', 'Capsule', ''),
               ('Clone', 'Clone', '')
               ),
        default = 'Cylinder'
    )

    obj_matrices = None
    new_prim = None
    hit_pos = None
    hit_dir = None
    prim_side_vec = None
    prim_front_vec = None

    edit_obj = None
    objects_to_clone = []
    history_objects = []

    orient_on_surface = BoolProperty(default=False)
    center_is_cursor = BoolProperty(default=False)
    median_center = BoolProperty(default=False)

    circle_segments = [16]
    sphere_segments = [16, 8]
    capsule_segments = [16, 4]

    tool_mode = 'IDLE'  # IDLE, IDLE_2, DRAW_1, DRAW_2


    def invoke(self, context, event):
        if context.area.type == 'VIEW_3D':
            clean(context, self)  # clean old stuff

            if self.prim_type == 'Clone':
                if not context.selected_objects:
                    self.report({'WARNING'}, "No Selected Objects to Clone!")
                    return {'CANCELLED'}
                else:
                    self.objects_to_clone = context.selected_objects.copy()

            # the arguments we pass the the callback
            args = (self, context)

            # Add the region OpenGL drawing callback
            # draw in view space with 'POST_VIEW' and 'PRE_VIEW'
            self._handle = bpy.types.SpaceView3D.draw_handler_add(draw_callback_px, args, 'WINDOW', 'POST_PIXEL')

            mi_settings = context.scene.mi_settings

            # get all matrices of visible objects
            # Check if it's EDIT MODE
            if context.mode == 'EDIT_MESH':
                self.edit_obj = context.scene.objects.active
            self.obj_matrices = ut_base.get_obj_dup_meshes(mi_settings.snap_objects, mi_settings.convert_instances, context, add_active_obj=True)

            context.window_manager.modal_handler_add(self)

            return {'RUNNING_MODAL'}

        else:
            self.report({'WARNING'}, "Active space must be a View3d")
            return {'CANCELLED'}


    def modal(self, context, event):
        context.area.tag_redraw()

        # Tooltip
        tooltip_text = "Shift: Scale Constraint; Ctrl+MouseWheel, Ctrl+Shift+MouseWheel, +-, Ctrl++, ctrl+-: Change Segments; Shift+LeftClick: Uniform Scale; C: CenterCursor; O: Orient on Surface"
        context.area.header_text_set(tooltip_text)

        m_coords = event.mouse_region_x, event.mouse_region_y

        if event.type in {'MIDDLEMOUSE'}:
            # allow navigation
            return {'PASS_THROUGH'}

        elif event.type == 'O' and event.value == 'PRESS':
            if self.orient_on_surface:
                self.orient_on_surface = False
            else:
                self.orient_on_surface = True

            return {'RUNNING_MODAL'}

        elif event.type == 'C' and event.value == 'PRESS':
            if self.center_is_cursor:
                self.center_is_cursor = False
            else:
                self.center_is_cursor = True

            return {'RUNNING_MODAL'}

        elif event.type == 'M' and event.value == 'PRESS':
            if self.median_center:
                self.median_center = False
            else:
                self.median_center = True

            return {'RUNNING_MODAL'}

        elif event.type == 'Z' and event.value == 'PRESS' and event.ctrl:
            # delete object with Ctrl+Z
            if self.history_objects:
                del_obj = self.history_objects[-1][0]
                del self.history_objects[-1]

                # remove old primitive
                objs = bpy.data.objects
                objs.remove(objs[del_obj.name], True)

                self.new_prim = None
                self.hit_pos = None
                self.hit_dir = None
                self.prim_side_vec = None
                self.prim_front_vec = None

                self.tool_mode = 'IDLE'

                context.scene.update()
                context.area.tag_redraw()

            return {'RUNNING_MODAL'}


        elif event.type in {'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'NUMPAD_MINUS', 'MINUS', 'NUMPAD_PLUS', 'EQUAL'} and self.prim_type != 'Clone':

            # CHANGE SEGMENTS HERE
            if self.new_prim:
                if event.type in {'WHEELUPMOUSE', 'WHEELDOWNMOUSE'}:
                    if event.ctrl:
                        # Meka less/more segments
                        if event.type == 'WHEELUPMOUSE':
                            if self.prim_type == 'Sphere':
                                if event.shift:
                                    self.sphere_segments[1] = self.sphere_segments[1] + 1
                                else:
                                    self.sphere_segments[0] = self.sphere_segments[0] + 1

                            elif self.prim_type == 'Capsule':
                                if event.shift:
                                    self.capsule_segments[1] = self.capsule_segments[1] + 1
                                else:
                                    self.capsule_segments[0] = self.capsule_segments[0] + 1

                            else:
                                self.circle_segments[0] += 1
                        else:
                            if self.prim_type == 'Sphere':
                                if event.shift:
                                    self.sphere_segments[1] = self.sphere_segments[1] - 1
                                    self.sphere_segments[1] = max(self.sphere_segments[1], 3)
                                else:
                                    self.sphere_segments[0] = self.sphere_segments[0] - 1
                                    self.sphere_segments[0] = max(self.sphere_segments[0], 3)

                            elif self.prim_type == 'Capsule':
                                if event.shift:
                                    self.capsule_segments[1] = self.capsule_segments[1] - 1
                                    self.capsule_segments[1] = max(self.capsule_segments[1], 1)
                                else:
                                    self.capsule_segments[0] = self.capsule_segments[0] - 1
                                    self.capsule_segments[0] = max(self.capsule_segments[0], 3)

                            else:
                                self.circle_segments[0] -= 1
                                self.circle_segments[0] = max(self.circle_segments[0], 3)
                    else:
                        # allow navigation
                        return {'PASS_THROUGH'}

                elif event.type in {'NUMPAD_PLUS', 'EQUAL'} and event.value == 'PRESS':
                    if self.prim_type == 'Sphere':
                        if event.ctrl:
                            self.sphere_segments[1] = self.sphere_segments[1] + 1
                        else:
                            self.sphere_segments[0] = self.sphere_segments[0] + 1

                    elif self.prim_type == 'Capsule':
                        if event.ctrl:
                            self.capsule_segments[1] = self.capsule_segments[1] + 1
                        else:
                            self.capsule_segments[0] = self.capsule_segments[0] + 1

                    else:
                        self.circle_segments[0] += 1

                elif event.type in {'NUMPAD_MINUS', 'MINUS'} and event.value == 'PRESS':
                    if self.prim_type == 'Sphere':
                        if event.ctrl:
                            self.sphere_segments[1] = self.sphere_segments[1] - 1
                            self.sphere_segments[1] = max(self.sphere_segments[1], 3)
                        else:
                            self.sphere_segments[0] = self.sphere_segments[0] - 1
                            self.sphere_segments[0] = max(self.sphere_segments[0], 3)

                    elif self.prim_type == 'Capsule':
                        if event.ctrl:
                            self.capsule_segments[1] = self.capsule_segments[1] - 1
                            self.capsule_segments[1] = max(self.capsule_segments[1], 1)
                        else:
                            self.capsule_segments[0] = self.capsule_segments[0] - 1
                            self.capsule_segments[0] = max(self.capsule_segments[0], 3)

                    else:
                        self.circle_segments[0] -= 1
                        self.circle_segments[0] = max(self.circle_segments[0], 3)

                if self.prim_type in {'Sphere','Capsule'}:
                    del self.history_objects[-1]

                    # If Edit Mode
                    if self.edit_obj:
                        bpy.ops.object.mode_set(mode='OBJECT', toggle=False)

                    segments = self.sphere_segments
                    if self.prim_type == 'Capsule':
                        segments = self.capsule_segments

                    # Replace Object Primitive
                    if self.tool_mode == 'IDLE':
                        self.new_prim = replace_prim(self.new_prim, segments, self.prim_type, context)
                    else:
                        self.new_prim = replace_prim(self.new_prim, segments, 'Circle', context)

                    self.history_objects.append([self.new_prim, self.hit_pos])

                    # If Edit Mode
                    if self.edit_obj:
                        bpy.ops.object.select_all(action='DESELECT')
                        self.new_prim.show_wire = True
                        self.new_prim.show_all_edges = True
                        self.edit_obj.select = True
                        context.scene.objects.active = self.edit_obj
                        bpy.ops.object.mode_set(mode='EDIT', toggle=False)

                else:
                    del self.history_objects[-1]

                    # If Edit Mode
                    if self.edit_obj:
                        bpy.ops.object.mode_set(mode='OBJECT', toggle=False)

                    # Replace Object Primitive
                    if self.tool_mode == 'IDLE':
                        self.new_prim = replace_prim(self.new_prim, self.circle_segments, self.prim_type, context)
                    else:
                        if self.prim_type == 'Cube' or self.prim_type == 'Plane':
                            self.new_prim = replace_prim(self.new_prim, self.circle_segments, 'Plane', context)
                        else:
                            self.new_prim = replace_prim(self.new_prim, self.circle_segments, 'Circle', context)

                    self.history_objects.append([self.new_prim, self.hit_pos])

                    # If Edit Mode
                    if self.edit_obj:
                        bpy.ops.object.select_all(action='DESELECT')
                        self.new_prim.show_wire = True
                        self.new_prim.show_all_edges = True
                        self.edit_obj.select = True
                        context.scene.objects.active = self.edit_obj
                        bpy.ops.object.mode_set(mode='EDIT', toggle=False)

            else:
                # allow navigation
                return {'PASS_THROUGH'}

        # FINISH THE TOOL!
        elif event.type in {'RIGHTMOUSE', 'ESC'}:
            # If Edit Mode. Join Primitives
            if self.edit_obj:
                bpy.ops.object.mode_set(mode='OBJECT', toggle=False)
                bpy.ops.object.select_all(action='DESELECT')

                for his_obj in self.history_objects:
                    his_obj[0].select = True

                self.edit_obj.select = True
                context.scene.objects.active = self.edit_obj

                bpy.ops.object.join()
                bpy.ops.object.mode_set(mode='EDIT', toggle=False)            

            # For non Edit Mode
            else:
                #bpy.ops.object.select_all(action='DESELECT')
                cursor_origin = context.scene.cursor_location.copy()

                for his_obj in self.history_objects:
                    bpy.ops.object.select_all(action='DESELECT')
                    his_obj[0].select = True

                    # apply scale
                    if self.prim_type != 'Clone':
                        context.scene.objects.active = his_obj[0]
                        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)

                    # set object position to hit
                    if his_obj[0].location != his_obj[1]:
                        context.scene.cursor_location = his_obj[1].copy()
                        bpy.ops.object.origin_set(type='ORIGIN_CURSOR')

                context.scene.cursor_location = cursor_origin
                bpy.ops.object.select_all(action='DESELECT')

            # clean
            clean(context, self)
            context.area.header_text_set()
            bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
            self.tool_mode == 'IDLE'

            return {'FINISHED'}

        if event.type == 'LEFTMOUSE':
            if self.tool_mode == 'IDLE' and event.value == 'PRESS':
                do_create_prim = False  # check if we found hit
                is_autoaxis = False

                # If Edit Mode
                if self.edit_obj:
                    bpy.ops.object.mode_set(mode='OBJECT', toggle=False)

                # make raycast only one time!
                ray_result = ut_base.get_mouse_raycast(context, self.obj_matrices, m_coords)

                # If Edit Mode
                if self.edit_obj:
                    bpy.ops.object.mode_set(mode='EDIT', toggle=False)

                if ray_result[0] and self.orient_on_surface and not self.center_is_cursor:
                    self.hit_pos = ray_result[2].copy()
                    self.hit_dir = ray_result[1].copy()
                    do_create_prim = True
                else:
                    # make auto pick according to 3d cursor
                    if ray_result[0] and not self.orient_on_surface and not self.center_is_cursor:
                        ray_result_auto =  auto_pick(context, ray_result[2], event)
                    else:
                        ray_result_auto =  auto_pick(context, None, event)

                    if self.center_is_cursor:
                        self.hit_pos = context.scene.cursor_location
                    else:
                        self.hit_pos = ray_result_auto[0].copy()

                    self.hit_dir = ray_result_auto[1].copy()

                    do_create_prim = True
                    is_autoaxis = True

                # CREATE PRIMITIVE!
                if do_create_prim:

                    # If Edit Mode
                    if self.edit_obj:
                        bpy.ops.object.mode_set(mode='OBJECT', toggle=False)

                    # Do Create Primitive
                    if self.prim_type == 'Sphere':
                        new_prim = create_prim(context, event, self.hit_pos, self.hit_dir, self.sphere_segments, is_autoaxis, 'Circle', self)
                    elif self.prim_type == 'Capsule':
                        new_prim = create_prim(context, event, self.hit_pos, self.hit_dir, self.capsule_segments, is_autoaxis, 'Circle', self)
                    elif self.prim_type in {'Cube','Plane'}:
                        new_prim = create_prim(context, event, self.hit_pos, self.hit_dir, None, is_autoaxis, 'Plane', self)
                    elif self.prim_type == 'Clone':
                        new_prim = create_prim(context, event, self.hit_pos, self.hit_dir, None, is_autoaxis, 'Clone', self)
                    else:
                        new_prim = create_prim(context, event, self.hit_pos, self.hit_dir, self.circle_segments, is_autoaxis, 'Circle', self)

                    self.new_prim = new_prim[0]
                    self.history_objects.append([self.new_prim, self.hit_pos])
                    self.prim_side_vec = new_prim[1].copy()
                    self.prim_front_vec = new_prim[2].copy()
                    self.tool_mode = 'DRAW_1'

                    # If Edit Mode
                    if self.edit_obj:
                        bpy.ops.object.select_all(action='DESELECT')
                        self.new_prim.show_wire = True
                        self.new_prim.show_all_edges = True
                        self.edit_obj.select = True
                        context.scene.objects.active = self.edit_obj
                        bpy.ops.object.mode_set(mode='EDIT', toggle=False)

            # Change tool state to Draw_2. Create depth
            elif self.tool_mode == 'IDLE_2' and event.value == 'PRESS':
                # If Edit Mode
                if self.edit_obj:
                    bpy.ops.object.mode_set(mode='OBJECT', toggle=False)

                # Replace Plane and Circle
                del self.history_objects[-1]
                if self.prim_type == 'Sphere':
                    self.new_prim = replace_prim(self.new_prim, self.sphere_segments, self.prim_type, context)
                elif self.prim_type == 'Capsule':
                    self.new_prim = replace_prim(self.new_prim, self.capsule_segments, self.prim_type, context)
                else:
                    self.new_prim = replace_prim(self.new_prim, self.circle_segments, self.prim_type, context)

                self.history_objects.append([self.new_prim, self.hit_pos])

                # If Edit Mode
                if self.edit_obj:
                    bpy.ops.object.select_all(action='DESELECT')
                    self.new_prim.show_wire = True
                    self.new_prim.show_all_edges = True
                    self.edit_obj.select = True
                    context.scene.objects.active = self.edit_obj
                    bpy.ops.object.mode_set(mode='EDIT', toggle=False)

                self.tool_mode = 'DRAW_2'

            if event.value == 'RELEASE':

                # change tool state. Wait for Draw_2 state
                if self.tool_mode == 'DRAW_1':

                    # set to IDLE if this is Plane Primitive
                    if self.prim_type in {'Plane', 'Circle', 'Clone'}:
                        self.tool_mode = 'IDLE'
                        return {'RUNNING_MODAL'}

                    if self.new_prim.scale[0] == 0 or self.new_prim.scale[1] == 0:
                        # remove primitive with 0,0,0 scale
                        objs = bpy.data.objects
                        del self.history_objects[-1]
                        objs.remove(objs[self.new_prim.name], True)
                        self.new_prim = None
                        self.prim_side_vec = None
                        self.prim_front_vec = None
                        self.tool_mode = 'IDLE'
                    else:
                        self.tool_mode = 'IDLE_2'

                else:
                    # go to standard state
                    self.tool_mode = 'IDLE'

        # Draw Primitive X and Y
        if self.tool_mode == 'DRAW_1':
            plane_pos = ut_base.get_mouse_on_plane(context, self.hit_pos, self.hit_dir, m_coords)

            if plane_pos:
                if event.shift:
                    if self.prim_type == 'Clone':
                        self.new_prim.scale = self.objects_to_clone[0].scale.copy()
                    else:
                        # Make the same scale with Shift key
                        new_dist = (plane_pos - self.hit_pos).length

                        self.new_prim.scale[0] = new_dist
                        self.new_prim.scale[1] = new_dist

                else:
                    if self.prim_type == 'Clone':
                        new_dist = (plane_pos - self.hit_pos).length

                        self.new_prim.scale[0] = self.objects_to_clone[0].scale[0] * new_dist
                        self.new_prim.scale[1] = self.objects_to_clone[0].scale[1] * new_dist
                        self.new_prim.scale[2] = self.objects_to_clone[0].scale[2] * new_dist

                    else:
                        dist_x = mathu.geometry.distance_point_to_plane(plane_pos, self.hit_pos, self.prim_side_vec)
                        dist_y = mathu.geometry.distance_point_to_plane(plane_pos, self.hit_pos, self.prim_front_vec)

                        self.new_prim.scale[0] = abs(dist_x)
                        self.new_prim.scale[1] = abs(dist_y)

        # Draw Primitive Z Depth
        elif self.tool_mode == 'DRAW_2':
            rv3d = context.region_data
            mouse_coords = event.mouse_region_x, event.mouse_region_y

            if event.shift:
                # Make the same scale with Shift key
                new_dist = self.new_prim.scale[0]
                if self.new_prim.scale[0] < self.new_prim.scale[1]:
                    new_dist = self.new_prim.scale[1]

                if not self.median_center:
                    self.new_prim.scale[2] = new_dist

                    # set new location
                    mult_vec = self.hit_dir.copy()
                    mult_vec[0] *= (new_dist)
                    mult_vec[1] *= (new_dist)
                    mult_vec[2] *= (new_dist)
                    self.new_prim.location = self.hit_pos + mult_vec

                else:
                    self.new_prim.scale[2] = new_dist

            else:
                view_front_vec = rv3d.view_rotation * Vector((0.0, 0.0, -1.0)).normalized()
                plane_pos = ut_base.get_mouse_on_plane(context, self.hit_pos, view_front_vec, m_coords)
                new_dist = (plane_pos - self.hit_pos).length

                if not self.median_center:
                    self.new_prim.scale[2] = abs(new_dist) / 2

                    # set new location
                    mult_vec = self.hit_dir.copy()
                    mult_vec[0] *= (new_dist / 2)
                    mult_vec[1] *= (new_dist / 2)
                    mult_vec[2] *= (new_dist / 2)
                    self.new_prim.location = self.hit_pos + mult_vec
                else:
                    self.new_prim.scale[2] = (abs(new_dist) / 2) * 2

        return {'RUNNING_MODAL'}


def clean(context, self):
    self.obj_matrices = None
    self.new_prim = None
    self.hit_pos = None
    self.hit_dir = None
    self.prim_side_vec = None
    self.prim_front_vec = None
    self.history_objects = []
    self.edit_obj = None
    self.objects_to_clone = None


def create_prim(context, event, hit_world, normal, segments, is_autoaxis, prim_type, self):
        rv3d = context.region_data

        if prim_type == 'Plane':
            bpy.ops.mesh.primitive_plane_add(radius=1)
        elif prim_type == 'Cube':
            bpy.ops.mesh.primitive_cube_add(radius=1)
        elif prim_type == 'Circle':
            bpy.ops.mesh.primitive_circle_add(radius=1, vertices=segments[0], fill_type='NGON')
        elif prim_type == 'Sphere':
            bpy.ops.mesh.primitive_uv_sphere_add(size=1, segments=segments[0], ring_count=segments[1])
        elif prim_type == 'Cylinder':
            bpy.ops.mesh.primitive_cylinder_add(radius=1, vertices=segments[0])
        elif prim_type == 'Cone':
            bpy.ops.mesh.primitive_cone_add(radius1=1, vertices=segments[0])
        elif prim_type == 'Capsule':
            add_mesh_capsule.add_capsule(1, 1, segments[1], segments[0], context)
        elif prim_type == 'Clone':
            orig_obj = self.objects_to_clone[-1]
            bpy.ops.object.select_all(action='DESELECT')
            orig_obj.select = True

            bpy.ops.object.duplicate(linked=True, mode='DUMMY')
            new_clone = context.selected_objects[0]
            context.scene.objects.active = new_clone

            #new_clone.location = Vector((0.0, 0.0, 0.0))
            #new_clone.scale = orig_obj.scale

        new_prim = bpy.context.object

        # get rotation vectors
        z_neg_vec = Vector((0.0, 0.0, -1.0))
        z_world_angle = z_neg_vec.angle(normal)

        if z_world_angle == math.radians(180) or z_world_angle == math.radians(-180):
            view_cam_upvec = rv3d.view_rotation * Vector((0.0, -1.0, 0.0)).normalized()
            view_upvec = view_cam_upvec.copy()
            view_upvec[2] = 0.0

            if view_upvec[0] == 0 and view_upvec[1] == 0:
                view_upvec = view_cam_upvec
            if view_upvec[0] > view_upvec[1]:
                view_upvec[0] = 1.0
                view_upvec[1] = 0.0
            else:
                view_upvec[0] = 0.0
                view_upvec[1] = 1.0

            view_upvec = view_upvec.normalized()

        else:
            view_upvec = z_neg_vec

        prim_side_vec = None

        # side vector
        if is_autoaxis is True:
            if normal[0] == 1:
                prim_side_vec = Vector((0, 1, 0))
            elif normal[0] == -1:
                prim_side_vec = Vector((0, -1, 0))
            elif normal[1] == 1:
                prim_side_vec = Vector((-1, 0, 0))
            elif normal[1] == -1:
                prim_side_vec = Vector((1, 0, 0))
            elif normal[2] == 1:
                prim_side_vec = Vector((1, 0, 0))
            elif normal[2] == -1:
                prim_side_vec = Vector((1, 0, 0))
        else:
            prim_side_vec = normal.cross(view_upvec).normalized()

        prim_front_vec = normal.cross(prim_side_vec).normalized()

        # ROTATE CUBE
        final_mat = mathu.Matrix().to_3x3()
        final_mat[0][0], final_mat[1][0], final_mat[2][0] = prim_side_vec[0], prim_side_vec[1], prim_side_vec[2]
        final_mat[0][1], final_mat[1][1], final_mat[2][1] = prim_front_vec[0], prim_front_vec[1], prim_front_vec[2]
        final_mat[0][2], final_mat[1][2], final_mat[2][2] = normal[0], normal[1], normal[2]
        #final_mat = final_mat.normalized()
        #new_prim.matrix_world = final_mat.to_4x4()
        new_prim.rotation_euler = final_mat.to_euler()

        new_prim.location = hit_world.copy()

        return new_prim, prim_side_vec, prim_front_vec


def replace_prim(old_prim, segments, prim_type, context):
    loc, rot, scale = old_prim.location.copy(), old_prim.rotation_euler.copy(), old_prim.scale.copy()
    #old_matrix = old_prim.matrix_world.copy()

    # remove old primitive
    objs = bpy.data.objects
    objs.remove(objs[old_prim.name], True)

    # new primitive
    if prim_type == 'Plane':
        bpy.ops.mesh.primitive_plane_add(radius=1)
    elif prim_type == 'Cube':
        bpy.ops.mesh.primitive_cube_add(radius=1)
    elif prim_type == 'Circle':
        bpy.ops.mesh.primitive_circle_add(radius=1, vertices=segments[0], fill_type='NGON')
    elif prim_type == 'Sphere':
        bpy.ops.mesh.primitive_uv_sphere_add(size=1, segments=segments[0], ring_count=segments[1])
    elif prim_type == 'Cylinder':
        bpy.ops.mesh.primitive_cylinder_add(radius=1, vertices=segments[0])
    elif prim_type == 'Cone':
        bpy.ops.mesh.primitive_cone_add(radius1=1, vertices=segments[0])
    elif prim_type == 'Capsule':
        add_mesh_capsule.add_capsule(1, 1, segments[1], segments[0], context)

    new_prim = bpy.context.object
    #new_prim.matrix_world = old_matrix
    new_prim.location, new_prim.rotation_euler, new_prim.scale = loc, rot, scale

    return new_prim


# auto pick position and axis
def auto_pick(context, center_pos, event):
    region = context.region
    rv3d = context.region_data
    view_dir_negative = rv3d.view_rotation * Vector((0.0, 0.0, 1.0))
    mouse_coords = event.mouse_region_x, event.mouse_region_y

    v_x = Vector((1, 0, 0))
    v_x_neg = Vector((-1, 0, 0))
    v_y = Vector((0, 1, 0))
    v_y_neg = Vector((0, -1, 0))
    v_z = Vector((0, 0, 1))
    v_z_neg = Vector((0, 0, -1))

    # GET BEST DIR
    best_dir = v_x
    best_angle = view_dir_negative.angle(v_x)

    if best_angle > view_dir_negative.angle(v_x_neg):
        best_dir = v_x_neg
        best_angle = view_dir_negative.angle(v_x_neg)

    if best_angle > view_dir_negative.angle(v_y):
        best_dir = v_y
        best_angle = view_dir_negative.angle(v_y)

    if best_angle > view_dir_negative.angle(v_y_neg):
        best_dir = v_y_neg
        best_angle = view_dir_negative.angle(v_y_neg)

    if best_angle > view_dir_negative.angle(v_z):
        best_dir = v_z
        best_angle = view_dir_negative.angle(v_z)

    if best_angle > view_dir_negative.angle(v_z_neg):
        best_dir = v_z_neg
        best_angle = view_dir_negative.angle(v_z_neg)

    if center_pos:
        #hit_world = ut_base.get_mouse_on_plane(context, center_pos, best_dir, event)
        return center_pos, best_dir
    else:
        new_center_pos = context.scene.cursor_location
        hit_world = ut_base.get_mouse_on_plane(context, new_center_pos, best_dir, mouse_coords)

        return hit_world, best_dir


def draw_callback_px(self, context):

    rh = bpy.context.region.height
    rw = bpy.context.region.width

    font_id = 0
    font_size = 20

    # segments values
    seg_1 = 0
    seg_2 = 0

    if self.prim_type == 'Sphere':
        seg_1 = self.sphere_segments[0]
        seg_2 = self.sphere_segments[1]
    elif self.prim_type == 'Capsule':
        seg_1 = self.capsule_segments[0]
        seg_2 = self.capsule_segments[1]
    else:
        if self.prim_type not in {'Plane', 'Cube'}:
            seg_1 = self.circle_segments[0]

    #Set font color
    bgl.glEnable(bgl.GL_BLEND)
    bgl.glColor4f(1, 0.75, 0.95, 1)
    bgl.glLineWidth(2)

    #Draw segments text
    blf.position(font_id, rw - 400, 250 - font_size, 0)
    blf.size(font_id, font_size, 72)
    blf.draw(font_id, str(seg_1))

    blf.position(font_id, rw - 400, 250 - (font_size * 2), 0)
    blf.size(font_id, font_size, 72)
    blf.draw(font_id, str(seg_2))

    blf.position(font_id, rw - 400, 250 - (font_size * 3 + 10), 0)
    blf.size(font_id, font_size, 72)
    blf.draw(font_id, "Orient " + str(self.orient_on_surface))

    blf.position(font_id, rw - 400, 250 - (font_size * 4 + 10), 0)
    blf.size(font_id, font_size, 72)
    blf.draw(font_id, "Median Center " + str(self.median_center))

    blf.position(font_id, rw - 400, 250 - (font_size * 5 + 10), 0)
    blf.size(font_id, font_size, 72)
    blf.draw(font_id, "Center is 3D Cursor " + str(self.center_is_cursor))

    # restore opengl defaults
    bgl.glLineWidth(1)
    bgl.glDisable(bgl.GL_BLEND)
    bgl.glColor4f(0.0, 0.0, 0.0, 1.0)

