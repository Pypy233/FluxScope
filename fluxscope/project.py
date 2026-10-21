"""Project configuration, workspace snapshots and compilation."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

CONFIG_NAME = "fluxscope.json"
_IGNORE_DIRS = {".git", "out", "cache", "cache-forge", "artifacts", "artifacts-forge", "crytic-export", "node_modules_cache"}


class _Cfg(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InstanceCfg(_Cfg):
    """One deployed (or to-be-deployed) instance of a contract."""

    id: str
    contract: str
    label: Optional[str] = None
    description: Optional[str] = None
    address: Optional[str] = None
    group: Optional[str] = None


class ExternalCfg(_Cfg):
    label: str
    kind: str = "external_contract"
    address: Optional[str] = None
    description: Optional[str] = None


class BindingCfg(_Cfg):
    """State variable of an instance -> another instance or an external party."""

    instance: str
    variable: str
    target: Optional[str] = Field(None, description="Instance id")
    external: Optional[ExternalCfg] = None
    source: str = Field("deployment", description="Where the binding comes from (deployment script, chain state, ...)")
    note: Optional[str] = None


class RegistryCfg(_Cfg):
    """Membership of instances in an address registry of another instance
    (a ``mapping(address => bool)``, an ``EnumerableSet.AddressSet`` or an
    address array), e.g. strategies registered in a vault. Calls to an address
    that the code checks against the registry resolve to the members, and
    access checks against it are satisfied for them."""

    instance: str
    variable: str
    members: List[str]
    source: str = "deployment"
    note: Optional[str] = None


class IdentityCfg(_Cfg):
    """Assert that asset expressions denote the same token (e.g. from chain state)."""

    same: List[str]
    source: str = "deployment"
    note: Optional[str] = None


class AssetLabelCfg(_Cfg):
    expression: str = Field(..., description="Instance id (own token) or instance.variable[.getter()]")
    label: str
    symbol: Optional[str] = None
    address: Optional[str] = None
    decimals: Optional[int] = None
    description: Optional[str] = None


class GroupCfg(_Cfg):
    id: str
    label: str
    members: List[str]
    description: Optional[str] = None


class DependencyCfg(_Cfg):
    path: str
    package: str
    version: str


class BuildCfg(_Cfg):
    framework: str = Field("foundry", description="foundry | hardhat | solc (anything crytic-compile supports)")
    target: str = "."
    args: Dict[str, Any] = Field(default_factory=dict, description="Extra crytic-compile keyword arguments")


class ChainCfg(_Cfg):
    chain_id: Optional[int] = None
    name: Optional[str] = None
    block: Optional[int] = None


class ProjectConfig(_Cfg):
    name: str
    description: Optional[str] = None
    repository: Optional[str] = None
    commit: Optional[str] = None
    version: Optional[str] = None
    build: BuildCfg = Field(default_factory=BuildCfg)
    instances: List[InstanceCfg]
    bindings: List[BindingCfg] = Field(default_factory=list)
    registries: List[RegistryCfg] = Field(default_factory=list)
    identities: List[IdentityCfg] = Field(default_factory=list)
    assets: List[AssetLabelCfg] = Field(default_factory=list)
    groups: List[GroupCfg] = Field(default_factory=list)
    dependencies: List[DependencyCfg] = Field(default_factory=list)
    chain: Optional[ChainCfg] = None
    exclude_entrypoints: List[str] = Field(default_factory=list, description="Contract.function names to skip")
    notes: List[str] = Field(default_factory=list)
    tasks: List[Dict[str, Any]] = Field(default_factory=list, description="Guided user tasks for the report")


def load_config(project_dir: Path, config_path: Optional[Path] = None) -> ProjectConfig:
    path = Path(config_path) if config_path else Path(project_dir) / CONFIG_NAME
    return ProjectConfig.model_validate(json.loads(path.read_text()))


@dataclass
class Workspace:
    """A normalised copy of the project that the analysis runs on."""

    root: Path
    original: Path
    files: List[Dict[str, Any]] = field(default_factory=list)
    normalized: List[str] = field(default_factory=list)
    snapshot_sha256: str = ""
    _tmp: Optional[tempfile.TemporaryDirectory] = None

    def cleanup(self) -> None:
        if self._tmp is not None:
            self._tmp.cleanup()
            self._tmp = None

    def read(self, rel: str) -> str:
        return (self.root / rel).read_text(errors="replace")


def prepare_workspace(project_dir: Path, workdir: Optional[Path] = None) -> Workspace:
    """Copy the project and normalise line endings of Solidity sources.

    solc receives LF-normalised sources from some build tools, while Slither maps
    offsets back onto the files on disk; CRLF files would otherwise produce
    wrong line numbers in the evidence. Hashes of the original bytes are kept.
    """

    project_dir = Path(project_dir).resolve()
    tmp = None
    if workdir is None:
        tmp = tempfile.TemporaryDirectory(prefix="fluxscope-")
        workdir = Path(tmp.name)
    dst = Path(workdir) / "project"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(project_dir, dst, ignore=lambda d, names: [n for n in names if n in _IGNORE_DIRS])
    ws = Workspace(root=dst, original=project_dir, _tmp=tmp)
    digest = hashlib.sha256()
    for p in sorted(dst.rglob("*.sol")):
        rel = p.relative_to(dst).as_posix()
        raw = p.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        digest.update(rel.encode() + b"\0" + raw)
        entry = {"path": rel, "sha256": sha}
        if b"\r\n" in raw:
            p.write_bytes(raw.replace(b"\r\n", b"\n"))
            ws.normalized.append(rel)
            entry["normalized_line_endings"] = True
        ws.files.append(entry)
    ws.snapshot_sha256 = digest.hexdigest()
    return ws


def _ensure_toolchain_path() -> None:
    foundry = Path.home() / ".foundry" / "bin"
    if foundry.is_dir() and str(foundry) not in os.environ.get("PATH", ""):
        os.environ["PATH"] = f"{foundry}{os.pathsep}{os.environ.get('PATH', '')}"


def compile_workspace(ws: Workspace, cfg: ProjectConfig):
    """Compile with crytic-compile and return a Slither instance."""

    _ensure_toolchain_path()
    from slither import Slither

    target = str((ws.root / cfg.build.target).resolve())
    kwargs = dict(cfg.build.args)
    if cfg.build.framework and cfg.build.framework != "auto":
        kwargs.setdefault("compile_force_framework", cfg.build.framework)
    cwd = os.getcwd()
    try:
        os.chdir(ws.root)
        return Slither(target, **kwargs)
    finally:
        os.chdir(cwd)


def tool_versions(slither) -> Dict[str, Optional[str]]:
    from importlib.metadata import version

    out: Dict[str, Optional[str]] = {}
    for pkg in ("slither-analyzer", "crytic-compile"):
        try:
            out[pkg] = version(pkg)
        except Exception:  # pragma: no cover
            out[pkg] = None
    try:
        cu = next(iter(slither.crytic_compile.compilation_units.values()))
        out["solc"] = cu.compiler_version.version
    except Exception:  # pragma: no cover
        out["solc"] = None
    return out
