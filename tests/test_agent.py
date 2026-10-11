"""The LLM path, tested with scripted fake models (no network)."""

import json
from types import SimpleNamespace

import pytest

from t2sql.agent import AnthropicChatModel, ModelTurn, OpenAIChatModel, ToolCall
from t2sql.config import Settings
from t2sql.service import TextToSQLService


class ScriptedModel:
    """Plays back a list of tool calls and records what the agent sent back."""

    name = "scripted"

    def __init__(self, script):
        self.script = list(script)
        self.seen_results = []
        self.system = None

    def start(self, system, user):
        self.system, self.user = system, user
        return [{"role": "user", "content": user}]

    def user_message(self, text):
        return {"role": "user", "content": text}

    def step(self, messages):
        name, args = self.script.pop(0)
        return ModelTurn("", [ToolCall(f"call{len(self.script)}", name, args)], {"role": "assistant"})

    def tool_results(self, turn, results):
        self.seen_results.extend(content for _, content in results)
        return [{"role": "tool", "content": c} for _, c in results]


def _service(settings, model):
    return TextToSQLService(settings, model=model)


def test_agent_corrects_itself_from_database_error(settings):
    model = ScriptedModel([
        ("run_sql", {"sql": "SELECT region, SUM(revenue) FROM orders GROUP BY region"}),
        ("run_sql", {"sql": "SELECT r.name AS region, COUNT(*) AS orders FROM orders o JOIN customers c ON c.customer_id = o.customer_id JOIN regions r ON r.region_id = c.region_id GROUP BY r.name"}),
        ("final_answer", {"sql": "SELECT r.name AS region, COUNT(*) AS orders FROM orders o JOIN customers c ON c.customer_id = o.customer_id JOIN regions r ON r.region_id = c.region_id GROUP BY r.name", "summary": "North has the most orders."}),
    ])
    ans = _service(settings, model).ask("orders by region")
    assert ans.ok and ans.engine == "scripted"
    assert len(ans.rows) == 4 and ans.summary == "North has the most orders."
    assert ans.attempts[0]["error"] and "no such column" in ans.attempts[0]["error"]
    assert model.seen_results[0].startswith("ERROR:")
    # relevant schema only, with approved metric formulas
    assert "TABLE regions" in model.system and "SUM(oi.quantity * oi.unit_price)" in model.system


def test_agent_unsafe_sql_is_blocked_and_fed_back(settings):
    model = ScriptedModel([
        ("run_sql", {"sql": "DELETE FROM orders"}),
        ("final_answer", {"sql": "SELECT COUNT(*) AS n FROM orders", "summary": "4,000 orders."}),
    ])
    ans = _service(settings, model).ask("how many orders")
    assert ans.ok and ans.rows == [[4000]]
    assert "only SELECT" in model.seen_results[0]


def test_agent_gives_up_after_max_attempts(settings):
    bad = ("run_sql", {"sql": "SELECT nope FROM orders"})
    ans = _service(settings.model_copy(update={"max_attempts": 2}), ScriptedModel([bad, bad, bad])).ask("x y z")
    assert not ans.ok and "gave up after 2" in ans.error


def test_history_is_passed_for_followups(settings):
    q = "SELECT COUNT(*) AS n FROM orders"
    model = ScriptedModel([("final_answer", {"sql": q, "summary": "ok"}), ("final_answer", {"sql": q, "summary": "ok"})])
    svc = _service(settings, model)
    svc.ask("how many orders", "hist")
    svc.ask("and by region?", "hist")
    assert "Previous questions" in model.user and "Q: how many orders" in model.user


# ---- provider adapters ------------------------------------------------------------------
def test_openai_adapter_round_trip():
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        tc = SimpleNamespace(id="t1", function=SimpleNamespace(name="run_sql", arguments=json.dumps({"sql": "SELECT 1"})))
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=[tc]))])

    m = OpenAIChatModel(None, "gpt-test", client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    msgs = m.start("sys", "q")
    turn = m.step(msgs)
    assert turn.tool_calls[0].arguments == {"sql": "SELECT 1"}
    assert captured["tools"][0]["type"] == "function" and captured["temperature"] == 0
    assert turn.raw["tool_calls"][0]["id"] == "t1"
    assert m.tool_results(turn, [(turn.tool_calls[0], "ok")]) == [{"role": "tool", "tool_call_id": "t1", "content": "ok"}]


def test_anthropic_adapter_round_trip():
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(content=[
            SimpleNamespace(type="text", text="Testing"),
            SimpleNamespace(type="tool_use", id="tu1", name="run_sql", input={"sql": "SELECT 1"}),
        ])

    m = AnthropicChatModel(None, "claude-test", client=SimpleNamespace(messages=SimpleNamespace(create=create)))
    msgs = m.start("sys prompt", "q")
    turn = m.step(msgs)
    assert captured["system"] == "sys prompt" and captured["tools"][0]["input_schema"]["required"] == ["sql"]
    assert turn.tool_calls[0].name == "run_sql" and turn.text == "Testing"
    res = m.tool_results(turn, [(turn.tool_calls[0], "ERROR: bad")])
    assert res[0]["content"][0] == {"type": "tool_result", "tool_use_id": "tu1", "content": "ERROR: bad", "is_error": True}


def test_missing_api_key_is_a_clear_error():
    with pytest.raises(ValueError, match="T2SQL_OPENAI_API_KEY"):
        OpenAIChatModel(None, "gpt-test")
