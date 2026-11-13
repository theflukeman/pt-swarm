"""
Worker Process Supervisor for pt-swarm.

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

from .model_selection import read_session_model, resolve_model
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
            return [polytoken_bin, "exec", "--model", "{model}", "{prompt}"]
        return None

    @staticmethod
    def _expand_cmd(template: List[str], values: Dict[str, str]) -> List[str]:
        """Substitute placeholders, dropping the model flag when none resolved.

        `polytoken exec --model` rejects the roster's `default` sentinel, so a member
        with no resolved model must start without the flag at all. A `{model}` token
        is removed together with the `--model`/`-m` flag directly before it, which
        leaves a bare `polytoken exec <prompt>`.
        """
        cmd: List[str] = []
        for token in template:
            if "{model}" in token and not values.get("model"):
                if cmd and cmd[-1] in ("--model", "-m"):
                    cmd.pop()
                continue
            cmd.append(token.format(**values))
        return cmd

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

            # The roster's model field is a request, not a selector: `default` is the
            # sentinel for "use this role's tier" and `session` means the model this
            # Polytoken session is running. Resolve it once, here, so every spawn path
            # (`add`, an @mention wake, a restart) launches with the same selector.
            desired_model, applied_model = resolve_model(
                model, role, read_session_model(self.swarm_dir)
            )

            spec = {
                "name": name,
                "role": role,
                "model": applied_model,
                "desired_model": desired_model,
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

            # The model travels on the command line: `polytoken` has no POLYTOKEN_MODEL
            # variable, and `exec --model` is the only way to pin a worker's model.
            cmd = self._expand_cmd(
                self.worker_cmd_template,
                {
                    "name": name,
                    "model": applied_model,
                    "workspace": str(work_dir),
                    "swarm_dir": str(self.swarm_dir),
                    "prompt": initial_input,
                },
            )

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
                    hint = ""
                    if "config load failed" in output or "is not set" in output:
                        # The usual cause is a cleared MCP child environment: `polytoken`
                        # cannot load a config that references provider env vars by name.
                        # The names differ per install, so derive them from the config
                        # rather than naming providers here. Imported here so that
                        # `python3 -m pt_swarm.env_vars` is not pre-imported by the package.
                        try:
                            from .env_vars import referenced_env_names

                            missing = referenced_env_names()
                        except Exception:
                            missing = []
                        hint = (
                            "The worker's `polytoken` could not load its config: the env vars "
                            "it references are missing. Declare them under this server's "
                            "`pass_env:`"
                            + (f" ({', '.join(missing)})" if missing else "")
                            + "; workers inherit the child's environment. "
                            "`python3 -m pt_swarm.env_vars` prints that block for this machine."
                        )
                    return {
                        "spawned": False,
                        "mode": "exited",
                        "name": name,
                        "workspace": str(work_dir),
                        "exit_code": proc.returncode,
                        "log": str(log_path),
                        "error": f"worker process exited immediately with code {proc.returncode}",
                        "output": output,
                        **({"hint": hint} if hint else {}),
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
