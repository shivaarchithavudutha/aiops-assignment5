#!/usr/bin/env bash
# Usage (from the repo root, in EVERY new terminal):   source env.sh
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$ROOT/.venv/bin/activate"
export PYTHONPATH="$ROOT:$ROOT/partA:$ROOT/partB:$ROOT/partC:$ROOT/partD"
export MLFLOW_TRACKING_URI="sqlite:///$ROOT/mlflow.db"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
cd "$ROOT"
echo "env ready: venv active, PYTHONPATH set, MLFLOW_TRACKING_URI=$MLFLOW_TRACKING_URI"
