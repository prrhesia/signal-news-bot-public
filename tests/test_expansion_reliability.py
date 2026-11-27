import json
import unittest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import aiohttp

from newsbot.ai import ModelOutputError, PoolUnavailable
from newsbot.expansion import SEARCH_URL, TavilySearch, detail_result
from newsbot.interactions import process_expansion
from test_release_105 import NOW, SignalFixture
from test_release_107 import Response


HEADLINE = "Westport council approves construction of a ferry terminal"
ECHO = "The Westport council officially greenlit construction of the major ferry terminal."
RESULT = {
    "title": "Westport ferry terminal plan",
    "content": "Westport ferry terminal will include a covered waiting area for passengers.",
    "url": "https://news.example/report?edition=local&utm_source=ignored",
}
DETAIL = {"text": "The plan includes a covered waiting area for passengers.", "sources": [1]}


class SearchTransport:
    def __init__(self, data, status=200):
        self.response = Response(data, status=status)
        self.requests = []

    @asynccontextmanager
    async def post(self, url, **kwargs):
        self.requests.append((url, kwargs))
        yield self.response


class ExpansionReliability(SignalFixture, unittest.IsolatedAsyncioTestCase):
    def enable_web(self):
        self.config.update(web_expand=True, tavily_key="synthetic-tavily-key", topic_feedback=False,
                           story_mute=False, models=[
                               {"name": "fixture", "provider": "groq", "model": "test", "key": "synthetic-model-key"}
                           ])

    def details_post(self, title=HEADLINE, description=""):
        n = self.store.one("SELECT COUNT(*) AS n FROM items")["n"]
        post = self.posted(f"Expansion test article {n}")
        self.store.execute("UPDATE items SET title=?,summary=?,description=? WHERE id=?",
                           (title, title, description, post["id"]))
        return self.get(post)

    async def quoted_expand(self, post, reader="command-reader", *, author="test-bot-aci", group=None):
        incoming = self.signal.incoming({"envelope": {"timestamp": NOW * 1000, "sourceUuid": reader,
            "dataMessage": {"message": "/expand", "groupInfo": {"groupId": group or self.group},
                            "quote": {"id": int(post["signal_timestamp"]), "authorUuid": author,
                                      "text": "Private quoted message never used as evidence"}}}})
        return await self.engine.command(*incoming) if incoming else None

    async def work(self):
        job = self.engine.expansions.get_nowait()
        try:
            await process_expansion(self.engine, *job)
        finally:
            self.engine.expansions.task_done()

    def search_count(self):
        return self.store.one("SELECT COALESCE(SUM(requests),0) AS n FROM expansion_search")["n"]

    def diagnostics(self):
        return "\n".join(row["detail"] for row in self.store.rows(
            "SELECT detail FROM events WHERE kind='expansion' ORDER BY id"))

    async def test_cosmetic_feed_echo_queues_one_cited_search_from_signal_reaction(self):
        self.enable_web()
        post = self.details_post(HEADLINE + " &#104;ttps://title.example/tracking", ECHO + " Read the full story.")
        result = {**RESULT, "content": RESULT["content"] + " Read www.snippet.example/more."}
        transport = SearchTransport(json.dumps({"results": [result, {**RESULT, "url": "http://127.0.0.1/x"}]}).encode())
        self.engine.search = TavilySearch(transport)

        async def model(headline, sources, validate):
            self.assertEqual(headline, HEADLINE)
            self.assertEqual(sources, [{"id": 1, "title": RESULT["title"], "snippet": RESULT["content"] + " Read"}])
            return validate(json.dumps(DETAIL))

        with patch.object(self.pool, "expand", AsyncMock(side_effect=model)) as expand, patch.object(
                self.signal, "rpc", AsyncMock(return_value={"timestamp": NOW * 1000})) as rpc:
            await self.react("reaction-reader", post["signal_timestamp"], "🔎\ufe0f")
            self.assertIn("No new", await self.quoted_expand(post))
            rpc.assert_not_awaited()
            self.assertEqual(self.engine.expansions.qsize(), 1)
            await self.work()
        expand.assert_awaited_once()
        rpc.assert_awaited_once()
        method, params = rpc.await_args.args
        self.assertEqual(method, "send")
        self.assertEqual(params["quoteTimestamp"], int(post["signal_timestamp"]))
        self.assertEqual(params["quoteAuthor"], "test-bot")
        self.assertIn("More from the web", params["message"])
        self.assertIn("https://news.example/report?edition=local", params["message"])
        self.assertNotIn("utm_source", params["message"])
        self.assertEqual(self.search_count(), 1)

        [(url, request)] = transport.requests
        self.assertEqual(url, SEARCH_URL)
        self.assertEqual(request["json"], {
            "query": HEADLINE, "topic": "news", "search_depth": "basic", "max_results": 4,
            "include_answer": False, "include_raw_content": False, "include_images": False, "auto_parameters": False,
        })
        self.assertFalse(request["allow_redirects"])
        self.assertLessEqual(request["timeout"].total, 7)
        diagnostics = self.diagnostics()
        for stage in ("🔎 reaction queued", "already requested", "Reserved one Tavily search", "Signal accepted"):
            self.assertIn(stage, diagnostics)
        for value in (HEADLINE, RESULT["url"], "synthetic-tavily-key", "reaction-reader", self.group,
                      "Private quoted message"):
            self.assertNotIn(value, diagnostics)

    async def test_material_feed_details_still_win_and_opt_in_is_required(self):
        self.enable_web()
        self.engine.search = AsyncMock()
        local = self.details_post(description=ECHO + " Westport council approved construction of a ferry terminal for $20 million.")
        empty = self.details_post(description=ECHO)
        with patch.object(self.pool, "expand", AsyncMock()) as model, patch.object(
                self.signal, "rpc", AsyncMock(return_value={"timestamp": NOW * 1000})) as rpc:
            self.assertIsNone(await self.quoted_expand(local))
            self.config.update(web_expand=False)
            await self.react("feed-only-reader", empty["signal_timestamp"], "🔎")
        self.engine.search.search.assert_not_awaited()
        model.assert_not_awaited()
        self.assertEqual(self.engine.expansions.qsize(), 0)
        self.assertEqual(self.search_count(), 0)
        feed, missing = [call.args[1]["message"] for call in rpc.await_args_list]
        self.assertIn("$20 million", feed)
        self.assertNotIn("greenlit", feed)
        self.assertIn("The saved feed entry has no additional details.", missing)
        self.assertIn("web fallback is off", self.diagnostics())

    async def test_search_failures_are_distinct_private_events_without_response_bodies(self):
        self.enable_web()
        cases = (
            (401, b"private response", "refused authorization"),
            (429, b"private response", "rate limit or account quota"),
            (503, b"private response", "HTTP 503"),
            (200, b"private response", "invalid or oversized JSON"),
            (200, b"{}", "missing a valid results list"),
            (200, b'{"results":[]}', "returned no results"),
            (200, b'{"results":[{"url":"http://127.0.0.1/private"}]}', "eligible public links"),
        )
        with patch.object(self.pool, "expand", AsyncMock()) as model, patch.object(
                self.signal, "rpc", AsyncMock(return_value={"timestamp": NOW * 1000})) as rpc:
            for n, (status, data, event) in enumerate(cases):
                with self.subTest(status=status, event=event):
                    transport = SearchTransport(data, status)
                    self.engine.search = TavilySearch(transport)
                    post = self.details_post()
                    self.assertIsNone(await self.quoted_expand(post, f"reader-{n}"))
                    await self.work()
                    self.assertEqual(len(transport.requests), 1)
                    self.assertIn(event, self.diagnostics())
                    message = rpc.await_args.args[1]["message"]
                    self.assertIn("More from the web", message)
                    self.assertNotIn(event, message)
        model.assert_not_awaited()
        self.assertEqual(self.search_count(), len(cases))
        self.assertNotIn("private response", self.diagnostics())
        self.assertNotIn("127.0.0.1", self.diagnostics())

    async def test_search_returns_links_without_inference_when_no_model_is_ready(self):
        self.enable_web()
        self.engine.search = AsyncMock()
        self.engine.search.search.return_value = [
            {"id": 1, "title": "Fixture result", "snippet": "Fixture text", "url": "https://example.org/report"}
        ]
        with patch.object(self.pool, "expand", AsyncMock()) as model, patch.object(
                self.signal, "rpc", AsyncMock(return_value={"timestamp": NOW * 1000})) as rpc:
            self.config.update(models=[])
            self.assertIsNone(await self.quoted_expand(self.details_post()))
            await self.work()
            self.engine.search.search.assert_awaited_once()
            self.assertIn("no eligible free model is ready", self.diagnostics())
            reply = rpc.await_args.args[1]["message"]
            self.assertIn("links only", reply)
            self.assertIn("https://example.org/report", reply)
            self.assertNotIn("Fixture text", reply)

            self.enable_web()
            self.engine.search.search.reset_mock()
            with patch.object(self.store, "reserve_search", return_value=False):
                self.assertIsNone(await self.quoted_expand(self.details_post(), "local-cap-reader"))
                await self.work()
            self.engine.search.search.assert_not_awaited()
            self.assertIn("local Tavily daily or monthly attempt limit", self.diagnostics())

            for n, (error, event) in enumerate((
                    (TimeoutError("private error"), "Tavily search timed out"),
                    (aiohttp.ClientConnectionError("private error"), "failed to connect or read"))):
                self.engine.search.search.side_effect = error
                self.assertIsNone(await self.quoted_expand(self.details_post(), f"transport-reader-{n}"))
                await self.work()
                self.assertIn(event, self.diagnostics())
        model.assert_not_awaited()
        self.assertEqual(self.search_count(), 3)
        self.assertNotIn("private error", self.diagnostics())

    async def test_invalid_model_output_and_unconfirmed_reply_are_not_retried(self):
        self.enable_web()
        result = SearchTransport(json.dumps({"results": [RESULT]}).encode())
        self.engine.search = TavilySearch(result)
        post = self.details_post()
        with patch.object(self.pool, "expand", AsyncMock(side_effect=PoolUnavailable("private model error"))) as model:
            with patch.object(self.signal, "rpc", AsyncMock(side_effect=TimeoutError("private Signal error"))) as rpc:
                self.assertIsNone(await self.quoted_expand(post))
                await self.work()
                await self.react("after-timeout", post["signal_timestamp"], "🔎")
                self.assertEqual(self.engine.expansions.qsize(), 0)
        model.assert_awaited_once()
        rpc.assert_awaited_once()
        self.assertEqual(len(result.requests), 1)
        self.assertEqual(self.search_count(), 1)
        self.assertIn("model failed or returned unusable citations", self.diagnostics())
        self.assertIn("Signal did not confirm", self.diagnostics())
        self.assertNotIn("private model error", self.diagnostics())
        self.assertNotIn("private Signal error", self.diagnostics())

    async def test_group_author_and_preview_gates_avoid_outbound_requests(self):
        self.enable_web()
        self.engine.search = AsyncMock()
        post = self.details_post()
        with patch.object(self.pool, "expand", AsyncMock()) as model, patch.object(
                self.signal, "rpc", AsyncMock()) as rpc:
            await self.react("other-group-reader", post["signal_timestamp"], "🔎", group="another-group")
            await self.react("wrong-author-reader", post["signal_timestamp"], "🔎", author="wrong-author")
            await self.react("removed-reaction", post["signal_timestamp"], "🔎", removed=True)
            await self.react("unmatched-reader", NOW * 1000 - 12, "🔎")
            malformed = self.signal.incoming({"envelope": {"timestamp": NOW * 1000, "sourceUuid": "malformed-reader",
                "dataMessage": {"groupInfo": {"groupId": self.group}, "reaction": {
                    "emoji": "🔎", "targetSentTimestamp": int(post["signal_timestamp"]),
                    "targetAuthorUuid": "test-bot-aci"}}}})
            await self.engine.command(*malformed)
            self.config.update(mode="preview")
            await self.react("preview-reader", post["signal_timestamp"], "🔎")
            self.assertIn("No new", await self.quoted_expand(post))
        self.engine.search.search.assert_not_awaited()
        model.assert_not_awaited()
        rpc.assert_not_awaited()
        self.assertEqual(self.engine.expansions.qsize(), 0)
        self.assertEqual(self.get(post)["expanded"], 0)
        for cause in ("target author could not be matched", "no matching post in local history",
                      "missing event timestamp or removal flag", "in preview"):
            self.assertIn(cause, self.diagnostics())
        self.assertNotIn("another-group", self.diagnostics())
        self.assertNotIn("wrong-author", self.diagnostics())

    async def test_inference_or_quoted_reply_stops_when_state_changes_mid_request(self):
        self.enable_web()
        self.engine.search = AsyncMock()
        rows = [{"id": 1, "title": RESULT["title"], "snippet": RESULT["content"], "url": RESULT["url"]}]
        first = self.details_post()
        second = self.details_post()

        async def pause_during_search(*args):
            self.config.update(paused=True)
            return rows

        async def change_group_during_model(_headline, _sources, validate):
            self.config.update(group_id="another-group")
            return validate(json.dumps(DETAIL))

        self.engine.search.search.side_effect = pause_during_search
        with patch.object(self.pool, "expand", AsyncMock(side_effect=change_group_during_model)) as model:
            with patch.object(self.signal, "rpc", AsyncMock()) as rpc:
                await self.quoted_expand(first)
                await self.work()
                model.assert_not_awaited()
                self.config.update(paused=False)
                self.engine.search.search.side_effect = None
                self.engine.search.search.return_value = rows
                await self.quoted_expand(second, "other-reader")
                await self.work()
        model.assert_awaited_once()
        rpc.assert_not_awaited()
        self.assertEqual(self.engine.search.search.await_count, 2)
        self.assertIn("stopped after Tavily", self.diagnostics())
        self.assertIn("reply canceled: group or selected account changed", self.diagnostics())


class CitationValidation(unittest.TestCase):
    def test_grounding_rejects_irrelevant_extra_citation_repeat_or_invented_claim(self):
        sources = [{"title": RESULT["title"], "snippet": RESULT["content"]},
                   {"title": "Westport ferry terminal plan", "snippet": "A vote on the project was held Wednesday."}]
        self.assertEqual(detail_result(json.dumps(DETAIL), HEADLINE, sources), (DETAIL["text"], [1]))
        for response in ({**DETAIL, "sources": [1, 2]},
                         {**DETAIL, "text": DETAIL["text"] + " An airport expands."},
                         {**DETAIL, "text": DETAIL["text"] + " Source: news.example."},
                         {**DETAIL, "text": "The plan includes a covered waiting area for 42 passengers."}):
            with self.subTest(response=response), self.assertRaises(ModelOutputError):
                detail_result(json.dumps(response), HEADLINE, sources)
        with self.assertRaises(ModelOutputError):
            detail_result(json.dumps(DETAIL), HEADLINE, sources, summary=DETAIL["text"])
