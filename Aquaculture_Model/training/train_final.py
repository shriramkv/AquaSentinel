"""
Train the production model on all ponds and save models/fish_health_model.joblib.
Validation numbers stored in the bundle come from leave-one-pond-out CV (training/benchmark.py).

Steps
  1. Load the dataset and build features with the SAME pipeline the API uses (app/features.py)
  2. Leave-one-pond-out validation of the exact production configuration
  3. Final fit on all ponds
  4. Save the bundle (model + feature list + metrics) that app/main.py loads
"""
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import confusion_matrix, f1_score, precision_score, recall_score

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.features import build_features, feature_columns  # noqa: E402
from app.logging_config import setup_logging  # noqa: E402

setup_logging()
logger = logging.getLogger("training.train_final")

CLASSES = ["Healthy", "Stressed", "Critical"]
CRITICAL_WEIGHT = 1.5           # favour catching Critical events over avoiding false alarms
# Same hyper-parameters as m_lgb() in benchmark.py - keep the two in sync
PARAMS = dict(n_estimators=300, learning_rate=0.06, num_leaves=31, min_child_samples=50, subsample=0.8,
              subsample_freq=1, colsample_bytree=0.7, reg_lambda=1.0, class_weight="balanced",
              random_state=42, verbose=-1, n_jobs=2)


def decide(P, w=CRITICAL_WEIGHT):
    """Multiply the Critical probability by `w` and renormalise (mirrors _proba() in app/main.py)."""
    Q = P.copy()
    Q[:, 2] *= w
    return Q / Q.sum(1, keepdims=True)


DATA_PATH = ROOT / "training/aquaculture_fish_health_dataset.csv"
logger.info("Loading %s", DATA_PATH)
raw = pd.read_csv(DATA_PATH, parse_dates=["timestamp"])
logger.info("Loaded %s rows from %d pond(s), %s .. %s", f"{len(raw):,}", raw.pond_id.nunique(),
            raw.timestamp.min(), raw.timestamp.max())

t0 = time.time()
feats, fault_reports = build_features(raw)
for pond_id, rep in fault_reports.items():
    logger.info("Cleaning %s: %s", pond_id,
                ", ".join(f"{s} {v['out_of_range']} out-of-range / {v['spikes']} spikes" for s, v in rep.items()))
# Attach the label of each 5-min step; rows without a matching label are dropped by the inner join
df = feats.merge(raw[["timestamp", "pond_id", "health_status"]], on=["timestamp", "pond_id"])
df = df[df.history_steps >= 12].reset_index(drop=True)       # need 1 h of history, as the API does
y = df.health_status.map({c: i for i, c in enumerate(CLASSES)}).to_numpy()
FEATS = feature_columns(feats)
logger.info("Built %d features for %s rows in %.0f s; class balance: %s", len(FEATS), f"{len(df):,}", time.time() - t0,
            ", ".join(f"{c} {np.mean(y == i):.1%}" for i, c in enumerate(CLASSES)))

# ---- leave-one-pond-out validation for the exact production configuration
P = np.zeros((len(df), 3))                  # out-of-fold probabilities: every row is predicted by a model
for pond in sorted(df.pond_id.unique()):    # that never saw its pond
    t0 = time.time()
    te = (df.pond_id == pond).to_numpy()
    tr = np.flatnonzero(~te)[::2]           # every 2nd row: neighbouring 5-min rows are near-duplicates
    m = lgb.LGBMClassifier(**PARAMS).fit(df.loc[tr, FEATS].to_numpy(), y[tr])
    P[te] = m.predict_proba(df.loc[te, FEATS].to_numpy())
    logger.info("Fold %s: trained on %s rows, tested on %s rows, macro-F1 %.3f (%.0f s)", pond, f"{len(tr):,}",
                f"{te.sum():,}", f1_score(y[te], decide(P[te]).argmax(1), average="macro"), time.time() - t0)
pred = decide(P).argmax(1)
validation = {
    "method": "leave-one-pond-out (train on 4 ponds, test on the unseen 5th)",
    "macro_f1": round(f1_score(y, pred, average="macro"), 4),
    "per_class": {c: {"precision": round(precision_score(y, pred, labels=[i], average="macro"), 4),
                      "recall": round(recall_score(y, pred, labels=[i], average="macro"), 4),
                      "f1": round(f1_score(y, pred, labels=[i], average="macro"), 4)}
                  for i, c in enumerate(CLASSES)},
    "per_pond_macro_f1": {p: round(f1_score(y[(df.pond_id == p).to_numpy()],
                                            pred[(df.pond_id == p).to_numpy()], average="macro"), 4)
                          for p in sorted(df.pond_id.unique())},
    "confusion_matrix": {"labels": CLASSES, "rows_true_cols_pred": confusion_matrix(y, pred).tolist()},
}
logger.info("Validation (critical_weight=%s): macro-F1 %.4f", CRITICAL_WEIGHT, validation["macro_f1"])
for c, s in validation["per_class"].items():
    logger.info("  %-8s precision %.4f  recall %.4f  f1 %.4f", c, s["precision"], s["recall"], s["f1"])
logger.info("  per-pond macro-F1: %s", validation["per_pond_macro_f1"])
logger.info("  confusion matrix (rows = true, columns = predicted):\n%s",
            pd.DataFrame(confusion_matrix(y, pred), index=CLASSES, columns=CLASSES).to_string())

# ---- final fit on all ponds
t0 = time.time()
model = lgb.LGBMClassifier(**PARAMS).fit(df[FEATS].to_numpy(), y)
logger.info("Final model fitted on all %s rows in %.0f s", f"{len(df):,}", time.time() - t0)
# Gain importance: how much each feature reduced the loss, summed over all splits that use it
imp = pd.Series(model.booster_.feature_importance("gain"), index=FEATS).sort_values(ascending=False)
logger.info("Top features (share of total gain):\n%s", (imp / imp.sum()).head(12).round(3).to_string())

# Everything the API needs travels in one file, so the model and its feature list cannot drift apart
bundle = {
    "models": [model],
    "features": FEATS,
    "classes": CLASSES,
    "critical_weight": CRITICAL_WEIGHT,
    "model_name": "LightGBM gradient boosting on engineered time-window features",
    "version": "1.0.0",
    "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "validation": validation,
    "top_features": (imp / imp.sum()).head(15).round(4).to_dict(),
    "notes": "Trained on simulated data (5 ponds, 90 days, tilapia/carp ranges). Retrain/fine-tune on real "
             "pond data before relying on it operationally.",
}
(ROOT / "models").mkdir(exist_ok=True)
MODEL_OUT = ROOT / "models/fish_health_model.joblib"
joblib.dump(bundle, MODEL_OUT, compress=3)
pd.DataFrame(confusion_matrix(y, pred), index=CLASSES, columns=CLASSES).to_csv(ROOT / "training/confusion_matrix.csv")
logger.info("Saved model bundle to %s (%.1f MB) and confusion matrix to %s", MODEL_OUT,
            MODEL_OUT.stat().st_size / 1e6, ROOT / "training/confusion_matrix.csv")
