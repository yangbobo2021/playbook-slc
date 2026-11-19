# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Boboyang
"""Render progress and verify read-only evidence for an active CLI command."""
from datetime import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import time

WAIT_SECONDS = 60
COMPILE_PHASES = {
    "text2gears": "生成流程规则",
    "optimize": "检查流程规则中可简化的步骤",
    "prefix": "整理发给 Agent 的指令",
    "gears2fsm": "生成并检查工作流的执行逻辑",
    "link": "组装可运行入口",
}
PHASE_START = re.compile(r"^→ ([\w-]+) \((?:writing|linking) .+\)$")
PHASE_DONE = re.compile(r"^✓ ([\w-]+) wrote (.+) \([^()]+\)$")
UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
LEASE = re.compile(rf"^\.({UUID})\.lock(?:\.retired\.{UUID})?$")


class WaitingProgress:
    def __init__(self, emit, clock):
        self.emit = emit
        self.clock = clock
        self.last_activity = clock()
        self.wait_reported = False

    def activity(self):
        self.last_activity = self.clock()
        self.wait_reported = False

    def wait(self, message, **evidence):
        if not self.wait_reported and self.clock() - self.last_activity >= WAIT_SECONDS:
            self.emit("waiting", message, **evidence)
            self.wait_reported = True

    def finish(self, _pid, _started, _stdout):
        pass


class CompileProgress(WaitingProgress):
    def __init__(self, emit, source, build, timeout, *, combined=False, clock=time.monotonic):
        super().__init__(emit, clock)
        self.build = build
        self.timeout = timeout
        self.combined = combined
        self.phase = None
        self.completed = {}
        self.emit("compile-start", f"正在把 {source.name} 编译成可运行的工作流。整条编译最多等待 {timeout} 秒。")

    def feed(self, line):
        started = PHASE_START.fullmatch(line.strip())
        if started and started[1] in COMPILE_PHASES and started[1] != self.phase:
            self.phase = started[1]
            self.activity()
            saved = "流程规则已保存。" if self.phase == "prefix" and "text2gears" in self.completed else ""
            self.emit("compile-phase", f"{saved}正在{COMPILE_PHASES[self.phase]}，等待 SLC 返回。",
                      phase=self.phase)
        done = PHASE_DONE.fullmatch(line.strip())
        if done and done[1] in COMPILE_PHASES and done[1] not in self.completed:
            path = (self.build / done[2]).resolve()
            if not path.is_relative_to(self.build) or not path.is_file():
                return
            self.completed[done[1]] = str(path)
            self.activity()
            if done[1] == "text2gears":
                self.emit("compile-saved", "流程规则已保存。", phase=done[1], path=str(path))

    def tick(self, _pid, _started):
        phase = COMPILE_PHASES.get(self.phase, "编译工作流")
        saved = "流程规则已保存。" if "text2gears" in self.completed else ""
        self.wait(f"{saved}正在{phase}，仍在等待 SLC 返回。", phase=self.phase)

    def timed_out(self):
        phase = COMPILE_PHASES.get(self.phase, "编译工作流")
        artifacts = "、".join(f"`{path}`" for path in self.completed.values())
        saved = f"已保留这次完成的中间产物 {artifacts}。" if artifacts else ""
        downstream = "登记和运行均未开始。" if self.combined else ""
        entry_status = "本次未确认生成可运行入口。" if any(self.build.glob("*.mjs")) else "本次未生成可运行入口。"
        self.emit("compile-timeout", f"编译已到 {self.timeout} 秒的等待上限并停止，最后停在{phase}。"
                  f"{saved}{entry_status}{downstream}", phase=self.phase,
                  build_dir=str(self.build), completed=self.completed.copy(), timeout=self.timeout,
                  publication="unconfirmed", downstream_started=False)


class RunProgress(WaitingProgress):
    def __init__(self, emit, read_replay, root, name, workflow_id, entry, data, input_hash, digest,
                 timeout, *, clock=time.monotonic):
        super().__init__(emit, clock)
        self.read_replay = read_replay
        self.root = root
        self.name = name
        self.workflow_id = workflow_id
        self.entry = entry
        self.data = data
        self.input_hash = input_hash
        self.digest = digest
        self.timeout = timeout
        self.input = f"/{name} {data}"
        spex_home = os.environ.get("SPEX_HOME", "")
        self.sessions = (Path(spex_home) if spex_home.strip() else Path.home() / ".spex") / "sessions"
        self.session = None
        self.replay_head = None
        self.business = None
        self.runtime_started = False
        self.runtime_finished = False
        self.emit("run-start", f"正在用 {data.name} 运行 /{name}。整条运行命令最多等待 {timeout} 秒。")

    def feed(self, line):
        status = line.strip()
        if not self.runtime_started and status == f"◇ /{self.name} started":
            self.runtime_started = True
            self.activity()
            self.emit("run-workflow-started", f"/{self.name} 已启动，正在处理本次输入。")
        if not self.runtime_finished and status == f"◇ /{self.name} finished":
            self.runtime_finished = True

    def find_session(self, pid, started, *, retired=False):
        matches = set()
        for path in self.sessions.glob(".*.lock*"):
            name = LEASE.fullmatch(path.name)
            if not name or (not retired and not path.name.endswith(".lock")):
                continue
            owner_path = path / "owner.json"
            try:
                if path.is_symlink() or owner_path.is_symlink():
                    continue
                owner = json.loads(owner_path.read_text())
                acquired = datetime.fromisoformat(owner["acquiredAt"].replace("Z", "+00:00")).timestamp()
                if (owner.get("kind") == "captain-session-lease" and owner.get("sessionId") == name[1]
                        and owner.get("pid") == pid and acquired >= started):
                    matches.add(name[1])
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                continue
        return next(iter(matches)) if len(matches) == 1 else None

    def refresh(self, pid, started, *, session=None, final=False):
        self.session = session or self.session or self.find_session(pid, started, retired=final)
        if not self.session or self.business or not re.fullmatch(UUID, self.session):
            return
        try:
            head = (self.sessions / f"{self.session}.replay.json").stat()
            identity = (self.session, head.st_ino, head.st_mtime_ns, head.st_size)
            if identity == self.replay_head and not final:
                return
            entries = self.read_replay(self.root, self.session)
            if self.digest(self.data) == self.input_hash:
                self.business = terminal_evidence(entries, self.session, self.workflow_id,
                                                  self.entry, self.input, str(self.data))
                if self.business:
                    self.business["input_sha256"] = self.input_hash
            self.replay_head = identity
        except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError):
            # The replay root may be between publications or unavailable in this release.
            return
        if self.business and not final:
            self.activity()
            self.emit("run-business-completed", self.result_message() + "正在等待 Playbook 返回最终回复。",
                      business=self.business)

    def result_message(self):
        output = self.business["output"]
        value = output.get("report") if isinstance(output, dict) and set(output) == {"report"} else output
        if isinstance(value, str) and value.isprintable() and len(value) <= 160 and "`" not in value:
            return f"已从本次运行记录确认 /{self.name} 完成。结果为 `{value}`。"
        return f"已从本次运行记录确认 /{self.name} 完成。结果见本次运行记录。"

    def tick(self, pid, started):
        self.refresh(pid, started)
        if self.business:
            self.wait("工作流结果已确认，仍在等待 Playbook 返回最终回复。")
        elif self.runtime_finished:
            self.wait("Playbook 显示流程已结束，正在等待最终回复。业务结果还需核对。")
        else:
            self.wait(f"/{self.name} 仍在运行，正在等待 Playbook 更新进度。")

    def timed_out(self):
        if self.business:
            message = self.result_message() + f"Playbook 未在 {self.timeout} 秒的时限内返回最终回复。"
        else:
            message = f"Playbook 运行命令已到 {self.timeout} 秒的等待上限。缺少能对应本次运行的成功终态，业务结果未确认。"
        self.emit("run-timeout", message, business=self.business, cli_reply="timed-out", timeout=self.timeout)

    def finish(self, pid, started, stdout):
        try:
            session = json.loads(stdout).get("sessionId")
        except (ValueError, AttributeError):
            session = None
        if isinstance(session, str) and self.session and session != self.session:
            self.business = None
            self.replay_head = None
        self.refresh(pid, started, session=session if isinstance(session, str) else None, final=True)


def terminal_evidence(entries, session, workflow_id, entry, command, data):
    """Match the fresh headless turn and root workflow before accepting output."""
    context = None
    turn = None
    runtime = None
    sources = {entry}
    if Path(entry).is_absolute():
        sources.add(Path(entry).as_uri())
    for item in entries:
        record = item["record"]
        kind = record.get("type")
        if kind == "session_context":
            config = record["configuration"]
            scoped = config.get("catalog", {}).get(workflow_id, {})
            if config.get("scopedWorkflowId") != workflow_id or scoped.get("from") not in sources:
                return None
            context = item["seq"]
        elif kind == "turn_started":
            if record["turn"].get("prompt") != command or record["turn"].get("id") != 1:
                return None
            turn = record["turn"]["id"]
        elif kind == "captain_telemetry" and context is not None and turn is not None:
            trace = record["payload"]
            if (record.get("contextSeq") != context or record.get("turnId") != turn
                    or trace.get("turnId") != turn or trace.get("playbookId") != workflow_id
                    or trace.get("depth") != 0 or not trace.get("sessionId")
                    or trace.get("rootSessionId") != trace["sessionId"]):
                continue
            payload = trace.get("payload", {})
            if trace.get("type") == "boss.input.received" and payload.get("text") == data:
                runtime = trace["sessionId"]
            if (trace.get("type") == "boss.input.settled" and trace["sessionId"] == runtime
                    and payload.get("outcome") == "terminal"
                    and payload.get("terminal", {}).get("kind") == "success" and payload.get("output") not in (None, "", {}, [])):
                return {"status": "confirmed-success", "session_id": session, "turn": turn,
                        "workflow_id": workflow_id, "runtime_session_id": runtime,
                        "input": data, "replay_sequence": item["seq"], "output": payload["output"]}
    return None
