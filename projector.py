import bpy
import math
from mathutils import Vector

# 清空场景中的所有物体
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete()

# ========== 参数设置 ==========
# 投影仪尺寸 (米)
PROJECTOR_WIDTH = 1.0
PROJECTOR_HEIGHT = 0.5
PROJECTOR_DEPTH = 0.8

# ========== FOV 设置 ==========
# 水平视场角 (度数) - 调整此值改变投影宽度
FOV_HORIZONTAL = 30.0  # 度

# 垂直视场角 (度数) - 调整此值改变投影高度
FOV_VERTICAL = 30.0  # 度

# 光线长度
LIGHT_LENGTH = 8.0

# 光线半径
LIGHT_RADIUS = 0.008

# 光线网格数量 (横向 x 纵向)
LIGHT_GRID_X = 9  # 横向光线数
LIGHT_GRID_Y = 5  # 纵向光线数

# ========== 创建投影仪 ==========
bpy.ops.mesh.primitive_cube_add(size=1)
projector = bpy.context.active_object
projector.name = "Projector"
projector.scale = (PROJECTOR_WIDTH, PROJECTOR_DEPTH, PROJECTOR_HEIGHT)
projector.location = (0, 0, PROJECTOR_HEIGHT / 2 + 1.5)

# 投影仪镜头位置 (前面中心点)
lens_pos = Vector((
    0,
    0 - PROJECTOR_DEPTH / 2,
    PROJECTOR_HEIGHT / 2 + 1.5
))

# ========== 创建光线函数 ==========
def create_light_ray(name, origin, direction, length):
    """
    Create a light ray (curve object) from an origin point along a specified direction.

    Args:
        name (str): The name of the curve object to be created.
        origin (Vector or tuple): The starting point of the ray in world coordinates (x, y, z).
        direction (Vector or tuple): The direction vector of the ray. Will be normalized internally.
        length (float): The length of the ray in meters.

    Returns:
        bpy.types.Object: The created curve object representing the light ray.
    """
    origin = Vector(origin)
    direction = Vector(direction).normalized()  # 归一化方向向量

    # 终点 = 起点 + 方向 * 长度
    end = origin + direction * length

    curve_data = bpy.data.curves.new(name=name, type='CURVE')
    curve_data.dimensions = '3D'
    curve_data.bevel_depth = LIGHT_RADIUS

    spline = curve_data.splines.new('POLY')
    spline.points.add(1)
    spline.points[0].co = (origin.x, origin.y, origin.z, 1)
    spline.points[1].co = (end.x, end.y, end.z, 1)

    curve_obj = bpy.data.objects.new(name, curve_data)
    bpy.context.collection.objects.link(curve_obj)

    return curve_obj

# ========== 根据角度计算光线方向 ==========
def get_ray_direction(angle_h, angle_v):
    """
    Calculate the normalized direction vector based on horizontal and vertical angles.

    Uses a spherical coordinate system where the default forward direction is -Y axis.
    The ray direction is computed by applying horizontal (yaw) and vertical (pitch) rotations.

    Args:
        angle_h (float): Horizontal angle offset in degrees.
                        Positive values rotate right, negative values rotate left.
        angle_v (float): Vertical angle offset in degrees.
                        Positive values rotate upward, negative values rotate downward.

    Returns:
        Vector: A normalized direction vector (x, y, z) representing the ray direction.
    """
    # 光线默认向 -Y 方向发射（前方）
    # 使用球坐标系计算方向
    dir_x = math.sin(math.radians(angle_h)) * math.cos(math.radians(angle_v))
    dir_y = -math.cos(math.radians(angle_h)) * math.cos(math.radians(angle_v))
    dir_z = math.sin(math.radians(angle_v))
    return Vector((dir_x, dir_y, dir_z))

# ========== 计算均匀分布的光线角度 ==========
light_beams = []
for i in range(LIGHT_GRID_X):
    for j in range(LIGHT_GRID_Y):
        # 计算UV坐标 (0-1)
        u = i / (LIGHT_GRID_X - 1) if LIGHT_GRID_X > 1 else 0.5
        v = j / (LIGHT_GRID_Y - 1) if LIGHT_GRID_Y > 1 else 0.5

        # 转换为角度偏移 (-FOV/2 到 +FOV/2)
        angle_h = (u - 0.5) * FOV_HORIZONTAL
        angle_v = (v - 0.5) * FOV_VERTICAL

        # 获取光线方向
        direction = get_ray_direction(angle_h, angle_v)

        # 创建光线
        idx = i * LIGHT_GRID_Y + j
        name = f"LightBeam_{idx:03d}"
        beam = create_light_ray(name, lens_pos, direction, LIGHT_LENGTH)
        light_beams.append(beam)

# ========== 创建镜头标记点 ==========
bpy.ops.mesh.primitive_uv_sphere_add(radius=0.05)
lens_marker = bpy.context.active_object
lens_marker.name = "LensMarker"
lens_marker.location = lens_pos

# ========== 正确设置父子关系 ==========
def set_parent_keep_transform(child, parent):
    """
    Set parent-child relationship while preserving the child's world transform.

    When parenting objects in Blender, the child's local transform is recalculated
    based on the parent's transform, which can cause unwanted position changes.
    This function ensures the child maintains its current world position,
    rotation, and scale after parenting.

    Args:
        child (bpy.types.Object): The object to become the child.
        parent (bpy.types.Object): The object to become the parent.

    Returns:
        None
    """
    world_matrix = child.matrix_world.copy()
    child.parent = parent
    child.matrix_parent_inverse = parent.matrix_world.inverted()
    child.matrix_world = world_matrix

for beam in light_beams:
    set_parent_keep_transform(beam, projector)
set_parent_keep_transform(lens_marker, projector)

# ========== 设置材质 ==========
# 投影仪材质 - 深灰色
mat_projector = bpy.data.materials.new(name="Mat_Projector")
mat_projector.diffuse_color = (0.15, 0.15, 0.15, 1)
projector.data.materials.append(mat_projector)

# 光线材质 - 黄色发光
mat_beam = bpy.data.materials.new(name="Mat_LightBeam")
mat_beam.use_nodes = True
nodes = mat_beam.node_tree.nodes
nodes.clear()
emission = nodes.new('ShaderNodeEmission')
emission.inputs['Color'].default_value = (1, 0.85, 0.1, 1)
emission.inputs['Strength'].default_value = 3.0
output = nodes.new('ShaderNodeOutputMaterial')
mat_beam.node_tree.links.new(emission.outputs['Emission'], output.inputs['Surface'])
mat_beam.diffuse_color = (1, 0.85, 0.1, 1)
for beam in light_beams:
    beam.data.materials.append(mat_beam)

# 镜头标记材质 - 红色
mat_lens = bpy.data.materials.new(name="Mat_Lens")
mat_lens.diffuse_color = (1, 0.2, 0.2, 1)
lens_marker.data.materials.append(mat_lens)

# ========== 输出信息 ==========
print("=" * 50)
print("投影仪模拟创建完成!")
print(f"FOV 水平: {FOV_HORIZONTAL}° | 垂直: {FOV_VERTICAL}°")
print(f"光线长度: {LIGHT_LENGTH}m")
print(f"光线数量: {LIGHT_GRID_X} x {LIGHT_GRID_Y} = {LIGHT_GRID_X * LIGHT_GRID_Y}")
print(f"投影仪位置: {projector.location}")
print(f"镜头位置: {lens_pos}")
print("移动 Projector 对象，光线会跟随移动。")
print("=" * 50)
import bpy
import math
from mathutils import Vector

# 清空场景中的所有物体
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete()

# ========== 参数设置 ==========
# 投影仪尺寸 (米)
PROJECTOR_WIDTH = 1.0
PROJECTOR_HEIGHT = 0.5
PROJECTOR_DEPTH = 0.8

# ========== FOV 设置 ==========
# 水平视场角 (度数) - 调整此值改变投影宽度
FOV_HORIZONTAL = 30.0  # 度

# 垂直视场角 (度数) - 调整此值改变投影高度
FOV_VERTICAL = 30.0  # 度

# 光线长度
LIGHT_LENGTH = 8.0

# 光线半径
LIGHT_RADIUS = 0.008

# 光线网格数量 (横向 x 纵向)
LIGHT_GRID_X = 9  # 横向光线数
LIGHT_GRID_Y = 5  # 纵向光线数

# ========== 创建投影仪 ==========
bpy.ops.mesh.primitive_cube_add(size=1)
projector = bpy.context.active_object
projector.name = "Projector"
projector.scale = (PROJECTOR_WIDTH, PROJECTOR_DEPTH, PROJECTOR_HEIGHT)
projector.location = (0, 0, PROJECTOR_HEIGHT / 2 + 1.5)

# 投影仪镜头位置 (前面中心点)
lens_pos = Vector((
    0,
    0 - PROJECTOR_DEPTH / 2,
    PROJECTOR_HEIGHT / 2 + 1.5
))

# ========== 创建光线函数 ==========
def create_light_ray(name, origin, direction, length):
    """
    Create a light ray (curve object) from an origin point along a specified direction.

    Args:
        name (str): The name of the curve object to be created.
        origin (Vector or tuple): The starting point of the ray in world coordinates (x, y, z).
        direction (Vector or tuple): The direction vector of the ray. Will be normalized internally.
        length (float): The length of the ray in meters.

    Returns:
        bpy.types.Object: The created curve object representing the light ray.
    """
    origin = Vector(origin)
    direction = Vector(direction).normalized()  # 归一化方向向量

    # 终点 = 起点 + 方向 * 长度
    end = origin + direction * length

    curve_data = bpy.data.curves.new(name=name, type='CURVE')
    curve_data.dimensions = '3D'
    curve_data.bevel_depth = LIGHT_RADIUS

    spline = curve_data.splines.new('POLY')
    spline.points.add(1)
    spline.points[0].co = (origin.x, origin.y, origin.z, 1)
    spline.points[1].co = (end.x, end.y, end.z, 1)

    curve_obj = bpy.data.objects.new(name, curve_data)
    bpy.context.collection.objects.link(curve_obj)

    return curve_obj

# ========== 根据角度计算光线方向 ==========
def get_ray_direction(angle_h, angle_v):
    """
    Calculate the normalized direction vector based on horizontal and vertical angles.

    Uses a spherical coordinate system where the default forward direction is -Y axis.
    The ray direction is computed by applying horizontal (yaw) and vertical (pitch) rotations.

    Args:
        angle_h (float): Horizontal angle offset in degrees.
                        Positive values rotate right, negative values rotate left.
        angle_v (float): Vertical angle offset in degrees.
                        Positive values rotate upward, negative values rotate downward.

    Returns:
        Vector: A normalized direction vector (x, y, z) representing the ray direction.
    """
    # 光线默认向 -Y 方向发射（前方）
    # 使用球坐标系计算方向
    dir_x = math.sin(math.radians(angle_h)) * math.cos(math.radians(angle_v))
    dir_y = -math.cos(math.radians(angle_h)) * math.cos(math.radians(angle_v))
    dir_z = math.sin(math.radians(angle_v))
    return Vector((dir_x, dir_y, dir_z))

# ========== 计算均匀分布的光线角度 ==========
light_beams = []
for i in range(LIGHT_GRID_X):
    for j in range(LIGHT_GRID_Y):
        # 计算UV坐标 (0-1)
        # 如果只有一条光线，UV坐标固定为0.5，确保光线在中心位置
        u = i / (LIGHT_GRID_X - 1) if LIGHT_GRID_X > 1 else 0.5
        v = j / (LIGHT_GRID_Y - 1) if LIGHT_GRID_Y > 1 else 0.5

        # 转换为角度偏移 (-FOV/2 到 +FOV/2)
        angle_h = (u - 0.5) * FOV_HORIZONTAL
        angle_v = (v - 0.5) * FOV_VERTICAL

        # 获取光线方向
        direction = get_ray_direction(angle_h, angle_v)

        # 创建光线
        idx = i * LIGHT_GRID_Y + j
        name = f"LightBeam_{idx:03d}"
        beam = create_light_ray(name, lens_pos, direction, LIGHT_LENGTH)
        light_beams.append(beam)

# ========== 创建镜头标记点 ==========
bpy.ops.mesh.primitive_uv_sphere_add(radius=0.05)
lens_marker = bpy.context.active_object
lens_marker.name = "LensMarker"
lens_marker.location = lens_pos

# ========== 正确设置父子关系 ==========
def set_parent_keep_transform(child, parent):
    """
    Set parent-child relationship while preserving the child's world transform.

    When parenting objects in Blender, the child's local transform is recalculated
    based on the parent's transform, which can cause unwanted position changes.
    This function ensures the child maintains its current world position,
    rotation, and scale after parenting.

    Args:
        child (bpy.types.Object): The object to become the child.
        parent (bpy.types.Object): The object to become the parent.

    Returns:
        None
    """
    world_matrix = child.matrix_world.copy()
    child.parent = parent
    child.matrix_parent_inverse = parent.matrix_world.inverted()
    child.matrix_world = world_matrix

for beam in light_beams:
    set_parent_keep_transform(beam, projector)
set_parent_keep_transform(lens_marker, projector)

# ========== 设置材质 ==========
# 投影仪材质 - 深灰色
mat_projector = bpy.data.materials.new(name="Mat_Projector")
mat_projector.diffuse_color = (0.15, 0.15, 0.15, 1)
projector.data.materials.append(mat_projector)

# 光线材质 - 黄色发光
mat_beam = bpy.data.materials.new(name="Mat_LightBeam")
mat_beam.use_nodes = True
nodes = mat_beam.node_tree.nodes
nodes.clear()
emission = nodes.new('ShaderNodeEmission')
emission.inputs['Color'].default_value = (1, 0.85, 0.1, 1)
emission.inputs['Strength'].default_value = 3.0
output = nodes.new('ShaderNodeOutputMaterial')
mat_beam.node_tree.links.new(emission.outputs['Emission'], output.inputs['Surface'])
mat_beam.diffuse_color = (1, 0.85, 0.1, 1)
for beam in light_beams:
    beam.data.materials.append(mat_beam)

# 镜头标记材质 - 红色
mat_lens = bpy.data.materials.new(name="Mat_Lens")
mat_lens.diffuse_color = (1, 0.2, 0.2, 1)
lens_marker.data.materials.append(mat_lens)

# ========== 输出信息 ==========
print("=" * 50)
print("投影仪模拟创建完成!")
print(f"FOV 水平: {FOV_HORIZONTAL}° | 垂直: {FOV_VERTICAL}°")
print(f"光线长度: {LIGHT_LENGTH}m")
print(f"光线数量: {LIGHT_GRID_X} x {LIGHT_GRID_Y} = {LIGHT_GRID_X * LIGHT_GRID_Y}")
print(f"投影仪位置: {projector.location}")
print(f"镜头位置: {lens_pos}")
print("移动 Projector 对象，光线会跟随移动。")
print("=" * 50)
