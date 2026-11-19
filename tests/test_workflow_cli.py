# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Boboyang
"""Offline CLI behavior through the Skill command's public entry point."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/workflow_cli.py"
INSTALLED_PLAYBOOK = shutil.which("playbook")

TOOL = """
from pathlib import Path
import json, os, sys
prefix = Path(os.environ['npm_config_prefix'])
args = sys.argv[1:]
command = Path(sys.argv[0]).name.removesuffix('.py')
with (prefix / 'calls.jsonl').open('a') as log:
    log.write(json.dumps([command, args, os.environ.get('SLC_AGENT'), os.environ.get('SLC_MODEL')]) + '\\n')
if command == 'npm':
    if args == ['root','-g']:
        print(prefix / 'lib/node_modules')
    else:
        if os.environ.get('TEST_MODE') == 'npm-failed':
            print('npm install failed', file=sys.stderr)
            sys.exit(1)
        assert args[:2] == ['install','-g']
        package, _, version = args[2].rpartition('@')
        root = prefix / 'lib/node_modules' / package
        root.mkdir(parents=True, exist_ok=True)
        (root/'package.json').write_text(json.dumps({'name':package,'version':version}))
        target = root/(package.split('/')[-1]+'.py')
        target.write_text(Path(__file__).read_text())
        target.chmod(0o755)
        link = prefix/'bin'/package.split('/')[-1]
        link.unlink(missing_ok=True)
        link.symlink_to(target)
elif command == 'slc':
    if os.environ.get('TEST_MODE') == 'clarification':
        print('SLC_CLARIFICATION: {"phase":"text2gears","questions":[{"id":"q1"}]}',file=sys.stderr)
        sys.exit(2)
    if os.environ.get('TEST_MODE') == 'failed':
        print('historical failure',file=sys.stderr)
        sys.exit(1)
    if os.environ.get('TEST_MODE') != 'missing-entry':
        if args[2] == 'playbook.text2gears':
            gears = Path('flow.playbook/flow.gears.md')
            gears.parent.mkdir(exist_ok=True)
            gears.write_text('# GEARS')
            print(gears)
        else:
            artifact_dir = Path('flow.playbook')
            artifact_dir.mkdir(exist_ok=True)
            (artifact_dir / 'package.json').write_text('{"type":"module"}')
            entry = Path('flow.mjs')
            entry.write_text('export default {};')
            print(entry)
elif command == 'node':
    req = json.load(sys.stdin)
    if req['operation'] == 'compiler-config':
        print(json.dumps({'status':'ready','config':{}}))
    elif req['operation'] == 'default-model':
        print(json.dumps({'status':'ready','model':None}))
    elif req['operation'] == 'lookup':
        request = prefix/'registry-request.json'
        entry = json.loads(request.read_text())['entry'] if request.is_file() else 'saved entry.mjs'
        print(json.dumps({'status':'found','name':req['name'],'entry':entry}))
    else:
        with (prefix/'registry-request.json').open('w') as f: json.dump(req,f)
        if os.environ.get('TEST_MODE') == 'conflict':
            print(json.dumps({'status':'conflict','token':'token','previous':[{'entry':'old.mjs'}],
                              'proposed':{'entry':req['entry']},'options':['reuse','replace','rename']}))
        elif os.environ.get('TEST_MODE') == 'register-failed':
            print(json.dumps({'status':'failed','error':'invalid registration'}))
        else:
            print(json.dumps({'status':'registered','name':'real-name','entry':req['entry']}))
elif command == 'playbook':
    if os.environ.get('TEST_MODE') == 'run-failed':
        print('run stopped',file=sys.stderr)
        sys.exit(1)
    print(json.dumps({'sessionId':'session','reply':'verified'}))
elif command == 'ps':
    sys.exit(1)
"""


@unittest.skipIf(os.name == "nt", "offline fixture uses Unix executable links")
class WorkflowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="playbook-slc-cli-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / "project with spaces"
        self.project.mkdir()
        self.prefix = self.root / "npm"
        self.bin = self.prefix / "bin"
        self.bin.mkdir(parents=True)
        self.source = self.project / "flow.md"
        self.source.write_text("# Fix input\n")
        self.data = self.project / "orders data.csv"
        self.data.write_text("order_id,amount\nA001,12\n")
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
                        npm_config_prefix=str(self.prefix), CODEX_THREAD_ID="recorded-session",
                        CODEX_HOME=str(self.root / "codex-config"),
                        CLAUDE_CONFIG_DIR=str(self.root / "claude-config"))
        for key in ("CLAUDECODE", "ANTHROPIC_MODEL", "SLC_AGENT", "SLC_MODEL"):
            self.env.pop(key, None)
        for name in ("npm", "ps", "node"):
            self.write_tool(self.bin / name)

    def write_tool(self, path):
        path.write_text(f"#!{sys.executable}\n" + TOOL)
        path.chmod(0o755)

    def package(self, name, version):
        root = self.prefix / "lib/node_modules/@sublang" / name
        root.mkdir(parents=True, exist_ok=True)
        (root / "package.json").write_text(json.dumps({"name": f"@sublang/{name}", "version": version}))
        executable = root / (name + ".py")
        self.write_tool(executable)
        (self.bin / name).symlink_to(executable)

    def cli(self, *args, code=0, env=None):
        result = subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], cwd=self.project,
                                env=env or self.env, capture_output=True, text=True, timeout=40)
        self.assertEqual(result.returncode, code, (result.stdout, result.stderr))
        return json.loads(result.stdout)

    def calls(self):
        file = self.prefix / "calls.jsonl"
        return list(map(json.loads, file.read_text().splitlines())) if file.exists() else []

    def test_install_missing_and_reuse_minimum_or_higher(self):
        self.package("slc", "0.24.0")
        result = self.cli("install")["result"]
        self.assertEqual(result["tools"]["slc"]["version"], "0.24.0")
        self.assertEqual(result["tools"]["playbook"]["version"], "19.1.0")
        calls = self.calls()
        self.assertEqual(calls, [["npm", ["install", "-g", "@sublang/playbook@19.1.0"], None, None]])
        self.cli("install")
        self.assertEqual(self.calls(), calls)

    def test_compile_installs_only_missing_compiler_and_stops_on_install_failure(self):
        intent = ["--session-model", "gpt-6.2-sol", "compile", self.source]
        failed = self.cli(*intent, code=1, env=dict(self.env, TEST_MODE="npm-failed"))
        self.assertEqual(failed["result"]["stage"], "installation")
        self.assertEqual([command[0] for command in self.calls() if command[0] != "ps"], ["npm"])
        result = self.cli(*intent)
        self.assertEqual(result["result"]["status"], "compiled")
        self.assertEqual([command[0] for command in self.calls() if command[0] != "ps"],
                         ["npm", "npm", "node", "slc"])
        self.assertFalse((self.bin / "playbook").exists())

    def test_upgrade_waits_and_honors_exact_prior_version(self):
        self.package("slc", "0.22.0")
        self.package("playbook", "19.1.0")
        pending = self.cli("install", code=2)
        self.assertEqual(pending["result"]["status"], "upgrade-confirmation-required")
        self.assertEqual(pending["result"]["tools"]["slc"]["version"], "0.22.0")
        self.cli("--approve-upgrade", "slc=0.20.0", "install", code=2)
        self.assertEqual(self.calls(), [])
        ready = self.cli("--approve-upgrade", "slc=0.22.0", "install")
        self.assertEqual(ready["result"]["tools"]["slc"]["version"], "0.23.0")
        self.assertEqual([c[1] for c in self.calls()], [["root", "-g"], ["install", "-g", "@sublang/slc@0.23.0"]])

    def test_compile_only_returns_the_entry_in_the_selected_working_directory(self):
        self.package("slc", "0.23.0")
        target = self.root / "target with spaces"
        target.mkdir()
        first = self.cli("--cwd", target, "--session-model", "gpt-6.2-sol", "compile", self.source)
        second = self.cli("--cwd", target, "compile", self.source)
        for receipt in (first, second):
            self.assertEqual(receipt["result"]["status"], "compiled")
            self.assertEqual(Path(receipt["result"]["entry"]), target / "flow.mjs")
            invocation = receipt["commands"][-1]
            self.assertEqual(Path(invocation["cwd"]), target)
            self.assertEqual(invocation["argv"][-2:], ["playbook", str(self.source)])
        self.assertTrue((target / "flow.playbook/package.json").is_file())
        self.assertFalse((target / ".playbook-slc").exists())
        self.assertFalse((self.project / "flow.mjs").exists())
        self.assertEqual([x[2:] for x in self.calls() if x[0] == "slc"],
                         [["codex", "gpt-6.2-sol"], ["codex", None]])
        self.assertFalse(any(x[0] in {"npm", "playbook"} for x in self.calls()))

    def test_gears_only_resolves_either_host_and_returns_the_reported_file(self):
        self.package("slc", "0.23.0")
        claude = dict(self.env, CLAUDECODE="1", ANTHROPIC_MODEL="claude-configured")
        claude.pop("CODEX_THREAD_ID")
        for host, model, env in (("codex", None, self.env), ("claude-code", "claude-configured", claude)):
            with self.subTest(host=host):
                target = self.root / "target with spaces"
                target.mkdir(exist_ok=True)
                receipt = self.cli("--cwd", target, "gears", self.source, env=env)
                result = receipt["result"]
                self.assertEqual(result["status"], "gears-generated")
                self.assertEqual(receipt["stages"]["selection"]["host"], host)
                self.assertNotIn("entry", result)
                self.assertEqual(Path(result["gears"]).read_text(), "# GEARS")
                self.assertEqual(Path(result["gears"]), target / "flow.playbook/flow.gears.md")
                self.assertEqual(Path(receipt["commands"][-1]["cwd"]), target)
                compiler = [call for call in self.calls() if call[0] == "slc"][-1]
                self.assertEqual(compiler[1][-2:], ["playbook.text2gears", str(self.source)])
                self.assertEqual(compiler[2:], [host, model])
        self.assertEqual(len([call for call in self.calls() if call[0] == "slc"]), 2)
        self.assertFalse(any(call[0] in {"npm", "playbook"} for call in self.calls()))

    def test_gears_only_requires_a_reported_file(self):
        self.package("slc", "0.23.0")
        receipt = self.cli("gears", self.source, code=1, env=dict(self.env, TEST_MODE="missing-entry"))
        self.assertEqual(receipt["result"]["error"], "SLC did not report exactly one GEARS file")
        self.assertNotIn("gears", receipt["result"])

    def test_combined_uses_claude_config_and_actual_registration(self):
        self.package("slc", "0.23.0")
        self.package("playbook", "19.1.0")
        env = dict(self.env, CLAUDECODE="1", ANTHROPIC_MODEL="glm-5.3-flash")
        env.pop("CODEX_THREAD_ID")
        target = self.root / "target with spaces"
        target.mkdir()
        result = self.cli("--cwd", target, "workflow", self.source, self.data, env=env)
        self.assertEqual(result["stages"]["selection"], {"host":"claude-code","model":"glm-5.3-flash","model_source":"host-config"})
        registry = json.loads((self.prefix/"registry-request.json").read_text())
        self.assertEqual(registry["adapter"], "claude")
        self.assertEqual(registry["model"], "glm-5.3-flash")
        self.assertEqual(registry["entry"], result["stages"]["compile"]["entry"])
        self.assertEqual(Path(registry["entry"]), target / "flow.mjs")
        self.assertTrue((target / "flow.playbook/package.json").is_file())
        invocation, = [c for c in result["commands"] if c["stage"] == "compile"]
        self.assertEqual(Path(invocation["cwd"]), target)
        self.assertEqual([x for x in self.calls() if x[0] == "playbook"][0][1],
                         ["run", "--json", "--", f"/real-name {self.data}"])
        self.assertEqual(result["result"]["reply"], "verified")

    def test_combined_resolves_current_host_and_both_model_sources(self):
        self.package("slc", "0.23.0")
        self.package("playbook", "19.1.0")
        self.env["CODEX_HOME"] = str(self.root / "codex-config")
        config_dir = Path(self.env["CODEX_HOME"])
        config_dir.mkdir()
        (config_dir / "config.toml").write_text('model = "codex-configured"\n')
        for host, adapter in (("codex", "codex"), ("claude-code", "claude")):
            env = dict(self.env)
            if host == "claude-code":
                env.pop("CODEX_THREAD_ID")
                env.update(CLAUDECODE="1", ANTHROPIC_MODEL="claude-configured")
            for from_session in (False, True):
                with self.subTest(host=host, from_session=from_session):
                    model = f"{adapter}-session" if from_session else f"{adapter}-configured"
                    options = ["--session-model", model] if from_session else []
                    result = self.cli(*options, "workflow", self.source, self.data, env=env)
                    self.assertEqual(result["stages"]["selection"], {
                        "host": host, "model": model, "model_source": "session" if from_session else "host-config",
                    })
                    registry = json.loads((self.prefix / "registry-request.json").read_text())
                    self.assertEqual((registry["adapter"], registry["model"]), (adapter, model))
                    self.assertEqual([x for x in self.calls() if x[0] == "slc"][-1][2:], [host, model])

    def test_does_not_start_downstream_after_failure_or_conflict(self):
        self.package("slc", "0.23.0")
        self.package("playbook", "19.1.0")
        for kind, status, code in [
            ("clarification", "clarification-required", 2), ("failed", "failed", 1),
            ("missing-entry", "failed", 1), ("conflict", "conflict", 2),
            ("register-failed", "failed", 1),
        ]:
            with self.subTest(kind=kind):
                result = self.cli("--session-model", "gpt-6.2-sol", "workflow", self.source, self.data,
                                  code=code, env=dict(self.env, TEST_MODE=kind))
                self.assertEqual(result["result"]["status"], status)
                self.assertFalse(any(x[0] == "playbook" for x in self.calls()))
                if kind in {"conflict","register-failed"}:
                    self.assertEqual(result["stages"]["compile"]["status"], "compiled")
        result = self.cli("--session-model", "gpt-6.2-sol", "workflow", self.source, self.data,
                          code=1, env=dict(self.env, TEST_MODE="run-failed"))
        self.assertEqual(result["result"]["stage"], "run")
        self.assertEqual(result["stages"]["register"]["status"], "registered")

    def test_run_only_does_not_require_current_host_or_touch_registration(self):
        self.package("playbook", "19.1.0")
        env = dict(self.env)
        env.pop("CODEX_THREAD_ID")
        result = self.cli("run", "/real-name", self.data, env=env)
        self.assertEqual(result["result"]["entry"], "saved entry.mjs")
        self.assertEqual(result["stages"], {})
        self.assertFalse((self.prefix/"registry-request.json").exists())

    def test_run_rejects_a_registration_changed_since_selection(self):
        self.package("playbook", "19.1.0")
        result = self.cli("run", "/real-name", self.data, "--expected-entry", "old.mjs", code=1)
        self.assertEqual(result["result"]["current_entry"], "saved entry.mjs")
        self.assertFalse(any(x[0] == "playbook" for x in self.calls()))

    @unittest.skipUnless(INSTALLED_PLAYBOOK and shutil.which("node"), "installed Playbook and Node needed for offline catalog checks")
    def test_real_catalog_reuse_replace_rename_and_stale_confirmation(self):
        (self.bin / "node").unlink()
        self.env.pop("npm_config_prefix")
        self.env.pop("NPM_CONFIG_PREFIX", None)
        self.env["SPEX_HOME"] = str(self.root / "registry")
        config = Path(self.env["SPEX_HOME"]) / "config/playbook.config.yaml"
        config.parent.mkdir(parents=True)
        config.write_text("# Keep this comment\n" + json.dumps({"captain":"codex","players":{},"playbooks":{}}))
        entries = []
        for label in ("old", "new"):
            entry = self.project / (label + ".mjs")
            entry.write_text("""
export default {id:"flow", command:"flow", intent:"fixture", artifactSchema:3,
runtimeProfile:{kind:"bespoke",artifactSchema:3},requiredRoleIds:["checker"],
concurrentRoleSets:[], validateOptions() { return {}; },
createRuntime() { throw Error("offline registration must not execute workflow"); }};
""")
            entries.append(entry)
        old, new = entries
        first = self.cli("register", old)["result"]
        self.assertEqual(first["name"], "flow")
        baseline = config.read_bytes()
        conflict = self.cli("register", new, code=2)["result"]
        self.assertEqual(conflict["previous"][0]["entry"], str(old))
        self.assertEqual(conflict["proposed"]["entry"], str(new))
        self.assertEqual(config.read_bytes(), baseline)
        used = self.cli("register", new, "--collision", "reuse", "--token", conflict["token"])["result"]
        self.assertEqual(used["entry"], str(old))
        self.assertEqual(config.read_bytes(), baseline)
        renamed = self.cli("register", new, "--collision", "rename", "--name", "other-flow",
                           "--token", conflict["token"])["result"]
        self.assertEqual(renamed["name"], "other-flow")
        self.assertTrue(Path(renamed["alias_entry"]).is_file())
        after_rename = config.read_bytes()
        self.assertIn(b"# Keep this comment", after_rename)
        self.assertEqual(self.cli("register", new, "--collision", "replace", "--token",
                                  conflict["token"], code=1)["result"]["stage"], "register")
        self.assertEqual(config.read_bytes(), after_rename)
        conflict = self.cli("register", new, code=2)["result"]
        self.assertEqual(conflict["previous"][0]["entry"], str(old))
        replaced = self.cli("register", new, "--collision", "replace", "--token", conflict["token"])["result"]
        self.assertEqual(replaced["name"], "flow")
        self.assertEqual(replaced["entry"], str(new))
        listing = subprocess.check_output([INSTALLED_PLAYBOOK,"--list","--no-provision"],
                                          cwd=self.project,env=self.env,text=True,timeout=25)
        self.assertIn("/flow ", listing)
        self.assertIn("/other-flow ", listing)
