"""Fit an ideal pinhole camera on a two-axis mount from a fixed scene point."""

import math

import numpy as np
from scipy.optimize import least_squares


def _rays(values, params):
    log_tan_half, pan_scale, tilt_scale, tilt_zero, target_yaw, target_pitch = params
    half_width = np.exp(log_tan_half)
    pan = (values[:, 0] - values[0, 0]) * pan_scale
    tilt = tilt_zero + (values[:, 1] - values[0, 1]) * tilt_scale
    x = values[:, 2] * half_width
    y = values[:, 3] * half_width / values[:, 4]
    vectors = np.column_stack((x, y, np.ones(len(values))))
    vectors /= np.linalg.norm(vectors, axis=1)[:, None]
    ct, st = np.cos(tilt), np.sin(tilt)
    cp, sp = np.cos(pan), np.sin(pan)
    rotated_y = ct * vectors[:, 1] - st * vectors[:, 2]
    rotated_z = st * vectors[:, 1] + ct * vectors[:, 2]
    rays = np.column_stack((cp * vectors[:, 0] - sp * rotated_y,
                            sp * vectors[:, 0] + cp * rotated_y, rotated_z))
    target = np.array([math.cos(target_pitch) * math.cos(target_yaw),
                       math.cos(target_pitch) * math.sin(target_yaw), math.sin(target_pitch)])
    return rays, target


def fit_optical_calibration(samples):
    """Estimate FOV and axis scales; fail when observations do not identify them."""
    if not isinstance(samples, list) or not 8 <= len(samples) <= 60:
        raise ValueError("Se necesitan entre 8 y 60 muestras del mismo punto.")
    rows = []
    for sample in samples:
        if not isinstance(sample, dict):
            raise ValueError("Cada muestra debe ser un objeto.")
        values = [sample.get(key) for key in ("pan", "tilt", "x", "y", "aspect")]
        if any(isinstance(v, bool) or not isinstance(v, (int, float))
               or not math.isfinite(v) for v in values):
            raise ValueError("Las muestras deben contener números finitos.")
        pan, tilt, x, y, aspect = values
        if abs(pan) > 1 or abs(tilt) > 1 or abs(x) > 1 or abs(y) > 1 or not 0.5 <= aspect <= 4:
            raise ValueError("Una muestra contiene coordenadas o aspecto fuera de rango.")
        rows.append(values)
    data = np.asarray(rows, dtype=float)
    if np.ptp(data[:, 4]) > 0.01 * np.mean(data[:, 4]):
        raise ValueError("Todas las imágenes deben tener la misma relación de aspecto.")
    if np.ptp(data[:, 0]) < 0.04 or np.ptp(data[:, 1]) < 0.04:
        raise ValueError("Toma muestras más separadas variando tanto pan como tilt.")
    if np.ptp(data[:, 2]) < 0.08 or np.ptp(data[:, 3]) < 0.08:
        raise ValueError("El punto debe aparecer en zonas más variadas de la imagen.")

    def residual(params):
        log_tan_half, pan_scale, tilt_scale, tilt_zero, target_yaw, target_pitch = params
        pan = (data[:, 0] - data[0, 0]) * pan_scale
        tilt = tilt_zero + (data[:, 1] - data[0, 1]) * tilt_scale
        target = np.array([math.cos(target_pitch) * math.cos(target_yaw),
                           math.cos(target_pitch) * math.sin(target_yaw), math.sin(target_pitch)])
        cp, sp, ct, st = np.cos(pan), np.sin(pan), np.cos(tilt), np.sin(tilt)
        x = cp * target[0] + sp * target[1]
        y = -sp * target[0] + cp * target[1]
        camera_y = ct * y + st * target[2]
        camera_z = -st * y + ct * target[2]
        depth = np.maximum(camera_z, 0.01)
        half_width = np.exp(log_tan_half)
        return np.concatenate((
            x / depth / half_width - data[:, 2],
            camera_y / depth / half_width * data[:, 4] - data[:, 3],
            10 * np.minimum(camera_z - 0.01, 0),
        ))

    lower = [math.log(math.tan(math.radians(10) / 2)), -2 * math.pi, -2 * math.pi,
             -math.pi, -math.pi, -math.pi / 2]
    upper = [math.log(math.tan(math.radians(160) / 2)), 2 * math.pi, 2 * math.pi,
             math.pi, math.pi, math.pi / 2]
    solutions = []
    for pan_scale in (-2.5, 2.5):
        for tilt_scale in (-1.0, 1.0):
            for tilt_zero in (-1.2, 0.0, 1.2):
                tangent = math.tan(math.radians(80) / 2)
                vx = data[0, 2] * tangent
                vy = data[0, 3] * tangent / data[0, 4]
                rotated_y = math.cos(tilt_zero) * vy - math.sin(tilt_zero)
                rotated_z = math.sin(tilt_zero) * vy + math.cos(tilt_zero)
                initial = [math.log(tangent), pan_scale, tilt_scale, tilt_zero,
                           math.atan2(rotated_y, vx), math.atan2(rotated_z, math.hypot(vx, rotated_y))]
                result = least_squares(residual, initial, bounds=(lower, upper), max_nfev=1500)
                if result.success:
                    rays, target = _rays(data, result.x)
                    angles = np.arccos(np.clip(rays @ target, -1, 1))
                    rms = float(np.sqrt(np.mean(angles ** 2)))
                    pixel_rms = float(np.sqrt(np.mean(result.fun ** 2)))
                    solutions.append((pixel_rms, rms, result))
    if not solutions:
        raise ValueError("El ajuste no convergió; revisa las muestras.")
    solutions.sort(key=lambda item: item[0])
    pixel_rms, rms, result = solutions[0]
    if result.active_mask[0]:
        limit = 10 if result.active_mask[0] < 0 else 160
        raise ValueError(
            f"El ajuste alcanzó el límite de FOV de {limit}° y no es fiable. "
            "Comprueba el punto marcado y el retraso de las imágenes; "
            "si persiste, toma más muestras o revisa las hipótesis del modelo."
        )
    singular = np.linalg.svd(result.jac, compute_uv=False)
    if singular[-1] < singular[0] * 1e-4:
        raise ValueError("Las muestras no permiten identificar el FOV y los ejes; toma más posiciones variadas.")
    if rms > math.radians(3):
        raise ValueError(f"El error angular es demasiado alto ({math.degrees(rms):.2f}°); revisa las muestras y el modelo.")
    horizontal = math.degrees(2 * math.atan(math.exp(result.x[0])))
    vertical = math.degrees(2 * math.atan(math.exp(result.x[0]) / float(np.mean(data[:, 4]))))
    for other_pixel_rms, _, other in solutions[1:]:
        other_fov = math.degrees(2 * math.atan(math.exp(other.x[0])))
        if other_pixel_rms < max(pixel_rms * 1.2, 0.00005) and abs(other_fov - horizontal) > 5:
            raise ValueError("Los datos admiten varios FOV posibles; toma más muestras en otras posiciones.")
    if abs(result.x[1]) < 0.1 or abs(result.x[2]) < 0.1:
        raise ValueError("El ajuste produjo una escala angular de eje inválida.")
    pan_scale, reference_tilt = result.x[1], result.x[3]
    if reference_tilt > math.pi / 2:
        reference_tilt -= math.pi
        pan_scale = -pan_scale
    elif reference_tilt < -math.pi / 2:
        reference_tilt += math.pi
        pan_scale = -pan_scale
    return {
        "horizontal_fov_degrees": horizontal,
        "vertical_fov_degrees": vertical,
        "pan_degrees_per_unit": math.degrees(pan_scale),
        "tilt_degrees_per_unit": math.degrees(result.x[2]),
        "reference_pan": float(data[0, 0]),
        "reference_tilt": float(data[0, 1]),
        "reference_axis_tilt_degrees": math.degrees(reference_tilt),
        "rms_error_degrees": math.degrees(rms),
        "samples": len(data),
    }
