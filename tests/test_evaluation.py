from t2sql.evaluation import load_cases, results_match, run_evaluation

from .conftest import EVAL_CASES


def test_results_match_rules():
    assert results_match([["a", 1.0], ["b", 2.0]], [["b", 2.0], ["a", 1.0]], ordered=False)
    assert not results_match([["a", 1.0], ["b", 2.0]], [["b", 2.0], ["a", 1.0]], ordered=True)
    assert results_match([[100.004]], [[100.0]], ordered=False)          # rounding noise
    assert not results_match([[101.0]], [[100.0]], ordered=False)
    assert not results_match([["a", 1]], [["a", 1], ["b", 2]], ordered=False)


def test_eval_file_is_valid():
    cases = load_cases(EVAL_CASES)
    assert len(cases) >= 20
    assert any(c.session for c in cases) and any(c.expect_error for c in cases)


def test_execution_accuracy_sqlite(service, tmp_path):
    out = run_evaluation(service, EVAL_CASES, tmp_path)
    misses = [r.id for r in out["results"] if not r.correct]
    assert out["summary"]["execution_accuracy"] >= 0.95, misses
    assert (tmp_path / "eval_report.md").exists()
