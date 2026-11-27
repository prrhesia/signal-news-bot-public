import asyncio
import json
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer

from newsbot.ai import ModelPool
from newsbot.config import Config
from newsbot.editorial import local_duplicate, related, terms
from newsbot.engine import Engine
from newsbot.feeds import FeedWorker
from newsbot.formatting import SIGNAL_INLINE_BYTES, duration_label
from newsbot.interactions import edit_footer, preference_delta, topic_preferences, weighted_feedback
from newsbot.moderation import arm_reply, process_removals
from newsbot.publishing import model_label
from newsbot.signal import Signal
from newsbot.store import Store
from test_bot import Base, decision, sample_picture

NOW = 1_790_000_000
FIRST_CHIPS = ("The Trump administration approved the sale of nearly 900,000 AI semiconductor chips to "
               "Defense Department–designated Chinese military companies, including Nvidia H200s.")
REPEAT_CHIPS = "US signed off on sales of nearly 900k AI chips to China in six months."


class Persistence104(Base, unittest.TestCase):
    def setUp(self):
        self.setup_data()

    def tearDown(self):
        self.cleanup_data()

    def test_legacy_settings_and_defaults_are_preserved(self):
        data = self.config.data
        data.update(focus="Saved focus", exclude="Saved exclusions", footer_small=True, include_images=True, topic_feedback=True)
        data["models"][0]["key"] = "fixture-only"
        del data["image_min_score"], data["feedback_weight"], data["reply_cleanup"]
        self.config.save()
        migrated = Config(self.config.path).data
        self.assertNotIn("footer_small", migrated)
        self.assertEqual((migrated["image_min_score"], migrated["feedback_weight"], migrated["reply_cleanup"]), (75, 3, False))
        self.assertEqual((migrated["focus"], migrated["exclude"]), ("Saved focus", "Saved exclusions"))
        self.assertEqual(migrated["models"][0]["key"], "fixture-only")
        self.assertTrue(migrated["include_images"] and migrated["topic_feedback"])

    def test_restart_does_not_resume_unfinished_purges(self):
        for item_id, state in enumerate(("sending", "queued", "accepted"), 1):
            self.store.execute("INSERT INTO withdrawals VALUES(?,?,?,?)", (item_id, "fixture", NOW, state))
        reopened = Store(Path(self.tmp.name) / "news.db", recover=True)
        try:
            self.assertEqual([r["state"] for r in reopened.rows("SELECT state FROM withdrawals ORDER BY item_id")],
                             ["unconfirmed", "skipped", "accepted"])
        finally:
            reopened.close()

    def test_cloudflare_display_shortens_only_the_footer(self):
        row = {"provider": "cloudflare", "actual_model": "@cf/qwen/qwen3-30b-a3b-fp8", "decision": '{"reported": true}'}
        self.assertEqual(model_label(row), "Cloudflare · qwen3-30b-a3b-fp8")
        self.assertEqual(model_label(row, exact=True), "Cloudflare · @cf/qwen/qwen3-30b-a3b-fp8")
        self.assertEqual(duration_label(45), "45s")
        self.assertEqual(duration_label(75), "75s (1m 15s)")
        self.assertEqual(duration_label(3600), "3,600s (1h)")
        self.assertEqual(weighted_feedback(-4, 3), -10)
        self.assertEqual(weighted_feedback(4, 3), 10)

    def test_sale_case_does_not_merge_materially_different_developments(self):
        old = {"id": 1, "title": FIRST_CHIPS, "summary": FIRST_CHIPS}
        stories = [(old, terms(old["title"]), terms(old["summary"]))]
        for title in ("US authorizes a second sale of 900,000 AI chips to China",
                      "US approves 1.2 million AI chip sales to China",
                      "US approves sale of 900k AI chips to Japan"):
            new = {"id": 2, "title": title}
            self.assertIsNone(local_duplicate(new, title, related(new, stories)), title)


class Group104(Base, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.setup_data()
        self.session = aiohttp.ClientSession()
        self.signal = Signal(self.config, self.store, self.session)
        self.signal.connected = True
        self.signal.discovered_account = "fixture-bot-account"
        self.signal.account_ids = {"fixture-bot-account": {"fixture-bot-account", "fixture-aci"}}
        self.pool = ModelPool(self.store, self.config, self.session)
        self.engine = Engine(self.store, self.config, self.pool, self.signal, FeedWorker(self.store, self.config, self.session))
        self.config.update(mode="live")
        self.scope = self.engine.scope
        self.group = self.scope[0]

    async def asyncTearDown(self):
        await self.session.close()
        self.cleanup_data()

    def news(self, title, state="sent", age=100, **values):
        row = self.add_item(state, title=title)
        base = {"rules": self.config.rules_key(), "summary": title, "model": "groq-20b", "provider": "groq",
                "score": 86, "actual_model": "openai/gpt-oss-20b", "sent_at": NOW - age,
                "signal_timestamp": str((NOW - age) * 1000 + row["id"]), "sent_account": self.signal.current_account}
        base.update(values)
        self.store.update_item(row["id"], **base)
        return self.store.one("SELECT * FROM items WHERE id=?", (row["id"],))

    async def command(self, body, sender="member-a", group=None):
        # Commands arrive independently in Signal. Keep cooldown coverage in the
        # transport tests, and drive confirmation races here without real sleeps.
        self.engine.last_command.clear()
        return await self.engine.command(body, sender, {"group": self.group if group is None else group})

    def reaction(self, stamp, emoji, event_at, remove=False):
        return {"group": self.group, "event_at": event_at, "reaction": {
            "targetSentTimestamp": int(stamp), "targetAuthorUuid": "fixture-aci", "emoji": emoji, "isRemove": remove}}

    async def test_purge_is_member_bound_and_only_targets_previewed_own_news(self):
        with patch("newsbot.moderation.time.time", return_value=NOW):
            eligible = self.news("A bank restored payment systems")
            self.store.execute("INSERT INTO edit_targets VALUES(?,?)", (str((NOW - 5) * 1000), eligible["id"]))
            self.news("Almost a day old", age=86399, signal_timestamp=str((NOW - 86401) * 1000))
            self.news("A recipient accepted this unconfirmed send", "uncertain")
            self.news("Wrong group", rules='{"group_id":"different"}')
            self.news("Previous account", sent_account="other-account")
            self.news("Outside selected window", age=3600)
            self.news("Signal send time precedes the window", age=1790, signal_timestamp=str((NOW - 1801) * 1000))
            arm_reply(self.store, "/status", {"timestamp": (NOW - 25) * 1000}, self.scope, self.signal.current_account)
            self.assertIsNone(await self.command("/purge 30m", group="foreign"))
            self.assertIn("configured Signal group", await self.engine.command("/purge 30m"))
            self.assertIn("Remove 1 bot news post from Signal", await self.command("/purge 30m"))
            self.news("Arrived after preview")
            self.assertIn("No valid", await self.command("/purge confirm", "other"))
            self.assertIsNone(self.store.one("SELECT state FROM withdrawals"))
            self.assertIn("Queued 1", await self.command("/purge confirm"))
            with patch.object(self.signal, "rpc", AsyncMock(return_value={
                    "timestamp": NOW * 1000, "results": [{"type": "SUCCESS"}]})) as rpc:
                await process_removals(self.engine)
                await process_removals(self.engine)
            rpc.assert_awaited_once_with("remoteDelete", {"groupId": self.group, "targetTimestamp": int(eligible["signal_timestamp"])})
            self.assertIn("Accepted: 1", await self.command("/purge status"))
            self.assertEqual(self.store.one("SELECT status FROM items WHERE id=?", (eligible["id"],))["status"], "sent")
            self.assertIn("Post slots used in the last hour: 4", await self.engine.command("/queue"))

    async def test_purge_partial_response_is_unconfirmed_and_not_retried_or_recapped(self):
        self.config.update(paused=True)
        with patch("newsbot.moderation.time.time", return_value=NOW):
            target = self.news("Previous claim removed from group")
            await self.command("/purge 30m")
            await self.command("/purge confirm")
            with patch.object(self.signal, "rpc", AsyncMock(return_value={"timestamp": NOW * 1000, "results": [
                    {"type": "SUCCESS"}, {"type": "NETWORK_FAILURE"}]})) as rpc:
                await process_removals(self.engine)
                await process_removals(self.engine)
            rpc.assert_awaited_once()
            self.assertIn("Unconfirmed: 1", await self.command("/purge status"))
            self.assertIn("No eligible", await self.command("/purge 30m"))
            self.news("A normal story stays")
            self.assertNotIn("Previous claim", await self.command("/summary"))
            self.assertEqual(self.store.one("SELECT status FROM items WHERE id=?", (target["id"],))["status"], "sent")

    async def test_purge_preview_expiry_and_switch_of_account(self):
        with patch("newsbot.moderation.time.time", return_value=NOW):
            self.news("A post to protect from a stale confirmation")
            self.assertIn("Remove 1", await self.command("/purge 30m"))
        with patch("newsbot.moderation.time.time", return_value=NOW + 301):
            self.assertIn("No valid", await self.command("/purge confirm"))
            await self.command("/purge 30m")
            await self.command("/purge confirm")
            self.config.update(signal_account="fixture-other-account")
            with patch.object(self.signal, "rpc", AsyncMock()) as rpc:
                await process_removals(self.engine)
            rpc.assert_not_awaited()

    async def test_real_sse_command_replies_are_exempt_or_labelled_and_due_after_three_minutes(self):
        self.config.update(reply_cleanup=True, paused=True)
        now = NOW * 1000
        replies_sent = asyncio.Event()
        data = []
        for i, command in enumerate(("/status", "/summary", "/why")):
            packet = {"envelope": {"sourceUuid": f"member-{i}", "timestamp": now - i,
                                  "dataMessage": {"message": command, "groupInfo": {"groupId": self.group}}}}
            data.append("data: " + json.dumps(packet) + "\n\n")

        async def server_events(request):
            return web.Response(text="".join(data), content_type="text/event-stream")

        calls = []

        async def rpc(method, params):
            calls.append((method, params))
            if len(calls) == 1:
                return {"timestamp": now - 1, "results": [{"type": "SUCCESS"}, {"type": "NETWORK_FAILURE"}]}
            if method == "send" and len(calls) == 3:
                replies_sent.set()
            return {"timestamp": now - len(calls), "results": [{"type": "SUCCESS"}]}

        app = web.Application()
        app.router.add_get("/api/v1/events", server_events)
        async with TestServer(app) as server:
            self.config.update(signal_url=str(server.make_url("")).rstrip("/"))
            with patch.object(self.signal, "rpc", side_effect=rpc):
                with patch("newsbot.signal.time.time", return_value=NOW):
                    task = asyncio.create_task(self.signal.listen(self.engine.command))
                    try:
                        await asyncio.wait_for(replies_sent.wait(), 2)
                    finally:
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                self.signal.connected = True
                queue = self.store.rows("SELECT target,due FROM reply_removals")
                self.assertEqual(queue, [{"target": now - 1, "due": (now - 1) / 1000 + 180}])
                self.assertTrue(calls[0][1]["message"].endswith("⏳ 3m"))
                self.assertTrue(calls[0][1]["textStyle"][-1].endswith(":ITALIC"))
                self.assertTrue(all("⏳ 3m" not in payload["message"] for _, payload in calls[1:]))
                with patch("newsbot.moderation.time.time", return_value=NOW + 179):
                    await process_removals(self.engine)
                self.assertEqual(len(calls), 3)
                with patch("newsbot.moderation.time.time", return_value=NOW + 180):
                    await process_removals(self.engine)
                self.assertEqual(calls[-1], ("remoteDelete", {"groupId": self.group, "targetTimestamp": now - 1}))

    async def test_turning_reply_cleanup_off_cancels_pending_and_never_targets_news(self):
        self.config.update(reply_cleanup=True)
        with patch("newsbot.moderation.time.time", return_value=NOW):
            target = self.news("News post")
            arm_reply(self.store, "/status", {"timestamp": int(target["signal_timestamp"])}, self.scope, self.signal.current_account)
            arm_reply(self.store, "/status", {"timestamp": NOW * 1000}, self.scope, self.signal.current_account)
            self.assertEqual(len(self.store.rows("SELECT * FROM reply_removals")), 2)
        with patch("newsbot.moderation.time.time", return_value=NOW + 170):
            with patch.object(self.signal, "rpc", AsyncMock()) as rpc:
                await process_removals(self.engine)
            rpc.assert_not_awaited()
            self.assertIsNotNone(self.store.one("SELECT * FROM reply_removals"))
            await self.engine.command("/replycleanup off")
            self.assertFalse(self.config.data["reply_cleanup"])
            self.assertIsNone(self.store.one("SELECT * FROM reply_removals"))

    async def test_duplicate_chip_example_and_genuine_followup(self):
        with patch("newsbot.engine.time.time", return_value=NOW):
            old = self.news(FIRST_CHIPS, age=8 * 60)
            self.store.add_feed("https://news.example/second", "Other outlet")
            self.feed = self.store.one("SELECT * FROM feeds WHERE title='Other outlet'")
            with patch("test_bot.time.time_ns", side_effect=(3450000000, 3450000001)):
                repeated = self.add_item(title=REPEAT_CHIPS)
                reversal = self.add_item(title="US reverses approval of 900,000 AI chip sales to China")
            with patch.object(self.pool, "analyze", AsyncMock(return_value=("cloudflare-gemma", [
                decision(repeated["id"], text=REPEAT_CHIPS), decision(reversal["id"], text=reversal["title"])]))) as analyze:
                await self.engine.process()
            self.assertIn("nearly 900,000", analyze.call_args.args[1][0]["title"])
            self.assertEqual(self.store.one("SELECT status FROM items WHERE id=?", (repeated["id"],))["status"], "skipped")
            self.assertEqual(self.store.one("SELECT status FROM items WHERE id=?", (reversal["id"],))["status"], "outbox")
            self.store.update_item(repeated["id"], status="outbox", summary=REPEAT_CHIPS)
            with patch.object(self.signal, "send", AsyncMock()) as send:
                await self.engine.deliver()
                send.assert_not_called()
            self.assertEqual(self.store.one("SELECT status FROM items WHERE id=?", (old["id"],))["status"], "sent")

    async def test_image_cutoff_checks_original_score_and_caption_edit_is_weighted(self):
        self.config.update(include_images=True, topic_feedback=True, footer_score=True)
        with patch("newsbot.engine.time.time", return_value=NOW):
            below = self.news("A utility outage affects 5 towns", "outbox", score=74)
            equal = self.news("A bank faces a widespread card outage", "outbox", score=75)
            self.store.execute("UPDATE items SET image_url=? WHERE status='outbox'", ("https://news.example/pic.png",))
            # The low original score stays text even if topic preference was positive.
            self.store.update_item(below["id"], decision='{"preference": 7}')
            uri = sample_picture()
            with patch("newsbot.engine.fetch_image", AsyncMock(return_value=uri)) as download, patch.object(
                    self.signal, "rpc", AsyncMock(side_effect=[{"timestamp": NOW * 1000 - 1}, {"timestamp": NOW * 1000}])) as rpc:
                await self.engine.deliver()
                await self.engine.deliver()
            download.assert_awaited_once()
            self.assertNotIn("attachment", rpc.call_args_list[0].args[1])
            self.assertEqual(rpc.call_args_list[1].args[1]["attachment"], [uri])
            posted = self.store.one("SELECT * FROM items WHERE id=?", (equal["id"],))
            self.assertTrue(json.loads(posted["rendered"])["image"])
            self.assertIn("score needed: 75/100", await self.engine.command("/images"))
            self.assertIn("score needed: 78/100", await self.engine.command("/images min 78"))
            self.assertIn("⚠️", await self.engine.command("/images min 101"))
            self.assertIn("👍 +3", await self.engine.command("/feedback"))
            self.assertIn("👍 +6", await self.engine.command("/feedback weight 6"))
            context = self.reaction(posted["signal_timestamp"], "👍", NOW * 1000)
            await self.engine.command("/_reaction", "member-a", context)
            await self.engine.command("/_reaction", "member-b", context)
            prefs = topic_preferences(self.store, self.config.data, NOW)
            self.assertEqual(preference_delta(posted, prefs, self.config.data["feedback_weight"]), 10)
            self.store.execute("UPDATE footer_edits SET due=1")
            with patch.object(self.signal, "rpc", AsyncMock(return_value={"timestamp": NOW * 1000 + 1})) as edit:
                await edit_footer(self.engine)
            self.assertEqual(edit.call_args.args[1]["editTimestamp"], NOW * 1000)
            self.assertNotIn("attachment", edit.call_args.args[1])
            self.assertIn("🎯 75 (+10)", edit.call_args.args[1]["message"])
            self.assertEqual(self.store.one("SELECT score FROM items WHERE id=?", (equal["id"],))["score"], 75)

    async def test_recap_layout_is_inline_and_local_fallback_groups_repeat(self):
        self.config.update(paused=True)
        with patch("newsbot.interactions.time.time", return_value=NOW):
            self.news(FIRST_CHIPS, age=60, summary="🇺🇸 🇨🇳 🤖 — " + FIRST_CHIPS)
            self.news(REPEAT_CHIPS, age=50, summary="🇺🇸 🇨🇳 🤖 — " + REPEAT_CHIPS)
            self.news("Bank restores app payments", age=10, summary="🏦 — A bank restored app payments.")
            text = await self.command("/summary 12")
        self.assertEqual(text.splitlines()[:2], ["🗞️ News recap · last 12h", ""])
        self.assertIn("• 🏦 A bank restored app payments.", text)
        self.assertEqual(text.count("• "), 2)
        self.assertIn("• 🇺🇸 🇨🇳", text)
        self.assertLessEqual(len(text.encode()), SIGNAL_INLINE_BYTES)

    async def test_inflight_recap_does_not_replay_just_purged_content(self):
        with patch("newsbot.interactions.time.time", return_value=NOW):
            row = self.news("Prior announcement captured before its purge")

            async def recap(_):
                self.store.execute("INSERT INTO withdrawals VALUES(?,?,?,'accepted')",
                                   (row["id"], self.signal.current_account, NOW))
                return ["Never return this withdrawn announcement."]

            with patch.object(self.pool, "recap", side_effect=recap):
                result = await self.command("/summary")
        self.assertIn("withdrawn", result)
        self.assertNotIn("Never return", result)
