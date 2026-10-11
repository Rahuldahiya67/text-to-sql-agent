"""Streamlit chat UI.

    streamlit run src/t2sql/ui/app.py

Runs the agent in-process by default. Set T2SQL_API_URL (e.g. http://localhost:8000) to call
the FastAPI service instead.
"""

from __future__ import annotations

import os
import uuid

import pandas as pd
import streamlit as st

st.set_page_config(page_title="Ask your data", layout="wide")

API_URL = os.getenv("T2SQL_API_URL")


@st.cache_resource
def get_service():
    from t2sql.service import TextToSQLService

    return TextToSQLService()


def ask(question: str, session_id: str) -> dict:
    if API_URL:
        import httpx

        headers = {"X-API-Key": os.environ["T2SQL_API_KEY"]} if os.getenv("T2SQL_API_KEY") else {}
        resp = httpx.post(f"{API_URL}/ask", json={"question": question, "session_id": session_id}, headers=headers, timeout=60)
        resp.raise_for_status()
        return resp.json()
    return get_service().ask(question, session_id).to_dict()


def render(answer: dict) -> None:
    if not answer["ok"]:
        st.warning(answer.get("summary") or answer.get("error"))
        return
    st.markdown(f"**{answer['title']}**")
    st.write(answer["summary"])
    df = pd.DataFrame(answer["rows"], columns=answer["columns"])
    chart = answer.get("chart")
    if chart:
        data = df.pivot_table(index=chart["x"], columns=chart["series"], values=chart["y"], aggfunc="sum") if chart.get("series") else df.set_index(chart["x"])[[chart["y"]]]
        (st.line_chart if chart["type"] == "line" else st.bar_chart)(data)
    st.dataframe(df, width="stretch", hide_index=True)
    with st.expander("SQL"):
        st.code(answer["sql"], language="sql")
        st.caption(f"{answer['engine']} · tables: {', '.join(answer['tables_used'])} · {answer['elapsed_ms']} ms"
                   + (" · result truncated" if answer.get("truncated") else ""))


st.title("Ask your data")
st.caption("Plain-English questions over the sales and logistics database. Follow-ups work: try "
           "*'revenue by region in 2025'*, then *'now split that by category'*.")

if "session_id" not in st.session_state:
    st.session_state.session_id = uuid.uuid4().hex[:12]
    st.session_state.messages = []

with st.sidebar:
    st.subheader("Examples")
    examples = [
        "Revenue by region in 2025",
        "Monthly revenue trend for 2025",
        "Top 5 customers by revenue",
        "Which transporter has the worst on-time delivery rate?",
        "Freight cost by transporter",
        "How many orders did the Pune Plant get in March 2026?",
    ]
    for ex in examples:
        if st.button(ex, width="stretch"):
            st.session_state.pending = ex
    if st.button("New conversation", type="secondary"):
        st.session_state.session_id = uuid.uuid4().hex[:12]
        st.session_state.messages = []

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.write(msg["content"]) if msg["role"] == "user" else render(msg["content"])

question = st.chat_input("Ask a question about sales, orders, shipments...") or st.session_state.pop("pending", None)
if question:
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.write(question)
    with st.chat_message("assistant"):
        with st.spinner("Writing SQL..."):
            answer = ask(question, st.session_state.session_id)
        render(answer)
    st.session_state.messages.append({"role": "assistant", "content": answer})
