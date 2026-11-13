"""
Multi-process and thread-safe, file-backed BoardManager for the Swarm architecture.
Implements exact parity with Antigravity's native board package and channels.mjs.
"""

import contextlib
import json
import os
import re
import sys
import tempfile
import threading
import time
import uuid
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

from .model_selection import read_session_model, resolve_model

MAIN_CHANNEL = "main"
ALL_CHANNELS = "*"
RESERVED_CHANNELS = {"main", "all", "here"}
CHANNEL_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,23}$")
NAME_RUNE_RE = re.compile(r"[_-]")


def _is_name_rune(char: str) -> bool:
    return bool(NAME_RUNE_RE.fullmatch(char)) or unicodedata.category(char) in ("Lu", "Ll", "Lt", "Lm", "Lo", "Nd")
SHARD_SUFFIX_RE = re.compile(r"^(.+)-\d+$")
ALL_BY_ROLE = {"coordinator", "narrator", "monitor"}
ROLE_WORKER_NAME_RE = re.compile(r"^(?:narrator|monitor)(?:-\d+)?$")


class _CrossProcessLock:
    """ Re-entrant thread lock combined with an OS-level cross-process file lock. """

    def __init__(self, lock_file: Path):
        self.lock_file = Path(lock_file)
        self._tlock = threading.RLock()
        self._depth = 0
        self._fh = None

    def __enter__(self):
        self._tlock.acquire()
        try:
            if self._depth == 0:
                self.lock_file.parent.mkdir(parents=True, exist_ok=True)
                self._fh = open(self.lock_file, "a+b")
                try:
                    self._acquire_os_lock(self._fh)
                except BaseException:
                    try:
                        self._fh.close()
                    finally:
                        self._fh = None
                    raise
            self._depth += 1
            return self
        except BaseException:
            self._tlock.release()
            raise

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            self._depth -= 1
            if self._depth == 0 and self._fh is not None:
                fh, self._fh = self._fh, None
                try:
                    self._release_os_lock(fh)
                finally:
                    fh.close()
        finally:
            self._tlock.release()

    @staticmethod
    def _acquire_os_lock(fh, timeout: float = 30.0):
        if sys.platform == "win32":
            import errno
            fh.seek(0)
            if fh.tell() == 0 and os.fstat(fh.fileno()).st_size == 0:
                fh.write(b"0")
                fh.flush()
            fh.seek(0)
            deadline = time.monotonic() + timeout
            while True:
                try:
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as e:
                    if e.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                        raise
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"Timed out waiting for board lock after {timeout}s") from e
                    time.sleep(0.01)
        else:
            deadline = time.monotonic() + timeout
            while True:
                try:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return
                except BlockingIOError as exc:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"Timed out waiting for board lock after {timeout}s") from exc
                    time.sleep(0.01)

    @staticmethod
    def _release_os_lock(fh):
        if sys.platform == "win32":
            try:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        else:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass


class BoardManager:
    def __init__(
        self,
        swarm_dir: Path,
        on_member_mentioned: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ):
        self.swarm_dir = Path(swarm_dir)
        self.swarm_dir.mkdir(parents=True, exist_ok=True)
        self.board_file = self.swarm_dir / "board.jsonl"
        self.members_file = self.swarm_dir / "members.json"
        self.sequence_file = self.swarm_dir / ".board.seq"
        self.lock = _CrossProcessLock(self.swarm_dir / ".board.lock")
        self._new_message_cond = threading.Condition()
        self._message_epoch = 0
        self.on_member_mentioned = on_member_mentioned
        self._init_files()
        self.sync_session_models()

    def _init_files(self):
        with self.lock:
            if not self.members_file.exists():
                initial_members = {
                    "title": "Default Swarm",
                    "members": {
                        "coordinator": {
                            "conversation_id": "coordinator",
                            "role": "coordinator",
                            "desired_state": "RUNNING",
                            # The coordinator is the Polytoken session itself, so it asks
                            # for `session` and applies to the model this conversation is
                            # running, as recorded by the model hook.
                            "desired_model": "session",
                            "applied_model": resolve_model(
                                "session", "coordinator", read_session_model(self.swarm_dir)
                            )[1],
                            "workspace_mode": "inherit",
                            "phase": "RUNNING",
                            "wake_mode": "working",
                            "wait_since": 0,
                            "last_read_seq": 0,
                            "last_nudged_seq": 0,
                            "last_park_blocked_seq": 0,
                            "killed": False,
                            "model": "session",
                        }
                    },
                }
                self._atomic_write_json(self.members_file, initial_members)
            if not self.board_file.exists():
                self.board_file.touch()
            if not self.sequence_file.exists():
                messages = self.read_raw_messages()
                high_water = max((int(m.get("seq", 0)) for m in messages), default=0)
                self._write_sequence_high_water(high_water)

    def sync_session_models(self) -> None:
        """Re-resolve `session` requests against the model this session is running.

        The model hook writes the session model into the swarm dir, but nothing calls
        back into the server when it changes, so the roster is reconciled whenever the
        roster is touched on the session's behalf (`status`, `add`, `model`) and once at
        startup. That also repairs boards whose coordinator entry was created before it
        carried a real selector.
        """
        with self.lock:
            data = self.read_members()
            session_model = read_session_model(self.swarm_dir)
            changed = False
            for member in data.get("members", {}).values():
                if not isinstance(member, dict):
                    continue
                role = member.get("role", "worker")
                request = member.get("model") or "default"
                legacy_coordinator = (
                    role == "coordinator" and member.get("applied_model") == "default"
                )
                if request != "session" and not legacy_coordinator:
                    continue
                if legacy_coordinator and request == "default":
                    member["model"] = member["desired_model"] = "session"
                applied_model = resolve_model("session", role, session_model)[1]
                if member.get("applied_model") != applied_model:
                    member["applied_model"] = applied_model
                    changed = True
            if changed:
                self.write_members(data)

    @staticmethod
    def _atomic_write_json(target: Path, data: Dict[str, Any]):
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, target)
        except Exception:
            if os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
            raise

    def _read_sequence_high_water(self) -> int:
        try:
            value = int(self.sequence_file.read_text(encoding="ascii").strip())
        except FileNotFoundError:
            return max((int(m.get("seq", 0)) for m in self.read_raw_messages()), default=0)
        if value < 0:
            raise ValueError("sequence high-water mark must be non-negative")
        return value

    def _write_sequence_high_water(self, value: int) -> None:
        target = self.sequence_file
        fd, tmp_path = tempfile.mkstemp(dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="ascii") as out:
                out.write(f"{value}\n")
                out.flush()
                os.fsync(out.fileno())
            os.replace(tmp_path, target)
            try:
                dir_fd = os.open(target.parent, os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except OSError:
                pass
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    @staticmethod
    def now_str() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # -------------------------------------------------------------------------
    # Mention & Channel Parsing (Ported 1:1 from channels.mjs / board/mentions.go)
    # -------------------------------------------------------------------------

    @staticmethod
    def _named_token(lower_text: str, lower_name: str) -> bool:
        if not lower_name:
            return False
        idx = lower_text.find("@")
        while idx >= 0:
            if idx > 0 and _is_name_rune(lower_text[idx - 1]):
                idx = lower_text.find("@", idx + 1)
                continue
            if lower_text.startswith(lower_name, idx + 1):
                end_idx = idx + 1 + len(lower_name)
                if end_idx >= len(lower_text) or not _is_name_rune(lower_text[end_idx]):
                    return True
            idx = lower_text.find("@", idx + 1)
        return False

    @classmethod
    def _named(cls, lower_text: str, lower_name: str) -> bool:
        if cls._named_token(lower_text, lower_name):
            return True
        m = SHARD_SUFFIX_RE.match(lower_name)
        return cls._named_token(lower_text, m.group(1)) if m else False

    @classmethod
    def channel_mentions(cls, text: str) -> List[str]:
        lower = (text or "").lower()
        out: List[str] = []
        idx = lower.find("@#")
        while idx >= 0:
            if idx > 0 and _is_name_rune(lower[idx - 1]):
                idx = lower.find("@#", idx + 1)
                continue
            j = idx + 2
            while j < len(lower) and _is_name_rune(lower[j]):
                j += 1
            ch_name = lower[idx + 2 : j]
            if (
                ch_name == MAIN_CHANNEL
                or (CHANNEL_NAME_RE.match(ch_name) and ch_name not in RESERVED_CHANNELS)
            ) and ch_name not in out:
                out.append(ch_name)
            idx = lower.find("@#", idx + 1)
        return out

    @classmethod
    def parse_mentions(cls, text: str, known_names: Iterable[str]) -> Tuple[List[str], bool, List[str]]:
        lower = (text or "").lower()
        mentioned_names = [
            name for name in known_names if cls._named(lower, str(name).lower())
        ]
        is_all = cls._named_token(lower, "all")
        channels = cls.channel_mentions(text)
        return mentioned_names, is_all, channels

    @staticmethod
    def effective_channels(role: str, stored_channels: Optional[List[str]], name: str) -> List[str]:
        if isinstance(stored_channels, list):
            return stored_channels
        if role in ALL_BY_ROLE:
            return [ALL_CHANNELS]
        if (not role or role == "worker") and ROLE_WORKER_NAME_RE.match(name or ""):
            return [ALL_CHANNELS]
        return []

    def channel_registry(self, messages: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
        msgs = messages if messages is not None else self.read_raw_messages()
        by_name: Dict[str, Dict[str, Any]] = {}
        for m in msgs:
            c = m.get("control")
            if not isinstance(c, dict):
                continue
            ch = c.get("channel")
            if not isinstance(ch, str) or not ch:
                continue
            op = c.get("op")
            if op == "create" and (ch not in by_name or by_name[ch].get("archived")):
                by_name[ch] = {
                    "name": ch,
                    "topic": str(c.get("topic") or ""),
                    "created_by": str(m.get("agent") or ""),
                    "archived": False,
                }
            elif op == "archive":
                entry = by_name.get(
                    ch, {"name": ch, "topic": "", "created_by": "", "archived": False}
                )
                entry["archived"] = True
                by_name[ch] = entry
        return list(by_name.values())

    # -------------------------------------------------------------------------
    # Message Posting & Reading
    # -------------------------------------------------------------------------

    def post_message(
        self,
        agent: str,
        text: str,
        channel: str = MAIN_CHANNEL,
        kind: Optional[str] = None,
        control: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        mentioned_targets: List[str] = []
        with self.lock:
            # Validate the roster before any board or sequence state is mutated.
            if not self.members_file.exists():
                raise FileNotFoundError(f"Member roster not found: {self.members_file}")
            members_data = self.read_members()
            members_map = members_data.get("members", {})
            messages = self.read_raw_messages()
            high_water = max(self._read_sequence_high_water(), max((int(m.get("seq", 0)) for m in messages), default=0))
            next_seq = high_water + 1

            # Preserve a complete final JSON record even when it lacks its line
            # terminator; discard only an invalid/torn record before appending.
            with open(self.board_file, "rb+") as board:
                raw = board.read()
                if raw and not raw.endswith(b"\n"):
                    tail_start = raw.rfind(b"\n") + 1
                    try:
                        json.loads(raw[tail_start:].decode("utf-8"))
                    except (json.JSONDecodeError, UnicodeError):
                        board.seek(tail_start)
                        board.truncate()
                    else:
                        board.seek(0, os.SEEK_END)
                        board.write(b"\n")
                        board.flush()
                        os.fsync(board.fileno())

            # Reserve before append/ack so a torn post cannot cause sequence reuse.
            self._write_sequence_high_water(next_seq)
            entry: Dict[str, Any] = {
                "seq": next_seq,
                "agent": agent,
                "text": text,
                "time": self.now_str(),
                "request_id": str(uuid.uuid4()),
            }
            if kind:
                entry["kind"] = kind
            if channel and channel != MAIN_CHANNEL:
                entry["channel"] = channel
            if control:
                entry["control"] = control

            with open(self.board_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
                f.flush()

            members_data = self.read_members()
            members_map = members_data.get("members", {})
            if agent in members_map:
                m = members_map[agent]
                m["last_nudged_seq"] = next_seq
                m["last_post_seq"] = next_seq
                m["last_post_time"] = entry["time"]

            # Identify mentioned targets to nudge/wake
            if not control:
                mentioned, is_all, ch_mentions = self.parse_mentions(text, members_map.keys())
                active_channels = {
                    c["name"] for c in self.channel_registry(messages + [entry]) if not c.get("archived")
                }
                for m_name, m_info in members_map.items():
                    if m_name == agent or m_info.get("killed"):
                        continue
                    eff = self.effective_channels(
                        m_info.get("role", "worker"), m_info.get("channels"), m_name
                    )
                    ch_called = any(
                        ch != MAIN_CHANNEL
                        and ch in active_channels
                        and (ALL_CHANNELS in eff or ch in eff)
                        for ch in ch_mentions
                    )
                    if is_all or m_name in mentioned or ch_called:
                        m_info["last_nudged_seq"] = next_seq
                        mentioned_targets.append(m_name)

            self.write_members(members_data)
            with self._new_message_cond:
                self._message_epoch += 1
                self._new_message_cond.notify_all()

        if self.on_member_mentioned and mentioned_targets:
            for target_name in mentioned_targets:
                try:
                    self.on_member_mentioned(target_name, entry)
                except Exception as exc:
                    print(f"BoardManager mention callback failed for {target_name}: {exc}", file=sys.stderr)

        return entry

    def read_raw_messages(self) -> List[Dict[str, Any]]:
        with self.lock:
            if not self.board_file.exists():
                return []
            msgs = []
            try:
                with open(self.board_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                msgs.append(json.loads(line))
                            except json.JSONDecodeError:
                                continue
            except (OSError, UnicodeError) as exc:
                print(f"Unable to read board file {self.board_file}: {exc}", file=sys.stderr)
                raise
            return msgs

    def _channel_membership_by_seq(
        self, reader: str, member_info: Dict[str, Any], messages: List[Dict[str, Any]],
    ) -> Dict[int, Set[str]]:
        """Replay membership events once and index access at each message sequence."""
        events = [
            message for message in messages
            if isinstance(message.get("control"), dict)
            and message["control"].get("op") in ("subscribe", "unsubscribe")
            and reader in message["control"].get("members", [])
        ]
        state = self.effective_channels(
            member_info.get("role", "worker"), member_info.get("channels"), reader
        )
        if events:
            prior = events[0]["control"].get("prev", {}).get(reader)
            if isinstance(prior, list):
                state = prior
        result: Dict[int, Set[str]] = {}
        event_index = 0
        for message in messages:
            seq = message.get("seq", 0)
            while event_index < len(events) and events[event_index].get("seq", 0) < seq:
                next_state = events[event_index]["control"].get("next", {}).get(reader)
                if isinstance(next_state, list):
                    state = next_state
                event_index += 1
            result[seq] = set(state)
        return result

    def _member_sees(
        self,
        reader: str,
        member_info: Dict[str, Any],
        msg: Dict[str, Any],
        active_channels: Set[str],
        messages: List[Dict[str, Any]],
        channel_memberships: Optional[Dict[int, Set[str]]] = None,
    ) -> bool:
        if reader == "coordinator":
            return True
        msg_ch = msg.get("channel") or MAIN_CHANNEL
        if msg_ch == MAIN_CHANNEL or msg.get("agent") == reader:
            return True
        if channel_memberships is None:
            channel_memberships = self._channel_membership_by_seq(reader, member_info, messages)
        eff = channel_memberships.get(msg.get("seq", 0), set())
        if ALL_CHANNELS in eff or msg_ch in eff:
            return True
        text = msg.get("text", "")
        mentioned, is_all, ch_mentions = self.parse_mentions(text, [reader])
        if is_all or reader in mentioned:
            return True
        return any(
            c != MAIN_CHANNEL and c in active_channels and (ALL_CHANNELS in eff or c in eff)
            for c in ch_mentions
        )

    def read_messages(
        self,
        since: int = 0,
        limit: int = 100,
        channel: Optional[str] = None,
        reader: Optional[str] = None,
        seqs: Optional[List[int]] = None,
    ) -> Dict[str, Any]:
        with self.lock:
            all_msgs = self.read_raw_messages()
            head_seq = all_msgs[-1]["seq"] if all_msgs else 0
            members_data = self.read_members()
            members_map = members_data.get("members", {})
            member_info = members_map.get(reader, {}) if reader else {}
            active_chs = {
                c["name"] for c in self.channel_registry(all_msgs) if not c.get("archived")
            }
            channel_memberships = (
                self._channel_membership_by_seq(reader, member_info, all_msgs) if reader else None
            )

            if seqs is not None:
                try:
                    seq_set = {int(s) for s in seqs}
                except (TypeError, ValueError) as exc:
                    raise ValueError("seqs must contain integer sequence IDs") from exc
                selected = [
                    m for m in all_msgs
                    if m.get("seq") in seq_set
                    and (not reader or self._member_sees(reader, member_info, m, active_chs, all_msgs, channel_memberships))
                ]
                return {"messages": selected, "head": head_seq}

            filtered = [m for m in all_msgs if m.get("seq", 0) > since]

            if channel and channel != ALL_CHANNELS:
                filtered = [
                    m for m in filtered
                    if (m.get("channel") or MAIN_CHANNEL) == channel
                    and (
                        not reader
                        or self._member_sees(
                            reader, member_info, m, active_chs, all_msgs, channel_memberships
                        )
                    )
                ]
            elif reader:
                filtered = [
                    m
                    for m in filtered
                    if self._member_sees(reader, member_info, m, active_chs, all_msgs, channel_memberships)
                ]

            returned = filtered[:limit]

            # Advance last_read_seq accurately based on what was actually returned
            if reader and reader in members_map:
                prev_read = members_map[reader].get("last_read_seq", 0)
                if channel and channel != ALL_CHANNELS:
                    # A channel view is partial and cannot establish a global read cursor.
                    new_read = prev_read
                elif len(filtered) <= limit:
                    # Reader caught up to head on their visible stream
                    new_read = max(prev_read, head_seq)
                elif returned:
                    new_read = max(prev_read, returned[-1]["seq"])
                else:
                    new_read = prev_read
                if new_read != prev_read:
                    members_map[reader]["last_read_seq"] = new_read
                    self.write_members(members_data)

            return {
                "messages": returned,
                "head": head_seq,
            }

    # -------------------------------------------------------------------------
    # Roster & Channel Lifecycle Management
    # -------------------------------------------------------------------------

    def read_members(self) -> Dict[str, Any]:
        with self.lock:
            if not self.members_file.exists():
                return {"members": {}}
            return json.loads(self.members_file.read_text(encoding="utf-8"))

    def write_members(self, data: Dict[str, Any]):
        with self.lock:
            self._atomic_write_json(self.members_file, data)

    def add_member(
        self,
        name: str,
        role: str = "worker",
        model: Optional[str] = None,
        workspace_mode: str = "inherit",
        conversation_id: Optional[str] = None,
        channels: Optional[List[str]] = None,
        fork: Optional[str] = None,
        phase: str = "delegated",
    ) -> Dict[str, Any]:
        with self.lock:
            data = self.read_members()
            if "members" not in data:
                data["members"] = {}
            if name in data["members"]:
                existing = data["members"][name]
                if existing.get("killed"):
                    raise ValueError(f"Member name '{name}' was killed and stays reserved")
                raise ValueError(f"Member '{name}' already exists on the board")
            all_msgs = self.read_raw_messages()
            head_seq = all_msgs[-1]["seq"] if all_msgs else 0

            if role == "worker" and ROLE_WORKER_NAME_RE.match(name):
                role = "narrator" if name.startswith("narrator") else "monitor"

            desired_model, applied_model = resolve_model(
                model, role, read_session_model(self.swarm_dir)
            )
            member_entry: Dict[str, Any] = {
                "conversation_id": conversation_id or str(uuid.uuid4()),
                "role": role,
                "desired_state": "RUNNING",
                "desired_model": desired_model,
                "applied_model": applied_model,
                "workspace_mode": workspace_mode,
                "phase": phase,
                "wake_mode": "mentions",
                "wait_since": head_seq,
                "last_read_seq": head_seq,
                "last_nudged_seq": head_seq,
                "last_park_blocked_seq": 0,
                "killed": False,
                "model": desired_model,
            }
            if channels is not None:
                member_entry["channels"] = list(channels)
            elif role in ALL_BY_ROLE:
                member_entry["channels"] = [ALL_CHANNELS]
            if fork:
                member_entry["fork"] = fork

            data["members"][name] = member_entry
            self.write_members(data)
            return member_entry

    def bind_member(self, name: str, job_id: str, state: str) -> bool:
        """Record the native harness job and its latest lifecycle state."""
        with self.lock:
            data = self.read_members()
            member = data.get("members", {}).get(name)
            if member is None:
                return False
            member["job_id"] = job_id
            member["state"] = state
            member["phase"] = state
            self.write_members(data)
            return True

    def set_title(self, title: str):
        with self.lock:
            data = self.read_members()
            data["title"] = title
            self.write_members(data)

    def set_model(self, name: str, new_model: str) -> bool:
        with self.lock:
            data = self.read_members()
            member = data.get("members", {}).get(name)
            if member is None:
                return False
            desired_model, applied_model = resolve_model(
                new_model, member.get("role", "worker"), read_session_model(self.swarm_dir)
            )
            member["model"] = desired_model
            member["desired_model"] = desired_model
            member["applied_model"] = applied_model
            self.write_members(data)
            return True

    def set_member_wake_mode(
        self, name: str, wake_mode: str, wait_since: Optional[int] = None
    ):
        with self.lock:
            data = self.read_members()
            if name in data.get("members", {}):
                m = data["members"][name]
                m["wake_mode"] = wake_mode
                if wait_since is not None:
                    m["wait_since"] = wait_since
                self.write_members(data)

    def kill_member(self, name: str) -> List[str]:
        """
        Kills a member or all numbered shards (<name>-1..<name>-k) if <name> is a group prefix.
        Returns list of killed member names.
        """
        killed_names: List[str] = []
        with self.lock:
            data = self.read_members()
            members = data.get("members", {})
            targets = []
            if name in members:
                targets.append(name)
            else:
                prefix = f"{name}-"
                for m_name in members:
                    if m_name.startswith(prefix) and m_name[len(prefix) :].isdigit():
                        targets.append(m_name)

            for t in targets:
                if not members[t].get("killed"):
                    members[t]["killed"] = True
                    members[t]["desired_state"] = "KILLED"
                    members[t]["phase"] = "KILLED"
                    killed_names.append(t)

            if killed_names:
                self.write_members(data)
        return killed_names

    def manage_channel(
        self,
        caller: str,
        op: str,
        channel: Optional[str] = None,
        topic: str = "",
        target_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        with self.lock:
            msgs = self.read_raw_messages()
            registry = self.channel_registry(msgs)
            reg_map = {c["name"]: c for c in registry}

            if op == "list":
                return {"status": "ok", "channels": registry}

            if not channel or not CHANNEL_NAME_RE.match(channel) or channel in RESERVED_CHANNELS:
                return {"error": f"Invalid or reserved channel name: '{channel}'"}

            if op == "create":
                if channel in reg_map and not reg_map[channel].get("archived"):
                    return {"status": "exists", "channel": channel, "topic": reg_map[channel]["topic"]}
                ctrl = {"op": "create", "channel": channel, "topic": topic}
                self.post_message(
                    agent=caller,
                    text=f"[channel] created #{channel}" + (f": {topic}" if topic else ""),
                    channel=MAIN_CHANNEL,
                    control=ctrl,
                )
                return {"status": "created", "channel": channel, "topic": topic}

            if op == "archive":
                if channel not in reg_map:
                    return {"error": f"Channel '{channel}' does not exist"}
                ctrl = {"op": "archive", "channel": channel}
                self.post_message(
                    agent=caller,
                    text=f"[channel] archived #{channel}",
                    channel=MAIN_CHANNEL,
                    control=ctrl,
                )
                return {"status": "archived", "channel": channel}

            if op in ("subscribe", "unsubscribe"):
                member_name = target_name or caller
                members_data = self.read_members()
                members_map = members_data.get("members", {})
                if member_name not in members_map:
                    return {"error": f"Member '{member_name}' not found"}

                m_info = members_map[member_name]
                prev_channels = list(m_info.get("channels") or self.effective_channels(
                    m_info.get("role", "worker"), m_info.get("channels"), member_name
                ))
                next_channels = list(prev_channels)

                if op == "subscribe":
                    if channel not in next_channels:
                        next_channels.append(channel)
                else:
                    next_channels = [c for c in next_channels if c != channel]

                m_info["channels"] = next_channels
                self.write_members(members_data)

                ctrl = {
                    "op": op,
                    "channel": channel,
                    "members": [member_name],
                    "prev": {member_name: prev_channels},
                    "next": {member_name: next_channels},
                }
                self.post_message(
                    agent=caller,
                    text=f"[channel] {member_name} {op}d #{channel}",
                    channel=MAIN_CHANNEL,
                    control=ctrl,
                )
                return {"status": "ok", "op": op, "channel": channel, "name": member_name, "channels": next_channels}

            return {"error": f"Unknown channel operation '{op}'"}

    # -------------------------------------------------------------------------
    # Event Waiting (swarm_wait)
    # -------------------------------------------------------------------------

    def wait_for_event(
        self,
        reader_name: str,
        since_seq: int,
        mentions_only: bool = True,
        timeout: float = 60.0,
    ) -> Dict[str, Any]:
        """
        Blocks until a relevant message arrives after `since_seq` or timeout expires.
        Works across threads (via threading.Event) and across processes (via mtime/seq polling).
        """
        deadline = time.time() + timeout
        self.set_member_wake_mode(
            reader_name, "mentions" if mentions_only else "anything", wait_since=since_seq
        )

        try:
            while True:
                with self._new_message_cond:
                    observed_epoch = self._message_epoch
                with self.lock:
                    msgs = self.read_raw_messages()
                    head_seq = msgs[-1]["seq"] if msgs else 0
                    new_msgs = [
                        m for m in msgs if m.get("seq", 0) > since_seq and not m.get("control")
                    ]
                    members_dict = self.read_members().get("members", {})
                    member_info = members_dict.get(reader_name, {})
                    known_names = set(members_dict.keys())
                    active_chs = {
                        c["name"] for c in self.channel_registry(msgs) if not c.get("archived")
                    }
                    channel_memberships = self._channel_membership_by_seq(
                        reader_name, member_info, msgs
                    )

                    matching: List[Dict[str, Any]] = []
                    for m in new_msgs:
                        if m.get("agent") == reader_name:
                            continue
                        if not mentions_only:
                            if self._member_sees(reader_name, member_info, m, active_chs, msgs, channel_memberships):
                                matching.append(m)
                        else:
                            text = m.get("text", "")
                            mentioned, is_all, chs = self.parse_mentions(text, known_names)
                            eff = channel_memberships.get(m.get("seq", 0), set())
                            ch_hit = any(
                                c != MAIN_CHANNEL
                                and c in active_chs
                                and (ALL_CHANNELS in eff or c in eff)
                                for c in chs
                            )
                            if reader_name in mentioned or is_all or ch_hit:
                                matching.append(m)

                    if matching:
                        return {
                            "woken": True,
                            "head": head_seq,
                            "since": since_seq,
                            "matching_seqs": [m["seq"] for m in matching],
                        }

                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                with self._new_message_cond:
                    if self._message_epoch == observed_epoch:
                        self._new_message_cond.wait(timeout=min(remaining, 0.25))
        finally:
            self.set_member_wake_mode(reader_name, "working")

        with self.lock:
            msgs = self.read_raw_messages()
            head_seq = msgs[-1]["seq"] if msgs else 0
        return {
            "woken": False,
            "head": head_seq,
            "since": since_seq,
            "matching_seqs": [],
        }
