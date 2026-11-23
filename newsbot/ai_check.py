"""One explicit inference test, with synthetic input and no Signal message."""

import time

import aiohttp

from .ai import ModelPool, PoolUnavailable
from .store import Store


async def check_ai(config, directory, alias):
    if alias not in {m["name"] for m in config.data["models"]}:
        print("Unknown model alias. See the configured aliases in the dashboard.")
        return False
    # In-memory overrides only: do not change the operator's persisted preferences.
    config.data.update(preferred_model=alias, fallback=False, mode="preview")
    store = Store(directory / "news.db")
    try:
        async with aiohttp.ClientSession(trust_env=False) as session:
            pool = ModelPool(store, config, session)
            if not pool.candidates():
                print(alias + ": not enabled/configured (key and, for Cloudflare, account ID are needed).")
                return False
            state = pool.route_state(pool.candidates()[0])
            if state["retired"] or state["blocked"]:
                print(f"{alias}: {state['error']}. Check the model ID in the dashboard.")
                return False
            wait = max(0, int(state["until"] - time.time()))
            if wait:
                print(f"{alias}: cooldown {wait}s. Wait before testing; the check does not bypass rate limits.")
                return False
            item = {"id": 1, "title": "SYNTHETIC TEST: statewide online licensing outage",
                    "description": "Synthetic test data, not actual news: state officials report a widespread outage affecting online driver licensing and vehicle registration. The cause is not yet established.",
                    "published": time.time(), "feed_title": "LOCAL TEST FIXTURE"}
            print(f"Testing {alias} with one synthetic article. This consumes one inference attempt; no Signal message will be sent.")
            try:
                model, decisions = await pool.analyze([item], [])
            except PoolUnavailable:
                print(alias + ": " + (store.model_state(alias)["error"] or "Local daily cap reached; no request was sent."))
                return False
            print(f"PASS: {model} returned a valid structured decision. Selected: {decisions[0]['post']}.")
            print(pool.footer(model))
            print("A valid one-article reply does not yet prove the full news batch/focus will work. Keep preview enabled.")
            return True
    finally:
        store.close()
