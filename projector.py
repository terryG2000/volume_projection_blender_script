# context.area: PROPERTIES
"""
体积投影仿真建模脚本

在 Blender 中运行本脚本，会创建一个普通投影仪向透明玻璃立方体投影的场景。
立方体内部的微小气泡按投影图案和深度层分布，每个气泡代表一个可散射光的体素/像素点。
"""

import math
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import bpy
from mathutils import Vector


# ========== 场景参数 ==========

PROJECTOR_LOCATION = (0.0, 1.25, 1.85)
PROJECTOR_SIZE = (1.15, 0.9, 0.52)  # width, depth, height

CUBE_CENTER = (0.0, -2.35, 1.75)
CUBE_SIZE = (3.2, 2.05, 2.2)  # width, depth, height
CUBE_ALPHA = 0.18

# 首次调试建议保持 True，场景能更快生成；最终出图可改为 False 提高气泡密度。
FAST_PREVIEW = True
RUNNING_FLAG_KEY = "volume_projection_script_running"

IMAGE_GRID_X = 16 if FAST_PREVIEW else 34
IMAGE_GRID_Z = 10 if FAST_PREVIEW else 22
IMAGE_FILL = 0.84
BUBBLE_DEPTH_LAYERS = 7
BUBBLE_RADIUS = 0.027
BACKGROUND_BUBBLE_RATE = 0.02 if FAST_PREVIEW else 0.13
SHOW_BACKGROUND_BUBBLES = False
BUBBLE_PATH_RAY_LIMIT = 160 if FAST_PREVIEW else 260

RAY_GRID_X = 6 if FAST_PREVIEW else 13
RAY_GRID_Z = 4 if FAST_PREVIEW else 9
SCATTER_SAMPLE_LIMIT = 18 if FAST_PREVIEW else 90

OUTPUT_DIR = Path(__file__).resolve().parent / "outputs"
PREVIEW_IMAGE_PATH = OUTPUT_DIR / "volume_projection_preview.png"
PREVIEW_BLEND_PATH = OUTPUT_DIR / "volume_projection_scene.blend"


# ========== 通用工具 ==========

Color = Tuple[float, float, float, float]


def set_input(node, names: Sequence[str], value) -> bool:
    """兼容 Blender 3.x/4.x 的节点输入名称差异。"""
    for name in names:
        if name in node.inputs:
            node.inputs[name].default_value = value
            return True
    return False


def ensure_collection(name: str) -> bpy.types.Collection:
    collection = bpy.data.collections.get(name)
    if collection is None:
        collection = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(collection)
    return collection


def link_to_collection(obj: bpy.types.Object, collection: Optional[bpy.types.Collection]) -> bpy.types.Object:
    if collection is None:
        return obj

    if obj.name not in collection.objects.keys():
        collection.objects.link(obj)
    for old_collection in list(obj.users_collection):
        if old_collection != collection:
            old_collection.objects.unlink(obj)
    return obj


def look_at(obj: bpy.types.Object, target: Vector) -> None:
    direction = target - obj.location
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def set_parent_keep_transform(child: bpy.types.Object, parent: bpy.types.Object) -> None:
    world_matrix = child.matrix_world.copy()
    child.parent = parent
    child.matrix_parent_inverse = parent.matrix_world.inverted()
    child.matrix_world = world_matrix


def stable_hash(ix: int, iz: int, salt: int = 0) -> float:
    n = (ix * 73856093) ^ (iz * 19349663) ^ (salt * 83492791)
    n = (n ^ (n >> 13)) * 1274126177
    return ((n & 0xFFFFFFFF) / 0xFFFFFFFF)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def log_step(message: str) -> None:
    print(f"[VolumeProjection] {message}", flush=True)


def prepare_blender_context() -> None:
    """避免当前处于 Sculpt/Edit 等模式时，运行脚本触发活动工具 poll 错误。"""
    try:
        if bpy.ops.object.mode_set.poll():
            bpy.ops.object.mode_set(mode="OBJECT")
    except Exception:
        pass

    try:
        bpy.ops.wm.tool_set_by_id(name="builtin.select")
    except Exception:
        pass


def scene_clear() -> None:
    prepare_blender_context()
    log_step("清理旧场景...")
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()

    for mesh in list(bpy.data.meshes):
        if mesh.users == 0 or mesh.name.startswith(("ProjectedPixel_TemplateMesh", "BubblePixel_TemplateMesh")):
            bpy.data.meshes.remove(mesh)

    for material in list(bpy.data.materials):
        if material.users == 0:
            bpy.data.materials.remove(material)


# ========== 材质 ==========

def create_simple_material(name: str, color: Color, roughness: float = 0.45) -> bpy.types.Material:
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True

    nodes = mat.node_tree.nodes
    nodes.clear()

    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    output = nodes.new("ShaderNodeOutputMaterial")

    set_input(bsdf, ("Base Color",), color)
    set_input(bsdf, ("Roughness",), roughness)
    mat.node_tree.links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])
    mat.diffuse_color = color
    return mat


def create_glass_material(name: str, color: Color, alpha: float) -> bpy.types.Material:
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    mat.blend_method = "BLEND"
    mat.diffuse_color = (color[0], color[1], color[2], alpha)

    if hasattr(mat, "use_screen_refraction"):
        mat.use_screen_refraction = True
    if hasattr(mat, "show_transparent_back"):
        mat.show_transparent_back = True

    nodes = mat.node_tree.nodes
    nodes.clear()

    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    output = nodes.new("ShaderNodeOutputMaterial")

    set_input(bsdf, ("Base Color",), color)
    set_input(bsdf, ("Alpha",), alpha)
    set_input(bsdf, ("Roughness",), 0.03)
    set_input(bsdf, ("Metallic",), 0.0)
    set_input(bsdf, ("Transmission Weight", "Transmission"), 0.65)
    set_input(bsdf, ("IOR",), 1.46)

    mat.node_tree.links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])
    return mat


def create_emission_material(
    name: str,
    color: Color,
    strength: float = 2.0,
    alpha: float = 1.0,
) -> bpy.types.Material:
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    mat.diffuse_color = (color[0], color[1], color[2], alpha)
    mat.blend_method = "BLEND"

    nodes = mat.node_tree.nodes
    nodes.clear()

    emission = nodes.new("ShaderNodeEmission")
    output = nodes.new("ShaderNodeOutputMaterial")

    emission.inputs["Color"].default_value = color
    emission.inputs["Strength"].default_value = strength
    mat.node_tree.links.new(emission.outputs["Emission"], output.inputs["Surface"])
    return mat


def create_object_color_emission_material(name: str, strength: float = 1.8) -> bpy.types.Material:
    """使用对象颜色驱动材质，方便大量气泡/像素共用同一份网格。"""
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    mat.blend_method = "BLEND"

    nodes = mat.node_tree.nodes
    nodes.clear()

    object_info = nodes.new("ShaderNodeObjectInfo")
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    output = nodes.new("ShaderNodeOutputMaterial")

    mat.node_tree.links.new(object_info.outputs["Color"], bsdf.inputs["Base Color"])
    if "Emission Color" in bsdf.inputs:
        mat.node_tree.links.new(object_info.outputs["Color"], bsdf.inputs["Emission Color"])
    elif "Emission" in bsdf.inputs:
        mat.node_tree.links.new(object_info.outputs["Color"], bsdf.inputs["Emission"])
    set_input(bsdf, ("Emission Strength",), strength)
    set_input(bsdf, ("Alpha",), 0.96)
    set_input(bsdf, ("Roughness",), 0.18)

    mat.node_tree.links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])
    return mat


def create_checker_floor_material(name: str) -> bpy.types.Material:
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True

    nodes = mat.node_tree.nodes
    nodes.clear()

    texcoord = nodes.new("ShaderNodeTexCoord")
    mapping = nodes.new("ShaderNodeMapping")
    checker = nodes.new("ShaderNodeTexChecker")
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    output = nodes.new("ShaderNodeOutputMaterial")

    checker.inputs["Color1"].default_value = (0.18, 0.18, 0.18, 1.0)
    checker.inputs["Color2"].default_value = (0.26, 0.26, 0.26, 1.0)
    checker.inputs["Scale"].default_value = 7.0
    set_input(bsdf, ("Roughness",), 0.65)

    mat.node_tree.links.new(texcoord.outputs["Generated"], mapping.inputs["Vector"])
    mat.node_tree.links.new(mapping.outputs["Vector"], checker.inputs["Vector"])
    mat.node_tree.links.new(checker.outputs["Color"], bsdf.inputs["Base Color"])
    mat.node_tree.links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])
    return mat


# ========== 几何创建 ==========

def add_cube(
    name: str,
    location: Iterable[float],
    scale: Iterable[float],
    material: Optional[bpy.types.Material] = None,
    collection: Optional[bpy.types.Collection] = None,
) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=location)
    obj = bpy.context.object
    obj.name = name
    obj.scale = scale
    if material is not None:
        obj.data.materials.append(material)
    return link_to_collection(obj, collection)


def create_curve(
    name: str,
    points: Sequence[Vector],
    material: bpy.types.Material,
    bevel_depth: float = 0.004,
    collection: Optional[bpy.types.Collection] = None,
) -> bpy.types.Object:
    curve = bpy.data.curves.new(name=name, type="CURVE")
    curve.dimensions = "3D"
    curve.resolution_u = 1
    curve.bevel_depth = bevel_depth
    curve.bevel_resolution = 1

    spline = curve.splines.new("POLY")
    spline.points.add(len(points) - 1)
    for point, co in zip(spline.points, points):
        point.co = (co.x, co.y, co.z, 1.0)

    obj = bpy.data.objects.new(name, curve)
    bpy.context.collection.objects.link(obj)
    obj.data.materials.append(material)
    return link_to_collection(obj, collection)


def create_wire_box(
    name: str,
    center: Vector,
    size: Vector,
    material: bpy.types.Material,
    bevel_depth: float,
    collection: Optional[bpy.types.Collection] = None,
) -> List[bpy.types.Object]:
    hw, hd, hh = size.x / 2, size.y / 2, size.z / 2
    cx, cy, cz = center.x, center.y, center.z
    vertices = [
        Vector((cx - hw, cy - hd, cz - hh)),
        Vector((cx + hw, cy - hd, cz - hh)),
        Vector((cx + hw, cy + hd, cz - hh)),
        Vector((cx - hw, cy + hd, cz - hh)),
        Vector((cx - hw, cy - hd, cz + hh)),
        Vector((cx + hw, cy - hd, cz + hh)),
        Vector((cx + hw, cy + hd, cz + hh)),
        Vector((cx - hw, cy + hd, cz + hh)),
    ]
    edges = [
        (0, 1), (1, 2), (2, 3), (3, 0),
        (4, 5), (5, 6), (6, 7), (7, 4),
        (0, 4), (1, 5), (2, 6), (3, 7),
    ]

    lines = []
    for index, (a, b) in enumerate(edges):
        lines.append(
            create_curve(
                f"{name}_Edge_{index:02d}",
                (vertices[a], vertices[b]),
                material,
                bevel_depth,
                collection,
            )
        )
    return lines


def create_frustum_mesh(
    name: str,
    lens: Vector,
    far_corners: Sequence[Vector],
    material: bpy.types.Material,
    collection: Optional[bpy.types.Collection] = None,
) -> bpy.types.Object:
    mesh = bpy.data.meshes.new(f"{name}_Mesh")
    verts = [lens] + list(far_corners)
    faces = [
        (0, 1, 2),
        (0, 2, 3),
        (0, 3, 4),
        (0, 4, 1),
        (1, 2, 3, 4),
    ]
    mesh.from_pydata([tuple(v) for v in verts], [], faces)
    mesh.update()

    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    obj.data.materials.append(material)
    return link_to_collection(obj, collection)


def make_template_meshes(pixel_material: bpy.types.Material, bubble_material: bpy.types.Material) -> Dict[str, bpy.types.Mesh]:
    bpy.ops.mesh.primitive_cube_add(size=1.0)
    pixel_template = bpy.context.object
    pixel_mesh = pixel_template.data
    pixel_mesh.name = "ProjectedPixel_TemplateMesh"
    pixel_mesh.materials.append(pixel_material)
    pixel_mesh.use_fake_user = True
    bpy.data.objects.remove(pixel_template, do_unlink=True)

    bpy.ops.mesh.primitive_uv_sphere_add(segments=10, ring_count=5, radius=1.0)
    bubble_template = bpy.context.object
    bubble_mesh = bubble_template.data
    bubble_mesh.name = "BubblePixel_TemplateMesh"
    bubble_mesh.materials.append(bubble_material)
    bubble_mesh.use_fake_user = True
    bpy.data.objects.remove(bubble_template, do_unlink=True)

    return {"pixel": pixel_mesh, "bubble": bubble_mesh}


# ========== 投影几何 ==========

class Projector:
    def __init__(self, location: Iterable[float], size: Iterable[float]):
        self.location = Vector(location)
        self.width, self.depth, self.height = size
        self.object: Optional[bpy.types.Object] = None
        self.lens_marker: Optional[bpy.types.Object] = None

    @property
    def lens_position(self) -> Vector:
        return Vector((self.location.x, self.location.y - self.depth / 2 - 0.08, self.location.z))

    def create(self, collection: bpy.types.Collection, materials: Dict[str, bpy.types.Material]) -> bpy.types.Object:
        body = add_cube("Projector_Body", self.location, (self.width, self.depth, self.height), materials["projector"], collection)
        self.object = body

        top_y = self.location.y + 0.04
        top_z = self.location.z + self.height / 2 + 0.055
        for index in range(8):
            x = -self.width * 0.38 + index * (self.width * 0.76 / 7)
            fin = add_cube(
                f"Projector_CoolingFin_{index:02d}",
                (x, top_y, top_z),
                (0.045, self.depth * 0.82, 0.11),
                materials["projector_dark"],
                collection,
            )
            set_parent_keep_transform(fin, body)

        lens_position = self.lens_position
        bpy.ops.mesh.primitive_cone_add(
            vertices=48,
            radius1=0.18,
            radius2=0.10,
            depth=0.26,
            location=(lens_position.x, lens_position.y + 0.07, lens_position.z),
            rotation=(math.radians(90), 0.0, 0.0),
        )
        lens = bpy.context.object
        lens.name = "Projector_Lens"
        lens.data.materials.append(materials["lens"])
        link_to_collection(lens, collection)
        set_parent_keep_transform(lens, body)

        bpy.ops.mesh.primitive_uv_sphere_add(segments=24, ring_count=12, radius=0.045, location=lens_position)
        marker = bpy.context.object
        marker.name = "Projector_Lens_Origin"
        marker.data.materials.append(materials["lens_core"])
        self.lens_marker = link_to_collection(marker, collection)
        set_parent_keep_transform(self.lens_marker, body)

        return body


class ProjectionVolume:
    def __init__(self, center: Iterable[float], size: Iterable[float], alpha: float):
        self.center = Vector(center)
        self.size = Vector(size)
        self.alpha = alpha
        self.object: Optional[bpy.types.Object] = None

    @property
    def near_y(self) -> float:
        return self.center.y + self.size.y / 2

    @property
    def far_y(self) -> float:
        return self.center.y - self.size.y / 2

    @property
    def min_x(self) -> float:
        return self.center.x - self.size.x / 2

    @property
    def max_x(self) -> float:
        return self.center.x + self.size.x / 2

    @property
    def min_z(self) -> float:
        return self.center.z - self.size.z / 2

    @property
    def max_z(self) -> float:
        return self.center.z + self.size.z / 2

    def target_on_far_face(self, ix: float, iz: float, nx: int, nz: int, fill: float = IMAGE_FILL) -> Vector:
        u = ix / (nx - 1) if nx > 1 else 0.5
        v = iz / (nz - 1) if nz > 1 else 0.5
        x = self.center.x + (u - 0.5) * self.size.x * fill
        z = self.center.z + (v - 0.5) * self.size.z * fill
        return Vector((x, self.far_y - 0.012, z))

    def ray_point_at_depth(self, lens: Vector, target: Vector, depth_fraction: float) -> Vector:
        y = self.near_y - self.size.y * depth_fraction
        direction = (target - lens).normalized()
        t = (y - lens.y) / direction.y
        return lens + direction * t

    def create(self, collection: bpy.types.Collection, materials: Dict[str, bpy.types.Material]) -> bpy.types.Object:
        cube = add_cube("Transparent_Glass_Cube", self.center, self.size, materials["glass"], collection)
        self.object = cube

        create_wire_box(
            "Transparent_Glass_Cube_Wireframe",
            self.center,
            self.size,
            materials["wire"],
            0.006,
            collection,
        )

        entry_panel = add_cube(
            "Incoming_Light_Entry_Face",
            (self.center.x, self.near_y + 0.004, self.center.z),
            (self.size.x * 0.86, 0.012, self.size.z * 0.86),
            materials["entry_panel"],
            collection,
        )
        exit_panel = add_cube(
            "Projected_Image_Exit_Face",
            (self.center.x, self.far_y - 0.006, self.center.z),
            (self.size.x * 0.86, 0.012, self.size.z * 0.86),
            materials["exit_panel"],
            collection,
        )
        set_parent_keep_transform(entry_panel, cube)
        set_parent_keep_transform(exit_panel, cube)

        for index in range(1, BUBBLE_DEPTH_LAYERS):
            fraction = index / BUBBLE_DEPTH_LAYERS
            y = self.near_y - self.size.y * fraction
            guide = create_curve(
                f"Bubble_Depth_Layer_{index:02d}",
                (
                    Vector((self.min_x, y, self.min_z)),
                    Vector((self.max_x, y, self.min_z)),
                    Vector((self.max_x, y, self.max_z)),
                    Vector((self.min_x, y, self.max_z)),
                    Vector((self.min_x, y, self.min_z)),
                ),
                materials["depth_guide"],
                0.002,
                collection,
            )
            set_parent_keep_transform(guide, cube)

        return cube


# ========== 像素图案和气泡 ==========

COLOR_BARS: Tuple[Color, ...] = (
    (1.0, 1.0, 1.0, 1.0),
    (1.0, 0.92, 0.10, 1.0),
    (0.08, 0.95, 1.0, 1.0),
    (0.05, 0.95, 0.26, 1.0),
    (1.0, 0.08, 0.88, 1.0),
    (1.0, 0.12, 0.08, 1.0),
    (0.14, 0.26, 1.0, 1.0),
)


def pattern_pixel(ix: int, iz: int, nx: int, nz: int) -> Tuple[bool, Color, float]:
    """返回该像素是否发光、颜色和亮度，用于构建远侧投影图案和体素气泡。"""
    edge = ix in (0, nx - 1) or iz in (0, nz - 1)
    if edge:
        return True, (1.0, 1.0, 1.0, 1.0), 1.0

    top_bar_rows = max(3, nz // 6)
    if iz >= nz - 1 - top_bar_rows:
        bar = min(len(COLOR_BARS) - 1, int(ix / nx * len(COLOR_BARS)))
        return True, COLOR_BARS[bar], 0.95

    right_strip = ix >= nx - 3
    if right_strip:
        bar = min(len(COLOR_BARS) - 1, int(iz / nz * len(COLOR_BARS)))
        return True, COLOR_BARS[bar], 0.9

    grid_line = ix % 6 == 0 or iz % 5 == 0
    if grid_line:
        return True, (0.68, 0.72, 0.75, 1.0), 0.36

    x = (ix - (nx - 1) / 2) / nx
    z = (iz - (nz - 1) / 2) / nz

    target_ring = 0.095 < math.sqrt((x * 1.3) ** 2 + (z * 1.8) ** 2) < 0.13
    target_cross = abs(x) < 0.018 and abs(z) < 0.17 or abs(z) < 0.024 and abs(x) < 0.17
    if target_ring or target_cross:
        return True, (1.0, 0.92, 0.22, 1.0), 0.88

    diagonal = abs((iz / max(nz - 1, 1)) - (ix / max(nx - 1, 1)) * 0.62 - 0.16) < 0.035
    if diagonal:
        return True, (0.72, 0.95, 1.0, 1.0), 0.72

    return False, (0.45, 0.47, 0.48, 1.0), 0.12


class BubbleField:
    def __init__(self, projector: Projector, volume: ProjectionVolume, meshes: Dict[str, bpy.types.Mesh]):
        self.projector = projector
        self.volume = volume
        self.meshes = meshes
        self.bubbles: List[bpy.types.Object] = []
        self.active_bubbles: List[bpy.types.Object] = []
        self.pattern_pixels: List[bpy.types.Object] = []

    def _depth_fraction(self, ix: int, iz: int) -> float:
        layer = (ix * 3 + iz * 5) % BUBBLE_DEPTH_LAYERS
        base = (layer + 0.5) / BUBBLE_DEPTH_LAYERS
        jitter = (stable_hash(ix, iz, 4) - 0.5) * 0.08
        return clamp(base + jitter, 0.08, 0.92)

    def _create_linked_object(
        self,
        name: str,
        mesh: bpy.types.Mesh,
        location: Vector,
        scale: Tuple[float, float, float],
        color: Color,
        collection: bpy.types.Collection,
    ) -> bpy.types.Object:
        obj = bpy.data.objects.new(name, mesh)
        bpy.context.collection.objects.link(obj)
        obj.location = location
        obj.scale = scale
        obj.color = color
        link_to_collection(obj, collection)
        return obj

    def create_projection_pixels(self, collection: bpy.types.Collection) -> List[bpy.types.Object]:
        cell_w = self.volume.size.x * IMAGE_FILL / IMAGE_GRID_X
        cell_h = self.volume.size.z * IMAGE_FILL / IMAGE_GRID_Z
        pixel_scale = (cell_w * 0.46, 0.004, cell_h * 0.46)

        for ix in range(IMAGE_GRID_X):
            for iz in range(IMAGE_GRID_Z):
                active, color, brightness = pattern_pixel(ix, iz, IMAGE_GRID_X, IMAGE_GRID_Z)
                if not active:
                    continue

                target = self.volume.target_on_far_face(ix, iz, IMAGE_GRID_X, IMAGE_GRID_Z)
                pixel = self._create_linked_object(
                    f"Projected_Image_Pixel_{ix:02d}_{iz:02d}",
                    self.meshes["pixel"],
                    target,
                    pixel_scale,
                    (color[0] * brightness, color[1] * brightness, color[2] * brightness, 1.0),
                    collection,
                )
                self.pattern_pixels.append(pixel)

        return self.pattern_pixels

    def create_bubbles(self, collection: bpy.types.Collection) -> List[bpy.types.Object]:
        for ix in range(IMAGE_GRID_X):
            for iz in range(IMAGE_GRID_Z):
                active, color, brightness = pattern_pixel(ix, iz, IMAGE_GRID_X, IMAGE_GRID_Z)
                background = SHOW_BACKGROUND_BUBBLES and (not active) and stable_hash(ix, iz, 7) < BACKGROUND_BUBBLE_RATE
                if not active and not background:
                    continue

                target = self.volume.target_on_far_face(ix, iz, IMAGE_GRID_X, IMAGE_GRID_Z)
                depth_fraction = self._depth_fraction(ix, iz)
                position = self.volume.ray_point_at_depth(self.projector.lens_position, target, depth_fraction)

                if active:
                    radius = BUBBLE_RADIUS * (0.78 + 0.55 * brightness)
                    bubble_color = (
                        clamp(color[0] * (0.75 + brightness * 0.4), 0.0, 1.0),
                        clamp(color[1] * (0.75 + brightness * 0.4), 0.0, 1.0),
                        clamp(color[2] * (0.75 + brightness * 0.4), 0.0, 1.0),
                        1.0,
                    )
                    name = f"BubblePixel_Active_{ix:02d}_{iz:02d}_L{int(depth_fraction * BUBBLE_DEPTH_LAYERS):02d}"
                else:
                    radius = BUBBLE_RADIUS * (0.35 + stable_hash(ix, iz, 8) * 0.25)
                    gray = 0.45 + stable_hash(ix, iz, 9) * 0.25
                    bubble_color = (gray, gray, gray, 0.72)
                    name = f"NoiseBubble_Background_{ix:02d}_{iz:02d}"

                bubble = self._create_linked_object(
                    name,
                    self.meshes["bubble"],
                    position,
                    (radius, radius, radius),
                    bubble_color,
                    collection,
                )
                self.bubbles.append(bubble)
                if active:
                    self.active_bubbles.append(bubble)

        return self.bubbles


# ========== 光路 ==========

def create_projection_rays(
    projector: Projector,
    volume: ProjectionVolume,
    collection: bpy.types.Collection,
    materials: Dict[str, bpy.types.Material],
) -> List[bpy.types.Object]:
    rays: List[bpy.types.Object] = []
    lens = projector.lens_position

    for ix in range(RAY_GRID_X):
        for iz in range(RAY_GRID_Z):
            target = volume.target_on_far_face(ix, iz, RAY_GRID_X, RAY_GRID_Z)
            name = f"Projector_Ray_{ix:02d}_{iz:02d}"
            rays.append(create_curve(name, (lens, target), materials["ray"], 0.0022, collection))

    far_corners = (
        volume.target_on_far_face(0, 0, RAY_GRID_X, RAY_GRID_Z),
        volume.target_on_far_face(RAY_GRID_X - 1, 0, RAY_GRID_X, RAY_GRID_Z),
        volume.target_on_far_face(RAY_GRID_X - 1, RAY_GRID_Z - 1, RAY_GRID_X, RAY_GRID_Z),
        volume.target_on_far_face(0, RAY_GRID_Z - 1, RAY_GRID_X, RAY_GRID_Z),
    )
    create_frustum_mesh("Projected_Light_Frustum", lens, far_corners, materials["frustum"], collection)
    return rays

def create_ray_aligned_bubble_paths(
    projector: Projector,
    bubble_field: BubbleField,
    collection: bpy.types.Collection,
    materials: Dict[str, bpy.types.Material],
) -> List[bpy.types.Object]:
    """显示从投影仪镜头到有效气泡体素的光线路径。"""
    paths: List[bpy.types.Object] = []
    active_bubbles = bubble_field.active_bubbles
    if not active_bubbles:
        return paths

    stride = max(1, len(active_bubbles) // BUBBLE_PATH_RAY_LIMIT)
    selected = active_bubbles[::stride][:BUBBLE_PATH_RAY_LIMIT]
    lens = projector.lens_position

    for index, bubble in enumerate(selected):
        paths.append(
            create_curve(
                f"RayAligned_Bubble_Path_{index:03d}",
                (lens, Vector(bubble.location)),
                materials["bubble_path_ray"],
                0.0016,
                collection,
            )
        )

    return paths

def create_scattering_rays(
    bubble_field: BubbleField,
    camera_location: Vector,
    collection: bpy.types.Collection,
    materials: Dict[str, bpy.types.Material],
) -> List[bpy.types.Object]:
    scatter: List[bpy.types.Object] = []
    if not bubble_field.active_bubbles:
        return scatter

    stride = max(1, len(bubble_field.active_bubbles) // SCATTER_SAMPLE_LIMIT)
    selected = bubble_field.active_bubbles[::stride][:SCATTER_SAMPLE_LIMIT]

    for index, bubble in enumerate(selected):
        start = bubble.location
        to_camera = (camera_location - start).normalized()
        side = Vector((
            0.35 + (stable_hash(index, 0, 11) - 0.5) * 0.42,
            0.30 + (stable_hash(index, 0, 12) - 0.5) * 0.22,
            (stable_hash(index, 0, 13) - 0.5) * 0.55,
        )).normalized()

        length_a = 0.34 + stable_hash(index, 0, 14) * 0.34
        length_b = 0.22 + stable_hash(index, 0, 15) * 0.28

        scatter.append(
            create_curve(
                f"Bubble_Scatter_ToCamera_{index:03d}",
                (start, start + to_camera * length_a),
                materials["scatter"],
                0.004,
                collection,
            )
        )
        if index % 3 == 0:
            scatter.append(
                create_curve(
                    f"Bubble_Scatter_Side_{index:03d}",
                    (start, start + side * length_b),
                    materials["scatter_side"],
                    0.0028,
                    collection,
                )
            )

    return scatter


def create_laser_written_guides(
    volume: ProjectionVolume,
    collection: bpy.types.Collection,
    materials: Dict[str, bpy.types.Material],
) -> List[bpy.types.Object]:
    guides = []
    for index, fraction in enumerate((0.28, 0.54, 0.78)):
        y = volume.near_y - volume.size.y * fraction
        z = volume.center.z + (index - 1) * volume.size.z * 0.22
        guides.append(
            create_curve(
                f"Bubble_Writing_Laser_Path_{index:02d}",
                (
                    Vector((volume.min_x - 0.42, y, z - 0.12)),
                    Vector((volume.max_x + 0.42, y, z + 0.12)),
                ),
                materials["writing_laser"],
                0.0035,
                collection,
            )
        )
    return guides


# ========== 场景布置 ==========

def setup_scene(camera_target: Vector) -> Vector:
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.render.resolution_x = 1800
    scene.render.resolution_y = 1100

    for engine in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE", "CYCLES"):
        try:
            scene.render.engine = engine
            break
        except Exception:
            continue

    if scene.render.engine == "CYCLES" and hasattr(scene, "cycles"):
        scene.cycles.samples = 96
        scene.cycles.use_denoising = True

    try:
        scene.view_settings.view_transform = "Filmic"
        scene.view_settings.look = "Medium High Contrast"
    except Exception:
        pass
    scene.view_settings.exposure = 0.0
    scene.view_settings.gamma = 1.0

    world = scene.world or bpy.data.worlds.new("World")
    scene.world = world
    world.color = (0.03, 0.035, 0.04)

    bpy.ops.object.light_add(type="AREA", location=(-2.8, 1.8, 5.2))
    key = bpy.context.object
    key.name = "Large_Softbox_Key_Light"
    key.data.energy = 360
    key.data.size = 5.0

    bpy.ops.object.light_add(type="POINT", location=(1.3, -3.0, 2.4))
    glint = bpy.context.object
    glint.name = "Glass_Glint_Light"
    glint.data.energy = 80
    glint.data.color = (0.72, 0.88, 1.0)

    bpy.ops.object.camera_add(location=(-5.6, 3.4, 3.05))
    camera = bpy.context.object
    camera.name = "Camera_Overview"
    camera.data.lens = 28
    look_at(camera, camera_target)
    scene.camera = camera

    return Vector(camera.location)


def create_floor(material: bpy.types.Material, collection: bpy.types.Collection) -> bpy.types.Object:
    bpy.ops.mesh.primitive_plane_add(size=8.0, location=(0.0, -1.65, 0.0))
    floor = bpy.context.object
    floor.name = "Dark_Checker_Floor"
    floor.data.materials.append(material)
    return link_to_collection(floor, collection)


def build_materials() -> Dict[str, bpy.types.Material]:
    return {
        "projector": create_simple_material("Mat_Projector_GreenBlack", (0.08, 0.12, 0.08, 1.0), 0.55),
        "projector_dark": create_simple_material("Mat_Projector_CoolingFins", (0.035, 0.055, 0.035, 1.0), 0.68),
        "lens": create_glass_material("Mat_Projector_Lens_Glass", (0.1, 0.12, 0.13, 1.0), 0.72),
        "lens_core": create_emission_material("Mat_Lens_Emission_Point", (1.0, 0.55, 0.16, 1.0), 4.0),
        "glass": create_glass_material("Mat_Transparent_Glass_Cube", (0.72, 0.92, 1.0, 1.0), CUBE_ALPHA),
        "wire": create_emission_material("Mat_Glass_Cube_Wireframe", (0.55, 0.75, 0.95, 1.0), 0.7),
        "ray": create_emission_material("Mat_Projector_Thin_Rays", (0.02, 0.018, 0.012, 1.0), 0.35),
        "bubble_path_ray": create_emission_material("Mat_Ray_Aligned_Bubble_Paths", (1.0, 0.82, 0.18, 1.0), 1.15),
        "frustum": create_glass_material("Mat_Faint_Yellow_Light_Frustum", (1.0, 0.84, 0.18, 1.0), 0.08),
        "scatter": create_emission_material("Mat_Bubble_Scatter_White", (0.92, 0.98, 1.0, 1.0), 3.6),
        "scatter_side": create_emission_material("Mat_Bubble_Scatter_Cyan", (0.35, 0.92, 1.0, 1.0), 2.4),
        "writing_laser": create_emission_material("Mat_Laser_Written_Bubble_Guide", (0.55, 0.26, 1.0, 1.0), 2.8),
        "pixel_object_color": create_object_color_emission_material("Mat_Projected_Image_Pixel_ObjectColor", 2.5),
        "bubble_object_color": create_object_color_emission_material("Mat_Bubble_ObjectColor", 2.2),
        "entry_panel": create_glass_material("Mat_Entry_Face_Warm_Glow", (1.0, 0.82, 0.20, 1.0), 0.12),
        "exit_panel": create_glass_material("Mat_Exit_Face_Cool_Glow", (0.72, 0.92, 1.0, 1.0), 0.08),
        "depth_guide": create_emission_material("Mat_Bubble_Depth_Guides", (0.70, 0.72, 0.76, 1.0), 0.45),
        "floor": create_checker_floor_material("Mat_Dark_Checker_Floor"),
    }


def build_scene() -> Dict[str, object]:
    log_step("开始生成体积投影场景。")
    scene_clear()

    log_step("创建集合分组...")
    collections = {
        "projector": ensure_collection("01_Projector"),
        "volume": ensure_collection("02_Glass_Volume"),
        "rays": ensure_collection("03_Light_Rays"),
        "bubbles": ensure_collection("04_Bubble_Pixels"),
        "image": ensure_collection("05_Projected_Image"),
        "environment": ensure_collection("06_Environment"),
    }

    log_step("创建材质和模板网格...")
    materials = build_materials()
    meshes = make_template_meshes(materials["pixel_object_color"], materials["bubble_object_color"])

    log_step("创建投影仪...")
    projector = Projector(PROJECTOR_LOCATION, PROJECTOR_SIZE)
    projector.create(collections["projector"], materials)

    log_step("创建透明玻璃立方体...")
    volume = ProjectionVolume(CUBE_CENTER, CUBE_SIZE, CUBE_ALPHA)
    volume.create(collections["volume"], materials)

    log_step("设置相机、灯光和地面...")
    camera_location = setup_scene(volume.center)
    create_floor(materials["floor"], collections["environment"])

    log_step(f"创建投影图案像素: {IMAGE_GRID_X} x {IMAGE_GRID_Z}...")
    bubble_field = BubbleField(projector, volume, meshes)
    bubble_field.create_projection_pixels(collections["image"])

    log_step("创建玻璃内部气泡像素...")
    bubble_field.create_bubbles(collections["bubbles"])

    log_step(f"创建入射光线: {RAY_GRID_X} x {RAY_GRID_Z}...")
    create_projection_rays(projector, volume, collections["rays"], materials)

    log_step("创建气泡路径验证光线...")
    create_ray_aligned_bubble_paths(projector, bubble_field, collections["rays"], materials)

    log_step("创建气泡散射光线...")
    create_scattering_rays(bubble_field, camera_location, collections["rays"], materials)

    log_step("创建激光写入辅助路径...")
    create_laser_written_guides(volume, collections["rays"], materials)

    print("=" * 72)
    print("体积投影仿真场景创建完成")
    print(f"投影仪镜头位置: {tuple(round(v, 3) for v in projector.lens_position)}")
    print(f"玻璃立方体中心: {tuple(round(v, 3) for v in volume.center)}")
    print(f"玻璃立方体尺寸: {volume.size.x:.2f}m x {volume.size.y:.2f}m x {volume.size.z:.2f}m")
    print(f"投影像素网格: {IMAGE_GRID_X} x {IMAGE_GRID_Z}")
    print(f"气泡总数: {len(bubble_field.bubbles)}")
    print(f"有效发光气泡: {len(bubble_field.active_bubbles)}")
    print(f"背景杂散气泡: {len(bubble_field.bubbles) - len(bubble_field.active_bubbles)}")
    print(f"深度层数: {BUBBLE_DEPTH_LAYERS}")
    print("对象已按 01_Projector / 02_Glass_Volume / 03_Light_Rays / 04_Bubble_Pixels 分组。")
    print("=" * 72)

    return {
        "projector": projector,
        "volume": volume,
        "bubble_field": bubble_field,
    }

def get_script_args() -> List[str]:
    if "--" not in sys.argv:
        return []
    return sys.argv[sys.argv.index("--") + 1:]


def render_preview(output_path: Path = PREVIEW_IMAGE_PATH) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    scene = bpy.context.scene
    scene.render.resolution_x = 1280
    scene.render.resolution_y = 820
    scene.render.film_transparent = False
    scene.render.filepath = str(output_path)
    scene.render.image_settings.file_format = "PNG"

    log_step(f"渲染验证预览图: {output_path}")
    bpy.ops.render.render(write_still=True)
    return output_path


def save_blend_file(output_path: Path = PREVIEW_BLEND_PATH) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    log_step(f"保存 Blender 场景文件: {output_path}")
    bpy.ops.wm.save_as_mainfile(filepath=str(output_path))
    return output_path


def main() -> None:
    window_manager = bpy.context.window_manager
    if window_manager.get(RUNNING_FLAG_KEY):
        log_step("检测到脚本已经在运行，本次重复启动已跳过。")
        return

    args = get_script_args()
    window_manager[RUNNING_FLAG_KEY] = True
    try:
        build_scene()
        if "--render-preview" in args:
            render_preview()
        if "--save-blend" in args:
            save_blend_file()
    finally:
        window_manager[RUNNING_FLAG_KEY] = False


if __name__ in {"__main__", "<run_path>"}:
    main()
