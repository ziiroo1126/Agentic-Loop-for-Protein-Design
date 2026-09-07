"""Logged subprocess execution with retained failure and timeout evidence."""

from __future__ import annotations

import math
import os
import signal
import subprocess
import threading
import time
from pathlib import Path

from interaction_design.persistence import write_json
from interaction_design.runtime.base import ODesignExecutionError


class _SigtermExit(SystemExit):
    """Allow outer BaseException handlers to retain their cancellation receipts."""

    def __init__(self) -> None:
        super().__init__(128 + signal.SIGTERM)

    def __str__(self) -> str:
        return "received SIGTERM"


def run_logged(
    command: list[str],
    run_dir: Path,
    *,
    metadata: dict[str, object],
    timeout_seconds: float | None = None,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    label: str = "ODesign",
    log_prefix: str = "odesign",
) -> tuple[Path, Path]:
    """Run a worker in its own session, retaining logs and execution evidence.

    On the main thread, default SIGTERM becomes SystemExit(143) after cleanup,
    so callers can record their own failure receipts. Existing SIGTERM policies
    and calls from other threads are left alone. SIGKILL cannot be intercepted;
    descendants that leave the worker's process group escape group cleanup.
    """
    if timeout_seconds is not None and (not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
        raise ValueError("timeout must be finite and positive")
    stdout_path = run_dir / f"{log_prefix}.stdout.log"
    stderr_path = run_dir / f"{log_prefix}.stderr.log"
    record = {
        "command": command,
        "cwd": str(cwd) if cwd else None,
        "metadata": metadata,
        "timeout_seconds": timeout_seconds,
        "status": "running",
    }
    write_json(run_dir / "execution.json", record)
    started = time.monotonic()
    process = None
    worker_stopped = False
    termination = None
    waiting = False
    owns_sigterm = (
        threading.current_thread() is threading.main_thread()
        and signal.getsignal(signal.SIGTERM) == signal.SIG_DFL
    )

    def request_termination(signum, frame):
        nonlocal termination
        if termination is None:
            termination = _SigtermExit()
            # Defer interruption until Popen has returned its process handle. Also
            # defer during cleanup/persistence, including repeated SIGTERMs.
            if waiting:
                raise termination

    def stop_worker():
        nonlocal worker_stopped
        if process is not None and not worker_stopped:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            record["returncode"] = process.returncode
            worker_stopped = True

    if owns_sigterm:
        signal.signal(signal.SIGTERM, request_termination)
    try:
        with stdout_path.open("w") as stdout, stderr_path.open("w") as stderr:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=env,
                stdout=stdout,
                stderr=stderr,
                start_new_session=True,
            )
            record["pid"] = process.pid
            write_json(run_dir / "execution.json", record)
            try:
                waiting = True
                if termination is not None:
                    raise termination
                code = process.wait(timeout=timeout_seconds)
            finally:
                waiting = False
        record["returncode"] = code
        if code != 0:
            with stderr_path.open("rb") as handle:
                handle.seek(max(0, stderr_path.stat().st_size - 4000))
                tail = handle.read().decode("utf-8", errors="replace")
            raise ODesignExecutionError(f"{label} exited with code {code}; stderr tail:\n{tail}")
        record["status"] = "completed"
    except BaseException as error:
        # Kill the entire local process group so timed-out GPU workers cannot linger.
        stop_worker()
        record.update(status="failed", error_type=type(error).__name__, message=str(error))
        if isinstance(error, subprocess.TimeoutExpired):
            record["status"] = "timed_out"
            raise ODesignExecutionError(
                f"{label} exceeded {timeout_seconds}s; logs retained at {run_dir}"
            ) from error
        if isinstance(error, OSError):
            raise ODesignExecutionError(f"failed to launch {label}: {error}") from error
        raise
    finally:
        try:
            # A signal during launch or error cleanup is deferred to this point.
            # Recheck after writing so cancellation during persistence is retained.
            # termination changes only once, so this takes at most two writes.
            while True:
                recorded_termination = termination
                if recorded_termination is not None:
                    stop_worker()
                    record.update(
                        status="failed",
                        error_type=type(recorded_termination).__name__,
                        message=str(recorded_termination),
                        signal=int(signal.SIGTERM),
                    )
                record["elapsed_seconds"] = time.monotonic() - started
                write_json(run_dir / "execution.json", record)
                if termination is recorded_termination:
                    break
            if termination is not None:
                raise termination
        finally:
            if owns_sigterm:
                signal.signal(signal.SIGTERM, signal.SIG_DFL)
    return stdout_path, stderr_path
