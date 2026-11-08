# Module 4 - Assignment 5 report (SwiftParcel ETA)

**Name / roll no:** ______   **Date:** ______

## 0. Machine and honesty statement
- Host: ______ ; VirtualBox VM: ___ CPU cores, ___ GB RAM, Ubuntu ___ ; Python ___ ; mlflow ___ ; ray ___ ; fastapi ___
- Lecture material reused / cited: ______ (or "none")
- **NOT tested / not done:** (list everything you did not run, e.g. "autoscaling under real load", "mlflow serve with virtualenv env-manager", ...)

## Part A
### A3.1 Load-test table (v1, batch 1, 4 s per level)
(paste the table printed by load_test.py)

### A3.2 Saturation point and capacity under p95 <= 80 ms
Little's Law: L = lambda * W (requests in system = throughput x latency). ...

### A3.3 Cost per row: batch 1 vs batch 100 (concurrency 1)
| batch | rps | ms/request | ms/row |
|---|---|---|---|
| 1 | | | |
| 100 | | | |
Explanation: ...

### A3.4 Change to ~double capacity
Change: ...   Before: ___ rps   After: ___ rps   Result (honest): ...

### A3.5 Why p95/p99 and not the mean (two sentences)
...

## Part B
### B1 Registry table
| version | alias | bundle |
|---|---|---|

### B2 Gate output
Default thresholds: (paste, or summarize + point to partB/gate_output.txt)
min_rel_gain = 0.60: ...
Assumption: slice regression is relative to prod's slice MAE.

### B3 Shadow comparison
(i) mean abs diff ___ min   (ii) ___ % differ by > 10 min   (iii) p95 prod ___ ms, candidate ___ ms
Decision (~5 sentences): ...
Why moving an alias does not change a running server: ...
Two ways a running service picks up the new version without dropping requests: 1) ... 2) ...

## Part C
### C3 Short answers
1. hash(): ...
2. Why stickiness matters (two reasons): ...
3. Autoscaling parameters: ...

## Part D
Abort criteria for a 10 % canary of v2:
| # | metric | threshold | window |
|---|---|---|---|
