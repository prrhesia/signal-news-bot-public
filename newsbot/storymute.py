"""Expiring, reversible mutes for a specific news development."""

import time

from .controls import duration
from .editorial import description_terms, feed_reports_change, local_duplicate, metadata, terms
from .formatting import Message, notice, toggle
from .moderation import DELETE_WINDOW
from .publishing import queue_post_edit

DEFAULT_MUTE = 86400
MAX_MUTES = 100


def active_mute(engine, item_id, now):
    if not engine.config.data["story_mute"]:
        return False
    return bool(engine.store.one("SELECT 1 FROM story_mutes WHERE id=? AND group_id=? AND selector=? AND expires>?",
                                 (item_id, *engine.scope, now)))


def muted_story(engine, item, summary):
    if not engine.config.data["story_mute"] or not engine.scope[0]:
        return None
    rows = engine.store.rows("""SELECT m.id,m.title,m.summary,i.published,substr(i.description,1,700) AS description
        FROM story_mutes m LEFT JOIN items i ON i.id=m.id WHERE m.group_id=? AND m.selector=? AND m.expires>?""",
                            (*engine.scope, time.time()))
    if not rows:
        return None
    title = terms(item["title"])
    for row in rows:
        old_title = terms(row["title"])
        if row["id"] == item["id"]:
            return row
        if len(title) >= 5 and title == old_title:
            if not feed_reports_change(item, row, title | description_terms(item), old_title | description_terms(row)):
                return row
            continue  # A reused headline may hide a correction or recovery in the feed.
        if match := local_duplicate(item, summary, [(row, old_title, terms(row["summary"]))]):
            return match
    return None


def editable_marker(row, now):
    return 0 <= now - row["sent_at"] < DELETE_WINDOW and metadata(row, "rendered").get("text")


def mute_post(engine, row, scope, now):
    store = engine.store
    with store.db:
        store.db.execute("DELETE FROM story_mutes WHERE expires<=?", (now,))
        count = store.one("SELECT COUNT(*) AS n FROM story_mutes WHERE group_id=? AND selector=?", scope)["n"]
        if count >= MAX_MUTES:
            return
        saved = store.db.execute("INSERT OR IGNORE INTO story_mutes VALUES(?,?,?,?,?,?)",
                                 (row["id"], *scope, row["title"][:500], row["summary"][:1200], now + DEFAULT_MUTE))
        if saved.rowcount and editable_marker(row, now):
            queue_post_edit(store, row["id"], now)


def storymute_command(engine, arg):
    if not engine.scope[0]:
        return notice("⚠️ No Signal group configured.")
    store, config, now = engine.store, engine.config, time.time()
    if not arg or arg in {"on", "off"}:
        if arg:
            config.update(story_mute=arg == "on")
        return notice("🔕 Story mute: " + toggle(config.data["story_mute"]),
                      "❌ mutes one story for 24h · /storymute list")
    action, _, value = arg.partition(" ")
    if action == "list" and (not value or value.isascii() and value.isdigit() and 0 < len(value) <= 2 and int(value)):
        page = int(value or 1)
        rows = store.rows("""SELECT id,title,expires FROM story_mutes WHERE group_id=? AND selector=?
                          AND expires>? ORDER BY expires,id LIMIT 6 OFFSET ?""", (*engine.scope, now, (page - 1) * 5))
        m = Message().line("🔕 Story mutes · " + toggle(config.data["story_mute"]), "BOLD")
        for row in rows[:5]:
            until = time.strftime("%m-%d %H:%MZ", time.gmtime(row["expires"]))
            m.add("• ").add(f"#{row['id']}", "MONOSPACE").line(f" {row['title'][:64]} · {until}")
        if not rows:
            m.line("No active mutes." if page == 1 else "No active mutes on this page.")
        if len(rows) > 5:
            m.line(f"Next: /storymute list {page + 1}", "ITALIC")
        return m.line("/storymute edit ID 2h · /storymute remove ID", "ITALIC").build()
    fields = arg.split()
    if (len(fields) >= 2 and fields[1].isascii() and fields[1].isdigit() and len(fields[1]) <= 16
            and (fields[0] == "edit" and len(fields) == 3 or fields[0] == "remove" and len(fields) == 2)):
        verb, item_id = fields[0], int(fields[1])
        params = (item_id, *engine.scope, now)
        match = "WHERE id=? AND group_id=? AND selector=? AND expires>?"
        if verb == "edit":
            try:
                seconds = duration(fields[2], {"m": 60, "h": 3600, "d": 86400}, 60, 7 * 86400)
            except ValueError:
                return notice("⚠️ Choose a duration from 1m to 7d.")
            changed = store.execute("UPDATE story_mutes SET expires=? " + match, (now + seconds, *params)).rowcount
        else:
            with store.db:
                changed = store.db.execute("DELETE FROM story_mutes " + match, params).rowcount
                row = store.one("SELECT * FROM items WHERE id=? AND status='sent'", (item_id,)) if changed else None
                if row and editable_marker(row, now):
                    queue_post_edit(store, item_id, now)
        if not changed:
            return notice("ℹ️ No active mute with that ID in this group.")
        return notice(f"✅ Story #{item_id} " + (f"muted for {fields[2]} from now" if verb == "edit" else "unmuted"),
                      "Already skipped entries are not automatically reposted.")
    return notice("🔕 Use /storymute on|off, list, edit ID 2h, or remove ID")
