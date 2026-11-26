import asyncio
import json
import time
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory

from .ai import PoolUnavailable, publisher_boilerplate
from .controls import operational_command
from .editorial import (
    RELATED_POSTS, best_stories, editor_stories, in_group, latest_posts, local_duplicate, matches_story,
    recent_stories, sql_scope, story_frequencies, story_ranks, story_sample, terms,
)
from .feeds import fetch_image, item_images, item_videos
from .formatting import Message, notice, toggle
from .interactions import (boolean_setting, edit_footer, expand_command, feed_command, numeric_toggle_setting, preference_delta,
                           process_expansion, recap_command, record_reaction, topic_preferences, why_command)
from .media import distinct_images, prepare_video
from .moderation import process_removals, purge_command, reply_cleanup_command
from .publishing import FOOTER_FIELDS, model_label, recorded_message, render_news
from .signal import direct_recipient
from .storymute import muted_story, storymute_command
from .version import VERSION


class Engine:
    def __init__(self, store, config, pool, signal, feeds=None, search=None):
        self.store, self.config, self.pool, self.signal = store, config, pool, signal
        self.feeds = feeds
        self.search = search
        self.expansions = asyncio.Queue(maxsize=4)
        self.last_command = {}
        self.pool_error = ""
        self.cleanup_previews = {}
        self.feed_previews = {}
        self.purge_previews = {}
        self.recap_cache = {}
        self.last_recap_attempt = 0
        self.dm_tests = {}
        self.last_dm_test = 0
        self.signal_changes = asyncio.Lock()

    @property
    def scope(self):
        return self.config.data["group_id"], self.config.data["signal_account"]

    async def command(self, body, source="web", context=None):
        if self.maintenance:
            return None if body == "/_reaction" else notice("⏳ Maintenance in progress", "Please try again shortly.")
        context = context or {}
        scope = self.scope
        if context and context.get("group") != scope[0]:
            return None
        if body == "/_reaction":
            await record_reaction(self, source, context, scope)
            return None
        if len(self.last_command) > 2000:
            self.last_command.clear()
        now = time.time()
        if source != "web" and now - self.last_command.get(source, 0) < 2:
            return None
        self.last_command[source] = now
        name, _, arg = body.partition(" ")
        name, arg = name.lower().strip(), arg.strip()
        c = self.config.data
        if name == "/summary":
            return await recap_command(self, arg, scope)
        if name == "/feed":
            return await feed_command(self, arg, source, scope)
        if name == "/why":
            return await why_command(self, context, scope)
        if name == "/expand":
            return await expand_command(self, source, context, scope)
        if name == "/dmtest":
            return await self.dm_test(arg, source, context, scope, now)
        if name == "/images":
            return numeric_toggle_setting(self, arg, "include_images", name, "🖼️ Feed images",
                                          ("min", "image_min_score"), 0, 100,
                                          "Original AI score needed: {value}/100 · /images on|off · /images min 75")
        if name == "/videos":
            return numeric_toggle_setting(self, arg, "include_videos", name, "🎬 Feed videos",
                                          ("min", "video_min_score"), 0, 100,
                                          "Original AI score: {value}/100 (instant has no score) · /videos on|off · /videos min 75")
        if name == "/feedback":
            return numeric_toggle_setting(self, arg, "topic_feedback", name, "🗳️ Topic feedback",
                                          ("weight", "feedback_weight"), 1, 10,
                                           "👍 +{value} · 👎 −{value} per vote, capped at ±10. /feedback on|off · /feedback weight 3")
        if name == "/storymute":
            return storymute_command(self, arg)
        if name in {"/emergency", "/rapemode"}:
            result = boolean_setting(self, arg, "emergency", "/emergency", "🚨 Emergency forwarding",
                                     "New feed headlines · no AI or hourly cap · pause and preview still apply")
            if arg == "off":
                self.store.requeue_direct("emergency")
            return result
        if name == "/replycleanup":
            return reply_cleanup_command(self, arg)
        if name == "/purge":
            return await purge_command(self, arg, source, context, scope)
        operational = operational_command(self, name, arg, source)
        if operational is not None:
            return operational
        if name == "/focus":
            if arg:
                self.config.update(focus=arg[:2000])
            return (Message().line("✅ Focus saved" if arg else "🎯 Focus", "BOLD")
                    .line(c["focus"] or "Not set — processing disabled")
                    .line("/focus <what matters to the group> replaces this text.", "ITALIC").build())
        if name == "/exclude":
            if arg:
                self.config.update(exclude="" if arg.lower() == "none" else arg[:2000])
            return (Message().line("✅ Exclusions saved" if arg else "🚫 Exclusions", "BOLD")
                    .line(c["exclude"] or "None").line("/exclude <topics to skip> replaces this list; /exclude none clears it.", "ITALIC").build())
        if name == "/models":
            if arg and (not arg.isascii() or not arg.isdigit() or len(arg) > 2 or not int(arg)):
                return notice("⚠️ Use /models or /models 2")
            page = int(arg or 1)
            models = self.pool.status()
            if page > max(1, (len(models) + 4) // 5):
                return notice("⚠️ No such model page.")
            message = Message().line("🤖 Models", "BOLD")
            for m in models[(page - 1) * 5:page * 5]:
                state = "✅" if m["configured"] and m["enabled"] else "❌"
                if m["cooldown_seconds"]:
                    state = "⏳"
                message.add(state + " ").add(m["name"], "MONOSPACE").line(f" · {m['provider']}")
            next_page = f" · /models {page + 1}" if page * 5 < len(models) else ""
            return message.line("✅ Configured, not tested · /usage" + next_page, "ITALIC").build()
        if name == "/catalog":
            try:
                models = await self.pool.free_catalog()
                message = Message().line("🆓 OpenRouter free models", "BOLD")
                for model in models[:6]:
                    message.add("• ").line(model, "MONOSPACE")
                return message.line("Full list in dashboard · shared account quota", "ITALIC").build()
            except Exception:
                return notice("⚠️ Catalog unavailable", "Existing model settings are unchanged.")
        if name == "/model":
            if arg and arg not in {"auto", *(m["name"] for m in c["models"])}:
                return notice("⚠️ Unknown alias", "Use /models to see available aliases.")
            if arg:
                self.config.update(preferred_model=arg)
            group, params = sql_scope(c)
            last = self.store.one(f"""SELECT model,provider,actual_model,decision,route FROM items
                    WHERE status='sent' AND {group} ORDER BY sent_at DESC LIMIT 1""", params) if c["group_id"] else None
            return (Message().line("✅ Model saved" if arg else "🤖 Model selection", "BOLD")
                    .add("• Preferred: ").line(c["preferred_model"], "MONOSPACE")
                    .line("• Last post: " + (model_label(last, exact=True) if last else "Not recorded for this group"))
                    .line("• Fallback: " + toggle(c["fallback"]))
                    .line("/model auto or /model ALIAS · /models lists choices.", "ITALIC").build())
        if name == "/fallback":
            return boolean_setting(self, arg, "fallback", name, "🤖 Fallback",
                                   "Try other eligible free models when the preferred one is unavailable; auto already does this")
        if name == "/footer":
            if arg:
                field, _, value = arg.partition(" ")
                if field not in FOOTER_FIELDS or value not in {"on", "off"}:
                    return notice("⚠️ Use /footer FIELD on|off", "Fields: " + ", ".join(FOOTER_FIELDS))
                self.config.update(**{FOOTER_FIELDS[field][0]: value == "on"})
            m = Message().line("✅ Footer saved" if arg else "🧾 Post footer", "BOLD")
            for field, (key, label) in FOOTER_FIELDS.items():
                m.add("• ").add(field, "MONOSPACE").line(f" · {label}: " + toggle(c[key]))
            return (m.line("📰 8/30 = 8 posts sent in the rolling hour. 🎯 86 = AI relevance, not certainty.")
                    .line("Instant feed posts always include a safe source link.")
                    .line("Calls left reflect the bot’s daily cap. Changes apply to future posts.", "ITALIC").build())
        if name == "/threshold":
            if arg and (not arg.isascii() or not arg.isdigit() or len(arg) > 3 or not 0 <= int(arg) <= 100):
                return notice("⚠️ Use /threshold 0..100")
            if arg:
                self.config.update(threshold=int(arg))
            return notice(f"🎯 Threshold: {c['threshold']}/100",
                          "Minimum relevance to post. Higher means more selective; exclusions still win. Example: /threshold 85.")
        if name == "/mode":
            if not arg:
                return notice("🚦 Mode: " + c["mode"], "/mode preview reviews selections without sending; /mode live posts new eligible stories.")
            if arg not in {"preview", "live"}:
                return notice("⚠️ Use /mode preview|live", "Preview decisions stay in the dashboard.")
            self.config.update(mode=arg)
            return notice("🧪 Preview mode" if arg == "preview" else "✅ Live mode", "Existing previews are not automatically published.")
        if name in {"/pause", "/resume"}:
            self.config.update(paused=name == "/pause")
            return notice("⏸️ Paused" if c["paused"] else "✅ Resumed")
        if name == "/status":
            counts = self.store.rows("SELECT status,COUNT(*) AS n FROM items GROUP BY status")
            message = (Message().line("📰 News bot status · v" + VERSION, "BOLD")
                       .line("• Mode: " + ("🧪 Preview" if c["mode"] == "preview" else "✅ Live"))
                       .line("• Processing: " + ("⏸️ Paused" if c["paused"] else "✅ Running" if c["focus"].strip() else "⚠️ Waiting for focus"))
                       .line("• Signal: " + ("✅ Connected" if self.signal.connected else "❌ Disconnected"))
                       .add("• Preferred model: ").line(c["preferred_model"], "MONOSPACE")
                       .line("• Fallback: " + toggle(c["fallback"]))
                       .line("• Summary length: " + c["summary_length"])
                       .line(f"• Posting limit: {c['max_posts_per_hour']}/hour")
                       .line("• Emergency forwarding: " + toggle(c["emergency"]))
                       .line("• Reply cleanup: " + toggle(c["reply_cleanup"]))
                       .line("• Focus: " + ("Set · /focus to view" if c["focus"].strip() else "Not set")))
            if counts:
                message.line().line("Articles", "BOLD")
                for r in counts:
                    message.line(f"• {r['status']}: {r['n']}")
            if self.pool_error:
                message.line().line("⚠️ " + self.pool_error, "ITALIC")
            return message.build()
        return None

    async def dm_test(self, arg, source, context, scope, now):
        recipient = direct_recipient(context.get("dm_recipient"))
        at = context.get("event_at")
        if (source == "web" or not scope[0] or context.get("group") != scope[0] or not recipient
                or type(at) is not int or abs(now - at / 1000) > 600):
            return None
        if arg:
            return notice("⚠️ Use /dmtest without a recipient or any text.")
        if now - self.last_dm_test < 4 or now - self.dm_tests.get(recipient, 0) < 60:
            return None
        self.dm_tests = {key: value for key, value in self.dm_tests.items() if now - value < 60}
        self.dm_tests[recipient], self.last_dm_test = now, now
        try:
            author = await self.signal.resolve_account()
            incoming = context.get("signal_account")
            if incoming and not await self.signal.owns((incoming,)):
                return None
            if self.maintenance or scope != self.scope or await self.signal.owns((recipient,)):
                return None
            if not self.maintenance:
                await self.signal.send_dm_test(recipient, scope, author)
        except Exception:
            self.store.event("signal", "Direct message test unconfirmed; not retried automatically")
            return notice("⚠️ Direct test not confirmed. Check Signal before trying again.")
        return None

    async def process(self):
        c = self.config.data
        if c["paused"] or self.maintenance:
            return
        cutoff = time.time() - c["max_age_hours"] * 3600
        self.store.execute("UPDATE items SET status='expired' WHERE status IN ('queued','outbox','preview') AND published<?", (cutoff,))
        rules = self.config.rules_key()
        mode = c["mode"]
        self.store.execute("UPDATE items SET status='queued' WHERE status='outbox' AND route='' AND rules!=?", (rules,))
        if not c["focus"].strip():
            return
        items = self.store.rows("""SELECT i.*,f.title AS feed_title FROM items i JOIN feeds f ON f.id=i.feed_id
            WHERE i.status='queued' AND f.removed=0 AND f.enabled=1 ORDER BY i.discovered DESC,i.id DESC LIMIT ?""",
                                (c["batch_size"],))
        if not items:
            return
        stories = recent_stories(self.store, c)
        newest = latest_posts(stories)
        newest_ids = {r["id"] for r, _, _ in newest}
        older = [story for story in stories if story[0]["id"] not in newest_ids]
        frequencies = None
        eligible = []
        for item in items:
            ranked = story_ranks(item, older)
            close = best_stories(ranked, limit=10)
            if match := local_duplicate(item, "", close + newest, before_ai=True):
                self.store.update_item(item["id"], expected_status="queued", status="skipped",
                                       reason=self.repeat_reason(match), decision="")
                continue
            if len(ranked) > RELATED_POSTS and frequencies is None:
                frequencies = story_frequencies(older)
            item["related_posts"] = [story_sample(r) for r, _, _ in editor_stories(item, ranked, frequencies, len(older))]
            eligible.append(item)
        items = eligible
        if not items:
            return
        recent = [story_sample(r) for r, _, _ in newest]
        recent_ids = [sample["id"] for sample in recent]
        try:
            model, decisions = await self.pool.analyze(items, recent)
        except PoolUnavailable as exc:
            message = str(exc)
            if self.pool_error != message:
                self.store.event("warning", message)
            self.pool_error = message
            return
        self.pool_error = ""
        if self.config.rules_key() != rules or c["paused"] or self.maintenance or c["mode"] != mode:
            return
        provenance = self.pool.last_success
        if provenance.get("alias", model) != model:
            provenance = {}
        definition = next((m for m in c["models"] if m["name"] == model), {})
        provider = provenance.get("provider", definition.get("provider", "")) if provenance.get("alias", model) == model else definition.get("provider", "")
        actual = provenance.get("actual_model", definition.get("model", ""))
        preferences = topic_preferences(self.store, c, time.time())
        original = {item["id"]: item for item in items}
        scope = self.scope
        for d in decisions:
            item = original[d["id"]]
            shift = preference_delta(item, preferences, c["feedback_weight"])
            post = (d["post"] and d.get("on_focus") is True and not d.get("excluded") and not d.get("duplicate")
                    and max(0, min(100, d["importance"] + shift)) >= c["threshold"])
            summary = ((d["emoji"] or "📰") + " — " + d["text"]) if post else ""
            reason = d["reason"]
            if mute := muted_story(self, item, summary):
                post, summary, reason = False, "", f"🔕 Story muted · #{mute['id']}"
            elif post and (match := matches_story(item, summary, stories, newest)):
                post, summary, reason = False, "", self.repeat_reason(match)
            elif d.get("duplicate"):
                reason = "↩️ Already covered or repeated in this batch · " + reason
            elif not d.get("excluded") and d.get("on_focus") is not True:
                reason = "🎯 Outside current focus · " + reason
            status = ("preview" if mode == "preview" else "outbox") if post else "skipped"
            saved = self.store.update_item(d["id"], expected_status="queued", status=status,
                                           summary=summary, reason=reason, score=d["importance"], model=model,
                                           rules=rules, route="", quota=self.pool.footer(model, actual_model=actual),
                                           provider=provider, actual_model=actual,
                                           decision=json.dumps({"preference": shift, "threshold": c["threshold"],
                                                                 "reported": provenance.get("reported", False),
                                                                 "configured_model": definition.get("model", ""),
                                                                 "editor_context": {"recent": recent_ids, "related": [
                                                                     sample["id"] for sample in item["related_posts"]]}}))
            if saved and post and scope == self.scope:
                stories.append(({"id": item["id"], "title": item["title"], "description": item["description"],
                                  "published": item["published"],
                                  "summary": summary, "status": status},
                                terms(item["title"]), terms(summary)))

    @staticmethod
    def repeat_reason(match):
        status = {"sent": "Already covered", "uncertain": "Matching delivery unconfirmed"}
        return f"↩️ {status.get(match['status'], 'Matching story pending')} · #{match['id']}"

    def route_current(self, item):
        c = self.config.data
        route = item["route"]
        if not route:
            return item["rules"] == self.config.rules_key()
        if not in_group(item, c):
            return False
        if route == "emergency":
            return c["emergency"]
        return route == "instant" and bool(self.store.one(
            "SELECT 1 FROM feeds WHERE id=? AND instant=1 AND enabled=1 AND removed=0", (item["feed_id"],)))

    def hour_count(self):
        return self.store.one("SELECT COUNT(*) AS n FROM items WHERE status='sent' AND sent_at>?", (time.time() - 3600,))["n"]

    def live(self):
        c = self.config.data
        return not (c["paused"] or self.maintenance) and c["mode"] == "live" and c["group_id"] and self.signal.connected

    def media_enabled(self, item, kind):
        c = self.config.data
        return (c["include_" + kind + "s"] and item["route"] != "emergency"
                and (item["route"] == "instant" or item["score"] >= c[kind + "_min_score"]))

    async def fetch_photos(self, urls):
        if not urls:
            return []
        photos = await asyncio.gather(*(fetch_image(self.feeds.session, url) for url in urls))
        return await asyncio.to_thread(distinct_images, photos)

    async def fetch_clip(self, clips, page, item, files):
        if not (clips or page):
            return None
        try:
            # signal-cli and the app have distinct systemd PrivateTmp mounts.
            # Both run as the same service user and can read the private data directory.
            directory = files.enter_context(TemporaryDirectory(prefix=".relay-media-", dir=self.config.path.parent))
        except OSError:
            return None
        path = Path(directory) / "clip.mp4"
        return str(path) if await prepare_video(self.feeds.session, clips, page, item["url"], path) else None

    async def deliver(self):
        c = self.config.data
        if not self.live():
            return
        sent = self.hour_count()
        item = self.store.one("""SELECT i.* FROM items i JOIN feeds f ON f.id=i.feed_id
            WHERE i.status='outbox' AND f.removed=0 AND f.enabled=1 AND (? OR i.route<>'')
              AND (? OR (i.route='emergency' AND ?))
            ORDER BY CASE i.route WHEN 'emergency' THEN 0 WHEN 'instant' THEN 1 ELSE 2 END,i.id LIMIT 1""",
                              (bool(c["focus"].strip()), sent < c["max_posts_per_hour"], c["emergency"]))
        if not item:
            return
        if not self.route_current(item):
            self.store.update_item(item["id"], status="queued", route="")
            return
        if item["published"] < time.time() - c["max_age_hours"] * 3600:
            self.store.update_item(item["id"], status="expired")
            return
        if mute := muted_story(self, item, item["summary"]):
            self.store.update_item(item["id"], status="skipped", reason=f"🔕 Story muted · #{mute['id']}")
            return
        definition, provider = None, ""
        if not item["route"]:
            definition = next((m for m in c["models"] if m["name"] == item["model"]), None)
            provider = definition["provider"] if definition else ""
            if item["provider"] == "nvidia" or provider == "nvidia":
                self.store.update_item(item["id"], status="preview", reason="NVIDIA free access is evaluation-only; not published live")
                return
            if not definition or item["provider"] and item["provider"] != provider:
                self.store.update_item(item["id"], status="queued", reason="Model configuration changed; analysis required")
                return
            if not c["include_sources"] and publisher_boilerplate(item["summary"]):
                self.store.update_item(item["id"], status="queued", reason="Source-free rewrite required")
                return
        if item["route"] != "emergency":
            confirmed = recent_stories(self.store, c, delivery=True)
            instant = item["route"] == "instant"
            if match := matches_story(item, "" if instant else item["summary"], confirmed, latest_posts(confirmed),
                                      before_ai=instant):
                self.store.update_item(item["id"], status="skipped", reason=self.repeat_reason(match))
                return
        with ExitStack() as files:
            urls, (clips, page) = item_images(item), item_videos(item)
            photos, video = [], None
            photo_attempt = bool(self.feeds and urls and self.media_enabled(item, "image"))
            video_attempt = bool(self.feeds and (clips or page) and self.media_enabled(item, "video"))
            if photo_attempt or video_attempt:
                scope, account = self.scope, getattr(self.signal, "current_account", "")
                photos, video = await asyncio.gather(self.fetch_photos(urls if photo_attempt else ()),
                    self.fetch_clip(clips if video_attempt else (), page if video_attempt else "", item, files))
                if (scope != self.scope or account != getattr(self.signal, "current_account", "")
                        or not self.live() or not self.route_current(item)
                        or item["published"] < time.time() - c["max_age_hours"] * 3600):
                    return
                if (item != self.store.one("SELECT * FROM items WHERE id=?", (item["id"],))
                        or not self.store.one("SELECT 1 FROM feeds WHERE id=? AND enabled=1 AND removed=0", (item["feed_id"],))):
                    return
                if mute := muted_story(self, item, item["summary"]):
                    self.store.update_item(item["id"], status="skipped", reason=f"🔕 Story muted · #{mute['id']}")
                    return
                sent = self.hour_count()
                if sent >= c["max_posts_per_hour"]:
                    return
                if definition is not None:
                    definition = next((m for m in c["models"] if m["name"] == item["model"] and m["provider"] == provider), None)
                    if not definition:
                        return
            media = {"photos_found": len(urls), "photos_attempted": photo_attempt,
                     "photos_rejected": len(urls) - len(photos) if photo_attempt else 0,
                     "video_found": bool(clips or page), "video_attempted": video_attempt,
                     "video_rejected": video_attempt and not video}
            photos = photos if self.media_enabled(item, "image") else []
            video = video if self.media_enabled(item, "video") else None
            await self.send_news(item, c, definition, sent, photos, video, media)

    async def send_news(self, item, c, definition, sent, photos, video, media):
        attachments = ([video] if video else []) + photos
        message = render_news(item, c, self.pool, definition, sent)
        rendered = recorded_message(message, c["footer_score"] and not item["route"], bool(photos), c["footer_age"])
        media = media | {"photos_submitted": len(photos), "video_submitted": bool(video)}
        media_result = json.dumps(media) if media["photos_found"] or media["video_found"] else ""
        if not self.store.update_item(item["id"], expected_status="outbox", status="sending", media_result=media_result):
            return
        try:
            author = getattr(self.signal, "current_account", "")
            scope = self.scope
            result = await self.signal.send(message, attachment=attachments) if attachments else await self.signal.send(message)
            timestamp = result.get("timestamp", "") if isinstance(result, dict) else ""
            if not author and scope == self.scope:
                author = getattr(self.signal, "current_account", "")
            media_result = json.dumps(media | {"photos_attached": len(photos), "video_attached": bool(video)}) if media_result else ""
            self.store.update_item(item["id"], status="sent", sent_at=time.time(), signal_timestamp=str(timestamp),
                                   rendered=rendered, sent_account=author if isinstance(author, str) else "",
                                   media_result=media_result)
        except Exception as error:
            # Network timeouts/partial sends cannot prove non-delivery. Require human review.
            receipt = {}
            if getattr(error, "delivered_some", False):
                receipt = dict(sent_at=time.time(), signal_timestamp=str(error.signal_timestamp), rendered=rendered,
                               sent_account=author or getattr(self.signal, "current_account", ""))
            self.store.update_item(item["id"], status="uncertain", **receipt,
                                   reason="Signal delivery unconfirmed. Check group; dashboard can requeue or dismiss.")
            self.store.event("warning", "Signal delivery unconfirmed; item moved to uncertain, not retried automatically")

    async def run(self):
        async with asyncio.TaskGroup() as group:
            group.create_task(self._analysis_loop())
            group.create_task(self._delivery_loop())
            group.create_task(self._maintenance_loop())
            group.create_task(self._feedback_loop())
            group.create_task(self._moderation_loop())
            group.create_task(self._expansion_loop())

    async def _expansion_loop(self):
        while True:
            job = await self.expansions.get()
            try:
                await process_expansion(self, *job)
            except Exception:
                self.store.event("warning", "Web details could not be completed")
            finally:
                self.expansions.task_done()

    async def _maintenance_loop(self):
        while True:
            try:
                self.maintain()
            except Exception as exc:
                self.store.event("error", "Local retention failed: " + type(exc).__name__)
            await asyncio.sleep(60)

    def maintain(self):
        now = time.time()
        self.cleanup_previews = {key: value for key, value in self.cleanup_previews.items() if now - value[0] <= 300}
        self.feed_previews = {key: value for key, value in self.feed_previews.items() if now - value[0] <= 300}
        self.purge_previews = {key: value for key, value in self.purge_previews.items() if now - value[2] <= 300}
        if not self.maintenance and self.store.maintenance_due("article-cleanup", 3600):
            n = self.store.cleanup(self.config.data)
            self.store.mark_maintenance("article-cleanup")
            if n:
                self.store.event("cleanup", f"Automatically removed {n} eligible local article records; Signal messages untouched")

    @property
    def maintenance(self):
        return (self.config.path.parent / "maintenance").exists()

    async def _analysis_loop(self):
        while True:
            try:
                await self.process()
            except Exception as exc:
                self.store.event("error", "Worker iteration failed: " + type(exc).__name__)
            await asyncio.sleep(10 if not self.pool_error else 30)

    async def _delivery_loop(self):
        while True:
            try:
                await self.deliver()
            except Exception as exc:
                self.store.event("error", "Delivery iteration failed: " + type(exc).__name__)
            await asyncio.sleep(3)

    async def _feedback_loop(self):
        while True:
            try:
                await edit_footer(self)
            except Exception as exc:
                self.store.event("error", "Feedback iteration failed: " + type(exc).__name__)
            await asyncio.sleep(4)

    async def _moderation_loop(self):
        while True:
            try:
                await process_removals(self)
            except Exception as exc:
                self.store.event("error", "Signal cleanup iteration failed: " + type(exc).__name__)
            await asyncio.sleep(2)
