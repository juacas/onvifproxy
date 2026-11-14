import math
import unittest

import numpy as np
import httpx
import onvif_fov_proxy as proxy

from optical_calibration import fit_optical_calibration


def synthetic_samples(pan_scale=2.2, tilt_scale=1.1, tilt_zero=0.9):
    horizontal_fov = math.radians(80)
    focal = math.tan(horizontal_fov / 2)
    aspect = 16 / 9
    # A distant landmark initially at (0.12, -0.07) in the image.
    initial = np.array([0.12 * focal, -0.07 * focal / aspect, 1])
    initial /= np.linalg.norm(initial)
    c, s = math.cos(tilt_zero), math.sin(tilt_zero)
    target = np.array([initial[0], c * initial[1] - s * initial[2],
                       s * initial[1] + c * initial[2]])
    samples = []
    for pan, tilt in [(0, 0), (0.08, 0), (-0.08, 0), (0, 0.1), (0, -0.1),
                      (0.08, 0.1), (-0.08, -0.1), (0.08, -0.1), (-0.08, 0.1),
                      (0.14, 0.04), (-0.13, -0.04), (0.02, 0.14)]:
        p, t = pan_scale * pan, tilt_zero + tilt_scale * tilt
        cp, sp, ct, st = math.cos(p), math.sin(p), math.cos(t), math.sin(t)
        a = cp * target[0] + sp * target[1]
        b = -sp * target[0] + cp * target[1]
        camera = np.array([a, ct * b + st * target[2], -st * b + ct * target[2]])
        x, y = camera[0] / camera[2] / focal, camera[1] / camera[2] / focal * aspect
        samples.append({"pan": pan, "tilt": tilt, "x": x, "y": y, "aspect": aspect})
    return samples


def boundary_samples():
    points = [
        (0.332472, 0.095477, -0.241777, 0.086698),
        (0.404860, 0.145729, 0.074389, 0.053956),
        (0.110134, 0.005025, -0.947777, 0.443980),
        (0.400207, -0.293970, 0.068249, 0.705919),
        (0.332989, 0.839196, -0.131272, -0.953027),
        (0.152017, -0.391960, -0.914012, 0.896097),
        (0.601861, -0.065327, 0.927728, 0.617787),
        (0.175801, 0.879397, -0.840342, -0.697365),
    ]
    return [dict(zip(("pan", "tilt", "x", "y", "aspect"), (*point, 16 / 9)))
            for point in points]


class OpticalCalibrationTests(unittest.TestCase):
    def test_recovers_synthetic_fov_and_scales(self):
        result = fit_optical_calibration(synthetic_samples())
        self.assertAlmostEqual(result["horizontal_fov_degrees"], 80, delta=0.1)
        self.assertAlmostEqual(result["pan_degrees_per_unit"], math.degrees(2.2), delta=0.1)
        self.assertAlmostEqual(result["tilt_degrees_per_unit"], math.degrees(1.1), delta=0.1)
        self.assertLess(result["rms_error_degrees"], 0.01)

    def test_rejects_insufficient_and_degenerate_samples(self):
        samples = synthetic_samples()
        with self.assertRaises(ValueError):
            fit_optical_calibration(samples[:3])
        for sample in samples:
            sample["tilt"] = 0
        with self.assertRaises(ValueError):
            fit_optical_calibration(samples)

    def test_noisy_data_produces_reasonable_fov(self):
        samples = synthetic_samples()
        rng = np.random.default_rng(42)
        for sample in samples:
            sample["x"] += float(rng.normal(0, 0.0003))
            sample["y"] += float(rng.normal(0, 0.0003))
        result = fit_optical_calibration(samples)
        self.assertAlmostEqual(result["horizontal_fov_degrees"], 80, delta=5)
        self.assertLess(result["rms_error_degrees"], 0.1)

    def test_reverse_axis_directions(self):
        samples = synthetic_samples(-2.2, -1.1, -0.9)
        result = fit_optical_calibration(samples)
        self.assertAlmostEqual(result["horizontal_fov_degrees"], 80, delta=0.2)
        self.assertAlmostEqual(result["pan_degrees_per_unit"], math.degrees(-2.2), delta=0.2)
        self.assertAlmostEqual(result["tilt_degrees_per_unit"], math.degrees(-1.1), delta=0.2)

    def test_rejects_aspect_mismatch_and_nonfinite_data(self):
        samples = synthetic_samples()
        samples[0]["aspect"] = 1.0
        with self.assertRaisesRegex(ValueError, "aspecto"):
            fit_optical_calibration(samples)
        samples[0]["aspect"] = float("nan")
        with self.assertRaisesRegex(ValueError, "finitos"):
            fit_optical_calibration(samples)

    def test_rejects_fov_at_lower_bound_with_explanation(self):
        with self.assertRaisesRegex(ValueError, "límite de FOV de 10°"):
            fit_optical_calibration(boundary_samples())


class OpticalApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_fit_via_web_endpoint(self):
        app = proxy.create_camera_app("test", {"ip": "not-used", "proxy_port": 8211})
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://proxy"
        ) as client:
            response = await client.post("/calibration/optical", json={"samples": synthetic_samples()})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertAlmostEqual(response.json()["horizontal_fov_degrees"], 80, delta=0.1)

    async def test_real_fit_failure_explains_fov_boundary(self):
        app = proxy.create_camera_app("test", {"ip": "not-used", "proxy_port": 8211})
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://proxy"
        ) as client:
            response = await client.post("/calibration/optical", json={"samples": boundary_samples()})
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("límite de FOV de 10°", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
