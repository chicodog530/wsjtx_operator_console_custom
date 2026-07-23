from __future__ import annotations

import math


def maidenhead_to_latlon(grid: str) -> tuple[float, float] | None:
    grid = (grid or "").strip().upper()
    if len(grid) < 4:
        return None
    try:
        lon = (ord(grid[0]) - ord("A")) * 20 - 180
        lat = (ord(grid[1]) - ord("A")) * 10 - 90
        lon += int(grid[2]) * 2
        lat += int(grid[3])
        if len(grid) >= 6:
            lon += (ord(grid[4]) - ord("A")) * (5 / 60)
            lat += (ord(grid[5]) - ord("A")) * (2.5 / 60)
            lon += 2.5 / 60
            lat += 1.25 / 60
        else:
            lon += 1.0
            lat += 0.5
        return lat, lon
    except (ValueError, IndexError):
        return None


def distance_bearing(
    source_grid: str,
    target_grid: str,
    unit: str = "mi",
) -> tuple[float | None, float | None]:
    a = maidenhead_to_latlon(source_grid)
    b = maidenhead_to_latlon(target_grid)
    if not a or not b:
        return None, None

    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    dlat = lat2 - lat1
    dlon = lon2 - lon1

    hav = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    )
    angular = 2 * math.atan2(math.sqrt(hav), math.sqrt(1 - hav))
    radius = 3958.7613 if unit == "mi" else 6371.0088
    distance = radius * angular

    y = math.sin(dlon) * math.cos(lat2)
    x = (
        math.cos(lat1) * math.sin(lat2)
        - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    )
    bearing = (math.degrees(math.atan2(y, x)) + 360) % 360
    return distance, bearing
