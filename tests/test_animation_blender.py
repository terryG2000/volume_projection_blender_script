"""Blender --background --factory-startup --python-exit-code 1 --python this_file."""
import importlib.util
import json
from pathlib import Path
import unittest

import bpy

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("projection_animation_test", ROOT / "projector.py")
projection = importlib.util.module_from_spec(spec)
spec.loader.exec_module(projection)
animation = projection.animation_module()
OUT = ROOT / "tmp" / "animation_test"


class AnimationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        OUT.mkdir(parents=True, exist_ok=True)
        cls.source, cls.cube = animation.create_rotating_cube()
        cls.source.frame_set(7, subframe=0.25)
        cls.path = OUT / "cube.json"
        cls.data = animation.export_animation(cls.path, cls.source, [cls.cube], samples=600)
        projection.build_scene()
        cls.scene = bpy.context.scene
        cls.scene["vp_samples_x"], cls.scene["vp_samples_z"] = 24, 18
        projection.update_projection()
        projection.register_projection_controls()

    def setUp(self):
        projection.load_point_animation(str(self.path))
        projection.projection_body()["power"] = 1.0
        self.scene["vp_flux_w"] = 180.0
        self.scene["vp_animation_radius"] = 0.18
        projection.update_projection()

    def bubbles(self):
        return [o for o in self.scene.objects if o.get("vp_role") == "bubble"]

    def test_export_samples_evaluated_rotation_and_restores_frame(self):
        self.assertEqual(self.source.frame_current, 7)
        self.assertAlmostEqual(self.source.frame_subframe, 0.25)
        self.assertEqual(len(self.data["frames"]), 48)
        self.assertEqual(len(self.data["frames"][0]["points"]), 600)
        self.assertNotEqual(self.data["frames"][0]["points"], self.data["frames"][12]["points"])
        for frame in self.data["frames"]:
            self.assertTrue(all(abs(v) <= 0.800001 for p in frame["points"] for v in p[:3]))

    def test_scrubbing_changes_colors_without_moving_points(self):
        before = [(o.as_pointer(), tuple(o.matrix_world.translation)) for o in self.bubbles()]
        colors = [tuple(o.color) for o in self.bubbles()]
        self.scene.frame_set(13)
        self.assertEqual(self.scene["vp_animation_frame"], 13)
        self.assertEqual(before, [(o.as_pointer(), tuple(o.matrix_world.translation)) for o in self.bubbles()])
        self.assertNotEqual(colors, [tuple(o.color) for o in self.bubbles()])
        self.assertGreater(self.scene["vp_lit_count"], 0)
        self.assertLess(self.scene["vp_lit_count"], len(self.bubbles()))

    def test_texture_and_bubbles_share_projector_frame(self):
        body = projection.projection_body()
        frame = projection.projection_frame(self.scene, body)
        image = bpy.data.images[self.scene["vp_frame_image"]]
        pixels = list(image.pixels)
        nx, nz = body["vp_grid"]
        for bubble in self.bubbles():
            ix, iz = bubble["pixel_x"], bubble["pixel_z"]
            expected = frame[ix, iz]
            for actual, target in zip(bubble.color, expected):
                self.assertAlmostEqual(actual, target, places=5)
            for actual, target in zip(pixels[4 * (iz * nx + ix):4 * (iz * nx + ix) + 4], expected):
                self.assertAlmostEqual(actual, target, places=5)

    def test_loop_clamp_and_fps_conversion(self):
        self.scene.frame_set(49)
        self.assertEqual(self.scene["vp_animation_frame"], 1)
        self.scene["vp_animation_loop"] = False
        self.scene.frame_set(50)
        self.assertEqual(self.scene["vp_animation_frame"], 48)
        self.scene.render.fps = 48
        self.scene.frame_set(25)
        self.assertEqual(self.scene["vp_animation_frame"], 13)

    def test_power_off_and_return_to_static_content(self):
        self.scene["vp_flux_w"] = 0.0
        self.scene.frame_set(15)
        self.assertEqual(self.scene["vp_lit_count"], 0)
        self.scene["vp_content"] = "WHITE"
        self.scene["vp_flux_w"] = 180.0
        projection.update_projection()
        self.assertEqual(self.scene["vp_lit_count"], len(self.bubbles()))

    def test_reconnect_does_not_duplicate_handler(self):
        projection.register_projection_controls()
        projection.register_projection_controls()
        self.assertEqual(bpy.app.handlers.frame_change_post.count(projection.point_animation_changed), 1)
        projection._animation_cache.clear()
        self.scene.frame_set(20)
        self.assertEqual(self.scene["vp_animation_frame"], 20)
        self.assertFalse(self.scene.get("vp_animation_error"))

    def test_invalid_import_preserves_current_animation(self):
        previous = self.scene["vp_animation_text"]
        invalid = OUT / "invalid.json"
        invalid.write_text('{"format":"bad"}', encoding="utf-8")
        with self.assertRaises(ValueError):
            projection.load_point_animation(str(invalid))
        self.assertEqual(self.scene["vp_animation_text"], previous)
        for point in ([0, 0, 0, float("nan"), 0, 0], [2, 0, 0, 1, 0, 0], [0, 0]):
            bad = dict(self.data, frames=[{"points": [point]}])
            with self.assertRaises(ValueError):
                animation.validate(bad)

    def test_empty_frame_is_black(self):
        path = OUT / "empty.json"
        path.write_text(json.dumps(dict(self.data, frames=[{"points": []}])), encoding="utf-8")
        projection.load_point_animation(str(path))
        self.assertEqual(self.scene["vp_lit_count"], 0)

    def test_evaluated_shape_keys_are_exported(self):
        source, cube = animation.create_rotating_cube()
        cube.animation_data_clear()
        cube.rotation_euler = (0, 0, 0)
        cube.shape_key_add(name="Basis")
        shape = cube.shape_key_add(name="Stretch")
        for vertex in shape.data:
            vertex.co.x *= 2
        shape.value = 0
        shape.keyframe_insert(data_path="value", frame=1)
        shape.value = 1
        shape.keyframe_insert(data_path="value", frame=2)
        source.frame_end = 2
        data = animation.export_animation(OUT / "deform.json", source, [cube], 600)
        widths = [max(p[0] for p in f["points"]) - min(p[0] for p in f["points"]) for f in data["frames"]]
        self.assertAlmostEqual(widths[1] / widths[0], 2, places=3)

    def test_shared_projector_pixel_cannot_get_independent_colors(self):
        bubbles = self.bubbles()
        first = next(o for o in bubbles if o["vp_lit"])
        second = next(o for o in bubbles if o != first)
        old = second.location.copy()
        self.scene["vp_freeze_points"] = True
        try:
            second.location = first.location
            projection.update_projection()
            self.assertGreater(self.scene["vp_animation_conflicts"], 0)
            self.assertEqual(tuple(first.color), tuple(second.color))
        finally:
            second.location = old
            self.scene["vp_freeze_points"] = False
            projection.update_projection()

    def test_source_export_rejects_no_mesh_and_excessive_size(self):
        with self.assertRaises(ValueError):
            animation.export_animation(OUT / "bad.json", self.source, [], 20)
        with self.assertRaises(ValueError):
            animation.export_animation(OUT / "bad.json", self.source, [self.cube], 20000)


suite = unittest.defaultTestLoader.loadTestsFromTestCase(AnimationTests)
result = unittest.TextTestRunner(verbosity=2).run(suite)
if not result.wasSuccessful():
    raise RuntimeError("Animation regression failed")
