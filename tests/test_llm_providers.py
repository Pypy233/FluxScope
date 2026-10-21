"""LLM providers without a network: request exchange, the Claude Code CLI
provider (against a fake `claude` executable) and response validation."""

import json
import os
import stat

import pytest

from fluxscope.llm import client as C

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["kind", "items"],
          "properties": {"kind": {"type": "string", "enum": ["a", "b"]},
                         "items": {"type": "array", "items": {"type": "integer"}}}}


def test_validate_json():
    assert C.validate_json(SCHEMA, {"kind": "a", "items": [1, 2]}) == []
    errs = C.validate_json(SCHEMA, {"kind": "c", "items": ["x"], "extra": 1})
    assert any("not one of" in e for e in errs)
    assert any("expected integer" in e for e in errs)
    assert any("unexpected 'extra'" in e for e in errs)
    assert C.validate_json(SCHEMA, {"items": []}) == ["$: missing 'kind'"]


def test_sample_changes_request_hash():
    h0 = C.request_hash("p", "m", "v", "s", "u", SCHEMA)
    assert h0 == C.request_hash("p", "m", "v", "s", "u", SCHEMA, 0)
    assert h0 != C.request_hash("p", "m", "v", "s", "u", SCHEMA, 1)


def test_exchange_round_trip(tmp_path):
    prov = C.make_provider("exchange", None, tmp_path)
    with pytest.raises(C.LLMPending) as exc:
        prov.complete_json("call", "v1", "SYSTEM", "USER", SCHEMA)
    md = exc.value.paths[0]
    key = md.name.split(".")[0]
    text = md.read_text()
    assert "## System prompt" in text and "SYSTEM" in text and "USER" in text
    assert str((tmp_path / "pending" / f"{key}.response.json").resolve()) in text
    # an answer that violates the schema is refused and stays pending
    resp = tmp_path / "pending" / f"{key}.response.json"
    resp.write_text(json.dumps({"answered_by": {"channel": "test", "model": "m"}, "response": {"kind": "zzz", "items": []}}))
    with pytest.raises(C.LLMError, match="does not match the schema"):
        prov.complete_json("call", "v1", "SYSTEM", "USER", SCHEMA)
    resp.write_text(json.dumps({"answered_by": {"channel": "test", "model": "m"}, "response": {"kind": "a", "items": [3]}}))
    data, rec = prov.complete_json("call", "v1", "SYSTEM", "USER", SCHEMA)
    assert data == {"kind": "a", "items": [3]}
    assert rec.answered_by == {"channel": "test", "model": "m"} and not rec.cached
    assert not list((tmp_path / "pending").glob(f"{key}.*"))  # moved into the cache
    # replay from the cache, no pending files needed
    data2, rec2 = C.make_provider("exchange", None, tmp_path).complete_json("call", "v1", "SYSTEM", "USER", SCHEMA)
    assert data2 == data and rec2.cached and rec2.answered_by["channel"] == "test"
    data3, rec3 = C.make_provider("replay", None, tmp_path).complete_json("call", "v1", "SYSTEM", "USER", SCHEMA)
    assert data3 == data and rec3.provider == "exchange"


FAKE_CLAUDE = """#!/usr/bin/env python3
import json, sys
args = sys.argv[1:]
assert args[0] == "-p" and "--json-schema" in args and "--tools" in args
schema = json.loads(args[args.index("--json-schema") + 1])
system = args[args.index("--system-prompt") + 1]
user = sys.stdin.read()
json.dump({"type": "result", "subtype": "success", "is_error": False, "result": "",
           "structured_output": {"kind": "b", "items": [len(user), len(system)]},
           "session_id": "fake-session", "duration_ms": 1500, "total_cost_usd": 0.0,
           "usage": {"input_tokens": 11, "output_tokens": 7},
           "modelUsage": {args[args.index("--model") + 1]: {}}}, sys.stdout)
"""


def test_claude_cli_provider_with_fake_cli(tmp_path, monkeypatch):
    exe = tmp_path / "claude"
    exe.write_text(FAKE_CLAUDE)
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(C.ClaudeCLIProvider, "executable", "claude")
    prov = C.make_provider("claude-cli", None, tmp_path / "cache")
    data, rec = prov.complete_json("call", "v1", "SYS", "hello", SCHEMA)
    assert data == {"kind": "b", "items": [5, 3]}
    assert rec.provider == "claude-cli" and rec.model == "claude-opus-5-5"
    assert rec.answered_by["channel"] == "claude-cli" and rec.answered_by["models"] == ["claude-opus-5-5"]
    assert rec.usage == {"input_tokens": 11, "output_tokens": 7} and rec.seconds == 1.5
    # recorded: a second call is served from the cache without running the CLI
    exe.write_text("#!/bin/sh\nexit 1\n")
    data2, rec2 = C.make_provider("claude-cli", None, tmp_path / "cache").complete_json("call", "v1", "SYS", "hello", SCHEMA)
    assert data2 == data and rec2.cached


def test_claude_cli_reports_failures(tmp_path, monkeypatch):
    exe = tmp_path / "claude"
    exe.write_text("#!/bin/sh\necho 'Not logged in' >&2\nexit 2\n")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(C.ClaudeCLIProvider, "executable", "claude")
    with pytest.raises(C.LLMError, match="Not logged in"):
        C.make_provider("claude-cli", None, None).complete_json("call", "v1", "SYS", "hello", SCHEMA)
