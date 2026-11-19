#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Boboyang
"""Run installed SLC and Playbook commands for the current Skill session."""
import argparse
import hashlib
import json
import os
from pathlib import Path
from queue import SimpleQueue
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

from workflow_progress import CompileProgress, RunProgress

TOOLS = {"slc": ("@sublang/slc", "0.23.0"), "playbook": ("@sublang/playbook", "19.1.0")}
AGENTS = {"codex": ("codex", "codex"), "claude-code": ("claude-code", "claude")}
ADAPTER = Path(__file__).with_name("workflow_registry.mjs")


class WorkflowError(Exception):
    def __init__(self, stage, message, **details):
        super().__init__(message)
        self.result = {"status": "failed", "stage": stage, "error": message, **details}


def version_key(value):
    match = re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?", value or "")
    if not match:
        raise ValueError(f"cannot verify version {value!r}")
    return (*map(int, match.groups()[:3]), match[4] is None)


def prefix():
    value = os.environ.get("npm_config_prefix") or os.environ.get("NPM_CONFIG_PREFIX")
    return Path(value).expanduser().resolve() if value else None


def installation(tool):
    package, minimum = TOOLS[tool]
    selected_prefix = prefix()
    search_path = str(selected_prefix / "bin") if selected_prefix and os.name != "nt" else (
        str(selected_prefix) if selected_prefix else None
    )
    executable = shutil.which(tool, path=search_path)
    if not executable:
        return {"package": package, "minimum": minimum, "state": "missing"}
    resolved = Path(executable).resolve()
    candidates = [*resolved.parents]
    # npm uses command shims instead of symlinks on Windows.
    if os.name == "nt":
        candidates.insert(0, Path(executable).parent / "node_modules" / package)
    for directory in candidates:
        metadata = directory / "package.json"
        if not metadata.is_file():
            continue
        info = json.loads(metadata.read_text(encoding="utf-8"))
        if info.get("name") == package:
            version = info.get("version")
            return {"package": package, "minimum": minimum, "version": version,
                    "command": str(Path(executable).absolute()), "root": str(directory),
                    "state": "ready" if version_key(version) >= version_key(minimum) else "upgrade-required"}
    raise WorkflowError("installation", f"cannot verify {package} metadata for {executable}")


def nearest_host():
    if os.name == "nt":
        return None
    pid = os.getppid()
    for _ in range(15):
        try:
            process_info = subprocess.run(["ps", "-p", str(pid), "-o", "ppid=", "-o", "comm="],
                                          text=True, capture_output=True, check=False)
        except OSError:
            # A tool sandbox can deny ps even though the session marker is present.
            return None
        info = process_info.stdout.strip().split(maxsplit=1)
        if len(info) != 2:
            break
        process = Path(info[1]).name
        if process in {"claude", "claude-code", "claude.exe"}:
            return "claude-code"
        if process in {"codex", "codex.exe", "Codex CLI", "Codex"}:
            return "codex"
        pid = int(info[0])
        if pid <= 1:
            break
    return None


def host_agent(hint):
    ancestry = nearest_host()
    markers = {"codex": os.environ.get("CODEX_THREAD_ID"), "claude-code": os.environ.get("CLAUDECODE")}
    marked = [host for host, value in markers.items() if value]
    actual = ancestry or (marked[0] if len(marked) == 1 else None)
    if actual and hint and hint != actual:
        raise WorkflowError("host", "host hint conflicts with the current session", detected_host=actual)
    if not (actual or hint):
        raise WorkflowError("host", "current session host is not available to the command; supply the host context")
    return actual or hint


def claude_model(cwd):
    home = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    model = None
    for config in (home / "settings.json", cwd / ".claude/settings.json", cwd / ".claude/settings.local.json"):
        if config.is_file():
            data = json.loads(config.read_text(encoding="utf-8"))
            model = data.get("env", {}).get("ANTHROPIC_MODEL") or data.get("model") or model
    return os.environ.get("ANTHROPIC_MODEL") or model


def configured_model(host, cwd):
    if host == "claude-code":
        return claude_model(cwd)
    config = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "config.toml"
    if config.is_file():
        import tomllib
        with config.open("rb") as stream:
            return tomllib.load(stream).get("model")
    return None


def model_selection(host, args, commands, tool):
    selected = args.session_model or configured_model(host, args.cwd)
    origin = "session" if args.session_model else "host-config" if selected else "host-default"
    if not selected:
        selected = adapter_call(commands, tool, "default-model", host=host)["model"]
    if selected is not None and (not isinstance(selected, str) or not re.fullmatch(r"[\w][\w./:@+\[\]-]*", selected)):
        raise WorkflowError("host", "configured model is not a usable model name")
    return {"model": selected, "model_source": origin}


def local_file(cwd, value):
    path = (cwd / Path(value).expanduser()).resolve()
    if not path.is_file():
        raise ValueError(f"file not found: {path}")
    return path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Commands:
    def __init__(self, cwd, timeout, progress_file=None):
        self.cwd = cwd
        self.timeout = timeout
        self.records = []
        self.progress = []
        self.progress_file = progress_file
        self.secrets = sorted({v for k, v in os.environ.items() if len(v) >= 8 and
                               re.search(r"(?:API_?KEY|TOKEN|PASSWORD|SECRET)$", k, re.I)}, key=len, reverse=True)

    def redact(self, line):
        for secret in self.secrets:
            line = line.replace(secret, "[redacted]")
        return line

    def emit(self, event, message, **evidence):
        message = self.redact(message)
        message = "".join(char if char.isprintable() else " " for char in message)
        record = {"at": time.time(), "event": event, "message": message, **evidence}
        record = json.loads(self.redact(json.dumps(record, ensure_ascii=False)))
        self.progress.append(record)
        if self.progress_file:
            self.progress_file.write(json.dumps(record, ensure_ascii=False) + "\n")
            self.progress_file.flush()
        print(message, file=sys.stderr, flush=True)

    def invoke(self, stage, argv, *, cwd=None, env=None, payload=None, observer=None):
        argv = list(map(str, argv))
        cwd = cwd or self.cwd
        started = time.time()
        lines = []
        pending = SimpleQueue()
        with tempfile.TemporaryFile() as stdout, subprocess.Popen(
            argv, cwd=cwd, env=env, stdin=subprocess.PIPE if payload is not None else subprocess.DEVNULL,
            stdout=stdout, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
            start_new_session=os.name != "nt",
        ) as child:
            def capture():
                for line in child.stderr:
                    safe = self.redact(line)
                    lines.append(safe)
                    if observer:
                        pending.put(safe)

            def progress():
                while not pending.empty():
                    observer.feed(pending.get())

            reader = threading.Thread(target=capture, daemon=True)
            reader.start()
            interruption = None
            try:
                if payload is not None:
                    try:
                        child.stdin.write(json.dumps(payload, ensure_ascii=False))
                        child.stdin.close()
                    except BrokenPipeError:
                        pass
                deadline = time.monotonic() + self.timeout
                if observer:
                    while child.poll() is None:
                        progress()
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise subprocess.TimeoutExpired(argv, self.timeout)
                        observer.tick(child.pid, started)
                        try:
                            child.wait(timeout=max(0, min(1, deadline - time.monotonic())))
                        except subprocess.TimeoutExpired:
                            continue
                else:
                    child.wait(timeout=self.timeout)
            except (subprocess.TimeoutExpired, KeyboardInterrupt) as error:
                interruption = "timed out" if isinstance(error, subprocess.TimeoutExpired) else "cancelled"
                try:
                    if os.name == "nt":
                        child.kill()
                    else:
                        os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                child.wait()
            finally:
                reader.join()
            stdout.seek(0)
            raw = stdout.read().decode("utf-8", errors="replace")
            if observer:
                progress()
                observer.finish(child.pid, started, raw)
        record = {"stage": stage, "argv": [self.redact(arg) for arg in argv], "cwd": str(cwd),
                  "started": started, "seconds": round(time.time() - started, 3),
                  "exit_code": child.returncode, "stdout": self.redact(raw), "stderr": "".join(lines)}
        self.records.append(record)
        if interruption:
            raise WorkflowError(stage, f"command {interruption}", timed_out=interruption == "timed out")
        if child.returncode:
            if stage == "compile" and child.returncode == 2:
                for line in record["stderr"].splitlines():
                    if line.startswith("SLC_CLARIFICATION: "):
                        clarification = json.loads(line.partition(": ")[2])
                        return {"status": "clarification-required", "stage": stage, "clarification": clarification}
            raise WorkflowError(stage, "command failed", exit_code=child.returncode)
        return raw


def need_tools(args, commands, names):
    tools = {name: installation(name) for name in names}
    approvals = dict(approval.split("=", 1) for approval in args.approve_upgrade)
    waiting = [tool for name, tool in tools.items()
               if tool["state"] == "upgrade-required" and approvals.get(name) != tool["version"]]
    if waiting:
        raise WorkflowError("installation", "installed versions require user confirmation",
                            status="upgrade-confirmation-required", tools=tools)
    for name, tool in tools.items():
        if tool["state"] == "ready":
            continue
        npm = shutil.which("npm")
        if not npm:
            raise WorkflowError("installation", "npm is not available on PATH")
        if tool["state"] == "upgrade-required":
            npm_root = Path(commands.invoke("installation", [npm, "root", "-g"]).strip()).resolve()
            if (npm_root / tool["package"]).resolve() != Path(tool["root"]).resolve():
                raise WorkflowError("installation", "the installed CLI and npm global target are different",
                                    installed=tool["root"], npm_root=str(npm_root))
            if installation(name) != tool:
                raise WorkflowError("installation", "installed CLI changed after confirmation")
        commands.emit("install-start", f"正在安装 {tool['package']} {tool['minimum']}。")
        commands.invoke("installation", [npm, "install", "-g", f"{tool['package']}@{tool['minimum']}"])
        updated = installation(name)
        if updated["state"] != "ready":
            raise WorkflowError("installation", "npm completed but the selected CLI is unavailable", tool=updated)
        tools[name] = updated
        commands.emit("install-complete", f"{tool['package']} {updated['version']} 已可用。")
    return tools


def adapter_call(commands, tool, operation, **values):
    raw = commands.invoke(operation, ["node", ADAPTER, tool["root"]], payload={"operation": operation, **values})
    result = json.loads(raw)
    if result["status"] == "failed":
        raise WorkflowError(operation, result["error"], **{"details": result.get("details")})
    return result


def compile_source(args, commands, tool, host, model, source):
    build_root = args.cwd / ".playbook-slc" / "builds"
    build_root.mkdir(parents=True, exist_ok=True)
    build = Path(tempfile.mkdtemp(prefix="build-", dir=build_root))
    observer = CompileProgress(commands.emit, source, build, args.timeout, combined=args.action == "workflow")
    config = adapter_call(commands, tool, "compiler-config", host=AGENTS[host][0])
    config_path = build / "slc.config.json"
    config_path.write_text(json.dumps(config["config"]), encoding="utf-8")
    env = dict(os.environ, SLC_AGENT=AGENTS[host][0])
    env.pop("SLC_MODEL", None)
    if model["model"]:
        env["SLC_MODEL"] = model["model"]
    before = digest(source)
    try:
        output = commands.invoke("compile", [tool["command"], "--config", config_path, "playbook", source],
                                 cwd=build, env=env, observer=observer)
    except WorkflowError as error:
        if error.result.get("timed_out"):
            observer.timed_out()
            error.result.update(build_dir=str(build), last_phase=observer.phase, completed=observer.completed)
        raise
    if isinstance(output, dict):
        return {**output, "build_dir": str(build), "source": str(source), "source_sha256": before}
    if digest(source) != before:
        raise WorkflowError("compile", "source changed during compilation", build_dir=str(build))
    entries = []
    for line in output.splitlines():
        path = (build / line).resolve()
        if path.suffix == ".mjs" and path.is_file() and path.is_relative_to(build):
            entries.append(path)
    if len(set(entries)) != 1:
        raise WorkflowError("compile", "SLC did not report exactly one runnable entry", build_dir=str(build))
    entry = entries[0]
    commands.emit("compile-complete", f"已生成可运行入口 `{entry}`。", entry=str(entry))
    return {"status": "compiled", "entry": str(entry), "entry_sha256": digest(entry),
            "build_dir": str(build), "source": str(source), "source_sha256": before}


def register(args, commands, tool, host, model, entry):
    commands.emit("register-start", f"正在登记工作流入口 `{entry.name}`。")
    result = adapter_call(commands, tool, "register", entry=str(entry), adapter=AGENTS[host][1],
                          model=model["model"], decision=args.collision, token=args.token, name=args.name,
                          executable=tool["command"])
    if result["status"] == "conflict":
        commands.emit("register-waiting", "发现同名工作流，正在等待你选择沿用、替换或换名。")
    elif result["status"] == "reused":
        commands.emit("register-complete", f"本次沿用已登记的 /{result['name']}。")
    else:
        commands.emit("register-complete", f"工作流已登记为 /{result['name']}。")
    return result


def read_replay(root, session):
    result = subprocess.run(["node", str(ADAPTER), root], input=json.dumps({"operation": "read-replay", "session": session}),
                            capture_output=True, text=True, check=True, timeout=1)
    return json.loads(result.stdout)["entries"]


def run_workflow(commands, tool, name, data, expected_entry=None):
    command = name.removeprefix("/")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", command):
        raise WorkflowError("run", "expected a registered slash command name")
    registration = adapter_call(commands, tool, "lookup", name=command)
    if expected_entry and registration["entry"] != expected_entry:
        raise WorkflowError("run", "the selected registration changed before execution",
                            expected_entry=expected_entry, current_entry=registration["entry"])
    input_hash = digest(data)
    observer = RunProgress(commands.emit, read_replay, tool["root"], command, registration.get("id", command),
                           registration["entry"], data, input_hash, digest, commands.timeout)
    try:
        output = commands.invoke("run", [tool["command"], "run", "--json", "--", observer.input], observer=observer)
    except WorkflowError as error:
        error.result.update(business=observer.business, session_id=observer.session,
                            cli_reply="timed-out" if error.result.get("timed_out") else "unavailable")
        if error.result.get("timed_out"):
            observer.timed_out()
        raise
    result = json.loads(output)
    if not isinstance(result.get("sessionId"), str) or not isinstance(result.get("reply"), str):
        raise WorkflowError("run", "Playbook did not return a session id and reply")
    if observer.business:
        commands.emit("run-reply", observer.result_message() + "Playbook 已返回最终回复。", business=observer.business)
    else:
        commands.emit("run-reply", "Playbook 已返回会话回复，业务终态还需核对。")
    return {"status": "run-returned", "name": command, "input": str(data),
            "input_sha256": input_hash, "entry": registration["entry"],
            "session_id": result["sessionId"], "reply": commands.redact(result["reply"]),
            "business": observer.business}


def execute(args, commands, stages):
    op = args.action
    if op == "install":
        names = args.tools or list(TOOLS)
        commands.emit("install-check", f"正在检查所需工具的版本（{'、'.join(names)}）。")
        tools = need_tools(args, commands, names)
        versions = "、".join(f"{name} {tool['version']}" for name, tool in tools.items())
        commands.emit("install-ready", f"所需工具已可用（{versions}）。")
        return {"status": "ready", "tools": tools}
    source = local_file(args.cwd, args.source) if op in {"compile", "workflow"} else None
    entry = local_file(args.cwd, args.entry) if op == "register" else None
    data = local_file(args.cwd, args.input) if op in {"run", "workflow"} else None
    if op == "run":
        tool = need_tools(args, commands, ["playbook"])["playbook"]
        return run_workflow(commands, tool, args.workflow, data, args.expected_entry)
    host = host_agent(args.host)
    tools = need_tools(args, commands, {"compile": ["slc"], "register": ["playbook"],
                                        "workflow": ["slc", "playbook"]}[op])
    model = model_selection(host, args, commands, tools.get("playbook") or tools["slc"])
    stages["selection"] = {"host": host, **model}
    if op in {"register", "workflow"} and not model["model"]:
        raise WorkflowError("register", "Playbook requires a named model; none can be resolved from the current host configuration")
    if source:
        stages["compile"] = compile_source(args, commands, tools["slc"], host, model, source)
        if op == "compile" or stages["compile"]["status"] != "compiled":
            return stages["compile"]
        entry = Path(stages["compile"]["entry"])
    stages["register"] = register(args, commands, tools["playbook"], host, model, entry)
    if op == "register" or stages["register"]["status"] == "conflict":
        return stages["register"]
    selected = stages["register"]
    return run_workflow(commands, tools["playbook"], selected["name"], data, selected["entry"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cwd", type=Path, default=Path.cwd())
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--progress-file", type=Path, help="write progress as JSONL to this new local file")
    parser.add_argument("--host", choices=AGENTS, help="host context when process detection is unavailable")
    parser.add_argument("--session-model", help="verified model name from the current conversation")
    parser.add_argument("--approve-upgrade", action="append", default=[], metavar="TOOL=OLD_VERSION")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("install").add_argument("tools", nargs="*", metavar="TOOL")
    sub.add_parser("compile").add_argument("source")
    registration = sub.add_parser("register")
    registration.add_argument("entry")
    registration.add_argument("--collision", choices=["reuse", "replace", "rename"])
    registration.add_argument("--token")
    registration.add_argument("--name")
    combined = sub.add_parser("workflow")
    combined.add_argument("source")
    combined.add_argument("input")
    run = sub.add_parser("run")
    run.add_argument("workflow")
    run.add_argument("input")
    run.add_argument("--expected-entry", help="entry returned by the user's selected registration")
    args = parser.parse_args()
    args.cwd = args.cwd.expanduser().resolve()
    if not args.cwd.is_dir() or args.timeout <= 0:
        parser.error("working directory must exist and timeout must be positive")
    if args.action == "install" and any(tool not in TOOLS for tool in args.tools):
        parser.error("tools must be slc or playbook")
    if any(x.split("=", 1)[0] not in TOOLS or "=" not in x for x in args.approve_upgrade):
        parser.error("approval must be slc=OLD_VERSION or playbook=OLD_VERSION")
    args.collision = getattr(args, "collision", None)
    args.token = getattr(args, "token", None)
    args.name = getattr(args, "name", None)
    if bool(args.collision) != bool(args.token) or (args.collision == "rename") != bool(args.name):
        parser.error("provide a collision choice with its token, and a name only for rename")
    progress_file = None
    if args.progress_file:
        try:
            fd = os.open(args.cwd / args.progress_file.expanduser(), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            progress_file = os.fdopen(fd, "w", encoding="utf-8")
        except OSError as error:
            parser.error(str(error))
    commands = Commands(args.cwd, args.timeout, progress_file)
    stages = {}
    try:
        result = execute(args, commands, stages)
    except WorkflowError as error:
        result = error.result
        if result["status"] == "upgrade-confirmation-required":
            commands.emit("install-waiting", "检测到低于要求的版本，正在等待你确认是否升级。")
    except (OSError, ValueError, TypeError) as error:
        result = {"status": "failed", "stage": args.action, "error": str(error)}
    if result["status"] == "clarification-required":
        commands.emit("compile-waiting", "编译停在需要你确认的流程问题，后续操作尚未开始。")
    if progress_file:
        progress_file.close()
    evidence = {"result": result, "stages": stages, "commands": commands.records, "progress": commands.progress}
    print(commands.redact(json.dumps(evidence, ensure_ascii=False)))
    return 0 if result["status"] in {"ready", "compiled", "registered", "reused", "run-returned"} else (
        2 if result["status"] in {"conflict", "upgrade-confirmation-required", "clarification-required"} else 1
    )


if __name__ == "__main__":
    sys.exit(main())
