"""Evidence registry: deduplicates evidence items and collects source files."""

from __future__ import annotations

import hashlib
import re
from typing import Dict, List, Optional

from .facts.resolver import Site
from .model import Evidence, ExecutionRef, SourceFile, SourceLocation


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.:-]+", "_", s)[:120]


class EvidenceRegistry:
    def __init__(self) -> None:
        self.items: Dict[str, Evidence] = {}
        self.files: Dict[str, None] = {}

    def site(self, site: Optional[Site], title: Optional[str] = None, description: Optional[str] = None) -> Optional[str]:
        if site is None or not site.file or site.start_line <= 0:
            return None
        eid = f"ev:src:{_slug(site.file)}:{site.start_line}-{site.end_line}"
        if eid not in self.items:
            where = f"{site.contract}.{site.function}" if site.function else site.contract
            self.items[eid] = Evidence(
                id=eid,
                kind="source",
                title=title or (f"{where}: {site.snippet.splitlines()[0][:90]}" if site.snippet else where),
                description=description,
                source=SourceLocation(file=site.file, contract=site.contract or None, function=site.function or None,
                                      start_line=site.start_line, end_line=site.end_line,
                                      snippet=site.snippet[:1200] if site.snippet else None),
            )
            self.files[site.file] = None
        elif description and not self.items[eid].description:
            self.items[eid].description = description
        return eid

    def rule(self, rule: str, note: str, impl: Optional[Site] = None, reference: Optional[str] = None) -> str:
        h = hashlib.sha1(f"{rule}|{note}".encode()).hexdigest()[:10]
        eid = f"ev:rule:{rule}:{h}"
        if eid not in self.items:
            desc = note
            if impl is not None:
                desc += f" — implementation at {impl.file}:{impl.start_line}-{impl.end_line}"
                self.site(impl, title=f"Implementation used by rule {rule}: {impl.contract}.{impl.function}")
            self.items[eid] = Evidence(id=eid, kind="rule", title=f"Rule {rule}", description=desc, reference=reference)
        return eid

    def standard(self, name: str, reference: str, description: str) -> str:
        eid = f"ev:std:{_slug(name)}"
        if eid not in self.items:
            self.items[eid] = Evidence(id=eid, kind="standard", title=name, description=description, reference=reference)
        return eid

    def config(self, key: str, title: str, description: str, reference: str = "fluxscope.json") -> str:
        eid = f"ev:cfg:{_slug(key)}"
        if eid not in self.items:
            self.items[eid] = Evidence(id=eid, kind="config", title=title, description=description, reference=reference)
        return eid

    def llm(self, call_id: str, title: str, description: str, meta: dict) -> str:
        eid = f"ev:llm:{_slug(call_id)}:{hashlib.sha1(title.encode()).hexdigest()[:8]}"
        if eid not in self.items:
            self.items[eid] = Evidence(id=eid, kind="llm", title=title, description=description, llm=meta)
        return eid

    def execution(self, eid: str, title: str, ref: ExecutionRef, description: Optional[str] = None) -> str:
        if eid not in self.items:
            self.items[eid] = Evidence(id=eid, kind="trace", title=title, description=description, execution=ref)
        return eid

    def sources(self, read) -> List[SourceFile]:
        out = []
        for path in sorted(self.files):
            try:
                content = read(path)
            except OSError:
                continue
            out.append(SourceFile(path=path, sha256=hashlib.sha256(content.encode()).hexdigest(), content=content))
        return out
