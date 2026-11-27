import json
import unittest
from unittest.mock import AsyncMock, patch

import aiohttp
import feedparser

from newsbot.ai import ModelOutputError, ModelPool, parse_decisions
from newsbot.engine import Engine
from newsbot.feeds import FeedWorker, item_images
from newsbot.formatting import Message, SIGNAL_INLINE_BYTES
from newsbot.interactions import edit_footer, vote_tally
from newsbot.publishing import recorded_message
from newsbot.signal import Signal
from newsbot.version import RELEASES, VERSION
from test_bot import Base, decision, sample_picture


NOW = 1_790_000_000
APPOINTMENTS = (
    "White House named Kirsten Davies national cyber director after widespread outages rattled Americans.",
    "Kirsten Davies appointed White House National Cyber Director, tasked with strengthening U.S. cyber defenses.",
    "President Trump named DoD CIO Kirsten Davies as White House National Cyber Director amid recent government outages.",
    "Kirsten Davies, the Defense Department CIO, was appointed White House national cyber director, replacing Sean Cairncross.",
)
COURT = "A Virginia judge extends the injunction halting construction of a planned triumphal arch."
OTHER_ORDER = "A second federal judge issued a separate injunction blocking construction of the planned triumphal arch."
OFF_FOCUS = "The president restated his view of an earlier summit without announcing any new result."


class Decision105(unittest.TestCase):
    def test_focus_flag_is_required_and_cannot_be_overridden_by_score(self):
        pick = decision(1, importance=100, on_focus=False, text=None, emoji=None)
        self.assertFalse(parse_decisions(json.dumps({"decisions": [pick]}), [{"id": 1}], strict=True)[0]["post"])
        del pick["on_focus"]
        with self.assertRaises(ModelOutputError):
            parse_decisions(json.dumps({"decisions": [pick]}), [{"id": 1}], strict=True)

    def test_strip_repeated_emoji_from_body(self):
        pick = decision(1, text="⚖️🚨🇺🇸 Judge issues a new court order.", emoji="⚖️ ⚖ 🚨")
        result = parse_decisions(json.dumps({"decisions": [pick]}), [{"id": 1}], strict=True)[0]
        self.assertEqual(result["text"], "Judge issues a new court order.")
        self.assertEqual(result["emoji"].replace("\ufe0f", ""), "⚖ 🚨")


class SignalFixture(Base):
    async def asyncSetUp(self):
        self.setup_data()
        self.session = aiohttp.ClientSession()
        self.worker = FeedWorker(self.store, self.config, self.session)
        self.pool = ModelPool(self.store, self.config, self.session)
        self.signal = Signal(self.config, self.store, self.session)
        self.signal.connected = True
        self.signal.discovered_account = "test-bot"
        self.signal.account_ids = {"test-bot": {"test-bot", "test-bot-aci"}}
        self.engine = Engine(self.store, self.config, self.pool, self.signal, self.worker)
        self.config.update(mode="live")
        self.group = self.engine.scope[0]
        p = patch("newsbot.engine.time.time", return_value=NOW)
        self.clock = p.start()
        self.addCleanup(p.stop)

    async def asyncTearDown(self):
        await self.session.close()
        self.cleanup_data()

    def get(self, item):
        return self.store.one("SELECT * FROM items WHERE id=?", (item["id"],))

    def posted(self, title, age=0, **extra):
        item = self.add_item("sent", title=title)
        fields = {"summary": title, "rules": self.config.rules_key(), "sent_at": NOW - age, "score": 91,
                  "signal_timestamp": str((NOW - age) * 1000 - item["id"]), "sent_account": "test-bot"}
        self.store.update_item(item["id"], **(fields | extra))
        return self.get(item)

    def ingest(self, *entries):
        rss = """<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/"><channel><title>Feed</title>"""
        for slug, title, content in entries:
            rss += (f"<item><guid>{slug}</guid><link>https://example.com/news/{slug}</link>"
                    f"<title>{title}</title>{content}</item>")
        parsed = feedparser.parse(rss + "</channel></rss>")
        self.worker.ingest(self.store.one("SELECT * FROM feeds WHERE id=?", (self.feed["id"],)), parsed, {}, NOW,
                           "https://example.com/rss")

    async def command(self, body):
        self.engine.last_command.clear()
        return await self.engine.command(body, "operator", {"group": self.group})

    async def react(self, user, target, emoji, author="test-bot-aci", removed=False, group=None):
        inbound = self.signal.incoming({"envelope": {"timestamp": int(self.clock.return_value * 1000) - 1,
            "sourceUuid": user, "dataMessage": {"groupInfo": {"groupId": self.group if group is None else group},
            "reaction": {"targetSentTimestamp": int(target), "targetAuthorUuid": author,
                         "isRemove": removed, "emoji": emoji}}}})
        if inbound:
            await self.engine.command(*inbound)


class Release105(SignalFixture, unittest.IsolatedAsyncioTestCase):
    async def test_appointment_rewrites_collapse_but_distinct_orders_and_updates_pass(self):
        correction = "White House withdraws the appointment of Kirsten Davies as National Cyber Director."
        self.config.update(batch_size=10, focus="Cybersecurity and important court rulings")
        items = [self.add_item(title=title) for title in (*APPOINTMENTS, COURT, OTHER_ORDER, correction, OFF_FOCUS)]
        mock_picks = [decision(item["id"], text=item["title"], importance=99, on_focus=item["title"] != OFF_FOCUS)
                      for item in items]
        with patch.object(self.pool, "analyze", AsyncMock(return_value=("groq-20b", mock_picks))), patch.object(
                self.signal, "rpc", AsyncMock(side_effect=[{"timestamp": NOW * 1000 + i} for i in range(4)])) as rpc:
            await self.engine.process()
            for _ in items:
                await self.engine.deliver()
        self.assertEqual(rpc.await_count, 4)
        states = [self.get(item)["status"] for item in items]
        self.assertEqual(states, ["sent", "skipped", "skipped", "skipped", "sent", "sent", "sent", "skipped"])
        self.assertTrue(all(f"#{items[0]['id']}" in self.get(item)["reason"] for item in items[1:4]))
        self.assertIn("Outside current focus", self.get(items[-1])["reason"])

    async def test_shared_memory_rolls_and_older_match_remains_available(self):
        oldest = self.posted(APPOINTMENTS[0], age=400)
        self.posted("Foreign private story", rules='{"group_id":"other-group"}')
        for n in range(16):
            self.posted(f"Filler report {n}", age=100 - n)
        item = self.add_item(title=APPOINTMENTS[3])
        with patch.object(self.pool, "analyze", AsyncMock(return_value=("groq-20b", [
                decision(item["id"], text=item["title"])]))) as analyze:
            await self.engine.process()
        articles, rolling = analyze.call_args.args
        self.assertEqual([p["title"] for p in rolling], [f"Filler report {n}" for n in range(15, 0, -1)])
        self.assertEqual(articles[0]["related_posts"][0]["title"], APPOINTMENTS[0])
        self.assertIn(f"#{oldest['id']}", self.get(item)["reason"])

    async def test_instant_feed_respects_limit_while_emergency_exceeds_it(self):
        self.config.update(max_posts_per_hour=1, focus="", footer_score=True, footer_hourly=True)
        self.posted("One confirmed post")
        backlog = self.add_item(title="Existing editorial backlog")
        self.assertIn("On", await self.command(f"/feed instant {self.feed['id']} on"))
        self.ingest(("baseline", "Existing headline from a new feed", ""))
        self.ingest(("instant", "A trusted feed's new headline", ""))
        with patch.object(self.pool, "analyze", AsyncMock()) as analyze, patch.object(
                self.signal, "rpc", AsyncMock(return_value={"timestamp": NOW * 1000})) as rpc:
            await self.engine.deliver()
            rpc.assert_not_awaited()
            self.assertIn("On", await self.command("/rapemode on"))
            self.ingest(("emergency", "Emergency headline reported by a feed", ""))
            await self.engine.deliver()
            rpc.assert_awaited_once()
            analyze.assert_not_awaited()
        payload = rpc.call_args.args[1]
        self.assertIn("🚨 Emergency headline · no AI", payload["message"])
        self.assertNotIn("https://example.com/news/emergency", payload["message"])
        self.assertIn("📰 2/1", payload["message"])
        self.assertNotIn("🎯", payload["message"])
        self.assertNotIn("attachment", payload)
        states = {i["guid"]: i["status"] for i in self.store.rows("SELECT guid,status FROM items")}
        self.assertEqual((states["baseline"], states["instant"], states["emergency"]), ("baseline", "outbox", "sent"))
        self.assertEqual(self.get(backlog)["status"], "queued")
        self.ingest(("pending", "Another emergency headline", ""))
        await self.command("/rapemode off")
        self.assertEqual(self.store.one("SELECT status,route FROM items WHERE guid='pending'"), {"status": "queued", "route": ""})
        await self.command(f"/feed instant {self.feed['id']} off")
        self.assertEqual(self.store.one("SELECT status FROM items WHERE guid='instant'")["status"], "queued")

    async def test_emergency_never_autopublishes_previews_or_posts_while_paused(self):
        self.config.update(mode="preview")
        await self.command("/rapemode on")
        self.ingest(("baseline", "Start feed here", ""))
        self.ingest(("preview", "A preview-only headline", ""))
        with patch.object(self.signal, "rpc", AsyncMock(return_value={"timestamp": NOW * 1000})) as rpc:
            await self.engine.deliver()
            self.config.update(mode="live", paused=True)
            self.ingest(("paused", "Headline fetched during pause", ""))
            await self.engine.deliver()
            rpc.assert_not_awaited()
            self.config.update(paused=False)
            await self.engine.deliver()
            rpc.assert_awaited_once()
        self.assertIn("Headline fetched during pause", rpc.call_args.args[1]["message"])
        self.assertEqual(self.store.one("SELECT status FROM items WHERE guid='preview'")["status"], "preview")

    async def test_per_feed_instant_forwards_once_and_does_not_release_into_another_group(self):
        self.config.update(focus="", include_images=True)
        await self.command(f"/feed instant {self.feed['id']} on")
        self.ingest(("baseline", "Prior headline in feed", ""))
        news_item = ("direct", "Bridge reopens after the outage", "")
        self.ingest(news_item)
        with patch.object(self.signal, "rpc", AsyncMock(return_value={"timestamp": NOW * 1000})) as rpc:
            await self.engine.deliver()
            self.ingest(news_item)
            await self.engine.deliver()
            rpc.assert_awaited_once()
            self.assertIn("⚡ Instant feed headline · no AI", rpc.call_args.args[1]["message"])
            self.ingest(("old-group", "Headline fetched for original group", ""))
            self.config.update(group_id="other-group", emergency=True)
            await self.engine.deliver()
            self.assertEqual(rpc.await_count, 1)
        self.assertEqual(self.store.one("SELECT status,route FROM items WHERE guid='old-group'"), {"status": "queued", "route": ""})

    async def test_second_vote_twenty_minutes_later_chains_confirmed_edit(self):
        self.config.update(topic_feedback=True, footer_score=True, feedback_weight=4)
        frame = Message().line("🇺🇸 Summary").add("🎯 ").line("91", "BOLD").add("🕒 ").add("4 min ago", "BOLD").build()
        item = self.posted("A major outage", rendered=recorded_message(frame, True, True, age=True))
        with patch.object(self.signal, "rpc", AsyncMock(side_effect=[
                {"timestamp": (NOW + 5) * 1000}, {"timestamp": (NOW + 1205) * 1000}])) as rpc:
            await self.react("first", item["signal_timestamp"], "👍")
            self.clock.return_value = NOW + 5
            await edit_footer(self.engine)
            self.clock.return_value = NOW + 1200
            await self.react("second", (NOW + 5) * 1000, "👍🏽")
            self.clock.return_value = NOW + 1205
            await edit_footer(self.engine)
            self.clock.return_value = NOW + 2700
            await self.react("late", item["signal_timestamp"], "👎")
            await edit_footer(self.engine)
        self.assertEqual(rpc.await_count, 2)
        first, second = [call.args[1] for call in rpc.await_args_list]
        self.assertIn("(+4)", first["message"])
        self.assertIn("(+8)", second["message"])
        self.assertNotIn("👍", second["message"])
        self.assertEqual(second["editTimestamp"], (NOW + 5) * 1000)
        self.assertNotIn("attachment", second)
        self.assertTrue(second["message"].endswith("\n🕒 4 min ago"))
        buf = second["message"].encode("utf-16-le")
        spans = [(int(start), int(n), kind) for start, n, kind in (span.split(":") for span in second["textStyle"])]
        self.assertEqual([(buf[2 * start:2 * (start + n)].decode("utf-16-le"), kind) for start, n, kind in spans],
                         [("91", "BOLD"), ("4 min ago", "BOLD")])
        self.assertEqual(vote_tally(self.store, item["id"]), {"n": 2, "score": 2})
        self.assertEqual(self.get(item)["score"], 91)
        why = await self.engine.command("/why", "other", {"group": self.group,
                                  "quote": {"id": (NOW + 1205) * 1000, "authorUuid": "test-bot-aci"}})
        self.assertIn("Votes closed:", why)
        self.assertIn("last edit accepted", why)

    async def test_unconfirmed_edit_is_not_retried_but_new_tally_can_replace_it(self):
        self.config.update(topic_feedback=True, footer_score=True)
        item = self.posted("An outage happened", rendered='{"text":"🎯 91","styles":[],"score":true}')
        with patch.object(self.signal, "rpc", AsyncMock(side_effect=[TimeoutError, {"timestamp": (NOW + 1205) * 1000}])) as rpc:
            await self.react("a", item["signal_timestamp"], "👍")
            self.clock.return_value = NOW + 5
            await edit_footer(self.engine)
            await edit_footer(self.engine)
            self.assertEqual(rpc.await_count, 1)
            self.clock.return_value = NOW + 1200
            await self.react("b", item["signal_timestamp"], "👍")
            self.clock.return_value = NOW + 1205
            await edit_footer(self.engine)
        self.assertEqual(rpc.await_count, 2)
        self.assertIn("(+6)", rpc.call_args.args[1]["message"])
        self.assertNotIn("👍", rpc.call_args.args[1]["message"])
        self.assertEqual(self.store.one("SELECT failed,edits FROM footer_edits"), {"failed": 0, "edits": 2})

    async def test_reaction_to_partial_edit_can_still_resolve_target(self):
        self.config.update(topic_feedback=True, footer_score=True)
        item = self.posted("Restored bank transfers", rendered='{"text":"🎯 91","styles":[],"score":true}')
        partial = (NOW + 5) * 1000
        with patch.object(self.signal, "rpc", AsyncMock(side_effect=[
                {"timestamp": partial, "results": [{"type": "SUCCESS"}, {"type": "NETWORK_FAILURE"}]},
                {"timestamp": (NOW + 1205) * 1000}])) as rpc:
            await self.react("a", item["signal_timestamp"], "👍")
            self.clock.return_value = NOW + 5
            await edit_footer(self.engine)
            self.assertEqual(self.store.one("SELECT failed FROM footer_edits")["failed"], 1)
            self.clock.return_value = NOW + 1200
            await self.react("b", partial, "👍")
            self.clock.return_value = NOW + 1205
            await edit_footer(self.engine)
        self.assertEqual(rpc.await_count, 2)
        self.assertIn("(+6)", rpc.call_args.args[1]["message"])
        self.assertEqual(rpc.call_args.args[1]["editTimestamp"], int(item["signal_timestamp"]))

    async def test_story_mute_marks_without_score_blocks_emergency_rewrite_and_can_be_removed(self):
        item = self.posted(APPOINTMENTS[0], rendered=recorded_message(Message().line(APPOINTMENTS[0]).build(), False, False))
        await self.command("/storymute on")
        await self.react("bad-group", item["signal_timestamp"], "❌", group="other")
        await self.react("bad-author", item["signal_timestamp"], "❌", author="not-our-bot")
        self.assertIsNone(self.store.one("SELECT id FROM story_mutes"))
        with patch.object(self.signal, "rpc", AsyncMock(side_effect=[{"timestamp": (NOW + 5) * 1000},
                                                                   {"timestamp": (NOW + 10) * 1000}])) as rpc:
            await self.react("a", item["signal_timestamp"], "❌")
            self.clock.return_value = NOW + 5
            await edit_footer(self.engine)
            self.assertIn("🔕 Story muted", rpc.call_args.args[1]["message"])
            self.assertIn(f"#{item['id']}", await self.command("/storymute list"))
            self.assertIn("muted for 2h", await self.command(f"/storymute edit {item['id']} 2h"))
            self.assertEqual(self.store.one("SELECT expires FROM story_mutes")["expires"], NOW + 7205)
            await self.command("/rapemode on")
            self.ingest(("baseline", "Current feed contents", ""))
            self.ingest(("duplicate", APPOINTMENTS[3], ""))
            await self.engine.deliver()
            self.assertEqual(rpc.await_count, 1)
            self.assertIn("Story muted", self.store.one("SELECT reason FROM items WHERE guid='duplicate'")["reason"])
            await self.command(f"/storymute remove {item['id']}")
            self.clock.return_value = NOW + 10
            await edit_footer(self.engine)
        self.assertEqual(rpc.await_count, 2)
        self.assertNotIn("Story muted", rpc.call_args.args[1]["message"])
        self.assertIsNone(self.store.one("SELECT id FROM story_mutes"))

    async def test_media_stays_entry_owned_and_gallery_is_a_single_send(self):
        self.config.update(include_images=True, footer_score=True, topic_feedback=True)
        self.ingest(("baseline", "Current feed contents", ""))
        common = '<media:content url="/shared.png" type="image/png"/>'
        gallery = "".join(f'<media:content url="/gallery-{n}.png" type="image/png"/>' for n in range(5))
        inline = """<description><![CDATA[<a href="/other-story"><img src="/wrong.png"></a>]]></description>"""
        self.ingest(("different", "Different update", '<media:content url="/different.png" type="image/png"/>' + common),
                    ("gallery", "Gallery report", gallery + '<media:thumbnail url="/small.png"/>'),
                    ("no-photo", "Update with no identifiable photo", common),
                    ("card", "Update with a related article card", inline))
        posts = {r["guid"]: r for r in self.store.rows("SELECT * FROM items")}
        entry = posts["gallery"]
        self.assertEqual(item_images(posts["different"]), ["https://example.com/different.png"])
        self.assertFalse(item_images(posts["no-photo"]))
        self.assertFalse(item_images(posts["card"]))
        self.assertEqual(item_images(entry), [f"https://example.com/gallery-{n}.png" for n in range(4)])
        self.store.update_item(entry["id"], status="outbox", rules=self.config.rules_key(), summary="A gallery update",
                               provider="groq", model="groq-20b", score=91)
        payloads = [sample_picture(color) for color in ("red", "lime", "blue", "yellow")]
        with patch("newsbot.engine.fetch_image", AsyncMock(side_effect=payloads)) as fetch, patch.object(
                self.signal, "rpc", AsyncMock(side_effect=[{"timestamp": NOW * 1000}, {"timestamp": (NOW + 6) * 1000}])) as rpc:
            await self.engine.deliver()
            self.clock.return_value = NOW + 1
            await self.react("a", NOW * 1000, "👍")
            self.clock.return_value = NOW + 6
            await edit_footer(self.engine)
        self.assertEqual([call.args[1] for call in fetch.await_args_list], item_images(entry))
        self.assertEqual(rpc.await_args_list[0].args[1]["attachment"], payloads)
        self.assertNotIn("attachment", rpc.await_args_list[1].args[1])
        self.assertEqual(rpc.await_args_list[1].args[1]["editTimestamp"], NOW * 1000)

    async def test_usage_is_short_with_many_aliases_version_has_five_highlights_and_recap_keeps_emoji(self):
        self.config.update(models=[{"name": f"alias-{n}", "provider": "groq", "model": "openai/gpt-oss-20b",
                                    "key": "test-value", "daily_requests": 123} for n in range(12)])
        brief = await self.command("/usage")
        self.assertLess(len(brief.encode()), SIGNAL_INLINE_BYTES)
        self.assertIn("(+2)", brief)
        self.assertIn("🟩", brief)
        self.assertNotIn("Page ", brief)
        self.assertNotIn("Page ", await self.command("/usage details alias-6"))
        self.assertIn("dashboard", await self.command("/usage details 2"))
        first_models = await self.command("/models")
        self.assertIn("/models 2", first_models)
        self.assertNotIn("alias-5", first_models)
        self.assertIn("alias-5", await self.command("/models 2"))
        version = await self.command("/version")
        self.assertIn(f"{VERSION} · {RELEASES[VERSION][0]}", version)
        self.assertEqual(version.count("\n• "), 5)
        self.posted("Some news")
        with patch.object(self.pool, "recap", AsyncMock(return_value=[
                "• ⚖️ — ⚖️ Court issued a new order.", "Another report restored payments."])):
            recap = await self.command("/summary")
        self.assertIn("• ⚖️ Court issued a new order.", recap)
        self.assertIn("• 📰 Another report restored payments.", recap)
        self.assertLess(len(recap.encode()), SIGNAL_INLINE_BYTES)
