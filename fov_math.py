def calculate_fov_move(dx: float, dy: float, cfg: dict) -> tuple[float, float, float]:
    duration_x = abs(dx) * cfg.get("pan_seconds", 1.6)
    duration_y = abs(dy) * cfg.get("tilt_seconds", 0.9)
    max_duration = max(duration_x, duration_y)
    if max_duration < 0.03:
        return 0.0, 0.0, 0.0
    speed = cfg.get("speed", 0.8)
    vx = (speed if dx > 0 else -speed) * (duration_x / max_duration) if abs(dx) > 0.01 else 0.0
    vy = (speed if dy > 0 else -speed) * (duration_y / max_duration) if abs(dy) > 0.01 else 0.0
    return round(vx, 3), round(vy, 3), max_duration


def calculate_sequential_fov_moves(dx: float, dy: float, cfg: dict) -> list[tuple[float, float, float]]:
    duration_x = abs(dx) * cfg.get("pan_seconds", 1.6)
    duration_y = abs(dy) * cfg.get("tilt_seconds", 0.9)
    if max(duration_x, duration_y) < 0.03:
        return []
    speed = cfg.get("speed", 0.8)
    moves = []
    if abs(dy) > 0.01:
        moves.append((0.0, round(speed if dy > 0 else -speed, 3), duration_y))
    if abs(dx) > 0.01:
        moves.append((round(speed if dx > 0 else -speed, 3), 0.0, duration_x))
    return moves
