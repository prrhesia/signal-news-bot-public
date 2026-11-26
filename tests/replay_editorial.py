"""Offline labelled replay; the fake editor is a probe, not a quality benchmark.

Run with PYTHONPATH pointing at the intended release. The same fixture and
response rules run against both Cadence and Lens.
"""

import asyncio
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from benchmark_editorial import FakeProvider
from newsbot.ai import ModelPool
from newsbot.config import Config
from newsbot.engine import Engine
from newsbot.store import Store
from newsbot.version import VERSION

NOW = 1_790_000_000


@dataclass(frozen=True)
class Case:
    key: str
    label: str
    focus: str
    prior: tuple[str, str]
    distractor: str
    incoming: tuple[str, str]


FERRY = ("Alderwick ferry crossings halted by storm at North Reach",
         "Three Alderwick crossings were canceled after storm damage at the North Reach dock.")
CLINIC = ("Morrowvale triage unit closed after pump failure",
          "The pump failed at Morrowvale and the triage unit shut for the day.")
MUSEUM = ("Bracken visual archive shuts after roof leak in east wing",
          "The east wing of the Bracken archive closed after a roof leak.")
CASES = (
    Case("ferry-rewrite", "rewrite", "Major public transport disruptions", FERRY,
         "Passenger travel plans for harbour {} ferry service",
         ("Alderwick ferry service halted; officials publish passenger travel plans",
          "The North Reach dock remains closed after the same storm damage.")),
    Case("clinic-rewrite", "rewrite", "Disruptions to urgent medical care", CLINIC,
         "Health board publishes clinic visitor arrangements for district {}",
         ("Regional health board publishes clinic visitor arrangements as Morrowvale triage unit closes",
          "Morrowvale staff redirected patients after the triage unit pump failure.")),
    Case("museum-rewrite", "rewrite", "Closures affecting public access to arts collections", MUSEUM,
         "New museum visitor ticket arrangements for borough {}",
         ("New museum visitor ticket arrangements after Bracken visual archive closure",
          "The Bracken visual archive remains shut after the roof leak.")),
    Case("ferry-correction", "follow-up", "Major public transport disruptions", FERRY,
         "Passenger travel plans for harbour {} ferry service",
         (FERRY[0], "Correction: only two crossings were canceled. The third used the south dock.")),
    Case("clinic-reopens", "follow-up", "Disruptions to urgent medical care", CLINIC,
         "Health board publishes clinic visitor arrangements for district {}",
         ("Morrowvale triage unit reopens after pump replacement",
          "Urgent patients can again use the Morrowvale unit.")),
    Case("other-ferry", "distinct", "Major public transport disruptions", FERRY,
         "Passenger travel plans for harbour {} ferry service",
         ("Dovecove ferry crossings halted by power failure at the south dock",
          "The separate Dovecove route stopped after an electrical fault.")),
    Case("other-topic", "off-focus", "Major public transport disruptions", FERRY,
         "Passenger travel plans for harbour {} ferry service",
         ("Alderwick ferry company publishes annual dividend notice", "There was no change to ferry service.")),
    Case("sparse", "insufficient", "Disruptions to urgent medical care", CLINIC,
         "Health board publishes clinic visitor arrangements for district {}",
         ("Morrowvale developments", "")),
)


async def replay(case):
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder)
        config = Config(path / "config.json")
        config.update(group_id="fixture-group", mode="live", focus=case.focus, fallback=False,
                      models=[{"name": "probe", "provider": "groq", "model": "mock-editor", "key": "fixture"}])
        store = Store(path / "news.db")
        store.add_feed("https://example.net/feed", "Synthetic feed")
        rules = config.rules_key()
        provider = FakeProvider()
        signal = SimpleNamespace(connected=True, send=AsyncMock(return_value={"timestamp": NOW * 1000}))
        engine = Engine(store, config, ModelPool(store, config, provider), signal)

        def record(title, description, age, status):
            url = "https://example.net/items/" + ("next" if status == "queued" else str(age))
            return store.execute("""INSERT INTO items
                (feed_id,url,title,description,published,discovered,status,summary,rules,sent_at)
                VALUES(1,?,?,?,?,?,?,?,?,?)""",
                (url, title, description, NOW - age, NOW - age, status, title if status == "sent" else "",
                 rules if status == "sent" else "", NOW - age if status == "sent" else None)).lastrowid

        try:
            prior = record(*case.prior, 7200, "sent")
            for n in range(8):
                record(case.distractor.format(n), "", 4200 - n * 60, "sent")
            for n in range(15):
                record(f"Unrelated historical clipping {n}", "", 3000 - n * 60, "sent")
            item = record(*case.incoming, 0, "queued")
            seen = None

            async def answer(response):
                nonlocal seen
                payload = json.loads(response.payload)
                article, = payload["articles"]
                samples = payload["recent_posts"] + article["related_posts"]
                seen = any((p["id"] == prior if "id" in p else p["title"] == case.prior[0]) for p in samples)
                duplicate = case.label == "rewrite" and seen
                pick = not duplicate and case.label not in {"off-focus", "insufficient"}
                text = case.incoming[1] if case.key == "ferry-correction" else article["title"]
                decision = {"id": article["id"], "post": pick, "importance": 90, "text": text if pick else "",
                            "emoji": "📰" if pick else "", "on_focus": case.label != "off-focus",
                            "excluded": False, "duplicate": duplicate, "complex": False, "reason": "Fixture decision"}
                return {"model": "mock-editor", "choices": [{"message": {
                    "content": json.dumps({"decisions": [decision]})}}]}

            with patch("newsbot.engine.time.time", return_value=NOW), patch("newsbot.ai.read_json", answer):
                await engine.process()
                analyzed = store.one("SELECT status,decision FROM items WHERE id=?", (item,))
                await engine.deliver()
            final = store.one("SELECT status FROM items WHERE id=?", (item,))["status"]
            return {
                "case": case.key, "label": case.label, "route": "normal", "prior_id": prior,
                "prior_in_request": seen, "requests": len(provider.input_bytes), "input_bytes": sum(provider.input_bytes),
                "evidence": json.loads(analyzed["decision"] or "{}").get("editor_context"),
                "after_analysis": analyzed["status"], "final": final, "mock_sends": signal.send.await_count,
            }
        finally:
            store.close()


async def main():
    rows = [await replay(case) for case in CASES]
    repeats = [r for r in rows if r["label"] == "rewrite"]
    followups = [r for r in rows if r["label"] == "follow-up"]
    print(json.dumps({
        "version": VERSION, "cases": rows,
        "repeat_prior_in_request": [sum(r["prior_in_request"] is True for r in repeats),
                                    sum(r["requests"] > 0 for r in repeats)],
        "repeat_escapes": [sum(r["final"] == "sent" for r in repeats), len(repeats)],
        "followups_not_sent": [sum(r["final"] != "sent" for r in followups), len(followups)],
        "off_focus_sent": sum(r["label"] == "off-focus" and r["final"] == "sent" for r in rows),
        "mock_requests": sum(r["requests"] for r in rows),
        "input_bytes": sum(r["input_bytes"] for r in rows),
    }, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
