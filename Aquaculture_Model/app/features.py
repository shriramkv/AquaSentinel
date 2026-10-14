"""
Feature pipeline shared by training and the API, so features are computed identically
in both places.

Input : DataFrame with columns timestamp, pond_id, temperature_c, tds_ppm, turbidity_ntu, ph
        (any sampling rate - the ESP32 sends every ~17 s, the training data is 5-min)
Output: one feature row per 5-minute step, using only past and current data (causal),
        so a prediction never depends on future readings.

Steps : clean()          drop sensor faults and single-sample spikes
        _pond_features() resample to 5 min, bridge short gaps, build rolling/domain features
        build_features() run both per pond and stack the results
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

SENSORS = ["temperature_c", "tds_ppm", "turbidity_ntu", "ph"]
RESAMPLE = "5min"
WINDOWS = {"1h": 12, "6h": 72, "24h": 288}      # steps at 5-min resolution
MIN_HISTORY_STEPS = 12                          # 1 h of data before a prediction is trustworthy

# Physically possible ranges; anything outside is a sensor fault (e.g. DS18B20 -127, ADC rails)
VALID_RANGE = {
    "temperature_c": (0.0, 45.0),
    "tds_ppm": (1.0, 5000.0),
    "turbidity_ntu": (0.0, 1000.0),
    "ph": (2.0, 12.0),
}
# A reading this far from the median of the previous 30 min is treated as a single-sample spike
SPIKE_LIMIT = {"temperature_c": 3.0, "tds_ppm": 0.35, "turbidity_ntu": 1.2, "ph": 1.0}
SPIKE_RELATIVE = {"tds_ppm", "turbidity_ntu"}   # limits for these are fractions of the median


def clean(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Remove sensor faults and spikes. Returns cleaned frame and a fault report.

    Expects the rows of ONE pond, already sorted by time. The report maps each sensor to
    {"out_of_range": n, "spikes": n} - the number of readings that were blanked (set to NaN).
    """
    df = df.copy()
    report = {}
    for col in SENSORS:
        x = pd.to_numeric(df[col], errors="coerce")        # non-numeric text becomes NaN
        lo, hi = VALID_RANGE[col]
        out_of_range = (x < lo) | (x > hi)
        x = x.mask(out_of_range)
        # Median of the previous 6 readings (shift(1) excludes the current one, keeping this causal).
        # The window is counted in ROWS, not minutes: 30 min at 5-min sampling, ~1.7 min at 17 s.
        med = x.rolling(6, min_periods=3).median().shift(1)
        dev = (x - med).abs()
        # Relative limits use a floor of 20 so near-zero medians do not flag ordinary noise as spikes
        limit = SPIKE_LIMIT[col] * (med.abs().clip(lower=20) if col in SPIKE_RELATIVE else 1)
        spike = dev > limit
        x = x.mask(spike)
        df[col] = x
        report[col] = {"out_of_range": int(out_of_range.sum()), "spikes": int(spike.sum())}
    return df, report


def _pond_features(g: pd.DataFrame) -> pd.DataFrame:
    """Engineer the feature table for one cleaned pond: one row per 5-minute step."""
    g = g.set_index("timestamp").sort_index()
    g = g[SENSORS].resample(RESAMPLE).mean()               # any sampling rate -> regular 5-min grid
    # Flag steps where at least one sensor has no value, BEFORE forward-filling hides the gap
    missing = g[SENSORS].isna().any(axis=1).astype(float)
    g = g.ffill(limit=6)                                   # bridge gaps up to 30 min

    f = pd.DataFrame(index=g.index)
    for col in SENSORS:
        x = g[col]
        short = col.split("_")[0]                          # temperature_c -> temperature, tds_ppm -> tds
        f[short] = x
        # Rolling statistics over 1 h / 6 h / 24 h. A window needs a quarter of its points
        # (at least 3) before it produces a value, so early rows are NaN rather than noisy.
        for name, n in WINDOWS.items():
            r = x.rolling(n, min_periods=max(3, n // 4))
            f[f"{short}_mean_{name}"] = r.mean()
            f[f"{short}_std_{name}"] = r.std()
            f[f"{short}_min_{name}"] = r.min()
            f[f"{short}_max_{name}"] = r.max()
        f[f"{short}_ewm_6h"] = x.ewm(span=72, adjust=False).mean()    # smoothed trend
        f[f"{short}_delta_1h"] = x - x.shift(12)                      # change over the last hour
        f[f"{short}_delta_6h"] = x - x.shift(72)
        f[f"{short}_dev_24h"] = x - f[f"{short}_mean_24h"]            # distance from the daily average

    T, ph = g["temperature_c"], g["ph"]
    # Domain features
    pka = 0.09018 + 2729.92 / (T + 273.15)                           # Emerson et al. 1975
    f["nh3_fraction"] = 1.0 / (1.0 + 10 ** (pka - ph))               # toxic ammonia share
    f["nh3_fraction_max_6h"] = f["nh3_fraction"].rolling(72, min_periods=3).max()
    f["nh3_fraction_mean_6h"] = f["nh3_fraction"].rolling(72, min_periods=3).mean()
    # Dissolved-oxygen saturation (mg/L) from temperature, APHA polynomial for fresh water
    f["do_saturation"] = 14.652 - 0.41022 * T + 0.007991 * T**2 - 0.000077774 * T**3
    f["do_saturation_min_6h"] = f["do_saturation"].rolling(72, min_periods=3).min()
    f["tds_compensated"] = g["tds_ppm"] / (1 + 0.02 * (T - 25))     # firmware skips this
    f["ph_range_24h"] = f["ph_max_24h"] - f["ph_min_24h"]           # algal bloom signature
    f["temp_range_24h"] = f["temperature_max_24h"] - f["temperature_min_24h"]
    f["turbidity_x_ph_range"] = f["turbidity_mean_6h"] * f["ph_range_24h"]
    # Time of day as a point on a circle, so 23:55 and 00:00 are neighbours
    hour = f.index.hour + f.index.minute / 60
    f["hour_sin"] = np.sin(2 * np.pi * hour / 24)
    f["hour_cos"] = np.cos(2 * np.pi * hour / 24)
    f["missing_frac_1h"] = missing.rolling(12, min_periods=1).mean()
    # Number of 5-min steps seen so far (capped at 24 h). Used to judge reliability, not a model input.
    f["history_steps"] = np.arange(1, len(f) + 1).clip(max=288)
    return f


def build_features(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Clean + resample + engineer features for one or more ponds.

    Returns (features, reports): `features` has one row per pond per 5-min step with columns
    timestamp, pond_id and the engineered features; `reports` maps pond_id to the fault report
    from clean().
    """
    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"])          # expected: naive local pond time
    if getattr(df["timestamp"].dt, "tz", None) is not None:
        logger.debug("Timezone-aware timestamps received; dropping the timezone and keeping the wall-clock time")
        df["timestamp"] = df["timestamp"].dt.tz_localize(None)
    if "pond_id" not in df:
        df["pond_id"] = "default"
    df = df.sort_values(["pond_id", "timestamp"])
    out, reports = [], {}
    for pid, g in df.groupby("pond_id", sort=False):
        g, rep = clean(g)
        feats = _pond_features(g)
        feats.insert(0, "pond_id", pid)
        out.append(feats.reset_index())
        reports[pid] = rep

        removed = {s: v for s, v in rep.items() if v["out_of_range"] or v["spikes"]}
        logger.debug("pond %s: %d readings -> %d five-minute steps (%s .. %s); removed %s",
                     pid, len(g), len(feats), feats.index.min(), feats.index.max(), removed or "nothing")
        # The 5-min grid covers the whole span between the first and last timestamp. A few readings
        # spread over a very long period (e.g. one wrong clock value) inflate it and slow everything down.
        if len(feats) > 2 * len(g) + WINDOWS["24h"]:
            logger.warning("pond %s: only %d readings spread over %d five-minute steps (%s .. %s) - "
                           "check for a wrong timestamp or a long gap in the data",
                           pid, len(g), len(feats), feats.index.min(), feats.index.max())
    return pd.concat(out, ignore_index=True), reports


def feature_columns(feats: pd.DataFrame) -> list[str]:
    """Model input columns: everything except the identifiers and the history counter."""
    return [c for c in feats.columns if c not in ("timestamp", "pond_id", "history_steps")]
