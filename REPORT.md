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

In a single-process Python/Uvicorn server running on a CPU-bound workload, Python's Global Interpreter Lock (GIL) constrains execution to a single core. Furthermore, because both the load generator and the Uvicorn server run inside the same VM, adding concurrent simulated clients causes CPU contention, thread starvation, and context-switching overhead. Once the server reaches maximum throughput (X_max), throughput stops growing. According to Little's Law, when lambda is fixed at X_max, average latency scales directly with queue depth: W = L / X_max. Additional concurrent clients simply queue up waiting for the single Python thread, leading to queue growth that causes p95 latency to jump to 606.1 ms at concurrency 2 and over 3600 ms at concurrency 16.

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

To resolve the single-core GIL bottleneck, Uvicorn was configured with two worker processes across the available CPU cores (uvicorn eta_app:app --port 8000 --log-level warning --workers 2).

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
* Lecture Re-use Citation: None (implemented directly from starter contract specifications).
