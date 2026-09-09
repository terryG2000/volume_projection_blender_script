# context.area: PROPERTIES
"""
体积投影仿真建模脚本

在 Blender 中运行本脚本，会创建一个普通投影仪向透明玻璃立方体投影的场景。
立方体内部的微小气泡按投影图案和深度层分布，每个气泡代表一个可散射光的体素/像素点。

GUI 保留窗口运行示例：
    blender.exe --python projector.py -- --keep-open
后台导出预览图示例：
    blender.exe --background --python projector.py -- --render-preview
"""

import math
import csv
import random
import json
import hashlib
import time
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import bpy
from mathutils import Matrix, Vector


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
BUBBLE_PATH_RAY_LIMIT = 160 if FAST_PREVIEW else 260
SCENE_VERSION = 2
DEPTH_SEED = 2006
MODEL_KEYS = ("vp_fov_x", "vp_fov_z", "vp_samples_x", "vp_samples_z", "vp_layers", "vp_radius", "vp_freeze_points")
OPTICAL_KEYS = ("vp_optics", "vp_flux_w", "vp_scatter_albedo", "vp_show_receiver", "vp_source_image")

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
    collection = bpy.context.scene.collection.children.get(name)
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
    bpy.context.view_layer.update()
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

    if not bpy.app.background:
        try:
            bpy.ops.wm.tool_set_by_id(name="builtin.select")
        except Exception:
            pass


def scene_clear() -> None:
    prepare_blender_context()
    log_step("清理旧场景...")
    for obj in list(bpy.context.scene.objects):
        if obj.get("vp_generated"):
            light = obj.data if obj.type == "LIGHT" else None
            bpy.data.objects.remove(obj, do_unlink=True)
            if light is not None and light.users == 0:
                bpy.data.lights.remove(light)

    for mesh in list(bpy.data.meshes):
        if mesh.users == 0:
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
        if self.lens_marker is not None:
            return self.lens_marker.matrix_world.translation.copy()
        return Vector((self.location.x, self.location.y - self.depth / 2 - 0.08, self.location.z))

    def create(self, collection: bpy.types.Collection, materials: Dict[str, bpy.types.Material]) -> bpy.types.Object:
        body = add_cube("Projector_Body", self.location, (self.width, self.depth, self.height), materials["projector"], collection)
        self.object = body

        top_y = self.location.y + 0.04
        top_z = self.location.z + self.height / 2 + 0.055
        for index in range(8):
            x = self.location.x - self.width * 0.38 + index * (self.width * 0.76 / 7)
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
        bpy.context.view_layer.update()
        body.lock_scale = (True, True, True)

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
        # Permute depths among adjacent rays; never jitter off the pixel ray.
        group, slot = divmod(ix, BUBBLE_DEPTH_LAYERS)
        layers = list(range(BUBBLE_DEPTH_LAYERS))
        random.Random(DEPTH_SEED + iz * 1009 + group * 9176).shuffle(layers)
        layer = layers[slot]
        base = (layer + 0.5) / BUBBLE_DEPTH_LAYERS
        jitter = (stable_hash(ix, iz, 4) - 0.5) * 0.5 / BUBBLE_DEPTH_LAYERS
        return base + jitter

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
                pixel["vp_role"] = "image"
                pixel["pixel_x"] = ix
                pixel["pixel_z"] = iz
                pixel["vp_reference_target"] = list(target)

        return self.pattern_pixels

    def create_bubbles(self, collection: bpy.types.Collection) -> List[bpy.types.Object]:
        for ix in range(IMAGE_GRID_X):
            for iz in range(IMAGE_GRID_Z):
                active, color, brightness = pattern_pixel(ix, iz, IMAGE_GRID_X, IMAGE_GRID_Z)
                target = self.volume.target_on_far_face(ix, iz, IMAGE_GRID_X, IMAGE_GRID_Z)
                depth_fraction = self._depth_fraction(ix, iz)
                position = self.volume.ray_point_at_depth(self.projector.lens_position, target, depth_fraction)

                if active:
                    radius = BUBBLE_RADIUS
                    bubble_color = (
                        clamp(color[0] * (0.75 + brightness * 0.4), 0.0, 1.0),
                        clamp(color[1] * (0.75 + brightness * 0.4), 0.0, 1.0),
                        clamp(color[2] * (0.75 + brightness * 0.4), 0.0, 1.0),
                        1.0,
                    )
                    name = f"BubblePixel_Active_{ix:02d}_{iz:02d}_L{int(depth_fraction * BUBBLE_DEPTH_LAYERS):02d}"
                else:
                    radius = BUBBLE_RADIUS
                    bubble_color = (0.008, 0.008, 0.008, 1.0)
                    name = f"BubblePixel_Dark_{ix:02d}_{iz:02d}"

                # Keep each finite sphere inside its own projected pixel footprint.
                magnification = (position.y - self.projector.lens_position.y) / (target.y - self.projector.lens_position.y)
                pitch = min(self.volume.size.x * IMAGE_FILL / (IMAGE_GRID_X - 1),
                            self.volume.size.z * IMAGE_FILL / (IMAGE_GRID_Z - 1))
                radius = min(radius, pitch * magnification * 0.22)

                bubble = self._create_linked_object(
                    name,
                    self.meshes["bubble"],
                    position,
                    (radius, radius, radius),
                    bubble_color,
                    collection,
                )
                self.bubbles.append(bubble)
                bubble["vp_role"] = "bubble"
                bubble["pixel_x"] = ix
                bubble["pixel_z"] = iz
                bubble["depth_fraction"] = depth_fraction
                bubble["vp_lit"] = active
                bubble["vp_reference_target"] = list(target)
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
            rays[-1]["vp_role"] = "beam"
            rays[-1]["vp_reference_target"] = list(target)
            rays[-1]["vp_coarse"] = True

    far_corners = (
        volume.target_on_far_face(0, 0, RAY_GRID_X, RAY_GRID_Z),
        volume.target_on_far_face(RAY_GRID_X - 1, 0, RAY_GRID_X, RAY_GRID_Z),
        volume.target_on_far_face(RAY_GRID_X - 1, RAY_GRID_Z - 1, RAY_GRID_X, RAY_GRID_Z),
        volume.target_on_far_face(0, RAY_GRID_Z - 1, RAY_GRID_X, RAY_GRID_Z),
    )
    frustum = create_frustum_mesh("Projected_Light_Frustum", lens, far_corners, materials["frustum"], collection)
    frustum["vp_role"] = "frustum"
    return rays

def create_ray_aligned_bubble_paths(
    projector: Projector,
    bubble_field: BubbleField,
    collection: bpy.types.Collection,
    materials: Dict[str, bpy.types.Material],
) -> List[bpy.types.Object]:
    """显示从投影仪镜头到有效气泡体素的光线路径。"""
    paths: List[bpy.types.Object] = []
    active_bubbles = bubble_field.bubbles
    if not active_bubbles:
        return paths

    stride = max(1, len(active_bubbles) // BUBBLE_PATH_RAY_LIMIT)
    selected = active_bubbles[::stride][:BUBBLE_PATH_RAY_LIMIT]
    lens = projector.lens_position

    for index, bubble in enumerate(selected):
        paths.append(
            create_curve(
                f"RayAligned_Bubble_Path_{index:03d}",
                (lens, Vector(bubble["vp_reference_target"])),
                materials["bubble_path_ray"],
                0.0016,
                collection,
            )
        )
        paths[-1]["vp_role"] = "beam"
        paths[-1]["vp_reference_target"] = bubble["vp_reference_target"]
        paths[-1]["pixel_x"] = bubble["pixel_x"]
        paths[-1]["pixel_z"] = bubble["pixel_z"]

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
        scatter[-1]["vp_role"] = "scatter"
        scatter[-1]["vp_bubble"] = bubble.name
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
            scatter[-1]["vp_role"] = "scatter"
            scatter[-1]["vp_bubble"] = bubble.name

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
        guides[-1]["vp_role"] = "writing"
    return guides


# ========== 场景布置 ==========

def setup_scene(camera_target: Vector) -> Vector:
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.render.resolution_x = 1800
    scene.render.resolution_y = 1100

    for engine in ("CYCLES", "BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
        try:
            scene.render.engine = engine
            break
        except Exception:
            continue

    if scene.render.engine == "CYCLES" and hasattr(scene, "cycles"):
        scene.cycles.samples = 32
        scene.cycles.preview_samples = 12
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
    world.use_nodes = True
    world.node_tree.nodes.get("Background").inputs["Strength"].default_value = 0.025

    bpy.ops.object.light_add(type="AREA", location=(-2.8, 1.8, 5.2))
    key = bpy.context.object
    key.name = "Large_Softbox_Key_Light"
    key.data.energy = 100
    key.data.size = 5.0

    bpy.ops.object.light_add(type="POINT", location=(1.3, -3.0, 2.4))
    glint = bpy.context.object
    glint.name = "Glass_Glint_Light"
    glint.data.energy = 3
    glint.data.color = (0.72, 0.88, 1.0)

    bpy.ops.object.camera_add(location=(-7.6, 0.3, 4.2))
    camera = bpy.context.object
    camera.name = "Camera_Overview"
    camera.data.lens = 32
    look_at(camera, Vector((camera_target.x, -1.5, 1.65)))
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
    if bpy.context.scene.get("vp_version") != SCENE_VERSION:
        bpy.context.window.scene = bpy.data.scenes.new("VolumeProjection")
    scene = bpy.context.scene
    previous_objects = set(scene.objects)
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

    bpy.context.view_layer.update()
    for obj in scene.objects:
        if obj not in previous_objects:
            obj["vp_generated"] = True
    initialize_projection(projector, volume)
    update_projection()
    bubble_field.bubbles = [o for o in scene.objects if o.get("vp_role") == "bubble"]
    bubble_field.active_bubbles = [o for o in bubble_field.bubbles if o.get("vp_lit")]
    bubble_field.pattern_pixels = [o for o in scene.objects if o.get("vp_role") == "image"]

    print("=" * 72)
    print("体积投影仿真场景创建完成")
    print(f"投影仪镜头位置: {tuple(round(v, 3) for v in projector.lens_position)}")
    print(f"玻璃立方体中心: {tuple(round(v, 3) for v in volume.center)}")
    print(f"玻璃立方体尺寸: {volume.size.x:.2f}m x {volume.size.y:.2f}m x {volume.size.z:.2f}m")
    print(f"投影像素网格: {IMAGE_GRID_X} x {IMAGE_GRID_Z}")
    print(f"气泡总数: {len(bubble_field.bubbles)}")
    print(f"有效发光气泡: {len(bubble_field.active_bubbles)}")
    print(f"未照亮的固定气泡: {len(bubble_field.bubbles) - len(bubble_field.active_bubbles)}")
    print(f"深度层数: {BUBBLE_DEPTH_LAYERS}")
    print("对象已按 01_Projector / 02_Glass_Volume / 03_Light_Rays / 04_Bubble_Pixels 分组。")
    print("=" * 72)

    return {
        "projector": projector,
        "volume": volume,
        "bubble_field": bubble_field,
    }


def projection_body():
    return bpy.context.scene.objects.get(bpy.context.scene.get("vp_body", ""))


def initialize_projection(projector, volume):
    scene = bpy.context.scene
    body = projector.object
    inverse = body.matrix_world.inverted()
    lens = inverse @ projector.lens_position
    origin = inverse @ volume.target_on_far_face(0, 0, IMAGE_GRID_X, IMAGE_GRID_Z)
    corner = inverse @ volume.target_on_far_face(IMAGE_GRID_X - 1, IMAGE_GRID_Z - 1, IMAGE_GRID_X, IMAGE_GRID_Z)
    body["vp_lens_local"] = list(lens)
    body["vp_plane_origin"] = list(origin)
    body["vp_plane_span"] = [corner.x - origin.x, corner.z - origin.z]
    body["vp_grid"] = [IMAGE_GRID_X, IMAGE_GRID_Z]
    body["vp_calibration_matrix"] = [value for row in body.matrix_world for value in row]
    body["power"] = 1.0
    body.id_properties_ui("power").update(min=0.0, max=5.0)
    scene["vp_version"] = SCENE_VERSION
    scene["vp_body"] = body.name
    scene["vp_volume"] = volume.object.name
    scene["vp_content"] = "TEST"
    scene["vp_auto"] = True
    scene["vp_show_rays"] = False
    scene["vp_show_scatter"] = True
    scene["vp_show_image"] = False
    scene["vp_show_layers"] = False
    for obj in scene.objects:
        if "vp_reference_target" in obj:
            obj["vp_target_local"] = list(inverse @ Vector(obj["vp_reference_target"]))
        if obj.get("vp_role") == "frustum":
            obj["vp_vertices_local"] = [value for vertex in obj.data.vertices for value in (inverse @ vertex.co)]
        if obj.get("vp_role") == "bubble":
            obj["vp_design_position"] = list(obj.matrix_world.translation)
    ensure_model_parameters(reset=True)
    ensure_optical_settings(reset=True)


def ensure_model_parameters(reset=False):
    scene = bpy.context.scene
    body = projection_body()
    nx, nz = body["vp_grid"]
    scale = body.matrix_world.to_scale()
    distance = abs(body["vp_plane_origin"][1] - body["vp_lens_local"][1]) * scale.y
    sx, sz = body["vp_plane_span"]
    defaults = {
        "vp_fov_x": math.degrees(2 * math.atan(sx * scale.x * nx / (nx - 1) / (2 * distance))),
        "vp_fov_z": math.degrees(2 * math.atan(sz * scale.z * nz / (nz - 1) / (2 * distance))),
        "vp_samples_x": nx, "vp_samples_z": nz, "vp_layers": BUBBLE_DEPTH_LAYERS,
        "vp_radius": BUBBLE_RADIUS, "vp_freeze_points": False,
    }
    limits = {"vp_fov_x": (5.0, 140.0), "vp_fov_z": (5.0, 140.0),
              "vp_samples_x": (2, 64), "vp_samples_z": (2, 64), "vp_layers": (1, 32),
              "vp_radius": (0.001, 0.08)}
    for key, value in defaults.items():
        if reset or key not in scene:
            scene[key] = value
            if key in limits:
                low, high = limits[key]
                scene.id_properties_ui(key).update(min=low, max=high)
    roles = {role: next((o for o in scene.objects if o.get("vp_role") == role), None)
             for role in ("bubble", "image", "beam", "scatter")}
    for role in ("bubble", "image"):
        obj = roles[role]
        if obj:
            scene[f"vp_{role}_mesh"] = obj.data.name
            scene[f"vp_{role}_collection"] = obj.users_collection[0].name
    for role in ("beam", "scatter"):
        obj = roles[role]
        if obj:
            scene[f"vp_{role}_material"] = obj.data.materials[0].name
    # Use the colored ray material rather than the old coarse black guide.
    ray = next((o for o in scene.objects if o.get("vp_role") == "beam" and not o.get("vp_coarse")), None)
    if ray:
        scene["vp_beam_material"] = ray.data.materials[0].name
        scene["vp_rays_collection"] = ray.users_collection[0].name
    if not scene.get("vp_volume"):
        volume = next(o for o in scene.objects if o.name.startswith("Transparent_Glass_Cube") and o.type == "MESH")
        scene["vp_volume"] = volume.name
    if reset:
        scene["vp_model_applied"] = ""


def pixel_target_local(body, ix, iz):
    nx, nz = body["vp_grid"]
    origin = Vector(body["vp_plane_origin"])
    sx, sz = body["vp_plane_span"]
    return origin + Vector((sx * ix / (nx - 1), 0.0, sz * iz / (nz - 1)))


def ray_volume_interval(lens, direction, volume, margin):
    """Slab intersection in the glass object's local unit cube, t in world meters."""
    inverse = volume.matrix_world.inverted()
    origin = inverse @ lens
    ray = inverse.to_3x3() @ direction
    scale = volume.matrix_world.to_scale()
    near, far = 0.0, float("inf")
    for axis in range(3):
        extent = 0.5 - margin / abs(scale[axis])
        if extent <= 0:
            return None
        if abs(ray[axis]) < 1e-10:
            if abs(origin[axis]) > extent:
                return None
            continue
        a = (-extent - origin[axis]) / ray[axis]
        b = (extent - origin[axis]) / ray[axis]
        near, far = max(near, min(a, b)), min(far, max(a, b))
        if near >= far:
            return None
    return near, far


def rebuild_point_cloud():
    scene = bpy.context.scene
    body = projection_body()
    world = body.matrix_world.copy()
    lens = world @ Vector(body["vp_lens_local"])
    volume = scene.objects[scene["vp_volume"]]
    mesh_bubble = bpy.data.meshes[scene["vp_bubble_mesh"]]
    mesh_image = bpy.data.meshes[scene["vp_image_mesh"]]
    bubbles_collection = bpy.data.collections[scene["vp_bubble_collection"]]
    image_collection = bpy.data.collections[scene["vp_image_collection"]]
    rays_collection = bpy.data.collections[scene["vp_rays_collection"]]
    beam_material = bpy.data.materials[scene["vp_beam_material"]]
    scatter_material = bpy.data.materials[scene["vp_scatter_material"]]
    nx, nz = body["vp_grid"]
    layers = int(scene["vp_layers"])
    radius_limit = float(scene["vp_radius"])
    old_curves = []
    for obj in list(scene.objects):
        if obj.get("vp_generated") and (obj.get("vp_role") in {"bubble", "image", "beam", "scatter", "frustum", "writing"}
                                         or obj.name.startswith("Bubble_Depth_Layer_")):
            if obj.type == "CURVE":
                old_curves.append(obj.data)
            bpy.data.objects.remove(obj, do_unlink=True)
    for curve in old_curves:
        if curve.users == 0:
            bpy.data.curves.remove(curve)
    span_x, span_z = body["vp_plane_span"]
    scale = world.to_scale()
    distance = abs(body["vp_plane_origin"][1] - body["vp_lens_local"][1]) * scale.y
    pixel_w, pixel_h = span_x * scale.x / (nx - 1), span_z * scale.z / (nz - 1)
    ray_stride = max(1, math.ceil(nx * nz / BUBBLE_PATH_RAY_LIMIT))
    points = []
    for iz in range(nz):
        for ix in range(nx):
            local_target = pixel_target_local(body, ix, iz)
            target = world @ local_target
            direction = (target - lens).normalized()
            image = bpy.data.objects.new(f"Projected_Image_Pixel_{ix:02d}_{iz:02d}", mesh_image)
            image_collection.objects.link(image)
            image.scale = (pixel_w * 0.46, 0.004, pixel_h * 0.46)
            image["vp_role"] = "image"
            image["vp_target_local"] = list(local_target)
            image["pixel_x"], image["pixel_z"] = ix, iz
            image["vp_generated"] = True
            if (iz * nx + ix) % ray_stride == 0:
                beam = create_curve(f"RayAligned_Bubble_Path_{ix:02d}_{iz:02d}", (lens, target), beam_material, 0.0016, rays_collection)
                beam["vp_role"] = "beam"
                beam["vp_target_local"] = list(local_target)
                beam["pixel_x"], beam["pixel_z"] = ix, iz
                beam["vp_generated"] = True
            interval = ray_volume_interval(lens, direction, volume, radius_limit)
            if interval is None:
                continue
            group, slot = divmod(ix, layers)
            permutation = list(range(layers))
            random.Random(DEPTH_SEED + iz * 1009 + group * 9176).shuffle(permutation)
            fraction = (permutation[slot] + 0.5 + (stable_hash(ix, iz, 4) - 0.5) * 0.5) / layers
            near, far = interval
            travel = near + fraction * (far - near)
            position = lens + direction * travel
            angular_pitch = min(pixel_w, pixel_h) * distance / (target - lens).length_squared
            radius = min(radius_limit, 0.20 * travel * angular_pitch)
            bubble = bpy.data.objects.new(f"BubblePixel_{ix:02d}_{iz:02d}", mesh_bubble)
            bubbles_collection.objects.link(bubble)
            bubble.location = position
            bubble.scale = (radius,) * 3
            bubble["vp_role"] = "bubble"
            bubble["vp_generated"] = True
            bubble["pixel_x"], bubble["pixel_z"] = ix, iz
            bubble["depth_fraction"] = fraction
            bubble["vp_design_position"] = list(position)
            points.append(bubble)
    for index, bubble in enumerate(points[::max(1, math.ceil(len(points) / SCATTER_SAMPLE_LIMIT))]):
        start = bubble.location
        for axis, direction in enumerate(((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))):
            scatter = create_curve(f"Bubble_Scatter_{index:03d}_{axis}", (start, start + Vector(direction) * 0.18), scatter_material, 0.0016, rays_collection)
            scatter["vp_role"] = "scatter"
            scatter["vp_bubble"] = bubble.name
            scatter["vp_direction"] = direction
            scatter["vp_generated"] = True
    for layer in range(1, layers):
        y = 0.5 - layer / layers
        coords = [volume.matrix_world @ Vector((x, y, z)) for x, z in ((-.5, -.5), (.5, -.5), (.5, .5), (-.5, .5), (-.5, -.5))]
        guide = create_curve(f"Bubble_Depth_Layer_{layer:02d}", coords, beam_material, 0.001, rays_collection)
        guide["vp_generated"] = True
    body["vp_calibration_matrix"] = [v for row in world for v in row]
    scene["vp_density"] = len(points) / abs(volume.matrix_world.determinant())
    bpy.context.view_layer.update()
    log_step(f"参数化点云: {nx} x {nz}, {len(points)} 个点位于玻璃内部。")


def apply_model_parameters():
    scene = bpy.context.scene
    body = projection_body()
    if any(key not in scene for key in MODEL_KEYS):
        ensure_model_parameters()
    for key in ("vp_samples_x", "vp_samples_z"):
        scene[key] = max(2, min(64, int(scene[key])))
    scene["vp_layers"] = max(1, min(32, int(scene["vp_layers"])))
    for key in ("vp_fov_x", "vp_fov_z"):
        scene[key] = clamp(float(scene[key]), 5.0, 140.0)
    scene["vp_radius"] = clamp(float(scene["vp_radius"]), 0.001, 0.08)
    # Rebuild in design mode when the projector pose changes; fixed-glass mode
    # only resamples illumination and keeps the manufactured point positions.
    pose = [value for row in body.matrix_world for value in row]
    signature = json.dumps([scene[key] for key in MODEL_KEYS] +
                           [pose if not scene["vp_freeze_points"] else None])
    if signature == scene.get("vp_model_applied"):
        return
    geometry = json.dumps([scene[key] for key in MODEL_KEYS[2:6]])
    nx, nz = scene["vp_samples_x"], scene["vp_samples_z"]
    body["vp_grid"] = [nx, nz]
    scale = body.matrix_world.to_scale()
    lens = Vector(body["vp_lens_local"])
    y = body["vp_plane_origin"][1]
    distance = abs(y - lens.y) * abs(scale.y)
    sx = 2 * distance * math.tan(math.radians(scene["vp_fov_x"]) / 2) / abs(scale.x) * (nx - 1) / nx
    sz = 2 * distance * math.tan(math.radians(scene["vp_fov_z"]) / 2) / abs(scale.z) * (nz - 1) / nz
    body["vp_plane_span"] = [sx, sz]
    body["vp_plane_origin"] = [lens.x - sx / 2, y, lens.z - sz / 2]
    if not scene["vp_freeze_points"] or geometry != scene.get("vp_geometry_applied"):
        rebuild_point_cloud()
    else:
        for obj in scene.objects:
            if obj.get("vp_role") in {"beam", "image"} and "pixel_x" in obj:
                obj["vp_target_local"] = list(pixel_target_local(body, obj["pixel_x"], obj["pixel_z"]))
                if obj.get("vp_role") == "image":
                    obj.scale = (sx * abs(scale.x) / (nx - 1) * 0.46, 0.004, sz * abs(scale.z) / (nz - 1) * 0.46)
    scene["vp_geometry_applied"] = geometry
    scene["vp_model_applied"] = signature


def project_to_pixel(body, world_position):
    """Pinhole inverse projection; refraction and optical PSF are not modeled."""
    if abs(body.matrix_world.determinant()) < 1e-12:
        return None
    point = body.matrix_world.inverted() @ Vector(world_position)
    lens = Vector(body["vp_lens_local"])
    plane = Vector(body["vp_plane_origin"])
    delta = point - lens
    if delta.y >= -1e-7:
        return None
    hit = lens + delta * ((plane.y - lens.y) / delta.y)
    nx, nz = body["vp_grid"]
    sx, sz = body["vp_plane_span"]
    ix = (hit.x - plane.x) / sx * (nx - 1)
    iz = (hit.z - plane.z) / sz * (nz - 1)
    # A pixel occupies a finite cell centered on an integer coordinate.
    if not (-0.5 <= ix < nx - 0.5 and -0.5 <= iz < nz - 0.5):
        return None
    return ix, iz


def ensure_optical_settings(reset=False):
    scene = bpy.context.scene
    defaults = {"vp_optics": True, "vp_flux_w": 180.0, "vp_scatter_albedo": 0.65,
                "vp_show_receiver": True, "vp_source_image": ""}
    for key, value in defaults.items():
        if reset or key not in scene:
            scene[key] = value
    scene["vp_flux_w"] = max(0.0, float(scene["vp_flux_w"]))
    scene["vp_scatter_albedo"] = clamp(float(scene["vp_scatter_albedo"]), 0.0, 1.0)
    scene.id_properties_ui("vp_flux_w").update(min=0.0, max=5000.0)
    scene.id_properties_ui("vp_scatter_albedo").update(min=0.0, max=1.0)


def load_projection_image(filepath):
    path = Path(bpy.path.abspath(filepath)).resolve()
    if not path.is_file():
        raise ValueError(f"图像文件不存在: {path}")
    image = bpy.data.images.load(str(path), check_existing=False)
    if image.size[0] == 0 or image.size[1] == 0:
        bpy.data.images.remove(image)
        raise ValueError("无法读取图像像素")
    image.pack()
    bpy.context.scene["vp_source_image"] = image.name
    bpy.context.scene["vp_content"] = "IMAGE"
    update_projection()


def camera_only_material(name, strength):
    """Markers are visible to the camera but do not add light or occlude the rig."""
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    nodes.clear()
    info = nodes.new("ShaderNodeObjectInfo")
    emission = nodes.new("ShaderNodeEmission")
    emission.inputs["Strength"].default_value = strength
    transparent = nodes.new("ShaderNodeBsdfTransparent")
    path = nodes.new("ShaderNodeLightPath")
    mix = nodes.new("ShaderNodeMixShader")
    output = nodes.new("ShaderNodeOutputMaterial")
    links.new(info.outputs["Color"], emission.inputs["Color"])
    links.new(path.outputs["Is Camera Ray"], mix.inputs[0])
    links.new(transparent.outputs[0], mix.inputs[1])
    links.new(emission.outputs[0], mix.inputs[2])
    links.new(mix.outputs[0], output.inputs["Surface"])
    return mat


def create_optical_rig():
    scene = bpy.context.scene
    collection = ensure_collection("07_Optical_Simulation")
    light = bpy.data.lights.new("Projector_Image_Light", "SPOT")
    light.use_nodes = True
    light.shadow_soft_size = 0.0
    light.spot_blend = 0.0
    spot = bpy.data.objects.new("Projector_Image_Light", light)
    collection.objects.link(spot)
    spot["vp_generated"], spot["vp_role"] = True, "projector_light"
    spot.rotation_mode = "QUATERNION"
    nodes, links = light.node_tree.nodes, light.node_tree.links
    nodes.clear()
    coords = nodes.new("ShaderNodeTexCoord")
    separate = nodes.new("ShaderNodeSeparateXYZ")
    links.new(coords.outputs["Normal"], separate.inputs[0])
    absolute = nodes.new("ShaderNodeMath")
    absolute.operation = "ABSOLUTE"
    links.new(separate.outputs["Z"], absolute.inputs[0])
    combine = nodes.new("ShaderNodeCombineXYZ")
    for axis in ("X", "Y"):
        divide = nodes.new("ShaderNodeMath")
        divide.operation = "DIVIDE"
        links.new(separate.outputs[axis], divide.inputs[0])
        links.new(absolute.outputs[0], divide.inputs[1])
        scale = nodes.new("ShaderNodeMath")
        scale.operation = "MULTIPLY_ADD"
        scale.name = f"FOV_{axis}"
        scale.inputs[2].default_value = 0.5
        links.new(divide.outputs[0], scale.inputs[0])
        links.new(scale.outputs[0], combine.inputs[axis])
    texture = nodes.new("ShaderNodeTexImage")
    texture.name = "ProjectionImage"
    texture.extension = "CLIP"
    texture.interpolation = "Closest"
    links.new(combine.outputs[0], texture.inputs["Vector"])
    emission = nodes.new("ShaderNodeEmission")
    emission.inputs["Strength"].default_value = 1.0
    alpha = nodes.new("ShaderNodeVectorMath")
    alpha.operation = "SCALE"
    links.new(texture.outputs["Color"], alpha.inputs[0])
    links.new(texture.outputs["Alpha"], alpha.inputs["Scale"])
    links.new(alpha.outputs[0], emission.inputs["Color"])
    output = nodes.new("ShaderNodeOutputLight")
    links.new(emission.outputs[0], output.inputs["Surface"])
    volume = scene.objects[scene["vp_volume"]]
    receiver = add_cube("Projection_Receiver", (volume.location.x, volume.location.y - volume.dimensions.y / 2 - 0.6, volume.location.z),
                        (4.0, 0.06, 2.8), create_simple_material("Mat_Receiver_Matte", (0.8, 0.8, 0.8, 1), 1.0), collection)
    receiver["vp_generated"], receiver["vp_role"] = True, "receiver"
    scene["vp_spot"], scene["vp_receiver"] = spot.name, receiver.name
    scene["vp_optical_collection"] = collection.name
    scene["vp_optical_marker_material"] = camera_only_material("Mat_Optical_Markers", 2.0).name
    scene["vp_optical_ray_material"] = camera_only_material("Mat_Colored_Optical_Rays", 1.0).name


def update_optical_projection(frame):
    scene = bpy.context.scene
    ensure_optical_settings()
    if scene.objects.get(scene.get("vp_spot", "")) is None:
        create_optical_rig()
    body = projection_body()
    spot = scene.objects[scene["vp_spot"]]
    receiver = scene.objects.get(scene.get("vp_receiver", ""))
    collection = bpy.data.collections[scene["vp_optical_collection"]]
    enabled = bool(scene["vp_optics"])
    if enabled and scene.render.engine != "CYCLES":
        scene.render.engine = "CYCLES"
    power = max(0.0, float(body.get("power", 1)))
    flux = max(0.0, float(scene["vp_flux_w"])) * power if enabled else 0.0
    albedo = clamp(float(scene["vp_scatter_albedo"]), 0, 1)
    lens = body.matrix_world @ Vector(body["vp_lens_local"])
    rotation = body.matrix_world.to_quaternion()
    spot.location = lens
    # Preserve projector roll as well as its optical axis.
    spot.rotation_quaternion = rotation @ Vector((0, -1, 0)).to_track_quat("-Z", "Y")
    tan_x = math.tan(math.radians(scene["vp_fov_x"]) / 2)
    tan_z = math.tan(math.radians(scene["vp_fov_z"]) / 2)
    spot.data.spot_size = min(math.pi, 2 * math.atan(math.hypot(tan_x, tan_z)) + 0.01)
    spot.data.energy = flux
    nodes = spot.data.node_tree.nodes
    nodes["FOV_X"].inputs[1].default_value = 1 / (2 * tan_x)
    nodes["FOV_Y"].inputs[1].default_value = 1 / (2 * tan_z)
    nx, nz = body["vp_grid"]
    source = bpy.data.images.get(scene.get("vp_source_image", "")) if scene.get("vp_content") == "IMAGE" else None
    if source is None:
        source = bpy.data.images.get(scene.get("vp_frame_image", ""))
        if source is None:
            source = bpy.data.images.new("Projection_Frame", width=nx, height=nz, float_buffer=True)
            scene["vp_frame_image"] = source.name
        if tuple(source.size) != (nx, nz):
            source.scale(nx, nz)
        source.pixels = [c for iz in range(nz) for ix in range(nx) for c in frame[ix, iz]]
        source.update()
    nodes["ProjectionImage"].image = source
    if receiver:
        set_visibility(receiver, scene["vp_show_receiver"])
    # Glass is a visual envelope only in this straight-ray approximation.
    volume = scene.objects[scene["vp_volume"]]
    volume.visible_shadow = False
    for obj in scene.objects:
        if obj.get("vp_generated") and obj.name.startswith("Projector_Lens"):
            obj.visible_shadow = False
    bubbles = {o.name: o for o in scene.objects if o.get("vp_role") == "bubble"}
    sources = {o.get("vp_bubble"): o for o in scene.objects if o.get("vp_role") == "scatter_light"}
    for name, light in list(sources.items()):
        if name not in bubbles:
            data = light.data
            bpy.data.objects.remove(light, do_unlink=True)
            if data.users == 0:
                bpy.data.lights.remove(data)
    material = bpy.data.materials[scene["vp_optical_marker_material"]]
    for name, bubble in bubbles.items():
        light = sources.get(name)
        if light is None:
            light = bpy.data.objects.new(f"Scatter_{name}", bpy.data.lights.new(f"Scatter_{name}", "POINT"))
            collection.objects.link(light)
            light["vp_generated"], light["vp_role"], light["vp_bubble"] = True, "scatter_light", name
        light.location = bubble.matrix_world.translation
        light.data.shadow_soft_size = max(bubble.scale)
        color = tuple(bubble["vp_incident_color"])
        distance = max((light.location - lens).length, 0.01)
        pixel_area = 4 * distance * distance * tan_x * tan_z / (nx * nz)
        intercepted = min(1.0, math.pi * max(bubble.scale) ** 2 / max(pixel_area, 1e-9))
        intensity = max(color[:3])
        light.data.energy = flux / (nx * nz) * intercepted * albedo * intensity
        light.data.color = tuple(c / intensity for c in color[:3]) if intensity > 0 else (0, 0, 0)
        bubble["vp_scattered_w"] = light.data.energy
        display_scale = power * albedo * (scene["vp_flux_w"] / 180.0) if enabled else power
        bubble.color = tuple(c * display_scale for c in color[:3]) + (1.0,)
        if bubble.data.materials[0] != material:
            bubble.data.materials[0] = material
        bubble.visible_shadow = False
    ray_material = bpy.data.materials[scene["vp_optical_ray_material"]]
    for obj in scene.objects:
        if obj.get("vp_role") in {"beam", "scatter", "image"}:
            if obj.data.materials[0] != ray_material:
                obj.data.materials[0] = ray_material
            obj.visible_shadow = False
    scene["vp_scattered_total_w"] = sum(o.data.energy for o in scene.objects if o.get("vp_role") == "scatter_light")
    bpy.context.view_layer.update()


def projection_image_data(scene):
    source = bpy.data.images.get(scene.get("vp_source_image", "")) if scene.get("vp_content") == "IMAGE" else None
    if source and source.size[0] > 0 and source.size[1] > 0:
        return source.size[0], source.size[1], list(source.pixels)
    return None


def sample_projection_color(body, sample, frame, image_data):
    if sample is None:
        return (0.0, 0.0, 0.0, 1.0)
    if image_data is not None:
        width, height, pixels = image_data
        nx, nz = body["vp_grid"]
        # Convert the continuous ray coordinate to UV before sampling the original
        # image, matching the spotlight's Closest filter without grid quantization.
        x = max(0, min(width - 1, math.floor((sample[0] + 0.5) / nx * width)))
        y = max(0, min(height - 1, math.floor((sample[1] + 0.5) / nz * height)))
        offset = (y * width + x) * 4
        rgba = pixels[offset:offset + 4]
        return tuple(c * rgba[3] for c in rgba[:3]) + (1.0,)
    return frame.get(tuple(math.floor(v + 0.5) for v in sample), (0.0, 0.0, 0.0, 1.0))


def projection_frame(scene, body, image_data=None):
    nx, nz = body["vp_grid"]
    mode = scene.get("vp_content", "TEST")
    image_data = image_data if image_data is not None else projection_image_data(scene)
    depths = {(obj["pixel_x"], obj["pixel_z"]): obj["depth_fraction"]
              for obj in scene.objects if obj.get("vp_role") == "bubble"}
    frame = {}
    for ix in range(nx):
        for iz in range(nz):
            if image_data is not None:
                color = sample_projection_color(body, (ix, iz), frame, image_data)
            elif mode == "COLOR_BARS":
                colors = ((1, 0, 0), (0, 1, 0), (0, 0, 1), (0, 1, 1), (1, 0, 1), (1, 1, 0))
                color = colors[min(5, ix * 6 // nx)] + (1.0,)
            elif mode == "WHITE":
                color = (1.0, 1.0, 1.0, 1.0)
            elif mode == "DEPTH":
                depth = depths.get((ix, iz), 0.5)
                color = (depth, 0.2 + 0.7 * (1 - abs(2 * depth - 1)), 1 - depth, 1.0)
            else:
                active, rgb, brightness = pattern_pixel(ix, iz, nx, nz)
                color = tuple(channel * brightness if active else 0.0 for channel in rgb[:3]) + (1.0,)
            frame[ix, iz] = color
    return frame


def set_visibility(obj, visible):
    obj.hide_render = not visible
    obj.hide_set(not visible)


def update_projection():
    scene = bpy.context.scene
    body = projection_body()
    if body is None or "vp_lens_local" not in body:
        return
    bpy.context.view_layer.update()
    apply_model_parameters()
    world = body.matrix_world
    lens = world @ Vector(body["vp_lens_local"])
    image_data = projection_image_data(scene)
    frame = projection_frame(scene, body, image_data)
    lit_count = 0
    power = max(0.0, float(body.get("power", 1.0)))
    for obj in scene.objects:
        role = obj.get("vp_role")
        if role == "bubble":
            sample = project_to_pixel(body, obj.matrix_world.translation)
            color = sample_projection_color(body, sample, frame, image_data)
            obj["vp_incident_color"] = color
            obj.color = tuple(channel * power for channel in color[:3]) + (1.0,)
            obj["vp_lit"] = power > 0 and max(color[:3]) > 0.001
            lit_count += int(obj["vp_lit"])
        elif role in {"beam", "image"}:
            target = world @ Vector(obj["vp_target_local"])
            if role == "beam":
                points = obj.data.splines[0].points
                points[0].co = (*lens, 1.0)
                points[-1].co = (*target, 1.0)
                signal = frame.get((obj.get("pixel_x", -1), obj.get("pixel_z", -1)), (1, 1, 1, 1))
                obj.color = tuple(c * power for c in signal[:3]) + (1.0,)
                visible = scene.get("vp_show_rays", True) and not obj.get("vp_coarse", False)
                set_visibility(obj, visible and power > 0 and max(signal[:3]) > 0.001)
            else:
                obj.location = target
                obj.rotation_euler = body.rotation_euler
                color = frame[obj["pixel_x"], obj["pixel_z"]]
                obj.color = tuple(channel * power for channel in color[:3]) + (1.0,)
                set_visibility(obj, scene.get("vp_show_image", False))
        elif role == "frustum":
            coordinates = obj["vp_vertices_local"]
            for i, vertex in enumerate(obj.data.vertices):
                vertex.co = world @ Vector(coordinates[i * 3:i * 3 + 3])
            obj.data.update()
            set_visibility(obj, False)
        elif role == "writing":
            set_visibility(obj, False)
        elif obj.name.startswith("Bubble_Depth_Layer_"):
            set_visibility(obj, scene.get("vp_show_layers", False))
        elif obj.name.startswith(("Incoming_Light_Entry_Face", "Projected_Image_Exit_Face")):
            set_visibility(obj, False)
    update_optical_projection(frame)
    for obj in scene.objects:
        if obj.get("vp_role") == "scatter":
            bubble = scene.objects.get(obj.get("vp_bubble", ""))
            if bubble:
                start = bubble.matrix_world.translation
                direction = Vector(obj.get("vp_direction", (0, 0, 1)))
                obj.data.splines[0].points[0].co = (*start, 1)
                obj.data.splines[0].points[-1].co = (*(start + direction * 0.18), 1)
                obj.color = bubble.color
            set_visibility(obj, bool(bubble and bubble.get("vp_lit") and scene.get("vp_show_scatter", False)
                                     and scene.get("vp_scatter_albedo", 0) > 0))
    scene["vp_lit_count"] = lit_count
    scene["vp_total_count"] = sum(obj.get("vp_role") == "bubble" for obj in scene.objects)


def restore_calibration():
    body = projection_body()
    values = body["vp_calibration_matrix"]
    body.matrix_world = Matrix([values[i:i + 4] for i in range(0, 16, 4)])
    update_projection()


def export_projection(output_dir=OUTPUT_DIR):
    update_projection()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    scene = bpy.context.scene
    body = projection_body()
    with (output_dir / "bubble_coordinates.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream)
        writer.writerow(("name", "pixel_x_calibrated", "pixel_z_calibrated", "depth_fraction",
                         "x_m", "y_m", "z_m", "pixel_x_current", "pixel_z_current", "lit", "scattered_w_approx",
                         "incident_r", "incident_g", "incident_b"))
        for bubble in sorted((o for o in scene.objects if o.get("vp_role") == "bubble"), key=lambda o: (o["pixel_x"], o["pixel_z"])):
            position = bubble.matrix_world.translation
            sample = project_to_pixel(body, position) or ("", "")
            writer.writerow((bubble.name, bubble["pixel_x"], bubble["pixel_z"], bubble["depth_fraction"],
                             *position, *sample, int(bubble["vp_lit"]), bubble.get("vp_scattered_w", 0.0),
                             *list(bubble["vp_incident_color"])[:3]))
    nx, nz = body["vp_grid"]
    frame = projection_frame(scene, body)
    image = bpy.data.images.new("Projection_Input_Export", width=nx, height=nz, alpha=True)
    try:
        image.pixels = [channel for iz in range(nz) for ix in range(nx) for channel in frame[ix, iz]]
        image.filepath_raw = str(output_dir / "projector_input.png")
        image.file_format = "PNG"
        image.save()
    finally:
        bpy.data.images.remove(image)
    metadata = {"model": "pinhole_with_isotropic_secondary_sources", "engine": scene.render.engine,
                "parameters": {key: scene.get(key) for key in MODEL_KEYS + OPTICAL_KEYS},
                "content": scene.get("vp_content"), "power": body.get("power"),
                "projector_matrix_world": [list(row) for row in body.matrix_world],
                "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "limitations": "No refraction, incident occlusion, multiple scattering, or primary-beam depletion."}
    (output_dir / "projection_settings.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    log_step(f"已导出气泡坐标及投影输入图: {output_dir}")


def live_projection_tick():
    if bpy.app.is_job_running("RENDER") or bpy.context.mode != "OBJECT":
        return 0.2
    scene = bpy.context.scene
    body = projection_body()
    if body is None or not scene.get("vp_auto", True):
        return 0.2
    runtime = getattr(bpy, "_volume_projection_runtime", {})
    pose = tuple(value for row in body.matrix_world for value in row)
    parameters = (scene.as_pointer(), pose) + tuple(scene.get(key) for key in MODEL_KEYS)
    if parameters != runtime.get("pending_parameters"):
        runtime["pending_parameters"] = parameters
        runtime["parameters_changed_at"] = time.monotonic()
        return 0.1
    if time.monotonic() - runtime.get("parameters_changed_at", 0) < 0.35:
        return 0.1
    signature = (scene.as_pointer(), tuple(value for row in body.matrix_world for value in row),
                 parameters,
                 body.get("power", 1.0), scene.get("vp_content"),
                 tuple(scene.get(key) for key in OPTICAL_KEYS),
                 tuple(scene.get(key) for key in ("vp_show_rays", "vp_show_scatter", "vp_show_image", "vp_show_layers")),
                 tuple(tuple(o.matrix_world.translation) for o in scene.objects if o.get("vp_role") == "bubble"))
    if signature != runtime.get("signature"):
        try:
            update_projection()
            runtime["signature"] = signature
        except Exception as exc:
            scene["vp_auto"] = False
            log_step(f"自动更新暂停: {exc}")
    return 0.15


class VP_OT_update(bpy.types.Operator):
    bl_idname = "vp.update"
    bl_label = "更新投影"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        update_projection()
        return {"FINISHED"}


class VP_OT_restore(bpy.types.Operator):
    bl_idname = "vp.restore"
    bl_label = "恢复标定位置"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        restore_calibration()
        return {"FINISHED"}


class VP_OT_content(bpy.types.Operator):
    bl_idname = "vp.content"
    bl_label = "投影内容"
    bl_options = {"REGISTER", "UNDO"}
    mode: bpy.props.EnumProperty(items=[("TEST", "测试图", ""), ("COLOR_BARS", "彩条", ""), ("WHITE", "全白", ""), ("DEPTH", "深度编码", ""), ("IMAGE", "图片", "")])

    def execute(self, context):
        if self.mode == "IMAGE" and bpy.data.images.get(context.scene.get("vp_source_image", "")) is None:
            self.report({"WARNING"}, "请先加载投影图片")
            return {"CANCELLED"}
        context.scene["vp_content"] = self.mode
        update_projection()
        return {"FINISHED"}


class VP_OT_export(bpy.types.Operator):
    bl_idname = "vp.export"
    bl_label = "导出坐标和投影图"

    def execute(self, context):
        try:
            export_projection()
        except OSError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, str(OUTPUT_DIR))
        return {"FINISHED"}


class VP_OT_load_image(bpy.types.Operator):
    bl_idname = "vp.load_image"
    bl_label = "加载投影图片"
    filepath: bpy.props.StringProperty(subtype="FILE_PATH")
    filter_glob: bpy.props.StringProperty(default="*.png;*.jpg;*.jpeg;*.bmp;*.tif;*.tiff;*.exr", options={"HIDDEN"})

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        try:
            load_projection_image(self.filepath)
        except (ValueError, RuntimeError, OSError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class VP_PT_controls(bpy.types.Panel):
    bl_label = "体积投影"
    bl_idname = "VP_PT_controls"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "体积投影"

    @classmethod
    def poll(cls, context):
        return projection_body() is not None

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        body = projection_body()
        layout.prop(body, "location", text="位置")
        layout.prop(body, "rotation_euler", text="旋转")
        layout.separator()
        layout.prop(scene, '["vp_fov_x"]', text="水平 FOV (°)")
        layout.prop(scene, '["vp_fov_z"]', text="垂直 FOV (°)")
        layout.prop(scene, '["vp_samples_x"]', text="横向点数")
        layout.prop(scene, '["vp_samples_z"]', text="纵向点数")
        layout.prop(scene, '["vp_layers"]', text="深度层数")
        layout.prop(scene, '["vp_radius"]', text="半径上限 (m)")
        layout.prop(scene, '["vp_freeze_points"]', text="固定气泡位置")
        layout.label(text=f"实际密度 {scene.get('vp_density', 0):.1f} 点/m³")
        layout.separator()
        layout.prop(body, '["power"]', text="相对强度")
        layout.prop(scene, '["vp_auto"]', text="自动更新")
        layout.operator_menu_enum("vp.content", "mode", text={"TEST": "测试图", "COLOR_BARS": "彩条", "WHITE": "全白", "DEPTH": "深度编码", "IMAGE": "图片"}.get(scene.get("vp_content"), "投影内容"))
        layout.operator("vp.load_image", icon="IMAGE_DATA")
        if scene.get("vp_source_image"):
            layout.label(text=scene["vp_source_image"], icon="IMAGE_DATA")
        layout.prop(scene, '["vp_optics"]', text="图像光照 / Cycles")
        layout.prop(scene, '["vp_flux_w"]', text="光源功率 (W)")
        layout.prop(scene, '["vp_scatter_albedo"]', text="散射比例")
        layout.prop(scene, '["vp_show_receiver"]', text="接收屏")
        layout.label(text=f"照亮 {scene.get('vp_lit_count', 0)} / {scene.get('vp_total_count', 0)}")
        for key, label in (("vp_show_rays", "像素光线"), ("vp_show_scatter", "散射示意"),
                           ("vp_show_image", "参考图像平面"), ("vp_show_layers", "深度层")):
            layout.prop(scene, f'["{key}"]', text=label)
        layout.operator("vp.update", icon="FILE_REFRESH")
        layout.operator("vp.restore", icon="LOOP_BACK")
        layout.operator("vp.export", icon="EXPORT")


def register_projection_controls():
    runtime = getattr(bpy, "_volume_projection_runtime", {})
    callback = runtime.get("timer")
    if callback and bpy.app.timers.is_registered(callback):
        bpy.app.timers.unregister(callback)
    for cls in reversed(runtime.get("classes", [])):
        bpy.utils.unregister_class(cls)
    classes = (VP_OT_update, VP_OT_restore, VP_OT_content, VP_OT_export, VP_OT_load_image, VP_PT_controls)
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy._volume_projection_runtime = {"timer": live_projection_tick, "classes": classes}
    if not bpy.app.background:
        bpy.app.timers.register(live_projection_tick, first_interval=0.15, persistent=True)


def select_projector_for_manual_adjustment(scene_data: Dict[str, object]) -> None:
    projector_obj = projection_body()
    if projector_obj is None:
        return

    bpy.ops.object.select_all(action="DESELECT")
    projector_obj.select_set(True)
    bpy.context.view_layer.objects.active = projector_obj

    projector_obj["manual_adjustment_note"] = (
        "Move or rotate this body to regenerate ray-aligned points after a short pause. "
        "Enable fixed points in the Volume Projection sidebar for illumination-only updates. "
        "No refraction model."
    )

    if bpy.app.background:
        log_step("--keep-open 需要 GUI 模式；当前是 --background，Blender 会在脚本结束后退出。")
        return

    for area in bpy.context.screen.areas:
        if area.type != "VIEW_3D":
            continue
        region = next((region for region in area.regions if region.type == "WINDOW"), None)
        space = next((space for space in area.spaces if space.type == "VIEW_3D"), None)
        if region is None or space is None:
            continue
        override = {
            "window": bpy.context.window,
            "screen": bpy.context.screen,
            "area": area,
            "region": region,
            "space_data": space,
        }
        try:
            with bpy.context.temp_override(**override):
                space.region_3d.view_perspective = "CAMERA"
                space.region_3d.view_camera_zoom = 0
                space.shading.type = "RENDERED"
                space.show_region_ui = True
        except Exception:
            pass
        break

    log_step("生成完成：Blender 窗口会保留，已选中 Projector_Body，可手动调整投影仪位置。")

def get_script_args() -> List[str]:
    if "--" not in sys.argv:
        return []
    return sys.argv[sys.argv.index("--") + 1:]


def render_preview(output_path: Path = PREVIEW_IMAGE_PATH) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    update_projection()

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
    text = bpy.data.texts.get("VolumeProjection_Controls.py") or bpy.data.texts.new("VolumeProjection_Controls.py")
    text.clear()
    text.write(Path(__file__).read_text(encoding="utf-8"))
    text.filepath = str(Path(__file__).resolve())
    text.use_module = False
    wm = bpy.context.window_manager
    running = wm.get(RUNNING_FLAG_KEY, False)
    wm[RUNNING_FLAG_KEY] = False
    try:
        bpy.ops.wm.save_as_mainfile(filepath=str(output_path))
    finally:
        wm[RUNNING_FLAG_KEY] = running
    return output_path


def main() -> None:
    window_manager = bpy.context.window_manager
    if window_manager.get(RUNNING_FLAG_KEY):
        log_step("检测到脚本已经在运行，本次重复启动已跳过。")
        return

    args = get_script_args()
    window_manager[RUNNING_FLAG_KEY] = True
    try:
        if bpy.context.scene.get("vp_version") == SCENE_VERSION and projection_body() and "--rebuild" not in args:
            scene_data = {}
            log_step("保留已有场景和投影仪位置，重新连接投影控制。")
            update_projection()
        else:
            scene_data = build_scene()
        register_projection_controls()
        if "--keep-open" in args:
            select_projector_for_manual_adjustment(scene_data)
        if "--export-data" in args:
            export_projection()
        if "--render-preview" in args:
            render_preview()
        if "--save-blend" in args:
            save_blend_file()
    finally:
        window_manager[RUNNING_FLAG_KEY] = False


if __name__ in {"__main__", "<run_path>"}:
    main()
