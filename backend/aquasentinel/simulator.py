"""Lake and boat simulator for the demo.

World : clean-lake baseline for six parameters plus pollution plumes from the scenarios the
        operator triggers. Also produces the 24 h fixed-point series per zone that the
        fish-health model needs.
Boat  : follows waypoints, samples the world, and can trace a plume up its gradient to the source.

The hidden "truth" of a reading (how much the plume changes the water there, relative to the
MATERIAL thresholds) is used only to score the detector on the Benchmarks page. It is never sent
to the agents.
"""
from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable
from zoneinfo import ZoneInfo

import numpy as np

from . import config
from .geo import Lake

# Change in each parameter at the discharge point (plume strength 1.0). Assumed signatures for the demo.
SCENARIOS = {
    "sewage": {"label": "Sewage inflow", "place": "west_outfall", "length": 230, "width": 110,
               "delta": {"do": -4.6, "ec": 750, "nitrate": 14, "ph": -0.5, "turbidity": 85, "temp": 0.8}},
    "farm_runoff": {"label": "Farm runoff", "place": "east_inlet", "length": 230, "width": 120,
                    "delta": {"do": -2.4, "ec": 150, "nitrate": 22, "ph": 0.2, "turbidity": 110, "temp": 0.0}},
    "industrial": {"label": "Industrial discharge", "place": "southwest_drain", "length": 210, "width": 100,
                   "delta": {"do": -2.2, "ec": 1400, "nitrate": 2, "ph": -2.2, "turbidity": 20, "temp": 3.5}},
    "algal_bloom": {"label": "Algal bloom", "place": "south_bay", "length": 190, "width": 170,
                    "delta": {"do": 2.6, "ec": 0, "nitrate": -2.0, "ph": 1.3, "turbidity": 38, "temp": 0.4}},
}
NOISE = {"ph": 0.03, "do": 0.08, "temp": 0.05, "ec": 4.0, "nitrate": 0.25, "turbidity": 0.8}
LIMITS = {"ph": (2.0, 12.0), "do": (0.2, 16.0), "temp": (5.0, 42.0), "ec": (20.0, 5000.0),
          "nitrate": (0.0, 200.0), "turbidity": (0.5, 900.0)}
# A reading counts as "polluted" in the benchmark when the plume shifts at least one parameter by this much.
# Defined in physical units, independently of the detector's own thresholds.
MATERIAL = {"ph": 0.3, "do": 1.0, "temp": 1.0, "ec": 100.0, "nitrate": 3.0, "turbidity": 10.0}
TRUTH_MIN = 1.0           # truth = largest plume-caused shift / MATERIAL; polluted when >= 1
BOAT_SPEED = 1.5          # m/s, the real hull's cruising speed; the demo multiplies it by `speed_scale`


@dataclass
class Scenario:
    id: int
    kind: str
    place_id: str
    x: float
    y: float
    started: float                 # epoch seconds; may be in the past ("discharge began 6 h ago")
    ramp_s: float                  # seconds to reach full strength
    ax: float = 0.0                # unit vector from the source into the lake
    ay: float = 0.0

    def shape(self, x: float, y: float) -> float:
        """Spatial plume strength 0..1 (1 at the source), stretched into the lake."""
        sc = SCENARIOS[self.kind]
        dx, dy = x - self.x, y - self.y
        along, cross = dx * self.ax + dy * self.ay, -dx * self.ay + dy * self.ax
        la = sc["length"] if along > 0 else sc["length"] * 0.4
        return math.exp(-math.hypot(along / la, cross / sc["width"]))

    def ramp(self, t: float) -> float:
        return max(0.0, min(1.0, (t - self.started) / self.ramp_s))


class World:
    def __init__(self, lake: Lake, seed: int = 7):
        self.lake = lake
        self.rng = np.random.default_rng(seed)
        self.scenarios: list[Scenario] = []
        self._next_id = 1
        self._tz = ZoneInfo(config.POND_TZ)

    # ------------------------------------------------------------------ scenarios
    def add_scenario(self, kind: str, place_id: str | None = None, hours_ago: float = 0.0) -> Scenario:
        if kind not in SCENARIOS:
            raise ValueError(f"Unknown scenario '{kind}'")
        place = self.lake.places.get(place_id or SCENARIOS[kind]["place"])
        if place is None:
            raise ValueError(f"Unknown place '{place_id}'")
        hours_ago = max(0.0, min(24.0, hours_ago))
        d = math.hypot(place.x, place.y) or 1.0
        sc = Scenario(self._next_id, kind, place.id, place.x, place.y, time.time() - hours_ago * 3600,
                      ramp_s=3600.0 if hours_ago > 0 else 20.0, ax=-place.x / d, ay=-place.y / d)
        self._next_id += 1
        self.scenarios = [s for s in self.scenarios if s.place_id != place.id] + [sc]
        return sc

    def clear_scenarios(self) -> None:
        self.scenarios = []

    def describe_scenarios(self) -> list[dict]:
        out = []
        for s in self.scenarios:
            lat, lon = self.lake.to_ll(s.x, s.y)
            out.append({"id": s.id, "kind": s.kind, "label": SCENARIOS[s.kind]["label"], "place": s.place_id,
                        "place_name": self.lake.places[s.place_id].name, "lat": lat, "lon": lon, "started": s.started})
        return out

    # ------------------------------------------------------------------ fields
    def _hour(self, t: float) -> float:
        d = datetime.fromtimestamp(t, self._tz)
        return d.hour + d.minute / 60 + d.second / 3600

    def baseline(self, x: float, y: float, t: float) -> dict[str, float]:
        day = math.sin(2 * math.pi * (self._hour(t) - 9) / 24)        # +1 mid-afternoon, -1 before dawn
        return {
            "ph": 7.45 + 0.15 * day,
            "do": 6.9 + 0.6 * day - 0.2 * x / 500,
            "temp": 28.5 + 1.8 * day + 0.3 * y / 300,
            "ec": 420 + 25 * x / 500,
            "nitrate": 3.8 + 0.6 * y / 300,
            "turbidity": 11 + 2 * math.sin(x / 170) * math.cos(y / 130),
        }

    def strength(self, x: float, y: float, t: float) -> tuple[float, Scenario | None]:
        """Strongest plume at this point and the scenario causing it."""
        best, who = 0.0, None
        for s in self.scenarios:
            c = s.shape(x, y) * s.ramp(t)
            if c > best:
                best, who = c, s
        return best, who

    def sample(self, x: float, y: float, t: float, noise: bool = True) -> tuple[dict[str, float], float]:
        """Sensor readings at a point and the hidden truth there (see MATERIAL)."""
        v = self.baseline(x, y, t)
        shift = dict.fromkeys(v, 0.0)
        for s in self.scenarios:
            c = s.shape(x, y) * s.ramp(t)
            for p, dv in SCENARIOS[s.kind]["delta"].items():
                shift[p] += dv * c
        truth = max(abs(shift[p]) / MATERIAL[p] for p in v)
        for p in v:
            v[p] += shift[p]
        for p in v:
            if noise:
                v[p] += float(self.rng.normal(0, NOISE[p]))
            lo, hi = LIMITS[p]
            v[p] = round(min(hi, max(lo, v[p])), 3)
        return v, truth

    # ------------------------------------------------------------------ fixed-point history for the fish-health model
    def station_rows(self, zone: dict, now: float, steps: int = 288) -> list[dict]:
        """24 h of 5-minute readings for one zone, in the units the fish-health model expects.

        A moving boat cannot supply 24 h at every point, so the simulator generates this series for
        a virtual fixed station per zone, placed at the zone's most exposed point (in practice an
        inlet or outfall, where a moored node would be installed), because harm to aquatic life
        starts locally.
        """
        zid = zone["id"]
        pts = self.lake.zone_points[zid]
        zone_shape = {s.id: max(s.shape(px, py) for px, py in pts) for s in self.scenarios}
        last = int(now // 300)
        zi = sum(ord(c) for c in zid)
        rows = []
        for k in range(last - steps + 1, last + 1):
            t = k * 300.0
            v = self.baseline(zone["x"], zone["y"], t)
            daylight = max(0.0, math.sin(2 * math.pi * (self._hour(t) - 6) / 24))
            for s in self.scenarios:
                c = zone_shape[s.id] * s.ramp(t)
                for p, dv in SCENARIOS[s.kind]["delta"].items():
                    # A bloom raises pH by day and lets it fall at night: the daily swing the model looks for
                    v[p] += dv * c * (1.6 * daylight - 0.3 if s.kind == "algal_bloom" and p == "ph" else 1.0)
            n = np.random.default_rng([zi, k]).normal(0, 1, 4)
            rows.append({
                "timestamp": datetime.fromtimestamp(t, self._tz).replace(tzinfo=None),
                "temperature_c": round(v["temp"] + 0.05 * n[0], 3),
                "tds_ppm": round(max(1.0, v["ec"] * config.TDS_PER_EC + 2.5 * n[1]), 2),
                "turbidity_ntu": round(max(0.5, v["turbidity"] + 0.5 * n[2]), 2),
                "ph": round(v["ph"] + 0.02 * n[3], 3),
            })
        return rows


@dataclass
class Trace:
    params: list[str]
    anomaly_id: int
    resume_mode: str
    samples: deque = field(default_factory=lambda: deque(maxlen=50))
    ema: float | None = None
    best: float = -1.0
    best_xy: tuple[float, float] | None = None
    since_best: float = 0.0
    dist: float = 0.0
    zig: int = 1
    zig_d: float = 0.0
    cast: float = 45.0
    cast_heading: float = 0.0
    base: float | None = None
    on_shore: bool = False
    turned_back: bool = False


class Boat:
    """Waypoint follower with an onboard plume-tracing controller."""

    SUBSTEP = 3.0          # metres between onboard control samples

    def __init__(self, lake: Lake, world: World):
        self.lake, self.world = lake, world
        dock = lake.places.get("dock")
        self.home = lake.pull_inside(dock.x, dock.y, 20) if dock else (0.0, 0.0)
        self.x, self.y = self.home
        self.heading = -math.pi / 2
        self.mode = "idle"                  # idle | survey | trace | hold | paused
        self.waypoints: list[tuple[float, float]] = []
        self.idx = 0
        self.speed_scale = 8.0
        self.battery = 100.0
        self.odometer = 0.0
        self.trace: Trace | None = None
        self.trace_path: list[tuple[float, float]] = []
        self._hold_until = 0.0
        self._paused_from = "idle"
        # Callbacks set by the hub
        self.score_fn: Callable[[dict, list[str] | None], float] = lambda reading, params: 0.0
        self.on_trace_done: Callable[[int, tuple[float, float], str], None] = lambda *a: None
        self.on_mission_done: Callable[[], None] = lambda: None

    # ------------------------------------------------------------------ commands
    def set_route(self, pts: list[tuple[float, float]]) -> None:
        self.waypoints, self.idx = list(pts), 0
        self.trace = None
        self.mode = "survey" if pts else "idle"

    def start_trace(self, params: list[str], anomaly_id: int) -> bool:
        if self.mode == "trace":
            return False
        resume = self.mode if self.mode in ("survey", "idle") else "idle"
        self.trace = Trace(params=params, anomaly_id=anomaly_id, resume_mode=resume)
        # First leg: cast across the current track so the gradient can be measured in two dimensions
        for side in (math.pi / 2, -math.pi / 2):
            h = self.heading + side
            if self.lake.navigable(self.x + 40 * math.cos(h), self.y + 40 * math.sin(h)):
                self.trace.cast_heading = h
                break
        else:
            self.trace.cast = 0.0
        self.trace_path = [(self.x, self.y)]
        self.mode = "trace"
        return True

    def pause(self) -> None:
        if self.mode != "paused":
            self._paused_from, self.mode = self.mode, "paused"

    def resume(self) -> None:
        if self.mode == "paused":
            self.mode = self._paused_from

    def go_home(self) -> None:
        self.set_route([self.home])

    # ------------------------------------------------------------------ motion
    def step(self, dt: float, now: float) -> None:
        """Advance the boat by `dt` wall-clock seconds."""
        if self.mode == "hold" and now >= self._hold_until:
            self.mode = self.trace.resume_mode if self.trace else "idle"
            self.trace = None
        if self.mode not in ("survey", "trace"):
            return
        remaining = BOAT_SPEED * self.speed_scale * dt
        while remaining > 1e-6 and self.mode in ("survey", "trace"):
            ds = min(self.SUBSTEP, remaining)
            remaining -= ds
            if self.mode == "survey":
                self._survey_step(ds)
            else:
                self._trace_step(ds, now)

    def _move(self, heading: float, ds: float) -> None:
        self.heading = heading
        self.x += ds * math.cos(heading)
        self.y += ds * math.sin(heading)
        self.odometer += ds
        self.battery = max(5.0, self.battery - ds * 0.0012)

    def _survey_step(self, ds: float) -> None:
        if self.idx >= len(self.waypoints):
            self.mode = "idle"
            self.on_mission_done()
            return
        tx, ty = self.waypoints[self.idx]
        d = math.hypot(tx - self.x, ty - self.y)
        if d <= ds:
            self.x, self.y = tx, ty
            self.odometer += d
            self.idx += 1
            return
        self._move(math.atan2(ty - self.y, tx - self.x), ds)

    def _trace_step(self, ds: float, now: float) -> None:
        tr = self.trace
        reading, _ = self.world.sample(self.x, self.y, now)
        # All parameters contribute: at the edge of a plume one parameter alone is too noisy to steer by
        raw = self.score_fn(reading, None)
        tr.ema = raw if tr.ema is None else 0.75 * tr.ema + 0.25 * raw
        tr.samples.append((self.x, self.y, tr.ema))
        if tr.ema > tr.best * 1.03 + 0.5:
            tr.best, tr.best_xy, tr.since_best = tr.ema, (self.x, self.y), 0.0
        else:
            tr.since_best += ds
        tr.dist += ds
        # Open water: give up after 160 m without improvement. Along the shore the peak is passed quickly.
        if tr.since_best > (70 if tr.on_shore else 160) and tr.dist > 200:
            if tr.on_shore and not tr.turned_back:
                # The signal fell away along the bank: search the other direction once before settling
                tr.turned_back, tr.base = True, tr.base + math.pi
                tr.since_best = -tr.since_best
                return
            return self._finish_trace("passed the peak along the shoreline" if tr.on_shore else "signal stopped rising")
        if tr.dist > 1800:
            return self._finish_trace("trace distance limit reached")

        def free(h: float) -> bool:
            return self.lake.navigable(self.x + ds * math.cos(h), self.y + ds * math.sin(h))

        if tr.cast > 0:
            heading = tr.cast_heading
            tr.cast -= ds
            if not free(heading):
                tr.cast = 0.0
                return
        else:
            g = self._gradient(tr)
            if g is not None and not tr.on_shore:
                tr.base = g
            if tr.base is None:
                tr.base = self.heading
            tr.zig_d += ds
            if tr.zig_d > 34:
                tr.zig, tr.zig_d = -tr.zig, 0.0
            heading = tr.base + (0.0 if tr.on_shore else tr.zig * 0.5)
            if not free(heading):
                # The gradient points at the bank: slide along the shoreline in the nearest free direction
                # and keep that direction, so the boat follows the bank instead of bouncing off it
                options = [tr.base + sign * math.radians(a) for a in range(15, 106, 15) for sign in (1, -1)]
                heading = next((h for h in options if free(h)), None)
                if heading is None:
                    return self._finish_trace("reached the shoreline")
                if not tr.on_shore:
                    tr.since_best = 0.0            # start the shoreline search with a fresh allowance
                tr.base, tr.on_shore = heading, True
            elif tr.on_shore and self.lake.shore_distance(self.x, self.y) > 14:
                # Drifted off the bank on a straight leg: steer back toward it while keeping direction
                sx, sy, _ = self.lake.nearest_shore(self.x, self.y)
                to_shore = math.atan2(sy - self.y, sx - self.x)
                turn = math.atan2(math.sin(to_shore - heading), math.cos(to_shore - heading))
                heading += max(-0.35, min(0.35, turn))
                if free(heading):
                    tr.base = heading
                else:
                    heading = tr.base
        self._move(heading, ds)
        if len(self.trace_path) < 2 or math.hypot(self.x - self.trace_path[-1][0], self.y - self.trace_path[-1][1]) > 9:
            self.trace_path.append((self.x, self.y))

    @staticmethod
    def _gradient(tr: Trace) -> float | None:
        """Direction of steepest increase of the anomaly score, from a plane fitted to recent samples."""
        if len(tr.samples) < 10:
            return None
        a = np.array(tr.samples, dtype=float)
        xy = a[:, :2] - a[:, :2].mean(axis=0)
        if np.linalg.matrix_rank(xy, tol=4.0) < 2:          # samples lie on one line: no 2-D gradient yet
            return None
        coef, *_ = np.linalg.lstsq(np.c_[xy, np.ones(len(a))], a[:, 2], rcond=None)
        if math.hypot(coef[0], coef[1]) < 1e-4:
            return None
        return math.atan2(coef[1], coef[0])

    def _finish_trace(self, reason: str) -> None:
        tr = self.trace
        est = tr.best_xy or (self.x, self.y)
        self.trace_path.append((self.x, self.y))
        self.mode = "hold"
        self._hold_until = time.time() + 6.0
        self.on_trace_done(tr.anomaly_id, est, reason)

    # ------------------------------------------------------------------ state for the dashboard
    def state(self) -> dict:
        lat, lon = self.lake.to_ll(self.x, self.y)
        return {
            "lat": lat, "lon": lon, "mode": self.mode,
            "bearing": round((90 - math.degrees(self.heading)) % 360, 1),
            "speed_scale": self.speed_scale, "speed_ms": round(BOAT_SPEED * self.speed_scale, 1),
            "battery": round(self.battery, 1), "distance_m": round(self.odometer),
            "waypoint_index": self.idx, "waypoints_total": len(self.waypoints),
            "zone": self.lake.zone_of(self.x, self.y),
        }
