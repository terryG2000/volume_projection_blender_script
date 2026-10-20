"""Correctness guards for idempotent scene updates; no machine-specific FPS assertions."""
import importlib.util
from pathlib import Path
import unittest

import bpy

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("projection_update_test", ROOT / "projector.py")
projection = importlib.util.module_from_spec(spec)
spec.loader.exec_module(projection)


class UpdatePerformanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        projection.build_scene()
        cls.scene = bpy.context.scene

    def test_visibility_only_writes_on_transition_and_reads_actual_state(self):
        class VisibilityProbe:
            hidden = False
            render_hidden = False
            writes = 0

            @property
            def hide_render(self):
                return self.render_hidden

            @hide_render.setter
            def hide_render(self, value):
                self.writes += 1
                self.render_hidden = value

            def hide_get(self):
                return self.hidden

            def hide_set(self, value):
                self.writes += 1
                self.hidden = value

        probe = VisibilityProbe()
        projection.set_visibility(probe, True)
        self.assertEqual(probe.writes, 0)
        projection.set_visibility(probe, False)
        self.assertEqual(probe.writes, 2)
        projection.set_visibility(probe, False)
        self.assertEqual(probe.writes, 2)
        probe.hidden = False  # A user edit must not be hidden by a stale cache.
        projection.set_visibility(probe, False)
        self.assertEqual(probe.writes, 3)
        projection.set_visibility(probe, True)
        self.assertEqual(probe.writes, 5)

    def test_repeated_updates_leave_geometry_colors_and_visibility_unchanged(self):
        self.scene["vp_hide_ray_geometry"] = True
        projection.update_projection()

        def snapshot():
            return {obj.name: (tuple(tuple(row) for row in obj.matrix_world), tuple(obj.color),
                               obj.hide_render, obj.hide_get())
                    for obj in self.scene.objects}

        before = snapshot()
        projection.update_projection()
        self.assertEqual(before, snapshot())

    def test_manual_state_changes_are_still_reconciled(self):
        self.scene["vp_hide_ray_geometry"] = True
        self.scene["vp_show_image"] = False
        projection.update_projection()
        bubble = next(o for o in self.scene.objects if o.get("vp_role") == "bubble")
        marker = next(o for o in self.scene.objects if o.get("vp_role") == "image")
        before_color = tuple(bubble.color)
        bubble.visible_shadow = True
        bubble["vp_scattered_w"] = 99.0
        bubble.color = (0.123, 0.456, 0.789, 1)
        marker.hide_set(False)
        marker.hide_render = False
        marker.location.x += 2.0
        projection.update_projection()
        self.assertFalse(bubble.visible_shadow)
        self.assertEqual(bubble["vp_scattered_w"], 0.0)
        self.assertEqual(tuple(bubble.color), before_color)
        self.assertTrue(marker.hide_get())
        self.assertTrue(marker.hide_render)
        target = projection.projection_body().matrix_world @ projection.Vector(marker["vp_target_local"])
        self.assertLess((marker.matrix_world.translation - target).length, 1e-6)


result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(UpdatePerformanceTests))
if not result.wasSuccessful():
    raise RuntimeError("Update performance regression failed")
