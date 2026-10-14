"""
Simulated aquaculture water-quality dataset for fish health classification.

Target : health_status in {Healthy, Stressed, Critical}
Ponds  : 5 (different management quality)
Period : 90 days, 5-minute sampling (2026-03-01 .. 2026-05-29, Indian summer + pre-monsoon)
Species: freshwater tilapia / Indian major carps (ranges in SPECIES below)

Pipeline per pond
  1. True water physics: temperature, pH, TDS, turbidity  (+ hidden DO and ammonia)
  2. Event library: feeding, rain, algal bloom, heat wave, overfeeding,
     aerator failure, water exchange, disease outbreak
  3. Labels from TRUE values: per-parameter stress -> weighted + worst-case score
     -> 6 h cumulative exposure (EMA) -> acute overrides -> class -> label noise
  4. Sensor layer mimicking the ESP32 rig: DS18B20 noise/resolution/-127 faults,
     pH probe drift, turbidity biofouling, TDS without temperature compensation
     (as in the current firmware), 12-bit ADC quantisation, Wi-Fi dropouts, spikes

Outputs
  aquaculture_fish_health_dataset.csv  -> model features (sensor readings) + label
  aquaculture_ground_truth.csv         -> true values, hidden DO/ammonia, events, stress score
  Both files are written next to this script (training/), where train_final.py and
  benchmark.py read the dataset from, whichever directory the script is started in.
"""
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.logging_config import setup_logging  # noqa: E402

logger = logging.getLogger("training.generate_dataset")

OUT_DIR = Path(__file__).resolve().parent      # training/
SEED = 42
# One shared generator: every draw below advances it, so the output depends on the ORDER of the
# rng calls. Adding, removing or reordering a call changes the whole dataset, even with the same seed.
rng = np.random.default_rng(SEED)

START = pd.Timestamp("2026-03-01 00:00")
DAYS = 90
STEP_MIN = 5
N = DAYS * 24 * 60 // STEP_MIN                 # samples per pond (25,920)
ts = pd.date_range(START, periods=N, freq=f"{STEP_MIN}min")
hour = (ts.hour + ts.minute / 60).to_numpy()   # time of day, 0..24
day = np.arange(N) * STEP_MIN / 1440.0         # days since START (fractional)
STEPS_PER_HOUR = 60 // STEP_MIN
STEPS_PER_DAY = 24 * STEPS_PER_HOUR

# ----------------------------------------------------------------------------- species config
SPECIES = {
    #            opt_lo opt_hi crit_lo crit_hi  (stress 0 inside optimal, 1 at critical)
    "temp":     (26.0, 30.0, 18.0, 35.0),
    "ph":       (6.5, 8.5, 5.5, 9.5),
    "tds":      (150., 600., 50., 2000.),
    "turb":     (0.0, 45.0, 0.0, 150.),
    "do":       (5.0, 99., 2.0, 99.),
    "nh3":      (0.0, 0.02, 0.0, 0.10),   # un-ionised ammonia, mg/L
}
# Share of each parameter in the weighted stress score (sums to 1). DO and ammonia matter most
# for the label but are NOT in the sensor data - the model has to infer them indirectly.
WEIGHTS = {"do": 0.25, "nh3": 0.20, "temp": 0.20, "ph": 0.15, "tds": 0.10, "turb": 0.10}
LETHAL = {"temp": (14.0, 37.5), "ph": (5.0, 10.0), "do_min": 1.5, "nh3_max": 0.30}   # acute: Critical at once
EMA_HOURS = 6                                    # memory of the cumulative-exposure score
THRESH_STRESSED, THRESH_CRITICAL = 0.20, 0.42    # score cut-offs between the three classes
LABEL_NOISE = 0.04                               # target share of labels flipped near the cut-offs

# ----------------------------------------------------------------------------- ponds
PONDS = [
    # id, management, stocking density (rel), aeration (rel), depth (m), base TDS
    dict(pond_id="P1", mgmt="excellent", density=0.7, aeration=1.3, depth=1.8, tds0=220),
    dict(pond_id="P2", mgmt="good",      density=0.9, aeration=1.1, depth=1.5, tds0=300),
    dict(pond_id="P3", mgmt="average",   density=1.0, aeration=1.0, depth=1.2, tds0=350),
    dict(pond_id="P4", mgmt="average",   density=1.2, aeration=0.9, depth=1.2, tds0=420),
    dict(pond_id="P5", mgmt="poor",      density=1.5, aeration=0.7, depth=0.9, tds0=480),
]


def pulse(start, dur_steps, shape="smooth"):
    """Event envelope 0..1 over the whole timeline."""
    e = np.zeros(N)
    end = min(N, start + dur_steps)
    k = np.arange(end - start)
    if shape == "smooth":                      # ramp up, hold, ramp down
        e[start:end] = np.sin(np.pi * k / max(dur_steps, 1)) ** 0.5
    elif shape == "decay":                     # instant jump, exponential recovery
        e[start:end] = np.exp(-k / (dur_steps / 4))
    elif shape == "box":
        e[start:end] = 1.0
    return e


def ar1(sd, tau_steps):
    """Slow random weather / process noise."""
    a = np.exp(-1 / tau_steps)
    x = np.zeros(N)
    eps = rng.normal(0, sd * np.sqrt(1 - a * a), N)
    for i in range(1, N):
        x[i] = a * x[i - 1] + eps[i]
    return x


def schedule(n_events, dur_h, earliest_day=2, bias_late=False):
    """Random event start indices (rain is biased to May / pre-monsoon)."""
    starts = []
    for _ in range(n_events):
        if bias_late:
            d = rng.triangular(10, 85, 88)
        else:
            d = rng.uniform(earliest_day, DAYS - 3)
        starts.append(int(d * STEPS_PER_DAY))
    durs = [int(rng.uniform(*dur_h) * STEPS_PER_HOUR) for _ in starts]
    return list(zip(starts, durs))


def do_saturation(T):
    """Dissolved-oxygen saturation (mg/L) in fresh water, APHA polynomial."""
    return 14.652 - 0.41022 * T + 0.007991 * T**2 - 0.000077774 * T**3


def nh3_fraction(T, ph):
    """Un-ionised ammonia fraction (Emerson et al. 1975)."""
    pka = 0.09018 + 2729.92 / (T + 273.15)
    return 1.0 / (1.0 + 10 ** (pka - ph))


def stress(x, key):
    """Stress 0..1 for one parameter: 0 inside the optimal band, rising linearly to 1 at the critical limit."""
    lo, hi, clo, chi = SPECIES[key]
    s = np.zeros_like(x, dtype=float)
    above = x > hi
    below = x < lo
    s[above] = (x[above] - hi) / (chi - hi)
    if clo < lo:                               # parameters such as turbidity have no harmful low side
        s[below] = (lo - x[below]) / (lo - clo)
    return np.clip(s, 0, 1)


def simulate_pond(p):
    """Simulate one pond. Returns (sensor_df, truth_df): what the ESP32 would report, and the hidden truth."""
    logger.info("Simulating pond %s (%s management, density %.1f, aeration %.1f, depth %.1f m)",
                p["pond_id"], p["mgmt"], p["density"], p["aeration"], p["depth"])
    ev = np.full(N, "", dtype=object)          # active event names per step, "|"-separated

    def tag(env, name):
        """Record `name` in the event log wherever its envelope is noticeably active."""
        on = env > 0.15
        ev[on] = np.where(ev[on] == "", name, ev[on] + "|" + name)

    # ---------------- events
    rain = np.zeros(N)
    for s, d in schedule(rng.integers(7, 12), (2, 8), bias_late=True):
        intensity = rng.uniform(0.4, 1.0)
        rain += intensity * pulse(s, d + 18 * STEPS_PER_HOUR, "decay")
    rain = np.clip(rain, 0, 1.5)
    tag(rain, "rain")

    heat = np.zeros(N)
    for s, d in schedule(rng.integers(2, 4), (72, 144)):
        heat = np.maximum(heat, pulse(s, d, "smooth") * rng.uniform(0.6, 1.0))
    tag(heat, "heat_wave")

    bloom = np.zeros(N)
    n_bloom = rng.integers(2, 4) if p["density"] >= 1.0 else rng.integers(1, 3)
    for s, d in schedule(n_bloom, (96, 240)):
        bloom = np.maximum(bloom, pulse(s, d, "smooth") * rng.uniform(0.6, 1.0))
    tag(bloom, "algal_bloom")

    overfeed = np.zeros(N)
    n_of = {"excellent": 1, "good": 2, "average": 3, "poor": 4}[p["mgmt"]]
    for s, d in schedule(n_of, (48, 120)):
        overfeed = np.maximum(overfeed, pulse(s, d, "box"))
    tag(overfeed, "overfeeding")

    aer_fail = np.zeros(N)
    n_af = {"excellent": 1, "good": 1, "average": 2, "poor": 3}[p["mgmt"]]
    for s, d in schedule(n_af, (3, 9)):
        s = (s // STEPS_PER_DAY) * STEPS_PER_DAY + int(rng.uniform(22, 26) * STEPS_PER_HOUR)  # night
        aer_fail[s:min(N, s + d)] = 1.0
    tag(aer_fail, "aerator_failure")

    disease = np.zeros(N)
    if p["mgmt"] in ("average", "poor") and rng.random() < 0.8:
        for s, d in schedule(1, (120, 240), earliest_day=30):
            disease = pulse(s, d, "smooth")
    tag(disease, "disease")

    exch_interval = {"excellent": 8, "good": 10, "average": 12, "poor": 16}[p["mgmt"]]
    exchange = np.zeros(N, dtype=bool)
    d0 = rng.uniform(3, exch_interval)
    while d0 < DAYS - 1:
        exchange[int(d0 * STEPS_PER_DAY + 10 * STEPS_PER_HOUR)] = True
        d0 += exch_interval + rng.uniform(-2, 2)
    exch_env = np.zeros(N)
    for i in np.where(exchange)[0]:
        exch_env[i:i + 3 * STEPS_PER_HOUR] = 1
    tag(exch_env, "water_exchange")

    feed_times = (np.isclose(hour, 8.0) | np.isclose(hour, 17.0))
    feed = np.zeros(N)
    for i in np.where(feed_times)[0]:
        k = np.arange(min(N - i, 18))
        feed[i:i + len(k)] += np.exp(-k / 6)

    # ---------------- temperature (deg C)
    season = 26.5 + 2.5 * np.sin(np.pi * day / 70) - 1.0 * np.clip((day - 70) / 20, 0, 1)
    amp = 2.6 / p["depth"]
    diurnal = amp * np.sin(2 * np.pi * (hour - 9.0) / 24)        # peak ~15:00
    temp = season + diurnal + ar1(0.8, 2 * STEPS_PER_DAY) + 4.5 * heat - 2.5 * rain
    temp += rng.normal(0, 0.05, N)

    # ---------------- phytoplankton activity drives DO / pH daily swings
    phyto = 0.5 + 0.15 * p["density"] + 1.2 * bloom - 0.3 * np.clip(rain, 0, 1)
    phyto = np.clip(phyto, 0.2, None)
    light = np.sin(2 * np.pi * (hour - 10.0) / 24)               # peak ~16:00, min ~04:00

    # ---------------- ammonia (total ammonia nitrogen, mg/L) and TDS (ppm) - stateful
    tan = np.zeros(N); tds = np.zeros(N)
    tan[0] = 0.25 * p["density"]; tds[0] = p["tds0"]
    feed_in = 0.00045 * p["density"] * (1 + 1.8 * overfeed + 1.5 * disease)
    k_remove = 0.0011 * (0.8 + 0.2 * p["aeration"])   # per step
    for i in range(1, N):
        t = tan[i - 1] + feed_in[i] - k_remove * tan[i - 1]
        t -= 0.0025 * rain[i] * tan[i - 1]
        x = tds[i - 1] * (1 + (0.00004 + 0.000006 * max(temp[i] - 25, 0)))  # feed + evaporation
        x -= 0.0008 * rain[i] * x
        if exchange[i]:
            frac = rng.uniform(0.25, 0.4)
            t *= (1 - frac)
            x = x * (1 - frac) + 180 * frac
        tan[i] = max(t, 0.02)
        tds[i] = max(x, 60)

    # ---------------- pH
    ph_base = 7.4 + rng.uniform(-0.2, 0.2) + ar1(0.12, 3 * STEPS_PER_DAY)
    ph = (ph_base + 0.6 * phyto * light + 0.9 * bloom * np.clip(light, 0, None)
          - 0.6 * np.clip(rain, 0, 1) - 0.15 * np.clip(tan - 1.0, 0, None) - 0.3 * disease)

    # ---------------- turbidity (NTU)
    turb_base = rng.uniform(15, 25) + 8 * (p["density"] - 1)
    turb = (turb_base + ar1(3, STEPS_PER_DAY) + 10 * feed * p["density"]
            + 90 * rain + 55 * bloom + 25 * disease)
    turb = np.clip(turb, 2, None)

    # ---------------- dissolved oxygen (hidden, mg/L)
    do = (do_saturation(temp) * (0.66 + 0.12 * p["aeration"])
          + 2.2 * phyto * light
          - 1.4 * bloom * np.clip(-light, 0, None)                # night crash during bloom
          - 0.6 * (p["density"] - 1) - 1.5 * disease - 0.3 * np.clip(rain, 0, 1)
          + ar1(0.3, STEPS_PER_DAY))
    # aerator failure: DO decays while it lasts, recovers afterwards
    deficit = np.zeros(N)
    for i in range(1, N):
        deficit[i] = deficit[i - 1] + (0.55 / STEPS_PER_HOUR if aer_fail[i] else -deficit[i - 1] * 0.08)
    do = np.clip(do - deficit, 0.2, None)

    nh3 = tan * nh3_fraction(temp, ph)          # only the un-ionised part of ammonia is toxic

    # ---------------- labels from TRUE values
    S = {
        "temp": stress(temp, "temp"), "ph": stress(ph, "ph"), "tds": stress(tds, "tds"),
        "turb": stress(turb, "turb"), "do": stress(do, "do"), "nh3": stress(nh3, "nh3"),
    }
    weighted = sum(WEIGHTS[k] * S[k] for k in S)                 # overall water quality
    worst = np.max(np.vstack(list(S.values())), axis=0)          # one bad parameter is enough to hurt fish
    interact = 0.5 * S["temp"] * S["do"] + 0.5 * S["ph"] * S["nh3"]   # heat+low oxygen, high pH+ammonia compound
    inst = np.clip(0.45 * weighted + 0.45 * worst + interact, 0, 1)  # instantaneous stress, 0..1

    # Fish respond to exposure over time, not to a single reading: smooth with a 6 h exponential average
    alpha = 1 - np.exp(-STEP_MIN / (EMA_HOURS * 60))
    score = np.zeros(N)
    for i in range(1, N):
        score[i] = score[i - 1] + alpha * (inst[i] - score[i - 1])
    # also keep a slower memory so recovery after a bad event takes time
    score = np.maximum(score, pd.Series(inst).rolling(STEPS_PER_HOUR * 2, min_periods=1).mean().to_numpy() * 0.9)

    acute = ((temp < LETHAL["temp"][0]) | (temp > LETHAL["temp"][1]) | (ph < LETHAL["ph"][0])
             | (ph > LETHAL["ph"][1]) | (do < LETHAL["do_min"]) | (nh3 > LETHAL["nh3_max"]))
    # acute exposure keeps fish critical for 2 h afterwards
    acute = pd.Series(acute.astype(float)).rolling(2 * STEPS_PER_HOUR, min_periods=1).max().to_numpy() > 0

    cls = np.where(score >= THRESH_CRITICAL, 2, np.where(score >= THRESH_STRESSED, 1, 0))
    cls[acute] = 2

    # Label noise: flip a small share of labels to a neighbouring class, only close to a threshold
    # (where a human observer would also be unsure) and never during an acute event
    true_cls = cls.copy()
    near = (np.abs(score - THRESH_STRESSED) < 0.05) | (np.abs(score - THRESH_CRITICAL) < 0.05)
    flip = near & (rng.random(N) < LABEL_NOISE / max(near.mean(), 1e-6) * 0.25) & ~acute
    cls[flip] = np.clip(cls[flip] + rng.choice([-1, 1], flip.sum()), 0, 2)

    names = np.array(["Healthy", "Stressed", "Critical"])
    logger.info("  %s labels: %s | %d label(s) flipped by noise, %.1f %% of steps acute",
                p["pond_id"], ", ".join(f"{n} {np.mean(cls == i):.1%}" for i, n in enumerate(names)),
                int(flip.sum()), 100 * acute.mean())
    logger.debug("  %s events (share of steps): %s", p["pond_id"],
                 pd.Series(ev)[ev != ""].str.split("|").explode().value_counts(normalize=False)
                 .div(N).round(3).to_dict())

    # ---------------- sensor layer (ESP32 rig)
    # DS18B20: +-0.5 C accuracy, 0.0625 C resolution, occasional -127 disconnect
    t_meas = temp + rng.normal(0, 0.2) + rng.normal(0, 0.08, N)
    t_meas = np.round(t_meas / 0.0625) * 0.0625
    t_meas[rng.random(N) < 0.0005] = -127.0

    # TDS: firmware does not apply temperature compensation (local-variable bug), so
    # the reading carries the ~2 %/C conductivity dependence. 12-bit ADC -> 1.61 ppm steps.
    tds_meas = tds * (1 + 0.02 * (temp - 25)) * (1 + rng.normal(0, 0.015, N))
    tds_meas = np.round(tds_meas / 1.612) * 1.612

    # pH: probe drift (recalibrated every 14 days), ADC quantisation 0.0036 pH
    drift_rate = rng.uniform(0.01, 0.03) * rng.choice([-1, 1])
    days_since_cal = day % 14
    ph_meas = ph + drift_rate * days_since_cal + rng.normal(0, 0.03, N)
    ph_meas = np.round(ph_meas / 0.003626) * 0.003626

    # Turbidity: biofouling bias that grows until weekly cleaning, ADC steps 0.474 NTU
    foul = rng.uniform(1.0, 2.5) * (day % 7) * (1.3 if p["mgmt"] == "poor" else 1.0)
    turb_meas = turb + foul + rng.normal(0, 1.0, N) + turb * rng.normal(0, 0.03, N)
    turb_meas = np.clip(np.round(turb_meas / 0.474) * 0.474, 0, None)

    meas = {"temperature_c": t_meas, "tds_ppm": tds_meas, "turbidity_ntu": turb_meas, "ph": ph_meas}

    # random single-sensor ADC spikes
    for k, scale in [("tds_ppm", 3.0), ("turbidity_ntu", 4.0), ("ph", None)]:
        idx = rng.random(N) < 0.0005
        if scale:
            meas[k][idx] *= scale
        else:
            meas[k][idx] = rng.choice([0.0, 14.85], idx.sum())   # ADC rail values

    # Wi-Fi dropouts: whole rows missing in short bursts (~1 % of rows)
    miss = np.zeros(N, dtype=bool)
    for s in rng.choice(N, int(N * 0.0025), replace=False):
        miss[s:s + rng.integers(1, 8)] = True
    for k in meas:
        meas[k] = meas[k].astype(float)
        meas[k][miss] = np.nan
    logger.info("  %s sensor layer: %.2f %% of rows lost to Wi-Fi dropouts, %d DS18B20 -127 fault(s)",
                p["pond_id"], 100 * miss.mean(), int(np.sum(meas["temperature_c"] == -127.0)))

    sensor_df = pd.DataFrame({
        "timestamp": ts, "pond_id": p["pond_id"],
        **{k: np.round(v, 3) for k, v in meas.items()},
        "health_status": names[cls],
    })
    truth_df = pd.DataFrame({
        "timestamp": ts, "pond_id": p["pond_id"], "pond_management": p["mgmt"],
        "true_temperature_c": temp.round(3), "true_tds_ppm": tds.round(2),
        "true_turbidity_ntu": turb.round(2), "true_ph": ph.round(3),
        "hidden_do_mg_l": do.round(3), "hidden_tan_mg_l": tan.round(4),
        "hidden_nh3_unionized_mg_l": nh3.round(5),
        "active_events": ev, "sensor_dropout": miss,
        "stress_score": score.round(4), "label_noise_flipped": flip,
        "health_status_clean": names[true_cls], "health_status": names[cls],
    })
    return sensor_df, truth_df


if __name__ == "__main__":
    setup_logging()
    logger.info("Generating %d ponds x %d days at %d-min steps (%s rows per pond), seed %d",
                len(PONDS), DAYS, STEP_MIN, f"{N:,}", SEED)
    sens, truth = zip(*(simulate_pond(p) for p in PONDS))
    sens = pd.concat(sens, ignore_index=True)
    truth = pd.concat(truth, ignore_index=True)
    sens.to_csv(OUT_DIR / "aquaculture_fish_health_dataset.csv", index=False)
    truth.to_csv(OUT_DIR / "aquaculture_ground_truth.csv", index=False)
    logger.info("Wrote %s", OUT_DIR / "aquaculture_fish_health_dataset.csv")
    logger.info("Wrote %s", OUT_DIR / "aquaculture_ground_truth.csv")

    logger.info("rows: %s", f"{len(sens):,}")
    logger.info("Class balance:\n%s", sens["health_status"].value_counts(normalize=True).round(3).to_string())
    logger.info("Class balance per pond:\n%s",
                pd.crosstab(sens["pond_id"], sens["health_status"], normalize="index").round(3).to_string())
    logger.info("Sensor readings:\n%s", sens.describe().round(2).to_string())
    logger.info("Hidden variables:\n%s",
                truth[["hidden_do_mg_l", "hidden_nh3_unionized_mg_l", "stress_score"]].describe().round(3).to_string())
