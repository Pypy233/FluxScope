"""Re-create corpus/beefy-erc4626 from upstream sources.

Clones beefy-contracts at the pinned commit, downloads the OpenZeppelin npm
tarballs pinned by its yarn.lock (verifying their SHA-1), and copies the
transitive import closure of the analysed contracts. Requires git and network.

    python scripts/fetch_beefy_snapshot.py [--out corpus/beefy-erc4626]
"""

from __future__ import annotations

import argparse
import hashlib
import io
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path

REPO = "https://github.com/beefyfinance/beefy-contracts.git"
COMMIT = "e33f868e2d995c2db9219029c26d31cd8e7d5cb2"
PACKAGES = {
    "@openzeppelin/contracts-upgradeable/": ("https://registry.npmjs.org/@openzeppelin/contracts-upgradeable/-/contracts-upgradeable-4.9.6.tgz",
                                             "38b21708a719da647de4bb0e4802ee235a0d24df", "lib/openzeppelin-contracts-upgradeable-4.9.6/"),
    "@openzeppelin-5/contracts/": ("https://registry.npmjs.org/@openzeppelin/contracts/-/contracts-5.4.0.tgz",
                                   "177594bdb2d86c71f5d1052fe40cb4edb95fb20f", "lib/openzeppelin-contracts-5.4.0/"),
}
ROOTS = ["contracts/BIFI/vaults/BeefyVaultV7.sol", "contracts/BIFI/strategies/Common/StrategyERC4626.sol"]
IMPORT = re.compile(r'import\s+(?:[^"\']*from\s+)?["\']([^"\']+)["\']')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="corpus/beefy-erc4626")
    args = ap.parse_args()
    out = Path(args.out)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        repo = tmp / "beefy"
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "fetch", "-q", "--depth", "1", REPO, COMMIT], check=True)
        subprocess.run(["git", "-C", str(repo), "checkout", "-q", "FETCH_HEAD"], check=True)
        pkg_dirs = {}
        for prefix, (url, sha1, dest) in PACKAGES.items():
            data = urllib.request.urlopen(url).read()
            got = hashlib.sha1(data).hexdigest()
            if got != sha1:
                raise SystemExit(f"checksum mismatch for {url}: {got} != {sha1}")
            d = tmp / dest
            with tarfile.open(fileobj=io.BytesIO(data)) as t:
                t.extractall(d, filter="data")
            pkg_dirs[prefix] = (d / "package", dest)

        def resolve(spec: str, cur: Path):
            for prefix, (d, dest) in pkg_dirs.items():
                if spec.startswith(prefix):
                    return d / spec[len(prefix):], dest + spec[len(prefix):]
            p = (cur.parent / spec).resolve()
            for prefix, (d, dest) in pkg_dirs.items():
                if str(p).startswith(str(d.resolve())):
                    return p, dest + str(p.relative_to(d.resolve()))
            return p, str(p.relative_to(repo.resolve()))

        seen = {}
        stack = [((repo / r).resolve(), r) for r in ROOTS]
        while stack:
            a, rel = stack.pop()
            if a in seen:
                continue
            seen[a] = rel
            for spec in IMPORT.findall(a.read_text()):
                stack.append(resolve(spec, a))
        for a, rel in seen.items():
            dst = out / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(a, dst)
        print(f"copied {len(seen)} files to {out}")


if __name__ == "__main__":
    main()
