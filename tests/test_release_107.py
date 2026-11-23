import json
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiohttp
import feedparser
from aiohttp import web
from aiohttp.test_utils import TestServer

from newsbot.ai import ModelOutputError, PoolUnavailable
from newsbot.expansion import TavilySearch, detail_result
from newsbot.feeds import item_images, item_videos, video_candidates
from newsbot.interactions import process_expansion
from newsbot.media import MAX_VIDEO_BYTES, download_mp4
from newsbot.web import create_app
from test_bot import sample_picture
from test_release_105 import NOW, SignalFixture


def box(kind, data):
    return (len(data) + 8).to_bytes(4, "big") + kind + data


def mp4(track=b"vide"):
    # Minimal local container for metadata validation, not a playable client fixture.
    media = box(b"mdia", box(b"hdlr", bytes(8) + track + bytes(12)))
    return box(b"ftyp", b"isom" + bytes(4)) + box(b"mdat", b"frame") + box(b"moov", box(b"trak", media))


class Response:
    def __init__(self, data=b"", mime="video/mp4", status=200, length=None, **headers):
        self.data, self.status, self.content = data, status, self
        self.headers = {"Content-Type": mime, **headers}
        self.content_length = length

    async def iter_chunked(self, _):
        for start in range(0, len(self.data), 13):
            yield self.data[start:start + 13]

    def raise_for_status(self):
        if self.status >= 400:
            raise aiohttp.ClientConnectionError()


class FeedTransport:
    def __init__(self, resources):
        self.resources = resources
        self.requests = []

    @asynccontextmanager
    async def get(self, url, headers, allow_redirects):
        assert not allow_redirects
        self.requests.append(url)
        yield self.resources.get(url, Response(status=404))


class Relay(SignalFixture, unittest.IsolatedAsyncioTestCase):
    def enable_search(self):
        self.config.update(web_expand=True, tavily_key="fixture", models=[
            {"name": "example-editor", "provider": "groq", "model": "test", "key": "fixture"}])

    def poll_nitter(self, *entries):
        rss = '<rss version="2.0"><channel><title>Fixture</title>'
        for stamp, headline, body in entries:
            rss += (f'<item><link>https://social.example/author/status/{stamp}</link><title>{headline}</title>'
                    f'<description><![CDATA[{body}]]></description></item>')
        self.worker.ingest(self.store.one("SELECT * FROM feeds WHERE id=?", (self.feed["id"],)),
                           feedparser.parse(rss + "</channel></rss>"), {}, NOW, "https://social.example/author/rss")

    def video_outbox(self, title, score):
        row = self.add_item(title=title)
        self.store.update_item(row["id"], status="outbox", summary=title, score=score,
                               rules=self.config.rules_key(), model="groq-20b")
        self.store.execute("UPDATE items SET video_urls=? WHERE id=?",
                           ('["https://example.net/video.mp4"]', row["id"]))
        return self.get(row)

    async def test_video_score_and_absent_media_degrade_to_one_text_send(self):
        self.assertIn("75/100", await self.command("/videos on"))
        self.assertIn("85/100", await self.command("/videos min 85"))
        self.assertIn("⚠️", await self.command("/videos min 101"))
        low = self.video_outbox("A regional bank opens emergency credit", 84)
        equal = self.video_outbox("The western ferry terminal reopens", 85)
        self.worker.session = FeedTransport({"https://example.net/video.mp4": Response(b"<html/>")})
        with patch.object(self.signal, "rpc", AsyncMock(return_value={"timestamp": NOW * 1000})) as rpc:
            await self.engine.deliver()
            await self.engine.deliver()
        self.assertEqual(self.worker.session.requests, ["https://example.net/video.mp4"])
        self.assertEqual(rpc.await_count, 2)
        self.assertTrue(all("attachment" not in call.args[1] for call in rpc.await_args_list))
        self.assertFalse(json.loads(self.get(low)["media_result"])["video_attempted"])
        self.assertTrue(json.loads(self.get(equal)["media_result"])["video_rejected"])

    async def test_nitter_resolves_own_download_and_sends_it_on_instant_route(self):
        await self.command(f"/feed instant {self.feed['id']} on")
        await self.command("/videos on")
        self.poll_nitter(("1", "Prior fixture item", ""))
        self.poll_nitter(("42", "A storm closes the terminal", '<a href="/author/status/42#m"><br>Video<br>'
                          '<img src="/pic/thumb.jpg"></a>'))
        page = "https://social.example/author/status/42"
        clip = "https://social.example/video/fixture/clip"
        html = b"""<div class="main-tweet"><div class="tweet-body"><div class="attachments">
          <a class="video-download" href="/video/fixture/clip">Download</a>
          <div class="quote"><div class="attachments"><video src="/unrelated.mp4"></video></div></div>
          </div></div></div><video src="/reply.mp4"></video>"""
        self.worker.session = FeedTransport({page: Response(html, "text/html"), clip: Response(mp4())})
        row = self.store.one("SELECT * FROM items WHERE url=?", (page,))
        self.assertEqual(item_videos(row), ([], page))
        self.assertEqual(item_images(row), [])
        paths = []

        async def send(method, params):
            self.assertEqual(method, "send")
            self.assertEqual(len(params["attachment"]), 1)
            path = Path(params["attachment"][0])
            paths.append(path)
            self.assertEqual(path.read_bytes(), mp4())
            self.assertEqual(path.parent.parent, self.config.path.parent)
            self.assertIn("🔗 " + page, params["message"])
            return {"timestamp": NOW * 1000}

        with patch.object(self.signal, "rpc", AsyncMock(side_effect=send)) as rpc:
            await self.engine.deliver()
        rpc.assert_awaited_once()
        self.assertFalse(paths[0].exists())
        self.assertEqual(self.worker.session.requests, [page, clip])
        self.assertTrue(json.loads(self.get(row)["media_result"])["video_attached"])

    async def test_nitter_gallery_survives_ingest_and_rejects_variant_and_other_story(self):
        await self.command(f"/feed instant {self.feed['id']} on")
        self.config.update(include_images=True)
        self.poll_nitter(("1", "Baseline", ""))
        self.poll_nitter(("2", "Restoration crews reach the west coast", '<img src="/pic/a.jpg"><img src="/pic/a-large.jpg">'
                          '<a href="/another/status/3"><img src="/pic/card.jpg"></a>'
                          '<blockquote><img src="/pic/quote.jpg"></blockquote><img src="/pic/b.png">'))
        expected = ["https://social.example/pic/" + name for name in ("a.jpg", "a-large.jpg", "b.png")]
        row = self.store.one("SELECT * FROM items WHERE status='outbox'")
        self.assertEqual(item_images(row), expected)
        a, same, b = sample_picture("red"), sample_picture("red", (48, 48), "JPEG"), sample_picture("blue")
        with patch("newsbot.engine.fetch_image", AsyncMock(side_effect=[a, same, b])) as fetch, patch.object(
                self.signal, "rpc", AsyncMock(return_value={"timestamp": NOW * 1000})) as rpc:
            await self.engine.deliver()
        self.assertEqual([call.args[1] for call in fetch.await_args_list], expected)
        self.assertEqual(rpc.await_args.args[1]["attachment"], [a, b])
        self.assertEqual(json.loads(self.get(row)["media_result"])["photos_rejected"], 1)
        self.assertEqual(json.loads(self.get(row)["media_result"])["photos_attached"], 2)

    async def test_video_enclosures_atom_and_inline_and_no_unsafe_redirect(self):
        feed = """<feed xmlns="http://www.w3.org/2005/Atom">
          <entry><link rel="enclosure" type="video/mp4" href="/one"/><link rel="enclosure" type="audio/mp4" href="/audio.mp4"/>
          <link rel="enclosure" type="video/mp4" href="http://127.0.0.1/unsafe.mp4"/></entry></feed>"""
        self.assertEqual(video_candidates(feedparser.parse(feed).entries[0], "https://example.com/news"),
                         ["https://example.com/one"])
        self.poll_nitter(("1", "News about the river", '<video poster="/pic/preview.jpg">'
                          '<source src="/media/example.mp4" type="video/mp4"></video>'))
        row = self.store.one("SELECT * FROM items ORDER BY id DESC LIMIT 1")
        self.assertEqual(item_videos(row)[0], ["https://social.example/media/example.mp4"])
        url = "https://example.com/clip.mp4"
        path = self.config.path.parent / "test-clip.mp4"
        for response in (Response(mp4(b"soun")), Response(mp4(), length=MAX_VIDEO_BYTES + 1),
                         Response(mp4(), "text/html"), Response(mp4()[:-2]),
                         Response(status=302, Location="http://169.254.169.254/clip.mp4")):
            session = FeedTransport({url: response})
            self.assertFalse(await download_mp4(session, url, path))
            self.assertEqual(session.requests, [url])

    async def test_video_fetch_race_never_sends_or_leaves_file_after_pause(self):
        self.config.update(include_videos=True)
        row = self.video_outbox("Regional ferries return to service", 90)
        paths = []

        async def pause(session, urls, page, article, path):
            path.write_bytes(mp4())
            paths.append(path)
            self.config.update(paused=True)
            return True

        with patch("newsbot.engine.prepare_video", side_effect=pause), patch.object(self.signal, "rpc", AsyncMock()) as rpc:
            await self.engine.deliver()
            rpc.assert_not_awaited()
        self.assertEqual(self.get(row)["status"], "outbox")
        self.assertFalse(paths[0].exists())

    async def test_rewrites_skip_but_new_order_survives_in_normal_and_instant_delivery(self):
        title = "Harbor commission grants Westport ferry terminal construction permit"
        echo = "Officials say Harbor commission granted Westport ferry terminal construction permit"
        new = "Harbor commission grants second Westport ferry terminal construction permit"
        sent = self.posted(title)
        content = "The authorization covers the east dock, passenger access and the new berth."
        self.store.execute("UPDATE items SET description=? WHERE id=?", (content, sent["id"]))
        normal = self.add_item(title=echo)
        self.store.execute("UPDATE items SET description=? WHERE id=?", (content, normal["id"]))
        with patch.object(self.pool, "analyze", AsyncMock()) as analyze:
            await self.engine.process()
        analyze.assert_not_awaited()
        self.assertIn(f"#{sent['id']}", self.get(normal)["reason"])

        await self.command(f"/feed instant {self.feed['id']} on")
        self.ingest(("base", "Baseline only", ""))
        self.ingest(("instant-echo", echo, f"<description>{content}</description>"),
                    ("instant-update", new, "<description>The authorization covers a separate west dock berth.</description>"))
        with patch.object(self.signal, "rpc", AsyncMock(return_value={"timestamp": NOW * 1000})) as rpc:
            await self.engine.deliver()
            await self.engine.deliver()
        rpc.assert_awaited_once()
        self.assertIn(new, rpc.await_args.args[1]["message"])
        self.assertIn("🔗 https://example.com/news/instant-update", rpc.await_args.args[1]["message"])

    async def test_expand_dedupes_reaction_and_command_and_cites_one_inference(self):
        self.enable_search()
        self.engine.search = TavilySearch(self.session)
        post = self.posted("Westport ferry terminal construction approved")
        self.store.execute("UPDATE items SET description='' WHERE id=?", (post["id"],))
        stamp = int(post["signal_timestamp"])
        calls = []

        async def search(request):
            payload = await request.json()
            calls.append("search")
            self.assertEqual(payload["query"], post["title"])
            self.assertFalse(payload["include_answer"] or payload["auto_parameters"])
            return web.json_response({"results": [
                {"title": "Westport ferry terminal plan", "url": "https://example.org/report?utm_id=ignored",
                 "content": "Westport ferry terminal will include a covered waiting area for passengers."},
                {"title": "Westport ferry terminal plan", "url": "http://127.0.0.1/ignore",
                 "content": "Westport ferry terminal will include an ice rink."}]})

        async def model(request):
            calls.append("model")
            payload = json.loads((await request.json())["messages"][1]["content"])
            self.assertEqual(list(payload), ["headline", "sources"])
            self.assertEqual(len(payload["sources"]), 1)
            self.assertNotIn("url", payload["sources"][0])
            return web.json_response({"choices": [{"message": {"content": json.dumps({
                "text": "The plan includes a covered waiting area for passengers.", "sources": [1]})}}]})

        app = web.Application()
        app.router.add_post("/search", search)
        app.router.add_post("/model", model)
        async with TestServer(app) as server:
            with patch("newsbot.expansion.SEARCH_URL", str(server.make_url("/search"))), patch(
                    "newsbot.ai.endpoint", return_value=str(server.make_url("/model"))), patch.object(
                    self.signal, "rpc", AsyncMock(return_value={"timestamp": NOW * 1000})) as rpc:
                await self.react("reader", stamp, "🔎")
                result = await self.engine.command("/expand", "operator", {
                    "group": self.group, "quote": {"id": stamp, "authorUuid": "test-bot-aci"}})
                self.assertIn("No new", result)
                self.assertEqual(self.engine.expansions.qsize(), 1)
                await process_expansion(self.engine, *self.engine.expansions.get_nowait())
        self.assertEqual(calls, ["search", "model"])
        rpc.assert_awaited_once()
        params = rpc.await_args.args[1]
        self.assertEqual(params["quoteTimestamp"], stamp)
        self.assertIn("https://example.org/report", params["message"])
        self.assertNotIn("utm_id", params["message"])
        self.assertEqual(self.store.rows("SELECT requests FROM expansion_search"), [{"requests": 1}])

    async def test_no_model_retry_and_citation_validation(self):
        self.config.update(models=[{"name": f"m{n}", "provider": "groq", "model": "test", "key": "fixture"} for n in range(2)])
        app = web.Application()

        async def rate_limited(_):
            return web.json_response({}, status=429)

        app.router.add_post("/", rate_limited)
        async with TestServer(app) as server:
            with patch("newsbot.ai.endpoint", return_value=str(server.make_url("/"))):
                with self.assertRaises(PoolUnavailable):
                    await self.pool.expand("Example headline", [], lambda value: value)
        self.assertEqual(self.store.usage("m0")["expand"], 1)
        self.assertEqual(self.store.usage("m1")["requests"], 0)
        with self.assertRaises(ModelOutputError):
            detail_result('{"text":"Waited 29 hours for a refund.","sources":[4]}', "Ticket refunds", [])

    async def test_expansion_saved_details_win_and_group_change_stops_search_reply(self):
        self.enable_search()
        self.engine.search = AsyncMock()
        local = self.posted("A bank pauses withdrawals")
        self.store.execute("UPDATE items SET description=? WHERE id=?",
                           ("A bank pauses withdrawals. Managers say debit card payments still work.", local["id"]))
        with patch.object(self.signal, "rpc", AsyncMock(return_value={"timestamp": NOW * 1000})) as rpc:
            await self.react("local-reader", int(local["signal_timestamp"]), "🔎")
            self.engine.search.search.assert_not_awaited()
            rpc.assert_awaited_once()
        empty = self.posted("Westport ferry terminal plan")
        self.store.execute("UPDATE items SET description='' WHERE id=?", (empty["id"],))
        await self.react("web-reader", int(empty["signal_timestamp"]), "🔎")

        async def switch(*_):
            self.config.update(group_id="different-group")
            return [{"id": 1, "title": "Terminal plan", "snippet": "Covered waiting area.", "url": "https://example.org/news"}]

        self.engine.search.search.side_effect = switch
        with patch.object(self.signal, "rpc", AsyncMock()) as rpc, patch.object(self.pool, "expand", AsyncMock()) as model:
            await process_expansion(self.engine, *self.engine.expansions.get_nowait())
            rpc.assert_not_awaited()
            model.assert_not_awaited()

    async def test_dmtest_targets_only_event_sender_never_other_groups_or_web(self):
        sender = "7a67c345-b595-4b70-bc5d-790ae2370fa2"

        def event(body, group, offset=0):
            payload = {"message": body}
            if group:
                payload["groupInfo"] = {"groupId": group}
            return {"account": "test-bot", "envelope": {"sourceUuid": sender,
                    "timestamp": NOW * 1000 + offset, "dataMessage": payload}}

        self.assertIsNone(self.signal.incoming(event("/dmtest", "another-group")))
        self.assertIsNone(self.signal.incoming(event("/dmtest", "")))
        self.assertIsNone(await self.engine.command("/dmtest"))
        one = self.signal.incoming(event("/dmtest", self.group))
        self.assertIsNone(self.signal.incoming(event("/dmtest", self.group)))
        self.config.update(mode="preview")
        with patch.object(self.signal, "rpc", AsyncMock(return_value={"timestamp": NOW * 1000})) as rpc:
            self.assertIsNone(await self.engine.command(*one))
            self.clock.return_value += 5
            self.assertIn("without a recipient", await self.engine.command(
                *self.signal.incoming(event("/dmtest someone-else", self.group, 1))))
            rpc.assert_awaited_once()
        self.assertEqual(rpc.await_args.args, ("send", {"recipient": [sender],
                              "message": "✅ Signal News Bot direct message test."}))

    async def test_search_settings_are_csrf_protected_and_do_not_render_the_key(self):
        app = create_app(self.config, self.store, self.pool, self.signal, self.engine)
        auth = aiohttp.BasicAuth("admin", self.config.data["dashboard_password"])
        form = {"tavily_key": "fixture-never-render-this", "web_expand": "on"}
        async with TestServer(app) as server:
            url = server.make_url("/search")
            async with self.session.post(url, data=form, auth=auth) as response:
                self.assertEqual(response.status, 403)
            form["csrf"] = self.config.data["csrf"]
            async with self.session.post(url, data=form, auth=auth) as response:
                self.assertEqual(response.status, 200)
                page = await response.text()
                self.assertIn("saved; blank keeps it", page)
                self.assertNotIn(form["tavily_key"], page)
            async with self.session.post(url, data={"clear": "1", "csrf": form["csrf"]}, auth=auth) as response:
                self.assertEqual(response.status, 200)
        self.assertFalse(self.config.data["web_expand"])
        self.assertEqual(self.config.data["tavily_key"], "")

    def test_search_quota_is_persistent_and_does_not_carry_over_months(self):
        # Jan 1 UTC followed by dates long before the app's normal retention.
        jan = 1767225600
        for day in range(16):
            for _ in range(12):
                self.assertTrue(self.store.reserve_search(jan + day * 86400))
            self.assertFalse(self.store.reserve_search(jan + day * 86400))
        for _ in range(8):
            self.assertTrue(self.store.reserve_search(jan + 16 * 86400))
        self.assertFalse(self.store.reserve_search(jan + 16 * 86400))
        self.assertTrue(self.store.reserve_search(jan + 31 * 86400))
