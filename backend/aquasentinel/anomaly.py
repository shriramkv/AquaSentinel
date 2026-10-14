"""Streaming anomaly detection on the boat's readings.

Each parameter is compared with a robust baseline (median and MAD of the recent readings that
were not under suspicion). A reading is flagged when, for CONFIRM consecutive readings, one
parameter is more than Z_LIMIT robust standard deviations from its baseline, or two parameters
are each more than Z_JOINT away, or a CPCB limit is breached.
Consecutive flagged readings are grouped into one anomaly event.
"""
from __future__ import annotations

import statistics
from collections import deque

from . import config, cpcb

Z_LIMIT = 5.0             # one parameter alone
Z_JOINT = 3.2             # each of two or more parameters moving together
Z_CLEAN = 2.5             # readings with every parameter inside this feed the baseline
CONFIRM = 3                 # consecutive readings needed before a flag is raised
BASELINE_N = 240            # clean readings kept for the baseline
MIN_BASELINE = 12           # no flags until the baseline has this many readings
BOOT_SPREAD = 6.0           # while the baseline is forming, a reading must be within this many MAD_FLOORs of it to join
CLOSE_AFTER = 15            # unflagged readings that end an event
# Smallest spread assumed for each parameter, so a very quiet baseline does not make noise look anomalous
MAD_FLOOR = {"ph": 0.05, "do": 0.15, "temp": 0.20, "ec": 10.0, "nitrate": 0.5, "turbidity": 2.0}


class Detector:
    def __init__(self):
        self.clean: dict[str, deque] = {p: deque(maxlen=BASELINE_N) for p in config.PARAMS}
        self.streak: dict[str, int] = {p: 0 for p in config.PARAMS}
        self.event: dict | None = None
        self._quiet = 0

    def baseline(self) -> dict[str, float]:
        return {p: round(statistics.median(v), 3) for p, v in self.clean.items() if v}

    def zscores(self, reading: dict) -> dict[str, float]:
        out = {}
        for p in config.PARAMS:
            x, hist = reading.get(p), self.clean[p]
            if x is None or len(hist) < MIN_BASELINE:
                continue
            med = statistics.median(hist)
            mad = max(statistics.median(abs(v - med) for v in hist), MAD_FLOOR[p])
            out[p] = 0.6745 * (x - med) / mad
        return out

    def score(self, reading: dict, params: list[str] | None = None) -> float:
        """How far the reading is from the baseline, summed over `params` (all when None)."""
        z = self.zscores(reading)
        return sum(min(abs(v), 200.0) for p, v in z.items() if params is None or p in params)

    def update(self, reading: dict) -> dict:
        """Process one reading. Returns {"flagged", "params", "z", "opened", "closed"}."""
        z = self.zscores(reading)
        joint = sum(abs(v) > Z_JOINT for v in z.values()) >= 2
        hit = []
        for p in config.PARAMS:
            zp = abs(z.get(p, 0.0))
            breach = p in z and cpcb.status(p, reading.get(p)) == "breach"
            self.streak[p] = self.streak[p] + 1 if (zp > Z_LIMIT or (joint and zp > Z_JOINT) or breach) else 0
            if self.streak[p] >= CONFIRM:
                hit.append(p)
        flagged = bool(hit)
        # Only readings that look ordinary feed the baseline, so the edge of a plume cannot drag it along
        if not z:
            # Baseline still forming (first readings after start-up): accept a reading only if it sits close to
            # what has been collected so far. Otherwise a boat that starts near a discharge would learn the
            # plume as "normal" and miss it.
            ordinary = all(not self.clean[p] or reading.get(p) is None or
                           abs(reading[p] - statistics.median(self.clean[p])) <= BOOT_SPREAD * MAD_FLOOR[p] for p in config.PARAMS)
        else:
            ordinary = all(abs(v) < Z_CLEAN for v in z.values())
        if ordinary and not any(self.streak.values()):
            for p in config.PARAMS:
                if reading.get(p) is not None:
                    self.clean[p].append(reading[p])

        opened = closed = None
        if flagged:
            self._quiet = 0
            if self.event is None:
                self.event = {"params": list(hit), "peak": {}, "peak_score": -1.0, "baseline": self.baseline(),
                              "started": reading["ts"], "n": 0}
                opened = self.event
            ev = self.event
            ev["n"] += 1
            for p in hit:
                if p not in ev["params"]:
                    ev["params"].append(p)
            s = self.score(reading, ev["params"])
            if s > ev["peak_score"]:
                ev["peak_score"] = s
                ev["peak"] = {p: reading.get(p) for p in config.PARAMS}
                ev["lat"], ev["lon"], ev["zone"] = reading.get("lat"), reading.get("lon"), reading.get("zone")
        elif self.event is not None:
            self._quiet += 1
            if self._quiet >= CLOSE_AFTER:
                closed, self.event, self._quiet = self.event, None, 0
                closed["ended"] = reading["ts"]
        return {"flagged": flagged, "params": hit, "z": {p: round(v, 1) for p, v in z.items()},
                "opened": opened, "closed": closed}
