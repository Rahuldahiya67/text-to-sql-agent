"""LLM agent: writes SQL with tool calling and corrects itself from database errors.

The model gets the relevant part of the schema and two tools:

    run_sql(sql)                 executes a read-only query, returns columns + first rows, or the error
    final_answer(sql, summary)   ends the loop with the query whose result answers the question

Every SQL string goes through the same guardrails as the offline planner before it reaches
the database, and the loop stops after ``max_attempts`` failed queries.
OpenAI and Anthropic use different tool-calling formats; both are adapted to one loop.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

SYSTEM_PROMPT = """You are a senior analytics engineer. You answer business questions by writing {dialect} SQL
against the database described below. Work like this:
1. Write one SELECT query and call run_sql to test it.
2. If it errors or the result looks wrong, fix it and call run_sql again.
3. When the result answers the question, call final_answer with that exact SQL and a one-sentence summary
   of what the numbers show.

Rules:
- Read-only: SELECT / WITH only. Never modify data.
- Use only the tables and columns listed. Use the listed sample values for filters, with their exact spelling.
- Prefer the approved metric formulas below so numbers match company reports.
- Alias every computed column with a readable snake_case name. Add ORDER BY for rankings and time series.
- Exclude cancelled orders from revenue and quantity unless the user asks about cancellations.

Approved metrics:
{metrics}

Schema:
{schema}
"""

TOOLS = [
    {
        "name": "run_sql",
        "description": "Execute a read-only SQL query and see the columns and first rows, or the database error.",
        "parameters": {
            "type": "object",
            "properties": {"sql": {"type": "string", "description": "A single SELECT statement"}},
            "required": ["sql"],
        },
    },
    {
        "name": "final_answer",
        "description": "Finish with the SQL whose result answers the question and a one-sentence summary.",
        "parameters": {
            "type": "object",
            "properties": {
                "sql": {"type": "string"},
                "summary": {"type": "string", "description": "One sentence describing what the result shows"},
            },
            "required": ["sql", "summary"],
        },
    },
]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ModelTurn:
    text: str
    tool_calls: list[ToolCall]
    raw: Any = None          # provider-specific message, appended back to the history


class ChatModel(Protocol):
    name: str

    def start(self, system: str, user: str) -> list[Any]: ...
    def step(self, messages: list[Any]) -> ModelTurn: ...
    def tool_results(self, turn: ModelTurn, results: list[tuple[ToolCall, str]]) -> list[Any]: ...


@dataclass
class AgentResult:
    sql: str | None
    summary: str | None
    attempts: list[dict] = field(default_factory=list)   # [{"sql":..., "error":...|None}]


class AgentError(RuntimeError):
    pass


class SQLAgent:
    def __init__(self, model: ChatModel, max_attempts: int = 3, max_steps: int = 8):
        self.model = model
        self.max_attempts = max_attempts
        self.max_steps = max_steps

    def run(self, question: str, system: str, history: list[dict], execute: Callable[[str], dict]) -> AgentResult:
        """``execute(sql)`` returns {"columns": [...], "rows": [...]} or {"error": "..."}."""
        context = ""
        if history:
            context = "Previous questions in this conversation (most recent last):\n" + "\n".join(
                f"Q: {h['question']}\nSQL: {h.get('sql') or '-'}" for h in history
            ) + "\n\nResolve references like 'that' or 'same' using them.\n\n"
        messages = self.model.start(system, f"{context}Question: {question}")
        result = AgentResult(sql=None, summary=None)
        failures = 0

        for _ in range(self.max_steps):
            turn = self.model.step(messages)
            if turn.raw is not None:
                messages.append(turn.raw)
            if not turn.tool_calls:
                messages.append(self._nudge())
                continue
            outputs = []
            for call in turn.tool_calls:
                sql = str(call.arguments.get("sql", ""))
                out = execute(sql)
                result.attempts.append({"sql": sql, "error": out.get("error")})
                if call.name == "final_answer" and "error" not in out:
                    result.sql, result.summary = out.get("sql", sql), call.arguments.get("summary")
                    return result
                if "error" in out:
                    failures += 1
                    if failures >= self.max_attempts:
                        raise AgentError(f"gave up after {failures} failed queries; last error: {out['error']}")
                    payload = f"ERROR: {out['error']}\nFix the query and try again."
                else:
                    preview = {"columns": out["columns"], "rows": out["rows"][:15], "row_count": len(out["rows"])}
                    payload = json.dumps(preview, default=str)
                    if call.name == "final_answer":
                        payload = "ok"
                outputs.append((call, payload))
            messages.extend(self.model.tool_results(turn, outputs))
        raise AgentError("the model did not finish within the step limit")

    def _nudge(self) -> Any:
        return self.model.user_message("Use the run_sql tool, then final_answer.")  # type: ignore[attr-defined]


# -------------------------------------------------------------------------------- providers
class OpenAIChatModel:
    def __init__(self, api_key: str | None, model: str, client: Any = None):
        if client is None:
            from openai import OpenAI

            if not api_key:
                raise ValueError("T2SQL_OPENAI_API_KEY is required for the openai provider")
            client = OpenAI(api_key=api_key)
        self.client, self.model, self.name = client, model, f"openai:{model}"
        self.tools = [{"type": "function", "function": t} for t in TOOLS]

    def start(self, system: str, user: str) -> list[Any]:
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def user_message(self, text: str) -> dict:
        return {"role": "user", "content": text}

    def step(self, messages: list[Any]) -> ModelTurn:
        resp = self.client.chat.completions.create(model=self.model, messages=messages, tools=self.tools, temperature=0)
        msg = resp.choices[0].message
        calls = [
            ToolCall(tc.id, tc.function.name, json.loads(tc.function.arguments or "{}"))
            for tc in (msg.tool_calls or [])
        ]
        raw = {"role": "assistant", "content": msg.content or "", "tool_calls": [
            {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": json.dumps(c.arguments)}}
            for c in calls
        ]} if calls else {"role": "assistant", "content": msg.content or ""}
        return ModelTurn(msg.content or "", calls, raw)

    def tool_results(self, turn: ModelTurn, results: list[tuple[ToolCall, str]]) -> list[Any]:
        return [{"role": "tool", "tool_call_id": call.id, "content": content} for call, content in results]


class AnthropicChatModel:
    def __init__(self, api_key: str | None, model: str, client: Any = None):
        if client is None:
            from anthropic import Anthropic

            if not api_key:
                raise ValueError("T2SQL_ANTHROPIC_API_KEY is required for the anthropic provider")
            client = Anthropic(api_key=api_key)
        self.client, self.model, self.name = client, model, f"anthropic:{model}"
        self.tools = [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]} for t in TOOLS]
        self.system = ""

    def start(self, system: str, user: str) -> list[Any]:
        self.system = system
        return [{"role": "user", "content": user}]

    def user_message(self, text: str) -> dict:
        return {"role": "user", "content": text}

    def step(self, messages: list[Any]) -> ModelTurn:
        resp = self.client.messages.create(
            model=self.model, max_tokens=1500, temperature=0, system=self.system, tools=self.tools, messages=messages,
        )
        text = "".join(b.text for b in resp.content if b.type == "text")
        calls = [ToolCall(b.id, b.name, dict(b.input)) for b in resp.content if b.type == "tool_use"]
        content = []
        for b in resp.content:
            if b.type == "text":
                content.append({"type": "text", "text": b.text})
            elif b.type == "tool_use":
                content.append({"type": "tool_use", "id": b.id, "name": b.name, "input": dict(b.input)})
        return ModelTurn(text, calls, {"role": "assistant", "content": content})

    def tool_results(self, turn: ModelTurn, results: list[tuple[ToolCall, str]]) -> list[Any]:
        return [{"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": call.id, "content": content,
             **({"is_error": True} if content.startswith("ERROR") else {})}
            for call, content in results
        ]}]


def build_model(settings) -> ChatModel | None:
    if settings.llm_provider == "openai":
        return OpenAIChatModel(settings.openai_api_key, settings.openai_model)
    if settings.llm_provider == "anthropic":
        return AnthropicChatModel(settings.anthropic_api_key, settings.anthropic_model)
    return None
