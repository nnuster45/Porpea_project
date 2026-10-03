"""Grid tiling and distance helpers."""

import math
from dataclasses import dataclass

import numpy as np

EARTH_RADIUS_M = 6_371_000


@dataclass(frozen=True)
class Rect:
    south: float
    west: float
    north: float
    east: float

    def to_google(self):
        return {
            "rectangle": {
                "low": {"latitude": self.south, "longitude": self.west},
                "high": {"latitude": self.north, "longitude": self.east},
            }
        }

    def subdivide(self):
        mid_lat = (self.south + self.north) / 2
        mid_lng = (self.west + self.east) / 2
        return [
            Rect(self.south, self.west, mid_lat, mid_lng),
            Rect(self.south, mid_lng, mid_lat, self.east),
            Rect(mid_lat, self.west, self.north, mid_lng),
            Rect(mid_lat, mid_lng, self.north, self.east),
        ]


def tiles(bbox, step):
    """Split a bbox dict (south/north/west/east) into rectangles of about `step` degrees."""
    n_lat = max(1, math.ceil(round((bbox["north"] - bbox["south"]) / step, 9)))
    n_lng = max(1, math.ceil(round((bbox["east"] - bbox["west"]) / step, 9)))
    d_lat = (bbox["north"] - bbox["south"]) / n_lat
    d_lng = (bbox["east"] - bbox["west"]) / n_lng
    out = []
    for i in range(n_lat):
        for j in range(n_lng):
            out.append(
                Rect(
                    round(bbox["south"] + i * d_lat, 6),
                    round(bbox["west"] + j * d_lng, 6),
                    round(bbox["south"] + (i + 1) * d_lat, 6),
                    round(bbox["west"] + (j + 1) * d_lng, 6),
                )
            )
    return out


def haversine_m(lat1, lng1, lat2, lng2):
    """Great-circle distance in metres; lat2/lng2 may be numpy arrays."""
    lat1, lng1, lat2, lng2 = map(np.radians, (lat1, lng1, lat2, lng2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lng2 - lng1) / 2) ** 2
    return 2 * EARTH_RADIUS_M * np.arcsin(np.sqrt(a))
