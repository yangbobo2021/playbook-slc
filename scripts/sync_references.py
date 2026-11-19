#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Boboyang
"""Copy selected, committed upstream files; never fetch or execute upstream code."""
import argparse
import fnmatch
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=30).stdout


def verify(snapshot):
    if snapshot.is_symlink():
        raise ValueError("snapshot root cannot be a symlink")
    manifest = json.loads((snapshot / "manifest.json").read_text())
    if manifest.get("schema") != "playbook-slc.upstream.v1":
        raise ValueError("unsupported manifest schema")
    expected = {"manifest.json"}
    for repo in manifest["repositories"]:
        for item in repo["files"]:
            rel = PurePosixPath(repo["name"]) / item["path"]
            if rel.is_absolute() or ".." in rel.parts:
                raise ValueError("invalid manifest path")
            path = snapshot / str(rel)
            if any(snapshot.joinpath(*rel.parts[:i]).is_symlink() for i in range(1, len(rel.parts) + 1)):
                raise ValueError(f"symlink in snapshot: {rel}")
            data = path.read_bytes()
            if digest(data) != item["sha256"] or len(data) != item["bytes"]:
                raise ValueError(f"snapshot mismatch: {rel}")
            expected.add(str(rel))
    actual = {str(p.relative_to(snapshot)) for p in snapshot.rglob("*") if p.is_file()}
    if actual != expected:
        raise ValueError(f"unexpected/missing files: {sorted(actual ^ expected)}")
    return {"verified_files": len(expected) - 1, "repositories": len(manifest["repositories"])}


def sync(args):
    selection_data = args.selection.read_bytes()
    selection = json.loads(selection_data)
    if set(selection["repositories"]) != {"slc", "playbook"}:
        raise ValueError("selection must contain exactly slc and playbook")
    args.destination.parent.mkdir(parents=True, exist_ok=True)
    if args.destination.is_symlink():
        raise ValueError("destination cannot be a symlink")
    temp = Path(tempfile.mkdtemp(prefix=".upstream-", dir=args.destination.parent))
    manifest = {"schema": "playbook-slc.upstream.v1",
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "selection_sha256": digest(selection_data), "repositories": []}
    try:
        for name, rules in selection["repositories"].items():
            repo = getattr(args, name)
            if repo is None:
                raise ValueError(f"--{name} is required")
            ref = getattr(args, name + "_ref")
            # Resolve all contents from one immutable Git object, not the worktree.
            commit = git(repo, "rev-parse", "--verify", ref + "^{commit}").decode().strip()
            files = git(repo, "ls-tree", "-rz", "--name-only", commit).decode().split("\0")
            chosen = sorted(p for p in files if p and any(fnmatch.fnmatchcase(p, pattern)
                            for pattern in rules["patterns"]))
            if "LICENSE" not in chosen or "package.json" not in chosen:
                raise ValueError(f"{name}: LICENSE/package.json missing from selection")
            package = json.loads(git(repo, "show", f"{commit}:package.json"))
            record = {"name": name, "url": rules["url"], "requested_ref": ref,
                      "commit": commit, "version": package["version"],
                      "license": package.get("license"), "purpose": rules["purpose"], "files": []}
            if record["license"] != "Apache-2.0":
                raise ValueError(f"{name}: license changed; review redistribution first")
            for rel in chosen:
                if PurePosixPath(rel).is_absolute() or ".." in PurePosixPath(rel).parts:
                    raise ValueError("unsafe Git path")
                data = git(repo, "show", f"{commit}:{rel}")
                target = temp / name / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                record["files"].append({"path": rel, "sha256": digest(data), "bytes": len(data),
                                        "url": f"{rules['url']}/blob/{commit}/{rel}"})
            manifest["repositories"].append(record)
        (temp / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        result = verify(temp)
        # Roll back if replacement fails; never remove a valid snapshot first.
        prior = None
        if args.destination.exists():
            prior = Path(tempfile.mkdtemp(prefix=".previous-", dir=args.destination.parent))
            prior.rmdir()
            os.replace(args.destination, prior)
        try:
            os.replace(temp, args.destination)
        except BaseException:
            if prior is not None:
                os.replace(prior, args.destination)
            raise
        if prior is not None:
            shutil.rmtree(prior)
        return result
    finally:
        if temp.exists():
            shutil.rmtree(temp)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--slc", type=Path)
    p.add_argument("--playbook", type=Path)
    p.add_argument("--slc-ref", default="origin/main")
    p.add_argument("--playbook-ref", default="origin/main")
    p.add_argument("--selection", type=Path, default=ROOT / "references/snapshot-selection.json")
    p.add_argument("--destination", type=Path, default=ROOT / "references/upstream")
    p.add_argument("--verify", action="store_true")
    args = p.parse_args()
    try:
        result = verify(args.destination) if args.verify else sync(args)
        print(json.dumps(result, ensure_ascii=False))
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        p.exit(1, f"snapshot error: {exc}\n")


if __name__ == "__main__":
    main()
