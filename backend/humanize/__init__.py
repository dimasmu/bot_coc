"""Humanization: random offsets, delays, and Bezier curves to avoid detection."""

import math
import random
import asyncio


def gaussian_offset(center_x: int, center_y: int, sigma: int = 5) -> tuple[int, int]:
    """Add Gaussian-distributed random offset to a tap coordinate.
    sigma=5 gives ~95% of taps within ±10px of center.
    """
    x = int(center_x + random.gauss(0, sigma))
    y = int(center_y + random.gauss(0, sigma))
    return (x, y)


def random_delay(min_ms: float, max_ms: float) -> float:
    """Return a random delay in seconds between min_ms and max_ms."""
    return random.uniform(min_ms, max_ms)


async def human_delay(min_s: float = 0.3, max_s: float = 1.5):
    """Asynchronously sleep for a random duration."""
    await asyncio.sleep(random.uniform(min_s, max_s))


async def human_tap(adb, x: int, y: int, sigma: int = 5):
    """Tap with Gaussian-offset coordinates and a small delay."""
    tx, ty = gaussian_offset(x, y, sigma)
    await adb.tap(tx, ty)
    await asyncio.sleep(random.uniform(0.05, 0.15))


def bezier_swipe_points(
    start_x: int, start_y: int,
    end_x: int, end_y: int,
    num_points: int = 20,
    control_offset: int = 50,
) -> list[tuple[int, int]]:
    """Generate a cubic Bezier curve swipe path from start to end.

    Adds random control point offsets to create natural-looking curves.
    """
    cp1_x = start_x + random.randint(-control_offset, control_offset)
    cp1_y = start_y + random.randint(-control_offset, control_offset)
    cp2_x = end_x + random.randint(-control_offset, control_offset)
    cp2_y = end_y + random.randint(-control_offset, control_offset)

    points = []
    for i in range(num_points + 1):
        t = i / num_points
        x = (1 - t) ** 3 * start_x + 3 * (1 - t) ** 2 * t * cp1_x + 3 * (1 - t) * t ** 2 * cp2_x + t ** 3 * end_x
        y = (1 - t) ** 3 * start_y + 3 * (1 - t) ** 2 * t * cp1_y + 3 * (1 - t) * t ** 2 * cp2_y + t ** 3 * end_y
        points.append((int(x), int(y)))
    return points


async def human_swipe(adb, start_x: int, start_y: int, end_x: int, end_y: int, duration_ms: int = 500):
    """Execute a human-like swipe using Bezier curve path via ADB input swipe."""
    import asyncio
    # ADB swipe is instantaneous, so we just use it directly with Bezier endpoints
    # The curve variation comes from randomizing the start/end slightly
    sx, sy = gaussian_offset(start_x, end_y, sigma=3)
    ex, ey = gaussian_offset(end_x, end_y, sigma=3)
    await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: adb._device.shell(f"input swipe {sx} {sy} {ex} {ey} {duration_ms}"),
    )
    await human_delay(0.1, 0.3)
