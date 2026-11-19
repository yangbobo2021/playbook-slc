#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Boboyang
"""Make an immutable-by-convention, hashed local backup before editing a case."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

EXCLUDE = {".git", "node_modules", ".venv", "__pycache__", ".playbook-slc"}


def backup(root, out, inputs):
    root = root.resolve(strict=True)
    out = out.absolute()
    if out.exists() or out.is_symlink():
        raise ValueError("backup destination already exists")
    records, missing, skipped, paths = [], [], [], {}
    for item in inputs:
        path = Path(item) if Path(item).is_absolute() else root / item
        # Keep lexical identity: resolving a symlink before checking would follow it.
        path = Path(os.path.abspath(path))
        try:
            rel = path.relative_to(root)
        except ValueError:
            raise ValueError(f"input outside project: {item}")
        if any((root.joinpath(*rel.parts[:i])).is_symlink() for i in range(1, len(rel.parts) + 1)):
            raise ValueError(f"explicit input contains symlink: {item}")
        if out.resolve().is_relative_to(path.resolve()):
            raise ValueError("backup destination cannot be inside an input")
        if any(part in EXCLUDE for part in rel.parts):
            raise ValueError(f"explicit input is excluded: {item}")
        if not path.exists():
            missing.append(str(rel))
            continue
        candidates = [path] if path.is_file() else path.rglob("*")
        for f in candidates:
            relative = f.relative_to(root)
            if any(part in EXCLUDE for part in relative.parts):
                continue
            if f.is_symlink():
                skipped.append(str(relative))
                continue
            if f.is_file():
                paths[str(relative)] = f
    out.mkdir(parents=True, exist_ok=False)
    try:
        for relative, src in sorted(paths.items()):
            target = out / "files" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            data = src.read_bytes()
            target.write_bytes(data)
            if target.read_bytes() != data:
                raise OSError(f"backup verification failed: {relative}")
            records.append({"path": relative, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        manifest = {"schema": "playbook-slc.backup.v1", "created_at": datetime.now(timezone.utc).isoformat(),
                    "project": str(root), "files": records, "missing": missing,
                    "skipped_symlinks": skipped, "excluded_names": sorted(EXCLUDE)}
        (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        return manifest
    except BaseException:
        # A partial backup must not look like a completed one.
        (out / "INCOMPLETE").write_text("Backup failed. Do not edit originals on the strength of this backup.\n")
        raise


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("paths", nargs="+")
    a = p.parse_args()
    try:
        record = backup(a.root, a.out, a.paths)
        print(json.dumps({"backup": str(a.out), "files": len(record["files"]),
                          "missing": record["missing"]}, ensure_ascii=False))
    except (OSError, ValueError) as exc:
        p.exit(1, f"backup error: {exc}\n")


if __name__ == "__main__":
    main()
