"""Human-readable feature names and rule-based advice for the web app."""
from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)

# Lookup tables used to turn a feature name such as "turbidity_mean_6h" into plain English.
# Feature names follow <sensor>_<statistic>_<window>, as built in app/features.py.
_SENSOR = {"temperature": "Water temperature", "tds": "TDS", "turbidity": "Turbidity", "ph": "pH"}
_STAT = {"mean": "average", "std": "variability", "min": "minimum", "max": "maximum"}
# Features that do not follow the naming pattern get a hand-written description
_SPECIAL = {
    "nh3_fraction": "Toxic ammonia share (from pH and temperature)",
    "nh3_fraction_max_6h": "Peak toxic ammonia share, last 6 h",
    "nh3_fraction_mean_6h": "Average toxic ammonia share, last 6 h",
    "do_saturation": "Max dissolved oxygen the water can hold (from temperature)",
    "do_saturation_min_6h": "Lowest oxygen-holding capacity, last 6 h",
    "tds_compensated": "TDS corrected to 25 °C",
    "ph_range_24h": "Daily pH swing (24 h) - algal bloom indicator",
    "temp_range_24h": "Daily temperature swing (24 h)",
    "turbidity_x_ph_range": "Turbidity x daily pH swing - bloom indicator",
    "hour_sin": "Time of day",
    "hour_cos": "Time of day",
    "missing_frac_1h": "Missing data, last hour",
}


def describe_feature(name: str) -> str:
    """Plain-English label for a model feature, shown next to each entry in `top_factors`."""
    if name in _SPECIAL:
        return _SPECIAL[name]
    parts = name.split("_")
    sensor = _SENSOR.get(parts[0], parts[0])
    if len(parts) == 1:                      # bare sensor name = the current (5-min) reading
        return f"{sensor} (current)"
    kind, window = parts[1], parts[-1]
    if kind in _STAT:
        return f"{sensor} {_STAT[kind]}, last {window}"
    if kind == "delta":
        return f"{sensor} change over last {window}"
    if kind == "dev":
        return f"{sensor} vs its 24 h average"
    if kind == "ewm":
        return f"{sensor} smoothed trend (6 h)"
    # A feature was added to app/features.py without a description here: the web app would show the raw name
    logger.warning("No description for feature %r - add one to app/explain.py", name)
    return name


def advice_for(row: pd.Series, status: str) -> list[str]:
    """Simple operator guidance. Rule-based - not part of the ML model.

    `row` is the latest feature row (cleaned 5-min values), `status` the predicted class.
    The thresholds are tilapia / Indian major carp ranges and are independent of the model,
    so advice can appear even when the predicted status is Healthy.
    """
    tips = []
    T, ph, tds, turb = row.get("temperature"), row.get("ph"), row.get("tds"), row.get("turbidity")
    ph_swing = row.get("ph_range_24h")
    nh3 = row.get("nh3_fraction")
    # pd.notna guards every rule: a sensor that is offline (NaN) simply produces no advice
    if pd.notna(ph_swing) and ph_swing > 1.5:
        tips.append("Large daily pH swing - likely algal bloom. Expect low oxygen before dawn: run aerators "
                    "at night and consider a partial water exchange.")
    if pd.notna(ph) and ph > 8.8:
        tips.append("pH is high - ammonia becomes more toxic. Reduce feeding and check ammonia.")
    if pd.notna(ph) and ph < 6.5:
        tips.append("pH is low - consider liming and check for heavy rain runoff.")
    if pd.notna(T) and T > 32:
        tips.append("Water is hot - oxygen is low at high temperature. Increase aeration, add shade or deepen water.")
    if pd.notna(T) and T < 22:
        tips.append("Water is cold for tilapia/carps - fish eat less; reduce feeding.")
    if pd.notna(turb) and turb > 80:
        tips.append("High turbidity - check for runoff, overfeeding or a bloom.")
    if pd.notna(tds) and tds > 1000:
        tips.append("TDS is high - schedule a water exchange.")
    if pd.notna(nh3) and nh3 > 0.1:
        tips.append("Conditions favour toxic ammonia (high pH and temperature) - avoid overfeeding.")
    # Critical with no specific rule triggered: the model saw a pattern the simple rules do not cover
    if status == "Critical" and not tips:
        tips.append("Critical conditions detected - inspect the pond, check aeration and dissolved oxygen now.")
    if status == "Critical":
        tips.append("Measure dissolved oxygen and ammonia manually - these sensors do not measure them directly.")
    logger.debug("advice for status %s: %d tip(s)", status, len(tips))
    return tips
