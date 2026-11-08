"""verify_ray.py -- C2: demonstrate the sticky canary on a 2-CPU Ray.

    python partC/verify_ray.py | tee partC/verify_output.txt
"""
import sys
import time
from pathlib import Path

import ray
import requests
from ray import serve

HERE = Path(__file__).resolve().parent
for p in (HERE, HERE.parent):
    sys.path.insert(0, str(p))
from eta_ray import build

BASE = "http://127.0.0.1:8000"
URL = BASE + "/v1/models/eta:predict"
ROW = dict(distance_km=30, n_stops=5, hour=12, weekday=2, traffic_index=0.5, rain=0, weight_kg=10)
IDS = [f"cust-{i:04d}" for i in range(1000)]
sess = requests.Session()


def arm_of(cid, headers=None):
    r = sess.post(URL, json={"customer_id": cid, "instances": [ROW]}, headers=headers, timeout=30)
    r.raise_for_status()
    return r.json()["arm"]


def arms_for(ids):
    return {c: arm_of(c) for c in ids}


def deploy(weight):
    serve.run(build(weight=weight), name="eta", route_prefix="/")
    deadline = time.time() + 90                     # wait until the NEW router is live
    while time.time() < deadline:
        try:
            r = sess.get(BASE + "/config", timeout=5)
            if r.status_code == 200 and abs(r.json()["weight"] - weight) < 1e-9:
                return
        except requests.RequestException:
            pass
        time.sleep(1)
    raise RuntimeError(f"app with weight={weight} did not become ready")


def share(a):
    return sum(v == "v2" for v in a.values()) / len(a)


ray.init(num_cpus=2, include_dashboard=False, ignore_reinit_error=True)
serve.start(http_options={"host": "127.0.0.1", "port": 8000})
print("Ray resources:", ray.cluster_resources())

# (a) share routed to the candidate -----------------------------------------------------------
deploy(0.2)
first = arms_for(IDS)
s = share(first)
print(f"(a) weight=0.2 over {len(IDS)} distinct customers: candidate share = {s:.3f} "
      f"({sum(v == 'v2' for v in first.values())} of {len(IDS)})")
assert abs(s - 0.2) <= 0.04, "share outside +-4 percentage points"

# (b) stickiness on a second call ----------------------------------------------------------------
sample = IDS[:200]
second = arms_for(sample)
changed = [c for c in sample if second[c] != first[c]]
print(f"(b) {len(sample)} customers called twice: {len(changed)} changed arm")
assert not changed

# (c) stickiness after redeploying the identical app ----------------------------------------------
deploy(0.2)
third = arms_for(sample)
changed = [c for c in sample if third[c] != first[c]]
print(f"(c) after redeploying identical app: {len(changed)} of {len(sample)} changed arm")
assert not changed

# (d) raising the weight never moves a candidate customer back to prod --------------------------------
deploy(0.5)
after = arms_for(IDS)
moved_back = [c for c in IDS if first[c] == "v2" and after[c] != "v2"]
moved_in = [c for c in IDS if first[c] == "v1" and after[c] == "v2"]
print(f"(d) weight 0.2 -> 0.5: new candidate share = {share(after):.3f}; "
      f"moved back to prod = {len(moved_back)}; newly moved to candidate = {len(moved_in)}")
assert not moved_back
assert abs(share(after) - 0.5) <= 0.04

# (e) header override ----------------------------------------------------------------------------------
prod_customers = [c for c in IDS if after[c] == "v1"][:50]
forced = [arm_of(c, headers={"X-Beta": "1"}) for c in prod_customers]
plain = [arm_of(c) for c in prod_customers]
print(f"(e) 50 prod-routed customers with X-Beta: 1 -> arms {set(forced)}; without header -> {set(plain)}")
assert all(a == "v2" for a in forced) and all(a == "v1" for a in plain)

print("\nALL CHECKS PASSED")
serve.shutdown()
ray.shutdown()
