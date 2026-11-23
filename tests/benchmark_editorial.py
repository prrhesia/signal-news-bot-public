"""Compare bounded story context in two releases without an AI or Signal account.

Point PYTHONPATH at either release and run this same file with the same Python.
"""

import asyncio
import json
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from newsbot.ai import ModelPool
from newsbot.config import Config
from newsbot.engine import Engine
from newsbot.store import Store


class Completion:
    status, headers = 200, {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass


class FakeProvider:
    def __init__(self):
        self.input_bytes = []

    def post(self, url, json, **kwargs):
        result = Completion()
        self.input_bytes.append(sum(len(msg["content"].encode()) for msg in json["messages"]))
        result.payload = json["messages"][-1]["content"]
        return result


async def answer(response):
    payload = json.loads(response.payload)
    decisions = [{"id": item["id"], "post": False, "importance": 55, "text": "", "emoji": "",
                  "on_focus": True, "excluded": False, "duplicate": True, "complex": False,
                  "reason": "Synthetic repeat"} for item in payload["articles"]]
    return {"model": "mock-editor", "choices": [{"message": {"content": json.dumps({"decisions": decisions})}}]}


async def benchmark():
    now = time.time()
    with tempfile.TemporaryDirectory() as folder:
        config = Config(Path(folder) / "config.json")
        config.update(group_id="fixture-group", mode="live", focus="Prioritize significant infrastructure outages. " * 35,
                      models=[{"name": "mock", "provider": "groq", "model": "mock-editor", "key": "fixture-key"}])
        store = Store(Path(folder) / "news.db")
        store.add_feed("https://example.net/feed", "Fixture feed")
        rules = config.rules_key()
        story = "International airport reports brief runway lighting outage; normal operations resumed."
        snippet = "Flights continued without further cancellations. No injury was reported. " * 10
        pending = [(f"Region {i}: {story}", snippet) if i < 48 else
                   (f"Region {i}: a second outage closes the airport after lighting restored.",
                    "The regional operator halted departures; crews confirmed a separate power failure.")
                   for i in range(60)]
        with store.db:
            store.db.executemany("""INSERT INTO items(feed_id,guid,url,title,description,published,discovered,status,summary,rules,sent_at)
                VALUES(1,?,? ,?,?,?,?,'sent',?,?,?)""",
                [(str(i), f"https://example.net/post/{i}", f"Region {i % 75}: {story}", snippet,
                  now - i * 220, now - i * 220, story, rules, now - i * 220) for i in range(2500)])
            store.db.executemany("""INSERT INTO items(feed_id,guid,url,title,description,published,discovered,status)
                VALUES(1,? ,? ,? ,? ,?,?,'queued')""",
                [(f"new-{i}", f"https://example.net/new/{i}", title, desc, now, now)
                 for i, (title, desc) in enumerate(pending)])
        provider = FakeProvider()
        engine = Engine(store, config, ModelPool(store, config, provider), SimpleNamespace(connected=False, send=AsyncMock()))
        started = time.monotonic()
        with patch("newsbot.ai.read_json", answer):
            for _ in range(10):
                await engine.process()
        print("Screened rows:", store.one("SELECT count(*) AS n FROM items WHERE status='skipped'")["n"])
        print("Mock requests:", len(provider.input_bytes))
        print("Representative AI input bytes (mean):",
              int(sum(provider.input_bytes) / len(provider.input_bytes)) if provider.input_bytes else "n/a")
        print("Database page bytes:", store.storage()["bytes"])
        print("Elapsed seconds:", round(time.monotonic() - started, 3))
        store.close()


if __name__ == "__main__":
    asyncio.run(benchmark())
