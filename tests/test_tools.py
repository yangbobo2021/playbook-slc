# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Boboyang
"""Offline CLI integration tests. Never invoke SLC, generated modules or models."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ToolsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="playbook-slc-test-")
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def cli(self, script, *args, ok=True):
        run = subprocess.run([sys.executable, str(ROOT / "scripts" / script), *map(str, args)],
                             text=True, capture_output=True, timeout=25)
        if ok:
            self.assertEqual(run.returncode, 0, run.stderr)
        else:
            self.assertNotEqual(run.returncode, 0, run.stdout)
        return run

    def write(self, rel, text):
        f = self.root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
        return f

    def test_backup_preserves_bytes_evidence_and_missing_entry(self):
        source = self.write("definition.md", "# 流程\n原始描述\n")
        artifact = self.write("definition.playbook/definition.fsm.ts", "export const marker = 1;\n")
        self.write("definition.playbook/.slc/attempts/one/events.jsonl", '{"kind":"failure"}\n')
        self.write("definition.playbook/node_modules/huge.js", "excluded")
        before = sha(source)
        out = self.root / ".playbook-slc/before"
        self.cli("backup_case.py", "--root", self.root, "--out", out,
                 "definition.md", "definition.playbook", "definition.mjs")
        m = json.loads((out / "manifest.json").read_text())
        self.assertEqual(m["missing"], ["definition.mjs"])
        self.assertEqual(len(m["files"]), 3)
        self.assertEqual(sha(out / "files/definition.md"), before)
        self.assertEqual(sha(out / "files/definition.playbook/definition.fsm.ts"), sha(artifact))
        source.write_text("changed")
        self.assertEqual(sha(out / "files/definition.md"), before)
        self.cli("backup_case.py", "--root", self.root, "--out", out, "definition.md", ok=False)
        self.assertEqual(sha(out / "files/definition.md"), before)

    def test_backup_rejects_escape_recursive_destination_and_symlink(self):
        source = self.write("definition.md", "original")
        cases = [(["../outside"], self.root / "out"), (["."], self.root / "recursive")]
        link = self.root / "linked.md"
        link.symlink_to(source)
        cases.append((["linked.md"], self.root / "out-link"))
        for files, out in cases:
            self.cli("backup_case.py", "--root", self.root, "--out", out, *files, ok=False)
            self.assertFalse(out.exists())
        self.assertEqual(source.read_text(), "original")

    def test_inspector_uses_audit_not_current_installation_and_never_executes(self):
        source = self.write("definition.md", "# TDD\n")
        marker = self.root / "MUST_NOT_EXIST"
        # Even executable-looking metadata and generated files remain data.
        executable = self.write("installed/dist/cli.js", f"#!/bin/sh\ntouch '{marker}'\n")
        executable.chmod(0o755)
        self.write("installed/package.json", json.dumps({"name": "@sublang/slc", "version": "99.0.0"}))
        self.write("definition.playbook/definition.fsm.ts", f"throw Error('never import'); // {marker}\n")
        events = [
            {"at": "2026-01-01T00:00:00Z", "kind": "invocation-start", "data": {
                "argv": ["playbook", "definition.md"], "cwd": str(self.root),
                "compiler": {"version": "0.23.0"}, "configuration": {"model": "test"},
                "environment": {"playbook": {"name": "@sublang/playbook", "version": "19.1.0"}}}},
            {"kind": "agent-event", "data": {"type": "tool_result", "payload": {"output": {
                "aggregated_output": json.dumps({"schema": "sublang.slc.check.v1", "status": "failed",
                    "category": "unverified", "findings": ["state firstRed: no reachable entry"]})}}}},
            {"kind": "progress", "data": {"kind": "phase-fail", "phase": "gears2fsm", "elapsedMs": 2012828}},
            {"kind": "invocation-finish", "data": {"ok": False, "outputs": [],
                "diagnostics": ["state firstRed remains unproved: no reachable entry"],
                "cause": {"category": "artifact"}}},
        ]
        audit = self.write("definition.playbook/.slc/attempts/2026-01/events.jsonl",
                           "\n".join(map(json.dumps, events)) + '\n{"partial":')
        before = sha(audit)
        out = self.root / "inspection"
        self.cli("inspect_case.py", "--project", self.root, "--source", "definition.md",
                 "--artifact-dir", "definition.playbook", "--slc", executable, "--out", out)
        report = json.loads((out / "summary.json").read_text())
        self.assertEqual(report["failed_phase"], "gears2fsm")
        self.assertEqual(report["audit"]["start"]["compiler"]["version"], "0.23.0")
        self.assertEqual(report["current_installation"]["version"], "99.0.0")
        self.assertEqual(report["audit"]["checker_calls"][0]["category"], "unverified")
        self.assertEqual(len(report["event_parse_errors"]), 1)
        self.assertFalse(report["verification"]["root_cause_confirmed"])
        self.assertFalse(marker.exists())
        self.assertEqual(sha(audit), before)
        self.assertEqual(report["files"][0]["sha256"], sha(source))
        self.cli("inspect_case.py", "--project", self.root, "--out", out, ok=False)

    def test_text_only_diagnosis_is_unknown_not_success(self):
        log = self.write("error.txt", 'slc: phase "link" failed\nBLOCKED: repository disposition cannot represent an uncommitted file update\n')
        out = self.root / "report"
        self.cli("inspect_case.py", "--project", self.root, "--log", log, "--out", out)
        r = json.loads((out / "summary.json").read_text())
        self.assertEqual(r["failed_phase"], "link")
        self.assertIsNone(r["audit"]["finish"])
        self.assertEqual([x["category"] for x in r["clues"]], ["repository-contract"])

    def test_environment_failure_does_not_invent_compilation_defect(self):
        log = self.write("network.txt", '✗ text2gears failed\nworkspace routing discovery timed out\n')
        out = self.root / "network-report"
        self.cli("inspect_case.py", "--project", self.root, "--log", log, "--out", out)
        r = json.loads((out / "summary.json").read_text())
        self.assertEqual(r["failed_phase"], "text2gears")
        self.assertEqual([x["category"] for x in r["clues"]], ["environment"])
        self.assertIsNone(r["current_installation"])

    def test_current_source_is_not_mistaken_for_historical_failed_input(self):
        source = self.write("definition.md", "updated description")
        original = self.write("definition.playbook/.slc/attempts/one/0001-input-definition.md", "original description")
        e = {"kind": "file-snapshot", "data": {"path": str(source),
             "snapshot": ".slc/attempts/one/0001-input-definition.md", "sha256": sha(original), "size": original.stat().st_size}}
        self.write("definition.playbook/.slc/attempts/one/events.jsonl", json.dumps(e))
        out = self.root / "source-check"
        self.cli("inspect_case.py", "--project", self.root, "--source", "definition.md",
                 "--artifact-dir", "definition.playbook", "--out", out)
        item = json.loads((out / "summary.json").read_text())["files"][0]
        self.assertIs(item["matches_recorded_input"], False)
        self.assertEqual(item["recorded_input"]["sha256"], sha(original))

    def test_attempt_selection_and_unknown_inputs(self):
        for name, phase in [("2026-01", "text2gears"), ("2026-02", "link")]:
            event = {"kind": "progress", "data": {"kind": "phase-fail", "phase": phase}}
            self.write(f"definition.playbook/.slc/attempts/{name}/events.jsonl", json.dumps(event))
        out = self.root / "latest"
        self.cli("inspect_case.py", "--project", self.root, "--artifact-dir", "definition.playbook", "--out", out)
        self.assertEqual(json.loads((out / "summary.json").read_text())["failed_phase"], "link")
        out = self.root / "explicit"
        self.cli("inspect_case.py", "--project", self.root, "--attempt",
                 "definition.playbook/.slc/attempts/2026-01", "--source", "missing.md", "--out", out)
        r = json.loads((out / "summary.json").read_text())
        self.assertEqual(r["failed_phase"], "text2gears")
        self.assertFalse(r["files"][0]["exists"])
        self.assertIsNone(r["audit"]["start"])

    def test_snapshot_copies_fixed_git_objects_ignores_dirty_files_and_detects_tampering(self):
        repo = self.root / "upstream"
        repo.mkdir()
        def git(*args):
            return subprocess.run(["git", "-C", str(repo), *args], check=True,
                                  text=True, capture_output=True, timeout=10).stdout.strip()
        git("init", "-q")
        git("config", "user.name", "Fixture")
        git("config", "user.email", "fixture@example.invalid")
        (repo / "package.json").write_text(json.dumps({"version": "1.0.0", "license": "Apache-2.0"}))
        (repo / "LICENSE").write_text("fixture license")
        (repo / "source.ts").write_text("original committed bytes")
        git("add", ".")
        git("commit", "-qm", "fixture")
        commit = git("rev-parse", "HEAD")
        (repo / "source.ts").write_text("dirty bytes must not be copied")
        selection = {"repositories": {name: {"url": "https://example.invalid/" + name,
                    "purpose": "fixture", "patterns": ["LICENSE", "package.json", "source.ts"]}
                    for name in ["slc", "playbook"]}}
        rules = self.write("selection.json", json.dumps(selection))
        destination = self.root / "snapshot"
        self.cli("sync_references.py", "--slc", repo, "--slc-ref", commit,
                 "--playbook", repo, "--playbook-ref", commit,
                 "--selection", rules, "--destination", destination)
        self.assertEqual((destination / "slc/source.ts").read_text(), "original committed bytes")
        self.cli("sync_references.py", "--verify", "--destination", destination)
        (destination / "slc/source.ts").write_text("tampered")
        self.cli("sync_references.py", "--verify", "--destination", destination, ok=False)
        self.assertEqual((repo / "source.ts").read_text(), "dirty bytes must not be copied")

    def test_distributed_snapshot_is_complete(self):
        self.cli("sync_references.py", "--verify")


if __name__ == "__main__":
    unittest.main()
