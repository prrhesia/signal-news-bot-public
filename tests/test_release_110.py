import unittest
from unittest.mock import AsyncMock, patch

from test_bot import decision
from test_release_105 import NOW, SignalFixture
from test_release_109 import article, match


APPROVAL = "The Harbor Communications Commission authorized Kiteworks’ plan to place a million satellite data centers in space"
APPROVAL_REWRITE = "HCC cleared Kiteworks to deploy up to 1,000,000 orbital data-center satellites"
ALERT = "Riverton declared a Code Amber after a 21-hour outage knocked out Postbox and Meetroom"
ALERT_REWRITE = "Riverton announced a ‘Code Amber’ after a 24-hour outage hit Postbox and Meetroom"
MONITORING = "Air Standards Agency reports zero airborne copper after Valley Works completed the disposal"
MONITORING_REWRITE = (
    "Air Standards Agency Administrator Morgan Taylor announced that monitors detected no airborne copper "
    "after disposal at Valley Works"
)
THREE_DAYS = 3 * 86400


class EditorialContinuity(unittest.TestCase):
    def test_cross_day_rewrites_match_in_either_order(self):
        for first, second in ((APPROVAL, APPROVAL_REWRITE), (ALERT, ALERT_REWRITE),
                              (MONITORING, MONITORING_REWRITE)):
            for old, new in ((first, second), (second, first)):
                with self.subTest(old=old, new=new):
                    self.assertIsNotNone(match(article(1, old), article(2, new, THREE_DAYS), True))

    def test_material_changes_and_distinct_events_still_pass(self):
        for old, new, detail in (
                (APPROVAL, APPROVAL_REWRITE.replace("1,000,000", "900,000"), ""),
                (APPROVAL, APPROVAL_REWRITE, "The revised decision adds new operating conditions."),
                (ALERT, ALERT_REWRITE, "Correction: the outage lasted 24 hours, not 21."),
                (ALERT, ALERT_REWRITE, "Independent logs put the outage at 30 hours."),
                (ALERT, ALERT_REWRITE, "This was a second, separate incident; Postbox and Meetroom were down again."),
                (ALERT, "Riverton restored Postbox and Meetroom and lifted Code Amber", ""),
                (ALERT, ALERT_REWRITE.replace("Postbox and Meetroom", "Ledgerdesk and Archivebox"), ""),
                (MONITORING, "Valley Works delays the disposal after a safety inspection", ""),
                (MONITORING, "Valley Works completes the disposal following a delay", ""),
                (MONITORING, MONITORING_REWRITE.replace("no airborne copper", "airborne copper above the limit"), ""),
                (MONITORING, MONITORING_REWRITE.replace("Valley Works", "North Plant"), "")):
            with self.subTest(new=new, detail=detail):
                self.assertIsNone(match(article(1, old), article(2, new, THREE_DAYS, detail), False))


class DeliveryContinuity(SignalFixture, unittest.IsolatedAsyncioTestCase):
    async def test_precise_older_event_is_not_crowded_out_of_the_local_guard(self):
        prior = self.posted(APPROVAL)
        self.store.execute("UPDATE items SET published=?,sent_at=?,description='' WHERE id=?",
                           (NOW - THREE_DAYS, NOW - THREE_DAYS, prior["id"]))
        for index in range(25):
            older = self.posted(f"RLC cleared Kiteworks to deploy up to 1,000,000 orbital data-center satellites for region {index}")
            self.store.execute("UPDATE items SET description='' WHERE id=?", (older["id"],))
        repeat = self.add_item(title=APPROVAL_REWRITE)
        self.store.execute("UPDATE items SET description='' WHERE id=?", (repeat["id"],))
        with patch.object(self.pool, "analyze", AsyncMock()) as analyze:
            await self.engine.process()
        analyze.assert_not_awaited()
        self.assertEqual(self.get(repeat)["status"], "skipped")
        self.assertIn(f"Already covered · #{prior['id']}", self.get(repeat)["reason"])

    async def test_older_rewrite_is_skipped_before_model_and_active_mute_keeps_distinct_update(self):
        self.config.update(story_mute=True)
        prior = self.posted(ALERT)
        await self.react("reader", prior["signal_timestamp"], "❌")
        self.store.execute("UPDATE items SET published=?,sent_at=? WHERE id=?", (NOW - THREE_DAYS, NOW - THREE_DAYS, prior["id"]))
        repeat = self.add_item(title=ALERT_REWRITE)
        update = self.add_item(title="Riverton lifts Code Amber after restoring Postbox and Meetroom")
        self.store.execute("UPDATE items SET description='' WHERE id IN (?,?)", (repeat["id"], update["id"]))
        with patch.object(self.pool, "analyze", AsyncMock(return_value=(
                "groq-20b", [decision(update["id"], text=update["title"])]))) as analyze, patch.object(
                self.signal, "rpc", AsyncMock(return_value={"timestamp": NOW * 1000 + 1})) as rpc:
            await self.engine.process()
            await self.engine.deliver()
        sent_to_model, _ = analyze.await_args.args
        self.assertEqual([item["id"] for item in sent_to_model], [update["id"]])
        self.assertIn(f"Story muted · #{prior['id']}", self.get(repeat)["reason"])
        self.assertEqual(self.get(update)["status"], "sent")
        rpc.assert_awaited_once()
