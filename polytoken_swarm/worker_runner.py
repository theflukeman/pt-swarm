"""
Worker Process Supervisor for Polytoken Swarm.

Spawns, tracks, wakes, and terminates background worker processes (or in-process
threads) when `swarm(action="add")` or an `@mention` targets a worker.
"""

import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .workspace_manager import WorkspaceManager


def _is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        SYNCHRONIZE = 0x00100000
        handle = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, False, pid
        )
        if not handle:
            return False
        try:
            WAIT_TIMEOUT = 0x00000102
            res = ctypes.windll.kernel32.WaitForSingleObject(handle, 0)
            return res == WAIT_TIMEOUT
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _process_start_marker(pid: int) -> Optional[str]:
    """Return a per-boot process start marker, so recycled PIDs are not mistaken for workers."""
    if not sys.platform.startswith("linux"):
        return None
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    # `comm` can contain spaces and parens, so parse the fields after the last ')'.
    fields = stat.rsplit(")", 1)[-1].split()
    # Overall field 22 is the process start time; the first field after ')'
    # is overall field 3 (state).
    return fields[19] if len(fields) >= 20 else None


class WorkerProcessManager:
    def __init__(
        self,
        swarm_dir: Path,
        workspace_root: Optional[Path] = None,
        worker_cmd_template: Optional[List[str]] = None,
        in_process_spawner: Optional[Callable[[Dict[str, Any]], Any]] = None,
        spawn_grace_seconds: float = 0.25,
    ):
        self.swarm_dir = Path(swarm_dir).resolve()
        self.logs_dir = self.swarm_dir / "logs"
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.pids_dir = self.swarm_dir / "pids"
        self.pids_dir.mkdir(parents=True, exist_ok=True)
        self.workspace_root = (
            Path(workspace_root).resolve() if workspace_root else Path.cwd().resolve()
        )
        self.workspace_manager = WorkspaceManager(
            self.workspace_root, worktrees_dir=self.swarm_dir / "workspaces"
        )
        self.worker_cmd_template = worker_cmd_template or self._resolve_default_cmd()
        self.in_process_spawner = in_process_spawner
        self.spawn_grace_seconds = spawn_grace_seconds
        self._processes: Dict[str, subprocess.Popen] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _resolve_default_cmd() -> Optional[List[str]]:
        env_cmd = os.environ.get("POLYTOKEN_WORKER_CMD")
        if env_cmd:
            tokens = shlex.split(env_cmd, posix=(os.name != "nt"))
            if os.name == "nt":
                tokens = [
                    t[1:-1] if len(t) >= 2 and t[0] == t[-1] and t[0] in ('"', "'") else t
                    for t in tokens
                ]
            return tokens
        polytoken_bin = os.environ.get("POLYTOKEN_BIN") or shutil.which("polytoken")
        if polytoken_bin:
            # A bare `polytoken` starts the TUI, which needs a TTY and exits
            # immediately when spawned as a worker. `exec` is the headless mode.
            return [polytoken_bin, "exec", "{prompt}"]
        return None

    def _pid_file(self, name: str) -> Path:
        return self.pids_dir / f"{name}.pid"

    def _read_pid(self, name: str) -> Optional[tuple]:
        pf = self._pid_file(name)
        if not pf.exists():
            return None
        try:
            raw = pf.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        try:
            data = json.loads(raw)
        except ValueError:
            data = None
        if isinstance(data, dict):
            try:
                return int(data["pid"]), data.get("start")
            except (KeyError, TypeError, ValueError):
                return None
        try:
            return int(raw), None
        except ValueError:
            return None

    def _write_pid(self, name: str, pid: int) -> None:
        payload = {"pid": pid, "start": _process_start_marker(pid)}
        self._pid_file(name).write_text(json.dumps(payload), encoding="utf-8")

    def is_running(self, name: str) -> bool:
        with self._lock:
            proc = self._processes.get(name)
            if proc is not None:
                if proc.poll() is None:
                    return True
                self._processes.pop(name, None)
            entry = self._read_pid(name)
            if entry is not None:
                pid, recorded_start = entry
                if _is_pid_alive(pid):
                    current_start = _process_start_marker(pid)
                    if not recorded_start or not current_start or recorded_start == current_start:
                        return True
                self._pid_file(name).unlink(missing_ok=True)
            return False

    def spawn_or_wake(
        self,
        name: str,
        prompt_text: str,
        brief_seq: int = 0,
        model: Optional[str] = None,
        workspace_mode: str = "inherit",
        role: str = "worker",
    ) -> Dict[str, Any]:
        with self._lock:
            if self.is_running(name):
                return {"spawned": False, "status": "already_running", "name": name}

            work_dir = self.workspace_manager.provision_workspace(name, mode=workspace_mode)
            identity_hint = (
                f'You are `{name}` on the swarm board. Pass caller="{name}" on every '
                f"swarm tool call so your posts, reads, and wake-ups are attributed to you.\n\n"
            )
            body = (
                f"You have just started. Your assignment (recorded on the board as #{brief_seq} for @{name}):\n\n"
                f"{prompt_text}"
                if brief_seq > 0
                else prompt_text
            )
            initial_input = identity_hint + body

            spec = {
                "name": name,
                "role": role,
                "model": model or "default",
                "workspace_mode": workspace_mode,
                "workspace_path": str(work_dir),
                "swarm_dir": str(self.swarm_dir),
                "initial_input": initial_input,
                "brief_seq": brief_seq,
            }

            if self.in_process_spawner is not None:
                self.in_process_spawner(spec)
                return {"spawned": True, "mode": "in_process", "name": name, "workspace": str(work_dir)}

            if not self.worker_cmd_template:
                return {
                    "spawned": False,
                    "mode": "unconfigured",
                    "name": name,
                    "workspace": str(work_dir),
                    "note": "Set POLYTOKEN_WORKER_CMD or POLYTOKEN_BIN to auto-launch CLI worker processes.",
                }

            env = os.environ.copy()
            env["POLYTOKEN_AGENT_NAME"] = name
            env["POLYTOKEN_AGENT_ROLE"] = role
            env["POLYTOKEN_SWARM_DIR"] = str(self.swarm_dir)
            env["POLYTOKEN_INITIAL_PROMPT"] = initial_input
            if model and model != "default":
                env["POLYTOKEN_MODEL"] = model

            cmd = [
                part.format(
                    name=name,
                    model=model or "default",
                    workspace=str(work_dir),
                    swarm_dir=str(self.swarm_dir),
                    prompt=initial_input,
                )
                for part in self.worker_cmd_template
            ]

            log_path = self.logs_dir / f"{name}.log"
            log_fh = open(log_path, "a", encoding="utf-8")
            try:
                proc = subprocess.Popen(
                    cmd,
                    cwd=str(work_dir),
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=log_fh,
                    stderr=subprocess.STDOUT,
                )
                self._processes[name] = proc
                self._write_pid(name, proc.pid)
                if self.spawn_grace_seconds > 0:
                    time.sleep(self.spawn_grace_seconds)
                if proc.poll() is not None:
                    # A worker that exits during startup is a failed spawn; reporting
                    # success here would leave the coordinator waiting on a dead agent.
                    self._processes.pop(name, None)
                    self._pid_file(name).unlink(missing_ok=True)
                    try:
                        log_fh.flush()
                        output = log_path.read_text(encoding="utf-8", errors="replace")[-800:]
                    except OSError:
                        output = ""
                    return {
                        "spawned": False,
                        "mode": "exited",
                        "name": name,
                        "workspace": str(work_dir),
                        "exit_code": proc.returncode,
                        "log": str(log_path),
                        "error": f"worker process exited immediately with code {proc.returncode}",
                        "output": output,
                    }
                return {
                    "spawned": True,
                    "mode": "subprocess",
                    "pid": proc.pid,
                    "name": name,
                    "workspace": str(work_dir),
                    "log": str(log_path),
                }
            except Exception as e:
                return {"spawned": False, "error": str(e), "name": name}
            finally:
                log_fh.close()

    def kill_worker(self, name: str, workspace_mode: str = "inherit"):
        with self._lock:
            proc = self._processes.pop(name, None)
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2.0)
            else:
                pid = self._read_pid(name)
                if pid is not None and _is_pid_alive(pid[0]):
                    try:
                        os.kill(pid[0], signal.SIGTERM)
                    except OSError:
                        pass
            self._pid_file(name).unlink(missing_ok=True)
            self.workspace_manager.cleanup_workspace(name, mode=workspace_mode)

    def kill_all(self):
        with self._lock:
            names = list(self._processes.keys())
            for pf in self.pids_dir.glob("*.pid"):
                if pf.stem not in names:
                    names.append(pf.stem)
        for name in names:
            self.kill_worker(name)
