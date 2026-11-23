import asyncio
import base64
import json
import time
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from newsbot.ai import ModelOutputError, ModelPool, PoolUnavailable, parse_decisions
from newsbot.engine import Engine
from newsbot.feeds import FeedWorker, fetch_image
from newsbot.formatting import SIGNAL_INLINE_BYTES, SignalText, utf16_units
from newsbot.interactions import edit_footer, preference_delta, topic_preferences, vote_tally
from newsbot.quotas import duration_reset
from newsbot.signal import Signal
from newsbot.web import create_app
from test_bot import Base, decision, sample_picture


class EditorialValidation(unittest.TestCase):
    def parse(self, **kwargs):
        d = decision(1, **kwargs)
        return parse_decisions(json.dumps({"decisions": [d]}), [{"id": 1}], source_links=False, strict=True)[0]

    def test_excluded_or_duplicate_does_not_pass_at_100(self):
        self.assertFalse(self.parse(excluded=True, importance=100)["post"])
        self.assertFalse(self.parse(duplicate=True, importance=100)["post"])
        with self.assertRaisesRegex(ModelOutputError, "flags"):
            self.parse(excluded="no")

    def test_emoji_count_flags_and_joiners(self):
        symbols = "🇺🇸🇬🇧👩🏽‍💻🚨"
        self.assertEqual(self.parse(emoji=symbols, complex=True, importance=92)["emoji"], "🇺🇸 🇬🇧 👩🏽‍💻 🚨")
        self.assertEqual(self.parse(emoji=symbols, complex=True, importance=89)["emoji"], "🇺🇸 🇬🇧 👩🏽‍💻")
        with self.assertRaises(ModelOutputError):
            self.parse(emoji="📰 news")

    def test_source_off_preserves_the_actor_and_claim(self):
        claim = "Attackers claim they leaked JPMorgan Chase customer data on the dark web."
        self.assertEqual(self.parse(text=claim)["text"], claim)
        self.assertTrue(self.parse(text="The president claimed the system had reached superintelligence.")["post"])
        for text in ("According to an outlet, the bank suffered a breach.", "A breach occurred, as reported by Reuters.",
                     "The same channel further reports that the bank was breached.", "Reuters reports a breach at the bank."):
            with self.assertRaisesRegex(ModelOutputError, "publisher"):
                self.parse(text=text)

    def test_short_cap_without_truncation(self):
        good = "The FAA lifted its ground stop at Newark after resolving a runway lighting outage."
        result = parse_decisions(json.dumps({"decisions": [decision(1, text=good)]}), [{"id": 1}], "short")
        self.assertEqual(result[0]["text"], good)
        with self.assertRaises(ModelOutputError):
            parse_decisions(json.dumps({"decisions": [decision(1, text="word " * 29)]}), [{"id": 1}], "short")

    def test_duration_headers_parse_without_guessing(self):
        self.assertEqual(duration_reset("2m59.56s", 100), 279.56)
        self.assertEqual(duration_reset("1h30m", 100), 5500)
        for bad in (None, "not reported", "2026-01-01", "1m invalid", "99999999h", "nan", "-5"):
            self.assertIsNone(duration_reset(bad, 100))


class ReleaseIntegration(Base, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.setup_data()
        self.session = aiohttp.ClientSession()
        self.feed_worker = FeedWorker(self.store, self.config, self.session)
        self.pool = ModelPool(self.store, self.config, self.session)
        self.signal = Signal(self.config, self.store, self.session)
        self.signal.connected = True
        self.signal.discovered_account = "+15550100000"
        self.signal.account_ids = {"+15550100000": {"+15550100000", "bot-aci"}}
        self.engine = Engine(self.store, self.config, self.pool, self.signal, self.feed_worker)
        self.config.update(mode="live")

    async def asyncTearDown(self):
        await self.session.close()
        self.cleanup_data()

    def saved(self, title, state="sent", *, age=600, summary=None, ts=2345, **values):
        row = self.add_item(state, title=title)
        self.store.update_item(row["id"], rules=self.config.rules_key(), sent_at=time.time() - age,
                               signal_timestamp=str(ts), summary=summary or title,
                               model="groq-20b", actual_model="openai/gpt-oss-20b", score=86,
                               provider="groq", **values)
        return self.store.one("SELECT * FROM items WHERE id=?", (row["id"],))

    async def test_seven_rewrites_across_feeds_and_cycles_emit_one_claim_then_allow_denial(self):
        titles = [
            "Hackers claim leak of JP Morgan Chase customer data on dark web",
            "JPMorgan Chase customer data leaked on dark web, hackers claim",
            "Dark web leak of JPMorgan Chase data claimed by hackers",
            "Hackers claim JPMorgan Chase customer data leak",
            "JP Morgan Chase data reportedly leaked by hackers on dark web",
            "Hackers claim dark-web leak of JPMorgan Chase data",
            "Customer data leak at J.P. Morgan Chase claimed by hacker group",
        ]
        rewrites = [
            "Hackers claim they leaked JPMorgan Chase customer data on the dark web.",
            "J.P. Morgan Chase customer data was leaked on the dark web, hackers claim.",
            "Hackers claim the JPMorgan Chase customer data leak appeared on the dark web.",
            "JPMorgan Chase customer data leaked on the dark web, hackers claim.",
            "Hackers claim they leaked JPMorgan Chase data on the dark web.",
            "Hackers claim a dark-web leak of JPMorgan Chase customer data.",
            "A hacker group claims it leaked JPMorgan Chase data on the dark web.",
        ]
        with patch.object(self.signal, "send", AsyncMock(return_value={"timestamp": 100})) as send:
            for i, (title, rewrite) in enumerate(zip(titles, rewrites, strict=True)):
                feed_url = f"https://example.com/other-feed-{i}"
                self.store.add_feed(feed_url, f"Feed {i}")
                self.feed = self.store.one("SELECT * FROM feeds WHERE url=?", (feed_url,))
                story = self.add_item(title=title)
                generated = decision(story["id"], text=rewrite)
                with patch.object(self.pool, "analyze", AsyncMock(return_value=("groq-20b", [generated]))) as analyze:
                    await self.engine.process()
                    await self.engine.deliver()
                if not i:
                    # Older than 24 hours and buried under more than 20 newer posts.
                    self.store.update_item(story["id"], sent_at=time.time() - 32 * 3600)
                    self.store.execute("UPDATE items SET discovered=? WHERE id=?", (time.time() - 33 * 3600, story["id"]))
                    for j in range(25):
                        self.saved(f"Unrelated airport announcement {j}", age=3600 + j, ts=1000 + j)
                else:
                    self.assertIn("Hackers claim leak", analyze.call_args.args[0][0]["related_posts"][0]["title"])
                    self.assertEqual(self.store.one("SELECT status FROM items WHERE id=?", (story["id"],))["status"], "skipped", title)
            self.assertEqual(send.await_count, 1)
            denial = self.add_item(title="JPMorgan Chase denies hackers' dark web data leak claim")
            with patch.object(self.pool, "analyze", AsyncMock(return_value=("groq-20b", [
                decision(denial["id"], text="JPMorgan Chase denied that hackers leaked its customer data on the dark web.")]))):
                await self.engine.process()
                await self.engine.deliver()
            self.assertEqual(send.await_count, 2)
            self.assertIn("denied", send.call_args.args[0])

    async def test_exclusion_uncertainty_and_feedback_gates(self):
        self.config.update(exclude="Russia–Ukraine conflict; no top-level exceptions", topic_feedback=True)
        blocked = self.add_item(title="US announces new weapons for war in Ukraine")
        allowed = self.add_item(title="Russian national banking outage disrupts payments")
        claim = self.add_item(title="President claims new AI reached superintelligence")
        with patch.object(self.pool, "analyze", AsyncMock(return_value=("groq-20b", [
            decision(blocked["id"], importance=100, excluded=True),
            decision(allowed["id"], text="A nationwide banking outage disrupted card payments across Russia.", emoji="🇷🇺 🏦"),
            decision(claim["id"], text="The president claimed the new AI system had reached superintelligence.", emoji="🇺🇸 🤖"),
        ]))):
            await self.engine.process()
        self.assertEqual([x["status"] for x in self.store.rows("SELECT status FROM items ORDER BY id")],
                         ["skipped", "outbox", "outbox"])

    async def test_delivery_checks_existing_sent_repeat_and_preserves_rate(self):
        claim = "Hackers claim a dark web leak of JPMorgan Chase customer data."
        self.saved("JP Morgan Chase data leak on dark web", summary=claim)
        dupe = self.saved("JPMorgan Chase customer data leak on dark web", "outbox", summary=claim)
        with patch.object(self.signal, "send", AsyncMock()) as send:
            await self.engine.deliver()
            send.assert_not_called()
        self.assertEqual(self.store.one("SELECT status FROM items WHERE id=?", (dupe["id"],))["status"], "skipped")

    async def test_footer_output_and_exact_reported_model(self):
        self.config.update(footer_usage=True, footer_hourly=True, footer_age=True, footer_score=True,
                           include_sources=True, max_posts_per_hour=30)
        row = self.add_item("outbox", title="US bank restores payments")
        self.store.update_item(row["id"], rules=self.config.rules_key(), summary="🇺🇸 🏦 — The bank restored payments.",
                               score=86, model="groq-20b", provider="groq", actual_model="openai/gpt-oss-20b",
                               decision='{"reported": true}')
        self.store.execute("UPDATE items SET published=1699999280,discovered=1699999281 WHERE id=?", (row["id"],))
        with patch("newsbot.engine.time.time", return_value=1700000000), patch.object(
                self.signal, "send", AsyncMock(return_value={"timestamp": 101})) as send:
            await self.engine.deliver()
            text = send.call_args.args[0]
        self.assertIn("🔗 " + row["url"], text)
        self.assertIn("Groq · GPT-OSS 20B · 900/900 calls", text)
        self.assertTrue(text.endswith("📰 1/30 · 🎯 86\n🕒 12 min ago"))
        for start, length, style in (s.split(":") for s in text.styles):
            buf = str(text).encode("utf-16-le")
            span = buf[2 * int(start):2 * (int(start) + int(length))].decode("utf-16-le")
            if style == "BOLD":
                self.assertIn(span, {"86", "12 min ago"})
            else:
                self.assertIn("GPT-OSS", span)
        self.assertIn("openai/gpt-oss-20b", await self.engine.command("/model"))
        self.assertIn("⚠️", await self.engine.command("/footer small off"))
        self.assertNotIn("footer_small", self.config.data)

    async def test_compact_stat_only_footer_has_no_local_label(self):
        self.config.update(footer_model=False, footer_age=False, footer_usage=True, footer_hourly=True,
                           footer_score=True, max_posts_per_hour=9)
        self.saved("The bank restored payments.", "outbox")
        with patch.object(self.signal, "rpc", AsyncMock(return_value={"timestamp": 2346})) as rpc:
            await self.engine.deliver()
        payload = rpc.call_args.args[1]
        self.assertEqual(payload["message"], "The bank restored payments.\n\n900/900 calls · 📰 1/9 · 🎯 86")
        self.assertIn("not provider balance", await self.engine.command("/help footer"))
        self.assertIn("No enabled models", await self.engine.command("/usage"))

    async def test_recap_uses_only_scoped_confirmed_sends_and_caches(self):
        self.saved("A bank restored transfers", age=30, ts=111)
        self.saved("Older report not in 12 hour window", age=13 * 3600)
        self.saved("Undelivered preview", "preview")
        foreign = self.saved("Foreign group private metadata")
        self.store.update_item(foreign["id"], rules='{"group_id":"OTHER"}')
        with patch.object(self.pool, "recap", AsyncMock(return_value=["The bank restored transfers."])) as ai:
            self.assertIn("restored transfers", await self.engine.command("/summary 12"))
            self.assertIn("restored transfers", await self.engine.command("/summary 12"))
            ai.assert_awaited_once()
            sent = ai.call_args.args[0]
            self.assertEqual(len(sent), 1)
            self.assertEqual(sent[0]["text"], "A bank restored transfers")
        with patch.object(self.pool, "recap", AsyncMock(side_effect=PoolUnavailable())) as ai:
            self.engine.last_recap_attempt = 0
            recap = await self.engine.command("/summary")
            ai.assert_awaited_once()
            self.assertIn("Older report", recap)
            self.assertNotIn("Foreign group", recap)
        self.assertIn("1–168", await self.engine.command("/summary 169"))

    def packet(self, sender, at, *, emoji=None, remove=False, timestamp=2345, group=None, author="bot-aci"):
        data = {"groupInfo": {"groupId": self.engine.scope[0] if group is None else group}}
        if emoji:
            data["reaction"] = {"emoji": emoji, "isRemove": remove, "targetSentTimestamp": timestamp, "targetAuthorUuid": author}
        else:
            data.update(message="/why", quote={"id": timestamp, "authorUuid": author, "text": "not sent to provider"})
        return {"envelope": {"sourceUuid": sender, "timestamp": at, "dataMessage": data}}

    async def test_reaction_batch_edits_footer_in_place_and_why_resolves_edit_timestamp(self):
        self.config.update(topic_feedback=True, footer_score=True)
        rendered = json.dumps({"text": "Framed summary\n29/30 · 🎯 86", "styles": ["0:6:BOLD"], "score": True})
        row = self.saved("Bank outage affects card payments", rendered=rendered)
        now = int(time.time() * 1000)
        for i, user in enumerate(("a", "b")):
            packet = self.packet(user, now + i, emoji="👍🏽")
            await self.engine.command(*self.signal.incoming(packet))
            self.assertIsNone(self.signal.incoming(packet))
        self.assertIsNone(self.signal.incoming(self.packet("c", now, emoji="👍", group="other")))
        packet = self.packet("c", now, emoji="👍", author="bad")
        await self.engine.command(*self.signal.incoming(packet))
        self.assertEqual(vote_tally(self.store, row["id"]), {"n": 2, "score": 2})
        self.assertGreater(preference_delta(row, topic_preferences(self.store, self.config.data, time.time())), 0)
        self.store.execute("UPDATE footer_edits SET due=1")
        with patch.object(self.signal, "rpc", AsyncMock(return_value={"timestamp": 5353})) as rpc:
            await edit_footer(self.engine)
        payload = rpc.call_args.args[1]
        self.assertEqual(payload["editTimestamp"], 2345)
        self.assertTrue(payload["noUrgent"])
        self.assertEqual(payload["message"], "Framed summary\n29/30 · 🎯 86 (+6)")
        self.assertEqual(payload["textStyle"], ["0:6:BOLD"])
        await self.engine.command(*self.signal.incoming(self.packet("a", now + 5, timestamp=5353, emoji="👎")))
        await self.engine.command(*self.signal.incoming(self.packet("a", now + 6, timestamp=5353, emoji="👍", remove=True)))
        self.assertEqual(vote_tally(self.store, row["id"]), {"n": 2, "score": 0})
        await self.engine.command(*self.signal.incoming(self.packet("b", now + 7, emoji="👍", remove=True)))
        self.assertEqual(vote_tally(self.store, row["id"]), {"n": 1, "score": -1})
        why = await self.engine.command(*self.signal.incoming(self.packet("d", now + 8, timestamp=5353)))
        self.assertIn("86/100", why)
        self.assertIn("👎 1", why)
        self.assertIn("👍 0 · 👎 1 · -3", why)

    async def test_ambiguous_edit_is_not_retried_and_legacy_or_image_posts_still_vote(self):
        self.config.update(topic_feedback=True, footer_score=True)
        orig = self.saved("Text post", rendered='{"text":"🎯 86","styles":[],"score":true}')
        image = self.saved("Image (no saved edit body)", ts=5555)
        now = int(time.time() * 1000)
        for ts in (2345, 5555):
            await self.engine.command(*self.signal.incoming(self.packet("a", now + ts, timestamp=ts, emoji="👍")))
        self.assertEqual(vote_tally(self.store, image["id"])["n"], 1)
        self.assertFalse(self.store.one("SELECT * FROM footer_edits WHERE item_id=?", (image["id"],)))
        self.store.execute("UPDATE footer_edits SET due=1")
        with patch.object(self.signal, "rpc", AsyncMock(side_effect=TimeoutError)) as rpc:
            await edit_footer(self.engine)
            await self.engine.command(*self.signal.incoming(self.packet("b", now + 9000, emoji="👍")))
            await edit_footer(self.engine)
            rpc.assert_awaited_once()
        self.assertEqual(vote_tally(self.store, orig["id"])["n"], 2)

    async def test_feeds_subscription_baseline_media_and_sender_bound_removal(self):
        url = "https://example.net/news/feed"
        rss = b"""<rss version='2.0' xmlns:media='http://search.yahoo.com/mrss/'><channel><title>Test feed</title>
                  <item><title>Test item</title><link>/news/story</link><media:content url='picture.png' type='image/png'/>
                  </item></channel></rss>"""
        with patch("newsbot.feeds.fetch_public", AsyncMock(return_value=(200, rss, {}, url))) as fetch:
            self.assertIn("Added feed #2", await self.engine.command("/feed add " + url))
            self.assertIn("Already subscribed", await self.engine.command("/feed add " + url))
            self.assertIn("without credentials", await self.engine.command("/feed add https://example.net/feed?access_token=private-value"))
            fetch.assert_awaited_once()
        baseline = self.store.one("SELECT * FROM items WHERE feed_id=2")
        self.assertEqual(baseline["status"], "baseline")
        self.assertEqual(baseline["image_url"], "https://example.net/news/picture.png")
        self.saved("Prior posted news")
        self.store.execute("UPDATE items SET feed_id=2 WHERE status='sent'")
        self.assertIn("Test feed", await self.engine.command("/feed remove 2", "first"))
        self.assertIn("No valid", await self.engine.command("/feed confirm", "other"))
        self.engine.last_command.clear()
        self.assertIn("Removed feed #2", await self.engine.command("/feed confirm", "first"))
        self.assertEqual(self.store.one("SELECT removed FROM feeds WHERE id=2")["removed"], 1)
        self.assertEqual(self.store.one("SELECT COUNT(*) AS n FROM items WHERE status='sent'")["n"], 1)
        self.assertNotIn("Test feed", await self.engine.command("/feeds"))

    async def test_feed_pagination_respects_inline_bytes_and_keeps_whole_rows(self):
        for i in range(125):
            self.store.add_feed(f"https://example.net/feed-{i}", "🇺🇸 Feed " + str(i) + " 🏦")
        all_ids = []
        for page in range(1, 9):
            message = await self.engine.command(f"/feeds {page}")
            self.assertLessEqual(len(message.encode()), SIGNAL_INLINE_BYTES)
            self.assertIsInstance(message, SignalText)
            if page == 1:
                self.assertGreater(message.count(" · 180s"), 10)
            for line in message.splitlines():
                if " #" in line:
                    self.assertTrue(line.endswith("180s (3m)"))
                    all_ids.append(int(line.split("#")[1].split()[0]))
            if "Next:" not in message:
                break
        self.assertEqual(all_ids, list(range(1, 127)))
        help_text = await self.engine.command("/help")
        self.assertLessEqual(len(help_text.encode()), SIGNAL_INLINE_BYTES)
        self.assertLess(utf16_units(help_text), 6500)

    async def test_image_payload_and_unsafe_or_ambiguous_delivery(self):
        uri = sample_picture()
        data = base64.b64decode(uri.partition(",")[2])
        with patch("newsbot.feeds.fetch_public", AsyncMock(return_value=(200, data, {"content-type": "image/png"}))):
            self.assertEqual(await fetch_image(self.session, "https://example.net/image"), uri)
        with patch("newsbot.feeds.fetch_public", AsyncMock(return_value=(200, b"<svg></svg>", {"content-type": "image/png"}))):
            self.assertIsNone(await fetch_image(self.session, "https://example.net/wrong-image"))
        self.config.update(include_images=True)
        row = self.saved("A bank fixed the payment outage", "outbox")
        self.store.execute("UPDATE items SET image_url='https://example.net/image.png' WHERE id=?", (row["id"],))
        with patch("newsbot.engine.fetch_image", AsyncMock(return_value=uri)), patch.object(
                self.signal, "rpc", AsyncMock(side_effect=TimeoutError)) as rpc:
            await self.engine.deliver()
            await self.engine.deliver()
            rpc.assert_awaited_once()
            self.assertEqual(rpc.call_args.args[1]["attachment"], [uri])
        self.assertEqual(self.store.one("SELECT status FROM items WHERE id=?", (row["id"],))["status"], "uncertain")

    async def test_removed_feed_cannot_complete_inflight_poll(self):
        started, release = asyncio.Event(), asyncio.Event()

        async def fetch(*args, **kwargs):
            started.set()
            await release.wait()
            return 200, b"<rss version='2.0'><channel><title>Feed</title></channel></rss>", {}, self.feed["url"]

        with patch("newsbot.feeds.fetch_public", fetch):
            task = asyncio.create_task(self.feed_worker.poll(self.feed))
            await started.wait()
            self.store.remove_feed(self.feed["id"])
            self.store.execute("UPDATE feeds SET removed=0,enabled=1,revision=revision+1 WHERE id=?", (self.feed["id"],))
            release.set()
            await task
        self.assertEqual(self.store.one("SELECT initialized FROM feeds")["initialized"], 0)

    async def test_dashboard_feed_mutation_and_preferences_are_authenticated(self):
        app = create_app(self.config, self.store, self.pool, self.signal, self.engine)
        async with TestClient(TestServer(app)) as client:
            auth = {"Authorization": "Basic " + base64.b64encode(("admin:" + self.config.data["dashboard_password"]).encode()).decode()}
            csrf = self.config.data["csrf"]
            response = await client.post("/command", headers=auth, data={"command": "/feed remove 1"})
            self.assertEqual(response.status, 403)
            response = await client.post("/command", headers=auth, data={"command": "/feed remove 1", "csrf": csrf})
            self.assertIn("Confirm feed removal", await response.text())
            response = await client.post("/command", headers=auth, data={"command": "/feed confirm", "csrf": csrf})
            self.assertIn("Removed feed #1", await response.text())
            with patch.object(self.pool, "status", side_effect=AssertionError("Settings should not fetch model quota status")):
                response = await client.get("/?page=settings", headers=auth)
            html = await response.text()
            for name in ("include_images", "topic_feedback", "footer_hourly", "footer_score",
                         "reply_cleanup", "feedback_weight", "image_min_score"):
                self.assertIn(f'name="{name}"', html)
            self.assertNotIn('name="footer_small"', html)

    async def test_usage_resets_error_and_cooldown_are_distinct(self):
        self.config.update(models=[
            {"name": "groq-test", "provider": "groq", "model": "openai/gpt-oss-20b", "key": "key"},
            {"name": "cf-test", "provider": "cloudflare", "model": "@cf/ibm-granite/granite-4.0-h-micro",
             "key": "key", "account_id": "1" * 32},
            {"name": "or-test", "provider": "openrouter", "model": "openrouter/free", "key": "key"},
            {"name": "gm-test", "provider": "gemini", "model": "gemini-fixture", "key": "key"},
        ])
        now = 1700000000
        with patch("newsbot.ai.time.time", return_value=now):
            q = await self.pool.reported_quota(self.config.data["models"][0], {
                "x-ratelimit-remaining-requests": "97", "x-ratelimit-reset-requests": "6m",
                "x-ratelimit-remaining-tokens": "98", "x-ratelimit-reset-tokens": "8.5s"})
            self.store.set_model_state("groq-test", now + 300, "HTTP 429; rate limit or provider capacity", q)
            self.store.count_usage("groq-test", kind="analysis")
            self.store.count_usage("groq-test", request=False, kind="failed")
            usage = await self.engine.command("/usage")
            details = await self.engine.command("/usage details groq-test")
            cf = await self.engine.command("/usage details cf-test")
            router = await self.engine.command("/usage details or-test")
            gemini = await self.engine.command("/usage details gm-test")
        self.assertIn("Cloudflare neurons: daily at 00:00 UTC", cf)
        self.assertIn("OpenRouter free requests: daily at 00:00 UTC", router)
        self.assertIn("midnight US Pacific", gemini)
        for text in ("local resets 00:00 UTC", "daily requests ~",
                     "tokens/min ~9s", "Retry in 300s (5m) · HTTP 429: rate limit or free capacity"):
            self.assertIn(text, details)
        self.assertIn("groq-test 199/200 · retry in 300s (429)", usage)
        self.assertNotIn("failed 1", usage)
        self.assertIn("Analysis 1 · fallback 0 · recap 0 · failed 1", details)
        with patch("newsbot.ai.time.time", return_value=now + 500):
            expired = await self.engine.command("/usage details groq-test")
        self.assertIn("97 requests (stale)", expired)
        self.assertIn("daily reset snapshot expired", expired)
        self.assertIn("Last error:", expired)
        self.assertNotIn("Retry in", expired)

    async def test_local_daily_usage_is_a_utc_counter_not_provider_reset(self):
        with patch("newsbot.store.datetime") as dt:
            with patch("newsbot.store.time.time", return_value=1700000000):
                dt.now.return_value = datetime(2026, 11, 19, 23, 59, tzinfo=timezone.utc)
                self.store.count_usage("groq-20b", kind="analysis")
                self.store.set_model_state("groq-20b", 1700001000, "HTTP 429", {"requests": 0})
                self.assertEqual(self.pool.local_remaining(self.config.data["models"][0]), 899)
                dt.now.return_value = datetime(2026, 11, 20, 0, 0, tzinfo=timezone.utc)
                self.assertEqual(self.pool.local_remaining(self.config.data["models"][0]), 900)
                self.assertEqual(self.store.model_state("groq-20b")["until"], 1700001000)

    async def test_http_to_scoring_to_delivery_with_preferred_rate_limited(self):
        self.config.update(preferred_model="preferred", threshold=78, summary_length="short", footer_score=True,
                           models=[{"name": alias, "provider": "groq", "model": model, "key": "synthetic-test-key"}
                                   for alias, model in (("fallback", "openai/gpt-oss-20b"), ("preferred", "openai/gpt-oss-120b"))])
        excluded = self.add_item(title="Parliament votes on arms for Russia–Ukraine war")
        qualifying = self.add_item(title="FAA lifts Newark ground stop")
        borderline = self.add_item(title="The city reports a minor bus delay")
        self.config.update(exclude="Russia–Ukraine conflict")
        routed = []

        async def provider(request):
            data = await request.json()
            routed.append(data)
            if data["model"].endswith("120b"):
                return web.json_response({"error": {"message": "must not be surfaced"}}, status=429, headers={"Retry-After": "120"})
            picks = [decision(excluded["id"], importance=100, excluded=True),
                     decision(qualifying["id"], importance=86, text="The FAA lifted its Newark ground stop after fixing runway lights."),
                     decision(borderline["id"], importance=77, text="A brief bus delay affected local riders downtown.")]
            return web.json_response({"model": "openai/gpt-oss-20b", "choices": [{"message": {"content": json.dumps({"decisions": picks})}}]})

        service = web.Application()
        service.router.add_post("/", provider)
        async with TestServer(service) as server:
            with patch("newsbot.ai.endpoint", return_value=str(server.make_url("/"))), patch.object(
                    self.signal, "rpc", AsyncMock(return_value={"timestamp": 5467})) as signal_rpc:
                await self.engine.process()
                await self.engine.deliver()
                await self.engine.deliver()
        self.assertEqual([b["model"] for b in routed], ["openai/gpt-oss-120b", "openai/gpt-oss-20b"])
        prompt = routed[1]["messages"]
        self.assertIn("exclusions first", prompt[0]["content"])
        self.assertEqual(json.loads(prompt[1]["content"])["exclude"], "Russia–Ukraine conflict")
        signal_rpc.assert_awaited_once()
        self.assertIn("Groq · GPT-OSS 20B", signal_rpc.call_args.args[1]["message"])
        self.assertIn("🎯 86", signal_rpc.call_args.args[1]["message"])
        self.assertEqual([row["status"] for row in self.store.rows("SELECT status FROM items ORDER BY id")],
                         ["skipped", "sent", "skipped"])
        usage = await self.engine.command("/usage details preferred")
        self.assertIn("Retry in", usage)
        self.assertIn("HTTP 429", usage)
        self.assertNotIn("must not be surfaced", usage)
        self.assertEqual(self.store.usage("fallback")["fallback"], 1)
