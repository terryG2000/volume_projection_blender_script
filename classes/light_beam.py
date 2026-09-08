"""光线类"""

import bpy
import math
from mathutils import Vector
from typing import List

# 动态加载时会自动注入 utils_materials 模块
try:
    from utils_materials import create_emission_material
except ImportError:
    from ..utils.materials import create_emission_material


class LightBeam:
    """光线类，管理投影光线的创建"""

    def __init__(
        self,
        projector,  # Projector 实例
        fov_horizontal: float = 30.0,
        fov_vertical: float = 30.0,
        length: float = 8.0,
        grid_x: int = 9,
        grid_y: int = 5,
        color: tuple = (1, 0.85, 0.1, 1),
        emission_strength: float = 3.0,
        name_prefix: str = "LightBeam"
    ):
        """
        初始化光线

        Args:
            projector: Projector 实例
            fov_horizontal: 水平视场角（度）
            fov_vertical: 垂直视场角（度）
            length: 光线长度（米）
            grid_x: 横向光线数量
            grid_y: 纵向光线数量
            color: 光线颜色 RGBA
            emission_strength: 发光强度
            name_prefix: 光线名称前缀
        """
        self.projector = projector
        self.fov_horizontal = fov_horizontal
        self.fov_vertical = fov_vertical
        self.length = length
        self.grid_x = grid_x
        self.grid_y = grid_y
        self.color = color
        self.emission_strength = emission_strength
        self.name_prefix = name_prefix
        self._beams: List[bpy.types.Object] = []

    @property
    def projection_width(self) -> float:
        """计算投影宽度"""
        return 2 * self.length * math.tan(math.radians(self.fov_horizontal / 2))

    @property
    def projection_height(self) -> float:
        """计算投影高度"""
        return 2 * self.length * math.tan(math.radians(self.fov_vertical / 2))

    @property
    def projection_center(self) -> Vector:
        """计算投影平面中心位置"""
        center_direction = self.get_ray_direction(0, 0)
        return self.projector.lens_position + center_direction * self.length

    @staticmethod
    def get_ray_direction(angle_h: float, angle_v: float) -> Vector:
        """
        根据水平角和垂直角计算光线方向

        Args:
            angle_h: 水平角度偏移（度）
            angle_v: 垂直角度偏移（度）

        Returns:
            归一化的方向向量
        """
        dir_x = math.sin(math.radians(angle_h)) * math.cos(math.radians(angle_v))
        dir_y = -math.cos(math.radians(angle_h)) * math.cos(math.radians(angle_v))
        dir_z = math.sin(math.radians(angle_v))
        return Vector((dir_x, dir_y, dir_z))

    @staticmethod
    def create_single_ray(
        name: str,
        origin: Vector,
        direction: Vector,
        length: float
    ) -> bpy.types.Object:
        """
        创建单条光线

        Args:
            name: 光线名称
            origin: 起点
            direction: 方向向量
            length: 长度

        Returns:
            创建的光线对象
        """
        direction = Vector(direction).normalized()
        end = origin + direction * length

        curve_data = bpy.data.curves.new(name=name, type='CURVE')
        curve_data.dimensions = '3D'
        curve_data.bevel_depth = 0  # 纯线条

        spline = curve_data.splines.new('POLY')
        spline.points.add(1)
        spline.points[0].co = (origin.x, origin.y, origin.z, 1)
        spline.points[1].co = (end.x, end.y, end.z, 1)

        curve_obj = bpy.data.objects.new(name, curve_data)
        bpy.context.collection.objects.link(curve_obj)

        return curve_obj

    def create(self) -> List[bpy.types.Object]:
        """
        创建所有光线

        Returns:
            光线对象列表
        """
        # 创建材质
        mat = create_emission_material(
            f"Mat_{self.name_prefix}",
            self.color,
            self.emission_strength
        )

        # 创建光线网格
        for i in range(self.grid_x):
            for j in range(self.grid_y):
                # 计算 UV 坐标 (0-1)
                u = i / (self.grid_x - 1) if self.grid_x > 1 else 0.5
                v = j / (self.grid_y - 1) if self.grid_y > 1 else 0.5

                # 转换为角度偏移
                angle_h = (u - 0.5) * self.fov_horizontal
                angle_v = (v - 0.5) * self.fov_vertical

                # 获取光线方向
                direction = self.get_ray_direction(angle_h, angle_v)

                # 创建光线
                idx = i * self.grid_y + j
                name = f"{self.name_prefix}_{idx:03d}"
                beam = self.create_single_ray(
                    name,
                    self.projector.lens_position,
                    direction,
                    self.length
                )
                beam.data.materials.append(mat)
                self._beams.append(beam)

        # 设置父子关系
        for beam in self._beams:
            self._set_parent_keep_transform(beam, self.projector.object)

        return self._beams

    def _set_parent_keep_transform(self, child, parent):
        """设置父子关系并保持子对象的世界变换"""
        world_matrix = child.matrix_world.copy()
        child.parent = parent
        child.matrix_parent_inverse = parent.matrix_world.inverted()
        child.matrix_world = world_matrix

    @property
    def beams(self) -> List[bpy.types.Object]:
        """返回光线对象列表"""
        return self._beams
