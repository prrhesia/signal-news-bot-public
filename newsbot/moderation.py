"""Explicit Signal deletions, separate from local article retention."""

import asyncio
import time

from .controls import duration
from .editorial import in_group, sql_scope
from .formatting import Message, duration_label, notice, toggle

DELETE_WINDOW = 86400
REPLY_TTL = 180
SESSION_TTL = 300
MAX_PURGE = 100
KEEP_REPLIES = {"/summary", "/why"}


def visible_post(item_ref="items.id"):
    return (f"NOT EXISTS (SELECT 1 FROM withdrawals w WHERE w.item_id={item_ref} "
            "AND w.state IN ('queued','sending','accepted','unconfirmed'))")


def valid_target(timestamp, now):
    if isinstance(timestamp, str) and 0 < len(timestamp) <= 19 and timestamp.isascii() and timestamp.isdigit():
        timestamp = int(timestamp)
    if type(timestamp) is int and int((now - DELETE_WINDOW) * 1000) < timestamp <= int(now * 1000):
        return timestamp
    return None


def arm_reply(store, command, result, scope, account, now=None):
    if command in KEEP_REPLIES or not scope[0] or not account:
        return
    now = time.time() if now is None else now
    stamp = valid_target(result.get("timestamp"), now) if isinstance(result, dict) else None
    if stamp:
        store.execute("""INSERT OR IGNORE INTO reply_removals(target,group_id,selector,account,due)
                         VALUES(?,?,?,?,?)""", (stamp, *scope, account, stamp / 1000 + REPLY_TTL))


def set_reply_cleanup(config, store, enabled):
    config.update(reply_cleanup=enabled)
    if not enabled:
        store.execute("DELETE FROM reply_removals")


def reply_cleanup_command(engine, arg):
    if arg not in {"", "on", "off"}:
        return notice("⚠️ Use /replycleanup on|off")
    if arg:
        set_reply_cleanup(engine.config, engine.store, arg == "on")
    return notice("🧹 Bot reply cleanup: " + toggle(engine.config.data["reply_cleanup"]),
                  f"After {duration_label(REPLY_TTL)}; keeps /summary and /why. Your messages and news stay.")


async def purge_command(engine, arg, sender, context, scope):
    if sender == "web" or not scope[0] or context.get("group") != scope[0]:
        return notice("🚫 Use /purge in the configured Signal group.")
    store, config = engine.store, engine.config.data
    group_filter, params = sql_scope(config)
    if arg == "status":
        rows = store.rows(f"""SELECT w.state,COUNT(*) AS n FROM withdrawals w JOIN items ON items.id=w.item_id
                              WHERE w.requested>=? AND {group_filter} GROUP BY w.state""",
                          (time.time() - DELETE_WINDOW, *params))
        stats = {r["state"]: r["n"] for r in rows}
        return (Message().line("🗑️ News purge · last 24h", "BOLD")
                .line(f"Accepted: {stats.get('accepted', 0)} · Pending: {stats.get('queued', 0) + stats.get('sending', 0)}")
                .line(f"Unconfirmed: {stats.get('unconfirmed', 0)} · Skipped: {stats.get('skipped', 0)}")
                .line("Accepted means Signal accepted the request; check the chat if needed.", "ITALIC").build())
    window = None
    if arg != "confirm":
        try:
            window = duration(arg, {"s": 1, "m": 60, "h": 3600}, 1, DELETE_WINDOW)
        except ValueError:
            return notice("🗑️ Preview with /purge 30m or /purge 2h",
                          "News sent in the last 24h only. Then /purge confirm · /purge status checks results.")
    try:
        account = await engine.signal.resolve_account()
    except Exception:
        return notice("⚠️ Could not verify the bot’s Signal account; nothing queued.")
    if scope != engine.scope or engine.maintenance:
        return notice("⚠️ Settings changed; start a fresh purge preview.")
    now = time.time()
    if arg == "confirm":
        preview = engine.purge_previews.pop(sender, None)
        if not preview or preview[:2] != (scope, account) or not 0 <= now - preview[2] <= SESSION_TTL:
            return notice("⚠️ No valid purge preview", "The same member must confirm within five minutes.")
        count = 0
        with store.db:
            for item_id, stamp in preview[3]:
                row = store.one("SELECT status,signal_timestamp,rules,sent_account FROM items WHERE id=?", (item_id,))
                if (not row or row["status"] != "sent" or not in_group(row, config)
                        or row["sent_account"] not in {"", account} or valid_target(row["signal_timestamp"], now) != stamp):
                    continue
                count += store.db.execute("""INSERT INTO withdrawals VALUES(?,?,?,'queued')
                    ON CONFLICT(item_id) DO UPDATE SET account=excluded.account,requested=excluded.requested,state='queued'
                    WHERE withdrawals.state='skipped'""", (item_id, account, now)).rowcount
        if count:
            store.event("purge", f"Confirmed manual Signal purge for {count} bot news posts.")
            return notice(f"🗑️ Queued {count} Signal news {'deletion' if count == 1 else 'deletions'}",
                          "Use /purge status to see results. Local history stays.")
        return notice("ℹ️ No previewed posts are still eligible; nothing queued.")

    engine.purge_previews.pop(sender, None)
    rows = store.rows(f"""SELECT id,signal_timestamp,title,summary FROM items
           WHERE status='sent' AND sent_at>=? AND sent_at<=? AND signal_timestamp!='' AND {group_filter}
           AND (sent_account='' OR sent_account=?)
           AND signal_timestamp NOT GLOB '*[^0-9]*' AND length(signal_timestamp)<=19
           AND CAST(signal_timestamp AS INTEGER)>? AND CAST(signal_timestamp AS INTEGER)<=?
           AND NOT EXISTS (SELECT 1 FROM withdrawals WHERE item_id=items.id AND state!='skipped')
           ORDER BY sent_at,id LIMIT ?""", (now - window, now, *params, account,
                                             int((now - window) * 1000), int(now * 1000), MAX_PURGE + 1))
    targets = [(r, stamp) for r in rows if (stamp := valid_target(r["signal_timestamp"], now))]
    if not targets:
        return notice("ℹ️ No eligible bot news posts in that window.")
    batch = targets[:MAX_PURGE]
    if len(engine.purge_previews) >= 100:
        engine.purge_previews.pop(next(iter(engine.purge_previews)))
    engine.purge_previews[sender] = (scope, account, now, [(r["id"], stamp) for r, stamp in batch])
    m = Message().line(f"🗑️ Remove {len(batch)} bot news {'post' if len(batch) == 1 else 'posts'} from Signal?", "BOLD")
    for row, _ in batch[:3]:
        label = " ".join((row["summary"] or row["title"] or "Untitled post").split())
        m.line("• " + (label[:100] + "…" if len(label) > 100 else label))
    if len(batch) < len(targets):
        m.line(f"Oldest {len(batch)} eligible posts; preview again for the rest.")
    elif len(batch) > 3:
        m.line(f"… and {len(batch) - 3} more.")
    return m.line("Send /purge confirm within five minutes. This can’t be undone in Signal.", "ITALIC").build()


async def process_removals(engine):
    if engine.maintenance or not engine.signal.connected:
        return
    async with engine.signal_changes:
        if engine.maintenance or not engine.signal.connected:
            return
        await remove_reply(engine)
        if not engine.maintenance and engine.signal.connected:
            await withdraw_post(engine)


async def remove_reply(engine):
    c, store = engine.config.data, engine.store
    if not c["reply_cleanup"]:
        return
    now = time.time()
    reply = store.one("SELECT * FROM reply_removals WHERE due<=? ORDER BY due LIMIT 1", (now,))
    if not reply:
        return
    scope = (reply["group_id"], reply["selector"])
    # Take the job before attempting it. A timed-out or interrupted delete
    # must not generate another message or resume under an unknown account.
    store.execute("DELETE FROM reply_removals WHERE target=? AND group_id=? AND account=?",
                  (reply["target"], reply["group_id"], reply["account"]))
    stamp = str(reply["target"])
    if (scope != engine.scope or not valid_target(reply["target"], now)
            or store.one("""SELECT 1 FROM items WHERE signal_timestamp=?
                            UNION ALL SELECT 1 FROM edit_targets WHERE timestamp=? LIMIT 1""", (stamp, stamp))):
        return
    try:
        async with asyncio.timeout(15):
            await engine.signal.remote_delete(reply["target"], scope, reply["account"])
    except Exception:
        store.event("warning", "Routine bot reply deletion unconfirmed; no automatic retry.")


async def withdraw_post(engine):
    store, now = engine.store, time.time()
    row = store.one("""SELECT items.id,items.status,items.signal_timestamp,items.rules,items.sent_account,w.account
                       FROM withdrawals w JOIN items ON items.id=w.item_id
                       WHERE w.state='queued' ORDER BY w.requested,w.item_id LIMIT 1""")
    if not row:
        return
    stamp = valid_target(row["signal_timestamp"], now)
    if (not stamp or row["status"] != "sent" or not in_group(row, engine.config.data)
            or row["sent_account"] not in {"", row["account"]}
            or store.one("SELECT 1 FROM reply_removals WHERE target=? AND group_id=? AND account=?",
                         (stamp, engine.scope[0], row["account"]))):
        store.execute("UPDATE withdrawals SET state='skipped' WHERE item_id=?", (row["id"],))
        return
    store.execute("UPDATE withdrawals SET state='sending' WHERE item_id=?", (row["id"],))
    try:
        async with asyncio.timeout(15):
            await engine.signal.remote_delete(stamp, engine.scope, row["account"])
    except Exception:
        store.execute("UPDATE withdrawals SET state='unconfirmed' WHERE item_id=?", (row["id"],))
        store.event("warning", "A manual Signal news deletion was not confirmed; check the chat. No automatic retry.")
    else:
        store.execute("UPDATE withdrawals SET state='accepted' WHERE item_id=?", (row["id"],))
