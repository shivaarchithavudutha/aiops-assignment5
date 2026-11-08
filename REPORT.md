# SwiftParcel ETA Serving System - Report (AIOps Module 4, Assignment 5)

## 0. Machine, versions, honesty statement

- **Machine:** VirtualBox VM, Ubuntu 26.04.1 LTS; Intel Core i7-14650HX, 8 cores assigned (`nproc` = 8); 9.6 GiB RAM (7.4 GiB available); Python 3.14.4.
- **Versions:** fastapi 0.162.2, uvicorn 0.54.0, pydantic 2.14.3, scikit-learn 1.9.1, mlflow 3.18.0, ray 2.60.0, pandas 3.1.1, numpy 2.5.4, joblib 1.6.0.
- **Lecture material reused:** none.
- **AI assistance:** I used Claude (Anthropic) for help understanding the concepts, drafting and debugging the code, and structuring this report. I ran every command, test and measurement myself and reviewed the results; every number here comes from my own runs, with raw outputs saved in the repo (`partA/loadtest_results*.json`, `partB/gate_output.txt`, `partB/shadow_output.txt`, `partC/verify_output.txt`).

**NOT tested / limitations**
1. Autoscaling scale-up: configured (candidate min 1, max 3) and deployed, but my verification traffic never triggered a scale-up. Not exercised.
2. With `--workers 2` each worker keeps its own metric counters, so `/metrics` shows only the worker that answered the scrape (no multiprocess aggregation).
3. Load generator and server ran on the same VM, and the load generator is a single Python process, so numbers are noisy; I did not measure how much it limits the 2-worker results at high concurrency.
4. My first 1-worker run showed throughput collapsing (385 to about 2-35 RPS at concurrency 2-16), which a normal saturation curve would not do. I suspect thread oversubscription (that terminal may have lacked `OMP_NUM_THREADS=1` from `env.sh`) but did not verify it. I re-ran the baseline with `env.sh` active and report the re-run.
5. The 1-worker baseline (re-run), 2-worker run and batch-size runs were done in separate sessions, so ratios between them carry session-to-session noise.
6. `mlflow models serve` used `--env-manager local`, not a freshly built environment.
7. The shadow run was sequential (prod then candidate per row), and compares the two models with each other only (no ground-truth delivery times).
8. `smoke_eta.py` was run against my Part A server (v1) only, not the MLflow or Ray servers.
9. Everything was run only on this Ubuntu 26.04.1 / Python 3.14.4 VM; I had no access to the instructors' automated checks.

---

## Part A - REST service (FastAPI)

`eta_app.py` loads the model once at start-up (`ETA_MODEL`, default `data/eta_v1.joblib`), validates with pydantic (ranges, batch 1-500, infeasible rule: distance > 150 and n_stops > 20 gives 422), echoes or generates `X-Request-ID`, exposes `/healthz` and a hand-written Prometheus `/metrics`, and returns a generic 500 if inference raises. `python -m pytest partA`: **19 passed**.

### A3.1 Load test, v1, one worker, 4 s per level (`partA/loadtest_results.json`; 0 errors, v1 served in all rows)

| Concurrency | Requests | RPS | p50 (ms) | p95 (ms) | p99 (ms) |
|:---:|:---:|:---:|:---:|:---:|:---:|
| 1 | 1570 | 392.5 | 2.5 | 2.9 | 3.8 |
| 2 | 1699 | 424.8 | 4.6 | 5.8 | 7.9 |
| 4 | 1339 | 334.8 | 11.2 | 19.3 | 22.9 |
| 8 | 1180 | 295.0 | 26.1 | 46.0 | 57.5 |
| 16 | 1281 | 320.2 | 47.8 | 84.5 | 102.0 |

### A3.2 Saturation point and capacity under SLO (p95 <= 80 ms)

**Saturation:** throughput peaks at concurrency 2 (424.8 RPS) and then settles around 295-335 RPS while latency keeps rising, so the single worker saturates at about 2 clients.

**Little's Law (W = L / lambda):** predicted W vs measured p50 is 2.5 vs 2.5 ms (c=1), 4.7 vs 4.6 (c=2), 11.9 vs 11.2 (c=4), 27.1 vs 26.1 (c=8), 50.0 vs 47.8 (c=16). Once throughput is capped near X_max, extra clients only add waiting time (W = L / X_max), so latency grows roughly in proportion to concurrency. One uvicorn process runs Python on about one core (GIL) and the load generator shares the VM, which is consistent with the plateau; I did not profile it further.

**Capacity under the SLO:** p95 passes at c=1, 2, 4, 8 (2.9, 5.8, 19.3, 46.0 ms) and fails at c=16 (84.5 ms); interpolation puts the SLO edge at about 15 clients. The highest throughput among passing rows is 424.8 RPS (c=2), so single-worker capacity under the SLO is about **425 RPS**.

### A3.3 Cost per row, batch 1 vs 100 (concurrency 1, 4 s)

| Batch | Requests | RPS | Time per request | ms per row = 1000 / (RPS x batch) |
|:---:|:---:|:---:|:---:|:---:|
| 1 | 1597 | 399.2 | 2.505 ms | 2.51 |
| 100 | 1293 | 323.2 | 3.094 ms | 0.031 |

Every request pays a fixed cost independent of its size (HTTP handling, JSON parsing, middleware, thread hand-off, call into scikit-learn). A 100-row request pays it once, so cost per row drops about 81x. The extra 99 rows added only about 0.6 ms (roughly 6 microseconds per row), so the truly per-row work (validation, DataFrame construction, vectorised Ridge prediction) is small.

### A3.4 Change to roughly double capacity: `--workers 2` (`partA/loadtest_results_workers2.json`)

| Concurrency | Requests | RPS | p50 (ms) | p95 (ms) | p99 (ms) |
|:---:|:---:|:---:|:---:|:---:|:---:|
| 1 | 1481 | 370.2 | 2.6 | 3.4 | 4.6 |
| 2 | 2734 | 683.5 | 2.8 | 3.5 | 4.2 |
| 4 | 3369 | 842.2 | 4.6 | 6.3 | 8.5 |
| 8 | 2582 | 645.5 | 11.4 | 22.0 | 27.8 |
| 16 | 1996 | 499.0 | 30.0 | 58.0 | 76.2 |

Hypothesis: the GIL limits one process to about one core, so two worker processes should roughly double throughput. **Result:** peak throughput rose from 424.8 RPS (1 worker, c=2) to 842.2 RPS (2 workers, c=4), **1.98x**. At equal concurrency the gain was 1.6x (c=2), 2.5x (c=4), 2.2x (c=8) and 1.6x (c=16), and none at c=1 (370 vs 392 RPS) because one client keeps only one worker busy. With two workers p95 stays within the SLO at every level (58.0 ms at c=16 vs 84.5 ms with one worker). Throughput falls after c=4 (842, 645, 499); the single-process load generator is probably the bottleneck there, but I did not measure it.

### A3.5 Why p95/p99 and not the mean
Latency is right-skewed, so a few very slow requests barely move the mean but are what unlucky users experience. SLOs and alerts use tail percentiles because a fine average can hide a bad experience for 1 request in 20 or 1 in 100.

---

## Part B - MLflow registry, gate and shadow

### B1. Registration
`eta_mlflow.py` defines a custom pyfunc (`EtaPyfunc`) returning a DataFrame with `eta_minutes` (one decimal) and `model_version`. Both models are logged with a signature, an input example and pinned `pip_requirements`, and registered as `eta-model` in a SQLite store (`mlflow.db`).

| Version | Alias | Bundle |
|:---:|:---:|:---:|
| 1 | prod | v1 (Ridge) |
| 2 | candidate | v2 (Gradient Boosting) |

### B2. Promotion gate
`decide(prod, cand, min_rel_gain=0.03, max_p95_ms=10.0, max_slice_regression=0.05)` is pure and promotes only if: overall MAE improves by at least `min_rel_gain` (relative to prod); candidate single-row p95 <= `max_p95_ms`; and neither the rush-hour slice (hours 7-9, 16-19) nor the rain == 1 slice regresses by more than `max_slice_regression`. **Assumption:** slice regression is relative to prod's slice MAE (candidate <= prod x 1.05). `promotion_gate()` loads both aliases, evaluates on `make_data(2000, seed=99)` and measures p95 over 200 single-row predictions. `python -m pytest partB`: **9 passed** (boundaries: gain 3.1% vs 2.9%, p95 10.0 vs 10.01, rush slice 20.99 vs 21.01, rain-only regression, purity).

- **Default thresholds** (`partB/gate_output.txt`): `promote: true`. MAE gain 48.94% (11.96 to 6.10 min, need >= 3%); candidate p95 1.36 ms (need <= 10); rush slice 19.68 to 8.58 min and rain slice 11.62 to 6.29 min, no regression.
- **`--min-rel-gain 0.60`:** `promote: false`. The gain 0.489 is below 0.60, so `overall_mae_gain` fails while the other criteria pass (p95 1.50 ms in this run; latency varies slightly per run).

### B3. Shadow comparison
200 identical single-row requests from `data/eta_fresh.csv` were sent to prod (port 5101) and candidate (port 5102), sequentially (prod, then candidate, per row) after 5 warm-up calls (`partB/shadow_output.txt`):

- (i) mean absolute difference: **9.48 min**
- (ii) requests differing by more than 10 min: **35.0%**
- (iii) p95 latency: prod **5.1 ms**, candidate **5.3 ms** (mean predictions 118.9 vs 119.0 min). These include the HTTP round trip, unlike the gate's in-process 1.36 ms.

**Decision.** The models disagree noticeably, but this is expected: the gate showed v2 is much more accurate (MAE 11.96 to 6.10, rush hour 19.68 to 8.58), the mean predictions are almost identical, and v2 adds no meaningful latency (5.3 vs 5.1 ms p95). I would promote v2, through a sticky canary (Part C) rather than an all-at-once switch, so real delivery outcomes can be checked and customers see few abrupt ETA changes.

**Why moving an alias does not change a running server:** an alias is only a pointer (name to version) in the registry database. `mlflow models serve -m models:/eta-model@prod` resolves it once at start-up, loads that version into memory and never re-reads the registry.

**Two ways to pick up a new version without dropping requests:** (1) blue/green or rolling replacement: start new servers on the new version, wait for health checks, shift traffic, then drain and stop the old ones; (2) in-process hot reload: poll the registry (or use a webhook), load the new model in the background and atomically swap the reference while in-flight requests finish on the old one.

---

## Part C - Ray Serve sticky canary

`eta_ray.py`: `EtaModel` loads a bundle once per replica; prod has 2 replicas; the candidate has `autoscaling_config` (min 1, max 3, `target_ongoing_requests=2`, `upscale_delay_s=5`, `downscale_delay_s=60`). A FastAPI-ingress router takes `{"customer_id", "instances"}`, validates with the Part A rules and returns the output plus `"arm"`. Routing: `bucket(customer_id)` = first 8 bytes of `sha256("salt:customer_id")` mapped to [0, 1); the customer goes to the candidate if bucket < weight (so about `weight` of customers, not requests). `X-Beta: 1` forces the candidate. Fractional CPUs: prod 2 x 0.25, candidate up to 3 x 0.25, router 0.1, worst case 1.35 CPUs on 2 CPUs.

### C2. Verification (`partC/verify_output.txt`; Ray with 2 CPUs, 1000 customer ids, weight 0.2)

| Check | Measured | Result |
|:---:|:---|:---:|
| (a) share within +-4 points of 0.2 | 0.190 (190 of 1000) | pass |
| (b) 200 customers called twice | 0 changed arm | pass |
| (c) same 200 after redeploying the identical app | 0 of 200 changed arm | pass |
| (d) weight 0.2 to 0.5 | share 0.507; moved back to prod = 0; newly on candidate = 317 | pass |
| (e) `X-Beta: 1` on 50 prod-routed customers | all `v2` with header, all `v1` without | pass |

### C3. Short answers
1. **Why not `hash()`?** Python randomises string hashing per process (`PYTHONHASHSEED`). Replicas, or one replica after a restart or redeploy, would give different values for the same id, so customers would switch arms. `sha256` is deterministic and uniform across processes and restarts.
2. **Why stickiness matters:** (i) a customer seeing ETAs from two different models on repeat visits gets inconsistent estimates and loses trust; (ii) in the experiment each customer must be in exactly one arm, otherwise both models contaminate every customer's outcomes and per-customer metrics cannot be attributed.
3. **Autoscaling parameters:** `min_replicas=1` because the candidate only gets about `weight` of customers; `max_replicas=3` caps it at 0.75 CPU so the app stays at or below 1.35 CPUs; `target_ongoing_requests=2` because predictions take milliseconds, so scale only when a queue builds; `upscale_delay_s=5` reacts to real surges but ignores blips; `downscale_delay_s=60` keeps replicas warm (model loading is costly) and avoids flapping. These are reasoned, not tuned, since autoscaling was not exercised.

---

## Part D - Release gate

### D1. Smoke test (`partD/smoke_eta.py --url ... --expect-version ... [--max-p95-ms 80]`)
Eight checks, exit 0 on success and 1 on failure: valid request returns 200 with a positive ETA; served version equals `--expect-version`; a batch of 3 equals the same rows sent singly; **model sanity**: longer distance, heavier traffic and more stops never give a shorter ETA (0.5-min tolerance); invalid input (distance -5) returns 422; p95 of 30 sequential requests <= limit.

- `--expect-version v1` on my Part A server: `SMOKE TEST PASSED (8/8 checks)`, exit code 0 (ETA 102.0 min; distance [5, 20, 50, 100] km gives [37.5, 76.2, 153.6, 282.6] min; traffic [0.1, 0.5, 0.9] gives [84.9, 102.0, 119.1]; stops [2, 8, 15] gives [89.0, 115.0, 145.3]; 422 on invalid input; p95 5.1 ms vs limit 80).
- `--expect-version v2` against the same v1 server: `[FAIL] model version matches --expect-version: served=v1 expected=v2`, `SMOKE TEST FAILED (7/8 checks)`, exit code 1.

### D2. Abort criteria for a 10% canary of v2

| # | Metric | Abort threshold | Window |
|:---:|:---|:---|:---|
| 1 | HTTP 5xx rate of canary traffic | above 1%, or above 2x prod's rate | rolling 5 min, at least 200 canary requests |
| 2 | Canary p95 latency | above 80 ms (SLO) or above 1.5x prod's p95 | 2 consecutive 5-min windows |
| 3 | Prediction divergence from prod on mirrored requests | mean absolute difference above 12 min (shadow baseline 9.5), or over 50% of requests differing by more than 10 min (baseline 35%) | rolling 15 min, at least 500 mirrored requests |
| 4 | Live accuracy once real delivery times arrive: canary MAE vs prod, overall and on rush-hour / rain slices | canary worse than prod by more than 5% overall or on any slice | rolling 1 hour, at least 500 completed deliveries per arm |
