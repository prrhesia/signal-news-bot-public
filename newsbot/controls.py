"""Group help and local operational controls; no credential display."""

import time

from .formatting import SIGNAL_INLINE_BYTES, Message, duration_label, notice
from .publishing import model_label
from .quotas import FIXED_RESET, provider_reset_line, snapshot_stale
from .version import RELEASES, VERSION


HELP_SECTIONS = [
    ("📰 Status & help", [
        ("/status", "Bot health and current settings"),
        ("/version", "Version, nickname and release highlights"),
        ("/queue", "Stories waiting and what’s holding them up"),
        ("/last", "Time and model of the last post"),
        ("/errors", "Recent bot or provider problems"),
        ("/summary [hours]", "Recap sent news. /summary = 24 hours; /summary 12 = 12 hours (1–168).")]),
    ("🎯 Focus & writing", [
        ("/focus <instructions>", "Set focus; /focus shows it"),
        ("/exclude <instructions|none>", "Set exclusions; /exclude shows them"),
        ("/threshold 0..100", "Minimum relevance to post; higher is stricter. Not a truth score."),
        ("/length short|normal|detailed|auto", "Bare /length shows the active choice")]),
    ("🚦 Posting", [
        ("/mode preview|live", "Preview never sends news automatically"),
        ("/pause", "Pause analysis and delivery; feeds still refresh"),
        ("/resume", "Resume in the current mode"),
        ("/limit 10", "Maximum posts in a rolling hour (1–300); emergency posts may exceed it"),
        ("/emergency on|off", "Emergency feed headlines without AI or the hourly cap; pause/preview still apply"),
        ("/images on|off", "Attach up to four eligible pictures from the feed"),
        ("/images min 75", "Minimum original AI score for an image (0–100)"),
        ("/videos on|off", "Attach an eligible MP4 from the entry; also works on instant feeds"),
        ("/videos min 75", "Minimum original AI score for video (0–100); instant posts have no score"),
        ("/feedback on|off", "👍 more like this · 👎 fewer; votes close 45 minutes after posting"),
        ("/feedback weight 3", "Points per vote (1–10); total adjustment capped at ±10"),
        ("/why", "Reply to a bot news post for its original score, reason, model and votes"),
        ("/expand", "React 🔎 or quote a recent bot post for feed details, or optional cited web details"),
        ("/storymute on|off", "❌ mutes a specific story for 24 hours; use /storymute list to manage")]),
    ("🤖 AI & usage", [
        ("/model <alias|auto>", "Choose a preferred model; /model also shows what wrote the last post"),
        ("/models", "Aliases and availability; /models 2 for the next page"),
        ("/fallback on|off", "Free-provider fallback; auto tries eligible models"),
        ("/usage [details ALIAS]", "Usage bars in one reply; details for one alias or all in the dashboard"),
        ("/catalog", "Sample of current OpenRouter free IDs; full list in the dashboard")]),
    ("🧾 Post footer", [
        ("/footer", "Show settings"),
        ("/footer FIELD on|off", "Fields: model, usage, age, source, hourly, score. Try /footer hourly on.")]),
    ("🔄 Feeds", [
        ("/feeds", "Feed IDs and intervals; /feeds 2 shows page 2"),
        ("/feed add URL", "Check and subscribe to a public RSS/Atom URL; no old-post flood"),
        ("/feed remove ID", "Stop polling a feed; then /feed confirm. Posted history stays."),
        ("/feed instant ID on|off", "New headlines from one feed without AI; safe source link always shown"),
        ("/refresh", "Refresh schedule and failure backoff"),
        ("/refresh 5m", "All feeds + new imports: every 5 minutes"),
        ("/refresh feed 12 10m", "Feed #12: every 10 minutes"),
        ("/refresh now", "Schedule healthy feeds; respects backoff and cooldown")]),
    ("🧹 VPS storage only", [
        ("/retention", "Storage and local retention settings"),
        ("/retention articles 7d", "Unposted records: 1–365 days"),
        ("/retention history 30d", "Sent records: 1–3650 days"),
        ("/cleanup preview", "Preview up to 1,000 eligible local deletions"),
        ("/cleanup confirm", "Confirm your preview within 5 minutes")]),
    ("💬 Signal chat", [
        ("/dmtest", "Send a fixed test message privately to the requesting group member"),
        ("/replycleanup on|off", "Delete routine bot replies after 3 minutes. Keep /summary and /why."),
        ("/purge 30m", "Preview bot news sent in the last 30 minutes; max 24h. Never targets other people’s messages."),
        ("/purge confirm", "Same member confirms within 5 minutes. Deletes from Signal, not local history."),
        ("/purge status", "See accepted, pending or unconfirmed deletions")]),
]


def help_message(topic=""):
    topics = {"status": 0, "focus": 1, "posting": 2, "ai": 3, "footer": 4, "feeds": 5, "retention": 6, "chat": 7}
    if topic and topic not in topics:
        return notice("⚠️ Help topics: status, focus, posting, ai, footer, feeds, retention, chat",
                      "Use /help for the full guide, or /help feeds for one section.")
    m = Message().line("📰 News bot command guide", "BOLD")
    if not topic:
        m.line("For details: /help status, focus, posting, ai, footer, feeds, retention or chat.", "ITALIC")
        for (title, commands) in HELP_SECTIONS:
            m.line().line(title, "BOLD")
            m.line(" · ".join(command for command, _ in commands), "MONOSPACE")
        return (m.line().line("👍 More stories like this · 👎 Fewer (when feedback is on).")
                .line("Reply /why to see the original AI score, reason and votes.")
                .line("Local /cleanup never deletes sent Signal messages.", "ITALIC").build())
    sections = [HELP_SECTIONS[topics[topic]]] if topic else HELP_SECTIONS
    for title, commands in sections:
        m.line().line(title, "BOLD")
        for command, description in commands:
            m.add("• ").line(command, "MONOSPACE").line("  " + description)
    if topic == "ai":
        m.line().line("Local counters reset at 00:00 UTC and include failures. Provider resets are separate. Reported figures may be stale or shared; they don’t guarantee capacity.", "ITALIC")
    elif topic == "footer":
        m.line().line("187/900 calls = bot’s daily allowance left, not provider balance. 📰 8/30 = posts sent in the rolling hour.", "ITALIC")
        m.line("Source off hides ordinary source links, not instant source links or essential uncertainty.", "ITALIC")
    elif topic == "retention":
        m.line().line("Cleanup never deletes sent Signal messages. Keep keys in the dashboard.", "ITALIC")
    return m.build()


def duration(value, units, low, high):
    if not 2 <= len(value) <= 11 or not value[:-1].isascii() or not value[:-1].isdigit() or value[-1] not in units:
        raise ValueError("Invalid duration")
    result = int(value[:-1]) * units[value[-1]]
    if not low <= result <= high:
        raise ValueError("Duration out of range")
    return result


def usage_message(pool, alias="", preferred=""):
    models = [m for m in pool.status() if m["configured"] and m["enabled"] and (not alias or m["name"] == alias)]
    if not models:
        if alias:
            return notice("⚠️ No enabled, configured model with that alias", "Use /usage or the dashboard.")
        return notice("📊 AI usage", "No enabled models with saved credentials.")
    if not alias:
        models.sort(key=lambda m: m["name"] != preferred)
        m = Message().line("📊 AI usage · local attempts left", "BOLD")
        for model in models[:10]:
            v = pool.quota_view(model["name"])
            filled = 6 * v["remaining"] // max(1, v["cap"])
            bar = "🟩" * filled + "⬜" * (6 - filled)
            cooldown, error = model["cooldown_seconds"], model["error"]
            if model["provider"] == "nvidia":
                state = " · preview only"
            elif model.get("retired"):
                state = " · retired"
            elif model.get("blocked"):
                state = " · blocked"
            elif cooldown:
                state = f" · retry in {cooldown:,}s" + (" (429)" if error.startswith("HTTP 429") else "")
            elif not v["remaining"]:
                state = " · local cap"
            else:
                state = " · check last error" if error else ""
            name = model["name"]
            m.add(bar + " ").add(name if len(name) <= 23 else name[:22] + "…", "MONOSPACE")
            m.line(f" {v['remaining']:,}/{v['cap']:,}{state}")
        more = f" (+{len(models) - 10})" if len(models) > 10 else ""
        return m.line("00:00 UTC reset · full usage in dashboard" + more, "ITALIC").build()
    m = Message().line("📊 AI usage · local resets 00:00 UTC", "BOLD")
    now = time.time()
    fixed_providers = set()
    unknown_groq_reset = False
    for model in models:
        v = pool.quota_view(model["name"])
        filled = min(10, max(0, int(10 * v["remaining"] / max(1, v["cap"]))))
        m.line().add("🤖 ").line(model["name"], "MONOSPACE")
        m.line("🟩" * filled + "⬜" * (10 - filled))
        m.line(f"{v['remaining']:,} / {v['cap']:,} local attempts left", "BOLD")
        q = v["quota"]
        reported = []
        if q.get("requests") is not None:
            reported.append(f"{q['requests']:,} requests" + (" (stale)" if snapshot_stale(q, "requests", v["checked"], now) else ""))
        if q.get("tokens") is not None:
            reported.append(f"{q['tokens']:,} tokens/min" + (" (stale)" if snapshot_stale(q, "tokens", v["checked"], now) else ""))
        if reported:
            m.line("🏦 Reported left: " + " · ".join(reported))
        provider = model["provider"]
        if provider in FIXED_RESET:
            fixed_providers.add(provider)
        elif q.get("requests_reset") is not None or q.get("tokens_reset") is not None:
            reset = provider_reset_line(provider, q, now)
            if reset:
                m.line(reset, "ITALIC")
        elif provider == "groq":
            unknown_groq_reset = True
        cooldown, error = model["cooldown_seconds"], model["error"]
        if model.get("retired"):
            m.line("⚠️ Retired model · replace its model ID in the dashboard.")
        elif cooldown:
            m.line(f"⏳ Retry in {duration_label(cooldown)}" + (f" · {short_ai_error(error)}" if error else ""))
        elif error:
            m.line("⚠️ Last error: " + short_ai_error(error))
        u = pool.store.usage(model["name"])
        m.line(f"Analysis {u.get('analysis', 0)} · fallback {u.get('fallback', 0)} · recap {u.get('recap', 0)} · failed {u.get('failed', 0)}",
               "ITALIC")
    for provider in FIXED_RESET:
        if provider in fixed_providers:
            m.line().line("⏱️ " + FIXED_RESET[provider], "ITALIC")
    if unknown_groq_reset:
        m.line("⏱️ Groq reset times appear when reported.", "ITALIC")
    return m.build()


def short_ai_error(error):
    if error.startswith("HTTP 429"):
        return "HTTP 429: rate limit or free capacity"
    if error.startswith("HTTP 401"):
        return "HTTP 401: credentials rejected"
    if error.startswith("HTTP 403"):
        return "HTTP 403: access denied"
    if error.startswith(("HTTP 404", "HTTP 410")):
        return error.split(";")[0] + ": model or endpoint unavailable"
    return error.split(";")[0][:105]


def feeds_message(store, arg):
    if arg and (not arg.isascii() or not arg.isdigit() or len(arg) > 5 or int(arg) < 1):
        return notice("⚠️ Use /feeds or /feeds 2")
    target = int(arg or 1)
    counts = store.one("""SELECT COUNT(*) AS total,COUNT(CASE WHEN enabled=0 THEN 1 END) AS disabled,
        COUNT(CASE WHEN enabled=1 AND error!='' THEN 1 END) AS failed FROM feeds WHERE removed=0""")
    total, disabled, failed = (counts[k] for k in ("total", "disabled", "failed"))
    title = f"🔄 Feeds · {total} total"
    stats = f"✅ {total - failed - disabled} healthy · ⚠️ {failed} errors · ⏸️ {disabled} disabled"
    page = 1
    m = Message().line(title, "BOLD").line(stats)
    header_bytes = len((title + "\n" + stats + "\n").encode())
    used = header_bytes
    for f in store.db.execute("SELECT id,title,interval,enabled,error,instant FROM feeds WHERE removed=0 ORDER BY id"):
        icon = "⏸️" if not f["enabled"] else "⚠️" if f["error"] else "✅"
        title_text = " ".join(f["title"].split())[:105]
        row = f"{icon} {'⚡ ' if f['instant'] else ''}#{f['id']} · {title_text} · {duration_label(f['interval'])}"
        if f["error"]:
            row += " · " + " ".join(f["error"].split())[:80]
        row_bytes = len(row.encode()) + 1
        if used + row_bytes + 60 > SIGNAL_INLINE_BYTES:
            if page == target:
                return m.line(f"Page {page} · Next: /feeds {page + 1}", "ITALIC").build()
            page += 1
            m = Message().line(title, "BOLD").line(stats)
            used = header_bytes
        m.line(row)
        used += row_bytes
    if page != target:
        return notice("⚠️ No such feed page", f"Last page: /feeds {page}.")
    return m.line(f"Page {page} · /feed add URL or /feed remove ID", "ITALIC").build()


def retention_message(engine):
    c = engine.config.data
    disk = engine.store.storage()
    return (Message().line("🧹 Local VPS retention", "BOLD")
            .line(f"• Unposted records: {c['retention_articles_days']} days")
            .line(f"• Sent history: {c['retention_history_days']} days")
            .line(f"• SQLite pages: {disk['bytes'] / 1048576:.1f} MiB · reusable: {disk['reusable_bytes'] / 1048576:.1f} MiB")
            .line("Automatic cleanup runs hourly, in batches of up to 1,000.", "ITALIC")
            .line("Signal messages, keys, settings, feeds and pending/uncertain deliveries are never deleted.", "ITALIC")
            .line("90-day fingerprints prevent repeated imports. Freed pages are reused; disk compaction is an admin operation.", "ITALIC").build())


def operational_command(engine, name, arg, source):
    c, store = engine.config.data, engine.store
    if name == "/version":
        nickname, highlights = RELEASES.get(VERSION, ("", ()))
        m = Message().line("📰 Signal News Bot " + VERSION + (" · " + nickname if nickname else ""), "BOLD")
        for point in highlights:
            m.line("• " + point)
        return m.build()
    if name == "/help":
        return help_message(arg.lower())
    if name == "/usage":
        if not arg:
            return usage_message(engine.pool, preferred=c["preferred_model"])
        action, _, alias = arg.partition(" ")
        if action == "details" and alias and " " not in alias:
            return usage_message(engine.pool, alias=alias)
        return notice("📊 One usage reply: /usage", "Use /usage details ALIAS for one model, or see all details in the dashboard.")
    if name == "/length":
        if arg:
            if arg not in {"short", "normal", "detailed", "auto"}:
                return notice("⚠️ Use /length short|normal|detailed|auto")
            engine.config.update(summary_length=arg)
        return notice("✍️ Summary length: " + c["summary_length"],
                      "short: one sentence · normal: concise · detailed: more facts · auto: follow focus. Try /length short.")
    if name == "/limit":
        if arg:
            if not arg.isascii() or not arg.isdigit() or len(arg) > 3 or not 1 <= int(arg) <= 300:
                return notice("⚠️ Use /limit 1..300")
            engine.config.update(max_posts_per_hour=int(arg))
        return notice(f"🚦 Maximum {c['max_posts_per_hour']} posts per rolling hour", "Use /limit 1–300, e.g. /limit 20.")
    if name == "/queue":
        m = Message().line("📥 Article queue", "BOLD")
        for r in store.rows("SELECT status,COUNT(*) AS n FROM items WHERE status IN ('queued','outbox','sending','uncertain','preview') GROUP BY status"):
            m.line(f"• {r['status']}: {r['n']}")
        m.line("• Mode: " + c["mode"])
        sent = store.one("SELECT COUNT(*) AS n FROM items WHERE status='sent' AND sent_at>?", (time.time() - 3600,))["n"]
        m.line(f"• Sent in the last hour: {sent} / {c['max_posts_per_hour']}")
        if sent >= c["max_posts_per_hour"]:
            m.line("⏳ Hourly posting limit reached")
        if c["paused"]:
            m.line("⏸️ Processing paused")
        if not c["focus"].strip():
            m.line("⚠️ Focus is not set")
        if not engine.signal.connected:
            m.line("⚠️ Signal disconnected")
        if engine.pool_error:
            m.line("⚠️ " + engine.pool_error)
        return m.line("Uncertain delivery requires manual review; it is never retried blindly.", "ITALIC").build()
    if name == "/last":
        row = store.one("SELECT sent_at,model,actual_model,provider,decision,route FROM items WHERE status='sent' ORDER BY sent_at DESC,id DESC LIMIT 1")
        if not row:
            return notice("📰 No sent post remains in local history", "Retention removes local history, not Signal messages.")
        return (Message().line("📰 Last successful post", "BOLD")
                .line(time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(row["sent_at"])))
                .line(model_label(row, exact=True), "MONOSPACE").build())
    if name == "/errors":
        m = Message().line("⚠️ Recent operational errors", "BOLD")
        rows = store.rows("SELECT * FROM events WHERE kind IN ('error','warning','model') AND (kind!='model' OR detail LIKE '%cooldown%') ORDER BY id DESC LIMIT 3")
        for row in rows:
            detail = " ".join(row["detail"].split())
            m.line("• " + time.strftime("%H:%M", time.gmtime(row["at"])) + "Z · " + (detail[:89] + "…" if len(detail) > 90 else detail))
        return m.line("No recent errors." if not rows else "More in dashboard · /feeds for feed errors", "ITALIC").build()
    if name == "/feeds":
        return feeds_message(store, arg)
    if name == "/refresh":
        if not arg:
            m = Message().line("🔄 Feed refresh schedule", "BOLD")
            for r in store.rows("SELECT interval,COUNT(*) AS n FROM feeds WHERE removed=0 GROUP BY interval ORDER BY interval LIMIT 20"):
                m.line(f"• {r['n']} feeds · every {duration_label(r['interval'])}")
            return m.line(f"New imports: {duration_label(c['poll_seconds'])}. Failed feeds keep backoff; /feeds lists IDs.", "ITALIC").build()
        if arg == "now":
            if not store.maintenance_due("refresh-now", 60):
                return notice("⏳ Refresh already requested", "Wait one minute before requesting again.")
            n = store.execute("UPDATE feeds SET next_poll=0 WHERE enabled=1 AND error='' ").rowcount
            store.mark_maintenance("refresh-now")
            return notice(f"✅ Scheduled {n} healthy feeds", "Failed feeds keep their retry backoff. Refresh does not force a news post.")
        try:
            parts = arg.split()
            if len(parts) == 1:
                interval = duration(arg, {"s": 1, "m": 60, "h": 3600}, 60, 86400)
                engine.config.update(poll_seconds=interval)
                store.execute("UPDATE feeds SET interval=?,next_poll=CASE WHEN error='' THEN MIN(next_poll,?) ELSE next_poll END WHERE removed=0", (interval, time.time() + interval))
            elif len(parts) == 3 and parts[0] == "feed" and parts[1].isascii() and parts[1].isdigit() and len(parts[1]) <= 12:
                interval = duration(parts[2], {"s": 1, "m": 60, "h": 3600}, 60, 86400)
                n = store.execute("UPDATE feeds SET interval=?,next_poll=CASE WHEN error='' THEN MIN(next_poll,?) ELSE next_poll END WHERE id=? AND removed=0", (interval, time.time() + interval, int(parts[1]))).rowcount
                if not n:
                    return notice("⚠️ Feed ID not found", "Use /feeds to see IDs.")
            else:
                raise ValueError()
        except ValueError:
            return notice("⚠️ Use /refresh 5m or /refresh feed 12 10m", "Allowed range: 1 minute to 24 hours. Use /refresh now to schedule healthy feeds.")
        return notice(f"✅ Refresh interval: {duration_label(interval)}", "Existing failure backoff is preserved.")
    if name == "/retention":
        if arg:
            try:
                field, value = arg.split()
                high = 365 if field == "articles" else 3650
                days = duration(value, {"d": 1}, 1, high)
                key = {"articles": "retention_articles_days", "history": "retention_history_days"}[field]
                engine.config.update(**{key: days})
                engine.cleanup_previews.clear()
            except (ValueError, KeyError):
                return notice("⚠️ Use /retention articles 7d or /retention history 30d")
        return retention_message(engine)
    if name == "/cleanup":
        if arg == "preview":
            ids = {r["id"] for r in store.cleanup_candidates(c)}
            engine.cleanup_previews[source] = (time.time(), ids, (c["retention_articles_days"], c["retention_history_days"]))
            return notice(f"🧹 {len(ids)} local article records eligible", "Use /cleanup confirm within 5 minutes. Signal messages and pending/uncertain deliveries are untouched. Up to 1,000 records per batch.")
        if arg == "confirm":
            preview = engine.cleanup_previews.pop(source, None)
            if not preview or time.time() - preview[0] > 300 or preview[2] != (c["retention_articles_days"], c["retention_history_days"]):
                return notice("⚠️ Run /cleanup preview first", "Confirmation must come from the same sender within 5 minutes, with unchanged retention settings.")
            n = store.cleanup(c, ids=preview[1])
            store.event("cleanup", f"Manually removed {n} eligible local article records; Signal messages untouched")
            return notice(f"✅ Removed {n} local article records", "Signal messages were not deleted. Freed database pages are reused automatically.")
        return notice("⚠️ Use /cleanup preview, then /cleanup confirm", "Local VPS records only—never Signal messages.")
    return None
