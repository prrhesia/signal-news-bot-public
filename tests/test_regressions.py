import asyncio
import base64
import json
import sqlite3
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from newsbot.__main__ import exclusive_run
from newsbot.ai import ModelPool
from newsbot.engine import Engine
from newsbot.feeds import FeedWorker
from newsbot.http import read_json
from newsbot.signal import Signal, SignalError
from newsbot.store import Store
from newsbot.web import create_app
from test_bot import Base, decision


class PersistenceTests(Base, unittest.TestCase):
    def setUp(self):
        self.setup_data()

    def tearDown(self):
        self.cleanup_data()

    def test_config_save_failure_restores_memory(self):
        original = self.config.data["focus"]
        with patch.object(self.config, "save", side_effect=OSError("Disk full")):
            with self.assertRaises(OSError):
                self.config.update(focus="Should not remain active")
        self.assertEqual(self.config.data["focus"], original)

    def test_old_database_migrates_without_losing_items(self):
        path = Path(self.tmp.name) / "old.db"
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, feed_id INTEGER, guid TEXT, url TEXT, title TEXT, description TEXT, published REAL, discovered REAL, status TEXT, summary TEXT, reason TEXT, score INTEGER, model TEXT, rules TEXT, quota TEXT, sent_at REAL, signal_timestamp TEXT)")
            db.execute("INSERT INTO items(id,title,status) VALUES(1,'Preserved history','sent')")
        upgraded = Store(path)
        try:
            row = upgraded.one("SELECT * FROM items WHERE id=1")
            self.assertEqual(row["title"], "Preserved history")
            self.assertEqual(row["provider"], "")
            self.assertEqual(upgraded.db.execute("PRAGMA user_version").fetchone()[0], 1)
        finally:
            upgraded.close()

    def test_future_schema_downgrade_refused(self):
        path = Path(self.tmp.name) / "future.db"
        with sqlite3.connect(path) as db:
            db.execute("PRAGMA user_version=999")
        with self.assertRaises(ValueError):
            Store(path)

    def test_quota_error_does_not_refresh_old_snapshot_time(self):
        self.store.set_model_state("test", quota={"requests": 7})
        old = self.store.model_state("test")["checked"]
        self.store.set_model_state("test", until=time.time() + 60, error="Network error")
        self.assertEqual(self.store.model_state("test")["checked"], old)

    def test_only_one_running_instance_per_data_directory(self):
        with exclusive_run(self.tmp.name):
            with self.assertRaises(SystemExit):
                with exclusive_run(self.tmp.name):
                    self.fail("Second instance acquired lock")
        with exclusive_run(self.tmp.name):
            pass


class IntegrationRegressions(Base, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.setup_data()
        self.session = aiohttp.ClientSession()
        self.pool = ModelPool(self.store, self.config, self.session)
        self.signal = Signal(self.config, self.store, self.session)
        self.engine = Engine(self.store, self.config, self.pool, self.signal)

    async def asyncTearDown(self):
        await self.session.close()
        self.cleanup_data()

    async def test_complete_chunked_json_body_is_read(self):
        async def chunks(request):
            response = web.StreamResponse(headers={"Content-Type": "application/json"})
            await response.prepare(request)
            await response.write(b'{"status":')
            await asyncio.sleep(0.02)
            await response.write(b'"complete"}')
            return response
        app = web.Application()
        app.router.add_get("/", chunks)
        server = TestServer(app)
        await server.start_server()
        try:
            async with self.session.get(server.make_url("/")) as response:
                self.assertEqual(await read_json(response), {"status": "complete"})
            async with self.session.get(server.make_url("/")) as response:
                with self.assertRaises(ValueError):
                    await read_json(response, limit=5)
        finally:
            await server.close()

    async def test_malformed_ai_json_falls_back_instead_of_stalling(self):
        item = self.add_item()
        async def answer(request):
            body = await request.json()
            if body["model"] == "bad":
                return web.json_response([])
            return web.json_response({"choices": [{"message": {"content": json.dumps({"decisions": [decision(item["id"])]})}}]})
        app = web.Application()
        app.router.add_post("/", answer)
        server = TestServer(app)
        await server.start_server()
        self.config.update(models=[{"name": x, "provider": "groq", "model": x, "key": "test"} for x in ("bad", "good")])
        try:
            with patch("newsbot.ai.endpoint", return_value=str(server.make_url("/"))):
                alias, _ = await self.pool.analyze([item], [])
            self.assertEqual(alias, "good")
        finally:
            await server.close()

    async def test_ignored_receipts_do_not_break_command_listener(self):
        self.assertIsNone(self.signal.incoming({"envelope": {"dataMessage": None}}))
        self.assertIsNone(self.signal.incoming({"envelope": {"receiptMessage": {}}}))
        self.assertIsNone(self.signal.incoming([]))

    async def test_sse_graceful_disconnect_updates_connection_state(self):
        async def events(request):
            return web.Response(text=": keepalive\n\n", content_type="text/event-stream")
        app = web.Application()
        app.router.add_get("/api/v1/events", events)
        server = TestServer(app)
        await server.start_server()
        self.config.update(signal_url=str(server.make_url("")).rstrip("/"))
        task = asyncio.create_task(self.signal.listen(self.engine.command))
        try:
            await asyncio.sleep(0.1)
            self.assertFalse(self.signal.connected)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await server.close()

    async def test_partial_or_unconfirmed_signal_result_is_not_success(self):
        with patch.object(self.signal, "rpc", AsyncMock(return_value={"timestamp": 123, "results": [{"type": "NETWORK_FAILURE"}]})):
            with self.assertRaises(SignalError):
                await self.signal.send("News")
        with patch.object(self.signal, "rpc", AsyncMock(return_value={})):
            with self.assertRaises(SignalError):
                await self.signal.send("News")

    async def test_operator_dismissal_during_ai_request_is_preserved(self):
        item = self.add_item()
        async def answer(*args):
            self.store.update_item(item["id"], status="skipped", reason="Operator dismissed")
            return "groq-20b", [decision(item["id"])]
        with patch.object(self.pool, "analyze", answer):
            await self.engine.process()
        self.assertEqual(self.store.one("SELECT status FROM items")["status"], "skipped")

    async def test_mode_change_during_ai_request_cannot_publish_preview_result(self):
        item = self.add_item()
        async def answer(*args):
            self.config.update(mode="live")
            return "groq-20b", [decision(item["id"])]
        with patch.object(self.pool, "analyze", answer):
            await self.engine.process()
        self.assertEqual(self.store.one("SELECT status FROM items")["status"], "queued")

    async def test_original_actual_model_is_preserved_in_post_footer(self):
        self.config.update(mode="live")
        item = self.add_item("outbox")
        self.store.update_item(item["id"], rules=self.config.rules_key(), summary="📰 — News", model="groq-20b", provider="groq", actual_model="original-model")
        self.store.set_model_state("groq-20b", quota={"requests": 10, "actual_model": "later-model"})
        self.signal.connected = True
        with patch.object(self.signal, "send", AsyncMock(return_value={"timestamp": 123})) as send:
            await self.engine.deliver()
            self.assertIn("original-model", send.call_args.args[0])
            self.assertNotIn("later-model", send.call_args.args[0])

    async def test_maintenance_blocks_generation_delivery_and_commands(self):
        (Path(self.tmp.name) / "maintenance").touch()
        self.add_item()
        with patch.object(self.pool, "analyze", AsyncMock()) as analyze:
            await self.engine.process()
            analyze.assert_not_called()
        before = self.config.data["focus"]
        reply = await self.engine.command("/focus changed")
        self.assertIn("Maintenance", reply)
        self.assertEqual(self.config.data["focus"], before)

    async def test_delivery_continues_while_analysis_waits(self):
        self.config.update(mode="live")
        item = self.add_item("outbox", "Ready to deliver")
        self.store.update_item(item["id"], rules=self.config.rules_key(), summary="📰 — News", model="groq-20b")
        self.add_item(title="Waiting for AI")
        self.signal.connected = True
        sent = asyncio.Event()
        async def wait_for_ai(*args):
            await asyncio.Event().wait()
        async def send(*args):
            sent.set()
            return {"timestamp": 123}
        with patch.object(self.pool, "analyze", wait_for_ai), patch.object(self.signal, "send", send):
            task = asyncio.create_task(self.engine.run())
            try:
                await asyncio.wait_for(sent.wait(), 1)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_slow_feed_does_not_block_next_healthy_feed(self):
        for i in range(2, 5):
            self.store.add_feed(f"https://example.com/{i}/rss", f"Feed {i}")
        reached_fourth = asyncio.Event()
        async def poll(feed):
            if feed["id"] in {1, 2}:
                await asyncio.Event().wait()
            self.store.execute("UPDATE feeds SET next_poll=? WHERE id=?", (time.time() + 3600, feed["id"]))
            if feed["id"] == 4:
                reached_fourth.set()
        worker = FeedWorker(self.store, self.config, self.session)
        with patch.object(worker, "poll", poll):
            task = asyncio.create_task(worker.run())
            try:
                await asyncio.wait_for(reached_fourth.wait(), 1)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_failed_feed_visible_and_irrelevant_story_never_posts(self):
        worker = FeedWorker(self.store, self.config, self.session)
        error = aiohttp.ClientResponseError(None, (), status=404)
        with patch("newsbot.feeds.fetch_public", AsyncMock(side_effect=error)):
            await worker.poll(self.feed)
        row = self.store.one("SELECT * FROM feeds")
        self.assertEqual(row["error"], "HTTP 404")
        self.assertGreater(row["next_poll"], time.time())
        item = self.add_item()
        self.config.update(mode="live")
        self.signal.connected = True
        with patch.object(self.pool, "analyze", AsyncMock(return_value=("groq-20b", [decision(item["id"], post=False)]))), patch.object(self.signal, "send", AsyncMock()) as send:
            await self.engine.process()
            await self.engine.deliver()
            send.assert_not_called()
        app = create_app(self.config, self.store, self.pool, self.signal, self.engine)
        client = TestClient(TestServer(app))
        await client.start_server()
        auth = "Basic " + base64.b64encode(("admin:" + self.config.data["dashboard_password"]).encode()).decode()
        try:
            response = await client.get("/", headers={"Authorization": auth})
            html = await response.text()
            self.assertIn("feed(s) need attention", html)
            self.assertIn("HTTP 404", html)
        finally:
            await client.close()

    async def test_switching_provider_does_not_forward_previous_provider_key(self):
        self.config.data["models"][0]["key"] = "groq-secret"
        app = create_app(self.config, self.store, self.pool, self.signal, self.engine)
        client = TestClient(TestServer(app))
        await client.start_server()
        auth = "Basic " + base64.b64encode(("admin:" + self.config.data["dashboard_password"]).encode()).decode()
        try:
            response = await client.post("/model", headers={"Authorization": auth}, data={
                "csrf": self.config.data["csrf"], "name": "groq-20b", "provider": "openrouter",
                "model": "openrouter/free", "key": "", "daily_requests": "50", "enabled": "on",
            }, allow_redirects=False)
            self.assertEqual(response.status, 303)
            changed = next(m for m in self.config.data["models"] if m["name"] == "groq-20b")
            self.assertEqual(changed["key"], "")
            self.assertEqual(response.headers["Cache-Control"], "no-store")
        finally:
            await client.close()
