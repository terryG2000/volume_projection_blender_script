"""Generate a standalone editable projection demo; GUI stays open.

blender --python demo_rotating_cube.py
blender --background --python demo_rotating_cube.py -- --render-preview
"""
import importlib.util
from pathlib import Path
import sys

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("cube_projection_demo", ROOT / "projector.py")
projection = importlib.util.module_from_spec(spec)
spec.loader.exec_module(projection)
animation = projection.animation_module()

source, cube = animation.create_rotating_cube()
animation.export_animation(ROOT / "outputs" / "rotating_cube.pointcloud.json", source, [cube], 1200)
data = projection.build_scene()
scene = bpy.context.scene
scene["vp_samples_x"], scene["vp_samples_z"] = 32, 24
scene["vp_hide_ray_geometry"] = True
projection.update_projection()
projection.register_projection_controls()
projection.load_point_animation(str(ROOT / "outputs" / "rotating_cube.pointcloud.json"))
scene["vp_animation_radius"] = 0.18
projection.update_projection()
scene.camera.location = (7.5, 8.0, 6.5)
projection.look_at(scene.camera, Vector((-1.4, -1.8, 1.75)))
scene.camera.data.lens = 40
scene.render.use_lock_interface = True
if not bpy.app.background:
    projection.select_projector_for_manual_adjustment(data)
projection.save_blend_file(ROOT / "outputs" / "rotating_cube_comparison.blend")
if "--render-preview" in sys.argv:
    for frame in (1, 13):
        scene.frame_set(frame)
        projection.render_preview(ROOT / "outputs" / f"rotating_cube_comparison_frame_{frame:02}.png")
    scene.frame_set(1)
if not bpy.app.background:
    bpy.ops.screen.animation_play()
