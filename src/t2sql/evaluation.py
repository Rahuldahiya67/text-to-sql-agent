"""Execution-accuracy evaluation.

Each case pairs a question with a hand-written gold SQL query. The generated SQL is correct
when it returns the same result as the gold query (order-insensitive unless the case says
order matters, numbers compared with a small tolerance). Comparing results instead of SQL
text is the standard approach (as in the Spider and BIRD benchmarks), because many different
queries are equally correct.

Cases can be multi-turn: a case with "session": "s1" shares conversation memory with the
other cases in that session, which tests follow-up questions.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .db import run_query
from .service import TextToSQLService


@dataclass
class Case:
    id: str
    question: str
    gold_sql: dict[str, str] | str | None = None     # str, or {"sqlite": ..., "postgres": ...}
    session: str | None = None
    ordered: bool = False
    expect_error: bool = False


@dataclass
class CaseResult:
    id: str
    question: str
    correct: bool
    generated_sql: str | None
    error: str | None
    attempts: int
    note: str = ""


def load_cases(path: str | Path) -> list[Case]:
    return [Case(**json.loads(line)) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def _norm(v: Any) -> Any:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)) or (hasattr(v, "__float__") and not isinstance(v, str)):
        f = float(v)
        return round(f, 2) if not math.isnan(f) else None
    return str(v).strip() if v is not None else None


def _close(a: Any, b: Any) -> bool:
    if isinstance(a, float) and isinstance(b, float):
        return math.isclose(a, b, rel_tol=1e-4, abs_tol=0.011)
    return a == b


def results_match(got: list[list[Any]], gold: list[list[Any]], ordered: bool) -> bool:
    if len(got) != len(gold):
        return False
    g1 = [[_norm(v) for v in r] for r in got]
    g2 = [[_norm(v) for v in r] for r in gold]
    if any(len(a) != len(b) for a, b in zip(g1, g2)):
        # allow extra columns in the generated result as long as the gold columns appear in order
        return False
    if not ordered:
        key = lambda r: tuple((str(type(v)), str(v)) for v in r)
        g1, g2 = sorted(g1, key=key), sorted(g2, key=key)
    return all(all(_close(a, b) for a, b in zip(r1, r2)) for r1, r2 in zip(g1, g2))


def run_evaluation(svc: TextToSQLService, cases_path: str | Path, report_dir: str | Path | None = None) -> dict:
    cases = load_cases(cases_path)
    results: list[CaseResult] = []
    cleared: set[str] = set()
    for case in cases:
        sid = f"eval-{case.session or case.id}"
        if sid not in cleared:            # start every session with empty memory
            svc.memory.clear(sid)
            cleared.add(sid)

        ans = svc.ask(case.question, sid)
        if case.expect_error:
            results.append(CaseResult(case.id, case.question, not ans.ok, ans.sql, ans.error, len(ans.attempts),
                                      "expected a refusal"))
            continue
        gold = case.gold_sql.get(svc.dialect, case.gold_sql.get("default")) if isinstance(case.gold_sql, dict) else case.gold_sql
        if not ans.ok:
            results.append(CaseResult(case.id, case.question, False, None, ans.error, len(ans.attempts)))
            continue
        gold_rows = run_query(svc.engine, gold, 10_000).rows
        ok = results_match(ans.rows, gold_rows, case.ordered)
        results.append(CaseResult(case.id, case.question, ok, ans.sql, None, len(ans.attempts),
                                  "" if ok else f"got {len(ans.rows)} rows, expected {len(gold_rows)}"))

    answered = results
    summary = {
        "cases": len(results),
        "execution_accuracy": round(sum(r.correct for r in answered) / len(answered), 3) if answered else None,
        "first_try_success": round(sum(r.correct and r.attempts <= 1 for r in answered) / len(answered), 3) if answered else None,
        "engine": svc.engine_name,
        "dialect": svc.dialect,
    }
    if report_dir:
        out = Path(report_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "eval_results.json").write_text(json.dumps({"summary": summary, "cases": [asdict(r) for r in results]}, indent=2))
        lines = ["# Text-to-SQL evaluation", "", f"Engine `{summary['engine']}` on `{summary['dialect']}`", "",
                 "| Metric | Value |", "|---|---|"]
        lines += [f"| {k} | {v} |" for k, v in summary.items() if k not in {"engine", "dialect"}]
        lines += ["", "| id | correct | question | note |", "|---|---|---|---|"]
        lines += [f"| {r.id} | {'yes' if r.correct else 'NO'} | {r.question} | {r.note or (r.error or '')} |" for r in results]
        (out / "eval_report.md").write_text("\n".join(lines) + "\n")
    return {"summary": summary, "results": results}
