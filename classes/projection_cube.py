"""投影立方体类"""

import bpy
from mathutils import Vector
from typing import List

# 动态加载时会自动注入 utils_materials 模块
try:
    from utils_materials import create_principled_material, create_emission_material
except ImportError:
    from ..utils.materials import create_principled_material, create_emission_material


class ProjectionCube:
    """投影立方体类，管理透明投影立方体和线框的创建"""

    def __init__(
        self,
        projector,  # Projector 实例
        light_beam,  # LightBeam 实例
        depth: float = 0.5,
        alpha: float = 0.8,
        color: tuple = (1, 0.9, 1.0, 1.0),
        wireframe_color: tuple = (0.3, 0.5, 0.8, 1.0),
        scale_factor: float = 1.2,
        custom_width: float = None,
        custom_height: float = None,
        name: str = "ProjectionCube"
    ):
        """
        初始化投影立方体

        Args:
            projector: Projector 实例
            light_beam: LightBeam 实例
            depth: 立方体厚度（米）
            alpha: 透明度 (0=全透明, 1=不透明)
            color: 立方体颜色 RGBA
            wireframe_color: 线框颜色 RGBA
            scale_factor: 自动计算时的缩放因子
            custom_width: 自定义宽度（None 表示自动计算）
            custom_height: 自定义高度（None 表示自动计算）
            name: 对象名称
        """
        self.projector = projector
        self.light_beam = light_beam
        self.depth = depth
        self.alpha = alpha
        self.color = color
        self.wireframe_color = wireframe_color
        self.scale_factor = scale_factor
        self.custom_width = custom_width
        self.custom_height = custom_height
        self.name = name
        self._object = None
        self._wireframe_lines: List[bpy.types.Object] = []
        self._wireframe_parent = None

    @property
    def width(self) -> float:
        """立方体宽度"""
        if self.custom_width is not None:
            return self.custom_width
        return self.light_beam.projection_width * self.scale_factor

    @property
    def height(self) -> float:
        """立方体高度"""
        if self.custom_height is not None:
            return self.custom_height
        return self.light_beam.projection_height * self.scale_factor

    @property
    def center(self) -> Vector:
        """立方体中心位置"""
        return self.light_beam.projection_center

    def create(self) -> bpy.types.Object:
        """
        创建投影立方体和线框

        Returns:
            创建的立方体对象
        """
        # 创建透明立方体
        bpy.ops.mesh.primitive_cube_add(size=1)
        self._object = bpy.context.active_object
        self._object.name = self.name
        self._object.scale = (self.width, self.depth, self.height)
        self._object.location = self.center

        # 应用透明材质
        mat = create_principled_material(f"Mat_{self.name}", self.color, self.alpha)
        self._object.data.materials.append(mat)

        # 创建线框
        self._create_wireframe()

        return self._object

    def _create_wireframe(self):
        """创建立方体线框"""
        # 计算立方体 8 个顶点
        hw, hh, hd = self.width / 2, self.height / 2, self.depth / 2
        cx, cy, cz = self.center.x, self.center.y, self.center.z

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

        # 立方体 12 条边
        edges = [
            (0, 1), (1, 2), (2, 3), (3, 0),  # 底面
            (4, 5), (5, 6), (6, 7), (7, 4),  # 顶面
            (0, 4), (1, 5), (2, 6), (3, 7),  # 竖边
        ]

        # 创建线框材质
        mat_wireframe = create_emission_material(
            f"Mat_{self.name}_Wireframe",
            self.wireframe_color,
            3.0
        )

        # 创建线条
        for i, (v1_idx, v2_idx) in enumerate(edges):
            curve_data = bpy.data.curves.new(name=f"{self.name}_Edge_{i}", type='CURVE')
            curve_data.dimensions = '3D'
            curve_data.bevel_depth = 0  # 纯线条

            spline = curve_data.splines.new('POLY')
            spline.points.add(1)
            spline.points[0].co = (*vertices[v1_idx], 1)
            spline.points[1].co = (*vertices[v2_idx], 1)

            curve_obj = bpy.data.objects.new(f"{self.name}_Edge_{i}", curve_data)
            bpy.context.collection.objects.link(curve_obj)
            curve_obj.data.materials.append(mat_wireframe)
            self._wireframe_lines.append(curve_obj)

        # 创建空对象作为线框父对象
        self._wireframe_parent = bpy.data.objects.new(f"{self.name}_Wireframe", None)
        bpy.context.collection.objects.link(self._wireframe_parent)
        self._wireframe_parent.location = (0, 0, 0)

        for line in self._wireframe_lines:
            line.parent = self._wireframe_parent

    @property
    def object(self) -> bpy.types.Object:
        """返回立方体对象"""
        return self._object

    @property
    def wireframe_lines(self) -> List[bpy.types.Object]:
        """返回线框线条列表"""
        return self._wireframe_lines
