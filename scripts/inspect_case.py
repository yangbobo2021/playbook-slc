#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Boboyang
"""Collect SLC evidence without executing SLC, generated modules, or shell text."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = {
    "environment": r"workspace routing discovery timed out|authentication failed|ERR_PROXY_TUNNEL|missing.*(?:SDK|native component)",
    "clarification": r"SLC_CLARIFICATION|clarification required",
    "unverified": r"remains unverified|remains unproved|no reachable entry|unverified coverage|exhausted.*(?:budget|attempt|depth)",
    "repository-contract": r"repository-disposition-mismatch|repository disposition|repositoryDisposition|uncommitted file update",
    "typescript": r"\bTS\d{4,5}\b",
}


def identity(path):
    if not path.is_file():
        return {"path": str(path), "exists": False}
    data = path.read_bytes()
    return {"path": str(path), "exists": True, "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest()}


def package_at(entry):
    """Read metadata only; never invoke an unknown compiler entry."""
    if entry is None:
        return None
    candidate = Path(entry).expanduser()
    if not candidate.exists():
        found = shutil.which(str(entry))
        if found is None:
            return {"requested": str(entry), "found": False}
        candidate = Path(found)
    resolved = candidate.resolve()
    record = {"requested": str(entry), "resolved": str(resolved), "found": True,
              "entry": identity(resolved)}
    for parent in list(resolved.parents)[:8]:
        f = parent / "package.json"
        if not f.is_file():
            continue
        try:
            pkg = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if pkg.get("name") == "@sublang/slc":
            record.update({"package": pkg["name"], "version": pkg.get("version"), "package_root": str(parent)})
            for dep in [parent / "node_modules/@sublang/playbook/package.json",
                        parent.parent / "playbook/package.json"]:
                if dep.is_file():
                    p = json.loads(dep.read_text())
                    record["playbook"] = {"path": str(dep.resolve()), "version": p.get("version")}
                    break
            break
    return record


def parse_events(path):
    events, errors = [], []
    if path is None or not path.is_file():
        return events, errors
    for line, text in enumerate(path.read_text(errors="replace").splitlines(), 1):
        try:
            event = json.loads(text)
            if not isinstance(event, dict):
                raise ValueError("event is not an object")
            events.append((line, event))
        except ValueError as exc:
            errors.append({"line": line, "error": str(exc)})
    return events, errors


def summarize(events, event_path):
    start, finish, stages, diagnostics, checks, errors, sessions = None, None, [], [], [], [], set()
    snapshots = []
    for line, e in events:
        kind, data = e.get("kind"), e.get("data", {})
        if not isinstance(data, dict):
            continue
        at = e.get("at")
        if kind == "invocation-start":
            start = {key: data.get(key) for key in ["argv", "cwd", "compiler", "configuration", "coverage"]}
            start["at"] = at
            env = data.get("environment", {})
            if isinstance(env, dict):
                start["environment"] = {k: {field: v.get(field) for field in ["name", "version", "root"]}
                                        for k, v in env.items() if isinstance(v, dict)}
        elif kind == "invocation-finish":
            finish = {"at": at, "ok": data.get("ok"), "outputs": data.get("outputs", []), "cause": data.get("cause")}
            diagnostics.extend(data.get("diagnostics", []))
        elif kind == "progress":
            stages.append({"at": at, "line": line, **data})
        elif kind == "phase-return":
            value = data.get("value", {})
            if isinstance(value, dict):
                diagnostics.extend(value.get("diagnostics", []))
        elif kind == "file-snapshot":
            snapshots.append({"line": line, **{key: data.get(key) for key in ["path", "snapshot", "sha256", "size"]}})
        elif kind == "agent-event":
            if data.get("sessionId"):
                sessions.add(data["sessionId"])
            payload = data.get("payload", {})
            if not isinstance(payload, dict):
                continue
            if data.get("type") == "error":
                errors.append({"at": at, "line": line, "message": payload.get("message")})
            if data.get("type") == "tool_result":
                output = payload.get("output", {})
                text = output.get("aggregated_output", "") if isinstance(output, dict) else ""
                try:
                    report = json.loads(text)
                except (TypeError, ValueError):
                    report = None
                if isinstance(report, dict) and report.get("schema") == "sublang.slc.check.v1":
                    checks.append({"at": at, "line": line, "status": report.get("status"),
                                   "category": report.get("category"), "findings": report.get("findings", [])})
    return {"event_file": str(event_path) if event_path else None, "start": start, "finish": finish,
            "stages": stages, "diagnostics": list(dict.fromkeys(str(x) for x in diagnostics)),
            "checker_calls": checks, "file_snapshots": snapshots,
            "agent_errors": errors, "session_ids": sorted(sessions)}


def inspect(args):
    project = args.project.resolve(strict=True)
    local = lambda value: (value if value.is_absolute() else project / value).resolve()
    artifact = local(args.artifact_dir) if args.artifact_dir else None
    attempt = local(args.attempt) if args.attempt else None
    if attempt is None and artifact is not None:
        parent = artifact / ".slc/attempts"
        candidates = sorted(p for p in parent.glob("*") if p.is_dir())
        attempt = candidates[-1] if candidates else None
    event_path = attempt / "events.jsonl" if attempt else None
    events, parse_errors = parse_events(event_path)
    audit = summarize(events, event_path)
    files = [identity(local(p)) for p in args.source]
    for item in files:
        # Only compare original input snapshots, not later before/after generated artifacts.
        original = next((s for s in audit["file_snapshots"]
                         if isinstance(s.get("path"), str)
                         and str(Path(s["path"]).resolve()) == item["path"]
                         and "-input-" in str(s.get("snapshot", ""))), None)
        item["recorded_input"] = original
        item["matches_recorded_input"] = (item.get("sha256") == original["sha256"]) if original and item["exists"] else None
    if artifact is not None and artifact.is_dir():
        files.extend(identity(p) for p in sorted(artifact.iterdir()) if p.is_file())
    logs = []
    corpus = "\n".join(audit["diagnostics"] + [str(x.get("message", "")) for x in audit["agent_errors"]])
    for path in args.log:
        f = local(path)
        logs.append(identity(f))
        if f.is_file():
            corpus += "\n" + f.read_text(errors="replace")
    clues = [{"category": key, "matches": sorted(set(re.findall(pattern, corpus, re.I)))[:12],
              "status": "investigation-hint-not-proven-cause"}
             for key, pattern in PATTERNS.items() if re.search(pattern, corpus, re.I)]
    match = re.search(r'(?:phase\s+["\']|✗\s+)([\w-]+)(?:["\']|\s+failed)', corpus)
    failed = next((s.get("phase") for s in reversed(audit["stages"]) if s.get("kind") == "phase-fail"), None)
    failed = failed or (match.group(1) if match else None)
    snapshot = ROOT / "references/upstream/manifest.json"
    snapshots = json.loads(snapshot.read_text())["repositories"] if snapshot.exists() else []
    # Recorded environment is authoritative for that invocation. Current installation is separate.
    recorded = audit.get("start") or {}
    versions = {"slc": (recorded.get("compiler") or {}).get("version"),
                "playbook": (recorded.get("environment") or {}).get("playbook", {}).get("version")}
    compare = [{"repository": r["name"], "snapshot_version": r["version"], "commit": r["commit"],
                "recorded_version": versions[r["name"]],
                "version_match": (versions[r["name"]] == r["version"]) if versions[r["name"]] else None}
               for r in snapshots]
    result = {"schema": "playbook-slc.inspection.v1", "collected_at": datetime.now(timezone.utc).isoformat(),
              "project": str(project), "attempt": str(attempt) if attempt else None,
              "failed_phase": failed, "audit": audit, "event_parse_errors": parse_errors,
              "files": files, "logs": logs, "current_installation": package_at(args.slc),
              "reference_versions": compare, "clues": clues,
              "verification": {"compiler_executed": False, "generated_modules_executed": False,
                               "root_cause_confirmed": False}}
    out = args.out.absolute()
    out.mkdir(parents=True, exist_ok=False)
    (out / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    (out / "summary.md").write_text(
        "# SLC 证据摘要\n\n"
        f"失败阶段：{failed or '未知'}。编译终态：{(audit['finish'] or {}).get('ok', '未知')}。\n\n"
        f"原始审计：{event_path or '未提供'}。\n\n"
        f"已捕获 {len(audit['checker_calls'])} 次结构化检查结果；它们不是完整编译次数。\n\n"
        f"调查线索：{', '.join(c['category'] for c in clues) or '暂无'}。\n\n"
        "本工具没有执行编译或生成模块，没有自动确认根因。请读取 summary.json 引用的原始证据和相应版本源码。\n")
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--project", type=Path, required=True)
    p.add_argument("--source", type=Path, action="append", default=[])
    p.add_argument("--artifact-dir", type=Path)
    p.add_argument("--attempt", type=Path)
    p.add_argument("--log", type=Path, action="append", default=[])
    p.add_argument("--slc")
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    try:
        result = inspect(a)
        print(json.dumps({"out": str(a.out), "failed_phase": result["failed_phase"],
                          "clues": [c["category"] for c in result["clues"]]}, ensure_ascii=False))
    except (OSError, ValueError, KeyError) as exc:
        p.exit(1, f"inspection error: {exc}\n")


if __name__ == "__main__":
    main()
