import json
import unittest
from unittest.mock import AsyncMock, patch

from newsbot.store import Store
from test_release_105 import NOW, SignalFixture


class HourlyCounter(SignalFixture, unittest.IsolatedAsyncioTestCase):
    def outbox(self, title):
        row = self.add_item("outbox", title)
        self.store.update_item(row["id"], summary=title, rules=self.config.rules_key(), model="groq-20b")
        self.store.execute("UPDATE items SET description='' WHERE id=?", (row["id"],))
        return row

    async def test_consecutive_confirmed_news_advance_the_footer(self):
        self.config.update(footer_hourly=True, max_posts_per_hour=25)
        first = self.outbox("Museum acquires rare portrait")
        second = self.outbox("Ferry terminal opens in Redbank")
        with patch.object(self.signal, "rpc", AsyncMock(side_effect=[
                {"timestamp": NOW * 1000, "results": [{"type": "SUCCESS"}]},
                {"timestamp": NOW * 1000 + 1}])) as rpc:
            await self.engine.deliver()
            await self.engine.deliver()

        messages = [call.args[1]["message"] for call in rpc.await_args_list]
        self.assertEqual(len(messages), 2)
        self.assertIn("📰 1/25", messages[0])
        self.assertIn("📰 2/25", messages[1])
        self.assertEqual([self.get(row)["status"] for row in (first, second)], ["sent", "sent"])
        self.assertEqual(self.engine.hour_count(), 2)

    async def test_visible_partial_results_use_slots_but_indeterminate_sends_do_not(self):
        self.config.update(footer_hourly=True, max_posts_per_hour=25)
        mixed = self.outbox("Museum acquires rare portrait")
        second_mixed = self.outbox("Ferry terminal opens in Redbank")
        unknown = self.outbox("Hospital resumes outpatient care")
        first = self.outbox("Harbor laboratory discovers comet")
        second = self.outbox("New solar farm goes online near Alderwick")
        mixed_results = [{"type": "SUCCESS"}, {"type": "NETWORK_FAILURE"}]
        with patch.object(self.signal, "rpc", AsyncMock(side_effect=[
                {"timestamp": NOW * 1000, "results": mixed_results},
                {"timestamp": NOW * 1000 + 1, "results": mixed_results},
                TimeoutError(),
                {"timestamp": NOW * 1000 + 2},
                {"timestamp": NOW * 1000 + 3},
        ])) as rpc:
            for _ in range(3):
                await self.engine.deliver()
            self.assertEqual(self.engine.hour_count(), 2)
            for _ in range(3):
                await self.engine.deliver()

        messages = [call.args[1]["message"] for call in rpc.await_args_list]
        self.assertEqual(len(messages), 5)
        for message, used in zip(messages, (1, 2, 3, 3, 4)):
            self.assertIn(f"📰 {used}/25", message)
        self.assertEqual([self.get(row)["status"] for row in (mixed, second_mixed, unknown, first, second)],
                         ["uncertain", "uncertain", "uncertain", "sent", "sent"])
        self.assertEqual(self.get(mixed)["signal_timestamp"], str(NOW * 1000))
        self.assertFalse(self.get(unknown)["signal_timestamp"])
        self.assertEqual(self.engine.hour_count(), 4)

    async def test_known_partial_delivery_can_reach_limit(self):
        self.config.update(footer_hourly=True, max_posts_per_hour=1)
        mixed = self.outbox("Museum acquires rare portrait")
        next_item = self.outbox("Ferry terminal opens in Redbank")
        with patch.object(self.signal, "rpc", AsyncMock(return_value={
                "timestamp": NOW * 1000, "results": [{"type": "SUCCESS"}, {"type": "NETWORK_FAILURE"}]})) as rpc:
            await self.engine.deliver()
            await self.engine.deliver()
        rpc.assert_awaited_once()
        self.assertEqual(self.get(mixed)["status"], "uncertain")
        self.assertEqual(self.get(next_item)["status"], "outbox")
        self.assertIn("Post slots used in the last hour: 1 / 1", await self.command("/queue"))

    async def test_limit_and_footer_use_the_current_group_and_account(self):
        self.config.update(footer_hourly=True, max_posts_per_hour=1)
        self.posted("Confirmed in original scope")
        for group, account, title in (
                ("another-group", "", "Volcano alert lifted at sunrise"),
                (self.group, "other-bot", "Country inaugurates observatory")):
            with self.subTest(group=group, account=account):
                self.config.update(group_id=group, signal_account=account)
                row = self.outbox(title)
                with patch.object(self.signal, "rpc", AsyncMock(return_value={"timestamp": NOW * 1000})) as rpc:
                    await self.engine.deliver()
                rpc.assert_awaited_once()
                self.assertIn("📰 1/1", rpc.await_args.args[1]["message"])
                self.assertEqual(self.get(row)["status"], "sent")

        self.config.update(group_id=self.group, signal_account="")
        remaining = self.outbox("Town adopts new building standards")
        with patch.object(self.signal, "rpc", AsyncMock()) as rpc:
            await self.engine.deliver()
        rpc.assert_not_awaited()
        self.assertEqual(self.get(remaining)["status"], "outbox")
        self.assertEqual(self.engine.hour_count(), 1)

    async def test_restart_keeps_confirmed_history_and_rolls_the_exact_hour(self):
        self.config.update(footer_hourly=True, max_posts_per_hour=1)
        # A pre-1.1.0 row can lack the account selector for an auto-picked account.
        self.posted("Historic confirmed post", age=3599, rules=json.dumps({"group_id": self.group}))
        interrupted = self.outbox("Museum acquires rare portrait")
        self.store.update_item(interrupted["id"], status="sending")
        next_item = self.outbox("Ferry terminal opens in Redbank")

        self.store.close()
        self.store = self.engine.store = Store(self.config.path.with_name("news.db"), recover=True)
        self.signal.store = self.pool.store = self.worker.store = self.store
        self.assertEqual(self.get(interrupted)["status"], "uncertain")
        with patch.object(self.signal, "rpc", AsyncMock(return_value={"timestamp": (NOW + 1) * 1000})) as rpc:
            await self.engine.deliver()
            rpc.assert_not_awaited()
            self.clock.return_value += 1
            await self.engine.deliver()
        rpc.assert_awaited_once()
        self.assertIn("📰 1/1", rpc.await_args.args[1]["message"])
        self.assertEqual(self.get(next_item)["status"], "sent")
        self.assertEqual(self.get(interrupted)["status"], "uncertain")
