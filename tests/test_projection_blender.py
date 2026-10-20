"""Run with Blender --background --factory-startup --python-exit-code 1 --python this_file."""
import importlib.util
import csv
import json
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
            self.assertEqual(len(bpy._volume_projection_runtime["classes"]), 10)
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

    def test_gpu_configuration_uses_available_device(self):
        self.assertTrue(hasattr(projection, "configure_cycles_device"))
        prefs = bpy.context.preferences.addons["cycles"].preferences
        supported = {item[0] for item in prefs.get_device_types(bpy.context)}
        available = []
        for backend in ("OPTIX", "CUDA", "HIP", "ONEAPI", "METAL"):
            if backend in supported:
                devices = prefs.get_devices_for_type(backend)
                if any(d.type == backend for d in devices):
                    available.append(backend)
                    break
        if not available:
            self.skipTest("No supported GPU on this host")
        projection.configure_cycles_device()
        self.assertEqual(prefs.compute_device_type, available[0])
        self.assertEqual(self.scene.cycles.device, "GPU")
        devices = prefs.get_devices_for_type(available[0])
        self.assertTrue(all(d.use for d in devices if d.type == available[0]))
        self.assertTrue(all(not d.use for d in devices if d.type == "CPU"))

    def test_gpu_unavailable_falls_back_to_cpu(self):
        self.assertTrue(hasattr(projection, "configure_cycles_device"))
        prefs = bpy.context.preferences.addons["cycles"].preferences
        with patch.object(type(prefs), "get_devices_for_type", return_value=[]):
            projection.configure_cycles_device()
        self.assertEqual(self.scene.cycles.device, "CPU")
        self.assertEqual(self.scene["vp_render_backend"], "CPU")

    def test_device_reconnect_preserves_scene_and_sampling(self):
        self.assertTrue(hasattr(projection, "configure_cycles_device"))
        self.scene["vp_freeze_points"] = True
        self.body.location.x += 0.1
        projection.update_projection()
        pose = self.body.matrix_world.copy()
        points = [(o.as_pointer(), tuple(o.location)) for o in self.scene.objects if o.get("vp_role") == "bubble"]
        self.scene.cycles.samples = 47
        self.scene.cycles.preview_samples = 9
        self.scene.cycles.use_preview_denoising = False
        projection.main()
        self.assertIn("vp_render_backend", self.scene)
        self.assertEqual(self.body.matrix_world, pose)
        self.assertEqual(points, [(o.as_pointer(), tuple(o.location)) for o in self.scene.objects if o.get("vp_role") == "bubble"])
        self.assertEqual(self.scene.cycles.samples, 47)
        self.assertEqual(self.scene.cycles.preview_samples, 9)
        self.assertTrue(self.scene.cycles.use_preview_denoising)
        self.scene.cycles.use_preview_denoising = False
        projection.update_projection()
        self.assertFalse(self.scene.cycles.use_preview_denoising)

    def test_viewport_denoising_enabled_without_increasing_samples(self):
        self.assertTrue(self.scene.cycles.use_preview_denoising)
        self.assertEqual(self.scene.cycles.preview_samples, 12)
        self.assertEqual(self.scene.cycles.samples, 32)
        self.assertEqual(self.scene.cycles.preview_denoising_start_sample, 1)
        if self.scene["vp_render_backend"] == "OPTIX":
            self.assertEqual(self.scene.cycles.preview_denoiser, "OPTIX")

    def test_viewport_denoising_uses_auto_without_optix(self):
        self.assertTrue(hasattr(projection, "configure_viewport_denoising"))
        prefs = bpy.context.preferences.addons["cycles"].preferences
        with patch.object(type(prefs), "get_devices_for_type", return_value=[]):
            projection.configure_cycles_device()
        projection.configure_viewport_denoising()
        self.assertTrue(self.scene.cycles.use_preview_denoising)
        self.assertEqual(self.scene.cycles.preview_denoiser, "AUTO")

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
        self.assertFalse(any(o.get("vp_generated") and o.get("vp_role") == "scatter_light" for o in self.scene.objects))

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

    def test_points_sample_color_without_scattering_or_albedo_attenuation(self):
        self.scene["vp_content"] = "WHITE"
        for legacy_albedo in (0.0, 0.3, 1.0):
            self.scene["vp_scatter_albedo"] = legacy_albedo
            projection.update_projection()
            bubbles = [o for o in self.scene.objects if o.get("vp_role") == "bubble"]
            self.assertTrue(bubbles)
            self.assertTrue(all(tuple(o.color[:3]) == (1, 1, 1) for o in bubbles))
            self.assertFalse(any(o.get("vp_generated") and o.get("vp_role") in {"scatter_light", "scatter"} for o in self.scene.objects))
        self.body["power"] = 0.5
        projection.update_projection()
        self.assertTrue(all(tuple(o.color[:3]) == (0.5, 0.5, 0.5) for o in bubbles))

    def test_old_scattering_is_removed_without_deleting_user_lights(self):
        self.scene["vp_freeze_points"] = True
        points = [(o.as_pointer(), tuple(o.location)) for o in self.scene.objects if o.get("vp_role") == "bubble"]
        for name, generated, kind in (("Legacy_Scatter_Light", True, "POINT"),
                                       ("User_Light", False, "POINT"),
                                       ("Legacy_Scatter_Curve", True, "CURVE")):
            data = bpy.data.curves.new(name, "CURVE") if kind == "CURVE" else bpy.data.lights.new(name, kind)
            obj = bpy.data.objects.new(name, data)
            self.scene.collection.objects.link(obj)
            obj["vp_role"] = "scatter" if kind == "CURVE" else "scatter_light"
            obj["vp_generated"] = generated
            obj["vp_bubble"] = "Missing_" + name
            if kind == "CURVE":
                data.splines.new("POLY").points.add(1)
                data.materials.append(bpy.data.materials[self.scene["vp_optical_ray_material"]])
        user_light = self.scene.objects["User_Light"]
        self.addCleanup(bpy.data.objects.remove, user_light, do_unlink=True)
        user_light.data.energy = 7.0
        projection.main()
        projection.update_projection()
        self.assertNotIn("Legacy_Scatter_Light", self.scene.objects)
        self.assertNotIn("Legacy_Scatter_Curve", self.scene.objects)
        self.assertIn("User_Light", self.scene.objects)
        self.assertEqual(user_light.data.energy, 7.0)
        self.assertEqual(points, [(o.as_pointer(), tuple(o.location)) for o in self.scene.objects if o.get("vp_role") == "bubble"])
        self.scene["vp_samples_x"] = 20
        projection.update_projection()
        self.assertFalse(any(o.get("vp_generated") and o.get("vp_role") in {"scatter", "scatter_light"} for o in self.scene.objects))

    def test_export_identifies_color_only_model(self):
        out = ROOT / "tmp" / "color_only_export"
        projection.export_projection(out)
        settings = json.loads((out / "projection_settings.json").read_text(encoding="utf-8"))
        self.assertEqual(settings["model"], "pinhole_color_sampling")
        self.assertNotIn("vp_scatter_albedo", settings["parameters"])
        with (out / "bubble_coordinates.csv").open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertTrue(rows)
        self.assertTrue(all(float(row["scattered_w_approx"]) == 0 for row in rows))

    def test_scattering_migration_preserves_other_scenes(self):
        other = bpy.data.scenes.new("Reference_Scattering")
        self.addCleanup(bpy.data.scenes.remove, other)
        shared = bpy.data.collections.new("Shared_Scattering")
        self.scene.collection.children.link(shared)
        other.collection.children.link(shared)
        for name, collection in (("Shared_Collection_Light", shared),
                                  ("Shared_Object_Light", self.scene.collection)):
            obj = bpy.data.objects.new(name, bpy.data.lights.new(name, "POINT"))
            obj["vp_generated"], obj["vp_role"] = True, "scatter_light"
            obj.data.energy = 7.0
            collection.objects.link(obj)
            if collection == self.scene.collection:
                other.collection.objects.link(obj)
        untouched = bpy.data.objects.new("Shared_User_Object", None)
        shared.objects.link(untouched)
        projection.update_projection()
        for name in ("Shared_Collection_Light", "Shared_Object_Light"):
            self.assertNotIn(name, self.scene.objects)
            self.assertIn(name, other.objects)
            self.assertEqual(other.objects[name].data.energy, 7.0)
        self.assertIn(untouched.name, self.scene.objects)
        self.assertIn(untouched.name, other.objects)

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

    def test_zero_source_power_darks_color_points(self):
        self.scene["vp_flux_w"] = 0.0
        projection.update_projection()
        for obj in self.scene.objects:
            if obj.get("vp_role") == "bubble":
                self.assertEqual(tuple(obj.color[:3]), (0, 0, 0))

    def test_volume_has_no_specular_reflection_but_lens_is_unchanged(self):
        volume = self.scene.objects[self.scene["vp_volume"]]
        nodes = volume.data.materials[0].node_tree.nodes
        glass = next(n for n in nodes if n.type == "BSDF_REFRACTION")
        self.assertFalse(any(n.type == "BSDF_PRINCIPLED" for n in nodes))
        self.assertAlmostEqual(glass.inputs["IOR"].default_value, 1.46)
        self.assertFalse(volume.hide_render)
        lens = bpy.data.materials["Mat_Projector_Lens_Glass"]
        lens_shader = next(n for n in lens.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
        self.assertEqual(lens_shader.inputs["Specular IOR Level"].default_value, 0.5)

    def test_rerun_removes_old_glass_reflection_without_resetting_experiment(self):
        volume = self.scene.objects[self.scene["vp_volume"]]
        self.scene["vp_freeze_points"] = True
        self.body.location.x += 0.1
        projection.update_projection()
        legacy = projection.create_glass_material("Legacy_Volume_Glass", (0.72, 0.92, 1, 1), 0.27)
        volume.data.materials[0] = legacy
        points = [(o.name, tuple(o.location), tuple(o.color)) for o in self.scene.objects if o.get("vp_role") == "bubble"]
        lights = [(o.name, o.data.energy) for o in self.scene.objects if o.type == "LIGHT"]
        pose = self.body.matrix_world.copy()
        projection.main()
        nodes = volume.data.materials[0].node_tree.nodes
        self.assertFalse(any(n.type == "BSDF_PRINCIPLED" for n in nodes))
        mix = next(n for n in nodes if n.type == "MIX_SHADER")
        self.assertAlmostEqual(mix.inputs[0].default_value, 0.27)
        self.assertEqual(pose, self.body.matrix_world)
        self.assertEqual(points, [(o.name, tuple(o.location), tuple(o.color)) for o in self.scene.objects if o.get("vp_role") == "bubble"])
        self.assertEqual(lights, [(o.name, o.data.energy) for o in self.scene.objects if o.type == "LIGHT"])

    def test_off_center_point_samples_original_image_on_current_ray(self):
        image = bpy.data.images.new("Fine_Color_Stripes", width=4, height=2, float_buffer=True)
        image.pixels = [1, 0, 0, 1, 0, 1, 0, 1, 0, 0, 1, 1, 1, 1, 0, 1] * 2
        self.scene["vp_source_image"] = image.name
        self.scene["vp_content"] = "IMAGE"
        self.scene["vp_samples_x"] = self.scene["vp_samples_z"] = 2
        projection.update_projection()
        self.scene["vp_freeze_points"] = True
        bubble = next(o for o in self.scene.objects if o.get("vp_role") == "bubble" and o["pixel_x"] == 0)
        lens = self.body.matrix_world @ Vector(self.body["vp_lens_local"])
        target = self.body.matrix_world @ projection.pixel_target_local(self.body, -0.3, 0)
        bubble.location = lens.lerp(target, 0.65)
        projection.update_projection()
        # u=0.1 is red in the original 4-column image; the coarse cell center is green.
        self.assertEqual(tuple(bubble.color[:3]), (1, 0, 0))
        # Moving the projector shifts this fixed point into a different original-image column.
        self.body.location.x -= 0.6
        projection.update_projection()
        self.assertEqual(tuple(bubble.color[:3]), (0, 1, 0))
        self.body.rotation_euler.z = math.pi
        projection.update_projection()
        self.assertEqual(tuple(bubble.color[:3]), (0, 0, 0))

    def test_color_bars_give_ray_and_point_the_same_hue(self):
        self.scene["vp_content"] = "COLOR_BARS"
        self.scene["vp_show_rays"] = True
        projection.update_projection()
        bubbles = [o for o in self.scene.objects if o.get("vp_role") == "bubble"]
        self.assertGreaterEqual(len({tuple(o.color[:3]) for o in bubbles}), 6)
        first = next(o for o in bubbles if o["pixel_x"] == 0)
        self.assertEqual(tuple(first.color[:3]), (1, 0, 0))
        for beam in (o for o in self.scene.objects if o.get("vp_role") == "beam"):
            bubble = next(o for o in bubbles if o["pixel_x"] == beam["pixel_x"] and o["pixel_z"] == beam["pixel_z"])
            self.assertEqual(tuple(beam.color[:3]), tuple(bubble.color[:3]))

    def test_hide_ray_geometry_preserves_illumination_and_restores_visibility(self):
        self.scene["vp_content"] = "COLOR_BARS"
        self.scene["vp_show_rays"] = True
        projection.update_projection()
        rays = [o for o in self.scene.objects if o.get("vp_role") == "beam"]
        visible_before = {o.name: (o.hide_get(), o.hide_render) for o in rays}
        self.assertTrue(any(not o.hide_render for o in rays if o.get("vp_role") == "beam"))
        bubbles = [o for o in self.scene.objects if o.get("vp_role") == "bubble"]
        points_before = [(o.as_pointer(), tuple(o.location), tuple(o.color), o.hide_get(), o.hide_render) for o in bubbles]
        lights = [o for o in self.scene.objects if o.type == "LIGHT"]
        lights_before = [(o.name, o.data.energy, tuple(o.data.color), o.hide_get(), o.hide_render) for o in lights]
        self.scene["vp_hide_ray_geometry"] = True
        projection.update_projection()
        self.assertTrue(all(o.hide_get() and o.hide_render for o in rays))
        self.assertEqual(points_before, [(o.as_pointer(), tuple(o.location), tuple(o.color), o.hide_get(), o.hide_render) for o in bubbles])
        self.assertEqual(lights_before, [(o.name, o.data.energy, tuple(o.data.color), o.hide_get(), o.hide_render) for o in lights])
        self.assertTrue(self.scene["vp_show_rays"])
        self.scene["vp_hide_ray_geometry"] = False
        projection.update_projection()
        self.assertEqual(visible_before, {o.name: (o.hide_get(), o.hide_render) for o in rays})
        self.scene["vp_show_rays"] = False
        self.scene["vp_hide_ray_geometry"] = True
        projection.update_projection()
        self.scene["vp_hide_ray_geometry"] = False
        projection.update_projection()
        self.assertTrue(all(o.hide_render for o in rays))

    def test_hide_ray_geometry_updates_through_live_timer(self):
        self.scene["vp_show_rays"] = True
        projection.register_projection_controls()
        with patch.object(projection.time, "monotonic", return_value=10.0):
            projection.live_projection_tick()
        with patch.object(projection.time, "monotonic", return_value=11.0):
            projection.live_projection_tick()
        rays = [o for o in self.scene.objects if o.get("vp_role") == "beam"]
        self.assertTrue(any(not o.hide_render for o in rays))
        self.scene["vp_hide_ray_geometry"] = True
        with patch.object(projection.time, "monotonic", return_value=12.0):
            projection.live_projection_tick()
        self.assertTrue(all(o.hide_render and o.hide_get() for o in rays))

    def test_old_scene_gets_hide_option_and_rerun_preserves_choice(self):
        if "vp_hide_ray_geometry" in self.scene:
            del self.scene["vp_hide_ray_geometry"]
        self.scene["vp_show_rays"] = True
        projection.main()
        self.assertIn("vp_hide_ray_geometry", self.scene)
        self.assertFalse(self.scene["vp_hide_ray_geometry"])
        self.scene["vp_hide_ray_geometry"] = True
        projection.main()
        self.assertTrue(self.scene["vp_hide_ray_geometry"])
        self.assertTrue(all(o.hide_render for o in self.scene.objects if o.get("vp_role") in {"beam", "scatter"}))


class MetricProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        projection.build_scene()
        cls.scene = bpy.context.scene
        cls.body = projection.projection_body()

    def setUp(self):
        self.scene["vp_content"] = "COLOR_BARS"
        self.scene["vp_depth_compensation"] = True
        self.scene["vp_image_width_m"], self.scene["vp_image_height_m"] = 1.6, 1.2
        self.scene["vp_freeze_points"] = True
        self.scene.unit_settings.scale_length = 1.0
        projection.update_projection()

    def test_equal_world_width_at_different_depths_has_equal_target_coordinates(self):
        volume = self.scene.objects[self.scene["vp_volume"]]
        center = volume.matrix_world.translation
        near, far = center + Vector((0.3, 0.7, 0.2)), center + Vector((0.3, -0.7, 0.2))
        self.assertEqual(projection.metric_image_sample(self.scene, self.body, near),
                         projection.metric_image_sample(self.scene, self.body, far))
        self.assertNotEqual(projection.project_to_pixel(self.body, near),
                            projection.project_to_pixel(self.body, far))
        nx, nz = self.body["vp_grid"]
        sample = projection.metric_image_sample(self.scene, self.body, near)
        self.assertAlmostEqual(sample[0], (0.3 / 1.6 + 0.5) * nx - 0.5, places=5)
        self.assertAlmostEqual(sample[1], (0.2 / 1.2 + 0.5) * nz - 0.5, places=5)

    def test_compensated_image_texture_and_points_use_same_warp(self):
        image = bpy.data.images.new("MetricTestImage", width=8, height=8, alpha=True)
        image.pixels = [v for y in range(8) for x in range(8) for v in (x / 7, y / 7, 0.25, 1)]
        self.scene["vp_source_image"], self.scene["vp_content"] = image.name, "IMAGE"
        try:
            positions = {o.name: tuple(o.matrix_world.translation) for o in self.scene.objects
                         if o.get("vp_role") == "bubble"}
            projection.update_projection()
            frame = projection.projection_frame(self.scene, self.body)
            nx, nz = self.body["vp_grid"]
            texture = bpy.data.images[self.scene["vp_frame_image"]]
            pixels = list(texture.pixels)
            spot = self.scene.objects[self.scene["vp_spot"]]
            self.assertEqual(spot.data.node_tree.nodes["ProjectionImage"].image, texture)
            for bubble in (o for o in self.scene.objects if o.get("vp_role") == "bubble"):
                self.assertEqual(positions[bubble.name], tuple(bubble.matrix_world.translation))
                sample = projection.project_to_pixel(self.body, bubble.matrix_world.translation)
                if sample is not None:
                    ix, iz = (math.floor(v + 0.5) for v in sample)
                    for actual, expected in zip(bubble.color, frame[ix, iz]):
                        self.assertAlmostEqual(actual, expected, places=5)
                    for actual, expected in zip(pixels[(iz * nx + ix) * 4:(iz * nx + ix) * 4 + 4], frame[ix, iz]):
                        self.assertAlmostEqual(actual, expected, places=5)
            self.scene["vp_depth_compensation"] = False
            projection.update_projection()
            self.assertEqual(spot.data.node_tree.nodes["ProjectionImage"].image, image)
        finally:
            self.scene["vp_content"] = "COLOR_BARS"
            bpy.data.images.remove(image)

    def test_metric_image_boundaries_units_and_pixel_conflicts(self):
        volume = self.scene.objects[self.scene["vp_volume"]]
        center = volume.matrix_world.translation
        self.assertIsNone(projection.metric_image_sample(self.scene, self.body, center + Vector((1, 0, 0))))
        old_unit = self.scene.unit_settings.scale_length
        try:
            self.scene.unit_settings.scale_length = 0.5
            half = projection.metric_image_sample(self.scene, self.body, center + Vector((0.6, 0, 0)))
            self.scene.unit_settings.scale_length = 1.0
            full = projection.metric_image_sample(self.scene, self.body, center + Vector((0.3, 0, 0)))
            for a, b in zip(half, full):
                self.assertAlmostEqual(a, b, places=5)
        finally:
            self.scene.unit_settings.scale_length = old_unit
        bubbles = [o for o in self.scene.objects if o.get("vp_role") == "bubble"]
        old = bubbles[1].location.copy()
        try:
            bubbles[1].location = bubbles[0].location
            projection.update_projection()
            self.assertGreater(self.scene["vp_compensation_conflicts"], 0)
            self.assertEqual(tuple(bubbles[0].color), tuple(bubbles[1].color))
        finally:
            bubbles[1].location = old
            projection.update_projection()

    def test_export_records_metric_coordinates_and_compensation_settings(self):
        old_unit = self.scene.unit_settings.scale_length
        try:
            self.scene.unit_settings.scale_length = 0.5
            folder = ROOT / "tmp" / "metric_export"
            projection.export_projection(folder)
            metadata = json.loads((folder / "projection_settings.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["scene_meters_per_blender_unit"], 0.5)
            self.assertTrue(metadata["parameters"]["vp_depth_compensation"])
            with (folder / "bubble_coordinates.csv").open(encoding="utf-8-sig", newline="") as stream:
                row = next(csv.DictReader(stream))
            bubble = self.scene.objects[row["name"]]
            for axis, key in enumerate(("x_m", "y_m", "z_m")):
                self.assertAlmostEqual(float(row[key]), bubble.matrix_world.translation[axis] * 0.5)
        finally:
            self.scene.unit_settings.scale_length = old_unit


if __name__ == "__main__":
    suite = unittest.TestSuite([unittest.defaultTestLoader.loadTestsFromTestCase(ProjectionTests),
                                unittest.defaultTestLoader.loadTestsFromTestCase(ParametricTests),
                                unittest.defaultTestLoader.loadTestsFromTestCase(OpticalTests),
                                unittest.defaultTestLoader.loadTestsFromTestCase(MetricProjectionTests)])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise RuntimeError("Projection regression tests failed")
