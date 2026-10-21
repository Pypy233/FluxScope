"""Language-model providers with request recording and replay.

Every request is identified by the SHA-256 of (provider, model, prompt version,
system prompt, user content, schema). Responses are written to a cache
directory so that an analysis can be replayed exactly (``provider=replay``)
and so that each relation proposed by a model can be traced to the request
and response that produced it.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_MODELS = {
    "anthropic": "claude-opus-5-5",
    "claude-cli": "claude-opus-5-5",
    "exchange": "claude-opus-5-5",
    "openai": os.environ.get("FLUXSCOPE_OPENAI_MODEL", "gpt-5"),
}


class LLMError(RuntimeError):
    pass


class LLMPending(LLMError):
    """Requests were written for an external answerer (``exchange`` provider)
    and have no response yet."""

    def __init__(self, paths):
        self.paths = [Path(p) for p in paths]
        super().__init__(f"{len(self.paths)} LLM request(s) waiting for an answer: "
                         + ", ".join(str(p) for p in self.paths))


def validate_json(schema: Dict[str, Any], data: Any, path: str = "$") -> List[str]:
    """Small validator for the strict JSON-schema subset used by FluxScope
    (object / array / string / integer / number / boolean, enum, required,
    additionalProperties: false). Returns a list of problems."""

    errs: List[str] = []
    t = schema.get("type")
    types = {"object": dict, "array": list, "string": str, "boolean": bool}
    if t in types and not isinstance(data, types[t]):
        return [f"{path}: expected {t}"]
    if t in ("integer", "number"):
        if isinstance(data, bool) or not isinstance(data, (int, float)) or (t == "integer" and not float(data).is_integer()):
            return [f"{path}: expected {t}"]
    if "enum" in schema and data not in schema["enum"]:
        errs.append(f"{path}: {data!r} is not one of {schema['enum']}")
    if t == "object":
        props = schema.get("properties", {})
        for k in schema.get("required", []):
            if k not in data:
                errs.append(f"{path}: missing '{k}'")
        for k, v in data.items():
            if k in props:
                errs.extend(validate_json(props[k], v, f"{path}.{k}"))
            elif schema.get("additionalProperties") is False:
                errs.append(f"{path}: unexpected '{k}'")
    if t == "array" and "items" in schema:
        for i, v in enumerate(data):
            errs.extend(validate_json(schema["items"], v, f"{path}[{i}]"))
    return errs


@dataclass
class LLMRecord:
    call_id: str
    provider: str
    model: str
    prompt_version: str
    request_sha256: str
    sample: int = 0
    response_sha256: str = ""
    cached: bool = False
    seconds: float = 0.0
    usage: Dict[str, Any] = field(default_factory=dict)
    stop_reason: Optional[str] = None
    answered_by: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


def request_hash(provider: str, model: str, prompt_version: str, system: str, user: str, schema: dict,
                 sample: int = 0) -> str:
    """``sample`` > 0 distinguishes repeated, independent requests with the
    same content (sample 0 keeps the plain hash)."""

    h = hashlib.sha256()
    parts = [provider, model, prompt_version, system, user, json.dumps(schema, sort_keys=True)]
    if sample:
        parts.append(f"sample={sample}")
    for part in parts:
        h.update(part.encode())
        h.update(b"\0")
    return h.hexdigest()


class Provider:
    name = "base"

    def __init__(self, model: Optional[str] = None, cache_dir: Optional[Path] = None):
        self.model = model or DEFAULT_MODELS.get(self.name, "")
        self.cache_dir = Path(cache_dir) if cache_dir else None

    # -- cache -----------------------------------------------------------
    def _cache_path(self, key: str) -> Optional[Path]:
        return self.cache_dir / f"{key}.json" if self.cache_dir else None

    def _load(self, key: str) -> Optional[dict]:
        p = self._cache_path(key)
        if p and p.exists():
            return json.loads(p.read_text())
        return None

    def _store(self, key: str, payload: dict) -> None:
        p = self._cache_path(key)
        if p:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(payload, indent=1, ensure_ascii=False))

    # -- api -------------------------------------------------------------
    def complete_json(self, call_id: str, prompt_version: str, system: str, user: str, schema: dict, sample: int = 0):
        key = request_hash(self.cache_name(), self.model, prompt_version, system, user, schema, sample)
        rec = LLMRecord(call_id=call_id, provider=self.cache_name(), model=self.model,
                        prompt_version=prompt_version, request_sha256=key, sample=sample)
        hit = self._load(key)
        if hit is not None:
            rec.cached = True
            rec.usage = hit.get("usage", {})
            rec.stop_reason = hit.get("stop_reason")
            rec.answered_by = hit.get("answered_by", {})
            rec.seconds = hit.get("seconds", 0.0)
            text = hit["response_text"]
        else:
            if self.name == "replay":
                raise LLMError(f"replay: no recorded response for request {key[:12]} in {self.cache_dir}")
            t = time.time()
            text, usage, stop = self._call(system, user, schema, key=key, call_id=call_id,
                                           prompt_version=prompt_version, sample=sample)
            rec.seconds = round(time.time() - t, 2)
            rec.usage, rec.stop_reason = usage, stop
            rec.answered_by = getattr(self, "last_answered_by", {}) or {}
            rec.seconds = getattr(self, "last_seconds", None) or rec.seconds
            self._store(key, {"provider": self.cache_name(), "model": self.model, "prompt_version": prompt_version,
                              "call_id": call_id, "sample": sample, "system": system, "user": user, "schema": schema,
                              "response_text": text, "usage": usage, "stop_reason": stop,
                              "answered_by": rec.answered_by, "seconds": rec.seconds,
                              "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        rec.response_sha256 = hashlib.sha256(text.encode()).hexdigest()
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMError(f"model output is not valid JSON: {exc}") from exc
        problems = validate_json(schema, data)
        if problems:
            raise LLMError(f"model output does not match the schema: {'; '.join(problems[:5])}")
        return data, rec

    def cache_name(self) -> str:
        return self.name

    def _call(self, system: str, user: str, schema: dict, **meta):  # pragma: no cover - network
        raise NotImplementedError


class AnthropicProvider(Provider):
    """Claude via the official Anthropic SDK: structured JSON output,
    adaptive thinking (always on for Claude Opus 5.5) and server-side refusal
    fallback."""

    name = "anthropic"

    def _call(self, system: str, user: str, schema: dict, **meta):  # pragma: no cover - network
        import anthropic

        client = anthropic.Anthropic()
        with client.beta.messages.stream(
            model=self.model,
            max_tokens=64000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"effort": "high", "format": {"type": "json_schema", "schema": schema}},
        ) as stream:
            msg = stream.get_final_message()
        if msg.stop_reason == "refusal":
            raise LLMError(f"model declined the request: {getattr(msg, 'stop_details', None)}")
        if msg.stop_reason == "max_tokens":
            raise LLMError("model output was truncated (max_tokens)")
        text = next((b.text for b in msg.content if b.type == "text"), "")
        usage = {"input_tokens": msg.usage.input_tokens, "output_tokens": msg.usage.output_tokens}
        return text, usage, msg.stop_reason


class OpenAIProvider(Provider):
    name = "openai"

    def _call(self, system: str, user: str, schema: dict, **meta):  # pragma: no cover - network
        import openai

        client = openai.OpenAI()
        resp = client.responses.create(
            model=self.model,
            instructions=system,
            input=user,
            text={"format": {"type": "json_schema", "name": "fluxscope_semantics", "schema": schema, "strict": True}},
        )
        usage = {}
        if getattr(resp, "usage", None) is not None:
            usage = {"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens}
        return resp.output_text, usage, getattr(resp, "status", None)


class ReplayProvider(Provider):
    """Serve recorded responses only (no network). The recorded provider and
    model are taken from the cache entries."""

    name = "replay"

    def __init__(self, model: Optional[str] = None, cache_dir: Optional[Path] = None, recorded_provider: str = ""):
        super().__init__(model, cache_dir)
        if not self.cache_dir or not self.cache_dir.is_dir():
            raise LLMError("replay needs --llm-cache pointing at a directory of recorded responses")
        self.recorded_provider = recorded_provider
        if not model or not recorded_provider:
            entries = sorted(self.cache_dir.glob("*.json"))
            if not entries:
                raise LLMError(f"replay: {self.cache_dir} contains no recorded responses")
            head = json.loads(entries[0].read_text())
            self.model = model or head["model"]
            self.recorded_provider = recorded_provider or head["provider"]

    def cache_name(self) -> str:
        return self.recorded_provider


class ClaudeCLIProvider(Provider):
    """Claude through the Claude Code CLI in headless mode (``claude -p``),
    authenticated with the user's Claude subscription instead of an API key.
    Tools are disabled and the reply is constrained by ``--json-schema``.
    Run it from a normal terminal (not from inside a Claude Code session)."""

    name = "claude-cli"
    executable = os.environ.get("FLUXSCOPE_CLAUDE_BIN", "claude")

    def _call(self, system: str, user: str, schema: dict, **meta):
        import shutil
        import subprocess

        exe = shutil.which(self.executable)
        if not exe:
            raise LLMError(f"'{self.executable}' not found on PATH (install Claude Code or set FLUXSCOPE_CLAUDE_BIN)")
        cmd = [exe, "-p", "--output-format", "json", "--json-schema", json.dumps(schema), "--tools", "",
               "--no-session-persistence", "--model", self.model, "--system-prompt", system]
        env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
        proc = subprocess.run(cmd, input=user, capture_output=True, text=True, env=env,
                              timeout=int(os.environ.get("FLUXSCOPE_CLAUDE_TIMEOUT", "1800")))
        if proc.returncode != 0:
            raise LLMError(f"claude -p failed ({proc.returncode}): {(proc.stderr or proc.stdout).strip()[:500]}")
        try:
            out = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise LLMError(f"claude -p did not return JSON: {proc.stdout[:300]}") from exc
        if out.get("is_error"):
            raise LLMError(f"claude -p reported an error: {out.get('result') or out.get('subtype')}")
        data = out.get("structured_output")
        text = json.dumps(data, ensure_ascii=False) if data is not None else _strip_fences(out.get("result") or "")
        models = sorted((out.get("modelUsage") or {}).keys())
        self.last_answered_by = {"channel": "claude-cli", "session_id": out.get("session_id"),
                                 "models": models, "cost_usd_equivalent": out.get("total_cost_usd")}
        self.last_seconds = round((out.get("duration_ms") or 0) / 1000, 2) or None
        usage = out.get("usage") or {}
        return text, {k: usage.get(k) for k in ("input_tokens", "output_tokens") if k in usage}, out.get("subtype")


class ExchangeProvider(Provider):
    """File exchange with an external answerer.

    Each new request is written to ``<cache>/pending/<hash>.request.json``
    (exact text, for hashing and replay) and ``<hash>.request.md`` (readable
    by a person or an agent). The answerer writes
    ``<hash>.response.json``::

        {"answered_by": {"channel": "...", "model": "..."}, "response": {...}}

    and the next run validates the response, moves it into the cache and
    continues. Used to run the LLM stage through a Claude subscription
    (e.g. Claude Code subagents) without an API key."""

    name = "exchange"

    def __init__(self, model: Optional[str] = None, cache_dir: Optional[Path] = None):
        super().__init__(model, cache_dir)
        if not self.cache_dir:
            raise LLMError("exchange needs --llm-cache (requests and answers are exchanged there)")
        self.pending: List[Path] = []

    def _call(self, system: str, user: str, schema: dict, key: str = "", call_id: str = "", prompt_version: str = "",
              sample: int = 0):
        pdir = self.cache_dir / "pending"
        resp = pdir / f"{key}.response.json"
        if resp.exists():
            raw = json.loads(resp.read_text())
            data = raw.get("response", raw) if isinstance(raw, dict) else raw
            self.last_answered_by = raw.get("answered_by", {}) if isinstance(raw, dict) else {}
            self.last_seconds = self.last_answered_by.get("seconds")
            text = json.dumps(data, ensure_ascii=False)
            problems = validate_json(schema, data)
            if problems:
                raise LLMError(f"{resp}: response does not match the schema: {'; '.join(problems[:5])}")
            for f in pdir.glob(f"{key}.*"):
                f.unlink()
            return text, {}, "answered"
        pdir.mkdir(parents=True, exist_ok=True)
        req = {"key": key, "call_id": call_id, "sample": sample, "prompt_version": prompt_version, "model": self.model,
               "system": system, "user": user, "schema": schema,
               "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        (pdir / f"{key}.request.json").write_text(json.dumps(req, indent=1, ensure_ascii=False))
        (pdir / f"{key}.request.md").write_text(_request_markdown(req, resp.resolve()))
        raise LLMPending([pdir / f"{key}.request.md"])


def _request_markdown(req: Dict[str, Any], resp: Path) -> str:
    return "\n".join([
        f"# FluxScope LLM request {req['key'][:12]} ({req['call_id']}, prompt {req['prompt_version']})",
        "",
        "## How to answer",
        "",
        "Answer as the language model of this request: follow the system prompt, use only the user message",
        "below, and produce one JSON object that matches the output schema exactly. Write it to",
        f"`{resp}` as",
        "`{\"answered_by\": {\"channel\": \"...\", \"model\": \"...\"}, \"response\": <the JSON object>}`.",
        "",
        "## System prompt",
        "",
        req["system"].rstrip(),
        "",
        "## Output schema",
        "",
        "```json",
        json.dumps(req["schema"], indent=1),
        "```",
        "",
        "## User message",
        "",
        req["user"].rstrip(),
        "",
    ])


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
        t = t.rsplit("```", 1)[0]
    return t.strip()


def make_provider(name: str, model: Optional[str], cache_dir: Optional[Path]) -> Provider:
    if name == "anthropic":
        return AnthropicProvider(model, cache_dir)
    if name == "claude-cli":
        return ClaudeCLIProvider(model, cache_dir)
    if name == "exchange":
        return ExchangeProvider(model, cache_dir)
    if name == "openai":
        return OpenAIProvider(model, cache_dir)
    if name == "replay":
        return ReplayProvider(model, cache_dir)
    raise LLMError(f"unknown LLM provider '{name}'")
