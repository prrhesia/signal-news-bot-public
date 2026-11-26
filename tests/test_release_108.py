import unittest
from unittest.mock import AsyncMock, patch

from newsbot.editorial import matches_story, terms
from newsbot.feeds import item_images, item_videos
from newsbot.interactions import edit_footer, vote_tally
from newsbot.storymute import muted_story
from test_release_105 import NOW, SignalFixture


VERDICT = (
    "Huawei convicted in US criminal trial for hiding its Iran business",
    "A U.S. jury found Huawei guilty of stealing trade secrets",
    "Huawei found guilty on ten criminal counts in US trial over concealed Iran dealings",
    "Huawei convicted of concealing Iran business and trade-secret violations in a U.S. criminal trial",
)
BREACH = (
    "Cyberattack breached a US CIA unclassified system without compromising classified information",
    "Newspaper reports CIA suffered a hack exposing unclassified data at the U.S. intelligence agency",
    "U.S. intelligence agencies report a breach of unclassified systems",
)


class Cadence(SignalFixture, unittest.IsolatedAsyncioTestCase):
    async def test_rewrites_in_either_order_and_material_followups(self):
        for cluster, update in ((VERDICT, "Huawei appeals its conviction over Iran business in U.S. court"),
                                (BREACH, "CIA restores unclassified systems after breach")):
            for i, old in enumerate(cluster):
                row = dict(id=i, title=old, summary=old, published=NOW)
                prior = [(row, terms(old), terms(old))]
                for new in cluster:
                    self.assertIsNotNone(matches_story(dict(id=9, title=new, published=NOW+600), "", prior,
                                                       prior, before_ai=True), (old, new))
                self.assertIsNone(matches_story(dict(id=9, title=update), update, prior, prior))

    async def test_partial_delivery_retains_target_for_feedback_and_story_mute(self):
        self.config.update(topic_feedback=True, story_mute=True, footer_score=True)
        row = self.add_item("outbox", VERDICT[0])
        self.store.update_item(row["id"], summary=VERDICT[0], score=90, rules=self.config.rules_key(), model="groq-20b")
        stamp = NOW * 1000
        with patch.object(self.signal, "rpc", AsyncMock(side_effect=[
                {"timestamp": stamp, "results": [{"type": "SUCCESS"}, {"type": "NETWORK_FAILURE"}]},
                {"timestamp": stamp + 9000}])) as rpc:
            await self.engine.deliver()
            self.assertEqual(self.get(row)["status"], "uncertain")
            await self.react("a", stamp, "👍")
            await self.react("b", stamp, "❌")
            self.assertEqual(vote_tally(self.store, row["id"])["n"], 1)
            self.assertTrue(muted_story(self.engine, dict(id=100, title=VERDICT[1], published=NOW), ""))
            self.clock.return_value += 30
            await edit_footer(self.engine)
            self.assertEqual(rpc.await_args.args[1]["editTimestamp"], stamp)
            self.assertEqual(self.get(row)["status"], "uncertain")

    async def test_other_focus_and_unrelated_event(self):
        title = "Seabrook orchestra postpones winter festival after storm"
        row = dict(id=1, title=title, summary=title, published=NOW)
        prior = [(row, terms(title), terms(title))]
        def match(title):
            return matches_story(dict(id=2, title=title, published=NOW+600), "", prior, prior, before_ai=True)
        self.assertTrue(match("Winter festival postponed by Seabrook orchestra"))
        self.assertFalse(match("Westford orchestra postpones winter festival after storm"))
        self.assertFalse(match("Seabrook orchestra postpones a second winter festival"))

    async def test_encoded_gallery_and_mp4_behind_plain_feed_teaser(self):
        self.ingest(("prior", "Baseline", ""))
        self.ingest(("rich", "Gallery report", """<description>See the gallery</description>
            <content:encoded xmlns:content="http://purl.org/rss/1.0/modules/content/"><![CDATA[
            <article><figure><img src="/one.jpg"></figure><figure><img src="/two.jpg"></figure></article>
            <video><source src="/clip.mp4" type="video/mp4"></video>]]></content:encoded>"""))
        row = self.store.one("SELECT * FROM items WHERE title='Gallery report'")
        self.assertEqual(len(item_images(row)), 2)
        self.assertEqual(item_videos(row)[0], ["https://example.com/clip.mp4"])
