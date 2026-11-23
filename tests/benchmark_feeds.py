"""Synthetic 200-feed ingestion benchmark: no network, credentials or AI calls.

Run from the project root: PYTHONPATH=. python tests/benchmark_feeds.py
"""

import asyncio
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import aiohttp

from newsbot.config import Config
from newsbot.feeds import FeedWorker
from newsbot.store import Store


async def benchmark():
    started = time.monotonic()
    with tempfile.TemporaryDirectory() as temp:
        config = Config(Path(temp) / "config.json")
        store = Store(Path(temp) / "news.db")
        for index in range(200):
            store.add_feed(f"https://example.com/feed/{index}", f"Synthetic {index}")
        phase = 0

        async def fetch(session, url, headers=None, final_url=False):
            index = url.rsplit("/", 1)[1]
            entries = "".join(
                f"<item><guid>{index}-{item}</guid><title>Report {index}-{item}</title>"
                f"<link>https://example.com/news/{index}/{item}</link>"
                "<description>Officials report an infrastructure incident. Details remain uncertain.</description></item>"
                for item in range(20 + phase)
            )
            await asyncio.sleep(0)
            result = 200, ("<rss version='2.0'><channel><title>Test</title>" + entries + "</channel></rss>").encode(), {}
            return (*result, url) if final_url else result

        async with aiohttp.ClientSession() as session:
            worker = FeedWorker(store, config, session)
            with patch("newsbot.feeds.fetch_public", fetch):
                task = asyncio.create_task(worker.run())
                try:
                    async with asyncio.timeout(30):
                        while store.one("SELECT COUNT(*) n FROM feeds WHERE initialized=1")["n"] != 200:
                            await asyncio.sleep(0.01)
                        phase = 1
                        store.execute("UPDATE feeds SET next_poll=0")
                        while store.one("SELECT COUNT(*) n FROM items WHERE status='queued'")["n"] != 200:
                            await asyncio.sleep(0.01)
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
        print("Synthetic feeds:", 200)
        print("Items:", store.one("SELECT COUNT(*) n FROM items")["n"])
        print("Baseline:", store.one("SELECT COUNT(*) n FROM items WHERE status='baseline'")["n"])
        print("New queued items:", store.one("SELECT COUNT(*) n FROM items WHERE status='queued'")["n"])
        print("Feed failures:", store.one("SELECT COUNT(*) n FROM feeds WHERE failures>0")["n"])
        print("Elapsed seconds:", round(time.monotonic() - started, 2))
        print("Database page bytes:", store.storage()["bytes"])
        store.close()


if __name__ == "__main__":
    asyncio.run(benchmark())
