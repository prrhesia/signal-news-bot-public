"""Group recaps, feed commands and local reaction feedback."""

import hashlib
import re
import time

from .ai import PoolUnavailable, split_story_text, unique_emoji
from .editorial import LOOKBACK, in_group, local_duplicate, metadata, related, sql_scope, terms
from .expansion import feed_details, plain_details, web_details
from .feeds import signalled_feed_url
from .formatting import EXPAND_EMOJI, SIGNAL_INLINE_BYTES, Message, notice, reaction_emoji, toggle
from .moderation import DELETE_WINDOW, valid_target, visible_post
from .publishing import edited_news, model_label, queue_post_edit
from .storymute import active_mute, mute_post

VOTE_WINDOW = 45 * 60


async def feed_command(engine, arg, sender, scope):
    action, _, value = arg.partition(" ")
    if action == "instant":
        parts = value.split()
        if (len(parts) not in {1, 2} or not parts[0].isascii() or not parts[0].isdigit() or len(parts[0]) > 12
                or len(parts) == 2 and parts[1] not in {"on", "off"}):
            return notice("⚡ Use /feed instant ID on|off", "Use /feeds to find the ID.")
        feed_id = int(parts[0])
        f = engine.store.one("SELECT instant FROM feeds WHERE id=? AND removed=0", (feed_id,))
        if not f:
            return notice("⚠️ Feed not found", "Use /feeds to find its ID.")
        enabled = f["instant"]
        if len(parts) == 2:
            enabled = parts[1] == "on"
            engine.store.execute("UPDATE feeds SET instant=? WHERE id=?", (int(enabled), feed_id))
            if not enabled:
                engine.store.requeue_direct("instant", feed_id)
        return notice(f"⚡ Feed #{feed_id} instant: " + toggle(enabled),
                      "New items only · no AI · hourly cap applies")
    if action == "add" and value and engine.feeds:
        return notice(await engine.feeds.subscribe(value, lambda: not engine.maintenance and engine.scope == scope))
    if action == "remove" and value.isascii() and value.isdigit() and len(value) <= 12:
        f = engine.store.one("SELECT id,title,revision FROM feeds WHERE id=? AND removed=0", (int(value),))
        if not f:
            return notice("⚠️ Feed not found", "Use /feeds to find its ID.")
        engine.feed_previews[sender] = (time.time(), scope, f["id"], f["revision"])
        return notice(f"🗑️ Stop polling #{f['id']} · {' '.join(f['title'].split())[:150]}?",
                      "Send /feed confirm within five minutes. Posted history stays.")
    if action == "confirm" and not value:
        pending = engine.feed_previews.pop(sender, None)
        if pending and pending[1] == scope and time.time() - pending[0] <= 300:
            f = engine.store.one("SELECT id FROM feeds WHERE id=? AND revision=? AND removed=0", pending[2:])
            if f and engine.store.remove_feed(f["id"]):
                return notice(f"✅ Removed feed #{f['id']}", "History and sent Signal messages are untouched.")
        return notice("⚠️ No valid removal to confirm", "First send /feed remove ID.")
    return notice("🔄 Manage feeds", "/feed add URL · /feed remove ID · /feed confirm · /feed instant ID on|off")


async def recap_command(engine, arg, scope):
    if arg and (not arg.isascii() or not arg.isdigit() or len(arg) > 3 or not 1 <= int(arg) <= 168):
        return notice("🗞️ Use /summary or /summary 12", "Choose 1–168 hours. Default: 24.")
    if not scope[0]:
        return notice("⚠️ No Signal group configured")
    hours = int(arg or 24)
    now = time.time()
    group, params = sql_scope(engine.config.data)
    start = now - hours * 3600
    condition = f"status='sent' AND sent_at>=? AND sent_at<=? AND summary!='' AND {group} AND {visible_post()}"
    posts = engine.store.rows(f"""SELECT id,title,summary,sent_at FROM items
            WHERE {condition} ORDER BY sent_at DESC,id DESC LIMIT 41""", (start, now, *params))
    m = Message().line(f"🗞️ News recap · last {hours}h", "BOLD")
    if not posts:
        return m.line("No confirmed posts for this group remain in local history for that period.").build()
    subset, remaining = [], 14_000
    for row in posts[:40]:
        remaining -= len(row["summary"].encode())
        if remaining < 0:
            break
        subset.append(row)
    key = (scope, hours, tuple(r["id"] for r in subset), engine.config.data["include_sources"])
    cached = engine.recap_cache.get(key)
    if cached and now - cached[0] < 300:
        bullets = cached[1]
    else:
        bullets = None
        # Recaps are lower priority: one group-wide AI recap per minute, no
        # additional provider request for empty, cached or paused recaps.
        if (scope == engine.scope and not engine.config.data["paused"]
                and now - engine.last_recap_attempt >= 60 and hasattr(engine.pool, "recap")):
            engine.last_recap_attempt = now
            try:
                bullets = await engine.pool.recap([{"posted_utc": time.strftime("%m-%d %H:%M", time.gmtime(r["sent_at"])),
                                                   "text": r["summary"]} for r in subset])
            except PoolUnavailable:
                pass
        if scope != engine.scope or engine.maintenance:
            return notice("⚠️ Settings changed; request the recap again.")
        if subset:
            placeholders = ",".join("?" for _ in subset)
            still_visible = engine.store.one(f"""SELECT COUNT(*) AS n FROM items
                                                 WHERE id IN ({placeholders}) AND {visible_post()}""",
                                             tuple(r["id"] for r in subset))["n"]
            if still_visible != len(subset):
                return notice("⚠️ A post was withdrawn while preparing this recap; request it again.")
        if bullets:
            if len(engine.recap_cache) >= 12:
                engine.recap_cache.pop(next(iter(engine.recap_cache)))
            engine.recap_cache[key] = (now, bullets)
    fallback = not bullets
    if fallback:
        bullets = local_recap(subset)
    used = 0
    limited = len(posts) > len(subset) or (fallback and len(subset) > 8)
    budget = SIGNAL_INLINE_BYTES - len(m.build().encode()) - 115
    m.line()
    for bullet in bullets[:8]:
        line = "• " + recap_text(bullet)
        size = len(line.encode()) + 1
        if size > budget:
            limited = True
            continue
        m.line(line)
        budget -= size
        used += 1
    if not used:
        return m.line("See News history in the dashboard for the full posts.").build()
    if limited or engine.config.data["retention_history_days"] * 24 < hours:
        m.line().line("Based on the latest posts still in local history.", "ITALIC")
    return m.build()


def recap_text(value):
    compact = " ".join(value.split())
    compact = re.sub(r"^(?:[•*–-]|\d+[.)])\s+", "", compact)
    emojis, body = split_story_text(compact)
    embedded, body = split_story_text(body)
    return " ".join(unique_emoji(emojis + embedded)[:2] or ["📰"]) + " " + body


def local_recap(posts):
    seen = []
    for row in posts:
        if not local_duplicate(row, row["summary"], related(row, seen, summary=row["summary"])):
            seen.append((row, terms(row["title"]), terms(row["summary"])))
        if len(seen) >= 8:
            break
    return [r["summary"] for r, _, _ in seen]


def post_target(engine, timestamp):
    if type(timestamp) is not int or not 0 < timestamp < 2**63:
        return None
    rows = engine.store.rows("""SELECT * FROM items WHERE signal_timestamp=? AND status IN ('sent','uncertain')
          UNION ALL SELECT items.* FROM edit_targets JOIN items ON items.id=edit_targets.item_id
          WHERE edit_targets.timestamp=? AND status IN ('sent','uncertain') LIMIT 8""", (str(timestamp), str(timestamp)))
    return next((row for row in rows if in_group(row, engine.config.data)), None)


def vote_tally(store, item_id):
    return store.one("""SELECT COUNT(NULLIF(vote,0)) AS n, COALESCE(SUM(vote),0) AS score
                      FROM topic_votes WHERE item_id=?""", (item_id,))


def weighted_feedback(net, weight):
    return max(-10, min(10, net * weight))


def expansion_event(store, detail, *, throttle=False):
    # Reactions on ordinary group messages can be frequent. Keep ignored-event
    # diagnostics bounded without writing any Signal identifiers.
    if throttle:
        key = "expansion:" + detail
        if not store.maintenance_due(key, 60):
            return
        store.mark_maintenance(key)
    store.event("expansion", detail)


def expansion_blocker(engine, row, scope, author):
    c = engine.config.data
    if scope != engine.scope or not in_group(row, c):
        return "group or selected account changed"
    if not author or engine.signal.current_account != author or row["sent_account"] not in {"", author}:
        return "posting account is unavailable or changed"
    if not engine.live():
        if engine.maintenance:
            return "maintenance is active"
        if c["paused"]:
            return "posting is paused"
        if c["mode"] != "live":
            return "posting is in preview"
        return "Signal is disconnected"
    now = time.time()
    if not 0 <= now - row["sent_at"] < DELETE_WINDOW or not valid_target(row["signal_timestamp"], now):
        return "post is outside the 24-hour window or has no valid Signal timestamp"
    if not engine.store.one(f"""SELECT 1 FROM items WHERE id=? AND status IN ('sent','uncertain')
                                AND {visible_post()}""", (row["id"],)):
        return "post is withdrawn or no longer in sent history"
    return ""


def web_expansion_blocker(engine, row):
    c = engine.config.data
    if c["web_expand"] is not True:
        return "web fallback is off"
    if not c["tavily_key"]:
        return "no Tavily key is saved"
    if not engine.search:
        return "search client is unavailable"
    if not signalled_feed_url(row["url"]):
        return "saved feed link is ineligible for public search"
    return ""


async def quote_expansion(engine, row, author, reply):
    store = engine.store
    edit = store.one("SELECT last_confirmed FROM footer_edits WHERE item_id=?", (row["id"],))
    target = valid_target((edit or {}).get("last_confirmed"), time.time()) or row["signal_timestamp"]
    quote = plain_details(row["summary"], 180)
    try:
        await engine.signal.send(reply, quote=(target, author, quote))
    except Exception:
        expansion_event(store, "Signal did not confirm the quoted reply; it will not be retried automatically.")
    else:
        expansion_event(store, "Signal accepted the quoted details reply.")


async def send_expansion(engine, row, scope, trigger):
    store = engine.store
    author = engine.signal.current_account
    if blocker := expansion_blocker(engine, row, scope, author):
        expansion_event(store, f"{trigger} ignored: {blocker}.", throttle=True)
        return False
    added = feed_details(row)
    search_blocker = web_expansion_blocker(engine, row) if not added else ""
    search = not added and not search_blocker
    if search and engine.expansions.full():
        expansion_event(store, f"{trigger} could not queue: the web details queue is full.", throttle=True)
        return False
    # The command and reaction share a persistent reservation, including after a timeout.
    if not store.execute(f"""UPDATE items SET expanded=1 WHERE id=? AND expanded=0 AND {visible_post()}""",
                         (row["id"],)).rowcount:
        expansion_event(store, f"{trigger} ignored: a details reply was already requested.", throttle=True)
        return False
    if search:
        engine.expansions.put_nowait((row["id"], scope, author))
        expansion_event(store, f"{trigger} queued a public headline search.")
    else:
        reply = Message().line("🔎 More from the feed", "BOLD")
        for sentence in added:
            reply.line("• " + sentence)
        if added:
            expansion_event(store, f"{trigger} found additional saved feed details; no web search.")
        else:
            expansion_event(store, f"{trigger} found no additional feed details; {search_blocker}.")
            reply.line("The saved feed entry has no additional details.")
        await quote_expansion(engine, row, author, reply.build())
    return True


async def process_expansion(engine, item_id, scope, author):
    store = engine.store
    row = store.one("SELECT * FROM items WHERE id=? AND status IN ('sent','uncertain') AND expanded=1", (item_id,))
    key = engine.config.data["tavily_key"]

    def blocked():
        if not row:
            return "post is no longer in sent history"
        return (expansion_blocker(engine, row, scope, author) or web_expansion_blocker(engine, row)
                or ("Tavily key changed" if key != engine.config.data["tavily_key"] else ""))

    def record(detail):
        expansion_event(store, detail)

    if blocker := blocked():
        record(f"Queued web details canceled: {blocker}.")
        return
    reply = await web_details(engine.search, engine.pool, row["title"], key, lambda: not blocked(),
                              store.reserve_search, record, summary=row["summary"])
    async with engine.signal_changes:
        if blocker := blocked():
            record(f"Web details reply canceled: {blocker}.")
        elif reply is not None:
            await quote_expansion(engine, row, author, reply)


async def expand_command(engine, sender, context, scope):
    q = context.get("quote") or {}
    if sender == "web" or not scope[0] or context.get("group") != scope[0] or not isinstance(q, dict) or not q:
        return notice("🔎 React to a recent bot news post, or reply to it with /expand.")
    async with engine.signal_changes:
        row = post_target(engine, q.get("id"))
        if not row:
            expansion_event(engine.store, "/expand ignored: no matching post in local history.", throttle=True)
            return notice("⚠️ No matching bot post in local history")
        if (not await engine.signal.owns((q.get("author"), q.get("authorNumber"), q.get("authorUuid")))
                or scope != engine.scope):
            expansion_event(engine.store, "/expand ignored: quoted author or group could not be matched.", throttle=True)
            return notice("⚠️ No matching bot post in local history")
        if not await send_expansion(engine, row, scope, "/expand"):
            return notice("🔎 No new details reply", "Already requested, no longer eligible, or posting is paused.")
    return None


def numeric_toggle_setting(engine, arg, key, command, label, option, low, high, hint):
    c = engine.config.data
    if arg in {"on", "off"}:
        engine.config.update(**{key: arg == "on"})
    elif arg:
        field, _, value = arg.partition(" ")
        subcommand, target = option
        if (field != subcommand or len(value) > 3 or not value.isascii()
                or not value.isdigit() or not low <= int(value) <= high):
            return notice(f"⚠️ Use {command} on|off or {command} {subcommand} {low}–{high}")
        engine.config.update(**{target: int(value)})
    return notice(label + ": " + toggle(c[key]), hint.format(value=c[option[1]]))


async def why_command(engine, context, scope):
    q = context.get("quote") or {}
    if context.get("group") != scope[0] or not q:
        return notice("🎯 Reply to a bot news post with /why", "See the saved score, reason, model and group feedback.")
    row = post_target(engine, q.get("id"))
    if not row or not await engine.signal.owns((q.get("author"), q.get("authorNumber"), q.get("authorUuid"))) or scope != engine.scope:
        return notice("⚠️ No matching post in local history", "Reply to a news post sent by this bot in this group.")
    d = metadata(row, "decision")
    m = Message().line(f"🧾 Why it posted · #{row['id']}", "BOLD").line()
    if row["route"]:
        m.add("• ⚡ ").line(model_label(row), "BOLD")
    else:
        threshold = d.get("threshold", metadata(row, "rules").get("threshold"))
        m.add("• 🎯 ").add("AI relevance: ", "BOLD").add(f"{row['score']}/100", "MONOSPACE")
        if threshold is not None:
            m.add(f" · threshold {threshold}")
        if d.get("preference"):
            m.add(f" · learned {d['preference']:+d}")
        m.line().add("• 🧭 ").add("Reason: ", "BOLD").line((row["reason"] or "Not recorded")[:200])
        m.add("• 🤖 ").add("Model: ", "BOLD").line(model_label(row, exact=True)[:115])
    tally = vote_tally(engine.store, row["id"])
    now = time.time()
    if tally["n"] or engine.config.data["topic_feedback"] and not row["route"]:
        votes, net = tally["n"], tally["score"]
        state = ("open" if now < row["sent_at"] + VOTE_WINDOW else "closed") if engine.config.data["topic_feedback"] else "off"
        m.line().add("• 🗳️ ").add(f"Votes {state}: ", "BOLD")
        m.line(f"👍 {(votes + net) // 2} · 👎 {(votes - net) // 2} · "
               f"{weighted_feedback(net, engine.config.data['feedback_weight']):+d}")
    muted = active_mute(engine, row["id"], now)
    if muted:
        m.line("• 🔕 Story muted")
    if tally["n"] or muted:
        m.line("• ✏️ Footer: " + feedback_status(engine, row), "ITALIC")
    return m.line("Relevance isn’t a truth rating.", "ITALIC").build() if not row["route"] else m.build()


def feedback_status(engine, row):
    c, store = engine.config.data, engine.store
    state = store.one("SELECT due,edits,failed FROM footer_edits WHERE item_id=?", (row["id"],))
    if not store.one(f"SELECT 1 FROM items WHERE id=? AND {visible_post()}", (row["id"],)):
        return "post is part of a Signal purge"
    saved = metadata(row, "rendered")
    if not saved.get("text"):
        return "no stored editable caption"
    if time.time() - row["sent_at"] >= DELETE_WINDOW:
        return "Signal’s 24-hour edit window has ended"
    if not active_mute(engine, row["id"], time.time()) and not (c["topic_feedback"] and c["footer_score"]):
        return "feedback or the score footer is off"
    if not saved.get("score") and not c["story_mute"]:
        return "no editable score footer on this post"
    if state and state["due"] > 0:
        return "edit queued"
    if state and state["failed"]:
        return "edit was not confirmed by Signal"
    if state and state["edits"] >= 10:
        return "Signal’s edit limit was reached"
    return "last edit accepted by Signal" if state and state["edits"] else "no edit sent"


async def record_reaction(engine, sender, context, scope):
    r = context.get("reaction") or {}
    at = context.get("event_at")
    if (sender == "web" or not scope[0] or context.get("group") != scope[0]
            or not isinstance(r, dict) or not isinstance(r.get("emoji"), str)):
        return
    if type(at) is not int or at <= 0 or type(r.get("isRemove")) is not bool:
        if reaction_emoji(r["emoji"]) == EXPAND_EMOJI:
            expansion_event(engine.store, "🔎 reaction ignored: missing event timestamp or removal flag.", throttle=True)
        return
    # Wait for a confirmed edit's timestamp to be indexed before looking up
    # the very first reaction sent in response to that edit.
    async with engine.signal_changes:
        await record_post_reaction(engine, sender, scope, r, at)


async def record_post_reaction(engine, sender, scope, r, at):
    c = engine.config.data
    emoji = reaction_emoji(r["emoji"])
    expand = emoji == EXPAND_EMOJI and not r["isRemove"]
    if not (c["topic_feedback"] or c["story_mute"] or expand):
        return
    store = engine.store
    row = post_target(engine, r.get("targetSentTimestamp"))
    if not row:
        if expand:
            expansion_event(store, "🔎 reaction ignored: no matching post in local history.", throttle=True)
        return
    if not await engine.signal.owns((r.get("targetAuthor"), r.get("targetAuthorNumber"), r.get("targetAuthorUuid"))):
        if expand:
            expansion_event(store, "🔎 reaction ignored: target author could not be matched.", throttle=True)
        return
    if await engine.signal.owns((sender,)) or scope != engine.scope or engine.maintenance:
        return
    account = getattr(engine.signal, "current_account", "")
    if row["sent_account"] and row["sent_account"] != account:
        if expand:
            expansion_event(store, "🔎 reaction ignored: posting account changed.", throttle=True)
        return
    if not store.one(f"SELECT 1 FROM items WHERE id=? AND {visible_post()}", (row["id"],)):
        if expand:
            expansion_event(store, "🔎 reaction ignored: post is withdrawn or no longer in sent history.", throttle=True)
        return
    now = time.time()
    age = now - row["sent_at"]
    if emoji == "❌" and not r["isRemove"] and c["story_mute"] and 0 <= age < DELETE_WINDOW:
        mute_post(engine, row, scope, now)
    if (c["topic_feedback"] and not row["route"] and 0 <= age < VOTE_WINDOW
            and row["sent_at"] - 60 <= at / 1000 < row["sent_at"] + VOTE_WINDOW):
        record_vote(engine, row, sender, scope, r, at, now, emoji)
    if expand:
        await send_expansion(engine, row, scope, "🔎 reaction")


def record_vote(engine, row, sender, scope, reaction, at, now, emoji):
    store, item_id = engine.store, row["id"]
    vote = {"👍": 1, "👎": -1}.get(emoji, 0)
    voter = hashlib.sha256((scope[0] + ":" + sender).encode()).hexdigest()
    previous = store.one("SELECT vote,at FROM topic_votes WHERE item_id=? AND voter=?", (item_id, voter))
    if previous and previous["at"] >= at:
        return
    if reaction["isRemove"]:
        # Ignore a delayed removal of the old reaction after a replacement.
        if not vote or previous and previous["vote"] not in {0, vote}:
            return
        vote = 0
    if not previous:
        if not vote:
            return
        count = store.one("SELECT COUNT(*) AS n FROM topic_votes WHERE item_id=?", (item_id,))["n"]
        if count >= 200:
            return
    with store.db:
        store.db.execute("""INSERT INTO topic_votes VALUES(?,?,?,?) ON CONFLICT(item_id,voter)
                            DO UPDATE SET vote=excluded.vote,at=excluded.at""", (item_id, voter, vote, at))
        rendered = metadata(row, "rendered")
        if rendered.get("score") and engine.config.data["footer_score"] and vote != (previous["vote"] if previous else 0):
            queue_post_edit(store, item_id, now)


def topic_preferences(store, config, now):
    if not config["topic_feedback"]:
        return []
    rows = store.rows(f"""SELECT i.title,i.summary,i.rules,i.sent_at,SUM(v.vote) AS net
          FROM items i JOIN topic_votes v ON v.item_id=i.id WHERE i.status IN ('sent','uncertain') AND i.sent_at>?
          AND {visible_post("i.id")}
          GROUP BY i.id HAVING net!=0 ORDER BY i.sent_at DESC LIMIT 150""", (now - LOOKBACK,))
    return [(terms(row["title"] + " " + row["summary"]), row["net"], 1 - (now - row["sent_at"]) / LOOKBACK)
            for row in rows if in_group(row, config)]


def preference_delta(item, preferences, weight=3):
    tokens = terms(item["title"])
    midpoint = sum(weighted_feedback(net, weight) * age for tags, net, age in preferences
                   if len(tags & tokens) >= 2 and len(tags & tokens) / max(1, len(tokens)) >= .34)
    return weighted_feedback(round(midpoint), 1)


async def edit_footer(engine):
    async with engine.signal_changes:
        await edit_due_footer(engine)


async def edit_due_footer(engine):
    c, store, now = engine.config.data, engine.store, time.time()
    feedback = c["topic_feedback"] and c["footer_score"]
    if (engine.maintenance or c["mode"] != "live" or c["paused"] or not (feedback or c["story_mute"])
            or not engine.signal.connected):
        return
    row = store.one(f"""SELECT i.*,e.attempted,e.last_confirmed FROM footer_edits e JOIN items i ON i.id=e.item_id
          WHERE e.due>0 AND e.due<=? AND e.edits<10 AND {visible_post("i.id")}
          ORDER BY e.due LIMIT 1""", (now,))
    if not row:
        return
    item_id = row["id"]
    store.execute("UPDATE footer_edits SET due=0 WHERE item_id=?", (item_id,))
    if (not in_group(row, c) or row["status"] not in {"sent", "uncertain"} or not 0 <= now - row["sent_at"] < DELETE_WINDOW
            or row["sent_account"] not in {"", getattr(engine.signal, "current_account", "")}):
        return
    saved = metadata(row, "rendered")
    if not saved.get("text"):
        return
    tally = vote_tally(store, item_id)
    # +0 and no votes are different states. Store signed net with a nonzero
    # sentinel for tied votes so a newly tied post displays an acknowledgment.
    net = weighted_feedback(tally["score"], c["feedback_weight"])
    display = 1000 if tally["n"] and not net else net
    suffix = ""
    if tally["n"] and saved.get("score"):
        suffix = f" ({net:+d})" if net else " (±0)"
    muted = active_mute(engine, item_id, now)
    message = edited_news(saved, suffix, muted)
    digest = hashlib.sha256(message.encode()).hexdigest()
    if (row["attempted"] == digest or not row["attempted"] and not (muted or feedback and saved.get("score"))
            or len(message.encode()) > SIGNAL_INLINE_BYTES):
        return
    store.execute("UPDATE footer_edits SET edits=edits+1,attempted=? WHERE item_id=?", (digest, item_id))
    try:
        # Caption edit only. Signal clients retain original media; uploading the
        # feed image again would not reliably be the same attachment.
        result = await engine.signal.send(message, edit_timestamp=row["last_confirmed"] or row["signal_timestamp"])
    except Exception as error:
        # Keep a timestamp reported even for partial delivery: a recipient
        # who received the edit can react to it. It isn't proof of delivery
        # to everyone, and a timeout may not supply a timestamp at all.
        with store.db:
            store.db.execute("UPDATE footer_edits SET failed=1 WHERE item_id=?", (item_id,))
            if stamp := getattr(error, "signal_timestamp", None):
                store.db.execute("INSERT OR IGNORE INTO edit_targets VALUES(?,?)", (str(stamp), item_id))
        store.event("signal", "Post edit unconfirmed; the same caption will not be retried. See /why.")
        return
    with store.db:
        store.db.execute("UPDATE footer_edits SET shown=?,failed=0,last_confirmed=? WHERE item_id=?",
                         (display, str(result["timestamp"]), item_id))
        store.db.execute("INSERT OR IGNORE INTO edit_targets VALUES(?,?)", (str(result["timestamp"]), item_id))


def boolean_setting(engine, arg, key, command, label, explanation):
    if arg not in {"", "on", "off"}:
        return notice(f"⚠️ Use {command} on|off")
    if arg:
        engine.config.update(**{key: arg == "on"})
    return notice(label + ": " + toggle(engine.config.data[key]), f"{explanation} · {command} on|off")
