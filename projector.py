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

# ========== 透明立方体设置 ==========
# 立方体尺寸 (米) - 设为 None 则自动根据投影尺寸计算
CUBE_WIDTH = None   # 自定义宽度，None 表示自动计算
CUBE_HEIGHT = None  # 自定义高度，None 表示自动计算
CUBE_DEPTH = 0.5    # 立方体厚度

# 自动计算时的缩放因子（当尺寸为 None 时使用）
CUBE_SCALE_FACTOR = 1.2  # 比投影面积大 20%

# 透明度设置 (0 = 完全透明, 1 = 完全不透明)
CUBE_ALPHA = 0.8   # 透明度
CUBE_COLOR = (1, 0.9, 1.0, 1.0)  # 立方体颜色 (RGBA)

# 线框设置
WIREFRAME_THICKNESS = 0.015  # 线框粗细 (米)
WIREFRAME_COLOR = (0.3, 0.5, 0.8, 1.0)  # 线框颜色 (RGBA)

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
    curve_data.bevel_depth = 0  # 纯线条，无粗细

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

# ========== 计算投影尺寸 ==========
# 投影宽度 = 2 * 距离 * tan(FOV/2)
projection_width = 2 * LIGHT_LENGTH * math.tan(math.radians(FOV_HORIZONTAL / 2))
projection_height = 2 * LIGHT_LENGTH * math.tan(math.radians(FOV_VERTICAL / 2))

# ========== 创建透明投影平面立方体 ==========
# 计算立方体尺寸（使用自定义值或自动计算）
cube_width = CUBE_WIDTH if CUBE_WIDTH is not None else projection_width * CUBE_SCALE_FACTOR
cube_height = CUBE_HEIGHT if CUBE_HEIGHT is not None else projection_height * CUBE_SCALE_FACTOR
cube_depth = CUBE_DEPTH

# 计算投影平面中心位置（光线终点平面的中心）
# 中心光线方向是正前方 (-Y 方向)
center_direction = get_ray_direction(0, 0)
projection_plane_center = lens_pos + center_direction * LIGHT_LENGTH

# 创建透明立方体
bpy.ops.mesh.primitive_cube_add(size=1)
projection_cube = bpy.context.active_object
projection_cube.name = "ProjectionCube"

# 设置立方体尺寸
projection_cube.scale = (cube_width, cube_depth, cube_height)

# 设置立方体位置（在投影平面上）
projection_cube.location = (
    projection_plane_center.x,
    projection_plane_center.y,
    projection_plane_center.z
)

# ========== 设置透明材质 ==========
mat_transparent = bpy.data.materials.new(name="Mat_TransparentCube")
mat_transparent.use_nodes = True
mat_transparent.blend_method = 'BLEND'

nodes = mat_transparent.node_tree.nodes
links = mat_transparent.node_tree.links

# 清除默认节点
nodes.clear()

# 创建节点
output_node = nodes.new('ShaderNodeOutputMaterial')
principled = nodes.new('ShaderNodeBsdfPrincipled')

# 设置颜色和透明度
principled.inputs['Base Color'].default_value = CUBE_COLOR
principled.inputs['Alpha'].default_value = CUBE_ALPHA

# 连接
links.new(principled.outputs['BSDF'], output_node.inputs['Surface'])

# 应用材质到立方体
projection_cube.data.materials.append(mat_transparent)

# ========== 创建线框边框 ==========
# 计算立方体8个顶点位置
hw, hh, hd = cube_width / 2, cube_height / 2, cube_depth / 2
cx, cy, cz = projection_plane_center.x, projection_plane_center.y, projection_plane_center.z

vertices = [
    (cx - hw, cy - hd, cz - hh),  # 0: 后下左
    (cx + hw, cy - hd, cz - hh),  # 1: 后下右
    (cx + hw, cy + hd, cz - hh),  # 2: 前下右
    (cx - hw, cy + hd, cz - hh),  # 3: 前下左
    (cx - hw, cy - hd, cz + hh),  # 4: 后上左
    (cx + hw, cy - hd, cz + hh),  # 5: 后上右
    (cx + hw, cy + hd, cz + hh),  # 6: 前上右
    (cx - hw, cy + hd, cz + hh),  # 7: 前上左
]

# 立方体12条边的顶点索引
edges = [
    (0, 1), (1, 2), (2, 3), (3, 0),  # 底面
    (4, 5), (5, 6), (6, 7), (7, 4),  # 顶面
    (0, 4), (1, 5), (2, 6), (3, 7),  # 竖边
]

# 创建线框材质
mat_wireframe = bpy.data.materials.new(name="Mat_Wireframe")
mat_wireframe.use_nodes = True
nodes_wf = mat_wireframe.node_tree.nodes
links_wf = mat_wireframe.node_tree.links
nodes_wf.clear()

output_wf = nodes_wf.new('ShaderNodeOutputMaterial')
emission_wf = nodes_wf.new('ShaderNodeEmission')
emission_wf.inputs['Color'].default_value = WIREFRAME_COLOR
emission_wf.inputs['Strength'].default_value = 3.0
links_wf.new(emission_wf.outputs['Emission'], output_wf.inputs['Surface'])

# 创建线条
wireframe_lines = []
for i, (v1_idx, v2_idx) in enumerate(edges):
    curve_data = bpy.data.curves.new(name=f"CubeEdge_{i}", type='CURVE')
    curve_data.dimensions = '3D'
    curve_data.bevel_depth = 0  # 纯线条，无粗细

    spline = curve_data.splines.new('POLY')
    spline.points.add(1)
    spline.points[0].co = (*vertices[v1_idx], 1)
    spline.points[1].co = (*vertices[v2_idx], 1)

    curve_obj = bpy.data.objects.new(f"CubeEdge_{i}", curve_data)
    bpy.context.collection.objects.link(curve_obj)
    curve_obj.data.materials.append(mat_wireframe)
    wireframe_lines.append(curve_obj)

# 创建空对象作为线框的父对象
wireframe_parent = bpy.data.objects.new("CubeWireframe", None)
bpy.context.collection.objects.link(wireframe_parent)
wireframe_parent.location = (0, 0, 0)  # 父对象在原点，避免坐标偏移

for line in wireframe_lines:
    line.parent = wireframe_parent

# ========== 输出信息 ==========
print("=" * 50)
print("投影仪模拟创建完成!")
print(f"FOV 水平: {FOV_HORIZONTAL}° | 垂直: {FOV_VERTICAL}°")
print(f"光线长度: {LIGHT_LENGTH}m")
print(f"光线数量: {LIGHT_GRID_X} x {LIGHT_GRID_Y} = {LIGHT_GRID_X * LIGHT_GRID_Y}")
print(f"投影仪位置: {projector.location}")
print(f"镜头位置: {lens_pos}")
print(f"投影尺寸: {projection_width:.2f}m x {projection_height:.2f}m")
if CUBE_WIDTH is not None or CUBE_HEIGHT is not None:
    print(f"透明立方体尺寸: {cube_width:.2f}m x {cube_height:.2f}m (自定义)")
else:
    print(f"透明立方体尺寸: {cube_width:.2f}m x {cube_height:.2f}m (比投影大{int((CUBE_SCALE_FACTOR-1)*100)}%)")
print(f"透明立方体透明度: {CUBE_ALPHA} (0=全透明, 1=不透明)")
print(f"透明立方体位置: {projection_cube.location}")
print("移动 Projector 对象，光线会跟随移动。")
print("=" * 50)
