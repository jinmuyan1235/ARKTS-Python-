#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${MOLECULE_VISION_GPU_VENV:-$HOME/.venvs/molecule-vision-gpu}"
source "$VENV_DIR/bin/activate"
cd "$PROJECT_ROOT"

echo "[startup] project: $PROJECT_ROOT"
echo "[startup] venv: $VIRTUAL_ENV"

export OCSR_DEVICE="${OCSR_DEVICE:-cuda:0}"
export APP_MODE="${APP_MODE:-production}"
export OCSR_BACKEND="${OCSR_BACKEND:-molscribe}"
export DECIMER_DEVICE="${DECIMER_DEVICE:-gpu}"
export MOLSCRIBE_ISOLATED_SUBPROCESS="${MOLSCRIBE_ISOLATED_SUBPROCESS:-true}"
export DECIMER_ISOLATED_SUBPROCESS="${DECIMER_ISOLATED_SUBPROCESS:-true}"
export OCSR_GPU_REQUIRED="${OCSR_GPU_REQUIRED:-true}"
export OCSR_GPU_MAX_CONCURRENT_INFERENCE="${OCSR_GPU_MAX_CONCURRENT_INFERENCE:-1}"
export OCSR_ENSEMBLE_PARALLEL="${OCSR_ENSEMBLE_PARALLEL:-false}"
export DECISION_REQUIRE_CALIBRATED_CONFIDENCE="${DECISION_REQUIRE_CALIBRATED_CONFIDENCE:-true}"
export MOLSCRIBE_MODEL_PATH="${MOLSCRIBE_MODEL_PATH:-models/molscribe/swin_base_char_aux_1m.pth}"
export LD_LIBRARY_PATH="$VIRTUAL_ENV/lib/python3.10/site-packages/nvidia/cudnn/lib:$VIRTUAL_ENV/lib/python3.10/site-packages/nvidia/cublas/lib:$VIRTUAL_ENV/lib/python3.10/site-packages/nvidia/cuda_runtime/lib:$VIRTUAL_ENV/lib/python3.10/site-packages/nvidia/cuda_cupti/lib:$VIRTUAL_ENV/lib/python3.10/site-packages/nvidia/cuda_nvrtc/lib:$VIRTUAL_ENV/lib/python3.10/site-packages/nvidia/cufft/lib:$VIRTUAL_ENV/lib/python3.10/site-packages/nvidia/cufile/lib:$VIRTUAL_ENV/lib/python3.10/site-packages/nvidia/curand/lib:$VIRTUAL_ENV/lib/python3.10/site-packages/nvidia/cusolver/lib:$VIRTUAL_ENV/lib/python3.10/site-packages/nvidia/cusparse/lib:$VIRTUAL_ENV/lib/python3.10/site-packages/nvidia/cusparselt/lib:$VIRTUAL_ENV/lib/python3.10/site-packages/nvidia/nccl/lib:$VIRTUAL_ENV/lib/python3.10/site-packages/nvidia/nvjitlink/lib:$VIRTUAL_ENV/lib/python3.10/site-packages/nvidia/nvtx/lib:/usr/lib/wsl/lib:${LD_LIBRARY_PATH:-}"

echo "[startup] APP_MODE=$APP_MODE OCSR_BACKEND=$OCSR_BACKEND OCSR_DEVICE=$OCSR_DEVICE DECIMER_DEVICE=$DECIMER_DEVICE"
echo "[startup] DECISION_REQUIRE_CALIBRATED_CONFIDENCE=$DECISION_REQUIRE_CALIBRATED_CONFIDENCE"
if [[ "${FAST_START:-false}" == "true" ]]; then
  export SKIP_GPU_ENV_CHECK=true
  export SKIP_OCSR_PRODUCTION_CHECK=true
  echo "[startup] FAST_START=true: skipping GPU/package checks and model warm-up; UI will still report backend status."
fi

if [[ "${SKIP_GPU_ENV_CHECK:-false}" != "true" ]]; then
  echo "[startup] checking GPU/package environment..."
  python scripts/verify_gpu_environment.py --no-strict
else
  echo "[startup] skipped GPU/package environment check."
fi
if [[ "${SKIP_OCSR_PRODUCTION_CHECK:-false}" != "true" ]]; then
  echo "[startup] running production OCSR warm-up; first model load can take a few minutes."
  echo "[startup] set FAST_START=true or SKIP_OCSR_PRODUCTION_CHECK=true to open the UI immediately."
  python scripts/check_ocsr_backend.py --backend "$OCSR_BACKEND" --production --warmup
else
  echo "[startup] skipped production OCSR warm-up."
fi
echo "[startup] launching Streamlit..."
python -m streamlit run app.py \
  --server.runOnSave=false \
  --server.fileWatcherType=none \
  --server.websocketPingInterval=20
