"""Blender evaluated-mesh animation interchange, independent of the projector rig."""
import bisect
import hashlib
import json
import math
from pathlib import Path
import random
import subprocess

import bpy
from mathutils import Vector

FORMAT = "volume_projection_point_animation"
VERSION = 1
MAX_BYTES = 64 * 1024 * 1024
MAX_POINTS = 500000


def source_revision():
    root = Path(__file__).resolve().parent
    command = ["git", "-c", f"safe.directory={root.as_posix()}", "-C", str(root)]
    try:
        revision = subprocess.run(command + ["rev-parse", "HEAD"], capture_output=True,
                                  text=True, check=True, timeout=5).stdout.strip()
        dirty = subprocess.run(command + ["diff", "--quiet", "HEAD", "--", "projector.py",
                               "point_cloud_animation.py", "demo_rotating_cube.py"], timeout=5).returncode
        return {"git_commit": revision, "source_modified": dirty != 0}
    except (OSError, subprocess.SubprocessError):
        return {"git_commit": "unavailable", "source_modified": True}


def validate(data):
    if not isinstance(data, dict) or data.get("format") != FORMAT or data.get("version") != VERSION:
        raise ValueError("Unsupported point animation format/version")
    if data.get("coordinates") != "normalized_isotropic" or data.get("color_space") != "linear_rgb":
        raise ValueError("Expected normalized_isotropic coordinates and linear_rgb")
    fps = data.get("fps")
    if not isinstance(fps, (int, float)) or isinstance(fps, bool) or not math.isfinite(fps) or not 0 < fps <= 240:
        raise ValueError("FPS must be in (0, 240]")
    frames = data.get("frames")
    if not isinstance(frames, list) or not 1 <= len(frames) <= 2000:
        raise ValueError("Expected 1..2000 frames")
    count = 0
    for frame in frames:
        if not isinstance(frame, dict) or not isinstance(frame.get("points"), list):
            raise ValueError("Each frame needs a points array")
        count += len(frame["points"])
        if count > MAX_POINTS:
            raise ValueError("Animation exceeds 500000 total points")
        for point in frame["points"]:
            if not isinstance(point, list) or len(point) != 6:
                raise ValueError("Each point must be [x,y,z,r,g,b]")
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in point):
                raise ValueError("Point components must be finite numbers")
            if any(abs(v) > 1.000001 for v in point[:3]) or any(not 0 <= v <= 1 for v in point[3:]):
                raise ValueError("Coordinates must be in [-1,1]; RGB in [0,1]")
    return data


def read(filepath):
    path = Path(filepath)
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("Animation JSON exceeds 64 MiB")
    return validate(json.loads(path.read_text(encoding="utf-8")))


def sample_surface(objects, depsgraph, count, seed=2006):
    triangles, cumulative = [], []
    total = 0.0
    for obj in objects:
        evaluated = obj.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        try:
            mesh.calc_loop_triangles()
            for tri in mesh.loop_triangles:
                vertices = [evaluated.matrix_world @ mesh.vertices[i].co for i in tri.vertices]
                area = (vertices[1] - vertices[0]).cross(vertices[2] - vertices[0]).length / 2
                if area <= 1e-12:
                    continue
                material = mesh.materials[tri.material_index] if tri.material_index < len(mesh.materials) else None
                color = tuple(material.diffuse_color[:3]) if material else tuple(obj.color[:3])
                triangles.append((vertices, color))
                total += area
                cumulative.append(total)
        finally:
            evaluated.to_mesh_clear()
    if not triangles:
        return []
    rng = random.Random(seed)
    points = []
    for _ in range(count):
        vertices, color = triangles[bisect.bisect_left(cumulative, rng.random() * total)]
        u, v = math.sqrt(rng.random()), rng.random()
        position = (1 - u) * vertices[0] + u * (1 - v) * vertices[1] + u * v * vertices[2]
        points.append([*position, *(max(0.0, min(1.0, c)) for c in color)])
    return points


def export_animation(filepath, scene, objects, samples=2048):
    """Sample selected evaluated meshes; preserve the source frame even on error."""
    objects = [o for o in objects if o.type == "MESH" and o.name in scene.objects]
    if not objects:
        raise ValueError("Select at least one mesh in the source scene")
    count = scene.frame_end - scene.frame_start + 1
    if not 1 <= count <= 2000 or not 1 <= samples <= 20000 or count * samples > MAX_POINTS:
        raise ValueError("Use 1..2000 frames, 1..20000 samples, <=500000 total points")
    old_frame, subframe = scene.frame_current, scene.frame_subframe
    frames = []
    low, high = Vector((math.inf,) * 3), Vector((-math.inf,) * 3)
    try:
        with bpy.context.temp_override(scene=scene, view_layer=scene.view_layers[0]):
            for number in range(scene.frame_start, scene.frame_end + 1):
                scene.frame_set(number)
                depsgraph = bpy.context.evaluated_depsgraph_get()
                points = sample_surface(objects, depsgraph, samples)
                for point in points:
                    for axis in range(3):
                        low[axis] = min(low[axis], point[axis])
                        high[axis] = max(high[axis], point[axis])
                frames.append({"source_frame": number, "points": points})
    finally:
        scene.frame_set(old_frame, subframe=subframe)
    if not math.isfinite(low.x):
        raise ValueError("No non-degenerate mesh surfaces in the animation")
    center = (low + high) / 2
    extent = max(high - low)
    # One transform for the entire clip preserves motion and relative size.
    scale = 1.6 / extent
    for frame in frames:
        for point in frame["points"]:
            point[:3] = [round((point[i] - center[i]) * scale, 6) for i in range(3)]
            point[3:] = [round(v, 6) for v in point[3:]]
    data = {"format": FORMAT, "version": VERSION, "coordinates": "normalized_isotropic",
            "color_space": "linear_rgb", "fps": scene.render.fps / scene.render.fps_base,
            "frames": frames, "source": {"blend": bpy.data.filepath, "scene": scene.name,
            "objects": [o.name for o in objects], "center_blender_units": list(center),
            "normalization_scale": scale, "samples_per_frame": samples,
            "exporter_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "color_method": "material.diffuse_color or object.color; no shader/texture baking",
            **source_revision()}}
    validate(data)
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, separators=(",", ":"), allow_nan=False), encoding="utf-8")
    return data


def create_rotating_cube():
    """Create an isolated source scene, without replacing the user's active scene."""
    scene = bpy.data.scenes.new("RotatingCubeSource")
    mesh = bpy.data.meshes.new("RotatingCubeMesh")
    vertices = [(x, y, z) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]
    mesh.from_pydata(vertices, [], [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1),
                                  (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)])
    obj = bpy.data.objects.new("AnimatedColorCube", mesh)
    scene.collection.objects.link(obj)
    for index, color in enumerate(((1, .05, .02, 1), (.02, 1, .08, 1), (.05, .15, 1, 1),
                                    (1, .7, .02, 1), (.8, .03, 1, 1), (.02, 1, 1, 1))):
        material = bpy.data.materials.new(f"CubeFace{index}")
        material.diffuse_color = color
        mesh.materials.append(material)
        mesh.polygons[index].material_index = index
    scene.frame_start, scene.frame_end, scene.render.fps = 1, 48, 24
    # Explicit keys avoid dependence on Blender's action-slot API for interpolation.
    for frame in range(1, 49):
        obj.rotation_euler = (.32, .23, 2 * math.pi * (frame - 1) / 48)
        obj.keyframe_insert(data_path="rotation_euler", frame=frame)
    scene.frame_set(1)
    return scene, obj
