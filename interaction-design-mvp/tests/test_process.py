from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import textwrap
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from interaction_design.runtime import process as process_module
from interaction_design.runtime.base import ODesignExecutionError
from interaction_design.runtime.process import run_logged


def test_timeout_retains_live_logs_and_execution_record(tmp_path):
    command = [sys.executable, "-u", "-c", "import time; print('started'); time.sleep(30)"]
    previous_handler = signal.getsignal(signal.SIGTERM)
    with pytest.raises(ODesignExecutionError, match="logs retained"):
        run_logged(command, tmp_path, metadata={"test": True}, timeout_seconds=0.5)
    assert (tmp_path / "odesign.stdout.log").read_text().strip() == "started"
    record = json.loads((tmp_path / "execution.json").read_text())
    assert record["status"] == "timed_out"
    assert record["command"] == command
    assert record["returncode"] < 0
    assert record["elapsed_seconds"] >= 0.5
    assert signal.getsignal(signal.SIGTERM) == previous_handler


def test_nonzero_exit_retains_command_and_stderr(tmp_path):
    command = [sys.executable, "-c", "import sys; sys.stderr.write('bad input'); sys.exit(7)"]
    previous_handler = signal.getsignal(signal.SIGTERM)
    with pytest.raises(ODesignExecutionError, match="bad input"):
        run_logged(command, tmp_path, metadata={})
    record = json.loads((tmp_path / "execution.json").read_text())
    assert record["status"] == "failed"
    assert record["returncode"] == 7
    assert signal.getsignal(signal.SIGTERM) == previous_handler


def test_success_restores_default_sigterm_handler(tmp_path, monkeypatch):
    previous_handler = signal.getsignal(signal.SIGTERM)
    real_signal = signal.signal
    handlers = []

    def track_handler(signum, handler):
        handlers.append(handler)
        return real_signal(signum, handler)

    real_signal(signal.SIGTERM, signal.SIG_DFL)
    monkeypatch.setattr(signal, "signal", track_handler)
    try:
        run_logged([sys.executable, "-c", "pass"], tmp_path, metadata={})
        assert len(handlers) == 2
        assert callable(handlers[0])
        assert handlers[1] == signal.SIG_DFL
        assert signal.getsignal(signal.SIGTERM) == signal.SIG_DFL
        assert json.loads((tmp_path / "execution.json").read_text())["status"] == "completed"
    finally:
        real_signal(signal.SIGTERM, previous_handler)


@pytest.mark.parametrize("ignored", [False, True])
def test_existing_sigterm_policy_is_preserved(tmp_path, monkeypatch, ignored):
    previous_handler = signal.getsignal(signal.SIGTERM)
    real_signal = signal.signal
    real_popen = subprocess.Popen
    received = []
    handler = signal.SIG_IGN if ignored else lambda signum, frame: received.append(signum)

    def start_worker(*args, **kwargs):
        signal.raise_signal(signal.SIGTERM)
        return real_popen(*args, **kwargs)

    def unexpected_registration(*args):
        pytest.fail("run_logged must preserve an existing SIGTERM policy")

    real_signal(signal.SIGTERM, handler)
    monkeypatch.setattr(signal, "signal", unexpected_registration)
    monkeypatch.setattr(subprocess, "Popen", start_worker)
    try:
        run_logged([sys.executable, "-c", "pass"], tmp_path, metadata={})
        assert signal.getsignal(signal.SIGTERM) == handler
        assert received == ([] if ignored else [signal.SIGTERM])
    finally:
        real_signal(signal.SIGTERM, previous_handler)


def test_non_main_thread_does_not_register_signal_handler(tmp_path, monkeypatch):
    def unexpected_registration(*args):
        pytest.fail("signal handlers must not be registered outside the main thread")

    monkeypatch.setattr(signal, "signal", unexpected_registration)
    with ThreadPoolExecutor(max_workers=1) as executor:
        result = executor.submit(
            run_logged, [sys.executable, "-c", "pass"], tmp_path, metadata={}
        ).result(timeout=5)
    assert all(path.is_file() for path in result)


def test_first_sigterm_during_timeout_cleanup_is_retained(tmp_path, monkeypatch):
    previous_handler = signal.getsignal(signal.SIGTERM)
    real_killpg = os.killpg

    def terminate_during_cleanup(pid, signum):
        handler = signal.getsignal(signal.SIGTERM)
        handler(signal.SIGTERM, None)
        handler(signal.SIGTERM, None)
        return real_killpg(pid, signum)

    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    monkeypatch.setattr(os, "killpg", terminate_during_cleanup)
    try:
        with pytest.raises(SystemExit) as error:
            run_logged(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                tmp_path,
                metadata={},
                timeout_seconds=0.05,
            )
        assert error.value.code == 143
        assert signal.getsignal(signal.SIGTERM) == signal.SIG_DFL
        record = json.loads((tmp_path / "execution.json").read_text())
        assert record["status"] == "failed"
        assert record["signal"] == signal.SIGTERM
        assert record["returncode"] == -signal.SIGKILL
    finally:
        signal.signal(signal.SIGTERM, previous_handler)


@pytest.mark.parametrize("phase", ["launch", "persistence"])
def test_sigterm_is_deferred_until_worker_handle_and_record_are_available(
    tmp_path, monkeypatch, phase
):
    previous_handler = signal.getsignal(signal.SIGTERM)
    real_popen = subprocess.Popen
    real_write_json = process_module.write_json
    real_signal = signal.signal
    children = []
    sent = False

    def request_termination():
        # Invoke the installed handler synchronously so this unit test does not
        # send an operating-system termination signal to the pytest process.
        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)

    def start_worker(*args, **kwargs):
        child = real_popen(*args, **kwargs)
        children.append(child)
        if phase == "launch":
            request_termination()
        return child

    def write_record(path, record):
        nonlocal sent
        result = real_write_json(path, record)
        if phase == "persistence" and record["status"] == "completed" and not sent:
            sent = True
            request_termination()
        return result

    real_signal(signal.SIGTERM, signal.SIG_DFL)
    monkeypatch.setattr(subprocess, "Popen", start_worker)
    monkeypatch.setattr(process_module, "write_json", write_record)
    try:
        script = "import time; time.sleep(30)" if phase == "launch" else "pass"
        with pytest.raises(SystemExit) as error:
            run_logged([sys.executable, "-c", script], tmp_path, metadata={})
        assert error.value.code == 128 + signal.SIGTERM
        assert signal.getsignal(signal.SIGTERM) == signal.SIG_DFL
        record = json.loads((tmp_path / "execution.json").read_text())
        assert record["status"] == "failed"
        assert record["signal"] == signal.SIGTERM
        assert record["message"] == "received SIGTERM"
        assert record["elapsed_seconds"] > 0
        assert children[0].poll() is not None
    finally:
        real_signal(signal.SIGTERM, previous_handler)
        for child in children:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=5)


def _wait_until(predicate, *, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    assert predicate(), "timed out waiting for synthetic process state"


def _process_is_running(pid):
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except FileNotFoundError:
        return False
    # Orphaned grandchildren may briefly remain as zombies until init reaps
    # them; zombies cannot execute or retain GPU allocations.
    return stat.rsplit(")", 1)[1].split()[0] != "Z"


@pytest.mark.skipif(sys.platform != "linux", reason="uses Linux /proc to detect live descendants")
def test_sigterm_cleans_worker_tree_and_allows_outer_failure_receipt(tmp_path):
    worker = tmp_path / "worker.py"
    worker.write_text(
        textwrap.dedent("""\
        import json
        import os
        import subprocess
        import sys
        import time
        from pathlib import Path

        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        print("worker started", flush=True)
        Path("workers.tmp").write_text(json.dumps([os.getpid(), child.pid]))
        Path("workers.tmp").replace("workers.json")
        time.sleep(30)
        """)
    )
    parent_script = tmp_path / "parent.py"
    parent_script.write_text(
        textwrap.dedent("""\
        import json
        import os
        import signal
        import sys
        import time
        from pathlib import Path
        from interaction_design.runtime import process

        real_killpg = os.killpg
        def slow_killpg(pid, signum):
            Path("cleaning").touch()
            time.sleep(0.3)
            return real_killpg(pid, signum)
        process.os.killpg = slow_killpg
        try:
            process.run_logged(
                [sys.executable, "worker.py"], Path.cwd(), metadata={"test": "sigterm"}
            )
        except BaseException as error:
            Path("outer-receipt.json").write_text(json.dumps({
                "status": "failed",
                "exit_code": error.code if isinstance(error, SystemExit) else None,
                "handler_restored": signal.getsignal(signal.SIGTERM) == signal.SIG_DFL,
            }))
            raise
        """)
    )
    env = os.environ.copy()
    source = str(Path(process_module.__file__).resolve().parents[2])
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [source, env.get("PYTHONPATH")]))
    parent = subprocess.Popen(
        [sys.executable, str(parent_script)],
        cwd=tmp_path,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    worker_pids = []
    try:
        _wait_until(lambda: (tmp_path / "workers.json").is_file())
        worker_pids = json.loads((tmp_path / "workers.json").read_text())
        assert all(_process_is_running(pid) for pid in worker_pids)
        parent.send_signal(signal.SIGTERM)
        _wait_until(lambda: (tmp_path / "cleaning").is_file())
        parent.send_signal(signal.SIGTERM)
        stdout, stderr = parent.communicate(timeout=5)
        assert parent.returncode == 128 + signal.SIGTERM, (stdout, stderr)
        _wait_until(lambda: not any(_process_is_running(pid) for pid in worker_pids))
        record = json.loads((tmp_path / "execution.json").read_text())
        assert record["status"] == "failed"
        assert record["signal"] == signal.SIGTERM
        assert record["returncode"] == -signal.SIGKILL
        assert record["elapsed_seconds"] > 0
        assert (tmp_path / "odesign.stdout.log").read_text().strip() == "worker started"
        receipt = json.loads((tmp_path / "outer-receipt.json").read_text())
        assert receipt == {"status": "failed", "exit_code": 143, "handler_restored": True}
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.communicate(timeout=5)
        for pid in worker_pids:
            if _process_is_running(pid):
                os.kill(pid, signal.SIGKILL)
