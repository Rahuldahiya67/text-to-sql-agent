# Text-to-SQL Analytics Agent

Ask a business question in plain English and get the **SQL**, the **result table**, a **chart** and a **one-line summary**. It is built to be safe to point at a real database: the generated SQL is parsed and checked before it runs, the connection itself is read-only, and queries have a time limit.

```
> Revenue by region in 2025
North has the highest revenue at Rs 67.38 Cr (44% of the total shown).

> now split that by category
North / Motors has the highest revenue at Rs 27.13 Cr (18% of the total shown).

> only the West region
Motors has the highest revenue at Rs 14.66 Cr (35% of the total shown).
```

Follow-up questions work because each conversation is stored (in MongoDB or in memory) and the next question is resolved against the previous one.

## What it does

| Capability | How |
|---|---|
| **Schema-aware** | Introspects tables, keys, row counts and sample values. Picks only the tables relevant to the question, plus the tables needed to join them |
| **Two ways to write SQL** | An **LLM agent** (OpenAI or Claude) with tool calling, or an **offline planner** that composes SQL from a semantic layer, with no API key needed |
| **Self-correcting** | The agent runs its query; if the database returns an error, the error goes back to the model to fix it (up to 3 attempts) |
| **Guardrails** | Parsed with sqlglot: one statement, SELECT only, allow-listed tables, no dangerous functions, LIMIT enforced |
| **Read-only by construction** | SQLite opened with `mode=ro`; PostgreSQL sessions set to `default_transaction_read_only` with a `statement_timeout` |
| **Conversation memory** | MongoDB document per session (`$push` + `$slice`), or in-memory |
| **Semantic layer** | `revenue`, `on_time_rate`, `freight_cost` and other metrics defined once in YAML, so every answer uses the same formula |
| **Evaluation** | 24 questions with hand-written gold SQL, scored by **execution accuracy**, on both SQLite and PostgreSQL |
| **Interfaces** | CLI, FastAPI service, Streamlit chat UI with charts |

## Architecture

```mermaid
flowchart TB
    Q[Question + session id] --> M[(Conversation memory<br/>MongoDB)]
    M --> R{Provider}
    R -- offline --> P[Planner<br/>question -> plan -> SQL<br/>via semantic layer]
    R -- openai / anthropic --> S[Table selection<br/>relevant tables + join path]
    S --> A[LLM agent<br/>tools: run_sql, final_answer]
    A -- run_sql --> G
    G -- error --> A
    P --> G[Guardrails<br/>sqlglot AST checks + LIMIT]
    G --> D[(Read-only DB<br/>SQLite / PostgreSQL<br/>statement timeout)]
    D --> O[Rows + chart spec + summary]
    O --> M
```

## Quick start (no API key, no database server)

```bash
git clone https://github.com/rahuldahiya67/text-to-sql-agent
cd text-to-sql-agent
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[all,dev]"

t2sql seed                                  # builds data/analytics.db (SQLite)
t2sql ask "Top 5 customers by revenue"
t2sql chat                                  # interactive, with follow-ups
streamlit run src/t2sql/ui/app.py           # chat UI with charts
t2sql serve                                 # API at http://localhost:8000/docs
```

## The demo database

A fictional industrial-goods distributor: 4,000 orders, 7,500 order lines and 3,800 shipments from January 2025 to June 2026, across 3 plants, 4 regions, 60 customers, 13 products and 5 transporters. The data is synthetic and generated with a fixed seed, so results are reproducible.

```
regions ─┬─ customers ─── orders ─┬─ order_items ─── products
plants ──┘                 │      └─ shipments ───── transporters
                           └── plants
```

## Questions it handles

| Question | What happens |
|---|---|
| `What was total revenue in 2025?` | single number, cancelled orders excluded |
| `Revenue by region in 2025` | grouped, bar chart |
| `Show the monthly revenue trend for 2025` | time series, line chart, change and peak in the summary |
| `Which transporter has the worst on-time delivery rate?` | ascending ranking, top 1 |
| `How many orders did the Pune Plant get in March 2026?` | value filter + month filter |
| `Which category sold the most units in Q1 2026?` | quarter filter, ranking |
| `now split that by category` | follow-up: adds a breakdown |
| `only the West region` / `what about units?` / `top 3 instead` | follow-ups: filter, metric swap, re-rank |
| `Freight cost by product` | **refused**: freight is per shipment, so splitting it by product would double count |

## Safety model

Three independent layers, so one mistake doesn't become a data incident:

1. **SQL guard** (`guard.py`) works on the parsed syntax tree, not on regexes. It rejects anything that isn't a single read query (`DELETE`, `DROP`, `SELECT ... INTO`, multiple statements), tables outside the allow-list, schema-qualified system tables, and functions like `pg_sleep` or `pg_read_file`. It adds or caps `LIMIT`.
2. **Read-only connection** (`db.py`). Even if a write slipped past the guard, SQLite rejects it (`attempt to write a readonly database`) and PostgreSQL rejects it (`cannot execute DELETE in a read-only transaction`).
3. **Time limit.** `statement_timeout` on PostgreSQL and a progress-handler deadline on SQLite stop runaway joins.

For production, also connect with a database role that only has `SELECT` grants on the reporting schema.

## Using an LLM

```bash
cp .env.example .env
# T2SQL_LLM_PROVIDER=openai     + T2SQL_OPENAI_API_KEY=sk-...
# T2SQL_LLM_PROVIDER=anthropic  + T2SQL_ANTHROPIC_API_KEY=sk-ant-...
```

The agent receives only the **relevant tables** (with descriptions, keys and sample values so it spells filters correctly), the **approved metric formulas** from the semantic layer, and the last few questions with their SQL. It has two tools:

- `run_sql(sql)`: runs the query through the guardrails and returns the first rows, or the error
- `final_answer(sql, summary)`: ends the loop

Failed queries are fed back so the model can correct them; after `T2SQL_MAX_ATTEMPTS` failures it stops and reports the last error. OpenAI and Anthropic use different tool-calling formats; both adapt to one loop (`agent.py`).

## PostgreSQL and MongoDB

```bash
docker compose up --build        # Postgres + MongoDB + API + UI, seeded automatically
```

Or point at your own:

```bash
T2SQL_DATABASE_URL=postgresql+psycopg://user:pass@host:5432/analytics
T2SQL_MEMORY_BACKEND=mongo
T2SQL_MONGO_URI=mongodb+srv://...
t2sql seed --url $T2SQL_DATABASE_URL        # demo data, optional
```

## Evaluation

```bash
t2sql eval --report reports
```

`eval/cases.jsonl` holds 24 cases: single questions, two multi-turn conversations (follow-ups share memory) and two questions that should be refused. Each answerable case has **gold SQL written by hand**, independently of the planner (different joins, date ranges instead of date functions, and so on). A generated query counts as correct when it returns the same result as the gold query: **execution accuracy**, the metric used by the Spider and BIRD text-to-SQL benchmarks. It's a better test than comparing SQL text, because many different queries are equally correct.

| Engine | Database | Execution accuracy | First-try success |
|---|---|---|---|
| offline planner | SQLite | **24 / 24** | 24 / 24 |
| offline planner | PostgreSQL 16 | **24 / 24** | 24 / 24 |

CI runs the tests against both databases (PostgreSQL as a service container) and fails if execution accuracy drops below 95%. Run the same eval with an LLM provider to compare models.

## API

```bash
curl -X POST localhost:8000/ask -H "content-type: application/json" \
  -d '{"question": "Freight cost by transporter", "session_id": "demo"}'
```

Response (abridged):

```json
{
  "ok": true,
  "title": "Freight cost (INR) by Transporter",
  "sql": "SELECT t.name AS transporter, SUM(s.freight_cost) AS freight_cost FROM orders AS o JOIN shipments AS s ...",
  "columns": ["transporter", "freight_cost"],
  "rows": [["Shree Ram Logistics", 8960622.57], ["Om Sai Transport", 8272622.26], "..."],
  "summary": "Shree Ram Logistics has the highest freight cost at Rs 89.61 lakh (22% of the total shown).",
  "chart": {"type": "bar", "x": "transporter", "y": "freight_cost", "series": null},
  "tables_used": ["orders", "shipments", "transporters"],
  "engine": "offline-planner"
}
```

| Endpoint | Purpose |
|---|---|
| `POST /ask` | question + optional `session_id` |
| `GET /sessions/{id}` / `DELETE /sessions/{id}` | read or clear conversation memory |
| `GET /schema` | tables, columns, keys, descriptions |
| `GET /health` | engine, dialect, table count |

## Project structure

```
src/t2sql/
  semantic.yaml   metrics, dimensions, join graph, table descriptions
  semantic.py     loads the semantic layer, learns filter values, selects tables
  planner.py      offline: question -> QueryPlan -> SQL, follow-up handling, fan-out guard
  agent.py        LLM tool-calling loop + OpenAI / Anthropic adapters
  guard.py        sqlglot-based SQL validation
  db.py           read-only engines, introspection, timed execution
  memory.py       conversation memory (MongoDB / in-memory)
  service.py      orchestration, summaries, chart suggestion
  evaluation.py   execution-accuracy evaluation
  api.py  cli.py  ui/app.py  seed.py
eval/cases.jsonl  24 evaluation cases with gold SQL
tests/            71 tests (fake LLM clients; PostgreSQL tests run when T2SQL_TEST_PG_URL is set)
```

## Design decisions

- **A semantic layer, not just a schema dump.** Letting a model invent "revenue" each time gives a different number every time someone asks. Metrics are defined once (including rules like "exclude cancelled orders") and both the planner and the LLM prompt use them.
- **Refusing fan-out instead of returning a wrong number.** Shipment-level amounts summed over order lines are counted once per line. The planner knows each metric's grain and refuses those combinations with an explanation.
- **Checks on the syntax tree.** Regex blocklists miss `SELECT ... INTO`, comments and multi-statement tricks. sqlglot gives an AST to inspect, and the same library rewrites the `LIMIT`.
- **Defence in depth.** The guard is one layer; the read-only connection and timeout still hold if the guard has a bug.
- **Evaluation by execution, on two databases.** It catches dialect bugs (date functions, quarter formats) that unit tests on SQL strings would miss.

## Possible next steps

- Few-shot retrieval: add the most similar solved questions from a query log to the prompt
- Row-level security: inject per-user filters (e.g. a user's region) into every plan
- Cache answers for repeated questions within a short window
- Explain-plan check to reject very expensive queries before they run
