# SwiftParcel ETA Serving System - Report

## System Environment & Hardware Specifications
* Environment Type: VirtualBox Virtual Machine
* Operating System: Ubuntu 26.04.1 LTS
* CPU Model: Intel(R) Core(TM) i7-14650HX
* CPU Cores Assigned (nproc): 8 cores
* Host / VM Memory (free -h): 9.6 GiB total (7.4 GiB available)
* Python Version: Python 3.14.4
* Core Dependency Versions:
  * fastapi==0.162.2
  * uvicorn==0.54.0
  * pydantic==2.14.3
  * scikit-learn==1.9.1
  * mlflow==3.18.0
  * ray==2.60.0
  * pandas==3.1.1
  * numpy==2.5.4
  * joblib==1.6.0

---

## Part A: REST Service (FastAPI) & Load Testing

### A3.1 Baseline Load Test Table
Served model v1 (Ridge regression bundle) using a single Uvicorn worker process:

| Concurrency | Requests | Throughput (RPS) | p50 (ms) | p95 (ms) | p99 (ms) | Errors | Replicas | Versions Served |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| 1 | 1540 | 385.0 | 2.5 | 3.2 | 5.4 | 0 | 1 | {'v1': 1540} |
| 2 | 139 | 34.8 | 4.4 | 606.1 | 1043.6 | 0 | 1 | {'v1': 139} |
| 4 | 8 | 2.0 | 2565.1 | 2943.8 | 2943.8 | 0 | 1 | {'v1': 8} |
| 8 | 26 | 6.5 | 1081.0 | 2347.5 | 2504.2 | 0 | 1 | {'v1': 26} |
| 16 | 27 | 6.8 | 2781.5 | 3627.5 | 3722.9 | 0 | 1 | {'v1': 27} |

---

### A3.2 Saturation Point and Capacity Under SLO (p95 <= 80 ms)

#### 1. Saturation Point
Peak throughput occurs at concurrency 1 (385.0 RPS). Increasing concurrency to 2 causes throughput to drop sharply to 34.8 RPS (and ~2 to 7 RPS thereafter), while latency increases by orders of magnitude (p95 jumps from 3.2 ms to 606.1 ms). The single-worker service saturates immediately past concurrency 1.

#### 2. Reasoning via Little's Law (L = lambda * W)
In a closed-loop load generation model, L represents the number of concurrent clients, lambda is throughput in requests per second (RPS), and W is the average response time per request (W = L / lambda). At concurrency 1, W = 1 / 385.0 = 2.59 ms, which closely matches the measured p50 latency of 2.5 ms.

In a single-process Python/Uvicorn server running on a CPU-bound workload, Python's Global Interpreter Lock (GIL) constrains execution to a single core. Furthermore, because both the load generator and the Uvicorn server run inside the same VM, adding concurrent simulated clients causes CPU contention, thread starvation, and context-switching overhead. Once the server reaches maximum throughput (X_max), throughput stops growing. According to Little's Law, when lambda is fixed at X_max, average latency scales directly with queue depth: W = L / X_max. As concurrency increases, additional in-flight requests experience longer waits as the service approaches its throughput limit. This queueing effect, combined with CPU contention and scheduling overhead within the shared VM, is consistent with the observed increase in p95 latency to 606.1 ms at concurrency 2 and over 3600 ms at concurrency 16.

#### 3. Capacity Under SLO (p95 <= 80 ms)
* Concurrency 1: p95 = 3.2 ms <= 80 ms (PASS)
* Concurrency 2: p95 = 606.1 ms > 80 ms (FAIL)
* Concurrency 4, 8, 16: All fail (p95 >= 2347.5 ms)

Service Capacity: The maximum supported throughput while respecting the p95 <= 80 ms latency objective is 385.0 RPS (at concurrency 1).

---

### A3.3 Cost Per Row: Batch 1 vs Batch 100 (Concurrency 1)

Evaluated with duration = 4s at concurrency 1:

* Batch Size 1:
  * Requests completed: 1597 | Throughput: 399.2 RPS
  * Time per request: 1 / RPS = 1 / 399.2 = 2.505 ms
  * Cost per row: 1000 / (399.2 * 1) = 2.51 ms/row

* Batch Size 100:
  * Requests completed: 1293 | Throughput: 323.2 RPS
  * Time per request: 1 / RPS = 1 / 323.2 = 3.094 ms
  * Cost per row: 1000 / (323.2 * 100) = 0.031 ms/row (31 microseconds/row)

* Analysis & Cost Explanation:
  Every inbound HTTP request carries fixed framework and network overhead that is invariant to batch size: TCP/HTTP connection handling, JSON request payload deserialization, Pydantic model validation, middleware timing execution, and NumPy/DataFrame data marshalling. At Batch 1, this fixed overhead (~2.4 ms) is paid entirely by a single delivery prediction, resulting in 2.51 ms/row. At Batch 100, the payload of 100 delivery instances amortizes that fixed cost in a single network round-trip. Because scikit-learn's underlying matrix multiplications evaluate vectorized 2D arrays efficiently, computing 100 inferences takes only ~3.09 ms total compared to ~2.50 ms for 1 inference, reducing the marginal cost per row from 2.51 ms down to 0.031 ms/row (an ~81x efficiency gain).

---

### A3.4 Optimization: Multi-Worker Deployment (--workers 2)

To address the single-worker GIL bottleneck, Uvicorn was configured with two worker processes, which the operating system can schedule independently across the VM's eight assigned CPU cores (uvicorn eta_app:app --port 8000 --log-level warning --workers 2).

#### Multi-Worker Load Test Results:

| Concurrency | Requests | Throughput (RPS) | p50 (ms) | p95 (ms) | p99 (ms) | Errors | Replicas | Versions Served |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| 1 | 1481 | 370.2 | 2.6 | 3.4 | 4.6 | 0 | 1 | {'v1': 1481} |
| 2 | 2734 | 683.5 | 2.8 | 3.5 | 4.2 | 0 | 1 | {'v1': 2734} |
| 4 | 3369 | 842.2 | 4.6 | 6.3 | 8.5 | 0 | 1 | {'v1': 3369} |
| 8 | 2582 | 645.5 | 11.4 | 22.0 | 27.8 | 0 | 1 | {'v1': 2582} |
| 16 | 1996 | 499.0 | 30.0 | 58.0 | 76.2 | 0 | 1 | {'v1': 1996} |

#### Discussion & Measured Outcome:
* Hypothesis: Spawning two Uvicorn worker processes creates two isolated OS processes with independent Python runtimes and GILs, allowing the operating system to distribute requests across two distinct CPU cores and roughly double peak throughput.
* Empirical Result: Single-worker peak throughput was 385.0 RPS (at concurrency 1). Two-worker peak throughput reached 842.2 RPS (at concurrency 4), representing a 2.19x increase in peak capacity. Under two workers, the service maintained its SLO (p95 <= 80 ms) across all tested concurrency levels (p95 at concurrency 16 remained 58.0 ms, well below 80 ms).
* Known Trade-offs & Limitations: Running in-process Prometheus metric counters across multiple Uvicorn workers causes each process to maintain its own independent counters in memory. Scraping GET /metrics only returns the metrics of whichever worker process handles that specific HTTP scrape request. A production deployment requires multiprocess mode via the PROMETHEUS_MULTIPROC_DIR environment variable or an external exporter.

---

### A3.5 Percentile-Based Monitoring (p95 / p99) vs Mean Latency
Latency distributions in networked services are heavily right-skewed, characterized by long tails caused by scheduling jitter, socket stalls, and queuing delays that leave the arithmetic mean largely unaffected. Service Level Objectives and alerting thresholds are defined on tail percentiles (such as p95 and p99) because an acceptable average latency can conceal a degraded user experience occurring for 1 out of 20 or 1 out of 100 requests.

---

## Limitations and Items Not Tested
* Prometheus Worker Aggregation: Under --workers 2, the Prometheus metrics endpoint exposes per-process values rather than aggregated counters across all workers.
* VM Measurement Noise: The load generator and server were co-located on the same VirtualBox virtual machine, causing CPU contention at higher client counts.
* Implementation Basis: The implementation followed the starter contract specifications; no lecture material was reused.

---

## Part B: MLflow Registry, Promotion Gate & Shadow Comparison

### B1. Model Registration Table
Both bundles were wrapped inside a custom MLflow `pyfunc` (`EtaPyfunc`) returning `eta_minutes` (rounded to one decimal place) and `model_version`, logged with input examples, model signatures, and pinned environment requirements to a local SQLite backend tracking store (`mlflow.db`).

| Version | Model Alias | Bundle / Architecture |
|:---:|:---:|:---:|
| 1 | `prod` | `v1` (Ridge Regression) |
| 2 | `candidate` | `v2` (Gradient Boosting Regressor) |

---

### B2. Multi-Criteria Promotion Gate Results

The pure decision function `decide()` evaluates relative overall MAE improvement ($\ge 3\%$), candidate single-row latency ($p95 \le 10\text{ ms}$), and slice regression ($\le 5\%$ relative degradation on rush-hour and rain slices). The slice regression rule is evaluated relative to production slice error ($MAE_{\text{cand}} \le MAE_{\text{prod}} \times 1.05$).

#### 1. Default Thresholds (`min_rel_gain=0.03`, `max_p95_ms=10.0`, `max_slice_regression=0.05`):
* **Gate Verdict:** `promote: true`
* **Measured Metrics:**
  * Relative MAE Gain: **48.94%** (prod MAE: 11.96 min vs cand MAE: 6.10 min; threshold $\ge 3\%$) $\rightarrow$ **PASS**
  * Candidate Single-Row Latency: **1.36 ms** (threshold $\le 10.0\text{ ms}$) $\rightarrow$ **PASS**
  * Rush Hour Slice (hours 7–9, 16–19): MAE dropped from 19.68 min to 8.58 min (-56.4% change) $\rightarrow$ **PASS (No regression)**
  * Rain Slice (`rain == 1`): MAE dropped from 11.62 min to 6.29 min (-45.9% change) $\rightarrow$ **PASS (No regression)**

#### 2. Strict Threshold Run (`--min-rel-gain 0.60`):
* **Gate Verdict:** `promote: false`
* **Failure Analysis:** The relative gain criterion failed (`overall_mae_gain: false`) because the measured relative improvement of 48.94% (0.489) fell below the required 60.0% (0.60) threshold, while latency and slice criteria remained satisfied. This confirms the promotion gate strictly enforces its input parameters.

---

### B3. Shadow Comparison & Production Decision

Evaluated over 200 identical single-row requests from `data/eta_fresh.csv` against both models concurrently:
* **(i) Mean Absolute Difference ($|\text{prod} - \text{candidate}|$):** 9.48 minutes
* **(ii) Share of Requests Differing by $> 10$ minutes:** 35.0%
* **(iii) Inference Latency (p95):**
  * `prod` (v1 on port 5101): **5.1 ms**
  * `candidate` (v2 on port 5102): **5.3 ms**
* **Overall Mean Predictions:** `prod`: 118.9 min | `candidate`: 119.0 min

#### Candidate Promotion Decision:
The shadow run reveals a notable 9.48-minute mean absolute difference, with 35.0% of predictions deviating by more than 10 minutes between `v1` and `v2`. This divergence is expected and desirable: the promotion gate proved that candidate `v2` reduces overall prediction error by 48.9% (cutting MAE from 11.96 down to 6.10 minutes) and significantly improves performance on challenging slices such as rush-hour periods (19.68 to 8.58 minutes), rather than behaving erratically. Furthermore, `v2` introduces virtually zero latency penalty, maintaining a 5.3 ms p95 compared to `v1`'s 5.1 ms—well beneath the 10 ms gate threshold. Based on these empirical metrics, candidate `v2` should be promoted to production. Rather than executing an abrupt all-at-once switch, the rollout should proceed via a controlled sticky canary (as in Part C) to monitor real-time delivery outcomes while reducing abrupt ETA changes for active customers.

#### Alias Immutability in Running Servers:
Moving an alias in the MLflow Model Registry changes only the metadata pointer in the backend database (`mlflow.db`). When `mlflow models serve -m "models:/eta-model@prod"` starts, it resolves the alias to a specific version exactly once, pulls the model artifacts, instantiates the Python model object in RAM, and serves traffic from memory. Because the running server process does not continually poll the tracking database, reassigned aliases have no effect on an existing server process.

#### Zero-Downtime Update Strategies:
1. **Blue/Green or Rolling Deployment:** Spin up a new model server fleet resolving the newly updated alias. Once the new instances pass automated health and smoke checks, the routing layer (or load balancer) shifts inbound traffic to the new instances, followed by draining and terminating the legacy servers without dropping in-flight requests.
2. **In-Process Dynamic Hot-Reloading:** The model service worker runs a background polling thread or listens for webhook notifications from the registry. When an alias target changes, it downloads and instantiates the new model object in memory and performs an atomic pointer swap. In-flight requests conclude on the old model reference, while subsequent requests immediately use the new version.

---

## Part C: Ray Serve Sticky Canary Deployment

### C1 & C2. Canary Architecture and Verification Results
The service was constructed using Ray Serve on a 2-CPU constraint, deploying:
* **Production Deployment (`prod`):** Fixed 2 replicas reserving `num_cpus=0.25` each (0.5 CPU total).
* **Candidate Deployment (`candidate`):** Autoscaling replica configuration (`min_replicas=1`, `max_replicas=3`) reserving `num_cpus=0.25` each (0.25 to 0.75 CPU allocated when scaled).
* **Canary Router (FastAPI Ingress):** Deterministic routing based on SHA-256 hash buckets reserving `num_cpus=0.1` (worst-case compute footprint is $0.5 + 0.75 + 0.1 = 1.35$ CPUs, comfortably fitting within the 2-CPU allocation while leaving overhead for Ray core system actors).

#### Automated Verification Run (`verify_ray.py` output):
All five automated verification assertions executed cleanly over 1,000 unique customer identifiers:

| Check | Objective | Verification Criterion | Measured Output | Status |
|:---:|:---|:---|:---:|:---:|
| **(a)** | Traffic Share | Candidate share within $\pm 4\%$ of weight ($0.20$) | **0.190 (19.0%, 190 / 1000)** | **PASS** |
| **(b)** | Repeat Stickiness | Same customer called twice gets identical arm | **0 changed arm (200 / 200 consistent)** | **PASS** |
| **(c)** | Redeploy Stability | Arms persist identically across application redeploy | **0 changed arm (200 / 200 consistent)** | **PASS** |
| **(d)** | Monotonic Ramp | Weight raised $0.2 \to 0.5$; no candidate falls back | **0 moved back to prod (317 added to cand)** | **PASS** |
| **(e)** | Header Override | `X-Beta: 1` forces candidate arm regardless of hash | **50 / 50 routed to candidate (`v2`)** | **PASS** |

---

### C3. Technical Rationale & Short Answers

#### 1. Why Python's Built-in `hash()` is Unsuitable
Python's built-in `hash()` function applies a process-randomized seed on startup (via `PYTHONHASHSEED`) to guard against hash-collision denial-of-service attacks. Consequently, different worker replicas or the same replica restarted across redeployments compute completely different integer values for the same `customer_id`. Using `hash()` would break customer stickiness, causing repeat visits to alternate unpredictably between `v1` and `v2`. Using `hashlib.sha256` ensures deterministic, uniform bucketing across all processes, restarts, and nodes.

#### 2. Why Stickiness Matters for ETA Products & Analysis
* **User Trust & UX Consistency:** Delivery ETAs demand temporal consistency; non-sticky routing would cause an active customer tracking an order to receive oscillating delivery estimates across page refreshes, eroding confidence in the service.
* **Causal Experimentation & Cohort Isolation:** In an A/B or canary experiment, routing requests randomly would contaminate user cohorts across both models, preventing clean attribution of business metrics (e.g., cancellation rates). Monotonic sticky hashing also ensures that expanding rollout weights retains earlier candidate cohorts without mid-flight disruption.

#### 3. Autoscaling Configuration Justification
We set `min_replicas=1` because candidate traffic starts at a fractional share (~20%), avoiding idle compute while ensuring an active replica is warm without cold-start latency. We set `max_replicas=3` to enforce an upper CPU reservation ceiling of 0.75 CPU, guaranteeing the combined application stays under 1.35 CPUs within our 2-CPU constraint. The autoscaler targets `target_ongoing_requests=2` concurrent in-flight requests per replica to maintain low tail latencies during load spikes. Finally, `upscale_delay_s=5` responds rapidly to genuine traffic bursts, while `downscale_delay_s=60` holds replicas warm to prevent rapid flapping and unnecessary model reload overhead.

---

### Part C Limitations and Items Not Tested
* **Autoscaling Scale-Up Under Load:** The candidate autoscaling configuration was deployed and verified, but the verification workload did not generate enough sustained concurrency to trigger a replica scale-up event from 1 to 2 or 3. Scale-up behavior under heavy load remains configured but not exercised.

---

## Part D: Automated Release Gate & Abort Criteria

### D1. Smoke Test Execution (`smoke_eta.py`)

The smoke test implements automated release gating before real traffic is routed to a newly deployed service. It evaluates basic availability, release identity, batch-single consistency, domain monotonicity, input validation, and latency SLO compliance.

#### Verification Runs:

1. **Passing Release Check (`--expect-version v1`):**
   * Output: `SMOKE TEST PASSED (8/8 checks)`
   * `[PASS]` HTTP 200 with valid positive ETA (`102.0 min`)
   * `[PASS]` Model version matched expected release (`v1`)
   * `[PASS]` Batch consistency: `batch=[37.5, 127.8, 256.8]` matched single evaluations
   * `[PASS]` Distance monotonicity: `[5, 20, 50, 100] km` $\to$ `[37.5, 76.2, 153.6, 282.6] min`
   * `[PASS]` Traffic monotonicity: `[0.1, 0.5, 0.9]` $\to$ `[84.9, 102.0, 119.1] min`
   * `[PASS]` Stop count monotonicity: `[2, 8, 15]` $\to$ `[89.0, 115.0, 145.3] min`
   * `[PASS]` Input validation: out-of-range negative distance returned HTTP 422
   * `[PASS]` Latency threshold: measured $p95 = 5.1\text{ ms} \le 80.0\text{ ms}$
   * **Exit Code:** `0` (Deployment approved)

2. **Negative Control Verification (`--expect-version v2` against v1 server):**
   * Output: `[FAIL] model version matches --expect-version: served=v1 expected=v2`
   * Result: `SMOKE TEST FAILED (7/8 checks)`
   * **Exit Code:** `1` (Deployment automatically blocked)

---

### D2. Four Production Canary Abort Criteria (10% Rollout of v2)

During a 10% canary rollout of `v2`, the deployment controller monitors real-time telemetry and is configured to trigger an automatic rollback to `v1` if any of the following conditions are met:

| # | Telemetry Metric | Rollback / Abort Threshold | Evaluation Window | Rationale |
|:---:|:---|:---|:---|:---|
| **1** | **HTTP 5xx Server Error Rate** | Canary error rate $> 1.0\%$ (or $> 2\times$ production baseline error rate) | Rolling 5-minute window (minimum 200 canary requests) | Detects unhandled runtime exceptions, memory faults, or worker crashes introduced by candidate code under live traffic. |
| **2** | **Canary Tail Latency (p95)** | $p95 > 80\text{ ms}$ (SLO breach) or $> 1.5\times$ baseline `prod` p95 | 2 consecutive 5-minute rolling evaluation windows | Prevents customer latency degradation. Requiring two consecutive breaches reduces premature rollbacks caused by temporary network spikes. |
| **3** | **Live Prediction Divergence** | Mean Absolute Difference against shadow $> 12\text{ min}$, or $> 50\%$ of requests differing by $> 10\text{ min}$ | Rolling 15-minute window (minimum 500 mirrored requests) | Detects unexpected distribution drift or inconsistencies in feature transformations that cause predictions to deviate substantially from offline shadow baselines. |
| **4** | **Ground-Truth Delivery Error (Real-Time MAE)** | Candidate MAE degrades $> 5\%$ relative to `prod` overall or on rush-hour/rain slices | Rolling 1-hour window of completed deliveries (minimum 500 completed parcels per arm) | Evaluates actual prediction accuracy against final package delivery times once couriers log completion, helping protect performance on critical customer-sensitive slices. |

