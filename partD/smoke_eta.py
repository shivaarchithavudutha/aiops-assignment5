"""smoke_eta.py -- Part D: release gate / smoke test for any ETA server.

    python partD/smoke_eta.py --url http://127.0.0.1:8000/v1/models/eta:predict --expect-version v1 [--max-p95-ms 80]

Works for Part A and Part C (REST format) and for `mlflow models serve` (URL ending in /invocations).
Exit code 0 = all checks passed, 1 = at least one failed.
"""
import argparse
import sys
import time

import numpy as np
import requests

BASE = dict(distance_km=30, n_stops=5, hour=12, weekday=2, traffic_index=0.5, rain=0, weight_kg=10)
TOL = 0.5          # minutes of tolerance for tree-model noise in the monotonic checks


def row(**kw):
    r = dict(BASE)
    r.update(kw)
    return r


class Client:
    def __init__(self, url):
        self.url, self.s = url, requests.Session()
        self.mlflow = url.rstrip("/").endswith("/invocations")

    def post(self, rows):
        body = {"dataframe_records": rows} if self.mlflow else {"instances": rows, "customer_id": "smoke-test"}
        return self.s.post(self.url, json=body, timeout=15)

    @staticmethod
    def parse(resp):
        j = resp.json()
        p = j["predictions"]
        if isinstance(p, dict):                                     # column-oriented
            return [float(x) for x in p["eta_minutes"]], p.get("model_version", [None])[0]
        if p and isinstance(p[0], dict):                            # MLflow records
            return [float(x["eta_minutes"]) for x in p], p[0].get("model_version")
        return [float(x) for x in p], j.get("model_version")

    def etas(self, rows):
        r = self.post(rows)
        assert r.status_code == 200, f"HTTP {r.status_code}: {r.text[:120]}"
        return self.parse(r)[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--expect-version", required=True)
    ap.add_argument("--max-p95-ms", type=float, default=80.0)
    a = ap.parse_args()
    c = Client(a.url)
    results = []

    def check(name, fn):
        try:
            ok, msg = fn()
        except Exception as e:                                      # any crash = failed check
            ok, msg = False, f"{type(e).__name__}: {e}"
        results.append(ok)
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {msg}")

    def valid_request():
        r = c.post([row()])
        vals, _ = c.parse(r)
        return r.status_code == 200 and len(vals) == 1 and vals[0] > 0, f"HTTP {r.status_code}, eta={vals}"

    def version():
        _, v = c.parse(c.post([row()]))
        return v == a.expect_version, f"served={v} expected={a.expect_version}"

    def batch_consistent():
        rows = [row(distance_km=5), row(distance_km=40), row(distance_km=90)]
        together = c.etas(rows)
        singles = [c.etas([x])[0] for x in rows]
        return together == singles and len(together) == 3, f"batch={together} singles={singles}"

    def distance_monotonic():
        ds = [5, 20, 50, 100]
        e = [c.etas([row(distance_km=d)])[0] for d in ds]
        return all(e[i + 1] >= e[i] - TOL for i in range(len(e) - 1)), f"distance {ds} -> {e}"

    def traffic_monotonic():
        ts = [0.1, 0.5, 0.9]
        e = [c.etas([row(traffic_index=t)])[0] for t in ts]
        return all(e[i + 1] >= e[i] - TOL for i in range(len(e) - 1)), f"traffic {ts} -> {e}"

    def stops_monotonic():
        ns = [2, 8, 15]
        e = [c.etas([row(n_stops=n)])[0] for n in ns]
        return all(e[i + 1] >= e[i] - TOL for i in range(len(e) - 1)), f"n_stops {ns} -> {e}"

    def invalid_is_422():
        r = c.post([row(distance_km=-5)])
        return r.status_code == 422, f"HTTP {r.status_code} (expected 422)"

    def latency():
        lat = []
        for _ in range(30):
            t0 = time.perf_counter()
            c.etas([row()])
            lat.append((time.perf_counter() - t0) * 1000)
        p95 = float(np.percentile(lat, 95))
        return p95 <= a.max_p95_ms, f"p95={p95:.1f} ms (limit {a.max_p95_ms} ms)"

    check("valid request returns 200 + one ETA", valid_request)
    check("model version matches --expect-version", version)
    check("batch result == the same rows sent one by one", batch_consistent)
    check("SANITY longer distance never gives shorter ETA", distance_monotonic)
    check("SANITY heavier traffic never gives shorter ETA", traffic_monotonic)
    check("SANITY more stops never gives shorter ETA", stops_monotonic)
    check("invalid input returns 422", invalid_is_422)
    check("p95 latency within limit", latency)

    ok = all(results)
    print(f"\nSMOKE TEST {'PASSED' if ok else 'FAILED'} ({sum(results)}/{len(results)} checks)")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
