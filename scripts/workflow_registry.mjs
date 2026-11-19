// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 Boboyang

// Use the installed CLI's YAML and catalog validation for registration. The
// public add command builds new bindings in isolation before anything is saved.
import { createHash, randomUUID } from 'node:crypto';
import {
  chmodSync, closeSync, existsSync, linkSync, lstatSync, mkdirSync, mkdtempSync,
  openSync, readFileSync, realpathSync, renameSync, rmSync, writeFileSync,
} from 'node:fs';
import { createRequire } from 'node:module';
import { homedir, tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { isDeepStrictEqual } from 'node:util';

const pkg = process.argv[2];
const url = path => pathToFileURL(path).href;
const hash = value => createHash('sha256').update(value).digest('hex');

async function compilerConfig(host) {
  const { loadConfigFile } = await import(url(join(pkg, 'dist/config-file.js')));
  const { config } = await loadConfigFile({ cwd: process.cwd(), seed: false });
  const previous = config.agent;
  delete config.agent;
  delete config.model;
  config.modelDefaults = false;
  if (previous && previous !== host) {
    for (const key of ['effort', 'fastMode', 'ultracode', 'subagentModel']) delete config[key];
  }
  // The compiler writes into a per-run directory. Keep pipeline discovery
  // anchored to the user's project as with a direct invocation there.
  config.pipelinePath = (config.pipelinePath ?? ['./pipelines']).map(path => resolve(path));
  return { status: 'ready', config };
}

async function defaultModel(host) {
  const req = createRequire(join(pkg, 'package.json'));
  const directory = req.resolve.paths('@sublang/cligent')
    .map(path => join(path, '@sublang/cligent'))
    .find(path => existsSync(join(path, 'package.json')));
  if (!directory) throw Error('the selected CLI does not have Cligent installed');
  const metadata = JSON.parse(readFileSync(join(directory, 'package.json'), 'utf8'));
  const entry = metadata.exports?.['.'];
  const module = typeof entry === 'string' ? entry : entry?.import ?? entry?.default;
  if (typeof module !== 'string') throw Error('cannot resolve the installed Cligent model defaults');
  const { defaultAgentModels } = await import(url(resolve(directory, module)));
  return { status: 'ready', model: defaultAgentModels(host, process.env, homedir()).model ?? null };
}

async function readReplay(session) {
  const { createCaptainSessionStore } = await import(url(join(pkg, 'reference/sdlc/code.playbook/bin/session-store.js')));
  const store = createCaptainSessionStore({ createSessionsDir: false });
  const { entries } = await store.readStream(session, { afterSeq: 0 });
  return { status: 'ready', entries: entries.filter(({ record }) =>
    record.type === 'session_context' || record.type === 'turn_started' ||
    (record.type === 'captain_telemetry' && record.topic === 'playbook.trace' &&
      ['boss.input.received', 'boss.input.settled'].includes(record.payload?.type))) };
}

function snapshot(path) {
  if (!existsSync(path)) return { target: path, bytes: null, mode: 0o600 };
  const target = realpathSync(path);
  const info = lstatSync(target);
  if (!info.isFile()) throw Error(`Playbook config is not a regular file: ${path}`);
  return { target, bytes: readFileSync(target), mode: info.mode & 0o777 };
}

function publish(path, before, text) {
  mkdirSync(dirname(before.target), { recursive: true });
  const lock = before.target + '.add-lock'; // Also used by the upstream add command.
  const fd = openSync(lock, 'wx', 0o600);
  const temporary = before.target + '.' + randomUUID() + '.tmp';
  try {
    writeFileSync(temporary, text, { flag: 'wx', mode: before.mode });
    chmodSync(temporary, before.mode);
    const current = snapshot(path);
    if (current.target !== before.target || !isDeepStrictEqual(current.bytes, before.bytes)) {
      throw Error('Playbook config changed during registration; inspect it again');
    }
    if (before.bytes === null) linkSync(temporary, before.target);
    else renameSync(temporary, before.target);
  } finally {
    closeSync(fd);
    rmSync(temporary, { force: true });
    rmSync(lock, { force: true });
  }
}

async function registry() {
  const root = join(pkg, 'reference/sdlc/code.playbook');
  const req = createRequire(join(pkg, 'package.json'));
  const { parseDocument, isAlias, isMap } = req('yaml');
  const {
    normalizeLaunchPlan, resolveUserConfigPath, resolveSpexHome,
    resolveFormerUserConfigPaths, canonicalizeRegistrySpecifier,
  } = await import(url(join(root, 'bin/launch-config.js')));
  const { prepareConfiguredRegistries } = await import(url(join(root, 'bin/provision.js')));
  const configPath = resolveUserConfigPath();
  const original = snapshot(configPath);
  const fromFile = original.bytes?.toString('utf8');
  const readOnlyPrepare = prepareConfiguredRegistries({ enabled: false, stderr: process.stderr });

  function parse(text) {
    const doc = parseDocument(text);
    if (doc.errors.length || !isMap(doc.contents)) {
      throw Error(`invalid Playbook configuration: ${doc.errors[0]?.message ?? 'expected a map'}`);
    }
    return doc;
  }

  async function catalog(cfg, readOnly = true) {
    if (!Object.keys(cfg.playbooks ?? {}).length) return {};
    return (await normalizeLaunchPlan(cfg, {
      configPath,
      prepareRegistryModule: readOnly ? readOnlyPrepare : prepareConfiguredRegistries({ stderr: process.stderr }),
    })).catalog;
  }

  function entryLocation(block) {
    const specifier = canonicalizeRegistrySpecifier(block.from, configPath);
    return specifier.startsWith('file:') ? fileURLToPath(specifier) : specifier;
  }

  function stageAdd(executable, entry, adapter, model) {
    const directory = mkdtempSync(join(tmpdir(), 'spex-add-'));
    try {
      const stagedConfig = join(directory, 'config/playbook.config.yaml');
      mkdirSync(dirname(stagedConfig));
      writeFileSync(stagedConfig, JSON.stringify({ captain: adapter, players: {}, playbooks: {} }));
      const argv = ['add', entry, '--agent', adapter, '--model', model];
      const result = spawnSync(executable, argv, {
        env: { ...process.env, SPEX_HOME: directory }, encoding: 'utf8',
      });
      if (result.error) throw result.error;
      const add = { argv: [executable, ...argv], exit_code: result.status,
                    stdout: result.stdout, stderr: result.stderr };
      if (result.status !== 0) {
        throw Object.assign(Error(result.stderr.trim() || 'playbook add failed'), { details: { add } });
      }
      const cfg = parse(readFileSync(stagedConfig, 'utf8')).toJS();
      const [id] = Object.keys(cfg.playbooks);
      if (!id || Object.keys(cfg.playbooks).length !== 1) throw Error('playbook add returned no unique registration');
      return { id, cfg, add };
    } finally {
      rmSync(directory, { recursive: true, force: true });
    }
  }

  function aliasEntry(entry, name) {
    if (!/^[A-Za-z][A-Za-z0-9_-]*$/.test(name)) throw Error('invalid new slash command name');
    const directory = join(resolveSpexHome(), 'playbook-slc', 'entries');
    mkdirSync(directory, { recursive: true, mode: 0o700 });
    const path = join(directory, name + '-' + randomUUID() + '.mjs');
    const validator = url(join(pkg, 'src/artifact-environment.js'));
    const source = `import { validateArtifactManifest } from ${JSON.stringify(validator)};
await validateArtifactManifest(${JSON.stringify(entry)});
const { default: original } = await import(${JSON.stringify(url(entry))});
export default { ...original, id: ${JSON.stringify(name)}, command: ${JSON.stringify(name)} };
`;
    writeFileSync(path, source, { flag: 'wx', mode: 0o600 });
    return path;
  }

  function detachMap(doc, prior, key) {
    const node = doc.get(key, true);
    if (node === undefined) doc.set(key, doc.createNode({}));
    else if (isAlias(node)) doc.set(key, doc.createNode(prior[key]));
    if (!isMap(doc.get(key, true))) throw Error(`Playbook config ${key} must be a map`);
  }

  return async function perform(request) {
    if (request.operation === 'lookup') {
      if (fromFile === undefined) throw Error('no existing Playbook registry');
      const cfg = parse(fromFile).toJS();
      const match = Object.values(await catalog(cfg)).find(item => item.command === request.name);
      if (!match) throw Error(`no registered Playbook command /${request.name}`);
      return { status: 'found', name: match.command, id: match.id, entry: entryLocation(cfg.playbooks[match.id]) };
    }

    if (!fromFile && resolveFormerUserConfigPaths(process.env, homedir()).some(existsSync)) {
      throw Error('a former Playbook config exists; relocate it with playbook --list before registering');
    }
    const { entry, executable, adapter, model, decision, token, name } = request;
    const currentDoc = parse(fromFile ?? readFileSync(join(root, 'playbook.config.template.yaml'), 'utf8'));
    const prior = currentDoc.toJS();
    const existing = await catalog(prior);
    const entryHash = hash(readFileSync(entry));
    const staged = stageAdd(executable, entry, adapter, model);
    const candidate = staged.cfg.playbooks[staged.id];
    const collisions = Object.values(existing)
      .filter(item => item.id === staged.id || item.command === candidate.command);
    const nextToken = hash(JSON.stringify([configPath, original.bytes && hash(original.bytes), entry, entryHash, adapter, model]));
    if (decision && token !== nextToken) throw Error('registration changed after confirmation; inspect the conflict again');

    const matched = collisions.map(item => ({ id: item.id, name: item.command, entry: entryLocation(prior.playbooks[item.id]) }));
    if (collisions.length && !decision) {
      return { status: 'conflict', previous: matched, proposed: { id: staged.id, name: candidate.command, entry },
               options: ['reuse', 'replace', 'rename'], token: nextToken };
    }
    if (decision && !collisions.length) throw Error('registration no longer conflicts; inspect it again');
    if (decision === 'reuse' || decision === 'replace') {
      if (matched.length !== 1) throw Error('multiple registrations conflict; choose a distinct name');
      if (decision === 'reuse') return { ...matched[0], status: 'reused' };
    }

    let alias;
    try {
      if (decision === 'rename') {
        if (Object.values(existing).some(item => item.id === name || item.command === name)) {
          throw Error(`new name /${name} already exists`);
        }
        alias = aliasEntry(entry, name);
      }
      const chosen = alias ? stageAdd(executable, alias, adapter, model) : staged;
      const id = chosen.id;
      const block = chosen.cfg.playbooks[id];
      const expected = structuredClone(prior);
      for (const key of ['playbooks', 'workflowCaptains', 'players']) {
        detachMap(currentDoc, prior, key);
        expected[key] ??= {};
      }
      if (decision === 'replace') {
        const old = matched[0];
        block.command = old.name;
        for (const key of ['playbooks', 'workflowCaptains']) {
          currentDoc.deleteIn([key, old.id]);
          delete expected[key][old.id];
        }
      }
      const occupied = new Set(Object.keys(expected.players));
      for (const [role, proposed] of Object.entries(block.roles)) {
        let player = proposed;
        for (let serial = 2; occupied.has(player); serial++) player = proposed + '-' + serial;
        occupied.add(player);
        block.roles[role] = player;
        const value = chosen.cfg.players[proposed];
        currentDoc.setIn(['players', player], value);
        Object.defineProperty(expected.players, player, { value, enumerable: true });
      }
      const captain = chosen.cfg.workflowCaptains[id];
      for (const [key, value] of [['playbooks', block], ['workflowCaptains', captain]]) {
        currentDoc.setIn([key, id], value);
        Object.defineProperty(expected[key], id, { value, enumerable: true, configurable: true });
      }
      const text = currentDoc.toString();
      if (!isDeepStrictEqual(parse(text).toJS(), expected)) throw Error('proposed registration changes unrelated config');
      const checked = await catalog(expected, false);
      if (!checked[id]) throw Error('new registration failed catalog validation');
      if (hash(readFileSync(entry)) !== entryHash) throw Error('entry changed during registration');
      publish(configPath, original, text);
      const saved = alias;
      alias = undefined;
      return { status: 'registered', id, name: checked[id].command, entry: block.from,
               source_entry: entry, config: configPath, decision: decision ?? 'new',
               ...(saved ? { alias_entry: saved } : {}), add: chosen.add };
    } finally {
      if (alias) rmSync(alias, { force: true });
    }
  };
}

try {
  let input = '';
  for await (const part of process.stdin) input += part;
  const request = JSON.parse(input);
  let result;
  if (request.operation === 'compiler-config') result = await compilerConfig(request.host);
  else if (request.operation === 'default-model') result = await defaultModel(request.host);
  else if (request.operation === 'read-replay') result = await readReplay(request.session);
  else result = await (await registry())(request);
  process.stdout.write(JSON.stringify(result) + '\n');
} catch (error) {
  process.stdout.write(JSON.stringify({ status: 'failed', error: error.message, details: error.details }) + '\n');
}
