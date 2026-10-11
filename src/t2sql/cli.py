"""Command line interface.

    t2sql seed                                  build the demo database (SQLite by default)
    t2sql ask "revenue by region in 2025"       one question
    t2sql chat                                  interactive session with follow-ups
    t2sql eval                                  execution-accuracy evaluation
    t2sql serve                                 FastAPI on :8000
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _print_answer(ans) -> None:
    if not ans.ok:
        print(f"! {ans.error}")
        return
    print(f"\n{ans.title}\n")
    print(ans.sql, "\n")
    widths = [max(len(str(c)), *(len(_cell(r[i])) for r in ans.rows[:20])) for i, c in enumerate(ans.columns)]
    print("  ".join(str(c).ljust(w) for c, w in zip(ans.columns, widths)))
    print("  ".join("-" * w for w in widths))
    for row in ans.rows[:20]:
        print("  ".join(_cell(v).ljust(w) for v, w in zip(row, widths)))
    if len(ans.rows) > 20:
        print(f"... {len(ans.rows) - 20} more rows")
    print(f"\n{ans.summary}")


def _cell(v) -> str:
    return f"{v:,.2f}" if isinstance(v, float) else str(v)


def cmd_seed(args) -> int:
    from .config import get_settings
    from .seed import build_database

    url = args.url or get_settings().database_url
    if url.startswith("sqlite:///"):
        Path(url.replace("sqlite:///", "")).parent.mkdir(parents=True, exist_ok=True)
    counts = build_database(url, n_orders=args.orders)
    print(f"Seeded {url}: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    return 0


def cmd_ask(args) -> int:
    from .service import TextToSQLService

    _print_answer(TextToSQLService().ask(args.question, args.session))
    return 0


def cmd_chat(args) -> int:
    from .service import TextToSQLService

    svc = TextToSQLService()
    session = "cli"
    svc.memory.clear(session)
    print(f"Ask a question ({svc.engine_name} on {svc.dialect}). Empty line to quit.")
    while True:
        try:
            q = input("\n> ").strip()
        except EOFError:
            break
        if not q:
            break
        _print_answer(svc.ask(q, session))
    return 0


def cmd_eval(args) -> int:
    from .evaluation import run_evaluation
    from .service import TextToSQLService

    out = run_evaluation(TextToSQLService(), args.cases, args.report)
    for k, v in out["summary"].items():
        print(f"{k:20} {v}")
    for r in out["results"]:
        if not r.correct:
            print(f"  MISS {r.id}: {r.question} ({r.note or r.error})")
    if args.min_accuracy is not None and out["summary"]["execution_accuracy"] < args.min_accuracy:
        return 2
    return 0


def cmd_serve(args) -> int:
    import uvicorn

    uvicorn.run("t2sql.api:app_factory", factory=True, host=args.host, port=args.port)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="t2sql", description="Text-to-SQL analytics agent")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("seed", help="create the demo database")
    s.add_argument("--url", help="database URL (default: T2SQL_DATABASE_URL)")
    s.add_argument("--orders", type=int, default=4000)
    s.set_defaults(func=cmd_seed)

    s = sub.add_parser("ask")
    s.add_argument("question")
    s.add_argument("--session", default=None)
    s.set_defaults(func=cmd_ask)

    s = sub.add_parser("chat")
    s.set_defaults(func=cmd_chat)

    s = sub.add_parser("eval")
    s.add_argument("--cases", default="eval/cases.jsonl")
    s.add_argument("--report", default="reports")
    s.add_argument("--min-accuracy", type=float, default=None)
    s.set_defaults(func=cmd_eval)

    s = sub.add_parser("serve")
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--port", type=int, default=8000)
    s.set_defaults(func=cmd_serve)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
