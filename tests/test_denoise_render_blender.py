"""OptiX 12-sample still-image proxy; verify the live viewport separately."""
import importlib.util
import json
from pathlib import Path

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("denoise_test", ROOT / "projector.py")
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)
p.build_scene()
scene = bpy.context.scene
if scene["vp_render_backend"] != "OPTIX":
    raise RuntimeError("This image comparison requires an OptiX GPU")
scene["vp_content"] = "COLOR_BARS"
scene["vp_hide_ray_geometry"] = True
p.update_projection()
scene.render.resolution_x, scene.render.resolution_y = 1280, 820
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = "PNG"
scene.render.image_settings.color_depth = "16"
scene.cycles.samples = scene.cycles.preview_samples
scene.cycles.use_adaptive_sampling = False
scene.cycles.seed = 0
scene.cycles.denoiser = scene.cycles.preview_denoiser
out = ROOT / "tmp" / "denoise_render"
out.mkdir(parents=True, exist_ok=True)


def render(name, denoise):
    scene.cycles.use_denoising = denoise
    scene.render.filepath = str(out / f"{name}.png")
    bpy.ops.render.render(write_still=True)
    image = bpy.data.images.load(scene.render.filepath, check_existing=False)
    values = np.array(image.pixels[:], dtype=np.float32).reshape(820, 1280, 4)[:, :, :3]
    bpy.data.images.remove(image)
    return values


raw = render("before", False)
clean = render("after", True)
mask = (raw.max(axis=2) > 0.5) & (raw.min(axis=2) < 0.03)
assert int(mask.sum()) > 100, "Probe contains too few saturated point pixels"
kept = (clean.max(axis=2) > 0.25) & (clean.min(axis=2) < 0.1)
retained = float(kept[mask].mean())
color_error = float(np.abs(raw[mask] / raw[mask].max(axis=1, keepdims=True)
                          - clean[mask] / np.maximum(clean[mask].max(axis=1, keepdims=True), 1e-8)).mean())


def floor_noise(values):
    patch = values[40:120, 300:480].mean(axis=2)
    laplacian = 4 * patch[1:-1, 1:-1] - patch[:-2, 1:-1] - patch[2:, 1:-1] - patch[1:-1, :-2] - patch[1:-1, 2:]
    return float(np.median(np.abs(laplacian)))


noise_before, noise_after = floor_noise(raw), floor_noise(clean)
result = {"backend": "OPTIX", "samples": scene.cycles.samples,
          "saturated_pixels_before": int(mask.sum()), "saturated_pixel_retention": retained,
          "normalized_rgb_mean_error": color_error,
          "floor_median_abs_laplacian_before": noise_before,
          "floor_median_abs_laplacian_after": noise_after,
          "scope": "12-sample still-render proxy, not viewport timing or all point visibility."}
print("DENOISE_COMPARISON", result, flush=True)
(out / "verification.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
assert retained > 0.9, "Denoising removed too many saturated point pixels"
assert color_error < 0.1, "Denoising changed point colors excessively"
assert noise_before > 1e-6 and noise_after < noise_before * 0.6, "Floor noise did not decrease"
print("PASS: noise reduced and saturated point colors retained", flush=True)
