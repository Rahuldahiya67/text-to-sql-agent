from t2sql.service import suggest_chart


def test_simple_question(service):
    ans = service.ask("What was total revenue in 2025?")
    assert ans.ok and len(ans.rows) == 1
    assert ans.summary.startswith("Revenue (INR): Rs")
    assert ans.tables_used == ["order_items", "orders"]
    assert ans.chart is None


def test_grouped_question_returns_bar_chart(service):
    ans = service.ask("Freight cost by transporter")
    assert ans.ok and len(ans.rows) == 5
    assert ans.chart == {"type": "bar", "x": "transporter", "y": "freight_cost", "series": None}
    assert "highest freight cost" in ans.summary


def test_time_series_returns_line_chart_in_order(service):
    ans = service.ask("Monthly revenue trend for 2025")
    assert [r[0] for r in ans.rows] == [f"2025-{m:02d}" for m in range(1, 13)]
    assert ans.chart["type"] == "line"
    assert "went from" in ans.summary


def test_follow_up_uses_session_memory(service):
    sid = "test-followup"
    service.memory.clear(sid)
    first = service.ask("Revenue by region in 2025", sid)
    second = service.ask("now split that by category", sid)
    assert len(first.rows) == 4 and len(second.rows) == 20
    assert second.columns == ["region", "category", "revenue"]
    history = service.memory.history(sid)
    assert [h["question"] for h in history] == ["Revenue by region in 2025", "now split that by category"]


def test_sessions_are_isolated(service):
    service.ask("Revenue by region in 2025", "iso-a")
    ans = service.ask("now split that by category", "iso-b")
    assert not ans.ok


def test_errors_are_reported_not_raised(service):
    ans = service.ask("Freight cost by product")
    assert not ans.ok and "double counting" in ans.error
    off = service.ask("What's the weather in Delhi?")
    assert not off.ok and off.summary


def test_chart_suggestion_rules():
    assert suggest_chart(["n"], [[1], [2]], set()) is None
    assert suggest_chart(["month", "v"], [["2025-01", 1], ["2025-02", 2]], set())["type"] == "line"
    assert suggest_chart(["region", "cat", "v"], [["N", "A", 1], ["S", "B", 2]], set()) == {
        "type": "bar", "x": "region", "y": "v", "series": "cat"}
