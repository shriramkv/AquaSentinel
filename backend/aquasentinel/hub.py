"""Central state and the Survey -> Detect -> Re-plan -> Diagnose loop.

Readings arrive from the simulator or from the real boat (POST /api/ingest) and take the same
path: zone lookup, anomaly detection, storage, WebSocket broadcast. When an anomaly opens, the
boat is re-routed to trace the plume and the agents are asked for a diagnosis.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from collections import deque

from . import camera, config, cpcb, runtime
from .anomaly import Detector
from .geo import lake
from .simulator import SCENARIOS, TRUTH_MIN, Boat, World
from .store import Store
from .zone_health import ZoneHealth

logger = logging.getLogger(__name__)

LINK_RADIUS_M = 450          # a new anomaly this close to an already-traced source is the same plume
LINK_WINDOW_S = 1800
DIAGNOSE_COOLDOWN_S = 45     # minimum gap between automatic Gemini diagnoses

# Why a trace ended, as codes the dashboard can translate
TRACE_REASON = {"passed the peak along the shoreline": "shore_peak", "signal stopped rising": "stopped_rising",
                "trace distance limit reached": "limit", "reached the shoreline": "shore"}


class Hub:
    def __init__(self, db_path=None):
        self.store = Store(db_path) if db_path else Store()
        self.lake = lake
        self.detector = Detector()
        self.zone_health = ZoneHealth()
        self.simulated = config.DATA_SOURCE != "boat"
        self.world = World(lake)
        self.boat = Boat(lake, self.world)
        self.boat.score_fn = self.detector.score
        self.boat.on_trace_done = self._trace_done
        self.boat.on_mission_done = self._mission_done
        self.clients: set = set()
        self.latest: dict | None = None
        self.mission: dict | None = None
        self.open_anomaly_id: int | None = None
        self.frames: dict[int, dict] = {}                 # anomaly id -> {"detect": jpeg, "source": jpeg}
        self.live_frame: dict | None = None               # {"jpeg", "ts", "simulated"}; ground-truth labels kept apart
        self._frame_labels: list[str] = []
        self.activity: deque = deque(maxlen=80)
        self.localisation: list[dict] = []                # simulator benchmark: estimate vs hidden source
        self.process_ms: deque = deque(maxlen=500)
        self.replan_mode = config.REPLAN_MODE if config.GEMINI_READY else "onboard"
        self._last_diagnosis = 0.0
        self.language = "en"                        # language the agents write in; set from the dashboard
        self.pending_request = ""                  # officer's wording while the Planner is working on it
        self._zones_dirty = True
        self._tasks: set = set()
        self.started = time.time()

    # ------------------------------------------------------------------ plumbing
    def spawn(self, coro) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._task_done)

    def _task_done(self, task) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception():
            logger.error("Background task failed", exc_info=task.exception())

    async def broadcast(self, msg: dict) -> None:
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_json(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)

    async def log(self, who: str, text: str, level: str = "info", code: str | None = None, **extra) -> None:
        """Add an activity entry. `text` is English; `code` plus the extra fields let the dashboard
        show the same message in the viewer's language."""
        entry = {"ts": time.time(), "who": who, "text": text, "level": level, "code": code, **extra}
        self.activity.append(entry)
        await self.broadcast({"type": "activity", "entry": entry})

    # ------------------------------------------------------------------ snapshot for a new client
    def snapshot(self) -> dict:
        return {
            "lake": self.lake.describe(),
            "config": {
                "simulated": self.simulated, "gemini_ready": config.GEMINI_READY, "gemini_model": config.GEMINI_MODEL,
                "replan_mode": self.replan_mode, "params": config.PARAM_META, "language": self.language,
                "scenario_kinds": [{"kind": k, "label": v["label"], "default_place": v["place"]} for k, v in SCENARIOS.items()],
            },
            "boat": self.boat.state() if self.simulated else None,
            "mission": self.mission,
            "scenarios": self.world.describe_scenarios(),
            "readings": self._recent_with_status(),
            "anomalies": self.store.latest("anomalies", 30),
            "diagnoses": self.store.latest("diagnoses", 10),
            "activity": list(self.activity),
            "zones": {"updated": self.zone_health.updated, "zones": self.zone_health.latest,
                      "model": self.zone_health.model_info, "error": self.zone_health.error},
            "baseline": self.detector.baseline(),
            "trace_path": self._trace_ll(),
        }

    def _recent_with_status(self) -> list[dict]:
        rows = self.store.recent_readings(900, since=time.time() - 3600)
        if rows:
            rows[-1]["status"] = {p: cpcb.status(p, rows[-1].get(p)) for p in config.PARAMS}
        return rows

    def _trace_ll(self) -> list[list[float]]:
        return [list(self.lake.to_ll(x, y)) for x, y in self.boat.trace_path]

    # ------------------------------------------------------------------ readings
    async def process_reading(self, r: dict, truth: float | None = None) -> dict:
        """One reading through detection, storage and broadcast. `r` needs ts, lat, lon and parameter values."""
        t0 = time.perf_counter()
        x, y = self.lake.to_xy(r["lat"], r["lon"])
        r["zone"] = self.lake.zone_of(x, y)
        r["mission_id"] = self.mission["id"] if self.mission else None
        res = self.detector.update(r)
        r["flagged"] = int(res["flagged"])
        r["id"] = self.store.add_reading({**r, "truth": truth})
        r["status"] = {p: cpcb.status(p, r.get(p)) for p in config.PARAMS}
        self.latest = r
        if not self.simulated:
            self.zone_health.add_boat_reading(r["zone"], r["ts"], r.get("temp"), r.get("ec"), r.get("turbidity"), r.get("ph"))
        await self.broadcast({"type": "reading", "reading": r, "z": res["z"], "flag_params": res["params"]})

        if res["opened"]:
            ev = res["opened"]
            aid = self.store.insert("anomalies", started=ev["started"], zone=r["zone"], lat=r["lat"], lon=r["lon"],
                                    params=ev["params"], peak=ev["peak"], baseline=ev["baseline"], status="open")
            self.open_anomaly_id = aid
            self.spawn(self._anomaly_opened(aid, (x, y)))
        elif self.open_anomaly_id and self.detector.event is not None and self.detector.event["n"] % 5 == 0:
            self._save_event(self.open_anomaly_id, self.detector.event)
        if res["closed"] and self.open_anomaly_id:
            aid, self.open_anomaly_id = self.open_anomaly_id, None
            self._save_event(aid, res["closed"], ended=res["closed"]["ended"])
            row = self.store.get("anomalies", aid)
            if row["status"] == "open":
                self.store.update("anomalies", aid, status="closed")
                row["status"] = "closed"
            await self.broadcast({"type": "anomaly", "anomaly": row})
        self.process_ms.append((time.perf_counter() - t0) * 1000)
        return r

    def _save_event(self, aid: int, ev: dict, ended: float | None = None) -> None:
        cols = {"params": ev["params"], "peak": ev["peak"]}
        if "lat" in ev:
            cols.update(lat=ev["lat"], lon=ev["lon"], zone=ev["zone"])
        if ended:
            cols["ended"] = ended
        self.store.update("anomalies", aid, **cols)

    # ------------------------------------------------------------------ anomaly lifecycle
    async def _anomaly_opened(self, aid: int, xy: tuple[float, float]) -> None:
        row = self.store.get("anomalies", aid)
        names = ", ".join(config.PARAM_META[p]["label"] for p in row["params"])
        await self.broadcast({"type": "anomaly", "anomaly": row})
        await self.log("detector", f"Anomaly #{aid} in zone {row['zone']}: {names} off baseline", "warn", code="anomaly_detected",
                       anomaly_id=aid, zone=row["zone"], param_keys=row["params"])
        self.capture_frame(aid, "detect")

        # Raised while the boat is already tracing a plume: it is that plume seen again. No new trace, no Gemini call.
        if self.simulated and self.boat.mode in ("trace", "hold") and self.boat.trace is not None:
            prev_id = self.boat.trace.anomaly_id
            self.store.update("anomalies", aid, linked_to=prev_id, status="linked",
                              replan={"by": "system", "action": "none", "reason": f"Same plume as anomaly #{prev_id}, being traced now"})
            await self.broadcast({"type": "anomaly", "anomaly": self.store.get("anomalies", aid)})
            await self.log("detector", f"Anomaly #{aid} matches the plume traced for #{prev_id}; no new trace",
                           code="anomaly_linked", anomaly_id=aid, prev=prev_id)
            return

        # Same plume as one already traced? Then do not chase it again.
        for prev in self.store.latest("anomalies", 20, since=time.time() - LINK_WINDOW_S, ts_col="started"):
            if prev["id"] != aid and prev.get("source_lat") is not None:
                px, py = self.lake.to_xy(prev["source_lat"], prev["source_lon"])
                if math.hypot(px - xy[0], py - xy[1]) < LINK_RADIUS_M:
                    self.store.update("anomalies", aid, linked_to=prev["id"], status="linked",
                                      replan={"by": "system", "action": "none",
                                              "reason": f"Same plume as anomaly #{prev['id']}, already traced"})
                    await self.broadcast({"type": "anomaly", "anomaly": self.store.get("anomalies", aid)})
                    await self.log("detector", f"Anomaly #{aid} matches the plume traced for #{prev['id']}; no new trace",
                                       code="anomaly_linked", anomaly_id=aid, prev=prev["id"])
                    return

        if self.simulated:
            await self._replan(aid)
        elif config.GEMINI_READY:
            await asyncio.sleep(8)                      # let a few more readings arrive before diagnosing
            await self._diagnose_quietly(aid)

    async def _replan(self, aid: int) -> None:
        """Decide how the boat reacts: the Planner agent (Gemini) or the fixed onboard rule."""
        row = self.store.get("anomalies", aid)
        if self.replan_mode == "gemini" and config.GEMINI_READY:
            try:
                from .agents import service
                await self.log("planner", f"Asking Gemini how to respond to anomaly #{aid}", code="planner_asking", anomaly_id=aid)
                result = await service.replan(row, self.language)
                names = [c["name"] for c in result["tool_calls"]]
                tracing = self.boat.trace is not None and self.boat.trace.anomaly_id == aid
                action = "trace" if tracing else "route" if "push_waypoints" in names else "none"
                self.store.update("anomalies", aid, replan={"by": "gemini", "action": action, "reason": result["text"],
                                                             "tool_calls": result["tool_calls"]})
                await self.log("planner", result["text"] or "Re-plan complete", anomaly_id=aid, tool_calls=result["tool_calls"])
                await self.broadcast({"type": "anomaly", "anomaly": self.store.get("anomalies", aid)})
                if action != "trace":
                    self.spawn(self._diagnose_later(aid, 8))
                return
            except Exception as e:
                await self.log("planner", f"Gemini re-plan failed ({_short(e)}); using the onboard rule", "error", code="replan_failed",
                               anomaly_id=aid, error=_short(e))
        started = self.boat.start_trace(row["params"], aid)
        reason = ("Onboard rule: on any anomaly, trace the gradient to its source" if started
                  else "Boat is already tracing another plume")
        self.store.update("anomalies", aid, replan={"by": "onboard rule", "action": "trace" if started else "none", "reason": reason})
        await self.log("planner", f"Anomaly #{aid}: {reason}", code="replan_onboard" if started else "replan_busy", anomaly_id=aid)
        await self.broadcast({"type": "anomaly", "anomaly": self.store.get("anomalies", aid)})
        if not started:
            self.spawn(self._diagnose_later(aid, 8))

    def start_trace(self, aid: int) -> dict:
        """Used by the Planner agent's tool."""
        row = self.store.get("anomalies", aid)
        if row is None:
            return {"ok": False, "error": f"No anomaly with id {aid}"}
        if not self.simulated:
            return {"ok": False, "error": "Plume tracing runs on the simulator's onboard controller only; "
                                          "for the real boat, push waypoints toward the hotspot instead"}
        ok = self.boat.start_trace(row["params"], aid)
        return {"ok": ok, **({} if ok else {"error": "Boat is already tracing a plume"})}

    def _trace_done(self, aid: int, est: tuple[float, float], reason: str) -> None:
        self.spawn(self._trace_finished(aid, est, reason))

    async def _trace_finished(self, aid: int, est: tuple[float, float], reason: str) -> None:
        sx, sy, d = self.lake.nearest_shore(*est)
        snapped = d < 60
        ex, ey = (sx, sy) if snapped else est
        note = f"Trace ended: {reason}. " + ("Estimate placed on the nearest shoreline." if snapped else "Estimate is in open water.")
        near = self.lake.nearest_place(ex, ey)
        if near and near[1] < 120:
            note += f" {near[1]:.0f} m from {near[0].name}."
        lat, lon = self.lake.to_ll(ex, ey)
        info = {"reason": TRACE_REASON.get(reason, "other"), "snapped": snapped,
                "place_id": near[0].id if near and near[1] < 120 else None,
                "place": near[0].name if near and near[1] < 120 else None,
                "distance_m": round(near[1]) if near and near[1] < 120 else None}
        self.store.update("anomalies", aid, source_lat=lat, source_lon=lon, source_note=note, source_info=info, status="traced")
        # Benchmark only: distance to the hidden source the simulator placed
        _, sc = self.world.strength(ex, ey, time.time())
        if sc is not None:
            self.localisation.append({"anomaly_id": aid, "scenario": sc.kind, "error_m": round(math.hypot(ex - sc.x, ey - sc.y), 1)})
        self.capture_frame(aid, "source")
        row = self.store.get("anomalies", aid)
        await self.broadcast({"type": "anomaly", "anomaly": row, "trace_path": self._trace_ll()})
        await self.log("planner", f"Source estimate for anomaly #{aid}: {note}", code="source_estimate", anomaly_id=aid, source_info=info)
        if config.GEMINI_READY:
            await self._diagnose_quietly(aid)
        else:
            await self.log("diagnostician", "Gemini is not configured, so no diagnosis was generated. "
                                             "Add GEMINI_API_KEY to .env and restart.", "warn", code="diag_no_gemini", anomaly_id=aid)

    async def _diagnose_later(self, aid: int, delay: float) -> None:
        await asyncio.sleep(delay)
        if config.GEMINI_READY:
            await self._diagnose_quietly(aid)

    async def _diagnose_quietly(self, aid: int) -> None:
        """Automatic diagnosis: a failure is already shown in the activity feed, so it is not raised again."""
        try:
            await self.diagnose(aid)
        except Exception:
            logger.warning("Automatic diagnosis of anomaly %s failed", aid)

    async def diagnose(self, aid: int, manual: bool = False) -> dict:
        """Run the Visual Inspector and Diagnostician agents for one anomaly and store the result."""
        if not config.GEMINI_READY:
            raise RuntimeError("Gemini is not configured. Set GEMINI_API_KEY and GEMINI_MODEL in .env and restart.")
        if not manual and time.time() - self._last_diagnosis < DIAGNOSE_COOLDOWN_S:
            await self.log("diagnostician", f"Skipped automatic diagnosis of #{aid} (cooldown); run it manually if needed",
                           code="diag_skipped", anomaly_id=aid)
            return {}
        self._last_diagnosis = time.time()
        from .agents import service
        row = self.store.get("anomalies", aid)
        if row is None:
            raise KeyError(f"No anomaly with id {aid}")
        frames = self.frames.get(aid, {})
        jpeg = frames.get("source") or frames.get("detect")
        await self.log("diagnostician", f"Diagnosing anomaly #{aid}", code="diag_start", anomaly_id=aid)
        try:
            body = await service.diagnose(row, jpeg, simulated_frame=self.simulated, language=self.language)
        except Exception as e:
            await self.log("diagnostician", f"Diagnosis of #{aid} failed: {_short(e)}", "error", code="diag_failed", anomaly_id=aid, error=_short(e))
            raise
        did = self.store.insert("diagnoses", anomaly_id=aid, ts=time.time(), body=body)
        diag = self.store.get("diagnoses", did)
        await self.broadcast({"type": "diagnosis", "diagnosis": diag})
        await self.log("diagnostician", f"Anomaly #{aid}: {body.get('probable_cause', 'see diagnosis')} "
                                         f"({body.get('confidence', '?')} confidence)", code="diag_done", anomaly_id=aid,
                       cause=body.get("probable_cause"), confidence=body.get("confidence"))
        return diag

    # ------------------------------------------------------------------ camera
    def render_frame(self) -> tuple[bytes, list[str]]:
        now = time.time()
        strength, sc = self.world.strength(self.boat.x, self.boat.y, now)
        near = sc is not None and math.hypot(self.boat.x - sc.x, self.boat.y - sc.y) < 70
        return camera.render(sc.kind if sc else None, strength, near, seed=int(now * 2) % 100000)

    def capture_frame(self, aid: int, slot: str) -> None:
        if self.simulated:
            jpeg, _ = self.render_frame()
        elif self.live_frame and time.time() - self.live_frame["ts"] < 120:
            jpeg = self.live_frame["jpeg"]
        else:
            return
        self.frames.setdefault(aid, {})[slot] = jpeg
        for old in sorted(self.frames)[:-40]:
            del self.frames[old]

    # ------------------------------------------------------------------ missions
    async def set_mission(self, pts: list[tuple[float, float]], request: str, summary: str, planner: str,
                          meta: dict | None = None) -> dict:
        """`meta` describes a preset mission as a code (whole_lake | zone | dock) so the dashboard can translate it."""
        wps = [list(self.lake.to_ll(x, y)) for x, y in pts]
        if self.mission and self.mission["status"] == "active":
            self.store.update("missions", self.mission["id"], status="replaced")
        mid = self.store.insert("missions", ts=time.time(), request=request, summary=summary, planner=planner,
                                waypoints=wps, status="active", meta=meta)
        self.mission = self.store.get("missions", mid)
        if self.simulated:
            self.boat.set_route(pts)
        await self.broadcast({"type": "mission", "mission": self.mission})
        await self.log("planner", f"Mission #{mid} ({planner}): {summary}", code="mission_set", mission_id=mid, planner=planner,
                       summary=summary, meta=meta)
        return self.mission

    def _mission_done(self) -> None:
        if self.mission and self.mission["status"] == "active":
            self.store.update("missions", self.mission["id"], status="complete")
            self.mission = self.store.get("missions", self.mission["id"])
            self.spawn(self.broadcast({"type": "mission", "mission": self.mission}))
            self.spawn(self.log("planner", f"Mission #{self.mission['id']} complete", code="mission_complete", mission_id=self.mission["id"]))

    # ------------------------------------------------------------------ zones
    def zone_histories(self) -> dict[str, list[dict]]:
        if self.simulated:
            now = time.time()
            return {z["id"]: self.world.station_rows(z, now) for z in self.lake.zones}
        return {z["id"]: self.zone_health.boat_rows(z["id"]) for z in self.lake.zones}

    async def refresh_zones(self) -> None:
        self._zones_dirty = False
        zones = await asyncio.to_thread(self.zone_health.refresh, self.zone_histories())
        await self.broadcast({"type": "zones", "zones": {"updated": self.zone_health.updated, "zones": zones,
                                                          "model": self.zone_health.model_info, "error": self.zone_health.error}})

    def mark_zones_dirty(self) -> None:
        self._zones_dirty = True

    # ------------------------------------------------------------------ main loop
    async def run(self) -> None:
        runtime.hub = self
        last, tick, last_zone = time.time(), 0, 0.0
        if self.simulated:
            pts = [self.boat.home] + self.lake.transects()
            await self.set_mission(pts, "Routine whole-lake survey", "Whole-lake transects, 110 m apart", "preset",
                                   meta={"code": "whole_lake", "spacing_m": 110})
        while True:
            await asyncio.sleep(0.5)
            now = time.time()
            tick += 1
            try:
                if self.simulated:
                    self.boat.step(now - last, now)
                    moving = self.boat.mode in ("survey", "trace")
                    await self.broadcast({"type": "boat", "boat": self.boat.state(),
                                          **({"trace_path": self._trace_ll()} if self.boat.mode == "trace" else {})})
                    if tick % (2 if moving else 10) == 0:
                        values, truth = self.world.sample(self.boat.x, self.boat.y, now)
                        lat, lon = self.lake.to_ll(self.boat.x, self.boat.y)
                        await self.process_reading({"ts": now, "lat": lat, "lon": lon, "source": "simulator", **values}, truth)
                    if tick % 6 == 0 and self.clients:
                        jpeg, self._frame_labels = self.render_frame()
                        self.live_frame = {"jpeg": jpeg, "ts": now, "simulated": True}
                        await self.broadcast({"type": "frame", "ts": now})
                if self._zones_dirty or now - last_zone > 120:
                    last_zone = now
                    await self.refresh_zones()
            except Exception:
                logger.exception("Main loop error")
            last = now

    # ------------------------------------------------------------------ benchmarks
    def benchmarks(self) -> dict:
        since = self.started
        c = self.store.confusion(since, TRUTH_MIN) if self.simulated else None
        n = self.store.count_readings(since)
        hours = max((time.time() - since) / 3600, 1e-6)
        ms = sorted(self.process_ms)
        return {
            "simulated": self.simulated, "since": since, "readings": n, "readings_per_hour": round(n / hours),
            "process_ms": {"p50": round(ms[len(ms) // 2], 2), "p95": round(ms[int(len(ms) * 0.95)], 2)} if ms else None,
            "detector": c and {**c, "precision": _ratio(c["tp"], c["tp"] + c["fp"]), "recall": _ratio(c["tp"], c["tp"] + c["fn"]),
                               "truth_rule": "A reading is 'polluted' when the simulated plume shifts pH by 0.3, DO by 1 mg/L, "
                                             "temperature by 1 °C, conductivity by 100 µS/cm, nitrate by 3 mg/L or turbidity by 10 NTU."},
            "localisation": self.localisation,
        }

    async def reset(self) -> None:
        self.store.reset()
        self.detector = Detector()
        self.boat.score_fn = self.detector.score
        self.world.clear_scenarios()
        self.zone_health.clear()
        self.frames.clear()
        self.activity.clear()
        self.localisation.clear()
        self.process_ms.clear()
        self.open_anomaly_id, self.latest, self.mission = None, None, None
        self.started = time.time()
        if self.simulated:
            self.boat.__init__(self.lake, self.world)
            self.boat.score_fn = self.detector.score
            self.boat.on_trace_done, self.boat.on_mission_done = self._trace_done, self._mission_done
            await self.set_mission([self.boat.home] + self.lake.transects(), "Routine whole-lake survey",
                                   "Whole-lake transects, 110 m apart", "preset", meta={"code": "whole_lake", "spacing_m": 110})
        self._zones_dirty = True
        await self.broadcast({"type": "reset"})


def _ratio(a: int, b: int) -> float | None:
    return round(a / b, 3) if b else None


def _short(e: Exception) -> str:
    s = f"{type(e).__name__}: {e}"
    return s if len(s) < 300 else s[:300] + "..."
