import base64
import importlib.util
import json
import os
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import TestClient, TestServer
import aiohttp
from aiohttp import web

from newsbot.ai import ModelOutputError, ModelPool, PoolUnavailable, parse_decisions
from newsbot.config import Config
from newsbot.controls import help_message
from newsbot.engine import Engine
from newsbot.formatting import SignalText
from newsbot.store import Store
from newsbot.version import VERSION
from newsbot.web import create_app
from test_bot import Base, decision


class RetentionTests(Base, unittest.TestCase):
    def setUp(self):
        self.setup_data()

    def tearDown(self):
        self.cleanup_data()

    def age_item(self, state, days=60):
        row = self.add_item(state=state, title=state + str(time.time_ns()))
        old = time.time() - days * 86400
        self.store.execute("UPDATE items SET discovered=?,published=?,sent_at=? WHERE id=?", (old, old, old, row["id"]))
        return row

    def test_retention_protects_inflight_and_recent(self):
        protected = [self.age_item(s) for s in ("queued", "outbox", "sending", "uncertain")]
        recent = self.age_item("sent", 2)
        for state in ("sent", "baseline", "skipped", "expired", "preview"):
            self.age_item(state)
        self.assertEqual(self.store.cleanup(self.config.data), 5)
        remaining = {r["id"] for r in self.store.rows("SELECT id FROM items")}
        self.assertEqual(remaining, {r["id"] for r in protected} | {recent["id"]})

    def test_cleanup_keeps_configuration_feeds_and_today_quota(self):
        self.age_item("sent")
        self.store.count_usage("groq-20b")
        before = self.config.path.read_bytes()
        self.store.cleanup(self.config.data)
        self.assertEqual(self.config.path.read_bytes(), before)
        self.assertEqual(len(self.store.rows("SELECT * FROM feeds")), 1)
        self.assertEqual(self.store.usage("groq-20b")["requests"], 1)

    def test_tombstones_prevent_reimport_by_url_and_guid(self):
        row = self.age_item("skipped")
        self.store.cleanup(self.config.data)
        self.assertTrue(self.store.seen_article(row["feed_id"], "another-guid", row["url"]))
        self.assertTrue(self.store.seen_article(row["feed_id"], row["guid"], "https://example.com/different"))
        self.store.insert_items([(row["feed_id"], row["guid"], row["url"], "Old headline", "", time.time(), time.time(), "queued")])
        self.assertIsNone(self.store.one("SELECT id FROM items"))
        self.store.execute("UPDATE seen_articles SET expires=?", (time.time() - 1,))
        self.assertFalse(self.store.seen_article(row["feed_id"], row["guid"], row["url"]))

    def test_deleted_article_ids_are_not_reused_by_ingestion(self):
        row = self.age_item("skipped")
        self.store.cleanup(self.config.data)
        self.store.insert_items([(row["feed_id"], "new-guid", "https://example.com/new", "New headline", "", time.time(), time.time(), "queued")])
        self.assertGreater(self.store.one("SELECT id FROM items")["id"], row["id"])

    def test_compaction_shrinks_and_preserves_remaining_rows(self):
        self.store.execute("CREATE TABLE waste (data BLOB)")
        self.store.execute("INSERT INTO waste VALUES(zeroblob(1000000))")
        self.store.execute("DELETE FROM waste")
        before = self.store.storage()["bytes"]
        self.store.compact()
        self.assertLess(self.store.storage()["bytes"], before)
        self.assertEqual(self.store.one("PRAGMA integrity_check")["integrity_check"], "ok")
        self.assertIsNotNone(self.store.one("SELECT id FROM feeds"))

    def test_upgrade_adds_defaults_without_overwriting_preferences(self):
        existing = json.loads(self.config.path.read_text())
        for key in ("footer_usage", "summary_length", "retention_articles_days"):
            existing.pop(key, None)
        existing.update(preferred_model="groq-20b", footer_model=False)
        self.config.path.write_text(json.dumps(existing))
        migrated = Config(self.config.path)
        self.assertEqual(migrated.data["summary_length"], "normal")
        self.assertTrue(migrated.data["footer_usage"])
        self.assertFalse(migrated.data["footer_model"])
        self.assertEqual(migrated.data["preferred_model"], "groq-20b")
        second = Store(self.config.path.parent / "news.db")
        second.close()

    def test_length_limits(self):
        content = json.dumps({"decisions": [decision(1, text=" ".join(["news"] * 100))]})
        with self.assertRaises(ModelOutputError):
            parse_decisions(content, [{"id": 1}], "normal")
        self.assertTrue(parse_decisions(content, [{"id": 1}], "detailed"))
        short = json.dumps({"decisions": [decision(1, text=" ".join(["news"] * 46))]})
        with self.assertRaises(ModelOutputError):
            parse_decisions(short, [{"id": 1}], "short")


class NewCommands(Base, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.setup_data()
        self.pool = ModelPool(self.store, self.config, None)
        self.signal = AsyncMock()
        self.signal.connected = True
        self.engine = Engine(self.store, self.config, self.pool, self.signal)

    def tearDown(self):
        self.cleanup_data()

    async def command(self, text, source="web"):
        self.engine.last_command.clear()
        return await self.engine.command(text, source)

    async def test_version_and_comprehensive_help(self):
        self.assertIn(VERSION, await self.command("/status"))
        self.assertIn(VERSION, await self.command("/version"))
        message = await self.command("/help")
        for cmd in ("/cleanup", "/retention", "/length", "/refresh", "/footer", "/limit", "/usage", "/errors", "/last", "/queue"):
            self.assertIn(cmd, message)
        self.assertIn("never deletes sent Signal messages", message)
        self.assertLess(len(message.encode("utf-16-le")) // 2, 6500)
        for span in message.styles:
            start, size, _ = span.split(":")
            self.assertLessEqual(int(start) + int(size), len(message.encode("utf-16-le")) // 2)
        self.assertNotIn("/model <", await self.command("/help feeds"))

    async def test_cleanup_confirmation_sender_expiry_and_protected_race(self):
        item = self.add_item("skipped")
        self.store.execute("UPDATE items SET discovered=? WHERE id=?", (time.time() - 60 * 86400, item["id"]))
        self.assertIn("preview first", await self.command("/cleanup confirm", "alice"))
        await self.command("/cleanup preview", "alice")
        self.assertIn("preview first", await self.command("/cleanup confirm", "bob"))
        self.store.update_item(item["id"], status="uncertain")
        self.assertIn("Removed 0", await self.command("/cleanup confirm", "alice"))
        self.assertIsNotNone(self.store.one("SELECT id FROM items WHERE id=?", (item["id"],)))
        await self.command("/cleanup preview", "alice")
        stamp, ids, policy = self.engine.cleanup_previews["alice"]
        self.engine.cleanup_previews["alice"] = (stamp - 301, ids, policy)
        self.assertIn("preview first", await self.command("/cleanup confirm", "alice"))
        self.signal.send.assert_not_called()
        self.signal.rpc.assert_not_called()

    async def test_cleanup_success_and_retention_validation(self):
        item = self.add_item("skipped")
        self.store.execute("UPDATE items SET discovered=? WHERE id=?", (time.time() - 60 * 86400, item["id"]))
        await self.command("/cleanup preview")
        self.assertIn("Removed 1", await self.command("/cleanup confirm"))
        self.assertIn("never deleted", await self.command("/retention"))
        await self.command("/retention articles 14d")
        self.assertEqual(self.config.data["retention_articles_days"], 14)
        for cmd in ("/retention articles 0d", "/retention articles 999d", "/retention history nope", "/retention secrets 2d"):
            self.assertIn("⚠️", await self.command(cmd))

    async def test_cleanup_does_not_delete_newly_eligible_unpreviewed_records(self):
        first = self.add_item("skipped", title="first")
        self.store.execute("UPDATE items SET discovered=? WHERE id=?", (time.time() - 60 * 86400, first["id"]))
        await self.command("/cleanup preview")
        second = self.add_item("skipped", title="second")
        self.store.execute("UPDATE items SET discovered=? WHERE id=?", (time.time() - 60 * 86400, second["id"]))
        self.assertIn("Removed 1", await self.command("/cleanup confirm"))
        self.assertIsNotNone(self.store.one("SELECT id FROM items WHERE id=?", (second["id"],)))

    async def test_automatic_retention_runs_hourly_even_while_paused(self):
        self.config.update(paused=True)
        item = self.add_item("skipped")
        self.store.execute("UPDATE items SET discovered=? WHERE id=?", (time.time() - 60 * 86400, item["id"]))
        self.engine.maintain()
        self.assertIsNone(self.store.one("SELECT id FROM items"))
        self.assertFalse(self.store.maintenance_due("article-cleanup", 3600))
        self.signal.rpc.assert_not_called()

    async def test_refresh_interval_and_failure_backoff(self):
        retry = time.time() + 500
        self.store.execute("UPDATE feeds SET error='HTTP 403',next_poll=?", (retry,))
        await self.command("/refresh 5m")
        self.assertEqual(self.config.data["poll_seconds"], 300)
        await self.command(f"/refresh feed {self.feed['id']} 10m")
        feed = self.store.one("SELECT * FROM feeds")
        self.assertEqual(feed["interval"], 600)
        self.assertEqual(feed["next_poll"], retry)
        await self.command("/refresh now")
        self.assertEqual(self.store.one("SELECT next_poll FROM feeds")["next_poll"], retry)
        self.assertIn("Wait one minute", await self.command("/refresh now"))
        for cmd in ("/refresh 1s", "/refresh 99h", "/refresh feed abc 5m", "/refresh nonsense"):
            self.assertIn("⚠️", await self.command(cmd))

    async def test_length_and_rate_changes_persist_and_change_rules(self):
        old_rules = self.config.rules_key()
        await self.command("/length detailed")
        self.assertNotEqual(old_rules, self.config.rules_key())
        await self.command("/limit 10")
        self.assertEqual(Config(self.config.path).data["max_posts_per_hour"], 10)
        for cmd in ("/limit 0", "/limit 301", "/length infinite"):
            self.assertIn("⚠️", await self.command(cmd))

    async def test_usage_visual_is_local_and_snapshots_marked_stale(self):
        self.config.data["models"][0]["key"] = "fixture-key"
        self.store.count_usage("groq-20b")
        self.store.set_model_state("groq-20b", quota={"requests": 998, "tokens": 5419})
        self.store.execute("UPDATE model_state SET checked=?", (time.time() - 600,))
        brief = await self.command("/usage")
        self.assertIn("groq-20b 899/900", brief)
        self.assertNotIn("998", brief)
        self.assertIn("🟩", brief)
        reply = await self.command("/usage details groq-20b")
        self.assertIn("🟩", reply)
        self.assertIn("899 / 900 local attempts left", reply)
        self.assertIn("998", reply)
        self.assertIn("stale", reply)
        self.assertNotIn("Provider quota: not reported", reply)
        self.assertIn("don’t guarantee capacity", await self.command("/help ai"))

    async def test_footer_all_off_and_native_styles_when_on(self):
        self.config.update(mode="live", footer_model=False, footer_age=False, footer_usage=False, include_sources=False)
        self.signal.send.return_value = {"timestamp": 123}
        for enabled in (False, True):
            self.config.update(footer_model=enabled, footer_age=enabled, footer_usage=enabled)
            row = self.add_item("outbox", title="article" + str(enabled))
            self.store.execute("UPDATE items SET published=1700000000,discovered=1700000001 WHERE id=?", (row["id"],))
            self.store.update_item(row["id"], rules=self.config.rules_key(), summary="📰 — News summary", model="groq-20b", actual_model="openai/gpt-oss-20b", provider="groq")
            with patch("newsbot.engine.time.time", return_value=1700000120):
                await self.engine.deliver()
            reply = self.signal.send.call_args.args[0]
            self.assertIsInstance(reply, SignalText)
            if enabled:
                self.assertIn("Groq · GPT-OSS 20B · 900/900 calls", reply)
                self.assertIn("2 min ago", reply)
                self.assertTrue(any(s.endswith(":ITALIC") for s in reply.styles))
                self.assertTrue(any(s.endswith(":BOLD") for s in reply.styles))
            else:
                self.assertEqual(reply, "📰 — News summary")

    async def test_dashboard_sections_and_preferences_security(self):
        app = create_app(self.config, self.store, self.pool, self.signal, self.engine)
        client = TestClient(TestServer(app))
        await client.start_server()
        auth = {"Authorization": "Basic " + base64.b64encode(("admin:" + self.config.data["dashboard_password"]).encode()).decode()}
        self.config.data["models"][0]["key"] = "NEVER-RENDER-THIS-KEY"
        try:
            for page in ("overview", "news", "feeds", "models", "settings", "help"):
                response = await client.get("/?page=" + page, headers=auth)
                self.assertEqual(response.status, 200)
                html = await response.text()
                self.assertIn("v" + VERSION, html)
                self.assertNotIn("NEVER-RENDER-THIS-KEY", html)
                self.assertNotIn("<script", html)
                self.assertNotIn("cdn.", html)
            response = await client.get("/help", headers=auth)
            self.assertEqual(response.status, 200)
            self.assertIn("/cleanup confirm", await response.text())
            response = await client.post("/preferences", headers=auth, data={})
            self.assertEqual(response.status, 403)
            response = await client.post("/command", headers=auth, data={"csrf": self.config.data["csrf"], "command": "/cleanup preview"})
            self.assertEqual(response.status, 200)
            self.assertIn("Signal messages", await response.text())
            response = await client.post("/command", headers=auth, data={"csrf": self.config.data["csrf"], "command": "/model auto"})
            self.assertEqual(response.status, 400)
            preferences = {"csrf": self.config.data["csrf"], "summary_length": "short", "retention_articles_days": "14", "retention_history_days": "60", "backup_retention_days": "90", "backup_keep": "4", "footer_model": "on"}
            response = await client.post("/preferences", headers=auth, data=preferences, allow_redirects=False)
            self.assertEqual(response.status, 303)
            self.assertEqual(Config(self.config.path).data["summary_length"], "short")
            self.assertFalse(self.config.data["footer_usage"])
            self.assertEqual(self.config.data["retention_articles_days"], 14)
            before = self.config.path.read_bytes()
            preferences["retention_articles_days"] = "0"
            response = await client.post("/preferences", headers=auth, data=preferences)
            self.assertEqual(response.status, 400)
            self.assertEqual(before, self.config.path.read_bytes())
        finally:
            await client.close()

    async def test_openrouter_reasoning_request_and_safe_429(self):
        requests = []
        async def answer(request):
            requests.append(await request.json())
            return web.json_response({"error": {"message": "UNSAFE-RAW-PROVIDER-DETAIL"}}, status=429, headers={"retry-after": "123"})
        app = web.Application()
        app.router.add_post("/", answer)
        server = TestServer(app)
        await server.start_server()
        self.config.update(models=[{"name": "free", "provider": "openrouter", "model": "openrouter/free", "key": "fake-key", "daily_requests": 50}], summary_length="short")
        try:
            async with aiohttp.ClientSession() as session:
                pool = ModelPool(self.store, self.config, session)
                with patch("newsbot.ai.endpoint", return_value=str(server.make_url("/"))), patch.object(pool, "reported_quota", AsyncMock(return_value={})):
                    with self.assertRaises(PoolUnavailable):
                        await pool.analyze([self.add_item()], [])
                body = requests[0]
                self.assertEqual(body["reasoning"], {"effort": "low", "exclude": True})
                self.assertEqual(body["max_tokens"], 4096)
                self.assertEqual(body["provider"]["max_price"], {"prompt": 0, "completion": 0})
                self.assertIn("One sentence", body["messages"][0]["content"])
                state = self.store.model_state("free")
                self.assertIn("daily exhaustion not established", state["error"])
                self.assertNotIn("UNSAFE", state["error"])
                self.assertGreater(state["until"], time.time() + 110)
        finally:
            await server.close()


class BackupPolicyTests(Base, unittest.TestCase):
    def setUp(self):
        self.setup_data()

    def tearDown(self):
        self.cleanup_data()

    def test_only_old_recognized_backups_and_minimum_kept(self):
        spec = importlib.util.spec_from_file_location("prune", Path(__file__).resolve().parents[1] / "scripts/prune-backups.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        root = Path(self.tmp.name) / "backups"
        root.mkdir()
        now = 2000000000
        for i in range(5):
            directory = root / str(i)
            directory.mkdir()
            for name in ("complete", "release", "data.tar.gz", "SHA256SUMS"):
                (directory / name).touch()
            (directory / "marker").write_text("signal-news-bot-v1\n")
            os.utime(directory / "complete", (now - (i + 40) * 86400,) * 2)
        unfamiliar = root / "unfamiliar"
        unfamiliar.mkdir()
        (root / "symlink").symlink_to(root / "0", target_is_directory=True)
        result = module.candidates(root, days=30, keep=3, now=now)
        self.assertEqual({p.name for p in result}, {"3", "4"})
        self.assertTrue(unfamiliar.exists())


class HelpTests(unittest.TestCase):
    def test_topic_help_is_short(self):
        self.assertLess(len(help_message("feeds")), 2000)
