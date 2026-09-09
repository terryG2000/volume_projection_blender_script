"""Render checks: projected color, secondary illumination, and non-reflective glass."""
import importlib.util
import math
from pathlib import Path

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("optical_render_test", ROOT / "projector.py")
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)
p.build_scene()
scene = bpy.context.scene
body = p.projection_body()
source = bpy.data.images.new("Render_Quadrants", width=2, height=2, float_buffer=True)
source.pixels = [1, 0, 0, 1, 0, 1, 0, 1, 0, 0, 1, 1, 1, 1, 1, 1]
scene["vp_source_image"] = source.name
scene["vp_content"] = "IMAGE"
scene["vp_scatter_albedo"] = 0.0
scene["vp_flux_w"] = 1500.0
p.update_projection()
spot = scene.objects[scene["vp_spot"]]
for obj in scene.objects:
    if obj.type == "LIGHT" and obj != spot and obj.get("vp_role") != "scatter_light":
        obj.data.energy = 0
    if obj.type not in {"LIGHT", "CAMERA"} and obj.get("vp_role") != "receiver":
        obj.hide_render = True
scene.world.use_nodes = True
scene.world.node_tree.nodes.get("Background").inputs["Strength"].default_value = 0
scene.camera.location = (0, -0.5, 1.85)
p.look_at(scene.camera, Vector((0, -4, 1.85)))
scene.camera.data.type = "ORTHO"
scene.camera.data.ortho_scale = 1.4
scene.render.resolution_x = scene.render.resolution_y = 64
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = "PNG"
scene.render.image_settings.color_depth = "16"
scene.view_settings.view_transform = "Standard"
scene.view_settings.look = "None"
scene.cycles.samples = 16
scene.cycles.use_denoising = False
out = ROOT / "tmp" / "optical_render"
out.mkdir(parents=True, exist_ok=True)


def render(name):
    scene.render.filepath = str(out / f"{name}.png")
    bpy.ops.render.render(write_still=True)
    image = bpy.data.images.load(scene.render.filepath, check_existing=False)
    pixels = list(image.pixels)
    bpy.data.images.remove(image)
    return pixels


def patch_rgb(pixels, x, y):
    return tuple(sum(pixels[(j * 64 + i) * 4 + c] for j in range(y - 3, y + 4)
                     for i in range(x - 3, x + 4)) / 49 for c in range(3))


on = render("quadrants")
quadrants = [patch_rgb(on, 16, 16), patch_rgb(on, 48, 16), patch_rgb(on, 16, 48), patch_rgb(on, 48, 48)]
print("RENDER_QUADRANTS", quadrants, flush=True)
for rgb, expected in zip(quadrants[:3], (0, 1, 2)):
    assert rgb[expected] > 0.03 and rgb[expected] > max(rgb[c] for c in range(3) if c != expected) * 3, rgb
assert min(quadrants[3]) > 0.03, quadrants[3]
spot.data.energy = 0
off = render("off")
assert max(off[0::4]) < 0.001, "Receiver still lit with all light sources off"

scene["vp_content"] = "WHITE"
scene["vp_flux_w"] = 5000.0
scene["vp_scatter_albedo"] = 1.0
p.update_projection()
spot.data.energy = 0
for obj in scene.objects:
    if obj.type not in {"LIGHT", "CAMERA"} and obj.get("vp_role") != "receiver":
        obj.hide_render = True
scatter = render("scatter_only")
mean = sum(scatter[0::4]) / (64 * 64)
print("SCATTER_ONLY_MEAN", mean, flush=True)
assert mean > 0.001, "Secondary sources did not illuminate the receiver"
print("PASS: projected RGB orientation, power-off, and secondary illumination", flush=True)

# A ray-filtered world isolates reflection from transmission and direct visibility.
glass_material = scene.objects[scene["vp_volume"]].data.materials[0]
scene = bpy.data.scenes.new("Glass_Reflection_Probe")
bpy.context.window.scene = scene
scene.render.engine = "CYCLES"
scene.render.resolution_x = scene.render.resolution_y = 64
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = "PNG"
scene.render.image_settings.color_depth = "16"
scene.view_settings.view_transform = "Standard"
scene.view_settings.look = "None"
scene.cycles.samples = 64
scene.cycles.use_denoising = False
scene.cycles.use_adaptive_sampling = False
scene.cycles.seed = 0
bpy.ops.mesh.primitive_plane_add(size=4, rotation=(math.pi / 2, 0, 0))
probe = bpy.context.object
baseline_material = p.create_glass_material("Glass_Reflection_Baseline", (0.72, 0.92, 1, 1), p.CUBE_ALPHA)
probe.data.materials.append(baseline_material)
bpy.ops.object.camera_add(location=(0, -3, 0))
scene.camera = bpy.context.object
p.look_at(scene.camera, Vector((0, 0, 0)))
scene.world = bpy.data.worlds.new("Reflection_Only_World")
scene.world.use_nodes = True
nodes, links = scene.world.node_tree.nodes, scene.world.node_tree.links
nodes.clear()
path = nodes.new("ShaderNodeLightPath")
not_transmission = nodes.new("ShaderNodeMath")
not_transmission.operation = "SUBTRACT"
not_transmission.inputs[0].default_value = 1
links.new(path.outputs["Is Transmission Ray"], not_transmission.inputs[1])
reflection = nodes.new("ShaderNodeMath")
reflection.operation = "MULTIPLY"
links.new(path.outputs["Is Glossy Ray"], reflection.inputs[0])
links.new(not_transmission.outputs[0], reflection.inputs[1])
background = nodes.new("ShaderNodeBackground")
background.inputs["Color"].default_value = (1, 1, 1, 1)
links.new(reflection.outputs[0], background.inputs["Strength"])
output = nodes.new("ShaderNodeOutputWorld")
links.new(background.outputs[0], output.inputs["Surface"])

baseline = render("glass_reflective_baseline")
probe.data.materials[0] = glass_material
no_reflection = render("glass_reflection_disabled")
baseline_mean = sum(baseline[0::4]) / (64 * 64)
reflection_mean = sum(no_reflection[0::4]) / (64 * 64)
print("GLASS_REFLECTION_MEAN", baseline_mean, reflection_mean, flush=True)
assert baseline_mean > 0.005, "Reflection probe did not detect the old glass reflection"
assert reflection_mean < baseline_mean * 0.1, "Glass still reflects the environment"

links.new(path.outputs["Is Transmission Ray"], background.inputs["Strength"])
transmission = render("glass_transmission_preserved")
transmission_mean = sum(transmission[0::4]) / (64 * 64)
print("GLASS_TRANSMISSION_MEAN", transmission_mean, flush=True)
assert transmission_mean > 0.01, "Disabling reflection also removed glass transmission"
print("PASS: glass reflection disabled and transmission preserved", flush=True)
