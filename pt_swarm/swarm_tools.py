"""
Swarm Tool Call Handlers for Polytoken.
Matches the exact return shapes and full feature set of Antigravity native swarm.
"""

import json
import os
import shutil
import subprocess
from typing import Any, Callable, Dict, List, Optional

from .board_manager import BoardManager, MAIN_CHANNEL


def _list_polytoken_models() -> List[Dict[str, Any]]:
    """Ask the Polytoken CLI which models this install can actually run."""
    exe = os.environ.get("POLYTOKEN_BIN") or shutil.which("polytoken")
    if not exe:
        return []
    try:
        proc = subprocess.run(
            [exe, "models", "--format", "json"],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []
    try:
        data = json.loads(proc.stdout)
    except (TypeError, ValueError):
        return []
    models: List[Dict[str, Any]] = []
    for entry in data.get("models", []) if isinstance(data, dict) else []:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not name:
            continue
        models.append(
            {
                "name": name,
                "description": entry.get("provider_name") or entry.get("provider") or "",
                "selectable": [s for s in entry.get("selectable", []) if isinstance(s, str)],
            }
        )
    return models


class SwarmToolHandlers:
    def __init__(
        self,
        board_manager: BoardManager,
        caller_agent_name: str = "coordinator",
        worker_runner: Optional[Any] = None,
        model_lister: Optional[Callable[[], List[Dict[str, Any]]]] = None,
    ):
        self.bm = board_manager
        self.caller = caller_agent_name
        self.worker_runner = worker_runner
        self.model_lister = model_lister

    def _effective_caller(self, kwargs: Dict[str, Any]) -> str:
        return str(kwargs.get("caller") or self.caller or "coordinator")

    def _default_channel_for(self, agent: str) -> str:
        members = self.bm.read_members().get("members", {})
        ch_list = members.get(agent, {}).get("channels")
        if isinstance(ch_list, list) and ch_list and ch_list[0] != "*":
            return ch_list[0]
        return MAIN_CHANNEL

    def handle_swarm(self, **kwargs) -> Dict[str, Any]:
        action = kwargs.get("action")
        caller = self._effective_caller(kwargs)

        if action == "status":
            members_info = self.bm.read_members()
            channels_info = self.bm.channel_registry()
            res: Dict[str, Any] = {
                "status": "ok",
                "title": members_info.get("title", ""),
                "roster": members_info.get("members", {}),
            }
            if channels_info:
                res["channels"] = channels_info
            return res

        elif action == "title":
            title = kwargs.get("title", "")
            self.bm.set_title(title)
            return {"status": "titled", "title": title}

        elif action == "models":
            show_all = bool(kwargs.get("all", False))
            discovered = (
                self.model_lister() if self.model_lister is not None else _list_polytoken_models()
            )
            models: List[Dict[str, Any]] = [
                {"name": "default", "description": "Inherited conversation model"}
            ]
            seen = {"default"}
            for entry in discovered:
                name = entry.get("name")
                if not name or name in seen:
                    continue
                models.append({"name": name, "description": entry.get("description", "")})
                seen.add(name)
            if show_all:
                for entry in discovered:
                    for variant in entry.get("selectable", []):
                        if variant in seen:
                            continue
                        models.append(
                            {"name": variant, "description": f"Variant of {entry['name']}"}
                        )
                        seen.add(variant)
            res: Dict[str, Any] = {"status": "ok", "models": models}
            if not discovered:
                res["note"] = (
                    "Could not read the model list from `polytoken models`; "
                    "use action=model to set a model by name."
                )
            return res

        elif action == "add":
            base_name = kwargs.get("name")
            brief = kwargs.get("brief", "")
            model = kwargs.get("model")
            workspace_mode = kwargs.get("workspace", "inherit")
            channels = kwargs.get("channels")
            fork = kwargs.get("fork")
            tasks: Optional[List[str]] = kwargs.get("tasks")
            raw_n = kwargs.get("n")
            if raw_n is not None:
                n_copies = int(raw_n)
                if n_copies < 1:
                    return {"error": f"Invalid 'n' parameter: {n_copies} (must be >= 1)"}
            else:
                n_copies = 1

            if not base_name:
                return {"error": "Missing 'name' parameter"}

            post_channel = (
                channels[0]
                if isinstance(channels, list) and channels and channels[0] != "*"
                else MAIN_CHANNEL
            )

            # Sharded group via `tasks` or `n > 1`
            if (isinstance(tasks, list) and len(tasks) > 0) or n_copies > 1:
                count = len(tasks) if (isinstance(tasks, list) and len(tasks) > 0) else n_copies
                added_members = []
                brief_seqs = []
                for idx in range(1, count + 1):
                    shard_name = f"{base_name}-{idx}"
                    shard_brief = (
                        f"{brief}\n\nYour assigned slice ({idx}/{count}): {tasks[idx - 1]}"
                        if isinstance(tasks, list) and idx <= len(tasks)
                        else brief
                    )
                    try:
                        m_entry = self.bm.add_member(
                            name=shard_name,
                            model=model,
                            workspace_mode=workspace_mode,
                            channels=channels,
                            fork=fork,
                        )
                    except ValueError as e:
                        return {"error": str(e)}
                    post = self.bm.post_message(
                        agent=caller,
                        text=f"@{shard_name} {shard_brief}",
                        channel=post_channel,
                        kind="brief",
                    )
                    if self.worker_runner is not None:
                        self.worker_runner.spawn_or_wake(
                            name=shard_name,
                            prompt_text=shard_brief,
                            brief_seq=post["seq"],
                            model=model,
                            workspace_mode=workspace_mode,
                            role=m_entry.get("role", "worker"),
                        )
                    added_members.append(shard_name)
                    brief_seqs.append(post["seq"])

                return {
                    "status": "added",
                    "name": base_name,
                    "members": added_members,
                    "model": model or "default",
                    "brief_seqs": brief_seqs,
                    "note": (
                        f"Spawned {len(added_members)} workers ({added_members[0]}..{added_members[-1]}). "
                        f"Briefs posted as #{brief_seqs[0]}..#{brief_seqs[-1]}."
                    ),
                }

            # Single worker add
            try:
                m_entry = self.bm.add_member(
                    name=base_name,
                    model=model,
                    workspace_mode=workspace_mode,
                    channels=channels,
                    fork=fork,
                )
            except ValueError as e:
                return {"error": str(e)}
            post = self.bm.post_message(
                agent=caller,
                text=f"@{base_name} {brief}",
                channel=post_channel,
                kind="brief",
            )
            spawn_info = None
            if self.worker_runner is not None:
                spawn_info = self.worker_runner.spawn_or_wake(
                    name=base_name,
                    prompt_text=brief,
                    brief_seq=post["seq"],
                    model=model,
                    workspace_mode=workspace_mode,
                    role=m_entry.get("role", "worker"),
                )

            res = {
                "status": "added",
                "name": base_name,
                "model": model or "default",
                "conversation_id": m_entry["conversation_id"],
                "note": f"The brief is on the board as #{post['seq']}, where every member of the swarm can read it.",
            }
            if spawn_info:
                res["process"] = spawn_info
            return res

        elif action == "kill":
            name = kwargs.get("name")
            if not name:
                return {"error": "Missing 'name'"}
            with self.bm.lock:
                members_before = self.bm.read_members().get("members", {})
                killed_list = self.bm.kill_member(name)
            if not killed_list:
                return {"error": f"Member or group '{name}' not found or already killed"}

            if self.worker_runner is not None:
                for k_name in killed_list:
                    ws_mode = members_before.get(k_name, {}).get("workspace_mode", "inherit")
                    self.worker_runner.kill_worker(k_name, workspace_mode=ws_mode)

            return {
                "status": "ok",
                "killed": name if len(killed_list) == 1 else killed_list,
            }

        elif action == "model":
            name = kwargs.get("name") or caller
            new_model = kwargs.get("model") or "default"
            if self.bm.set_model(name, new_model):
                return {"status": "ok", "updated": name, "model": new_model}
            return {"error": f"Member '{name}' not found"}

        elif action == "channel":
            op = kwargs.get("op")
            channel = kwargs.get("channel")
            topic = kwargs.get("topic", "")
            target_name = kwargs.get("name")
            if not op:
                return {"error": "Missing 'op' parameter for channel action"}
            return self.bm.manage_channel(
                caller=caller,
                op=op,
                channel=channel,
                topic=topic,
                target_name=target_name,
            )

        return {"error": f"Unknown action '{action}'"}

    def handle_post_message(
        self, text: str, channel: Optional[str] = None, caller: Optional[str] = None, **_extra
    ) -> Dict[str, Any]:
        if not text:
            return {"error": "Missing text"}
        effective_caller = caller or self.caller
        target_channel = channel or self._default_channel_for(effective_caller)
        msg = self.bm.post_message(
            agent=effective_caller, text=text, channel=target_channel
        )
        return {"status": "posted", "seq": msg["seq"]}

    def handle_read_board(
        self,
        since: int = 0,
        limit: int = 100,
        channel: Optional[str] = None,
        status: bool = False,
        seqs: Optional[List[int]] = None,
        caller: Optional[str] = None,
        **_extra,
    ) -> Dict[str, Any]:
        effective_caller = caller or self.caller
        if status:
            members = self.bm.read_members()
            return {"status": "ok", "members": members.get("members", {})}

        return self.bm.read_messages(
            since=since,
            limit=limit,
            channel=channel,
            reader=effective_caller,
            seqs=seqs,
        )

    def handle_swarm_wait(
        self,
        since: int,
        mentions_only: bool = True,
        timeout_seconds: int = 300,
        caller: Optional[str] = None,
        **_extra,
    ) -> str:
        effective_caller = caller or self.caller
        outcome = self.bm.wait_for_event(
            reader_name=effective_caller,
            since_seq=since,
            mentions_only=mentions_only,
            timeout=float(timeout_seconds),
        )
        base_msg = (
            f"Waiting for a message that calls on you by name, or on @all posted after #{since}."
            if mentions_only
            else f"Waiting for any message posted after #{since}."
        )
        if outcome.get("woken"):
            head = outcome.get("head", since)
            matching_seqs = ", ".join(f"#{s}" for s in outcome.get("matching_seqs", []))
            return (
                f"{base_msg}\n"
                f"[swarm] Woken by new board message(s) {matching_seqs} (board head is #{head}; "
                f"you have read up to #{since}). Call read_board(since={since}) to read what you missed."
            )
        return base_msg
