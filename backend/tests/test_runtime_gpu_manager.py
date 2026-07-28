"""Runtime GPU diagnostics should report real status without faking success."""

from src.runtime.gpu_manager import (
    default_gpu_selection,
    environment_status,
    gpu_selection_options,
    nvidia_smi_status,
    tensorflow_status,
    torch_status,
)
from src.runtime.inference_scheduler import InferenceScheduler


def test_runtime_status_shapes_are_stable() -> None:
    status = environment_status(run_matrix_test=False)
    assert {"python", "platform", "nvidia_smi", "torch", "tensorflow"}.issubset(status)
    assert "cuda_available" in torch_status(run_matrix_test=False)
    assert "gpu_available" in tensorflow_status(run_matrix_test=False)
    assert "gpus" in nvidia_smi_status()


def test_gpu_selection_options_include_cpu_auto_and_detected_gpus(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.runtime.gpu_manager.nvidia_smi_status",
        lambda: {
            "available": True,
            "gpus": [{
                "index": 1,
                "name": "RTX Test",
                "driver_version": "999.99",
                "memory_total_mb": 16000,
                "memory_used_mb": 512,
            }],
        },
    )

    options = gpu_selection_options()
    by_value = {option["value"]: option for option in options}

    assert {"auto", "cpu", "cuda:1"}.issubset(by_value)
    assert "优先可用 GPU" in by_value["auto"]["label"]
    assert by_value["cuda:1"]["molscribe_device"] == "cuda:1"
    assert by_value["cuda:1"]["decimer_device"] == "gpu"
    assert by_value["cuda:1"]["visible_gpu_index"] == "1"


def test_default_gpu_selection_prefers_env_cuda(monkeypatch) -> None:
    options = [
        {"value": "auto"},
        {"value": "cpu"},
        {"value": "cuda:0"},
    ]
    monkeypatch.setenv("OCSR_DEVICE", "cuda:0")
    monkeypatch.setenv("DECIMER_DEVICE", "gpu")
    assert default_gpu_selection(options) == "cuda:0"


def test_inference_scheduler_limits_gpu_slots() -> None:
    scheduler = InferenceScheduler(max_gpu_concurrent=1)
    with scheduler.slot_for_device("unit", "cuda:0"):
        try:
            with scheduler.slot_for_device("unit", "cuda:0"):
                raise AssertionError("second GPU slot should not be available")
        except RuntimeError as exc:
            assert "GPU 推理队列繁忙" in str(exc)
