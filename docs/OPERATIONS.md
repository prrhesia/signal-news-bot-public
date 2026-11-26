# Operation, updates and recovery

## Normal commands (root on the VPS)

```bash
newsbotctl status
newsbotctl doctor
newsbotctl test-ai cloudflare-gemma
newsbotctl test-ai openrouter-free
newsbotctl logs
newsbotctl logs -f
newsbotctl restart
newsbotctl stop
newsbotctl start
newsbotctl password
newsbotctl import /absolute/path/to/feeds.opml
newsbotctl compact
newsbotctl prune-backups
newsbotctl prune-backups --confirm
```

`doctor` checks database integrity, authenticated dashboard readiness and Signal
daemon readiness, without printing API keys. It does not prove registration, live
group delivery or a provider's free-tier entitlement. Check those in preview.

`test-ai ALIAS` makes at most one inference attempt with one synthetic article,
does not send a Signal message, and does not change persisted focus/model/mode.
It consumes free-provider capacity and respects existing cooldowns/local caps.
Pause normal analysis first (`/pause`) to avoid concurrent retries while diagnosing.
Output contains safe error categories, never the API key or raw provider response.
An output-validation error is different from a network timeout, TLS error or HTTP
401/403/429. A one-article success still needs a preview test with your real feed batch.

`/footer` lists the independent switches: `model`, `usage`, `age`, `source`,
`hourly` and `score`. `/footer hourly on` shows posts **sent** in the rolling
hour, including this one (`📰 1/30` on the first post). Emergency posts may
push the numerator past the limit. `/footer score on` shows `🎯 86`
(relevance, not truth). `187/900 calls` is the bot’s remaining daily
attempts for the generating alias, not the provider balance.
Age is the last footer line and comes from the feed timestamp, not the start of
the incident. Source off omits publisher boilerplate and the link in normal
posts, **not** the fact that the actor made a claim.
The model footer shows the generating provider/model, not the preferred alias.
Cloudflare names are shortened: `Cloudflare · gemma-4-26b-a4b-it` in the footer;
`/why` and `/model` show the exact ID where available. `configured` means the provider
did not identify the model. Footer preferences otherwise affect only future sends.

### Signal commands

```text
/focus Track major cyberattacks, infrastructure outages and official responses
/focus
/exclude Opinion, minor local outages and routine announcements
/exclude none
/models
/catalog
/model groq-120b
/model auto
/fallback on
/fallback off
/threshold 75
/usage
/usage details groq-120b
/mode preview
/mode live
/pause
/resume
/status
/help
/help feeds
/version
/feeds
/feed add https://publisher.example/rss
/feed remove 12
/feed confirm
/feed instant 12 on
/queue
/last
/errors
/limit 10
/emergency on
/emergency off
/length short
/length normal
/length detailed
/length auto
/summary
/summary 12
/images on
/images min 75
/videos on
/videos min 75
/feedback on
/feedback weight 3
/why
/expand
/dmtest
/storymute on
/storymute list
/storymute edit 123 2h
/storymute remove 123
/refresh
/refresh 5m
/refresh feed 12 10m
/refresh now
/footer usage off
/footer hourly on
/replycleanup on
/purge 30m
/purge confirm
/purge status
/retention
/retention articles 7d
/retention history 30d
/cleanup preview
/cleanup confirm
```

Every member of the configured group may use these commands. Other groups and DMs
cannot control the bot. API keys, upgrades and shell commands are never available
through Signal. Commands are replay-deduplicated and must be recent (ten minutes).

Model changes apply to future analysis; already generated posts retain their original
model. With an explicit alias selected, fallback off pins it. With `auto`, all
eligible configured models remain candidates. Focus, exclusions, threshold, group,
source wording and length changes invalidate old AI outbox decisions. Excluded,
off-focus or duplicate stories are not eligible even if their scores are high. The
15-post rolling editor context is separate from the related SQLite history lookup
and never removes votes or older history. Already skipped
stories are not automatically re-analyzed; use the dashboard for individual retries.

### Instant feeds and emergency forwarding

- `/feed instant ID on|off` forwards new headlines from that feed at its usual
  RSS poll interval. `/emergency on|off` does this for all enabled feeds while on
  and bypasses the hourly cap. Both are opt-in. Global emergency takes priority.
- Direct posts use the feed headline, not an AI summary. Instant posts always link
  the safe public source, even when `/footer source off` is set. They skip
  high-confidence rewrites of recent covered events and honor enabled media
  toggles without an AI score cutoff. Global emergency stays text-only and
  uses the normal source setting; it doesn't run the similarity check.
- Both direct modes bypass editorial focus and exclusions, but respect exact
  URL/GUID dedup, active story mutes, age limits, group scoping, preview and pause.
  Reports aren't independently verified.
- Only fresh entries fetched while the route is on use it; the existing baseline
  or editorial queue is not dumped. Existing previews do not automatically go
  live. Turning a route off returns its unsent live outbox to normal review.
  Use `/emergency off` once the emergency is over. The previous command alias
  still works for existing operators.

### Recaps, feedback and feeds

- `/summary` recaps up to the last 24 hours; supply `1–168` hours to override.
  It reads only confirmed bot news sent to the configured group, from retained
  history, excluding pending or completed Signal purges. Hosted recap input is capped
  at 40 posts; if busy, paused, cooled down or out of quota, get the latest updates in
  the same compact bullet format. Recaps don’t spend hourly news slots.
- `/feedback on` enables 👍 for more related news or 👎 for less, for 45 minutes
  after each original AI post. Votes gently
  adjust future relevance decisions for up to seven days, never exclusions or
  duplicate checks. `/feedback weight 3` controls points per net vote (1–10,
  default 3). Adjustments are capped at ±10. The displayed original score is
  not changed: two net 👍 on `🎯 85` show `🎯 85 (+6)`, with no count in the footer. Changing or removing a
  reaction in the window recalculates it; existing votes stay local and are kept
  across upgrades. Changing weight does not bulk-edit historical posts.
- Reply `/why` for the original score, reason, exact model, votes and edit status.
  Eligible new text and media posts can acknowledge votes in place; edits only
  change the text or caption and never reupload attachments. Edits batch briefly, target the last accepted revision, and
  respect Signal's 24-hour and 10-edit limits. A tally that leaves the visible
  adjustment unchanged does not use an edit. An unconfirmed edit is not retried
  blindly. Older posts without a saved caption use `/why`.
  Ordinary chat and individual votes are not sent to an AI provider.
- React 🔎 or quote with `/expand` for one follow-up reply on a bot post sent in
  the last 24 hours. Saved, unused feed details go first and use no AI quota.
  If none were saved, the optional **Settings → Web details** fallback searches the
  public headline with Tavily, and makes at most one AI attempt on the snippets.
  Only the verified IDs of search results can become citation links. No chat,
  private identifiers, or article URLs are sent as the query. The fallback
  defaults off. Keep Tavily on a free plan with billing off; the bot allows
  12 search attempts per UTC day and 200 per UTC month. Details require live,
  unpaused posting. Use the command to keep your existing vote reaction.
- Send `/dmtest` without arguments in the configured group. The bot sends a
  fixed test message directly to you, even in preview, with no group success
  reply. It rejects other recipients, limits repeat requests, and never
  retries an unconfirmed send. Incoming DMs and bot-to-bot commands stay disabled.
- `/storymute on` separately enables ❌ on confirmed posts less than 24 hours old.
  It suppresses close matches of the same development for 24 hours and adds a
  `🔕 Story muted` marker when the post can still be edited. `/storymute list`
  (optionally with a page number), `/storymute edit ID 2h` (1m–7d), and
  `/storymute remove ID` manage the list. Off suspends matching without deleting
  the list. Expiry, toggle, and removal don't republish skipped items or bulk-edit
  history; mutes do not suppress every future story about the same subject.
- `/feed add URL` validates a public RSS/Atom feed and establishes a baseline;
  avoid any credential-bearing URL. `/feed remove ID` previews the name, and the
  same sender must confirm within five minutes with `/feed confirm`. It stops polling,
  cancels pending unsent stories from that feed, and preserves sent and uncertain history.
  `/feeds` packs whole rows into Signal’s 2,000 UTF-8 byte inline body.
- `/images on` adds up to four distinct photos from the news item's own feed entry.
  `/images min 75` requires an original AI score of 75 or more by default.
  Photos are capped at 1 MB each; size variants and unrelated cards are omitted.
- `/videos on` attaches one eligible MP4; `/videos min 75` sets its independent
  original AI score cutoff. On instant feeds only the toggles apply. Relay
  accepts RSS/Atom enclosures, Media RSS, and item-level video markup. If a public
  Nitter item identifies only its own status thumbnail, the bot checks that status's
  video/download link. Clips are capped at 16 MiB and downloaded just before
  sending. HLS-only streams, authenticated instances, and arbitrary embedded
  players aren't supported. Unavailable media is omitted. An unconfirmed
  Signal send is never retried as a text copy.
- The private **News & history** cards show candidates, rejected/duplicate
  photos and confirmed attachment counts. They don't show media download URLs.

### Removing messages from Signal

- `/replycleanup on` removes the bot’s own routine command replies after three
  minutes (shown as `⏳ 3m`). `/summary` and `/why` replies remain. This is off by default; `/replycleanup
  off` cancels any pending reply deletions. It never touches a member’s messages,
  ordinary chat or bot news.
- `/purge 30m` or `/purge 2h` previews recent confirmed bot news (seconds, minutes
  or hours, up to Signal’s 24-hour limit). Any member of the configured group can
  preview it. The **same member** must send `/purge confirm` within five minutes.
  The bot targets only that preview, up to 100 posts at a time. This command is
  unavailable in DMs, other groups and the dashboard.
- `/purge status` distinguishes accepted, pending, skipped and unconfirmed requests.
  Signal deletion is best effort across devices; unconfirmed requests aren’t
  automatically retried. Restart cancels unattempted purges; preview them again if
  needed. Purge leaves the local post as history for duplicate checks and does not
  give back hourly posting slots. It is separate from local `/cleanup` and retention.

## Feed errors and article states

Failed feeds are listed near the top of the dashboard with their error, failure
count and next retry. Healthy feeds keep working. Interval changes and refresh-now
preserve failed-feed backoff. Three requests run concurrently, with a bounded fetch deadline
and exponential error backoff. Default interval is 180 seconds; priority feeds may
use 60–120 seconds, background feeds longer intervals. Respect publishers' limits.

| State | Meaning |
|---|---|
| baseline | Present on a feed's first successful fetch; not automatically posted. |
| queued | Waiting for AI analysis/free capacity. |
| skipped | AI judged it irrelevant/low importance/repeated, or an operator dismissed it. |
| preview | Selected, but private to the dashboard. |
| outbox | Selected and waiting for live delivery/rate allowance. |
| sent | Signal accepted the send; not a guarantee every recipient has read/received it. |
| uncertain | A send was interrupted/partially failed; inspect the group before retrying. |
| expired | Outside the configured news-age window. |

Normal delivery requires selected, above-threshold news. Only opt-in instant
routes bypass editorial selection, and only emergency forwarding bypasses the
hourly limit. Preview, pause, maintenance and staleness guards still apply. AI relevance
does not prove truth; attribution and uncertainty should remain in every summary.

## Updating after we change the project

### Local article retention and disk space

Automatic cleanup runs hourly, including while news processing is paused. It removes
up to 1,000 eligible records per batch: baseline/skipped/expired/preview records
older than `retention_articles_days` (default 7), and sent records older than
`retention_history_days` measured from send time (default 30). Queued/outbox/sending/
uncertain deliveries are never removed by retention. Config, account state and feeds
are untouched, and **no Signal deletion RPC is made**. The messages remain in Signal.

Manual cleanup requires preview followed by confirmation from the same sender within
five minutes and with unchanged retention settings. Candidates are checked again at
confirmation; a record moved into a protected state is not deleted. Compact hashed
URL/GUID fingerprints last 90 days to prevent re-imports. Usage and event history
is kept for 30 days, replay IDs for 7 days; today's quota counters stay intact.

SQLite reuses freed pages. `newsbotctl compact` stops services, creates a recovery
snapshot, checks free disk headroom, compacts offline, and restarts the previously
running services. This is deliberately an admin command, not a group action that
could interrupt delivery. The backup itself takes space and remains separate.

Settings contains an opt-in backup policy (default disabled, 30 days, keep newest 3).
When enabled, root operations prune only recognized completed direct-child snapshots
after successful backups/updates. The newest minimum count is always retained, as are
unrecognized directories and all releases. `newsbotctl prune-backups` previews;
`newsbotctl prune-backups --confirm` applies the enabled policy. No scheduled root
daemon is added: without new backups/updates or this command, old backups remain.

### Writing length

`short`: target one tight sentence, 12–22 words (hard limit 28 words/260 characters).
`normal`: existing concise style (85 words/600 characters maximum).
`detailed`: a few supported sentences (180 words/1200 characters maximum).
`auto`: focus controls length, within the detailed hard limit; default 20–55 words.
These are feed-based summaries, not full article retrieval. Length is requested in
the original inference and validated without an extra shortening call or blind
truncation that could remove attribution/uncertainty. Over-limit output is rejected
and normal fallback/cooldown applies. Changes invalidate pending decisions.

### Update procedure

Your code and data are separate. An update does **not** replace the data directory.

After updating, test reactions on a **new** eligible bot post if needed.
Old uncertain sends without a recorded timestamp cannot be recovered by the
update. Check `/feedback`, `/storymute`, `/replycleanup`, `/images`, and
`/videos` for the saved settings. `/why` on a test post distinguishes a saved
vote from an unconfirmed edit; `/storymute list` shows whether the mute was
recorded. Routine bot replies marked `⏳ 3m` are the cleanup targets. For media,
compare the dashboard's candidate, rejected and selected counts with the
actual Signal message. Counts in a confirmed send are not proof of client
display. A feed that supplies only one photo or no playable MP4 may
legitimately produce a single photo or a text post. For a repeat, compare the
earlier and later item IDs plus their **Sent** times, not the article age in
the footer. On the later item, **Story context supplied to the editor** lists
the prior IDs actually provided for the last completed analysis. Local
pre-analysis skips and direct forwards don't have that input record.

1. Obtain the new archive from the same trusted project source; do not run an
   arbitrary archive or installer sent by a stranger. Upload the archive and its
   checksum to the VPS. Send `/pause` then `/mode preview` separately before updating.
2. Extract it into a **new directory**, not over a running installed release:

```bash
cd /root &&
  sha256sum -c signal-news-bot-1.0.9.tar.gz.sha256 &&
  mkdir newsbot-update-1.0.9 &&
  tar -xzf signal-news-bot-1.0.9.tar.gz -C newsbot-update-1.0.9 &&
  newsbotctl update /root/newsbot-update-1.0.9/signal-news-bot &&
  newsbotctl doctor &&
  cat /opt/signal-news-bot/current/VERSION
```

The updater:

1. Downloads and checksum-checks needed upstream runtimes while old services run.
2. Creates a versioned code release with its own virtual environment; installs
   prebuilt Python wheels, then runs the regression suite on the VPS's Python.
3. Stops services, takes a private checksum-protected snapshot of all application
   data, and enables maintenance mode to prevent posting during the switch.
4. Atomically switches the `current` code pointer, runs additive database migrations,
   starts both services, then checks readiness before clearing maintenance.
5. If activation fails, attempts to restore the pre-update code and data. Restored
   news is **paused in preview mode** so old history cannot silently produce duplicates.

Keys, focus, exclusions, subscriptions, Signal registration, queued articles,
vote history and weights are preserved on successful upgrades. Already queued AI
outbox decisions may be checked under the new rules. New defaults do not replace an
existing model list. This is a compatibility mechanism, not a promise that every
future dependency or Signal server change can be fixed automatically.

Updating briefly stops reception/posting. Missed Signal commands outside the replay
window can be ignored; review status after an update. Do not delete releases/runtime
versions referenced by backups. No update script changes your YARR, website, firewall
or swap settings.

### Private backups and rollback

```bash
newsbotctl backup
newsbotctl backups
newsbotctl rollback BACKUP_ID --confirm
```

Rollback restores **both code and data** to that snapshot. Later settings/history
are not merged, and sends or deletions already made in Signal cannot be rolled back. The current
state is backed up first; data being replaced is also moved aside rather than
deleted. Restored configuration is paused/preview. Check the group, then explicitly
resume and choose live mode if appropriate. Signal identity/encryption state can
require manual attention after restoring old snapshots. Never run two instances
from the same Signal account data.

Backups contain API keys, dashboard password, Signal keys and article history.
They are root-private under `/var/backups/signal-news-bot`; protect any off-server
copies equally. No automatic pruning is enabled: monitor disk and retain backups
according to your needs. Prefer secure encrypted external backup storage.

## Paths and resource checks

| Path | Purpose |
|---|---|
| `/opt/signal-news-bot/current` | Symlink to active code release. |
| `/opt/signal-news-bot/releases/` | Versioned code and virtual environments. |
| `/opt/signal-news-bot/vendor/` | Checksum-verified prebuilt Signal/optional Java runtimes. |
| `/var/lib/signal-news-bot/` | Private configuration, SQLite and Signal account state. |
| `/var/backups/signal-news-bot/` | Private pre-update/manual snapshots. |

```bash
free -h
systemctl show signal-news-signal signal-news-bot -p MemoryCurrent -p MemoryPeak
du -sh /opt/signal-news-bot /var/lib/signal-news-bot /var/backups/signal-news-bot
```

First native installation is estimated at 450–650 MiB before article data. The
verified 0.14.10 binary takes about 389 MiB and the local Python 3.11 environment
with runtime wheels and pip took 37 MiB, before app code and Debian prerequisites.
Budget at least 1 GiB free for updates, plus headroom for additional snapshots and
data. The old release stays, and a new vendor version needs its download and
extracted binary during staging. Every retained release/backup adds disk usage.
Article history follows local retention. Old release directories and backups
are separate; monitor them and retain rollback paths.
Service limits are 512 MiB for native Signal and 256 MiB for Python, not reservations
or guarantees. Hitting them can restart a service. Existing swap use means your VPS
needs observation under real load. We do not change kernel tuning or add swap.

### Optional public HTTPS dashboard

Keep the app bound to loopback. On an **existing Caddy installation**, with a
dedicated subdomain pointing to the VPS, a site block can be:

```caddyfile
newsbot.example.com {
    reverse_proxy 127.0.0.1:8787
}
```

The app still requires its random password. Validate/reload your proxy normally.
Do not expose Signal RPC port 7583 (it is unauthenticated), or expose dashboard port
8787 over public unencrypted HTTP. Use SSH or HTTPS, and consider proxy login limits.

### Native compatibility fallback

Native signal-cli is an upstream experimental build. If necessary, free additional
RAM first, then install a JVM-backed release:

```bash
SIGNAL_RUNTIME=jvm newsbotctl update /root/newsbot-update/signal-news-bot
```

This downloads a private Java 25 runtime, without changing system Java. Signal's
JVM heap is 256 MiB and total process cap 768 MiB; this fallback needs more headroom.
Default installs/updates select native. Signal tooling must stay current with
server changes; releases older than roughly three months can stop working.
