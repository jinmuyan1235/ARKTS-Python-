"""Managed subprocess helpers."""

from __future__ import annotations

import sys

from src.runtime.job_manager import MODEL_WORKER_RESULT_MARKER, extract_json_object, run_json_command, run_process


def test_extract_json_object_with_marker_and_trailing_logs() -> None:
    payload = extract_json_object(
        "native log\n"
        f'{MODEL_WORKER_RESULT_MARKER}{{"status": "success", "value": 3}}\n'
        "later warning",
        marker=MODEL_WORKER_RESULT_MARKER,
    )
    assert payload == {"status": "success", "value": 3}


def test_run_process_times_out_and_terminates() -> None:
    result = run_process([sys.executable, "-c", "import time; time.sleep(10)"], timeout=0.1)
    assert result.timed_out is True


def test_run_json_command_parses_stdout_payload() -> None:
    result = run_json_command(
        [sys.executable, "-c", "print('noise'); print('{\"status\":\"success\",\"answer\":42}')"],
        timeout=5,
    )
    assert result.returncode == 0
    assert result.payload == {"status": "success", "answer": 42}
