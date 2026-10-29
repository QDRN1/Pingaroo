"""Bounded subprocess helpers.  Every child runs in its own process group so a
timeout or Stop can kill the whole tree."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass


@dataclass
class RunResult:
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool = False
    cancelled: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out and not self.cancelled


class CancelToken:
    """Shared stop flag.  The worker sets it on Stop Testing; every blocking helper
    polls it."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    def reset(self) -> None:
        self._event.clear()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def wait(self, seconds: float) -> bool:
        """Sleep up to `seconds`; return True if cancelled during the wait."""
        return self._event.wait(max(0.0, seconds))


def kill_process_group(proc: subprocess.Popen, grace: float = 3.0) -> None:
    """SIGTERM the child's process group, then SIGKILL if it is still around."""
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except PermissionError:
        proc.terminate()
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return
        time.sleep(0.05)
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        proc.kill()
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass


def run(cmd: list[str], timeout: float, cancel: CancelToken | None = None,
        env: dict | None = None, user: str | None = None) -> RunResult:
    """Run a command with a hard timeout.  Output is captured as text."""
    kwargs: dict = {}
    if user:
        kwargs["user"] = user
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            start_new_session=True, env=env, **kwargs)
    except FileNotFoundError:
        return RunResult(None, "", "", error=f"command not found: {cmd[0]}")
    except (PermissionError, OSError) as exc:
        return RunResult(None, "", "", error=f"cannot start {cmd[0]}: {exc}")

    out_chunks: list[str] = []
    err_chunks: list[str] = []

    def _reader(stream, sink):
        try:
            for line in stream:
                sink.append(line)
        except ValueError:
            pass

    t_out = threading.Thread(target=_reader, args=(proc.stdout, out_chunks), daemon=True)
    t_err = threading.Thread(target=_reader, args=(proc.stderr, err_chunks), daemon=True)
    t_out.start()
    t_err.start()

    deadline = time.monotonic() + timeout
    timed_out = False
    cancelled = False
    while True:
        if proc.poll() is not None:
            break
        if cancel is not None and cancel.cancelled:
            cancelled = True
            kill_process_group(proc)
            break
        if time.monotonic() >= deadline:
            timed_out = True
            kill_process_group(proc)
            break
        time.sleep(0.05)
    t_out.join(timeout=2)
    t_err.join(timeout=2)
    for stream in (proc.stdout, proc.stderr):
        try:
            stream.close()
        except Exception:
            pass
    return RunResult(proc.returncode, "".join(out_chunks), "".join(err_chunks),
                     timed_out=timed_out, cancelled=cancelled)
