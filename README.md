# SwiftParcel ETA service: REST, MLflow, Ray Serve, smoke test (AIOps Module 4, Assignment 5)

Serves a delivery-time (ETA) regression model four ways: a FastAPI REST service (Part A), an MLflow
registry + promotion gate + shadow comparison (Part B), a Ray Serve sticky canary (Part C) and a
release-gate smoke test (Part D). CPU only. Tested layout: Ubuntu 22.04/24.04, Python 3.10+.

## Repository layout
```
eta_data.py  load_test.py  requirements.txt   <- given starter files
env.sh                                        <- one-line setup per terminal
partA/  eta_app.py  test_eta_app.py  loadtest_results.json
partB/  eta_mlflow.py  test_gate.py  shadow.py  gate_output.txt  shadow_output.txt
partC/  eta_ray.py  verify_ray.py  verify_output.txt
partD/  smoke_eta.py
REPORT.md                                     <- tables, answers, machine description, NOT-tested list
```

## 1. One-time setup (from a clean checkout)
```bash
sudo apt update && sudo apt install -y python3 python3-venv python3-pip git
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python eta_data.py            # writes data/eta_train.csv, eta_fresh.csv, eta_v1.joblib, eta_v2.joblib
python -c "import pandas as pd, eta_data; pd.read_csv('data/eta_fresh.csv')[eta_data.FEATURES].head(200).to_csv('data/eta_sample.csv', index=False)"
```
In every new terminal afterwards:  `source env.sh`   (activates the venv, sets PYTHONPATH, MLFLOW_TRACKING_URI).

## 2. Part A: REST service
```bash
python -m pytest partA -v                         # 15+ tests
uvicorn eta_app:app --port 8000                   # serves v1 (ETA_MODEL=data/eta_v2.joblib for v2)
curl -s localhost:8000/healthz
python load_test.py --url http://localhost:8000/v1/models/eta:predict --data data/eta_sample.csv \
       --concurrency 1 2 4 8 16 --duration 4 --out partA/loadtest_results.json
```

## 3. Part B: MLflow
```bash
rm -rf mlflow.db mlruns                           # start from a clean registry
python partB/eta_mlflow.py register               # logs v1, v2; aliases prod->v1, candidate->v2
python -m pytest partB -v
python partB/eta_mlflow.py gate | tee partB/gate_output.txt
python partB/eta_mlflow.py gate --min-rel-gain 0.60 | tee -a partB/gate_output.txt
# shadow: two terminals (after `source env.sh` in each)
mlflow models serve -m "models:/eta-model@prod"      -p 5101 --env-manager local
mlflow models serve -m "models:/eta-model@candidate" -p 5102 --env-manager local
python partB/shadow.py | tee partB/shadow_output.txt
```

## 4. Part C: Ray Serve sticky canary
```bash
python partC/verify_ray.py | tee partC/verify_output.txt     # starts its own 2-CPU Ray, runs 5 checks
```

## 5. Part D: smoke test
```bash
python partD/smoke_eta.py --url http://localhost:8000/v1/models/eta:predict --expect-version v1 --max-p95-ms 80
echo "exit code: $?"
```
