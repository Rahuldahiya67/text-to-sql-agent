from t2sql.memory import InMemoryStore, MongoStore


def test_in_memory_store_keeps_recent_turns():
    store = InMemoryStore(max_turns=3)
    for i in range(5):
        store.append("s", {"question": f"q{i}"})
    assert [t["question"] for t in store.history("s", limit=10)] == ["q2", "q3", "q4"]
    assert [t["question"] for t in store.history("s", limit=2)] == ["q3", "q4"]
    store.clear("s")
    assert store.history("s") == []


class FakeCollection:
    """Implements the subset of pymongo used by MongoStore, including $push/$each/$slice."""

    def __init__(self):
        self.docs = {}
        self.calls = []

    def update_one(self, flt, update, upsert=False):
        self.calls.append(update)
        doc = self.docs.setdefault(flt["_id"], {"_id": flt["_id"], "turns": []})
        push = update["$push"]["turns"]
        doc["turns"] = (doc["turns"] + push["$each"])[push["$slice"]:]
        doc.update(update.get("$set", {}))

    def find_one(self, flt, projection):
        doc = self.docs.get(flt["_id"])
        if not doc:
            return None
        n = projection["turns"]["$slice"]
        return {"_id": doc["_id"], "turns": doc["turns"][n:]}

    def delete_one(self, flt):
        self.docs.pop(flt["_id"], None)


def test_mongo_store_uses_push_with_slice():
    coll = FakeCollection()
    store = MongoStore(None, "db", "sessions", max_turns=2, client={"db": {"sessions": coll}})
    for q in ["a", "b", "c"]:
        store.append("s1", {"question": q, "sql": "SELECT 1"})
    assert [t["question"] for t in store.history("s1", limit=5)] == ["b", "c"]
    update = coll.calls[0]
    assert update["$push"]["turns"]["$slice"] == -2 and "$setOnInsert" in update
    store.clear("s1")
    assert store.history("s1") == []
