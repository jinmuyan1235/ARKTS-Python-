"""Run the official MolScribe trainer under strict feasibility limits."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import queue
import re
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path


OFFICIAL_COMMIT = "7296a30413eb55436702011efdff78131f66d162"
STEP_PATTERN = re.compile(
    r"Epoch:\s*\[\d+\]\[(\d+)/(\d+)\].*?Loss:\s*([0-9.eE+\-]+).*?Grad:\s*([0-9.eE+\-]+)/([0-9.eE+\-]+)"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def gpu_process_memory() -> int:
    result = subprocess.run(
        ["nvidia-smi", "--query-compute-apps=used_memory", "--format=csv,noheader,nounits"],
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
    )
    values = []
    for line in result.stdout.splitlines():
        match = re.search(r"(\d+)", line)
        if match:
            values.append(int(match.group(1)))
    return max(values, default=0)


def stream_output(process: subprocess.Popen[str], events: queue.Queue[tuple[float, str]], log_path: Path) -> None:
    with log_path.open("w", encoding="utf-8") as log:
        assert process.stdout is not None
        for line in process.stdout:
            stamp = time.monotonic()
            log.write(line); log.flush()
            events.put((stamp, line.rstrip()))


def main() -> int:
    capability_path = Path(__file__).resolve().parents[1] / "config" / "model_capabilities.json"
    capabilities = json.loads(capability_path.read_text(encoding="utf-8"))
    if capabilities.get("fine_tuning_enabled") is False:
        raise SystemExit(
            "Model fine-tuning phase is closed: MolScribe is blocked_before_first_backward; no training is permitted."
        )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--official-source", type=Path, required=True)
    parser.add_argument("--probe-data", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--production-site-packages", type=Path, required=True)
    parser.add_argument("--probe-site-packages", type=Path, required=True)
    parser.add_argument("--max-steps", type=int, default=100)
    parser.add_argument("--timeout-seconds", type=int, default=7200)
    parser.add_argument("--memory-stop-mib", type=int, default=15360)
    args = parser.parse_args()
    args.python = args.python.absolute()
    args.official_source = args.official_source.resolve()
    args.probe_data = args.probe_data.resolve()
    args.checkpoint = args.checkpoint.resolve()
    args.output = args.output.resolve()
    args.production_site_packages = args.production_site_packages.resolve()
    args.probe_site_packages = args.probe_site_packages.resolve()
    if args.max_steps > 100 or args.timeout_seconds > 7200 or args.memory_stop_mib > 15360:
        raise SystemExit("Probe limits may not be relaxed")
    if args.output.exists():
        raise SystemExit(f"Refusing to overwrite {args.output}")
    if not all((args.official_source / name).is_file() for name in ("train.py", "evaluate.py")):
        raise SystemExit("Official MolScribe training source is incomplete")
    revision = subprocess.check_output(
        ["git", "-C", str(args.official_source), "rev-parse", "HEAD"], text=True
    ).strip()
    if revision != OFFICIAL_COMMIT:
        raise SystemExit(f"Unexpected official source revision: {revision}")

    args.output.mkdir(parents=True)
    checkpoint_hash_before = sha256_file(args.checkpoint)
    environment = os.environ.copy()
    environment["PYTHONUNBUFFERED"] = "1"
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(args.official_source), str(args.probe_site_packages), str(args.production_site_packages)]
    )
    nvidia_root = args.production_site_packages / "nvidia"
    cuda_libraries = [
        str(path)
        for path in sorted(nvidia_root.glob("*/lib"))
        if path.is_dir()
    ]
    wsl_driver = Path("/usr/lib/wsl/lib")
    if wsl_driver.is_dir():
        cuda_libraries.append(str(wsl_driver))
    existing_ld = [item for item in environment.get("LD_LIBRARY_PATH", "").split(":") if item]
    environment["LD_LIBRARY_PATH"] = ":".join(dict.fromkeys([*cuda_libraries, *existing_ld]))
    command = [
        str(args.python), "-m", "torch.distributed.run", "--nproc_per_node=1", "--nnodes=1",
        "--master_addr=127.0.0.1", "--master_port=29571", str(args.official_source / "train.py"),
        "--data_path", str(args.probe_data), "--train_file", "train_probe.csv", "--valid_file", "valid.csv",
        "--vocab_file", str(args.official_source / "molscribe/vocab/vocab_chars.json"),
        "--formats", "chartok_coords,edges", "--coord_bins", "128", "--sep_xy", "--input_size", "384",
        "--encoder", "swin_base", "--decoder", "transformer", "--encoder_lr", "4e-4", "--decoder_lr", "4e-4",
        "--scheduler", "cosine", "--warmup_ratio", "0.02", "--label_smoothing", "0.1",
        "--batch_size", "1", "--gradient_accumulation_steps", "1", "--epochs", "1",
        "--train_steps_per_epoch", str(args.max_steps), "--num_workers", "1", "--print_freq", "1",
        "--load_path", str(args.checkpoint), "--save_path", str(args.output), "--save_mode", "last",
        "--use_checkpoint", "--fp16", "--backend", "gloo", "--do_train",
    ]
    started = time.monotonic()
    process = subprocess.Popen(
        command,
        cwd=args.official_source,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    events: queue.Queue[tuple[float, str]] = queue.Queue()
    reader = threading.Thread(
        target=stream_output, args=(process, events, args.output / "training.log"), daemon=True
    )
    reader.start()

    step_events: list[dict[str, float | int]] = []
    peak_memory = 0
    first_batch_peak = None
    stop_reason = None
    nonfinite_streak = 0
    while process.poll() is None:
        elapsed = time.monotonic() - started
        if elapsed > args.timeout_seconds:
            stop_reason = "timeout"
            process.terminate()
        try:
            memory = gpu_process_memory()
            peak_memory = max(peak_memory, memory)
            if memory > args.memory_stop_mib:
                stop_reason = "gpu_memory_limit"
                process.terminate()
        except Exception:
            pass
        while True:
            try:
                timestamp, line = events.get_nowait()
            except queue.Empty:
                break
            lower = line.lower()
            if "out of memory" in lower:
                stop_reason = "oom"
                process.terminate()
            match = STEP_PATTERN.search(line)
            if match:
                step, total = int(match.group(1)), int(match.group(2))
                loss, encoder_grad, decoder_grad = map(float, match.group(3, 4, 5))
                finite = all(math.isfinite(value) for value in (loss, encoder_grad, decoder_grad))
                nonfinite_streak = 0 if finite else nonfinite_streak + 1
                step_events.append({
                    "step": step + 1, "total": total, "timestamp": timestamp,
                    "loss": loss, "encoder_grad": encoder_grad, "decoder_grad": decoder_grad,
                })
                if first_batch_peak is None:
                    first_batch_peak = peak_memory
                if nonfinite_streak >= 2:
                    stop_reason = "consecutive_nonfinite_values"
                    process.terminate()
        if stop_reason:
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
            break
        time.sleep(0.2)
    reader.join(timeout=10)
    return_code = process.wait()
    elapsed = time.monotonic() - started
    log_text = (args.output / "training.log").read_text(encoding="utf-8", errors="replace")
    if return_code != 0 and stop_reason is None:
        if "modified by an inplace operation" in log_text:
            stop_reason = "official_training_inplace_autograd_failure"
        else:
            stop_reason = "training_process_failure"

    intervals = [
        float(step_events[index]["timestamp"]) - float(step_events[index - 1]["timestamp"])
        for index in range(1, len(step_events))
    ]
    saved = args.output / "swin_base_transformer_last.pth"
    if not saved.is_file():
        candidates = sorted(args.output.glob("*.pth"))
        saved = candidates[-1] if candidates else saved

    reload_result: dict[str, object] = {"attempted": False, "success": False}
    if return_code == 0 and saved.is_file() and not stop_reason:
        dev_image = next((args.probe_data / "images").glob("*.png"))
        reload_code = (
            "import json,torch; from molscribe import MolScribe; "
            f"m=MolScribe(r'{saved}',device=torch.device('cuda:0')); "
            f"print(json.dumps(m.predict_image_file(r'{dev_image}'),default=str))"
        )
        reload_run = subprocess.run(
            [str(args.python), "-c", reload_code], env=environment, text=True,
            capture_output=True, timeout=600, check=False,
        )
        reload_result = {
            "attempted": True,
            "success": reload_run.returncode == 0,
            "returncode": reload_run.returncode,
            "stdout_tail": reload_run.stdout[-2000:],
            "stderr_tail": reload_run.stderr[-2000:],
        }

    dev_scores_path = args.output / "best_valid.json"
    dev_scores = json.loads(dev_scores_path.read_text()) if dev_scores_path.is_file() else None
    result = {
        "status": "completed" if return_code == 0 and not stop_reason else "stopped",
        "official_commit": revision,
        "command": command,
        "returncode": return_code,
        "stop_reason": stop_reason,
        "elapsed_seconds": round(elapsed, 3),
        "completed_training_steps": len(step_events),
        "first_batch_peak_gpu_memory_mib": first_batch_peak,
        "peak_gpu_process_memory_mib": peak_memory,
        "mean_step_seconds": round(statistics.mean(intervals), 4) if intervals else None,
        "median_step_seconds": round(statistics.median(intervals), 4) if intervals else None,
        "p95_step_seconds": round(sorted(intervals)[int(0.95 * (len(intervals) - 1))], 4) if intervals else None,
        "first_loss": step_events[0]["loss"] if step_events else None,
        "last_loss": step_events[-1]["loss"] if step_events else None,
        "all_logged_gradients_finite": all(
            math.isfinite(float(item[key])) for item in step_events for key in ("encoder_grad", "decoder_grad")
        ),
        "saved_checkpoint": str(saved) if saved.is_file() else None,
        "saved_checkpoint_sha256": sha256_file(saved) if saved.is_file() else None,
        "checkpoint_reload": reload_result,
        "dev_scores": dev_scores,
        "original_checkpoint_sha256_before": checkpoint_hash_before,
        "original_checkpoint_sha256_after": sha256_file(args.checkpoint),
        "original_checkpoint_unchanged": sha256_file(args.checkpoint) == checkpoint_hash_before,
    }
    (args.output / "probe_result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "completed" and reload_result.get("success") else 2


if __name__ == "__main__":
    raise SystemExit(main())
