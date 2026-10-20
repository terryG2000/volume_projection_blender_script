"""Profile the saved 3072-point demo without saving changes to it.

blender -b outputs/rotating_cube_metric.blend --python this_file -- --label baseline
"""
import argparse
import cProfile
import hashlib
import importlib.util
import io
import json
import linecache
from pathlib import Path
import pstats
import statistics
import subprocess
import sys
import time

import bpy

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--label", default="current")
parser.add_argument("--line-profile", action="store_true")
parser.add_argument("--revision", help="Optional existing 7..40-character Git commit hash")
parser.add_argument("--render", action="store_true", help="Render frame 13 twice at 640x440 / 12 samples")
args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])
if args.revision and args.line_profile:
    raise ValueError("Line tracing requires the working-tree source for accurate source-line text")
if not args.label.replace("_", "").replace("-", "").isalnum():
    raise ValueError("Use an alphanumeric benchmark label")
spec = importlib.util.spec_from_file_location("projection_benchmark", ROOT / "projector.py")
projection = importlib.util.module_from_spec(spec)
if args.revision:
    if not 7 <= len(args.revision) <= 40 or any(c not in "0123456789abcdefABCDEF" for c in args.revision):
        raise ValueError("Use a Git commit hash, not a branch or shell expression")
    code = subprocess.check_output(
        ["git", "-c", f"safe.directory={ROOT.as_posix()}", "-C", str(ROOT),
         "show", f"{args.revision}:projector.py"], encoding="utf-8")
    exec(compile(code, str(ROOT / "projector.py"), "exec"), projection.__dict__)
else:
    code = (ROOT / "projector.py").read_text(encoding="utf-8")
    spec.loader.exec_module(projection)
projection.main()
scene = bpy.context.scene
old_frame = scene.frame_current
try:
    scene.frame_set(2)
    timings = []
    for frame in (3, 7, 11, 15, 19, 23):
        start = time.perf_counter()
        scene.frame_set(frame)
        if scene.get("vp_animation_error"):
            raise RuntimeError(scene["vp_animation_error"])
        timings.append(time.perf_counter() - start)
    profiler = cProfile.Profile()
    profiler.runcall(scene.frame_set, 27)
    stream = io.StringIO()
    pstats.Stats(profiler, stream=stream).strip_dirs().sort_stats("cumulative").print_stats(35)
    result = {
        "label": args.label, "blender": bpy.app.version_string,
        "source": projection.animation_module().source_revision(),
        "loaded_projector_revision": args.revision or "working-tree",
        "loaded_projector_sha256": hashlib.sha256(code.encode("utf-8")).hexdigest(),
        "objects": len(scene.objects),
        "bubbles": sum(o.get("vp_role") == "bubble" for o in scene.objects),
        "engine": scene.render.engine, "viewport_samples": scene.cycles.preview_samples,
        "frame_set_seconds": timings, "median_seconds": statistics.median(timings),
        "note": "Headless frame update plus depsgraph evaluation, not GUI FPS or GPU rendering.",
        "profile": stream.getvalue(),
    }
    if args.line_profile:
        totals, previous = {}, {}

        def trace(frame, event, arg):
            if frame.f_code.co_name not in {"update_projection", "update_optical_projection"}:
                return None
            now = time.perf_counter()
            if frame in previous:
                key, start = previous[frame]
                totals[key] = totals.get(key, 0.0) + now - start
            if event == "return":
                previous.pop(frame, None)
            else:
                previous[frame] = ((frame.f_code.co_filename, frame.f_lineno), now)
            return trace

        sys.settrace(trace)
        try:
            scene.frame_set(31)
        finally:
            sys.settrace(None)
        result["line_profile_with_tracing_overhead"] = [
            {"line": number, "seconds": seconds, "code": linecache.getline(path, number).strip()}
            for (path, number), seconds in sorted(totals.items(), key=lambda item: -item[1])[:15]]
    output = ROOT / "tmp" / "animation_performance"
    output.mkdir(parents=True, exist_ok=True)
    if args.render:
        scene.frame_set(13)
        scene.render.resolution_x, scene.render.resolution_y = 640, 440
        scene.render.resolution_percentage = 100
        scene.cycles.samples = 12
        scene.cycles.seed = 2006
        scene.cycles.use_animated_seed = False
        scene.cycles.use_adaptive_sampling = False
        scene.render.image_settings.file_format = "PNG"
        scene.render.filepath = str(output / f"{args.label}.png")
        result["render_seconds"] = []
        for _ in range(2):
            start = time.perf_counter()
            bpy.ops.render.render(write_still=True)
            result["render_seconds"].append(time.perf_counter() - start)
        result["render_png_sha256"] = hashlib.sha256(Path(scene.render.filepath).read_bytes()).hexdigest()
    (output / f"{args.label}.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "profile"}, indent=2), flush=True)
    print(stream.getvalue(), flush=True)
finally:
    scene.frame_set(old_frame)
