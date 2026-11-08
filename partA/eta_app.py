"""eta_app.py -- Part A: SwiftParcel ETA REST service (FastAPI).

Run:  uvicorn eta_app:app --port 8000            (from the repo root, after `source env.sh`)
Env:  ETA_MODEL = path to a model bundle (default data/eta_v1.joblib)
"""
import logging
import os
import threading
import time
import uuid
from typing import List

import joblib
import pandas as pd
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field, model_validator

log = logging.getLogger("eta_app")
PREDICT_PATH = "/v1/models/eta:predict"


def _resolve(path: str) -> str:
    """Find the model file whether we run from the repo root or from partA/."""
    if os.path.exists(path):
        return path
    here = os.path.dirname(os.path.abspath(__file__))
    for base in (here, os.path.join(here, "..")):
        alt = os.path.join(base, path)
        if os.path.exists(alt):
            return alt
    return path


# ---- model is loaded ONCE, at import time (not per request) -------------------------
MODEL_PATH = _resolve(os.environ.get("ETA_MODEL", "data/eta_v1.joblib"))
bundle = joblib.load(MODEL_PATH)
model = bundle["model"]                    # the sklearn estimator (tests may monkeypatch it)
FEATURE_COLUMNS = bundle["feature_columns"]
VERSION = bundle["version"]


# ---- request schema -----------------------------------------------------------------
class Instance(BaseModel):
    distance_km: float = Field(ge=0.1, le=500)
    n_stops: int = Field(ge=1, le=60)
    hour: int = Field(ge=0, le=23)
    weekday: int = Field(ge=0, le=6)
    traffic_index: float = Field(ge=0, le=1)
    rain: int = Field(ge=0, le=1)
    weight_kg: float = Field(gt=0, le=1000)

    @model_validator(mode="after")
    def _feasible(self):
        if self.distance_km > 150 and self.n_stops > 20:
            raise ValueError("infeasible delivery: distance_km > 150 with n_stops > 20")
        return self


class PredictRequest(BaseModel):
    instances: List[Instance] = Field(min_length=1, max_length=500)


# ---- tiny Prometheus metrics (no extra library) --------------------------------------
BUCKETS = [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]


class Metrics:
    def __init__(self):
        self.lock = threading.Lock()
        self.counts = {}                              # (path, status) -> int
        self.bucket_counts = [0] * len(BUCKETS)       # non-cumulative per bucket
        self.total = 0
        self.sum = 0.0

    def observe(self, path, status, seconds):
        with self.lock:
            key = (path, str(status))
            self.counts[key] = self.counts.get(key, 0) + 1
            self.total += 1
            self.sum += seconds
            for i, b in enumerate(BUCKETS):
                if seconds <= b:
                    self.bucket_counts[i] += 1
                    break

    def render(self):
        with self.lock:
            lines = ["# HELP eta_requests_total Total HTTP requests.",
                     "# TYPE eta_requests_total counter"]
            for (path, status), n in sorted(self.counts.items()):
                lines.append(f'eta_requests_total{{path="{path}",status="{status}"}} {n}')
            lines += ["# HELP eta_request_latency_seconds Request latency in seconds.",
                      "# TYPE eta_request_latency_seconds histogram"]
            cum = 0
            for b, n in zip(BUCKETS, self.bucket_counts):
                cum += n
                lines.append(f'eta_request_latency_seconds_bucket{{le="{b}"}} {cum}')
            lines.append(f'eta_request_latency_seconds_bucket{{le="+Inf"}} {self.total}')
            lines.append(f"eta_request_latency_seconds_sum {self.sum}")
            lines.append(f"eta_request_latency_seconds_count {self.total}")
            return "\n".join(lines) + "\n"


METRICS = Metrics()
app = FastAPI(title="SwiftParcel ETA service")


# ---- middleware: request id + metrics ------------------------------------------------
@app.middleware("http")
async def request_id_and_metrics(request: Request, call_next):
    rid = request.headers.get("x-request-id") or str(uuid.uuid4())
    request.state.request_id = rid
    t0 = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:                                  # last-resort safety net
        log.exception("unhandled error request_id=%s", rid)
        response = JSONResponse(status_code=500,
                                content={"error": "internal_error", "request_id": rid})
    response.headers["X-Request-ID"] = rid
    if request.url.path != "/metrics":                 # /metrics itself is not counted
        METRICS.observe(request.url.path, response.status_code, time.perf_counter() - t0)
    return response


@app.exception_handler(RequestValidationError)
async def on_validation_error(request: Request, exc: RequestValidationError):
    details = []
    for e in exc.errors():
        loc = [str(x) for x in e.get("loc", ()) if x != "body"]
        details.append({"field": ".".join(loc) or "body", "msg": str(e.get("msg", ""))})
    return JSONResponse(status_code=422,
                        content={"error": "validation_failed", "details": details,
                                 "request_id": request.state.request_id})


# ---- endpoints -----------------------------------------------------------------------
@app.post(PREDICT_PATH)
def predict(req: PredictRequest, request: Request):
    rid = request.state.request_id
    try:
        df = pd.DataFrame([i.model_dump() for i in req.instances])[FEATURE_COLUMNS]
        preds = model.predict(df)
        out = [round(float(p), 1) for p in preds]
    except Exception:
        log.exception("inference failed request_id=%s", rid)   # details only in the log
        return JSONResponse(status_code=500,
                            content={"error": "internal_error", "request_id": rid})
    return {"predictions": out, "model_version": VERSION, "request_id": rid}


@app.get("/healthz")
def healthz():
    return {"status": "ok", "model_version": VERSION}


@app.get("/metrics")
def metrics():
    return PlainTextResponse(METRICS.render(), media_type="text/plain; version=0.0.4")
