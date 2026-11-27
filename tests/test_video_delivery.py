import json
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import feedparser

from newsbot.feeds import item_videos
from test_release_105 import NOW, SignalFixture
from test_release_107 import FeedTransport, Response, mp4


class VideoDelivery(SignalFixture, unittest.IsolatedAsyncioTestCase):
    def ingest_nitter(self, page, title, description):
        rss = f"""<rss version="2.0"><channel><title>Fixture</title>
            <item><title>{title}</title><link>{page}</link>
            <description><![CDATA[{description}]]></description></item></channel></rss>"""
        feed = self.store.one("SELECT * FROM feeds WHERE id=?", (self.feed["id"],))
        self.worker.ingest(feed, feedparser.parse(rss), {}, NOW, "https://social.example/author/rss")
        return self.store.one("SELECT * FROM items WHERE url=?", (page,))

    async def test_inline_mp4_respects_toggle_and_original_score(self):
        self.ingest(("baseline", "Older edition", ""))
        clip_type = 'Video/MP4; codecs="avc1.42001E"'
        entries = (
            ("off", "Local train fares rise", 99),
            ("below", "Budget vote postponed", 74),
            ("eligible", "Storm damage closes airport", 75),
        )
        for slug, title, score in entries:
            self.ingest((slug, title, f"""<description>Short teaser</description>
                <content:encoded xmlns:content="http://purl.org/rss/1.0/modules/content/"><![CDATA[
                    <video><source src="/hls.mp4" type="application/vnd.apple.mpegurl">
                           <source src="/media/{slug}" type='{clip_type}'></video>
                    <blockquote><video src="/unrelated.mp4"></video></blockquote>
                ]]></content:encoded>"""))
            row = self.store.one("SELECT * FROM items WHERE guid=?", (slug,))
            self.store.update_item(row["id"], status="outbox", summary=title, score=score,
                                   rules=self.config.rules_key(), model="groq-20b")

        clip = "https://example.com/media/eligible"
        self.worker.session = FeedTransport({clip: Response(mp4())})
        calls = []

        async def send(method, params):
            self.assertEqual(method, "send")
            attachments = params.get("attachment", [])
            if attachments:
                self.assertEqual(len(attachments), 1)
                path = Path(attachments[0])
                self.assertEqual(path.read_bytes(), mp4())
            calls.append(bool(attachments))
            return {"timestamp": NOW * 1000}

        self.assertFalse(self.config.data["include_videos"])
        with patch.object(self.signal, "rpc", AsyncMock(side_effect=send)):
            await self.engine.deliver()
            self.assertEqual(self.worker.session.requests, [])
            await self.command("/videos on")
            await self.engine.deliver()
            self.assertEqual(self.worker.session.requests, [])
            await self.engine.deliver()
        self.assertEqual(calls, [False, False, True])
        self.assertEqual(self.worker.session.requests, [clip])
        for slug, _, score in entries:
            row = self.store.one("SELECT * FROM items WHERE guid=?", (slug,))
            self.assertEqual(item_videos(row), ([f"https://example.com/media/{slug}"], ""))
            result = json.loads(row["media_result"])
            attached = slug == "eligible"
            self.assertEqual(result["video_attempted"], attached)
            self.assertEqual(result["video_attached"], attached)
            self.assertEqual(row["score"], score)

    async def test_nitter_uses_only_own_status_attachment(self):
        await self.command(f"/feed instant {self.feed['id']} on")
        await self.command("/videos on")
        self.ingest(("baseline", "Older edition", ""))
        page = "https://social.example/author/status/42"
        clip = "https://social.example/video/proxy-owned"
        row = self.ingest_nitter(page, "Ferry rescues boat crew", """
            <a href="/author/status/42#m"><img src="/pic/ext_tw_video_thumb%2F42.jpg"></a>
            <a href="/reader/status/43#m"><img src="/pic/tweet_video_thumb%2F43.jpg"></a>
        """)
        html = b"""<div class="main-tweet"><div class="tweet-body">
            <div class="quote"><div class="attachments"><video src="/quote.mp4"></video></div></div>
            <div class="attachments"><video>
                <source src="http://127.0.0.1/private" type="video/mp4">
                <source src="/sound.mp4" type="audio/mp4">
                <source src="/video/proxy-owned" type="Video/MP4; codecs=avc1">
            </video></div></div></div>
            <div class="reply"><div class="tweet-body"><div class="attachments">
                <video src="/reply.mp4"></video></div></div></div>"""
        self.assertEqual(item_videos(row), ([], page))
        self.worker.session = FeedTransport({page: Response(html, "text/html"), clip: Response(mp4())})
        paths = []

        async def send(method, params):
            self.assertEqual(method, "send")
            self.assertEqual(len(params.get("attachment", [])), 1)
            path = Path(params["attachment"][0])
            paths.append(path)
            self.assertEqual(path.read_bytes(), mp4())
            self.assertIn(page, params["message"])
            return {"timestamp": NOW * 1000}

        with patch.object(self.signal, "rpc", AsyncMock(side_effect=send)) as rpc:
            await self.engine.deliver()
        rpc.assert_awaited_once()
        self.assertEqual(self.worker.session.requests, [page, clip])
        self.assertTrue(json.loads(self.get(row)["media_result"])["video_attached"])
        self.assertFalse(paths[0].exists())

    async def test_nitter_redirect_to_another_status_is_rejected_and_recorded(self):
        await self.command(f"/feed instant {self.feed['id']} on")
        await self.command("/videos on")
        self.ingest(("baseline", "Older edition", ""))
        page = "https://social.example/author/status/42"
        row = self.ingest_nitter(page, "Researchers release weather report",
                                 '<a href="/author/status/42#m">Video<img src="/pic/sample.jpg"></a>')
        self.worker.session = FeedTransport({page: Response(status=302, Location="/author/status/43")})

        with patch.object(self.signal, "rpc", AsyncMock(return_value={"timestamp": NOW * 1000})) as rpc:
            await self.engine.deliver()
            await self.engine.deliver()
        rpc.assert_awaited_once()
        self.assertNotIn("attachment", rpc.await_args.args[1])
        self.assertEqual(self.worker.session.requests, [page])
        media = json.loads(self.get(row)["media_result"])
        self.assertTrue(media["video_found"])
        self.assertTrue(media["video_attempted"])
        self.assertTrue(media["video_rejected"])
        self.assertFalse(media["video_submitted"])
        self.assertFalse(media["video_attached"])
