"""投影仪类"""

import bpy
from mathutils import Vector

# 动态加载时会自动注入 utils_materials 模块
try:
    from utils_materials import create_simple_material
except ImportError:
    from ..utils.materials import create_simple_material


class Projector:
    """投影仪类，管理投影仪的创建和属性"""

    def __init__(
        self,
        width: float = 1.0,
        height: float = 0.5,
        depth: float = 0.8,
        location: tuple = (0, 0, 2.0),
        name: str = "Projector"
    ):
        """
        初始化投影仪

        Args:
            width: 投影仪宽度（米）
            height: 投影仪高度（米）
            depth: 投影仪深度（米）
            location: 投影仪位置 (x, y, z)
            name: 对象名称
        """
        self.width = width
        self.height = height
        self.depth = depth
        self.location = Vector(location)
        self.name = name
        self._object = None
        self._lens_marker = None

    @property
    def lens_position(self) -> Vector:
        """返回镜头位置（投影仪前面中心点）"""
        return Vector((
            self.location.x,
            self.location.y - self.depth / 2,
            self.location.z
        ))

    def create(self) -> bpy.types.Object:
        """
        创建投影仪对象

        Returns:
            创建的投影仪对象
        """
        # 创建投影仪主体
        bpy.ops.mesh.primitive_cube_add(size=1)
        self._object = bpy.context.active_object
        self._object.name = self.name
        self._object.scale = (self.width, self.depth, self.height)
        self._object.location = self.location

        # 创建镜头标记点
        bpy.ops.mesh.primitive_uv_sphere_add(radius=0.05)
        self._lens_marker = bpy.context.active_object
        self._lens_marker.name = f"{self.name}_LensMarker"
        self._lens_marker.location = self.lens_position

        # 设置父子关系
        self._set_parent_keep_transform(self._lens_marker, self._object)

        # 应用材质
        mat = create_simple_material(f"Mat_{self.name}", (0.15, 0.15, 0.15, 1))
        self._object.data.materials.append(mat)

        mat_lens = create_simple_material(f"Mat_{self.name}_Lens", (1, 0.2, 0.2, 1))
        self._lens_marker.data.materials.append(mat_lens)

        return self._object

    def _set_parent_keep_transform(self, child, parent):
        """设置父子关系并保持子对象的世界变换"""
        world_matrix = child.matrix_world.copy()
        child.parent = parent
        child.matrix_parent_inverse = parent.matrix_world.inverted()
        child.matrix_world = world_matrix

    @property
    def object(self) -> bpy.types.Object:
        """返回投影仪对象"""
        return self._object
