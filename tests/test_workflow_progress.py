# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Boboyang
"""Check progress delivery and guard result attribution without running the toolchain."""
import copy
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from workflow_cli import Commands, WorkflowError
from workflow_progress import CompileProgress, terminal_evidence


class WorkflowProgressTest(unittest.TestCase):
    def test_wait_progress_uses_clock_and_keeps_the_last_saved_stage(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            artifact = root / "flow.gears.raw.md"
            artifact.touch()
            clock = [0]
            messages = []
            progress = CompileProgress(lambda event, text, **details: messages.append((event, text, details)),
                                       root / "flow.md", root, 700, combined=True, clock=lambda: clock[0])
            progress.feed(f"→ text2gears (writing {artifact})")
            progress.feed(f"✓ text2gears wrote {artifact} (10m01s)")
            progress.feed(f"→ prefix (writing {root / 'flow.gears.md'})")
            progress.feed("… prefix still running (15m48s)")
            progress.tick(1, 0)
            self.assertNotIn("waiting", [event for event, _, _ in messages])
            clock[0] = 60
            progress.tick(1, 0)
            progress.tick(1, 0)
            progress.timed_out()
            self.assertEqual([event for event, _, _ in messages].count("waiting"), 1)
            event, text, details = messages[-1]
            self.assertEqual(event, "compile-timeout")
            self.assertEqual(details["completed"], {"text2gears": str(artifact)})
            self.assertIn("登记和运行均未开始", text)
            self.assertNotIn("15m48s", text)

    def test_observer_keeps_real_subprocess_timeout_and_raw_receipt(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            with (root / "progress.jsonl").open("w") as stream:
                commands = Commands(root, 2, stream)
                progress = CompileProgress(commands.emit, root / "flow.md", root, 2)
                statement = '''
import sys
import time
from pathlib import Path
print("→ prefix (writing flow.gears.md)", file=sys.stderr, flush=True)
while 'compile-phase' not in Path('progress.jsonl').read_text():
    time.sleep(.02)
Path('observed-before-exit').touch()
time.sleep(8)
'''
                with self.assertRaises(WorkflowError) as error:
                    commands.invoke("compile", [sys.executable, "-c", statement], observer=progress)
                self.assertTrue(error.exception.result["timed_out"])
                self.assertTrue((root / "observed-before-exit").is_file())
                self.assertIn("→ prefix", commands.records[0]["stderr"])
                self.assertEqual(progress.phase, "prefix")
                events = [json.loads(line) for line in (root / "progress.jsonl").read_text().splitlines()]
                self.assertEqual([event["event"] for event in events], ["compile-start", "compile-phase"])

    def test_requires_the_root_workflow_terminal_for_the_same_input_and_turn(self):
        entry, input_file = "/project/flow.mjs", "/project/orders data.csv"
        command = f"/flow {input_file}"
        trace = {"playbookId": "flow", "rootSessionId": "runtime", "sessionId": "runtime", "depth": 0, "turnId": 1}
        records = [
            {"type": "session_context", "configuration": {"scopedWorkflowId": "flow", "catalog": {"flow": {"from": entry}}}},
            {"type": "turn_started", "turn": {"id": 1, "prompt": command}},
            {"type": "captain_telemetry", "contextSeq": 1, "turnId": 1,
             "payload": {**trace, "type": "boss.input.received", "payload": {"text": input_file}}},
            {"type": "captain_telemetry", "contextSeq": 1, "turnId": 1,
             "payload": {**trace, "type": "boss.input.settled",
                         "payload": {"outcome": "terminal", "terminal": {"kind": "success"}, "output": {"total": 30}}}},
        ]
        def check(replay):
            entries = [{"seq": i, "record": record} for i, record in enumerate(replay, 1)]
            return terminal_evidence(entries, "logical-session", "flow", entry, command, input_file)

        self.assertEqual(check(records)["output"], {"total": 30})
        self.assertIsNone(check(records[:-1]))
        for key, value in {"depth": 1, "turnId": 2, "sessionId": "another-runtime"}.items():
            altered = copy.deepcopy(records)
            altered[-1]["payload"][key] = value
            self.assertIsNone(check(altered))
        altered = copy.deepcopy(records)
        altered[2]["payload"]["payload"]["text"] = "/project/another file.csv"
        self.assertIsNone(check(altered))
