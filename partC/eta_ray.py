"""eta_ray.py -- Part C: Ray Serve app with a sticky (per-customer) canary.

    build(weight=0.1, extra_work_ms=0.0)  -> Serve application
    POST /v1/models/eta:predict   {"customer_id": "...", "instances": [...]}
    GET  /config                  -> {"weight": ...}   (handy to know which router version is live)
"""
import hashlib
import sys
import time
from pathlib import Path
from typing import List, Optional

HERE = Path(__file__).resolve().parent
for _p in (HERE, HERE.parent):
    sys.path.insert(0, str(_p))

import joblib
import pandas as pd
from fastapi import FastAPI, Header
from pydantic import BaseModel, Field, model_validator
from ray import serve

SALT = "eta-canary-v1"            # fixed forever: changing it would re-shuffle every customer


def find(path):
    for base in (Path.cwd(), HERE, HERE.parent):
        if (base / path).exists():
            return str((base / path).resolve())
    raise FileNotFoundError(path)


def bucket(customer_id: str) -> float:
    """Stable value in [0, 1) for a customer. sha256 is identical across processes/restarts
    (unlike built-in hash(), which is salted per process)."""
    digest = hashlib.sha256(f"{SALT}:{customer_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2 ** 64


def choose_arm(customer_id: str, weight: float, force_candidate: bool = False) -> str:
    if force_candidate:
        return "v2"
    return "v2" if bucket(customer_id) < weight else "v1"


# ---- request schema (same rules as Part A) ---------------------------------------------
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


class RouteBody(BaseModel):
    customer_id: str = Field(min_length=1)
    instances: List[Instance] = Field(min_length=1, max_length=500)


# ---- model deployment ------------------------------------------------------------------
@serve.deployment
class EtaModel:
    def __init__(self, path: str, version: str, extra_work_ms: float = 0.0):
        b = joblib.load(path)                      # loaded ONCE per replica
        self.model, self.cols, self.version = b["model"], b["feature_columns"], version
        self.extra_work_ms = extra_work_ms

    def predict(self, rows: list) -> dict:
        if self.extra_work_ms:
            time.sleep(self.extra_work_ms / 1000.0)    # simulate heavier model (for autoscaling demo)
        df = pd.DataFrame(rows)[self.cols]
        preds = [round(float(p), 1) for p in self.model.predict(df)]
        return {"predictions": preds, "model_version": self.version}


# ---- router (ingress) ------------------------------------------------------------------
api = FastAPI(title="ETA canary router")


@serve.deployment(ray_actor_options={"num_cpus": 0.1})
@serve.ingress(api)
class Router:
    def __init__(self, prod_handle, cand_handle, weight: float):
        self.prod, self.cand, self.weight = prod_handle, cand_handle, float(weight)

    @api.get("/config")
    def config(self):
        return {"weight": self.weight}

    @api.post("/v1/models/eta:predict")
    async def predict(self, body: RouteBody, x_beta: Optional[str] = Header(default=None)):
        arm = choose_arm(body.customer_id, self.weight, force_candidate=(x_beta == "1"))
        handle = self.cand if arm == "v2" else self.prod
        rows = [i.model_dump() for i in body.instances]
        out = await handle.predict.remote(rows)
        return {**out, "arm": arm}


def build(weight: float = 0.1, extra_work_ms: float = 0.0):
    """Fractional CPUs: prod 2 x 0.25, candidate up to 3 x 0.25, router 0.1
    -> at most 1.35 CPUs, so everything fits on a 2-CPU Ray."""
    prod = EtaModel.options(
        name="EtaProd", num_replicas=2, ray_actor_options={"num_cpus": 0.25},
    ).bind(find("data/eta_v1.joblib"), "v1", extra_work_ms)
    cand = EtaModel.options(
        name="EtaCandidate", ray_actor_options={"num_cpus": 0.25},
        autoscaling_config={"min_replicas": 1, "max_replicas": 3, "initial_replicas": 1,
                            "target_ongoing_requests": 2,
                            "upscale_delay_s": 5, "downscale_delay_s": 60},
    ).bind(find("data/eta_v2.joblib"), "v2", extra_work_ms)
    return Router.bind(prod, cand, weight)
