"""Command line.

  python -m cc run   --input <folder|zip|file> --out outputs/my_show [--genre period_drama] [--rules-only] [--incremental]
  python -m cc parse --input <folder|zip|file>            # just show detected format and coverage
  python -m cc decide --out outputs/my_show --flag 12 --decision accepted|dismissed|intentional [--note "..."]
  python -m cc mcp   --out outputs/my_show                # start the read-only MCP server on that bible
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv=None):
    ap = argparse.ArgumentParser(prog="cc", description="Continuity checker for serialized drama scripts")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run"); r.add_argument("--input", required=True); r.add_argument("--out", required=True)
    r.add_argument("--genre"); r.add_argument("--config"); r.add_argument("--rules-only", action="store_true")
    r.add_argument("--incremental", action="store_true"); r.add_argument("--format", choices=["kuku", "screenplay", "llm"])
    r.add_argument("--decisions", help="alias decisions CSV (default: <out>/alias_decisions.csv)")
    p = sub.add_parser("parse"); p.add_argument("--input", required=True); p.add_argument("--format", choices=["kuku", "screenplay", "llm"])
    d = sub.add_parser("decide"); d.add_argument("--out", required=True); d.add_argument("--flag", type=int, required=True)
    d.add_argument("--decision", choices=["accepted", "dismissed", "intentional"], required=True); d.add_argument("--note", default="")
    m = sub.add_parser("mcp"); m.add_argument("--out", required=True); m.add_argument("--config")
    a = ap.parse_args(argv)

    if a.cmd == "run":
        from .pipeline import load_config, run
        cfg = load_config(a.config)
        s = run(a.input, a.out, cfg=cfg, genre=a.genre, use_rules_only=a.rules_only, incremental=a.incremental,
                force_format=a.format, decisions_path=a.decisions)
        print(f"\nflags: see {Path(a.out) / 'report.md'}  |  coverage {s['coverage']:.1%}  |  warnings: {len(s['warnings'])}")
    elif a.cmd == "parse":
        from .parsing import parse_path
        res = parse_path(a.input, force_format=a.format)
        print(f"format={res.format_name} coverage={res.coverage:.2%} episodes={len(res.episodes)} lines={len(res.all_lines())}")
        for w in res.warnings:
            print("warning:", w)
        for u in res.unparsed_samples[:10]:
            print("unparsed:", u)
    elif a.cmd == "decide":
        from .bible import Bible
        b = Bible(Path(a.out) / "bible.sqlite")
        b.decide(a.flag, a.decision, a.note, feedback_log=Path(a.out) / "feedback_log.jsonl")
        print(f"flag {a.flag} -> {a.decision}")
    elif a.cmd == "mcp":
        from mcp_server import serve
        serve(Path(a.out) / "bible.sqlite", a.config)


if __name__ == "__main__":
    sys.exit(main())
