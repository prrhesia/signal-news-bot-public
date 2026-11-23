import json
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp import web
from aiohttp.test_utils import TestServer

from newsbot.ai import PoolUnavailable, parse_decisions
from newsbot.config import validate_models
from newsbot.formatting import Message
from newsbot.interactions import edit_footer, vote_tally
from newsbot.media import distinct_images
from newsbot.publishing import recorded_message, render_news
from test_bot import decision, sample_picture
from test_release_105 import NOW, SignalFixture


class Clarity(SignalFixture, unittest.IsolatedAsyncioTestCase):
    async def test_quick_votes_coalesce_and_later_votes_only_edit_changed_delta(self):
        self.config.update(topic_feedback=True, footer_score=True, feedback_weight=4)
        frame = recorded_message(Message().line("🎯 90").build(), True, False)
        post = self.posted("Major outage at a public agency", rendered=frame)
        stamp = int(post["signal_timestamp"])
        with patch.object(self.signal, "rpc", AsyncMock(side_effect=[
                {"timestamp": (NOW + sub) * 1000} for sub in (5, 305, 905)])) as rpc:
            await self.react("a", stamp, "👍")
            await self.react("b", stamp, "👍")
            self.clock.return_value = NOW + 5
            await edit_footer(self.engine)
            self.clock.return_value = NOW + 300
            await self.react("c", (NOW + 5) * 1000, "👍")
            self.clock.return_value = NOW + 305
            await edit_footer(self.engine)
            self.clock.return_value = NOW + 600
            await self.react("d", stamp, "👍")
            self.clock.return_value = NOW + 605
            await edit_footer(self.engine)  # Still +10, no count-only edit.
            self.clock.return_value = NOW + 900
            await self.react("c", (NOW + 305) * 1000, "👎")
            self.clock.return_value = NOW + 905
            await edit_footer(self.engine)
        sent = [call.args[1] for call in rpc.await_args_list]
        self.assertEqual([p["message"] for p in sent], ["🎯 90 (+8)", "🎯 90 (+10)", "🎯 90 (+8)"])
        self.assertEqual([p["editTimestamp"] for p in sent], [stamp, (NOW + 5) * 1000, (NOW + 305) * 1000])
        self.assertEqual(vote_tally(self.store, post["id"]), {"n": 4, "score": 2})

    async def test_details_quote_feed_once_even_with_feedback_off(self):
        self.config.update(topic_feedback=False, story_mute=False)
        post = self.posted("A major outage affected payment processing.")
        self.store.execute("UPDATE items SET description=? WHERE id=?", (
            "A major outage affected payment processing. Officials say bank transfers were restored at noon.",
            post["id"]))
        stamp = int(post["signal_timestamp"])
        with patch.object(self.signal, "rpc", AsyncMock(return_value={"timestamp": NOW * 1000})) as rpc:
            await self.react("wrong-author", stamp, "🔎", author="somebody-else")
            await self.react("wrong-group", stamp, "🔎", group="another-group")
            await self.react("removed", stamp, "🔎", removed=True)
            self.config.update(paused=True)
            await self.react("paused", stamp, "🔎")
            rpc.assert_not_awaited()
            self.config.update(paused=False)
            await self.react("reader", stamp, "🔎")
            await self.react("later", stamp, "🔎")
            self.assertIn("No new", await self.engine.command("/expand", "operator", {
                "group": self.group, "quote": {"id": stamp, "authorUuid": "test-bot-aci"}}))
        rpc.assert_awaited_once()
        payload = rpc.await_args.args[1]
        self.assertEqual(payload["quoteTimestamp"], stamp)
        self.assertEqual(payload["quoteAuthor"], "test-bot")
        self.assertIn("restored at noon", payload["message"])
        self.assertNotIn("A major outage", payload["message"])
        self.assertNotIn("editTimestamp", payload)
        self.assertEqual(vote_tally(self.store, post["id"])["n"], 0)

    async def test_duplicate_skips_inference_but_new_incident_fact_and_focus_cases_go_to_editor(self):
        self.config.update(focus="Major U.S. infrastructure disruptions and meaningful international diplomacy",
                           exclude="Court cases, conflict-only diplomacy and routine announcements")
        initial = self.posted("USDA says a cyberattack hit its farmer payment system")
        self.store.execute("UPDATE items SET description=? WHERE id=?", (
            "The department is assessing whether the attacker accessed sensitive data in the farmer payment system.",
            initial["id"]))
        repeated = self.add_item(title="Hackers breached the U.S. Department of Agriculture’s payment system for farmers")
        recovery = self.add_item(title="USDA says its farmer payment system has recovered from the cyberattack")
        court = self.add_item(title="A court approves a large civil settlement")
        summit = self.add_item(title="Three nations announce a new cross-border aviation agreement")
        self.store.execute("UPDATE items SET description=? WHERE id=?", (
            "The agency confirms funds are moving again following the attack.", recovery["id"]))
        self.store.execute("UPDATE items SET description=? WHERE id=?", (
            "A different outlet reports on the same farmer payment system incident and possible data access.", repeated["id"]))
        picks = [decision(i["id"], importance=99, text=i["title"], excluded=i is court, on_focus=i is not court)
                 for i in (recovery, court, summit)]
        with patch.object(self.pool, "analyze", AsyncMock(return_value=("groq-20b", picks))) as analyze:
            await self.engine.process()
        self.assertEqual({i["id"] for i in analyze.await_args.args[0]}, {i["id"] for i in (recovery, court, summit)})
        self.assertIn(f"#{initial['id']}", self.get(repeated)["reason"])
        self.assertEqual([self.get(i)["status"] for i in (recovery, court, summit)], ["outbox", "skipped", "outbox"])

    async def test_known_cloudflare_retirement_can_be_replaced_without_touching_alias_or_key(self):
        old = {"name": "workers", "provider": "cloudflare", "key": "fixture-key", "account_id": "a" * 32,
               "model": "@cf/qwen/qwen3-30b-a3b-fp8"}
        self.config.update(models=[old])
        item = self.add_item()
        with self.assertRaises(PoolUnavailable):
            await self.pool.analyze([item], [])
        self.assertEqual(self.store.usage("workers")["requests"], 0)
        self.assertIn("retired", await self.command("/usage"))

        calls = []

        async def complete(request):
            body = await request.json()
            calls.append(body["model"])
            if body["model"].endswith("gemma-4-26b-a4b-it"):
                return web.json_response({}, status=410)
            return completion(item["id"])

        app = web.Application()
        app.router.add_post("/", complete)
        async with TestServer(app) as server:
            with patch("newsbot.ai.endpoint", return_value=str(server.make_url("/"))):
                self.config.update(models=[old | {"model": "@cf/google/gemma-4-26b-a4b-it"}])
                for _ in range(2):
                    with self.assertRaises(PoolUnavailable):
                        await self.pool.analyze([item], [])
                self.config.update(models=[old | {"model": "@cf/openai/gpt-oss-20b"}])
                self.assertEqual((await self.pool.analyze([item], []))[0], "workers")
        self.assertEqual(calls, ["@cf/google/gemma-4-26b-a4b-it", "@cf/openai/gpt-oss-20b"])
        self.assertEqual(self.store.usage("workers")["requests"], 2)

    async def test_requesty_requires_zero_prices_and_falls_through_to_zai(self):
        item = self.add_item()
        model = "google/gemma-4-31b-it"
        self.config.update(models=[{"name": "gateway", "provider": "requesty", "model": model, "key": "gateway-fixture"},
                                   {"name": "flash", "provider": "zai", "model": "glm-4.7-flash", "key": "flash-fixture"}])
        for provider, paid in [("requesty", "policy/paid"), ("zai", "glm-5.3-flash")]:
            with self.assertRaises(ValueError):
                validate_models([{"name": "paid", "provider": provider, "model": paid}])
        calls, price, cost = [], 0.01, 0

        async def catalog(request):
            self.assertEqual(request.headers.get("Authorization"), "Bearer gateway-fixture")
            return web.json_response({"data": [{"id": model, "pricing": [{"input_price": 0, "output_price": price}]}]})

        async def complete(request):
            body = await request.json()
            calls.append(body["model"])
            if body["model"] == model:
                return completion(item["id"], usage={"cost": cost})
            self.assertEqual(body["thinking"], {"type": "disabled"})
            return completion(item["id"])

        app = web.Application()
        app.router.add_get("/models", catalog)
        app.router.add_post("/completion", complete)
        async with TestServer(app) as server:
            with patch("newsbot.ai.REQUESTY_CATALOG_URL", str(server.make_url("/models"))), patch(
                    "newsbot.ai.endpoint", return_value=str(server.make_url("/completion"))):
                self.assertEqual((await self.pool.analyze([item], []))[0], "flash")
                self.assertEqual(self.store.usage("gateway")["requests"], 0)
                price = 0
                self.pool._requesty_catalogs.clear()
                self.clock.return_value = NOW + 121
                self.assertEqual((await self.pool.analyze([item], []))[0], "gateway")
                cost = .01
                self.assertEqual((await self.pool.analyze([item], []))[0], "flash")
                self.assertEqual((await self.pool.analyze([item], []))[0], "flash")
        self.assertEqual(calls, ["glm-4.7-flash", model, model, "glm-4.7-flash", "glm-4.7-flash"])
        self.assertIn("blocked", await self.command("/usage"))

    def test_source_setting_on_normal_and_both_direct_routes(self):
        row = self.posted("A regional power failure")
        for route in ("", "instant", "emergency"):
            for visible in (False, True):
                c = self.config.data | {"include_sources": visible, "footer_usage": False}
                self.assertEqual("🔗" in render_news(row | {"route": route}, c, self.pool, None, 0),
                                 visible or route == "instant")

    def test_post_has_one_emoji_prefix_and_photos_are_distinct(self):
        pick = decision(1, emoji="🤖 🇺🇸", text="🤖🇺🇸 The source describes a new AI announcement.🤖 🇺🇸")
        d = parse_decisions(json.dumps({"decisions": [pick]}), [{"id": 1}], strict=True)[0]
        self.assertEqual(d["emoji"], "🤖 🇺🇸")
        self.assertEqual(d["text"], "The source describes a new AI announcement.")
        a, resize, b = (sample_picture("red"), sample_picture("red", (48, 48), "JPEG"), sample_picture("blue"))
        self.assertEqual(distinct_images([a, resize, b, a, "data:image/png;base64,bad"]), [a, b])


def completion(item_id, **extra):
    choices = [{"message": {"content": json.dumps({"decisions": [decision(item_id)]})}}]
    return web.json_response({"choices": choices, **extra})
