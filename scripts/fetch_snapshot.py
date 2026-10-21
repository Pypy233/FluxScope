"""Re-create a pinned corpus snapshot from upstream sources.

Reads ``<corpus dir>/snapshot.json``::

    {
      "repository": "https://github.com/org/repo.git",
      "commit": "<full sha>",
      "roots": ["src/Vault.sol", ...],                # analysed contracts
      "remappings": {"@org/": "src/org/"},            # repository-internal import prefixes
      "submodules": [                                 # git submodules, pinned by commit
        {"path": "lib/dep", "repository": "https://github.com/org/dep.git", "commit": "<full sha>"}
      ],
      "packages": [                                   # npm packages, pinned by tarball SHA-1
        {"prefix": "@openzeppelin/contracts/", "url": "https://registry.npmjs.org/...tgz",
         "sha1": "...", "dest": "lib/openzeppelin-contracts-5.3.0/"}
      ]
    }

clones the repository at the commit, downloads and verifies the packages, and
copies the transitive import closure of the roots (byte-identical). Writes
``snapshot.lock.json`` with the SHA-256 of every copied file. Requires git
and network access.

    python scripts/fetch_snapshot.py corpus/kaia-yield
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path

IMPORT = re.compile(r'import\s+(?:[^"\';]*?from\s+)?["\']([^"\']+)["\']', re.S)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("corpus", help="corpus directory containing snapshot.json")
    args = ap.parse_args()
    out = Path(args.corpus)
    spec = json.loads((out / "snapshot.json").read_text())
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        repo = tmp / "repo"
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "fetch", "-q", "--depth", "1", spec["repository"], spec["commit"]],
                       check=True)
        subprocess.run(["git", "-C", str(repo), "checkout", "-q", "FETCH_HEAD"], check=True)
        for sm in spec.get("submodules", []):
            d = repo / sm["path"]
            shutil.rmtree(d, ignore_errors=True)
            subprocess.run(["git", "init", "-q", str(d)], check=True)
            subprocess.run(["git", "-C", str(d), "fetch", "-q", "--depth", "1", sm["repository"], sm["commit"]], check=True)
            subprocess.run(["git", "-C", str(d), "checkout", "-q", "FETCH_HEAD"], check=True)
        repo = repo.resolve()
        pkgs = []
        for p in spec.get("packages", []):
            data = urllib.request.urlopen(p["url"]).read()
            got = hashlib.sha1(data).hexdigest()
            if got != p["sha1"]:
                raise SystemExit(f"checksum mismatch for {p['url']}: {got} != {p['sha1']}")
            d = tmp / "pkg" / p["dest"]
            with tarfile.open(fileobj=io.BytesIO(data)) as t:
                t.extractall(d, filter="data")
            pkgs.append((p["prefix"], (d / "package").resolve(), p["dest"]))
        internal = [(k, (repo / v).resolve()) for k, v in spec.get("remappings", {}).items()]

        def where(path: Path) -> str:
            for _, d, dest in pkgs:
                if path.is_relative_to(d):
                    return dest + path.relative_to(d).as_posix()
            return path.relative_to(repo).as_posix()

        def resolve(imp: str, cur: Path) -> Path:
            for prefix, d, _ in pkgs:
                if imp.startswith(prefix):
                    return d / imp[len(prefix):]
            for prefix, d in internal:
                if imp.startswith(prefix):
                    return d / imp[len(prefix):]
            return (cur.parent / imp).resolve()

        seen = {}
        stack = [(repo / r).resolve() for r in spec["roots"]]
        while stack:
            a = stack.pop()
            if a in seen:
                continue
            if not a.exists():
                raise SystemExit(f"unresolved import: {a}")
            seen[a] = where(a)
            for imp in IMPORT.findall(a.read_text()):
                stack.append(resolve(imp, a))
        lock = {}
        for a, rel in sorted(seen.items(), key=lambda x: x[1]):
            dst = out / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(a, dst)
            lock[rel] = hashlib.sha256(a.read_bytes()).hexdigest()
        (out / "snapshot.lock.json").write_text(json.dumps(
            {"repository": spec["repository"], "commit": spec["commit"], "files": lock}, indent=1) + "\n")
        print(f"copied {len(seen)} files to {out}")


if __name__ == "__main__":
    main()
