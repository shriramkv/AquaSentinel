"""Lake geometry: outline, named places, zones and survey routes.

Everything is computed in local metres (x east, y north) around the outline's centre and
converted to latitude/longitude at the edges of the system.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from . import config

ZONE_COLS, ZONE_ROWS = 3, 2
ZONE_NAMES = {(0, 1): ("NW", "North-west"), (1, 1): ("N", "North"), (2, 1): ("NE", "North-east"),
              (0, 0): ("SW", "South-west"), (1, 0): ("S", "South"), (2, 0): ("SE", "South-east")}


@dataclass(frozen=True)
class Place:
    id: str
    name: str
    kind: str
    x: float
    y: float


class Lake:
    def __init__(self, path: Path = config.LAKE_FILE):
        fc = json.loads(Path(path).read_text())
        props = fc.get("properties", {})
        self.name: str = props.get("name", "Lake")
        self.demo: bool = bool(props.get("demo", False))
        poly = next(f for f in fc["features"] if f["geometry"]["type"] == "Polygon")
        ring_ll = poly["geometry"]["coordinates"][0]
        if ring_ll[0] == ring_ll[-1]:
            ring_ll = ring_ll[:-1]
        self.lon0 = sum(p[0] for p in ring_ll) / len(ring_ll)
        self.lat0 = sum(p[1] for p in ring_ll) / len(ring_ll)
        self._kx = 111320 * math.cos(math.radians(self.lat0))
        self._ky = 110540
        self.ring = [self.to_xy(lat, lon) for lon, lat in ring_ll]
        xs, ys = [p[0] for p in self.ring], [p[1] for p in self.ring]
        self.bbox = (min(xs), min(ys), max(xs), max(ys))
        self.places: dict[str, Place] = {}
        for f in fc["features"]:
            if f["geometry"]["type"] == "Point":
                lon, lat = f["geometry"]["coordinates"]
                x, y = self.to_xy(lat, lon)
                p = f["properties"]
                self.places[p["id"]] = Place(p["id"], p.get("name", p["id"]), p.get("kind", "place"), x, y)
        # Interior sample points on a 25 m grid, grouped by zone (used for zone means and centroids)
        self.zone_points: dict[str, list[tuple[float, float]]] = {z[0]: [] for z in ZONE_NAMES.values()}
        x0, y0, x1, y1 = self.bbox
        y = y0
        while y <= y1:
            x = x0
            while x <= x1:
                if self.contains(x, y):
                    self.zone_points[self.zone_of(x, y)].append((x, y))
                x += 25
            y += 25
        self.zones = []
        for zid, zname in ZONE_NAMES.values():
            pts = self.zone_points[zid]
            if not pts:
                continue
            cx, cy = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
            lat, lon = self.to_ll(cx, cy)
            self.zones.append({"id": zid, "name": zname, "x": cx, "y": cy, "lat": lat, "lon": lon})

    # ------------------------------------------------------------------ conversions
    def to_xy(self, lat: float, lon: float) -> tuple[float, float]:
        return (lon - self.lon0) * self._kx, (lat - self.lat0) * self._ky

    def to_ll(self, x: float, y: float) -> tuple[float, float]:
        return round(self.lat0 + y / self._ky, 6), round(self.lon0 + x / self._kx, 6)

    # ------------------------------------------------------------------ polygon tests
    def contains(self, x: float, y: float) -> bool:
        inside, n = False, len(self.ring)
        for i in range(n):
            xa, ya = self.ring[i]
            xb, yb = self.ring[(i + 1) % n]
            if (ya > y) != (yb > y) and x < (xb - xa) * (y - ya) / (yb - ya) + xa:
                inside = not inside
        return inside

    def nearest_shore(self, x: float, y: float) -> tuple[float, float, float]:
        """Closest point on the outline: (x, y, distance in metres)."""
        best = (x, y, float("inf"))
        n = len(self.ring)
        for i in range(n):
            xa, ya = self.ring[i]
            xb, yb = self.ring[(i + 1) % n]
            dx, dy = xb - xa, yb - ya
            t = max(0.0, min(1.0, ((x - xa) * dx + (y - ya) * dy) / (dx * dx + dy * dy)))
            px, py = xa + t * dx, ya + t * dy
            d = math.hypot(x - px, y - py)
            if d < best[2]:
                best = (px, py, d)
        return best

    def shore_distance(self, x: float, y: float) -> float:
        return self.nearest_shore(x, y)[2]

    def navigable(self, x: float, y: float, margin: float = 8.0) -> bool:
        """Inside the lake and at least `margin` metres off the shore."""
        return self.contains(x, y) and self.shore_distance(x, y) >= margin

    def pull_inside(self, x: float, y: float, margin: float = 15.0) -> tuple[float, float]:
        """Move a point (e.g. an inlet on the shoreline) toward the lake centre until it is navigable."""
        for i in range(200):
            if self.navigable(x, y, margin):
                break
            d = math.hypot(x, y) or 1.0
            x, y = x - 5 * x / d, y - 5 * y / d
        return x, y

    # ------------------------------------------------------------------ zones and places
    def zone_of(self, x: float, y: float) -> str:
        x0, y0, x1, y1 = self.bbox
        c = min(ZONE_COLS - 1, max(0, int((x - x0) / (x1 - x0) * ZONE_COLS)))
        r = min(ZONE_ROWS - 1, max(0, int((y - y0) / (y1 - y0) * ZONE_ROWS)))
        return ZONE_NAMES[(c, r)][0]

    def nearest_place(self, x: float, y: float, kinds: tuple[str, ...] = ("inlet", "outfall")) -> tuple[Place, float] | None:
        cands = [p for p in self.places.values() if p.kind in kinds]
        if not cands:
            return None
        p = min(cands, key=lambda p: math.hypot(p.x - x, p.y - y))
        return p, math.hypot(p.x - x, p.y - y)

    # ------------------------------------------------------------------ routes
    def transects(self, spacing: float = 110.0, zone: str | None = None, margin: float = 25.0) -> list[tuple[float, float]]:
        """Lawnmower route: east-west lines `spacing` metres apart, limited to one zone if given."""
        x0, y0, x1, y1 = self.bbox
        spacing = max(40.0, min(400.0, spacing))
        pts: list[tuple[float, float]] = []
        y, flip = y0 + spacing / 2, False
        while y < y1:
            xs = [x for x in _frange(x0, x1, 5.0)
                  if self.navigable(x, y, margin) and (zone is None or self.zone_of(x, y) == zone)]
            if xs:
                a, b = (min(xs), y), (max(xs), y)
                pts += [b, a] if flip else [a, b]
                flip = not flip
            y += spacing
        return pts

    def path_ok(self, a: tuple[float, float], b: tuple[float, float], margin: float = 5.0) -> bool:
        """True when the straight line a-b stays on navigable water."""
        n = max(2, int(math.hypot(b[0] - a[0], b[1] - a[1]) / 10))
        return all(self.navigable(a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n, margin) for i in range(n + 1))

    def describe(self) -> dict:
        """Map summary for the dashboard and for the Planner agent."""
        ring_ll = [self.to_ll(x, y) for x, y in self.ring]
        x0, y0, x1, y1 = self.bbox
        return {
            "name": self.name, "demo": self.demo,
            "outline": [[lat, lon] for lat, lon in ring_ll],
            "centre": list(self.to_ll(0, 0)),
            "size_m": {"east_west": round(x1 - x0), "north_south": round(y1 - y0)},
            "places": [{"id": p.id, "name": p.name, "kind": p.kind,
                        "lat": self.to_ll(p.x, p.y)[0], "lon": self.to_ll(p.x, p.y)[1]} for p in self.places.values()],
            "zones": [{k: z[k] for k in ("id", "name", "lat", "lon")} for z in self.zones],
        }


def _frange(a: float, b: float, step: float):
    while a <= b:
        yield a
        a += step


lake = Lake()
