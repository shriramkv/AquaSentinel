"""
Leave-one-pond-out benchmark: train on 4 ponds, test on the unseen 5th, repeat for every pond.
This measures how the model will behave on a pond it has never seen, which is the real use case.

Compares five models plus a LightGBM+XGBoost ensemble and writes training/benchmark_results.csv.
The out-of-fold probabilities are saved to training/_oof_probs.npy for further analysis
(e.g. choosing CRITICAL_WEIGHT).
"""
import logging
import sys, time, json
from pathlib import Path

import numpy as np
import pandas as pd
import lightgbm as lgb
import xgboost as xgb
from sklearn.ensemble import RandomForestClassifier
from sklearn.neural_network import MLPClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import f1_score, balanced_accuracy_score, recall_score, precision_score
from sklearn.utils.class_weight import compute_sample_weight

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.features import build_features, feature_columns, SENSORS  # noqa: E402
from app.logging_config import setup_logging  # noqa: E402

setup_logging()
logger = logging.getLogger("training.benchmark")

CLASSES = ["Healthy", "Stressed", "Critical"]
SEED = 42

DATA_PATH = ROOT / "training/aquaculture_fish_health_dataset.csv"
logger.info("Loading %s", DATA_PATH)
raw = pd.read_csv(DATA_PATH, parse_dates=["timestamp"])
feats, _ = build_features(raw)                                  # same pipeline as the API
df = feats.merge(raw[["timestamp", "pond_id", "health_status"]], on=["timestamp", "pond_id"])
df = df[df.history_steps >= 12].reset_index(drop=True)          # need 1 h history
y = df.health_status.map({c: i for i, c in enumerate(CLASSES)}).to_numpy()
FEATS = feature_columns(feats)                                  # all engineered features
RAW = ["temperature", "tds", "turbidity", "ph", "hour_sin", "hour_cos"]   # baseline: current readings + time
logger.info("%s rows, %d engineered features, %d pond(s); class balance: %s", f"{len(df):,}", len(FEATS),
            df.pond_id.nunique(), ", ".join(f"{c} {np.mean(y == i):.1%}" for i, c in enumerate(CLASSES)))


def seq_matrix(frame, steps=72, stride=6):
    """Raw 6 h sequence (every 30 min) for the MLP: the network learns its own features."""
    cols = ["temperature", "tds", "turbidity", "ph"]
    parts = []
    for pid, g in frame.groupby("pond_id", sort=False):
        x = g[cols].ffill().bfill()                # the network cannot handle NaN
        # 12 lagged copies (now, 30 min ago, ... 5.5 h ago) of each sensor = the "sequence"
        lags = [x.shift(k).add_suffix(f"_l{k}") for k in range(0, steps, stride)]
        parts.append(pd.concat(lags + [g[["hour_sin", "hour_cos"]]], axis=1).bfill())
    return pd.concat(parts).loc[frame.index].to_numpy(dtype=np.float32)


SEQ = seq_matrix(df)


# Each m_* function trains one model on (Xtr, ytr) and returns class probabilities for Xte.
# All of them compensate for the class imbalance (Healthy is ~80 % of the rows).
def m_lgb(Xtr, ytr, Xte):
    # Same hyper-parameters as PARAMS in train_final.py - keep the two in sync
    m = lgb.LGBMClassifier(n_estimators=300, learning_rate=0.06, num_leaves=31, min_child_samples=50,
                           subsample=0.8, subsample_freq=1, colsample_bytree=0.7, reg_lambda=1.0,
                           class_weight="balanced", random_state=SEED, verbose=-1, n_jobs=2)
    m.fit(Xtr, ytr)
    return m.predict_proba(Xte)


def m_xgb(Xtr, ytr, Xte):
    m = xgb.XGBClassifier(n_estimators=300, learning_rate=0.1, max_depth=6, subsample=0.8,
                          colsample_bytree=0.7, min_child_weight=5, tree_method="hist",
                          random_state=SEED, n_jobs=2)
    m.fit(Xtr, ytr, sample_weight=compute_sample_weight("balanced", ytr))
    return m.predict_proba(Xte)


def m_rf(Xtr, ytr, Xte):
    m = RandomForestClassifier(n_estimators=200, min_samples_leaf=5, max_samples=0.3, max_features="sqrt",
                               class_weight="balanced_subsample", random_state=SEED, n_jobs=2)
    m.fit(Xtr, ytr)
    return m.predict_proba(Xte)


def m_mlp(Xtr, ytr, Xte):
    sc = StandardScaler().fit(Xtr)                 # fitted on the training ponds only: no leakage
    Xtr, Xte = sc.transform(Xtr), sc.transform(Xte)
    rng = np.random.default_rng(SEED)
    # oversample minority classes so the network sees them as often as Healthy
    idx = np.concatenate([rng.choice(np.flatnonzero(ytr == c), (ytr == 0).sum() // 3, replace=True)
                          for c in range(3)])
    m = MLPClassifier(hidden_layer_sizes=(128, 64), alpha=1e-3, batch_size=512, learning_rate_init=1e-3,
                      max_iter=25, early_stopping=True, n_iter_no_change=5, random_state=SEED)
    m.fit(Xtr[idx], ytr[idx])
    return m.predict_proba(Xte)


# name -> (which input matrix to use, training function)
MODELS = {
    "LightGBM - raw readings only (baseline)": ("raw", m_lgb),
    "MLP neural net - raw 6h sequence": ("seq", m_mlp),
    "Random Forest - engineered features": ("feat", m_rf),
    "XGBoost - engineered features": ("feat", m_xgb),
    "LightGBM - engineered features": ("feat", m_lgb),
}

results, probs = {}, {}
for name, (kind, fn) in MODELS.items():
    rows, t0 = [], time.time()
    P = np.zeros((len(df), 3))
    for pond in sorted(df.pond_id.unique()):
        t_fold = time.time()
        te = (df.pond_id == pond).to_numpy()
        X = {"raw": df[RAW].to_numpy(), "feat": df[FEATS].to_numpy(), "seq": SEQ}[kind]
        tr = np.flatnonzero(~te)[::2]          # every 2nd row: neighbours are near-duplicates
        P[te] = fn(X[tr], y[tr], X[te])
        logger.info("  %s | held-out pond %s: macro-F1 %.3f (%.0f s)", name, pond,
                    f1_score(y[te], P[te].argmax(1), average="macro"), time.time() - t_fold)
    probs[name] = P
    results[name] = P
    logger.info("%s: done in %.0f s", name, time.time() - t0)

# Simple ensemble: average the two best models' out-of-fold probabilities
probs["Ensemble - LightGBM + XGBoost"] = (probs["LightGBM - engineered features"]
                                          + probs["XGBoost - engineered features"]) / 2


def score(P, mask=None):
    """Headline metrics for one model. Class 2 = Critical, class 1 = Stressed; no CRITICAL_WEIGHT applied."""
    mask = np.ones(len(y), bool) if mask is None else mask
    pred = P[mask].argmax(1)
    yt = y[mask]
    return dict(
        macro_f1=f1_score(yt, pred, average="macro"),
        balanced_acc=balanced_accuracy_score(yt, pred),
        critical_recall=recall_score(yt, pred, labels=[2], average="macro", zero_division=0),
        critical_precision=precision_score(yt, pred, labels=[2], average="macro", zero_division=0),
        stressed_f1=f1_score(yt, pred, labels=[1], average="macro", zero_division=0),
    )


table = []
for name, P in probs.items():
    s = score(P)
    per_pond = {p: f1_score(y[df.pond_id == p], P[(df.pond_id == p).to_numpy()].argmax(1), average="macro")
                for p in sorted(df.pond_id.unique())}
    # worst_pond_f1: the weakest held-out pond - how bad it can get on an unfamiliar pond
    table.append({"model": name, **{k: round(v, 3) for k, v in s.items()},
                  "worst_pond_f1": round(min(per_pond.values()), 3)})
table = pd.DataFrame(table).sort_values("macro_f1", ascending=False)
pd.set_option("display.width", 200)
logger.info("Leave-one-pond-out results:\n%s", table.to_string(index=False))
table.to_csv(ROOT / "training/benchmark_results.csv", index=False)
logger.info("Saved %s and the out-of-fold probabilities (training/_oof_probs.npy, training/_y.npy)",
            ROOT / "training/benchmark_results.csv")
np.save(ROOT / "training/_oof_probs.npy", {k: v for k, v in probs.items()}, allow_pickle=True)
np.save(ROOT / "training/_y.npy", y)
