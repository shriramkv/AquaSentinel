"""Tools the Gemini agents can call.

Every tool is a plain function with typed arguments; ADK turns the signature and docstring into
the function declaration Gemini sees. Data tools are read-only with fixed query shapes (no free
SQL), and the simulator's hidden ground truth is never returned. Boat commands are validated
against the lake outline before they are accepted.
"""
from __future__ import annotations

import math
import time

from .. import config, cpcb, runtime

MAX_WAYPOINTS = 60


def _hub():
    return runtime.hub


def _param(parameter: str) -> str | None:
    p = parameter.strip().lower().replace(" ", "_")
    alias = {"dissolved_oxygen": "do", "oxygen": "do", "temperature": "temp", "conductivity": "ec",
             "electrical_conductivity": "ec", "no3": "nitrate", "turb": "turbidity"}
    p = alias.get(p, p)
    return p if p in config.PARAMS else None


def _round(d: dict, n: int = 2) -> dict:
    return {k: (round(v, n) if isinstance(v, float) else v) for k, v in d.items()}


# ----------------------------------------------------------------------------- map and boat
def get_lake_map() -> dict:
    """Returns the lake's name, size, outline centre, named places (inlets, outfalls, dock) with
    latitude/longitude, and the six zones with their ids (NW, N, NE, SW, S, SE) and centre points."""
    d = _hub().lake.describe()
    d.pop("outline", None)
    return d


def get_boat_status() -> dict:
    """Returns the boat's position, mode (idle, survey, trace, hold, paused), battery, and the active mission."""
    h = _hub()
    m = h.mission
    return {"boat": h.boat.state() if h.simulated else (h.latest and {k: h.latest[k] for k in ("lat", "lon", "zone", "ts")}),
            "mission": m and {k: m[k] for k in ("id", "request", "summary", "status")},
            "latest_reading": h.latest and {p: h.latest.get(p) for p in config.PARAMS}}


def plan_transect_survey(area: str, spacing_m: float) -> dict:
    """Builds a lawnmower survey route of parallel east-west lines.

    Args:
        area: "whole_lake", or a zone id: NW, N, NE, SW, S or SE.
        spacing_m: distance between lines in metres (40 to 400). Smaller is denser and slower.

    Returns waypoints as parallel lists `latitudes` and `longitudes`, plus the route length.
    The route is NOT sent to the boat until you call push_waypoints with these lists.
    """
    lk = _hub().lake
    zone = None if area.strip().lower() in ("whole_lake", "whole lake", "all", "lake") else area.strip().upper()
    if zone and zone not in lk.zone_points:
        return {"error": f"Unknown area '{area}'. Use whole_lake or one of {sorted(lk.zone_points)}"}
    pts = lk.transects(spacing=spacing_m, zone=zone)
    if not pts:
        return {"error": "No navigable water found for that area and spacing"}
    ll = [lk.to_ll(x, y) for x, y in pts]
    length = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:]))
    return {"latitudes": [p[0] for p in ll], "longitudes": [p[1] for p in ll], "waypoints": len(pts), "length_m": round(length)}


def route_to_place(place_id: str) -> dict:
    """Gives a navigable waypoint just off a named place (inlet, outfall, bay, dock).

    Args:
        place_id: id of a place from get_lake_map, for example north_inlet.

    Returns one waypoint as `latitudes` and `longitudes` lists. Combine with other waypoints
    and send with push_waypoints.
    """
    lk = _hub().lake
    p = lk.places.get(place_id.strip().lower())
    if p is None:
        return {"error": f"Unknown place '{place_id}'. Known: {sorted(lk.places)}"}
    lat, lon = lk.to_ll(*lk.pull_inside(p.x, p.y, 20))
    return {"place": p.name, "latitudes": [lat], "longitudes": [lon]}


async def push_waypoints(latitudes: list[float], longitudes: list[float], summary: str) -> dict:
    """Sends a new route to the boat, replacing its current one. The boat visits the waypoints in order.

    Args:
        latitudes: waypoint latitudes in visiting order.
        longitudes: waypoint longitudes, same length and order as latitudes.
        summary: one sentence describing the mission for the operator.

    Rejected if any waypoint is outside the lake or within 8 m of the shore, or if a leg crosses land.
    """
    h = _hub()
    if len(latitudes) != len(longitudes) or not latitudes:
        return {"accepted": False, "error": "latitudes and longitudes must be non-empty lists of equal length"}
    if len(latitudes) > MAX_WAYPOINTS:
        return {"accepted": False, "error": f"At most {MAX_WAYPOINTS} waypoints"}
    pts, bad = [], []
    for i, (lat, lon) in enumerate(zip(latitudes, longitudes)):
        try:
            x, y = h.lake.to_xy(float(lat), float(lon))
        except (TypeError, ValueError):
            bad.append(f"waypoint {i + 1}: not a number")
            continue
        if not h.lake.navigable(x, y):
            bad.append(f"waypoint {i + 1} ({lat}, {lon}) is outside the lake or too close to the shore")
        pts.append((x, y))
    start = (h.boat.x, h.boat.y) if h.simulated else (pts[0] if pts else (0, 0))
    if not bad:
        for i, (a, b) in enumerate(zip([start] + pts, pts)):
            if not h.lake.path_ok(a, b):
                bad.append(f"the leg into waypoint {i + 1} crosses land")
    if bad:
        return {"accepted": False, "error": "; ".join(bad[:6])}
    m = await h.set_mission(pts, request=h.pending_request or summary, summary=str(summary)[:300], planner="gemini")
    return {"accepted": True, "mission_id": m["id"], "waypoints": len(pts)}


def start_plume_trace(anomaly_id: int) -> dict:
    """Tells the boat's onboard controller to leave its route and follow the pollution gradient of
    an anomaly to its source. The boat resumes its route afterwards.

    Args:
        anomaly_id: id of the anomaly to trace, from get_hotspots or the anomaly notice.
    """
    return _hub().start_trace(int(anomaly_id))


def get_hotspots(hours: float) -> dict:
    """Lists anomalies detected in the last `hours` hours: parameters involved, peak readings,
    the baseline they are compared with, location, zone, and the source estimate if traced."""
    h = _hub()
    rows = h.store.latest("anomalies", 15, since=time.time() - max(0.1, min(hours, 168)) * 3600, ts_col="started")
    return {"anomalies": [_anomaly_view(r) for r in rows]}


def _anomaly_view(r: dict) -> dict:
    keep = ("id", "zone", "lat", "lon", "params", "peak", "baseline", "status", "linked_to", "source_lat", "source_lon", "source_note")
    out = {k: r.get(k) for k in keep}
    out["started_utc"] = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(r["started"]))
    out["minutes_ago"] = round((time.time() - r["started"]) / 60, 1)
    if r.get("ended"):
        out["duration_s"] = round(r["ended"] - r["started"])
    return out


# ----------------------------------------------------------------------------- data
def get_parameter_stats(parameter: str, hours: float) -> dict:
    """Per-zone statistics of one water-quality parameter over the last `hours` hours.

    Args:
        parameter: one of ph, do (dissolved oxygen, mg/L), temp (°C), ec (conductivity, µS/cm),
            nitrate (mg/L), turbidity (NTU).
        hours: look-back window in hours (up to 336).

    Returns for each zone: number of readings, min, mean, max, and how many readings were flagged anomalous.
    """
    p = _param(parameter)
    if p is None:
        return {"error": f"Unknown parameter '{parameter}'. Use one of {config.PARAMS}"}
    rows = _hub().store.stats(p, time.time() - max(0.05, min(hours, 336)) * 3600)
    return {"parameter": p, "unit": config.PARAM_META[p]["unit"], "hours": hours,
            "zones": [_round(r) for r in rows] or "no readings in this window"}


def compare_periods(parameter: str, recent_hours: float, previous_hours: float) -> dict:
    """Compares a parameter's per-zone mean in the most recent period with the period just before it.
    Use it for questions like "which stretch got worse this week".

    Args:
        parameter: ph, do, temp, ec, nitrate or turbidity.
        recent_hours: length of the recent period, ending now.
        previous_hours: length of the earlier period, ending where the recent one starts.
    """
    p = _param(parameter)
    if p is None:
        return {"error": f"Unknown parameter '{parameter}'. Use one of {config.PARAMS}"}
    now = time.time()
    t1 = now - max(0.05, recent_hours) * 3600
    t0 = t1 - max(0.05, previous_hours) * 3600
    st = _hub().store
    recent = {r["zone"]: r for r in st.stats(p, t1)}
    prev = {r["zone"]: r for r in st.stats(p, t0, t1)}
    out = []
    for z in sorted(set(recent) | set(prev)):
        a, b = recent.get(z), prev.get(z)
        row = {"zone": z, "recent_mean": a and round(a["mean"], 2), "recent_n": a["n"] if a else 0,
               "previous_mean": b and round(b["mean"], 2), "previous_n": b["n"] if b else 0}
        if a and b and b["mean"]:
            row["change"] = round(a["mean"] - b["mean"], 2)
            row["change_pct"] = round(100 * (a["mean"] - b["mean"]) / abs(b["mean"]), 1)
        out.append(row)
    return {"parameter": p, "unit": config.PARAM_META[p]["unit"], "zones": out,
            "note": "A zone with few or no readings in a period cannot be compared reliably."}


def get_recent_readings(zone: str, limit: int) -> dict:
    """Latest individual readings, newest last.

    Args:
        zone: a zone id (NW, N, NE, SW, S, SE) or "all".
        limit: number of readings, 1 to 40.
    """
    z = None if zone.strip().lower() in ("all", "", "any") else zone.strip().upper()
    rows = _hub().store.recent_readings(max(1, min(int(limit), 40)), zone=z)
    keep = ("ts", "lat", "lon", "zone", *config.PARAMS, "flagged")
    return {"readings": [{**{k: r.get(k) for k in keep if k != "ts"},
                          "time_utc": time.strftime("%H:%M:%S", time.gmtime(r["ts"]))} for r in rows]}


def get_baseline() -> dict:
    """The detector's current clean-water baseline: the median of recent readings that were not anomalous."""
    return {"baseline": _hub().detector.baseline(), "units": {p: m["unit"] for p, m in config.PARAM_META.items()}}


def list_diagnoses(limit: int) -> dict:
    """Earlier diagnoses written for anomalies: probable cause, confidence, evidence and recommended actions.

    Args:
        limit: how many of the latest diagnoses to return, 1 to 10.
    """
    rows = _hub().store.latest("diagnoses", max(1, min(int(limit), 10)))
    return {"diagnoses": [{"anomaly_id": r["anomaly_id"], "minutes_ago": round((time.time() - r["ts"]) / 60, 1),
                           **{k: v for k, v in r["body"].items() if k != "tool_calls"}} for r in rows]}


# ----------------------------------------------------------------------------- reference and ML
def cpcb_criteria() -> dict:
    """The CPCB Designated Best Use water-quality classes A to E with their limits, which of those
    limits the boat can measure, and reference limits for nitrate and turbidity from BIS IS 10500."""
    return cpcb.CRITERIA


def classify_water_quality(ph: float, dissolved_oxygen_mg_l: float, conductivity_us_cm: float) -> dict:
    """Finds the best CPCB Designated Best Use class whose pH, DO and conductivity limits a sample meets.
    Coliform, BOD and ammonia are not measured by the boat, so the true class can only be equal or lower.

    Args:
        ph: measured pH.
        dissolved_oxygen_mg_l: measured dissolved oxygen in mg/L.
        conductivity_us_cm: measured electrical conductivity in µS/cm.
    """
    return cpcb.classify(ph, dissolved_oxygen_mg_l, conductivity_us_cm)


def get_zone_health() -> dict:
    """Aquatic-life risk for each lake zone from the fish-health machine-learning model: status
    (Healthy, Stressed, Critical), confidence, whether the result is reliable, the factors that drove
    it, and operator advice. The model uses temperature, TDS, turbidity and pH over 24 h; it does not
    see dissolved oxygen or nitrate."""
    zh = _hub().zone_health
    if zh.error:
        return {"error": f"Fish-health model unavailable: {zh.error}"}
    zones = []
    for z, p in zh.latest.items():
        if not p.get("available"):
            zones.append({"zone": z, "available": False, "reason": p.get("reason")})
            continue
        zones.append({"zone": z, "status": p["health_status"], "confidence": p["confidence"], "reliable": p["reliable"],
                      "top_factors": [{"description": f["description"], "value": f["value"], "impact": f["impact"]} for f in p["top_factors"][:3]],
                      "advice": p["advice"], "latest_inputs": p["latest_readings"], "data_warnings": p["data_quality"]["warnings"]})
    return {"zones": zones, "model_note": zh.model_info.get("note"),
            "history_note": "In the simulator each zone's 24 h history comes from a virtual fixed station at the zone's most exposed point."
            if _hub().simulated else "Zone history comes from the boat's own readings in each zone."}


PLANNER_TOOLS = [get_lake_map, get_boat_status, plan_transect_survey, route_to_place, push_waypoints, get_hotspots, start_plume_trace]
DIAGNOSTICIAN_TOOLS = [get_parameter_stats, get_recent_readings, get_baseline, cpcb_criteria, classify_water_quality, get_zone_health, get_lake_map]
REPORTER_TOOLS = [get_parameter_stats, compare_periods, get_recent_readings, get_baseline, get_hotspots, list_diagnoses,
                  cpcb_criteria, classify_water_quality, get_zone_health, get_lake_map, get_boat_status]
