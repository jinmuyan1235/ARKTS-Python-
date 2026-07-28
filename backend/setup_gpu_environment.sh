#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${MOLECULE_VISION_GPU_VENV:-$HOME/.venvs/molecule-vision-gpu}"

echo "[1/6] Checking WSL/Linux GPU visibility"
uname -a
nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader

echo "[2/6] Creating Python environment at $VENV_DIR"
PYTHON_BIN="${PYTHON_BIN:-python3.10}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "python3.10 not found; falling back to python3. DECIMER officially documents Python 3.10, so this may fail."
  PYTHON_BIN="python3"
fi
"$PYTHON_BIN" -m venv "$VENV_DIR"
source "$VENV_DIR/bin/activate"
python -m pip install --upgrade pip setuptools wheel

echo "[3/6] Installing core application dependencies"
python -m pip install \
  "numpy>=1.24,<2.0" \
  "opencv-python-headless>=4.8,<5" \
  "Pillow>=10,<12" \
  "pandas>=2.0,<3" \
  "matplotlib>=3.7,<4" \
  "scikit-learn>=1.3,<2" \
  "joblib>=1.3,<2" \
  "streamlit>=1.41,<2" \
  "rdkit>=2023.9.1" \
  "pytest>=7.4,<9" \
  "pymupdf>=1.24,<2"

python -m pip install "reportlab>=4.0,<5" || echo "WARNING: reportlab installation failed; PDF export may be unavailable in this GPU venv."

echo "[4/6] Installing PyTorch CUDA build and MolScribe"
python -m pip install --index-url https://download.pytorch.org/whl/cu128 torch torchvision
python -m pip install huggingface_hub MolScribe

echo "[5/6] Installing TensorFlow CUDA extras and DECIMER"
python -m pip install "tensorflow[and-cuda]" decimer

echo "[5b/6] Repairing known NumPy/RDKit ABI conflicts from optional OCSR dependencies"
python -m pip uninstall -y rdkit-pypi || true
python -m pip uninstall -y opencv-python || true
python -m pip install --force-reinstall "numpy>=1.26,<2.0" "Pillow>=10,<12" "opencv-python-headless>=4.8,<5" "rdkit>=2023.9.1"

echo "[6/6] Writing GPU environment hint"
cat > "$PROJECT_ROOT/.env.gpu.example" <<'EOF'
APP_MODE=production
OCSR_BACKEND=molscribe
OCSR_DEVICE=cuda:0
DECIMER_DEVICE=gpu
OCSR_GPU_REQUIRED=true
OCSR_GPU_MAX_CONCURRENT_INFERENCE=1
OCSR_ENSEMBLE_PARALLEL=false
MOLSCRIBE_MODEL_PATH=models/molscribe/swin_base_char_aux_1m.pth
SKIP_OCSR_PRODUCTION_CHECK=false
LD_LIBRARY_PATH=/usr/lib/wsl/lib:$VIRTUAL_ENV/lib/python3.10/site-packages/nvidia/cudnn/lib:$VIRTUAL_ENV/lib/python3.10/site-packages/nvidia/cublas/lib:$VIRTUAL_ENV/lib/python3.10/site-packages/nvidia/cuda_runtime/lib:$VIRTUAL_ENV/lib/python3.10/site-packages/nvidia/cuda_cupti/lib:$VIRTUAL_ENV/lib/python3.10/site-packages/nvidia/cufft/lib:$VIRTUAL_ENV/lib/python3.10/site-packages/nvidia/curand/lib:$VIRTUAL_ENV/lib/python3.10/site-packages/nvidia/cusolver/lib:$VIRTUAL_ENV/lib/python3.10/site-packages/nvidia/cusparse/lib:$LD_LIBRARY_PATH
EOF

echo "GPU environment created. Activate with:"
echo "source \"$VENV_DIR/bin/activate\""
echo "Then run:"
echo "python scripts/download_ocsr_models.py"
echo "python scripts/check_ocsr_backend.py --backend molscribe --production --warmup"
