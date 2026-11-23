import json
import io
from contextlib import redirect_stdout
import unittest
from unittest.mock import AsyncMock, patch

from newsbot.ai import ModelOutputError, completion_text, parse_decisions
from newsbot.config import Config
from newsbot.ai_check import check_ai
from newsbot.formatting import Message, SignalText, article_age
from newsbot.engine import Engine
from test_bot import Base, decision


class FormattingTests(unittest.TestCase):
    def test_utf16_offsets_and_trim(self):
        message = Message().add("✅ 🤖 ").add("model", "MONOSPACE").line().line("Note\n", "ITALIC").build()
        self.assertIsInstance(message, SignalText)
        self.assertEqual(message.styles, ("5:5:MONOSPACE", "11:4:ITALIC"))

    def test_age(self):
        self.assertIn("unknown", article_age(None, 10000))
        self.assertIn("unknown", article_age(10001, 10000))
        self.assertEqual(article_age(9880, 10000), "Published 2 min ago")
        self.assertEqual(article_age(6400, 10000), "Published 1 hr ago")

    def test_completed_reasoning(self):
        item = decision(1, post=False)
        item.update(text=None, emoji=None)
        result = parse_decisions('<think>analysis</think>\n' + json.dumps({"decisions": [item]}), [{"id": 1}])
        self.assertEqual(result[0]["text"], "")

    def test_specific_errors(self):
        for content, error in [("<think>unfinished", "Unfinished reasoning"), ("oops", "not valid JSON")]:
            with self.assertRaisesRegex(ModelOutputError, error):
                parse_decisions(content, [{"id": 1}])
        with self.assertRaisesRegex(ModelOutputError, "No final text"):
            completion_text({"choices": [{"finish_reason": "length", "message": {"content": None}}]})


class CommandTests(Base, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.setup_data()
        self.pool = AsyncMock()
        self.pool.status = lambda: []
        self.pool.local_remaining = lambda model: 899
        self.engine = Engine(self.store, self.config, self.pool, AsyncMock())

    def tearDown(self):
        self.cleanup_data()

    async def test_footer_persistence(self):
        for field in ("model", "age", "source"):
            response = await self.engine.command(f"/footer {field} on")
            self.assertIsInstance(response, SignalText)
        saved = Config(self.config.path)
        self.assertTrue(saved.data["footer_age"])
        self.assertTrue(saved.data["include_sources"])
        await self.engine.command("/footer model off")
        self.assertFalse(self.config.data["footer_model"])
        self.assertIn("⚠️", await self.engine.command("/footer quota off"))

    async def test_replies_have_native_styles(self):
        for command in ("/help", "/status", "/focus", "/exclude", "/models", "/model", "/usage", "/pause", "/resume", "/mode preview"):
            reply = await self.engine.command(command)
            self.assertIsInstance(reply, SignalText)
            self.assertTrue(reply.styles)
            length = len(reply.encode("utf-16-le")) // 2
            for span in reply.styles:
                start, size, _ = span.split(":")
                self.assertLessEqual(int(start) + int(size), length)

    async def test_diagnostic_does_not_persist_preferences(self):
        original = self.config.path.read_bytes()
        alias = self.config.data["models"][0]["name"]
        with patch("newsbot.ai_check.ModelPool") as pool_class:
            pool = pool_class.return_value
            pool.candidates.return_value = [self.config.data["models"][0]]
            pool.route_state.return_value = {"retired": False, "blocked": False, "until": 0}
            pool.analyze = AsyncMock(return_value=(alias, [decision(1)]))
            pool.footer.return_value = "test quota"
            with redirect_stdout(io.StringIO()):
                self.assertTrue(await check_ai(self.config, self.config.path.parent, alias))
        self.assertEqual(self.config.path.read_bytes(), original)

    async def test_post_footer_switches(self):
        self.config.update(mode="live", footer_model=False, footer_age=True)
        item = self.add_item(state="outbox")
        self.store.execute("UPDATE items SET published=? WHERE id=?", (1700000000, item["id"]))
        self.store.update_item(item["id"], rules=self.config.rules_key(), model="groq-20b", provider="groq", actual_model="actual-model", summary="News summary")
        self.pool.footer = lambda *args, **kwargs: "AI: groq-20b (actual-model) · provider quota: not reported"
        self.engine.signal.connected = True
        self.engine.signal.send.return_value = {"timestamp": 123}
        with patch("newsbot.engine.time.time", return_value=1700000120):
            await self.engine.deliver()
        message = self.engine.signal.send.call_args.args[0]
        self.assertNotIn("AI:", message)
        self.assertIn("899/900 calls", message)
        self.assertIn("2 min ago", message)
