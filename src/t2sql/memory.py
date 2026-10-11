"""Conversation memory, so follow-up questions can refer to earlier ones.

Each session stores its turns: question, final SQL, the offline plan (if any) and a timestamp.
MongoDB is a natural fit: one document per session, turns appended with $push and trimmed with $slice.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Protocol


class ConversationStore(Protocol):
    def history(self, session_id: str, limit: int = 5) -> list[dict]: ...
    def append(self, session_id: str, turn: dict) -> None: ...
    def clear(self, session_id: str) -> None: ...


class InMemoryStore:
    def __init__(self, max_turns: int = 50):
        self.sessions: dict[str, list[dict]] = {}
        self.max_turns = max_turns

    def history(self, session_id: str, limit: int = 5) -> list[dict]:
        return list(self.sessions.get(session_id, [])[-limit:])

    def append(self, session_id: str, turn: dict) -> None:
        turns = self.sessions.setdefault(session_id, [])
        turns.append({**turn, "created_at": datetime.now(timezone.utc).isoformat()})
        del turns[:-self.max_turns]

    def clear(self, session_id: str) -> None:
        self.sessions.pop(session_id, None)


class MongoStore:
    def __init__(self, uri: str | None, db: str, collection: str, max_turns: int = 50, client: Any = None):
        if client is None:
            from pymongo import MongoClient

            if not uri:
                raise ValueError("T2SQL_MONGO_URI is required for the mongo memory backend")
            client = MongoClient(uri)
        self.collection = client[db][collection]
        self.max_turns = max_turns

    def history(self, session_id: str, limit: int = 5) -> list[dict]:
        doc = self.collection.find_one({"_id": session_id}, {"turns": {"$slice": -limit}})
        return list(doc.get("turns", [])) if doc else []

    def append(self, session_id: str, turn: dict) -> None:
        now = datetime.now(timezone.utc)
        self.collection.update_one(
            {"_id": session_id},
            {
                "$push": {"turns": {"$each": [{**turn, "created_at": now}], "$slice": -self.max_turns}},
                "$set": {"updated_at": now},
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )

    def clear(self, session_id: str) -> None:
        self.collection.delete_one({"_id": session_id})


def build_memory(settings) -> ConversationStore:
    if settings.memory_backend == "mongo":
        return MongoStore(settings.mongo_uri, settings.mongo_db, settings.mongo_collection)
    return InMemoryStore()
