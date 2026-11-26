import json
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiohttp
from aiohttp.test_utils import TestServer

from newsbot.ai import ModelOutputError
from newsbot.editorial import local_duplicate, terms
from newsbot.expansion import detail_result
from newsbot.storymute import muted_story
from newsbot.web import create_app
from replay_editorial import CASES, replay
from test_bot import decision, sample_picture
from test_release_105 import NOW, SignalFixture
from test_release_107 import FeedTransport, Response, mp4


# Fictional names and snippets preserve the operator's relationships without
# assuming the original feed text, model input, or ordering from Signal captions.
APPROVAL = (
    "The NSC approved OrbitalWorks’ plan to launch one million satellite-based data centers",
    "NSC cleared OrbitalWorks to deploy 1,000,000 data-center satellites in space",
    "The U.S. National Spectrum Commission approved OrbitalWorks to deploy up to one million orbital data center satellites",
)
ALERT = (
    "Evermont declared a Code Red after a major 24‑hour outage knocked out its email and meeting tools",
    "Evermont declared a ‘Code Red’ after a 24-hour outage in its collaboration suite, forcing crews to restore email tools",
    "Evermont declared Code Red after a 24 hour global outage of its hosted services",
)
CORRECTION = "Evermont declared Code Red after a 24 hour global outage of its hosted services"


def article(item_id, title, seconds=0, description=""):
    return dict(id=item_id, title=title, description=description, published=NOW + seconds, summary=title)


def match(old, new, before_ai):
    return local_duplicate(new, "" if before_ai else new["title"], [(old, terms(old["title"]), terms(old["summary"]))],
                           before_ai=before_ai)


class Developments(unittest.TestCase):
    def test_reported_repeat_shapes_match_in_either_order_at_both_guards(self):
        for family in (APPROVAL, ALERT):
            for a in family:
                for b in family:
                    if a == b:
                        continue
                    for before_ai in (True, False):
                        with self.subTest(a=a, b=b, before_ai=before_ai):
                            self.assertIsNotNone(match(article(1, a), article(2, b, 900), before_ai))

    def test_quantity_approval_generalizes_to_unrelated_actor_and_equipment(self):
        older = article(1, "Coastal Logistics Authority authorizes Inland Aero to deploy 50,000 relief aircraft")
        newer = article(2, "CLA clears Inland Aero to launch up to 50k relief aircraft", 900)
        self.assertIsNotNone(match(older, newer, True))
        other = article(3, "Civil Licensing Authority authorizes Inland Aero to deploy 50,000 relief aircraft", 900)
        self.assertIsNone(match(older, other, False))

    def test_distinct_roles_counts_stage_and_completed_recovery_are_not_repeats(self):
        cases = (
            (APPROVAL[0], "NSC accepted OrbitalWorks’ application to deploy one million data-center satellites"),
            (APPROVAL[0], "NSC cleared OrbitalWorks to deploy 900,000 data-center satellites in space"),
            (APPROVAL[0], "NSC cleared HorizonWorks to deploy one million data-center satellites in space"),
            (APPROVAL[0], "NRC cleared OrbitalWorks to deploy one million data-center satellites in space"),
            (APPROVAL[0], "NSC cleared OrbitalWorks to operate one million data-center satellites in space"),
            (APPROVAL[0], "NSC cleared OrbitalWorks to deploy one million satellites for wildfire monitoring"),
            (ALERT[1], "Evermont lifts Code Red after restoring its collaboration suite following the 24-hour outage"),
            (ALERT[1], "Evermont declared a second Code Red after a separate 24-hour outage of its hosted services"),
            (ALERT[2], "Evermont declared Code Red after a 12 hour global outage of its hosted services"),
            ("Evermont declared Code Red after a 24-hour outage of email in Canada",
             "Evermont declared Code Red after a 24-hour outage of email in Japan"),
        )
        for previous, new in cases:
            with self.subTest(new=new):
                self.assertIsNone(match(article(1, previous), article(2, new, 900), False))
        self.assertIsNone(match(article(1, ALERT[2]), article(2, CORRECTION, 900,
            "Correction: service was restored after 12 hours, not 24."), False))
        self.assertIsNone(match(article(1, APPROVAL[1]), article(2, APPROVAL[2], 900,
            "The decision adds conditions to the previous clearance."), True))
        self.assertIsNone(match(article(1, ALERT[0]), article(2, ALERT[2], 86400), True))
        snippet = "The company issued the alert and stated the outage lasted for 24 hours."
        self.assertIsNotNone(match(article(1, ALERT[0], 0, snippet), article(2, ALERT[0], 86400, snippet), True))

    def test_cited_details_accept_equivalent_duration_but_not_a_new_number(self):
        sources = [{"title": "Port terminal disruption",
                    "snippet": "After a 24‑hour outage, staff have opened the freight gate to queued trucks."}]
        supported = {"text": "The freight gate reopened after 24 hours.", "sources": [1]}
        self.assertEqual(detail_result(json.dumps(supported), "Terminal outage", sources)[1], [1])
        with self.assertRaises(ModelOutputError):
            detail_result(json.dumps({**supported, "text": "The freight gate reopened after 48 hours."}),
                          "Terminal outage", sources)


class Lens(SignalFixture, unittest.IsolatedAsyncioTestCase):
    async def test_replay_prior_is_in_serialized_model_input_and_followups_survive(self):
        for case in CASES:
            with self.subTest(case=case.key):
                result = await replay(case)
                self.assertEqual(result["requests"], 1)
                evidence = result["evidence"]
                self.assertEqual(len(evidence["recent"]), 15)
                self.assertLessEqual(len(evidence["related"]), 4)
                if case.label == "rewrite":
                    self.assertIs(result["prior_in_request"], True)
                    self.assertIn(result["prior_id"], evidence["related"])
                self.assertEqual(result["mock_sends"], int(case.label in {"follow-up", "distinct"}))

    async def test_same_batch_rewrites_do_not_send_even_if_mock_editor_selects_all(self):
        self.config.update(batch_size=10)
        titles = (*APPROVAL, *ALERT, "NSC cleared OrbitalWorks to deploy 900,000 data-center satellites",
                  "Evermont lifts Code Red after restoring its email and meeting tools")
        items = [self.add_item(title=title) for title in titles]
        self.store.execute("UPDATE items SET description=''")
        picks = [decision(item["id"], text=item["title"]) for item in items]
        with patch.object(self.pool, "analyze", AsyncMock(return_value=("groq-20b", picks))), patch.object(
                self.signal, "rpc", AsyncMock(return_value={"timestamp": NOW * 1000})) as rpc:
            await self.engine.process()
            for _ in items:
                await self.engine.deliver()
        self.assertEqual(rpc.await_count, 4)
        self.assertEqual([self.get(item)["status"] for item in items],
                         ["sent", "skipped", "skipped", "sent", "skipped", "skipped", "sent", "sent"])

    async def test_active_mute_covers_later_reworded_instants_but_not_new_outcomes(self):
        self.config.update(story_mute=True)
        self.ingest(("baseline", "Old baseline entry", ""))
        await self.command(f"/feed instant {self.feed['id']} on")
        for previous, repeated, update in (
                (APPROVAL[2], APPROVAL[0], "NSC cleared OrbitalWorks to deploy a second fleet of 900,000 data-center satellites"),
                (ALERT[2], ALERT[0], "Evermont restores its services and lifts Code Red following the outage")):
            with self.subTest(previous=previous):
                earlier = self.posted(previous)
                self.store.execute("UPDATE items SET description=? WHERE id=?", (previous + ".", earlier["id"]))
                await self.react(f"reader-{earlier['id']}", earlier["signal_timestamp"], "❌")
                self.assertIn(f"#{earlier['id']}", await self.command("/storymute list"))
                offset = str(earlier["id"])
                self.ingest(("repeat-" + offset, repeated, ""), ("new-" + offset, update, ""))
                with patch.object(self.signal, "rpc", AsyncMock(return_value={"timestamp": NOW * 1000})) as rpc:
                    await self.engine.deliver()
                    await self.engine.deliver()
                rpc.assert_awaited_once()
                states = self.store.rows("SELECT status,reason FROM items WHERE guid IN (?,?) ORDER BY id",
                                         ("repeat-" + offset, "new-" + offset))
                self.assertEqual([r["status"] for r in states], ["skipped", "sent"])
                self.assertIn("Story muted", states[0]["reason"])
        followup = article(199, CORRECTION, 900, "Correction: service was restored after 12 hours, not 24.")
        self.assertFalse(muted_story(self.engine, followup, followup["title"]))

    async def test_private_dashboard_identifies_input_history_without_showing_raw_prompt(self):
        previous = self.posted("North ferry station suspends all sailings")
        incoming = self.add_item(title="South rail station reopens after work")
        with patch.object(self.pool, "analyze", AsyncMock(return_value=("groq-20b", [
                decision(incoming["id"], text=incoming["title"])]))):
            await self.engine.process()
        app = create_app(self.config, self.store, self.pool, self.signal, self.engine)
        auth = aiohttp.BasicAuth("admin", self.config.data["dashboard_password"])
        async with TestServer(app) as server:
            url = server.make_url("/?status=outbox")
            async with self.session.get(url) as resp:
                self.assertEqual(resp.status, 401)
            async with self.session.get(url, auth=auth) as resp:
                self.assertEqual(resp.status, 200)
                html = await resp.text()
        self.assertIn("Story context supplied to the editor", html)
        self.assertIn(f"<code>#{previous['id']}</code>", html)
        self.assertNotIn(previous["title"], html)

    async def test_rich_media_counts_distinguish_confirmed_and_mixed_receipts(self):
        self.config.update(include_images=True, include_videos=True)
        await self.command(f"/feed instant {self.feed['id']} on")
        self.ingest(("baseline", "Old entry", ""))
        for n, title in enumerate(("Museum opens storage vault", "New ferry arrives at west dock")):
            self.ingest((str(n), title, f"""<description>Plain teaser</description>
                <content:encoded xmlns:content="http://purl.org/rss/1.0/modules/content/"><![CDATA[
                <article><figure><img src="/one.jpg"></figure><figure><img src="/two.jpg"></figure></article>
                <video><source src="/part{n}.mp4" type="video/mp4"></video>]]></content:encoded>"""))
        self.worker.session = FeedTransport({f"https://example.com/part{n}.mp4": Response(mp4()) for n in range(2)})
        pictures = [sample_picture("red"), sample_picture("blue")]
        seen = []

        async def send(_method, params):
            video, *photos = params["attachment"]
            seen.append((Path(video).read_bytes(), photos))
            if "ferry" in params["message"]:
                return {"timestamp": NOW * 1000 + 1, "results": [{"type": "SUCCESS"}, {"type": "NETWORK_FAILURE"}]}
            return {"timestamp": NOW * 1000}

        with patch("newsbot.engine.fetch_image", AsyncMock(side_effect=pictures * 2)), patch.object(
                self.signal, "rpc", AsyncMock(side_effect=send)) as rpc:
            for _ in range(3):
                await self.engine.deliver()
        self.assertEqual(rpc.await_count, 2)
        self.assertEqual(seen, [(mp4(), pictures), (mp4(), pictures)])
        sent, uncertain = self.store.rows("SELECT status,media_result FROM items WHERE guid IN ('0','1') ORDER BY id")
        self.assertEqual([sent["status"], uncertain["status"]], ["sent", "uncertain"])
        confirmed, mixed = (json.loads(row["media_result"]) for row in (sent, uncertain))
        self.assertEqual(confirmed["photos_attached"], 2)
        self.assertTrue(confirmed["video_attached"])
        self.assertEqual(mixed["photos_submitted"], 2)
        self.assertTrue(mixed["video_submitted"])
        self.assertNotIn("photos_attached", mixed)
        self.assertNotIn("video_attached", mixed)
