import asyncio
import base64
import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image

from newsbot.ai import ModelPool, PoolUnavailable, parse_decisions
from newsbot.config import Config, validate_models
from newsbot.engine import Engine
from newsbot.feeds import FeedWorker, PublicResolver, canonical_url, parse_opml, public_url
from newsbot.signal import Signal
from newsbot.store import Store
from newsbot.web import create_app


class Base:
    def setup_data(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = Config(Path(self.tmp.name) / "config.json")
        self.store = Store(Path(self.tmp.name) / "news.db")
        self.config.update(focus="Major cyber incidents and outages", group_id=base64.b64encode(b"g" * 32).decode())
        self.store.add_feed("https://example.com/rss", "Example")
        self.feed = self.store.one("SELECT * FROM feeds")

    def add_item(self, state="queued", title="Statewide outage affects licensing systems"):
        cur = self.store.execute("INSERT INTO items(feed_id,guid,url,title,description,published,discovered,status) VALUES(?,?,?,?,?,?,?,?)",
             (self.feed["id"], title, "https://example.com/" + str(time.time_ns()), title,
              "Officials report a statewide outage affecting driver licensing and vehicle registration systems.",
              time.time(), time.time(), state))
        return self.store.one("SELECT * FROM items WHERE id=?", (cur.lastrowid,))

    def cleanup_data(self):
        self.store.close()
        self.tmp.cleanup()


def decision(item_id, **kwargs):
    data = {"id": item_id, "post": True, "importance": 90, "text": "Officials report a statewide outage affecting driver licensing and vehicle registration systems.", "emoji": "🇺🇸🚗", "reason": "Major public service disruption",
            "on_focus": True, "excluded": False, "duplicate": False, "complex": False}
    data.update(kwargs)
    return data


def sample_picture(color="blue", size=(32, 32), fmt="PNG"):
    with Image.new("RGB", size, color) as sample, io.BytesIO() as buffer:
        sample.save(buffer, fmt)
        return f"data:image/{fmt.lower()};base64," + base64.b64encode(buffer.getvalue()).decode()


class CoreTests(Base, unittest.TestCase):
    def setUp(self):
        self.setup_data()

    def tearDown(self):
        self.cleanup_data()

    def test_config_private_and_persistent(self):
        self.assertEqual(self.config.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(Config(self.config.path).data["focus"], self.config.data["focus"])

    def test_opml_nested_import_and_url_security(self):
        xml = b'<opml><body><outline text="Folder"><outline xmlUrl="https://example.com/rss" text="News"/><outline xmlUrl="https://example.com/rss" text="News"/></outline></body></opml>'
        self.assertEqual(parse_opml(xml), [("https://example.com/rss", "News")])
        for url in ("http://127.0.0.1/rss", "http://[::1]/", "http://169.254.169.254/", "file:///etc/passwd", "https://user:password@example.com/rss", "https://example.com:8080/rss", "http://localhost/"):
            self.assertFalse(public_url(url), url)
        with self.assertRaises(ValueError):
            parse_opml(b'<opml><outline xmlUrl="http://127.0.0.1/"/></opml>')

    def test_xml_entities_blocked(self):
        with self.assertRaises(Exception):
            parse_opml(b'<!DOCTYPE x [<!ENTITY a SYSTEM "file:///etc/passwd">]><opml><outline text="&a;"/></opml>')

    def test_url_tracking_removed(self):
        self.assertEqual(canonical_url("https://EXAMPLE.com/story?a=1&utm_source=x&fbclid=y#top"), "https://example.com/story?a=1")

    def test_model_paid_ids_rejected(self):
        model = {"name": "test", "provider": "openrouter", "model": "paid/model"}
        with self.assertRaises(ValueError):
            validate_models([model])
        model["model"] = "openrouter/free"
        validate_models([model])
        model.update(provider="cloudflare", model="@cf/unreviewed/model")
        with self.assertRaises(ValueError):
            validate_models([model])

    def test_model_response_requires_exact_ids(self):
        item = self.add_item()
        parsed = parse_decisions(json.dumps({"decisions": [decision(item["id"])]}), [item])
        self.assertTrue(parsed[0]["post"])
        with self.assertRaises(ValueError):
            parse_decisions(json.dumps({"decisions": [decision(999)]}), [item])
        with self.assertRaises(ValueError):
            parse_decisions(json.dumps({"decisions": [decision(item["id"], post="true")]}), [item])

    def test_usage_attempts_and_tokens(self):
        self.store.count_usage("a")
        self.store.count_usage("a", 120, request=False)
        self.assertEqual(self.store.usage("a")["requests"], 1)
        self.assertEqual(self.store.usage("a")["tokens"], 120)

    def test_restart_recovers_only_inflight_sends(self):
        item = self.add_item("sending")
        second = Store(Path(self.tmp.name) / "news.db")
        self.assertEqual(second.one("SELECT status FROM items WHERE id=?", (item["id"],))["status"], "sending")
        second.close()
        second = Store(Path(self.tmp.name) / "news.db", recover=True)
        self.assertEqual(second.one("SELECT status FROM items WHERE id=?", (item["id"],))["status"], "uncertain")
        second.close()


class AsyncTests(Base, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.setup_data()
        self.session = aiohttp.ClientSession()
        self.pool = ModelPool(self.store, self.config, self.session)
        self.signal = Signal(self.config, self.store, self.session)
        self.engine = Engine(self.store, self.config, self.pool, self.signal)

    async def asyncTearDown(self):
        await self.session.close()
        self.cleanup_data()

    async def test_group_commands_focus_and_model(self):
        await self.engine.command("/focus Only major infrastructure outages")
        self.assertEqual(self.config.data["focus"], "Only major infrastructure outages")
        await self.engine.command("/model groq-120b")
        self.assertEqual(self.config.data["preferred_model"], "groq-120b")
        await self.engine.command("/fallback off")
        self.config.data["models"][0]["key"] = "one-provider-key"
        candidates = self.pool.candidates()
        self.assertEqual([m["name"] for m in candidates], ["groq-120b"])
        self.assertEqual(candidates[0]["key"], "one-provider-key")

    async def test_group_scope_replay_and_stale_protection(self):
        envelope = {"sourceUuid": "member-1", "timestamp": int(time.time() * 1000),
                    "dataMessage": {"message": "/focus outages", "groupInfo": {"groupId": self.config.data["group_id"]}}}
        packet = {"params": {"envelope": envelope}}
        self.assertEqual(self.signal.incoming(packet)[0], "/focus outages")
        self.assertIsNone(self.signal.incoming(packet))
        envelope["timestamp"] += 1
        envelope["dataMessage"]["groupInfo"]["groupId"] = "other-group"
        self.assertIsNone(self.signal.incoming(packet))
        envelope["dataMessage"]["groupInfo"]["groupId"] = self.config.data["group_id"]
        envelope["timestamp"] = int((time.time() - 1000) * 1000)
        self.assertIsNone(self.signal.incoming(packet))

    async def test_preview_never_posts(self):
        item = self.add_item()
        with patch.object(self.pool, "analyze", AsyncMock(return_value=("groq-20b", [decision(item["id"])]))), \
             patch.object(self.signal, "send", AsyncMock()) as send:
            await self.engine.process()
            await self.engine.deliver()
            send.assert_not_called()
        self.assertEqual(self.store.one("SELECT status FROM items")["status"], "preview")

    async def test_live_has_quota_footer(self):
        item = self.add_item()
        self.config.update(mode="live")
        self.signal.connected = True
        with patch.object(self.pool, "analyze", AsyncMock(return_value=("groq-20b", [decision(item["id"])]))), \
             patch.object(self.signal, "send", AsyncMock(return_value={"timestamp": 123})) as send:
            await self.engine.process()
            await self.engine.deliver()
            message = send.call_args.args[0]
            self.assertIn("🇺🇸🚗 —", message)
            self.assertIn("900/900 calls", message)
            self.assertNotIn(" local", message)
            self.assertIn("Groq · GPT-OSS 20B · configured", message)
            self.assertNotIn("provider quota", message)
        self.assertEqual(self.store.one("SELECT status FROM items")["status"], "sent")

    async def test_signal_ambiguous_failure_not_retried(self):
        item = self.add_item("outbox")
        self.store.update_item(item["id"], rules=self.config.rules_key(), summary="📰 — News", model="groq-20b")
        self.config.update(mode="live")
        self.signal.connected = True
        with patch.object(self.signal, "send", AsyncMock(side_effect=asyncio.TimeoutError())) as send:
            await self.engine.deliver()
            await self.engine.deliver()
            self.assertEqual(send.call_count, 1)
        self.assertEqual(self.store.one("SELECT status FROM items")["status"], "uncertain")

    async def test_focus_change_during_analysis_discards_result(self):
        item = self.add_item()
        async def changed(*args):
            self.config.update(focus="A different situation")
            return "groq-20b", [decision(item["id"])]
        with patch.object(self.pool, "analyze", changed):
            await self.engine.process()
        self.assertEqual(self.store.one("SELECT status FROM items")["status"], "queued")

    async def test_existing_outbox_rechecked_after_focus_change(self):
        item = self.add_item("outbox")
        self.store.update_item(item["id"], rules="old focus")
        self.config.update(mode="live")
        self.signal.connected = True
        with patch.object(self.signal, "send", AsyncMock()) as send:
            await self.engine.deliver()
            send.assert_not_called()
        self.assertEqual(self.store.one("SELECT status FROM items")["status"], "queued")

    async def test_pool_exhaustion_keeps_queue(self):
        self.add_item()
        await self.engine.process()
        self.assertIn("queued", self.engine.pool_error)
        self.assertEqual(self.store.one("SELECT status FROM items")["status"], "queued")

    async def test_nvidia_only_eligible_for_preview(self):
        self.config.update(models=[{"name": "nv", "provider": "nvidia", "model": "openai/gpt-oss-20b", "key": "test", "enabled": True, "daily_requests": 10}])
        self.assertEqual([m["name"] for m in self.pool.candidates()], ["nv"])
        self.config.update(mode="live")
        self.assertEqual(self.pool.candidates(), [])

    async def test_nvidia_approved_preview_never_publishes(self):
        self.config.update(models=[{"name": "nv", "provider": "nvidia", "model": "openai/gpt-oss-20b", "key": "test", "enabled": True, "daily_requests": 10}], mode="live")
        item = self.add_item("outbox")
        self.store.update_item(item["id"], rules=self.config.rules_key(), summary="📰 — News", model="nv")
        self.signal.connected = True
        with patch.object(self.signal, "send", AsyncMock()) as send:
            await self.engine.deliver()
            send.assert_not_called()
        self.assertEqual(self.store.one("SELECT status FROM items")["status"], "preview")

    async def test_preview_not_counted_as_published_in_live_mode(self):
        old = self.add_item("preview", "Private preview story")
        self.store.update_item(old["id"], summary="This preview was never published.")
        new = self.add_item()
        self.config.update(mode="live")
        with patch.object(self.pool, "analyze", AsyncMock(return_value=("groq-20b", [decision(new["id"])]))) as analyze:
            await self.engine.process()
            self.assertEqual(analyze.call_args.args[1], [])

    async def test_old_queue_expires_without_ai_call(self):
        item = self.add_item()
        self.store.execute("UPDATE items SET published=? WHERE id=?", (time.time() - 86400, item["id"]))
        with patch.object(self.pool, "analyze", AsyncMock()) as analyze:
            await self.engine.process()
            analyze.assert_not_called()
        self.assertEqual(self.store.one("SELECT status FROM items")["status"], "expired")

    async def test_model_local_attempt_cap_is_enforced(self):
        self.config.update(models=[{"name": "test", "provider": "groq", "model": "openai/gpt-oss-20b", "key": "test", "enabled": True, "daily_requests": 1}])
        self.store.count_usage("test")
        with patch.object(self.session, "post") as post:
            with self.assertRaises(PoolUnavailable):
                await self.pool.analyze([self.add_item()], [])
            post.assert_not_called()

    async def test_sambanova_daily_header_not_confused_with_minute(self):
        model = {"provider": "sambanova"}
        quota = await self.pool.reported_quota(model, {"x-ratelimit-remaining-requests-day": "18", "x-ratelimit-remaining-requests": "3"})
        self.assertEqual(quota["requests"], 18)

    async def test_feed_first_poll_is_baseline_then_new_items_queue(self):
        rss = b'<rss version="2.0"><channel><title>Test</title><item><guid>1</guid><title>Initial story</title><link>https://example.com/1</link><description>Test</description></item></channel></rss>'
        worker = FeedWorker(self.store, self.config, self.session)
        with patch("newsbot.feeds.fetch_public", AsyncMock(return_value=(200, rss, {"etag": "test"}, "https://example.com/rss"))):
            await worker.poll(self.feed)
        self.assertEqual(self.store.one("SELECT status FROM items")["status"], "baseline")
        feed = self.store.one("SELECT * FROM feeds")
        rss = rss.replace(b"<guid>1", b"<guid>2").replace(b"/1</link>", b"/2</link>")
        with patch("newsbot.feeds.fetch_public", AsyncMock(return_value=(200, rss, {}, "https://example.com/rss"))):
            await worker.poll(feed)
        self.assertEqual(self.store.one("SELECT status FROM items ORDER BY id DESC LIMIT 1")["status"], "queued")

    async def test_private_dns_addresses_rejected(self):
        resolver = PublicResolver()
        with patch.object(resolver.resolver, "resolve", AsyncMock(return_value=[{"host": "127.0.0.1"}])):
            with self.assertRaises(OSError):
                await resolver.resolve("public-looking.example", 443)
        await resolver.close()

    async def test_ai_http_fallback_and_reported_quota(self):
        item = self.add_item()
        attempts = []
        async def respond(request):
            body = await request.json()
            attempts.append(body["model"])
            if body["model"] == "broken":
                return web.json_response({"error": "limited"}, status=429, headers={"retry-after": "60"})
            return web.json_response({"choices": [{"message": {"content": json.dumps({"decisions": [decision(item["id"])]})}}],
                    "usage": {"total_tokens": 123}, "model": "working"},
                    headers={"x-ratelimit-remaining-requests": "998", "x-ratelimit-remaining-tokens": "7000"})
        app = web.Application()
        app.router.add_post("/ai", respond)
        server = TestServer(app)
        await server.start_server()
        self.config.update(models=[{"name": n, "provider": "groq", "model": m, "key": "test", "enabled": True, "daily_requests": 10} for n, m in [("a", "broken"), ("b", "working")]])
        try:
            with patch("newsbot.ai.endpoint", return_value=str(server.make_url("/ai"))):
                name, decisions = await self.pool.analyze([item], [])
            self.assertEqual(name, "b")
            self.assertEqual(attempts, ["broken", "working"])
            self.assertEqual(self.store.usage("b")["tokens"], 123)
            self.assertIn("998 requests left", self.pool.footer("b"))
            self.assertIn("7000 tokens left/min", self.pool.footer("b"))
            self.assertGreater(self.store.model_state("a")["until"], time.time())
        finally:
            await server.close()

    async def test_web_auth_csrf_secret_redaction_and_xss(self):
        self.config.data["models"][0]["key"] = "SUPER-SECRET-API-KEY"
        self.config.update(focus="<script>alert('x')</script>")
        app = create_app(self.config, self.store, self.pool, self.signal, self.engine)
        client = TestClient(TestServer(app))
        await client.start_server()
        auth = {"Authorization": "Basic " + base64.b64encode(("admin:" + self.config.data["dashboard_password"]).encode()).decode()}
        try:
            response = await client.get("/")
            self.assertEqual(response.status, 401)
            response = await client.get("/", headers=auth)
            self.assertEqual(response.status, 200)
            html = await response.text()
            self.assertNotIn("SUPER-SECRET-API-KEY", html)
            self.assertNotIn("<script>alert", html)
            self.assertIn("&lt;script&gt;", html)
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            response = await client.post("/action", headers=auth, data={"action": "sample"})
            self.assertEqual(response.status, 403)
            response = await client.post("/action", headers=auth, data={"action": "sample", "csrf": self.config.data["csrf"]}, allow_redirects=False)
            self.assertEqual(response.status, 303)
        finally:
            await client.close()

    async def test_signal_sse_command_and_rpc_reply(self):
        received = asyncio.Event()
        replies = []
        packet = {"envelope": {"sourceUuid": "trusted-member", "timestamp": int(time.time() * 1000),
            "dataMessage": {"message": "/focus Significant outages only", "groupInfo": {"groupId": self.config.data["group_id"]}}}}

        async def events(request):
            response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            await response.write(("id: event-1\ndata: " + json.dumps(packet) + "\n\n").encode())
            await received.wait()
            return response

        async def rpc(request):
            body = await request.json()
            if body["method"] == "listAccounts":
                return web.json_response({"jsonrpc": "2.0", "id": body["id"], "result": [{"number": "+15555550123", "aci": "test"}]})
            replies.append(body)
            received.set()
            return web.json_response({"jsonrpc": "2.0", "id": body["id"], "result": {"timestamp": 123}})

        app = web.Application()
        app.router.add_get("/api/v1/events", events)
        app.router.add_post("/api/v1/rpc", rpc)
        server = TestServer(app)
        await server.start_server()
        self.config.update(signal_url=str(server.make_url("")).rstrip("/"))
        task = asyncio.create_task(self.signal.listen(self.engine.command))
        try:
            await asyncio.wait_for(received.wait(), timeout=3)
            self.assertEqual(self.config.data["focus"], "Significant outages only")
            self.assertEqual(replies[0]["method"], "send")
            self.assertEqual(replies[0]["params"]["account"], "+15555550123")
            self.assertEqual(replies[0]["params"]["groupId"], self.config.data["group_id"])
            self.assertIn("✅ Focus saved", replies[0]["params"]["message"])
            self.assertTrue(replies[0]["params"]["textStyle"])
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await server.close()


if __name__ == "__main__":
    unittest.main()
