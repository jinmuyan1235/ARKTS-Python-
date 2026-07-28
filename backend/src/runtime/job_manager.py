"""Managed subprocess helpers for model and UI jobs."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any, Sequence


MODEL_WORKER_RESULT_MARKER = "MODEL_WORKER_RESULT_JSON="


@dataclass
class ProcessResult:
    """Captured result from a managed subprocess."""

    command: list[str]
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool
    elapsed_ms: float
    payload: dict[str, Any] | None = None

    def last_output_line(self) -> str:
        output = (self.stderr or self.stdout or "").strip().splitlines()
        return output[-1] if output else ""


def _popen_kwargs(cwd: str | Path | None, env: dict[str, str] | None, text: bool = True) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "cwd": str(cwd) if cwd is not None else None,
        "env": env,
        "text": text,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return kwargs


def start_process(
    command: Sequence[str | os.PathLike[str]],
    *,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
    stdout: Any = subprocess.PIPE,
    stderr: Any = subprocess.PIPE,
    text: bool = True,
) -> subprocess.Popen[str]:
    """Start a subprocess in its own process group/session."""
    normalized = [str(part) for part in command]
    return subprocess.Popen(
        normalized,
        stdout=stdout,
        stderr=stderr,
        **_popen_kwargs(cwd, env, text=text),
    )


def terminate_process_tree(process: subprocess.Popen[Any], grace_seconds: float = 3.0) -> None:
    """Terminate a subprocess and its children when the platform supports it."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                text=True,
                timeout=max(grace_seconds, 1.0),
            )
        except Exception:
            if process.poll() is None:
                process.kill()
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=grace_seconds)
    except Exception:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except Exception:
                process.kill()


def is_process_alive(pid: int | None) -> bool:
    """Return whether a process id appears to still be alive."""
    if not pid or pid <= 0:
        return False
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            return f'"{pid}"' in result.stdout or f",{pid}," in result.stdout
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return False


def terminate_process_tree_by_pid(pid: int | None, grace_seconds: float = 3.0) -> None:
    """Terminate a process group/session by pid when the Popen object is unavailable."""
    if not is_process_alive(pid):
        return
    assert pid is not None
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
                timeout=max(grace_seconds, 1.0),
            )
        except Exception:
            return
        return
    try:
        os.killpg(pid, signal.SIGTERM)
        deadline = time.perf_counter() + grace_seconds
        while time.perf_counter() < deadline:
            if not is_process_alive(pid):
                return
            time.sleep(0.05)
        if is_process_alive(pid):
            os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except Exception:
        try:
            os.kill(pid, signal.SIGTERM)
        except Exception:
            return


def run_process(
    command: Sequence[str | os.PathLike[str]],
    *,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
) -> ProcessResult:
    """Run a command, killing the process tree on timeout."""
    normalized = [str(part) for part in command]
    started = time.perf_counter()
    process = start_process(normalized, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=timeout if timeout and timeout > 0 else None)
    except subprocess.TimeoutExpired:
        timed_out = True
        terminate_process_tree(process)
        try:
            stdout, stderr = process.communicate(timeout=5)
        except Exception:
            stdout, stderr = "", ""
    elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
    return ProcessResult(
        command=normalized,
        returncode=process.returncode,
        stdout=stdout or "",
        stderr=stderr or "",
        timed_out=timed_out,
        elapsed_ms=elapsed_ms,
    )


def extract_json_object(text: str, marker: str | None = None) -> dict[str, Any] | None:
    """Extract a JSON object from logs, optionally after a marker prefix."""
    if marker:
        for line in reversed(text.splitlines()):
            if line.startswith(marker):
                try:
                    value = json.loads(line[len(marker) :])
                except json.JSONDecodeError:
                    return None
                return value if isinstance(value, dict) else None

    stripped = text.strip()
    if not stripped:
        return None
    decoder = json.JSONDecoder()
    for index, char in enumerate(stripped):
        if char != "{":
            continue
        try:
            value, _end = decoder.raw_decode(stripped[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def run_json_command(
    command: Sequence[str | os.PathLike[str]],
    *,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
    marker: str | None = None,
) -> ProcessResult:
    """Run a managed command and parse the JSON payload from stdout."""
    result = run_process(command, cwd=cwd, env=env, timeout=timeout)
    result.payload = extract_json_object(result.stdout, marker=marker)
    return result


def start_logged_process(
    command: Sequence[str | os.PathLike[str]],
    *,
    cwd: str | Path | None,
    env: dict[str, str] | None,
    stdout_path: str | Path,
    stderr_path: str | Path,
) -> subprocess.Popen[str]:
    """Start a managed subprocess whose stdout/stderr are written to files."""
    out_path = Path(stdout_path)
    err_path = Path(stderr_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    err_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as stdout_file, err_path.open("w", encoding="utf-8") as stderr_file:
        return start_process(command, cwd=cwd, env=env, stdout=stdout_file, stderr=stderr_file, text=True)
