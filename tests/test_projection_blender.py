"""Run with Blender --background --factory-startup --python-exit-code 1 --python this_file."""
import importlib.util
import csv
import math
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("projection_under_test", ROOT / "projector.py")
projection = importlib.util.module_from_spec(spec)
spec.loader.exec_module(projection)


class ProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = projection.build_scene()
        bpy.context.scene["vp_freeze_points"] = True
        projection.update_projection()
        bpy.context.view_layer.update()

    def test_parent_preserves_newly_assigned_scale(self):
        fin = bpy.data.objects["Projector_CoolingFin_00"]
        self.assertAlmostEqual(fin.matrix_world.to_scale().x, 0.045, places=5)

    def test_dark_pixels_still_have_physical_scatterers(self):
        self.assertEqual(len(self.data["bubble_field"].bubbles),
                         projection.IMAGE_GRID_X * projection.IMAGE_GRID_Z)

    def test_lens_tracks_body_transform(self):
        projector = self.data["projector"]
        before = projector.lens_position.copy()
        body = projector.object
        old = body.location.copy()
        try:
            body.location.x += 0.3
            bpy.context.view_layer.update()
            self.assertAlmostEqual(projector.lens_position.x - before.x, 0.3, places=5)
        finally:
            body.location = old
            bpy.context.view_layer.update()

    def test_live_projection_keeps_bubbles_fixed_and_turns_away(self):
        self.assertTrue(hasattr(projection, "update_projection"), "Live projection is missing")
        body = self.data["projector"].object
        bubbles = self.data["bubble_field"].bubbles
        positions = [tuple(b.matrix_world.translation) for b in bubbles]
        old = body.rotation_euler.copy()
        try:
            body.rotation_euler.z += math.pi
            bpy.context.view_layer.update()
            projection.update_projection()
            self.assertEqual(bpy.context.scene["vp_lit_count"], 0)
            self.assertEqual(positions, [tuple(b.matrix_world.translation) for b in bubbles])
        finally:
            body.rotation_euler = old
            bpy.context.view_layer.update()
            projection.update_projection()

    def test_calibrated_ray_mapping(self):
        self.assertTrue(hasattr(projection, "project_to_pixel"), "Inverse projection is missing")
        body = self.data["projector"].object
        for bubble in self.data["bubble_field"].bubbles:
            sample = projection.project_to_pixel(body, bubble.matrix_world.translation)
            self.assertIsNotNone(sample)
            self.assertAlmostEqual(sample[0], bubble["pixel_x"], places=4)
            self.assertAlmostEqual(sample[1], bubble["pixel_z"], places=4)

    def test_pixel_rays_do_not_intersect_other_bubbles(self):
        lens = self.data["projector"].lens_position
        bubbles = self.data["bubble_field"].bubbles
        volume = self.data["volume"]
        for bubble in bubbles:
            position = bubble.matrix_world.translation
            radius = max(bubble.scale)
            self.assertLess(abs(position.x - volume.center.x) + radius, volume.size.x / 2)
            self.assertLess(abs(position.y - volume.center.y) + radius, volume.size.y / 2)
            self.assertLess(abs(position.z - volume.center.z) + radius, volume.size.z / 2)
            direction = (position - lens).normalized()
            for other in bubbles:
                if other != bubble:
                    distance = (other.matrix_world.translation - lens).cross(direction).length
                    self.assertGreater(distance, max(other.scale))

    def test_reference_image_updates_with_content(self):
        scene = bpy.context.scene
        images = [o for o in scene.objects if o.get("vp_role") == "image"]
        self.assertEqual(len(images), projection.IMAGE_GRID_X * projection.IMAGE_GRID_Z)
        try:
            scene["vp_content"] = "WHITE"
            projection.update_projection()
            self.assertEqual(scene["vp_lit_count"], scene["vp_total_count"])
            self.assertTrue(all(min(o.color[:3]) > 0.99 for o in images))
        finally:
            scene["vp_content"] = "TEST"
            projection.update_projection()

    def test_export_contains_fixed_and_dark_points(self):
        out = ROOT / "tmp" / "test_export"
        projection.export_projection(out)
        with (out / "bubble_coordinates.csv").open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), projection.IMAGE_GRID_X * projection.IMAGE_GRID_Z)
        self.assertTrue(any(row["lit"] == "0" for row in rows))
        self.assertTrue((out / "projector_input.png").is_file())

    def test_rerun_preserves_user_edits_and_registration(self):
        body = self.data["projector"].object
        old = body.location.copy()
        marker = bpy.data.objects.new("User_Note", None)
        bpy.context.scene.collection.objects.link(marker)
        try:
            body.location.x += 0.2
            bpy.context.view_layer.update()
            count = len(bpy.context.scene.objects)
            projection.main()
            projection.main()
            self.assertEqual(len(bpy.context.scene.objects), count)
            self.assertAlmostEqual(body.location.x, old.x + 0.2, places=5)
            self.assertIn(marker.name, bpy.context.scene.objects)
            self.assertEqual(len(bpy._volume_projection_runtime["classes"]), 6)
        finally:
            body.location = old
            bpy.data.objects.remove(marker, do_unlink=True)
            bpy.context.view_layer.update()
            projection.update_projection()

    def test_turn_off_and_restore(self):
        body = self.data["projector"].object
        original = body.matrix_world.copy()
        body["power"] = 0.0
        projection.update_projection()
        self.assertEqual(bpy.context.scene["vp_lit_count"], 0)
        body["power"] = 1.0
        body.location.x += 0.5
        body.rotation_euler.z += 0.2
        projection.restore_calibration()
        bpy.context.view_layer.update()
        for row1, row2 in zip(body.matrix_world, original):
            for a, b in zip(row1, row2):
                self.assertAlmostEqual(a, b, places=5)


class ParametricTests(unittest.TestCase):
    def setUp(self):
        projection.build_scene()
        self.scene = bpy.context.scene
        self.body = projection.projection_body()

    def bubbles(self):
        return [o for o in self.scene.objects if o.get("vp_role") == "bubble"]

    def test_density_rebuilds_without_resetting_pose_or_user_objects(self):
        self.body.location.x += 0.15
        bpy.context.view_layer.update()
        pose = self.body.matrix_world.copy()
        marker = bpy.data.objects.new("User_Control", None)
        self.scene.collection.objects.link(marker)
        self.scene["vp_samples_x"] = 24
        self.scene["vp_samples_z"] = 18
        projection.update_projection()
        self.assertEqual(len(self.bubbles()), 432)
        self.assertEqual(list(self.body["vp_grid"]), [24, 18])
        self.assertEqual(pose, self.body.matrix_world)
        self.assertIn(marker.name, self.scene.objects)

    def test_fov_is_actual_full_angular_field(self):
        self.scene["vp_fov_x"] = 60.0
        self.scene["vp_fov_z"] = 45.0
        projection.update_projection()
        nx, nz = self.body["vp_grid"]
        plane = Vector(self.body["vp_plane_origin"])
        sx, sz = self.body["vp_plane_span"]
        lens = self.body.matrix_world @ Vector(self.body["vp_lens_local"])
        center = plane + Vector((sx / 2, 0, sz / 2))
        left = self.body.matrix_world @ (center - Vector((sx * nx / (nx - 1) / 2, 0, 0)))
        right = self.body.matrix_world @ (center + Vector((sx * nx / (nx - 1) / 2, 0, 0)))
        self.assertAlmostEqual(math.degrees((left - lens).angle(right - lens)), 60.0, places=3)
        self.assertGreater(len(self.bubbles()), 0)
        for point in self.bubbles():
            sample = projection.project_to_pixel(self.body, point.matrix_world.translation)
            self.assertAlmostEqual(sample[0], point["pixel_x"], places=3)
            self.assertAlmostEqual(sample[1], point["pixel_z"], places=3)

    def test_fov_can_keep_existing_scatterers_fixed(self):
        before = {b.name: tuple(b.matrix_world.translation) for b in self.bubbles()}
        span = tuple(self.body["vp_plane_span"])
        self.scene["vp_freeze_points"] = True
        self.scene["vp_fov_x"] = 75.0
        projection.update_projection()
        self.assertEqual(before, {b.name: tuple(b.matrix_world.translation) for b in self.bubbles()})
        self.assertNotEqual(span, tuple(self.body["vp_plane_span"]))

    def test_rebuild_while_aiming_away_creates_no_outside_points(self):
        self.body.rotation_euler.z = math.pi
        self.scene["vp_samples_x"] = 20
        projection.update_projection()
        self.assertEqual(len(self.bubbles()), 0)
        self.assertEqual(self.scene["vp_lit_count"], 0)

    def test_pose_change_rebuilds_points_on_new_rays(self):
        before = {b.name: tuple(b.location) for b in self.bubbles()}
        self.body.location.x += 0.4
        self.body.rotation_euler.z += 0.12
        projection.update_projection()
        self.assertNotEqual(before, {b.name: tuple(b.location) for b in self.bubbles()})
        self.assertGreater(len(self.bubbles()), 0)
        volume = self.scene.objects[self.scene["vp_volume"]]
        lens = self.body.matrix_world @ Vector(self.body["vp_lens_local"])
        for point in self.bubbles():
            position = point.matrix_world.translation
            sample = projection.project_to_pixel(self.body, position)
            self.assertAlmostEqual(sample[0], point["pixel_x"], places=3)
            self.assertAlmostEqual(sample[1], point["pixel_z"], places=3)
            local = volume.matrix_world.inverted() @ position
            self.assertTrue(all(abs(value) < 0.5 for value in local))
            target = self.body.matrix_world @ projection.pixel_target_local(
                self.body, point["pixel_x"], point["pixel_z"])
            self.assertLess((position - lens).cross((target - lens).normalized()).length, 1e-5)
        pointers = [b.as_pointer() for b in self.bubbles()]
        projection.update_projection()
        self.assertEqual(pointers, [b.as_pointer() for b in self.bubbles()])

    def test_pose_away_and_back_removes_and_restores_points(self):
        before = {b.name: tuple(b.location) for b in self.bubbles()}
        self.body.rotation_euler.z = math.pi
        projection.update_projection()
        self.assertEqual(len(self.bubbles()), 0)
        self.body.rotation_euler.z = 0
        projection.update_projection()
        self.assertEqual(before, {b.name: tuple(b.location) for b in self.bubbles()})

    def test_timer_debounces_pose_and_respects_auto_switch(self):
        projection.register_projection_controls()
        before = {b.name: tuple(b.location) for b in self.bubbles()}
        with patch.object(projection.time, "monotonic", return_value=10.0):
            projection.live_projection_tick()
        self.body.location.x += 0.4
        bpy.context.view_layer.update()
        with patch.object(projection.time, "monotonic", return_value=11.0):
            projection.live_projection_tick()
        with patch.object(projection.time, "monotonic", return_value=11.2):
            projection.live_projection_tick()
        self.assertEqual(before, {b.name: tuple(b.location) for b in self.bubbles()})
        self.scene["vp_auto"] = False
        with patch.object(projection.time, "monotonic", return_value=12.0):
            projection.live_projection_tick()
        self.assertEqual(before, {b.name: tuple(b.location) for b in self.bubbles()})
        self.scene["vp_auto"] = True
        with patch.object(projection.time, "monotonic", return_value=13.0):
            projection.live_projection_tick()
        self.assertNotEqual(before, {b.name: tuple(b.location) for b in self.bubbles()})

    def test_fixed_mode_keeps_points_after_pose_and_fov_change(self):
        before = {b.name: tuple(b.location) for b in self.bubbles()}
        self.scene["vp_freeze_points"] = True
        self.body.location.x += 0.4
        self.scene["vp_fov_x"] = 60.0
        projection.update_projection()
        self.assertEqual(before, {b.name: tuple(b.location) for b in self.bubbles()})


class OpticalTests(unittest.TestCase):
    def setUp(self):
        projection.build_scene()
        self.scene = bpy.context.scene
        self.body = projection.projection_body()

    def test_textured_spot_tracks_lens_and_power(self):
        self.assertTrue(hasattr(projection, "update_optical_projection"))
        spot = self.scene.objects[self.scene["vp_spot"]]
        self.assertEqual(spot.data.type, "SPOT")
        self.assertIsNotNone(spot.data.node_tree.nodes.get("ProjectionImage").image)
        self.body.location.x += 0.25
        self.body.rotation_euler.z += 0.1
        projection.update_projection()
        lens = self.body.matrix_world @ Vector(self.body["vp_lens_local"])
        self.assertLess((spot.location - lens).length, 1e-5)
        forward = self.body.matrix_world.to_quaternion() @ Vector((0, -1, 0))
        self.assertLess(((spot.rotation_quaternion @ Vector((0, 0, -1))) - forward).length, 1e-5)
        self.body["power"] = 0
        projection.update_projection()
        self.assertEqual(spot.data.energy, 0)
        self.assertTrue(all(o.data.energy == 0 for o in self.scene.objects if o.get("vp_role") == "scatter_light"))

    def test_image_sampling_matches_pixel_centers(self):
        image = bpy.data.images.new("Test_Quadrants", width=2, height=2, float_buffer=True)
        image.pixels = [1, 0, 0, 1, 0, 1, 0, 1, 0, 0, 1, 1, 1, 1, 1, 1]
        self.scene["vp_source_image"] = image.name
        self.scene["vp_content"] = "IMAGE"
        self.scene["vp_samples_x"] = 2
        self.scene["vp_samples_z"] = 2
        projection.update_projection()
        frame = projection.projection_frame(self.scene, self.body)
        self.assertEqual(frame[0, 0], (1, 0, 0, 1))
        self.assertEqual(frame[1, 0], (0, 1, 0, 1))
        self.assertEqual(frame[0, 1], (0, 0, 1, 1))
        self.assertEqual(frame[1, 1], (1, 1, 1, 1))

    def test_each_bubble_has_secondary_source_with_bounded_flux(self):
        bubbles = [o for o in self.scene.objects if o.get("vp_role") == "bubble"]
        lights = [o for o in self.scene.objects if o.get("vp_role") == "scatter_light"]
        self.assertEqual(len(lights), len(bubbles))
        for light in lights:
            bubble = self.scene.objects[light["vp_bubble"]]
            self.assertLess((light.location - bubble.location).length, 1e-5)
            self.assertEqual(light.data.energy > 0, bool(bubble["vp_lit"]))
        self.assertLess(sum(light.data.energy for light in lights), self.scene["vp_flux_w"])
        self.scene["vp_scatter_albedo"] = 0
        projection.update_projection()
        self.assertTrue(all(light.data.energy == 0 for light in lights))

    def test_receiver_is_fixed_and_non_emissive(self):
        self.assertIn("vp_receiver", self.scene)
        receiver = self.scene.objects[self.scene["vp_receiver"]]
        pose = receiver.matrix_world.copy()
        self.body.location.x += 0.25
        projection.update_projection()
        self.assertEqual(pose, receiver.matrix_world)
        self.assertFalse(any(node.type == "EMISSION" for node in receiver.data.materials[0].node_tree.nodes))
        self.scene["vp_optics"] = False
        projection.update_projection()
        self.assertEqual(self.scene.objects[self.scene["vp_spot"]].data.energy, 0)

    def test_load_image_packs_source_and_invalid_path_preserves_it(self):
        out = ROOT / "tmp" / "image_load"
        projection.export_projection(out)
        projection.load_projection_image(str(out / "projector_input.png"))
        name = self.scene["vp_source_image"]
        self.assertEqual(self.scene["vp_content"], "IMAGE")
        self.assertIsNotNone(bpy.data.images[name].packed_file)
        with self.assertRaises(ValueError):
            projection.load_projection_image(str(out / "does_not_exist.png"))
        self.assertEqual(name, self.scene["vp_source_image"])

    def test_zero_source_power_darks_scatter_markers(self):
        self.scene["vp_flux_w"] = 0.0
        projection.update_projection()
        for obj in self.scene.objects:
            if obj.get("vp_role") == "bubble":
                self.assertEqual(tuple(obj.color[:3]), (0, 0, 0))

    def test_off_center_point_samples_original_image_on_current_ray(self):
        image = bpy.data.images.new("Fine_Color_Stripes", width=4, height=2, float_buffer=True)
        image.pixels = [1, 0, 0, 1, 0, 1, 0, 1, 0, 0, 1, 1, 1, 1, 0, 1] * 2
        self.scene["vp_source_image"] = image.name
        self.scene["vp_content"] = "IMAGE"
        self.scene["vp_samples_x"] = self.scene["vp_samples_z"] = 2
        self.scene["vp_scatter_albedo"] = 1.0
        projection.update_projection()
        self.scene["vp_freeze_points"] = True
        bubble = next(o for o in self.scene.objects if o.get("vp_role") == "bubble" and o["pixel_x"] == 0)
        lens = self.body.matrix_world @ Vector(self.body["vp_lens_local"])
        target = self.body.matrix_world @ projection.pixel_target_local(self.body, -0.3, 0)
        bubble.location = lens.lerp(target, 0.65)
        projection.update_projection()
        # u=0.1 is red in the original 4-column image; the coarse cell center is green.
        self.assertEqual(tuple(bubble.color[:3]), (1, 0, 0))
        secondary = next(o for o in self.scene.objects if o.get("vp_role") == "scatter_light" and o["vp_bubble"] == bubble.name)
        self.assertEqual(tuple(secondary.data.color), (1, 0, 0))
        # Moving the projector shifts this fixed point into a different original-image column.
        self.body.location.x -= 0.6
        projection.update_projection()
        self.assertEqual(tuple(bubble.color[:3]), (0, 1, 0))
        self.assertEqual(tuple(secondary.data.color), (0, 1, 0))
        self.body.rotation_euler.z = math.pi
        projection.update_projection()
        self.assertEqual(tuple(bubble.color[:3]), (0, 0, 0))
        self.assertEqual(secondary.data.energy, 0)

    def test_color_bars_give_ray_and_point_the_same_hue(self):
        self.scene["vp_content"] = "COLOR_BARS"
        self.scene["vp_show_rays"] = True
        self.scene["vp_scatter_albedo"] = 1.0
        projection.update_projection()
        bubbles = [o for o in self.scene.objects if o.get("vp_role") == "bubble"]
        self.assertGreaterEqual(len({tuple(o.color[:3]) for o in bubbles}), 6)
        first = next(o for o in bubbles if o["pixel_x"] == 0)
        self.assertEqual(tuple(first.color[:3]), (1, 0, 0))
        for beam in (o for o in self.scene.objects if o.get("vp_role") == "beam"):
            bubble = next(o for o in bubbles if o["pixel_x"] == beam["pixel_x"] and o["pixel_z"] == beam["pixel_z"])
            self.assertEqual(tuple(beam.color[:3]), tuple(bubble.color[:3]))


if __name__ == "__main__":
    suite = unittest.TestSuite([unittest.defaultTestLoader.loadTestsFromTestCase(ProjectionTests),
                                unittest.defaultTestLoader.loadTestsFromTestCase(ParametricTests),
                                unittest.defaultTestLoader.loadTestsFromTestCase(OpticalTests)])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise RuntimeError("Projection regression tests failed")
