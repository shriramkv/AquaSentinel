"""CPCB Designated Best Use criteria: lookup and classification from the parameters the boat measures."""
from __future__ import annotations

import json

from . import config

CRITERIA = json.loads((config.DATA_DIR / "cpcb_criteria.json").read_text())


def _meets(c: dict, ph: float | None, do: float | None, ec: float | None) -> tuple[bool, list[str]]:
    fails = []
    if ph is not None and "ph_min" in c and not (c["ph_min"] <= ph <= c["ph_max"]):
        fails.append(f"pH {ph:.2f} outside {c['ph_min']}-{c['ph_max']}")
    if do is not None and "do_mg_l_min" in c and do < c["do_mg_l_min"]:
        fails.append(f"DO {do:.2f} mg/L below {c['do_mg_l_min']}")
    if ec is not None and "ec_us_cm_max" in c and ec > c["ec_us_cm_max"]:
        fails.append(f"conductivity {ec:.0f} µS/cm above {c['ec_us_cm_max']}")
    return not fails, fails


def classify(ph: float | None, do: float | None, ec: float | None) -> dict:
    """Best DBU class whose pH / DO / conductivity limits are met.

    Coliform, BOD, ammonia, SAR and boron are not measured by the boat, so this is an upper
    bound on the class: lab analysis can only lower it.
    """
    checks = []
    best = None
    for c in CRITERIA["classes"]:
        ok, fails = _meets(c["criteria"], ph, do, ec)
        checks.append({"class": c["class"], "use": c["use"], "meets_measured_limits": ok, "failed": fails})
        if ok and best is None:
            best = c
    return {
        "best_class_on_measured_parameters": best["class"] if best else "below E",
        "use": best["use"] if best else "Does not meet the measured limits of any class",
        "checks": checks,
        "caveat": "Based on pH, DO and conductivity only. " + ", ".join(CRITERIA["not_measured_by_boat"]) +
                  " are not measured, so the true class may be lower.",
    }


def status(param: str, value: float | None) -> str:
    """Tile status for one reading: ok | watch | breach | none.

    pH and DO against class D (wildlife and fisheries), conductivity against class E, nitrate against
    the BIS drinking-water reference. Temperature and turbidity have no limit for lake water: "none".
    """
    if param in ("temp", "turbidity"):
        return "none"
    if value is None:
        return "ok"
    if param == "do":
        return "breach" if value < 4.0 else "watch" if value < 5.0 else "ok"
    if param == "ph":
        return "breach" if not 6.5 <= value <= 8.5 else "watch" if not 6.8 <= value <= 8.2 else "ok"
    if param == "ec":
        return "breach" if value > 2250 else "watch" if value > 1500 else "ok"
    if param == "nitrate":
        return "breach" if value > 45 else "watch" if value > 10 else "ok"
    return "ok"
