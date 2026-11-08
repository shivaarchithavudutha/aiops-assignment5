"""shadow.py -- B3: send the SAME 200 single-row requests to prod (5101) and candidate (5102).

    python partB/shadow.py | tee partB/shadow_output.txt
"""
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from eta_data import FEATURES

PROD_URL = "http://127.0.0.1:5101/invocations"
CAND_URL = "http://127.0.0.1:5102/invocations"
N = 200


def find(path):
    for base in (Path.cwd(), HERE, HERE.parent):
        if (base / path).exists():
            return base / path
    raise FileNotFoundError(path)


def eta_from(resp_json):
    """MLflow returns {"predictions": [{"eta_minutes":.., "model_version":..}]} (or column dict)."""
    p = resp_json["predictions"]
    if isinstance(p, dict):
        return float(p["eta_minutes"][0])
    first = p[0]
    return float(first["eta_minutes"] if isinstance(first, dict) else first)


def call(sess, url, row):
    t0 = time.perf_counter()
    r = sess.post(url, json={"dataframe_records": [row]}, timeout=30)
    ms = (time.perf_counter() - t0) * 1000
    r.raise_for_status()
    return eta_from(r.json()), ms


rows = pd.read_csv(find("data/eta_fresh.csv"))[FEATURES].head(N).to_dict(orient="records")
sp, sc = requests.Session(), requests.Session()
for row in rows[:5]:                                   # warm-up (not counted)
    call(sp, PROD_URL, row)
    call(sc, CAND_URL, row)

pp, pl, cp, cl = [], [], [], []
for row in rows:                                       # identical request goes to both
    a, ta = call(sp, PROD_URL, row)
    b, tb = call(sc, CAND_URL, row)
    pp.append(a); pl.append(ta); cp.append(b); cl.append(tb)

diff = np.abs(np.array(pp) - np.array(cp))
print(f"requests sent to each server        : {N}")
print(f"(i)   mean |prod - candidate|       : {diff.mean():.2f} minutes")
print(f"(ii)  share differing by > 10 min   : {100 * (diff > 10).mean():.1f} %")
print(f"(iii) p95 latency prod (v1, :5101)  : {np.percentile(pl, 95):.1f} ms")
print(f"(iii) p95 latency cand (v2, :5102)  : {np.percentile(cl, 95):.1f} ms")
print(f"      mean prediction prod / cand   : {np.mean(pp):.1f} / {np.mean(cp):.1f} minutes")
