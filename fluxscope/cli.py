"""FluxScope command line interface: ``python -m fluxscope <command>``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .llm.client import LLMPending

LLM_CHOICES = ["none", "anthropic", "claude-cli", "exchange", "openai", "replay"]

ROOT = Path(__file__).resolve().parent.parent


def cmd_schema(args: argparse.Namespace) -> int:
    from .model import json_schema

    out = Path(args.out)
    out.write_text(json.dumps(json_schema(), indent=2) + "\n")
    print(f"wrote {out}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    from .validate import validate_file

    status = 0
    for f in args.files:
        errs = validate_file(Path(f))
        if errs:
            status = 1
            print(f"{f}: {len(errs)} problem(s)")
            for e in errs:
                print(f"  - {e}")
        else:
            print(f"{f}: ok")
    return status


def cmd_analyze(args: argparse.Namespace) -> int:
    from .analyze import AnalysisOptions, analyze_project
    from .model import semantic_errors
    from .project import load_config

    project = Path(args.project)
    cfg = load_config(project, Path(args.config) if args.config else None)

    def progress(stage: str, msg: str) -> None:
        if not args.quiet:
            print(f"[{stage}] {msg}", file=sys.stderr)

    opts = AnalysisOptions(llm=args.llm, llm_model=args.llm_model,
                           llm_cache=Path(args.llm_cache) if args.llm_cache else None, progress=progress)
    try:
        result = analyze_project(project, cfg, opts)
    except LLMPending as exc:
        return _pending(exc)
    errs = semantic_errors(result)
    if errs:
        print("warning: result has semantic problems:\n  " + "\n  ".join(errs[:20]), file=sys.stderr)
    data = result.to_json_dict()
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
        print(f"wrote {args.json}")
    if args.html:
        from .report import write_report

        write_report(result, Path(args.html))
        print(f"wrote {args.html}")
    if not args.json and not args.html:
        print(json.dumps(data, indent=1, ensure_ascii=False))
    counts = {k: len(v) for k, v in data.items() if isinstance(v, list)}
    print("summary: " + ", ".join(f"{k}={v}" for k, v in counts.items()), file=sys.stderr)
    return 0


def _pending(exc) -> int:
    print("The LLM stage is waiting for answers (exchange provider). Answer each request below by writing", file=sys.stderr)
    print("the matching <hash>.response.json next to it, then rerun the same command:", file=sys.stderr)
    for p in exc.paths:
        print(f"  {p}", file=sys.stderr)
    return 3


def cmd_report(args: argparse.Namespace) -> int:
    from .report import write_report
    from .validate import validate_file

    errs = validate_file(Path(args.result))
    if errs:
        print(f"{args.result} is not a valid analysis result:\n  " + "\n  ".join(errs[:20]), file=sys.stderr)
        return 1
    write_report(json.loads(Path(args.result).read_text()), Path(args.out))
    print(f"wrote {args.out}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from .server.app import run

    run(host=args.host, port=args.port, projects_dir=Path(args.projects), data_dir=Path(args.data))
    return 0


def cmd_diff(args: argparse.Namespace) -> int:
    from .diff import diff, format_diff

    a = json.loads(Path(args.before).read_text())
    b = json.loads(Path(args.after).read_text())
    print(format_diff(diff(a, b)))
    return 0


def cmd_evaluate(args: argparse.Namespace) -> int:
    from .evaluate import evaluate, format_table

    res = evaluate(Path(args.truth), args.configs.split(","), llm=args.llm, llm_model=args.llm_model,
                   llm_cache=Path(args.llm_cache) if args.llm_cache else None, runs=args.runs)
    pending = [p for sc in res["configs"].values() for p in sc.get("pending", [])]
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(res, indent=1) + "\n")
        print(f"wrote {args.out}")
    print(format_table(res))
    if pending:
        return _pending(LLMPending(pending))
    return 0


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="fluxscope", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("schema", help="write the JSON schema")
    p.add_argument("--out", default=str(ROOT / "schema" / "fluxscope-analysis-0.1.schema.json"))
    p.set_defaults(func=cmd_schema)

    p = sub.add_parser("validate", help="validate analysis-result JSON files")
    p.add_argument("files", nargs="+")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("analyze", help="analyse a Solidity project and write JSON / HTML")
    p.add_argument("project", help="project directory containing fluxscope.json")
    p.add_argument("--config", help="alternative project configuration file")
    p.add_argument("--json", help="write the analysis result JSON here")
    p.add_argument("--html", help="write an offline HTML report here")
    p.add_argument("--llm", default="none", choices=LLM_CHOICES,
                   help="semantic enrichment with a language model (default: none); claude-cli uses a Claude "
                        "subscription through `claude -p`, exchange writes requests for an external answerer")
    p.add_argument("--llm-model", help="model id (provider default if omitted)")
    p.add_argument("--llm-cache", help="directory for recorded LLM requests/responses (replay)")
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("report", help="build an offline HTML report from a result JSON")
    p.add_argument("result")
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("diff", help="semantic diff of two analysis results")
    p.add_argument("before")
    p.add_argument("after")
    p.set_defaults(func=cmd_diff)

    p = sub.add_parser("evaluate", help="score configurations against a ground-truth annotation")
    p.add_argument("truth")
    p.add_argument("--configs", default="rules,llm-only,combined")
    p.add_argument("--llm", default="none", choices=LLM_CHOICES)
    p.add_argument("--runs", type=int, default=1, help="repeat the LLM configurations (separate requests)")
    p.add_argument("--llm-model")
    p.add_argument("--llm-cache")
    p.add_argument("--out")
    p.set_defaults(func=cmd_evaluate)

    p = sub.add_parser("serve", help="run the web API and viewer")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--projects", default=str(ROOT / "corpus"), help="directory with analysable projects")
    p.add_argument("--data", default=str(ROOT / "build" / "jobs"), help="job storage directory")
    p.set_defaults(func=cmd_serve)

    args = parser.parse_args(argv)
    sys.exit(args.func(args))
