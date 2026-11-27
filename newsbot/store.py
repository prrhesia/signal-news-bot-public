import json
import hashlib
import sqlite3
import time
from contextlib import nullcontext
from datetime import datetime, timezone

from .editorial import sql_scope


class Store:
    def __init__(self, path, recover=False):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        if self.db.execute("PRAGMA user_version").fetchone()[0] > 1:
            self.db.close()
            raise ValueError("Database schema is newer than this release; restore a matching backup instead of downgrading in place")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS feeds (
          id INTEGER PRIMARY KEY, url TEXT UNIQUE, title TEXT, interval INTEGER,
          enabled INTEGER DEFAULT 1, initialized INTEGER DEFAULT 0,
          next_poll REAL DEFAULT 0, etag TEXT DEFAULT '', modified TEXT DEFAULT '',
          error TEXT DEFAULT '', failures INTEGER DEFAULT 0, checked REAL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS items (
          id INTEGER PRIMARY KEY, feed_id INTEGER, guid TEXT, url TEXT, title TEXT,
          description TEXT, published REAL, discovered REAL, status TEXT,
          summary TEXT DEFAULT '', reason TEXT DEFAULT '', score INTEGER DEFAULT 0,
          model TEXT DEFAULT '', rules TEXT DEFAULT '', quota TEXT DEFAULT '',
          sent_at REAL DEFAULT 0, signal_timestamp TEXT DEFAULT '',
          UNIQUE(feed_id, guid));
        CREATE INDEX IF NOT EXISTS items_status ON items(status, discovered);
        CREATE INDEX IF NOT EXISTS items_url ON items(url);
        CREATE TABLE IF NOT EXISTS usage (
          day TEXT, model TEXT, requests INTEGER DEFAULT 0, tokens INTEGER DEFAULT 0,
          PRIMARY KEY(day, model));
        CREATE TABLE IF NOT EXISTS model_state (
          model TEXT PRIMARY KEY, until REAL DEFAULT 0, error TEXT DEFAULT '',
          quota TEXT DEFAULT '{}', checked REAL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS events (
          id INTEGER PRIMARY KEY, at REAL, kind TEXT, detail TEXT);
        CREATE TABLE IF NOT EXISTS commands (id TEXT PRIMARY KEY, at REAL);
        CREATE TABLE IF NOT EXISTS seen_articles (digest TEXT PRIMARY KEY, expires REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS seen_expires ON seen_articles(expires);
        CREATE TABLE IF NOT EXISTS maintenance_state (name TEXT PRIMARY KEY, at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS item_sequence (singleton INTEGER PRIMARY KEY CHECK(singleton=1), last_id INTEGER NOT NULL);
        INSERT OR IGNORE INTO item_sequence VALUES(1,0);
        UPDATE item_sequence SET last_id=MAX(last_id,COALESCE((SELECT MAX(id) FROM items),0)) WHERE singleton=1;
        CREATE INDEX IF NOT EXISTS feeds_due ON feeds(enabled,next_poll);
        CREATE INDEX IF NOT EXISTS items_delivery ON items(status,sent_at);
        CREATE INDEX IF NOT EXISTS items_signal_timestamp ON items(signal_timestamp) WHERE signal_timestamp!='';
        CREATE TABLE IF NOT EXISTS topic_votes (
          item_id INTEGER, voter TEXT, vote INTEGER NOT NULL, at INTEGER NOT NULL,
          PRIMARY KEY(item_id,voter));
        CREATE INDEX IF NOT EXISTS topic_votes_at ON topic_votes(at);
        CREATE TABLE IF NOT EXISTS footer_edits (
          item_id INTEGER PRIMARY KEY, due REAL NOT NULL, edits INTEGER NOT NULL DEFAULT 0,
          shown INTEGER NOT NULL DEFAULT 0, failed INTEGER NOT NULL DEFAULT 0);
        CREATE INDEX IF NOT EXISTS footer_edits_due ON footer_edits(due) WHERE due>0;
        CREATE TABLE IF NOT EXISTS edit_targets (timestamp TEXT PRIMARY KEY, item_id INTEGER NOT NULL);
        CREATE INDEX IF NOT EXISTS edit_targets_item ON edit_targets(item_id);
        CREATE TABLE IF NOT EXISTS story_mutes (
          id INTEGER PRIMARY KEY, group_id TEXT NOT NULL, selector TEXT NOT NULL,
          title TEXT NOT NULL, summary TEXT NOT NULL, expires REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS story_mutes_scope ON story_mutes(group_id,selector,expires);
        CREATE TABLE IF NOT EXISTS reply_removals (
          target INTEGER NOT NULL, group_id TEXT NOT NULL, selector TEXT NOT NULL,
          account TEXT NOT NULL, due REAL NOT NULL, PRIMARY KEY(account,group_id,target));
        CREATE INDEX IF NOT EXISTS reply_removals_due ON reply_removals(due);
        CREATE TABLE IF NOT EXISTS withdrawals (
          item_id INTEGER PRIMARY KEY, account TEXT NOT NULL, requested REAL NOT NULL, state TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS withdrawals_queued ON withdrawals(requested,item_id) WHERE state='queued';
        CREATE TABLE IF NOT EXISTS expansion_search (day TEXT PRIMARY KEY, requests INTEGER NOT NULL);
        """)
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(items)")}
        for name in ("provider", "actual_model", "image_url", "decision", "rendered", "sent_account",
                     "image_urls", "route", "video_urls", "video_page", "media_result"):
            if name not in columns:
                self.db.execute(f"ALTER TABLE items ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")
        if "expanded" not in columns:
            self.db.execute("ALTER TABLE items ADD COLUMN expanded INTEGER NOT NULL DEFAULT 0")
        feed_columns = {r[1] for r in self.db.execute("PRAGMA table_info(feeds)")}
        for name in ("removed", "revision", "instant"):
            if name not in feed_columns:
                self.db.execute(f"ALTER TABLE feeds ADD COLUMN {name} INTEGER NOT NULL DEFAULT 0")
        edit_columns = {r[1] for r in self.db.execute("PRAGMA table_info(footer_edits)")}
        if "attempted" not in edit_columns:
            self.db.execute("ALTER TABLE footer_edits ADD COLUMN attempted TEXT NOT NULL DEFAULT ''")
        if "last_confirmed" not in edit_columns:
            self.db.execute("ALTER TABLE footer_edits ADD COLUMN last_confirmed TEXT NOT NULL DEFAULT ''")
            # Predecessor stored accepted and partially delivered edit IDs together.
            # Recover the last one only if the predecessor marked that attempt accepted.
            self.db.execute("""UPDATE footer_edits SET last_confirmed=COALESCE((
                SELECT timestamp FROM edit_targets WHERE item_id=footer_edits.item_id ORDER BY rowid DESC LIMIT 1
            ),'') WHERE edits>0 AND failed=0""")
        usage_columns = {r[1] for r in self.db.execute("PRAGMA table_info(usage)")}
        for name in ("analysis", "fallback", "recap", "failed", "expand"):
            if name not in usage_columns:
                self.db.execute(f"ALTER TABLE usage ADD COLUMN {name} INTEGER NOT NULL DEFAULT 0")
        self.db.execute("PRAGMA user_version=1")
        self.db.commit()
        # A process interrupted during send may have delivered its message. Do not resend blindly.
        if recover:
            self.execute("UPDATE items SET status='uncertain', reason='Restart interrupted send; check group before retrying' WHERE status='sending'")
            self.execute("UPDATE withdrawals SET state='unconfirmed' WHERE state='sending'")
            self.execute("UPDATE withdrawals SET state='skipped' WHERE state='queued'")

    def execute(self, sql, params=()):
        cur = self.db.execute(sql, params)
        self.db.commit()
        return cur

    def rows(self, sql, params=()):
        return [dict(r) for r in self.db.execute(sql, params)]

    def one(self, sql, params=()):
        row = self.db.execute(sql, params).fetchone()
        return dict(row) if row else None

    def event(self, kind, detail):
        self.execute("INSERT INTO events(at,kind,detail) VALUES(?,?,?)", (time.time(), kind, detail[:1000]))

    def add_feed(self, url, title, interval=180):
        return self.execute("INSERT OR IGNORE INTO feeds(url,title,interval) VALUES(?,?,?)", (url, title, interval)).rowcount

    def insert_items(self, rows, images=None, transaction=True, *, route="", rules="", preview=False, videos=None):
        # One transaction per fetched feed, not an SSD fsync for every article.
        with self.db if transaction else nullcontext():
            for row in rows:
                if not self.seen_article(row[0], row[1], row[2]):
                    self.db.execute("UPDATE item_sequence SET last_id=MAX(last_id,COALESCE((SELECT MAX(id) FROM items),0))+1 WHERE singleton=1")
                    item_id = self.db.execute("SELECT last_id FROM item_sequence WHERE singleton=1").fetchone()[0]
                    state, source = row[-1], route if row[-1] == "queued" else ""
                    photos = images.get(row[2], ()) if images is not None else ()
                    clips, page = videos.get(row[2], ((), "")) if videos else ((), "")
                    if source:
                        state = "preview" if preview else "outbox"
                    reason = ("Emergency headline · no AI" if source == "emergency"
                              else "Instant feed headline · no AI" if source else "")
                    self.db.execute("""INSERT OR IGNORE INTO items(id,feed_id,guid,url,title,description,published,discovered,
                            status,image_url,image_urls,summary,reason,rules,route,video_urls,video_page)
                          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                                    (item_id, *row[:-1], state, photos[0] if photos else "",
                                      json.dumps(photos) if images is not None else "", row[3] if source else "",
                                      reason, rules if source else "", source, json.dumps(clips) if clips else "", page))

    def remove_feed(self, feed_id):
        with self.db:
            n = self.db.execute("UPDATE feeds SET removed=1,enabled=0,instant=0,revision=revision+1 WHERE id=? AND removed=0", (feed_id,)).rowcount
            if n:
                self.db.execute("""UPDATE items SET status='skipped',reason='Feed removed'
                    WHERE feed_id=? AND status IN ('queued','preview','outbox')""", (feed_id,))
        return n

    def requeue_direct(self, route, feed_id=None):
        condition = " AND feed_id=?" if feed_id is not None else ""
        params = (route, feed_id) if feed_id is not None else (route,)
        self.execute("""UPDATE items SET status='queued',route='',summary='',rules='',reason='Awaiting editorial review'
                      WHERE status='outbox' AND route=?""" + condition, params)

    def post_slots_last_hour(self, config, account="", now=None):
        if not config["group_id"]:
            return 0
        now = time.time() if now is None else now
        scope, params = sql_scope(config)
        # A mixed receipt with a successful recipient can already be visible.
        # It uses a slot without becoming a confirmed group delivery.
        return self.one(f"""SELECT COUNT(*) AS n FROM items
            WHERE status IN ('sent','uncertain') AND (status='sent' OR signal_timestamp!='')
            AND sent_at>? AND sent_at<=? AND {scope}
            AND (?='' OR sent_account='' OR sent_account=?)""", (now - 3600, now, *params, account, account))["n"]

    @staticmethod
    def article_digests(feed_id, guid, url):
        return [hashlib.sha256(value.encode()).hexdigest() for value in
                ("url:" + url, f"guid:{feed_id}:{guid}")]

    def seen_article(self, feed_id, guid, url):
        return bool(self.one("SELECT 1 FROM seen_articles WHERE digest IN (?,?) AND expires>? LIMIT 1",
                             (*self.article_digests(feed_id, guid, url), time.time())))

    def cleanup_candidates(self, config, now=None, limit=1000):
        now = time.time() if now is None else now
        return self.rows("""SELECT id,status,feed_id,guid,url FROM items WHERE (
          (status IN ('baseline','skipped','expired','preview') AND discovered<?)
          OR (status='sent' AND sent_at>0 AND sent_at<?))
          AND NOT EXISTS (SELECT 1 FROM withdrawals w WHERE w.item_id=items.id AND w.state IN ('queued','sending'))
          ORDER BY id LIMIT ?""",
                         (now - config["retention_articles_days"] * 86400,
                          now - config["retention_history_days"] * 86400, limit))

    def cleanup(self, config, ids=None, now=None):
        """Local SQLite cleanup only. This class has no Signal client or deletion RPC."""
        now = time.time() if now is None else now
        candidates = self.cleanup_candidates(config, now)
        if ids is not None:
            candidates = [r for r in candidates if r["id"] in ids]
        with self.db:
            self.db.execute("UPDATE item_sequence SET last_id=MAX(last_id,COALESCE((SELECT MAX(id) FROM items),0)) WHERE singleton=1")
            for row in candidates:
                for digest in self.article_digests(row["feed_id"], row["guid"], row["url"]):
                    self.db.execute("INSERT INTO seen_articles VALUES(?,?) ON CONFLICT(digest) DO UPDATE SET expires=MAX(expires,excluded.expires)",
                                    (digest, now + 90 * 86400))
                self.db.execute("DELETE FROM items WHERE id=?", (row["id"],))
                self.db.execute("DELETE FROM topic_votes WHERE item_id=?", (row["id"],))
                self.db.execute("DELETE FROM footer_edits WHERE item_id=?", (row["id"],))
                self.db.execute("DELETE FROM edit_targets WHERE item_id=?", (row["id"],))
                self.db.execute("DELETE FROM withdrawals WHERE item_id=?", (row["id"],))
            self.db.execute("DELETE FROM seen_articles WHERE expires<?", (now,))
            self.db.execute("DELETE FROM events WHERE at<?", (now - 30 * 86400,))
            self.db.execute("DELETE FROM commands WHERE at<?", (now - 7 * 86400,))
            self.db.execute("DELETE FROM topic_votes WHERE at<?", (int((now - 30 * 86400) * 1000),))
            self.db.execute("DELETE FROM story_mutes WHERE expires<=?", (now,))
            self.db.execute("DELETE FROM reply_removals WHERE due<?", (now - 86400,))
            # Keep today's counters: cleanup must not reset local daily request limits.
            day = datetime.fromtimestamp(now - 30 * 86400, timezone.utc).date().isoformat()
            self.db.execute("DELETE FROM usage WHERE day<?", (day,))
            self.db.execute("DELETE FROM expansion_search WHERE day<?", (day[:7] + "-01",))
        return len(candidates)

    def storage(self):
        page = self.db.execute("PRAGMA page_size").fetchone()[0]
        pages = self.db.execute("PRAGMA page_count").fetchone()[0]
        free = self.db.execute("PRAGMA freelist_count").fetchone()[0]
        return {"bytes": page * pages, "reusable_bytes": page * free}

    def compact(self):
        # Called in a worker thread using a separate connection, while the app is
        # in maintenance mode. SQLite takes the required database locks itself.
        self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.db.execute("VACUUM")

    def maintenance_due(self, name, interval):
        row = self.one("SELECT at FROM maintenance_state WHERE name=?", (name,))
        return not row or time.time() - row["at"] >= interval

    def mark_maintenance(self, name):
        self.execute("INSERT INTO maintenance_state VALUES(?,?) ON CONFLICT(name) DO UPDATE SET at=excluded.at", (name, time.time()))

    def update_item(self, item_id, expected_status=None, **values):
        allowed = {"status", "summary", "reason", "score", "model", "rules", "quota", "sent_at",
                    "signal_timestamp", "provider", "actual_model", "decision", "rendered", "sent_account",
                    "route", "media_result"}
        if not values or not set(values) <= allowed:
            raise ValueError("Invalid item fields")
        condition = " AND status=?" if expected_status else ""
        params = (*values.values(), item_id, expected_status) if expected_status else (*values.values(), item_id)
        return self.execute("UPDATE items SET " + ",".join(k + "=?" for k in values) + " WHERE id=?" + condition, params).rowcount

    def count_usage(self, name, tokens=0, request=True, kind=None):
        day = datetime.now(timezone.utc).date().isoformat()
        kinds = tuple(int(kind == k) for k in ("analysis", "fallback", "recap", "failed", "expand"))
        self.execute("""INSERT INTO usage(day,model,requests,tokens,analysis,fallback,recap,failed,expand)
           VALUES(?,?,?,?,?,?,?,?,?)
           ON CONFLICT(day,model) DO UPDATE SET requests=requests+excluded.requests,tokens=tokens+excluded.tokens,
             analysis=analysis+excluded.analysis,fallback=fallback+excluded.fallback,recap=recap+excluded.recap,
             failed=failed+excluded.failed,expand=expand+excluded.expand""", (day, name, int(request), tokens, *kinds))

    def reserve_search(self, now=None):
        day = datetime.fromtimestamp(time.time() if now is None else now, timezone.utc).date().isoformat()
        return bool(self.execute("""INSERT INTO expansion_search(day,requests) SELECT ?,1
            WHERE (SELECT COALESCE(SUM(requests),0) FROM expansion_search WHERE day>=? AND day<=?)<200
            ON CONFLICT(day) DO UPDATE SET requests=requests+1 WHERE requests<12""",
            (day, day[:7] + "-01", day)).rowcount)

    def usage(self, name):
        day = datetime.now(timezone.utc).date().isoformat()
        return self.one("SELECT * FROM usage WHERE day=? AND model=?", (day, name)) or {"requests": 0, "tokens": 0}

    def model_state(self, name):
        return self.one("SELECT * FROM model_state WHERE model=?", (name,)) or {"until": 0, "error": "", "quota": "{}", "checked": 0}

    def set_model_state(self, name, until=0, error="", quota=None):
        old = self.model_state(name)
        self.execute("""INSERT INTO model_state(model,until,error,quota,checked) VALUES(?,?,?,?,?)
          ON CONFLICT(model) DO UPDATE SET until=excluded.until,error=excluded.error,quota=excluded.quota,checked=excluded.checked""",
                     (name, until, error, json.dumps(quota if quota is not None else json.loads(old["quota"])), time.time() if quota is not None else old["checked"]))

    def close(self):
        self.db.close()
