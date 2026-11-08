"""eta_mlflow.py -- Part B: MLflow registry, multi-criteria promotion gate.

CLI (from repo root, after `source env.sh`):
    python partB/eta_mlflow.py register
    python partB/eta_mlflow.py gate [--min-rel-gain 0.60] [--max-p95-ms 10] [--max-slice-regression 0.05]
Tracking URI comes from MLFLOW_TRACKING_URI (default: sqlite:///mlflow.db).
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _p in (HERE, HERE.parent):                     # so `import eta_data` works from anywhere
    sys.path.insert(0, str(_p))

import joblib
import mlflow
import numpy as np
import pandas as pd
import sklearn
from mlflow import MlflowClient
from mlflow.models import infer_signature

import eta_data
from eta_data import FEATURES

MODEL_NAME = "eta-model"
RUSH_HOURS = [7, 8, 9, 16, 17, 18, 19]
EPS = 1e-9                                          # float tolerance for boundary comparisons


def find(path):
    """Locate a repo file (e.g. data/eta_v1.joblib) from cwd, partB/ or the repo root."""
    for base in (Path.cwd(), HERE, HERE.parent):
        if (base / path).exists():
            return str((base / path).resolve())
    raise FileNotFoundError(f"{path} not found - did you run `python eta_data.py`?")


# ----------------------------------------------------------------------------------------
# B1. custom pyfunc
# ----------------------------------------------------------------------------------------
class EtaPyfunc(mlflow.pyfunc.PythonModel):
    """Wraps a {model, feature_columns, version} bundle. Returns eta_minutes + model_version."""

    def load_context(self, context):
        b = joblib.load(context.artifacts["bundle"])
        self.model = b["model"]
        self.cols = b["feature_columns"]
        self.version = b["version"]

    def predict(self, context, model_input, params=None):
        df = pd.DataFrame(model_input)[self.cols]
        preds = np.round(self.model.predict(df), 1)
        return pd.DataFrame({"eta_minutes": preds, "model_version": self.version})


PIP_REQUIREMENTS = [f"mlflow=={mlflow.__version__}", f"scikit-learn=={sklearn.__version__}",
                    f"pandas=={pd.__version__}", f"numpy=={np.__version__}",
                    f"joblib=={joblib.__version__}"]


def register():
    mlflow.set_tracking_uri(os.environ.get("MLFLOW_TRACKING_URI", "sqlite:///mlflow.db"))
    mlflow.set_experiment("eta-serving")
    client = MlflowClient()
    fresh = eta_data.make_data(2000, seed=99)
    example = fresh[FEATURES].head(3)
    versions = {}
    for v in ("v1", "v2"):
        path = find(f"data/eta_{v}.joblib")
        bundle = joblib.load(path)
        out = pd.DataFrame({"eta_minutes": np.round(bundle["model"].predict(example), 1),
                            "model_version": bundle["version"]})
        signature = infer_signature(example, out)
        mae = float(np.abs(bundle["model"].predict(fresh[FEATURES]) - fresh.eta_minutes).mean())
        with mlflow.start_run(run_name=f"eta-{v}"):
            mlflow.log_param("bundle_version", bundle["version"])
            mlflow.log_param("model_class", type(bundle["model"]).__name__)
            mlflow.log_metric("fresh_mae", mae)
            info = mlflow.pyfunc.log_model(name="model", python_model=EtaPyfunc(),
                                           artifacts={"bundle": path}, signature=signature,
                                           input_example=example,
                                           pip_requirements=PIP_REQUIREMENTS)
        mv = mlflow.register_model(info.model_uri, MODEL_NAME)
        versions[v] = str(mv.version)
    client.set_registered_model_alias(MODEL_NAME, "prod", versions["v1"])
    client.set_registered_model_alias(MODEL_NAME, "candidate", versions["v2"])

    print(f"\nRegistry table for '{MODEL_NAME}'")
    print(f"{'version':<9}{'alias':<12}{'bundle'}")
    for v, ver in versions.items():
        alias = "prod" if v == "v1" else "candidate"
        print(f"{ver:<9}{alias:<12}{v}")
    return versions


# ----------------------------------------------------------------------------------------
# B2. promotion gate
# ----------------------------------------------------------------------------------------
def _rel_change(prod_val, cand_val):
    """(cand - prod) / prod, safe when prod == 0."""
    if prod_val == 0:
        return 0.0 if cand_val == 0 else float("inf")
    return (cand_val - prod_val) / prod_val


def decide(prod, cand, min_rel_gain=0.03, max_p95_ms=10.0, max_slice_regression=0.05):
    """Pure function. prod/cand: dicts with mae, mae_rush, mae_rain, p95_ms.

    Slice regression is RELATIVE: (cand_slice_mae - prod_slice_mae) / prod_slice_mae.
    """
    rel_gain = -_rel_change(prod["mae"], cand["mae"])          # positive = candidate better
    rush_reg = _rel_change(prod["mae_rush"], cand["mae_rush"])
    rain_reg = _rel_change(prod["mae_rain"], cand["mae_rain"])
    criteria = {
        "overall_mae_gain": bool(rel_gain >= min_rel_gain - EPS),
        "latency_p95": bool(cand["p95_ms"] <= max_p95_ms + EPS),
        "no_rush_regression": bool(rush_reg <= max_slice_regression + EPS),
        "no_rain_regression": bool(rain_reg <= max_slice_regression + EPS),
    }
    details = {"rel_gain": rel_gain, "cand_p95_ms": cand["p95_ms"],
               "rush_regression": rush_reg, "rain_regression": rain_reg}
    return {"criteria": criteria, "promote": all(criteria.values()), "details": details}


def evaluate(pyfunc_model, df, n_latency=200):
    X = df[FEATURES]
    y = df["eta_minutes"].to_numpy()
    pred = pyfunc_model.predict(X)["eta_minutes"].to_numpy()
    err = np.abs(pred - y)
    rush = df["hour"].isin(RUSH_HOURS).to_numpy()
    rain = (df["rain"] == 1).to_numpy()
    lat = []
    for i in range(n_latency):                                  # single-row predictions
        row = X.iloc[[i]]
        t0 = time.perf_counter()
        pyfunc_model.predict(row)
        lat.append((time.perf_counter() - t0) * 1000)
    return {"mae": float(err.mean()), "mae_rush": float(err[rush].mean()),
            "mae_rain": float(err[rain].mean()), "p95_ms": float(np.percentile(lat, 95))}


def promotion_gate(**thresholds):
    mlflow.set_tracking_uri(os.environ.get("MLFLOW_TRACKING_URI", "sqlite:///mlflow.db"))
    fresh = eta_data.make_data(2000, seed=99)
    prod_m = mlflow.pyfunc.load_model(f"models:/{MODEL_NAME}@prod")
    cand_m = mlflow.pyfunc.load_model(f"models:/{MODEL_NAME}@candidate")
    prod, cand = evaluate(prod_m, fresh), evaluate(cand_m, fresh)
    result = decide(prod, cand, **thresholds)
    result["prod_metrics"], result["cand_metrics"], result["thresholds"] = prod, cand, thresholds
    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["register", "gate"])
    ap.add_argument("--min-rel-gain", type=float)
    ap.add_argument("--max-p95-ms", type=float)
    ap.add_argument("--max-slice-regression", type=float)
    a = ap.parse_args()
    if a.cmd == "register":
        register()
    else:
        th = {k: v for k, v in dict(min_rel_gain=a.min_rel_gain, max_p95_ms=a.max_p95_ms,
                                    max_slice_regression=a.max_slice_regression).items()
              if v is not None}
        promotion_gate(**th)
