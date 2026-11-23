# Install Signal News Bot

This guide is for a **fresh** VPS setup. If the bot is already installed, use
the [update procedure](OPERATIONS.md#update-procedure) instead; don't register the
Signal account again. See the [project overview](../README.md) for the design and scope.

## Requirements and resources

- **Debian 12+ x86_64 with systemd**, root/sudo and outbound HTTPS access.
- A **dedicated number you control** that can receive SMS or a verification call.
  Direct VPS registration below requires no second smartphone.
- At least one eligible free-tier API account. Start with Groq, Cloudflare and
  OpenRouter; see [provider signup instructions](PROVIDERS.md).
- Local ports 8787 (dashboard) and 7583 (Signal RPC) must be unused.

Measured fresh native runtime and Python environment: about **427 MiB disk before**
app data and Debian prerequisites. Budget **450–650 MiB** for a fresh native
install before data; leave **at least 1 GiB free plus backup/data headroom** for updates.
Local checks measured native Signal at **255 MiB RSS** without an account and Python
at **48–51 MiB** during synthetic feed and editorial workloads. Plan for roughly
**350–600 MiB combined under use**; this is an estimate, not a VPS measurement.
Measure current available RAM and swap use rather than relying on historical readings.
Hard service caps are 512 MiB Signal + 256 MiB Python; these are not reservations.

## 1. Copy the archive to your VPS

Download the versioned install archive and matching checksum from GitHub
Releases, not the automatic **Source code** archive. **On your computer**, run
this from the download folder and replace the destination with your VPS address:

```bash
scp signal-news-bot-1.0.7.tar.gz signal-news-bot-1.0.7.tar.gz.sha256 root@YOUR_VPS_IP:~/
```

**On the VPS, as root:**

```bash
sha256sum -c signal-news-bot-1.0.7.tar.gz.sha256 &&
  mkdir newsbot-install-1.0.7 &&
  tar -xzf signal-news-bot-1.0.7.tar.gz -C newsbot-install-1.0.7 &&
  cd newsbot-install-1.0.7/signal-news-bot &&
  bash install.sh
```

The installer downloads checksum-verified prebuilt Signal tooling, installs Python
wheels in an isolated release, runs regression tests, creates a dedicated unprivileged
service account and installs systemd services. It does not alter YARR, your website,
firewall, swap or system Java. Save the random dashboard password it prints.

If an existing unrelated `newsbot` account/path/helper is detected, it refuses to
overwrite it. If installation fails, keep the error output; do not repeatedly
register your number or manually delete account data.

## 2. Register the bot directly on the VPS

**Do not use your current personal Signal number.** Primary registration can
disconnect an existing mobile installation. Keep your iPhone account untouched.

Replace the reserved example number with the dedicated number you control:

```bash
newsbotctl signal -a '+15555550123' register
newsbotctl signal -a '+15555550123' verify 'YOUR_SMS_CODE'
newsbotctl signal -a '+15555550123' updateProfile --given-name 'News Bot'
```

The helper stops the services while using the account files, then restarts them.
Do not run a second receiver/daemon against the same account directory.

### If registration requests a CAPTCHA

Open https://signalcaptchas.org/registration/generate.html in your computer's
browser. Solve it, then **right-click the Open Signal link and copy its address**
instead of opening your personal Signal app. Retry with the copied token:

```bash
newsbotctl signal -a '+15555550123' register --captcha 'signalcaptcha://YOUR_TOKEN'
newsbotctl signal -a '+15555550123' verify 'YOUR_SMS_CODE'
```

Do not share SMS codes, CAPTCHA tokens, API keys or account data in support messages.
Some numbers are rejected or rate-limited. Avoid repeated rapid attempts.

For voice verification, make the initial SMS attempt, wait at least 60 seconds,
then use `register --voice` and `verify 'YOUR_VOICE_CODE'` in the same command pattern.

**Do not run `newsbotctl link` when registering directly.** Linking is an alternative
for an account already created in a separate mobile app, not another required step.

## 3. Add the bot to the group

From your existing iPhone Signal account, add the newly registered bot number to
your group. If only admins may post, give the bot the required posting permission.
If the bot needs to accept its invitation, get its group ID in the dashboard below:

```bash
newsbotctl signal -a '+15555550123' updateGroup -g 'BASE64_GROUP_ID'
```

## 4. Open the dashboard securely

**On your computer**, keep this SSH tunnel running:

```bash
ssh -N -L 8787:127.0.0.1:8787 root@YOUR_VPS_IP
```

Open **http://127.0.0.1:8787** in your computer's browser:

Sign in as **admin** with the password the installer printed. Use
`newsbotctl password` on the VPS if you need to recover it.

The dashboard is intentionally local-only. Do not expose port 7583: Signal RPC has
no authentication. For a public authenticated HTTPS site, see [operations](OPERATIONS.md).

## 5. Configure providers, focus and RSS feeds

1. **Find Signal group IDs:** copy the intended group's base64 ID into settings.
   Account can remain blank when exactly one account is registered; otherwise select
   its number/account UUID. Save the settings.
2. **Focus:** describe the situations and importance rules. Processing waits for a
   nonempty focus. Set exclusions and threshold; the default threshold is 75/100.
3. **AI providers:** enter your private keys in the dashboard, not in Signal.
   Follow [Groq, Cloudflare and OpenRouter signup steps](PROVIDERS.md).
4. **RSS subscriptions:** upload YARR's OPML (`.xml`/`.opml`). Import is additive;
   re-importing does not duplicate subscriptions or remove existing ones.
5. Leave mode as **preview**. Wait for baseline fetching, then use **Test 5 recent
   baseline items** to test immediately. Inspect preview/skipped decisions.
6. When satisfied, switch to **live**. Existing previews remain private unless
   individually approved; newly selected stories enter the live outbox.

Example focus:

> Track major cyberattacks and infrastructure outages. Prioritize official
> confirmations, widespread effects and materially new developments. Skip routine
> announcements, opinion and repeat coverage. Treat attacker claims as claims.

Example group commands:

```text
/focus Track major cyberattacks and infrastructure outages
/models
/model groq-20b
/model auto
/usage
/pause
/resume
/status
/help
/help retention
/summary 12
/footer hourly on
/footer score on
/length normal
/images on
/images min 75
/videos on
/videos min 75
/feedback on
/feedback weight 3
/replycleanup on
/feed add https://publisher.example/rss
/refresh 5m
/retention articles 7d
/retention history 30d
/cleanup preview
```

Failed feeds appear prominently on the dashboard and retry with backoff. Healthy
feeds continue. Default polling is every three minutes; individual intervals are
editable. RSS publication lag and provider outages are outside the bot's control.

The private dashboard keeps its sections and controls. Each section loads only its
own data and uses local CSS only—no theme packages, remote assets or scripts.

## Local storage retention (never Signal message deletion)

The bot automatically cleans eligible local article records hourly (up to
1,000 per batch): unposted articles after 7 days, sent history after 30 days by
default. Configure in Settings or through `/retention`. Queued, outbox, sending and
uncertain records are protected. SQLite fingerprints prevent exact URL/GUID repeats
for 90 days; after that, undated old feed entries can be rediscovered. Today's usage
counters are preserved. Old events/usage/replay records also have bounded retention.

`/cleanup preview` followed by `/cleanup confirm` removes only previewed, still
eligible local records. **This never sends a Signal deletion.** To explicitly
remove the bot’s recent news from Signal, preview with `/purge 30m` and confirm
separately. `/replycleanup` only affects the bot’s own routine replies.
Freed SQLite pages are reused; `newsbotctl compact` takes a backup, briefly stops
services and reclaims file space offline. Separate backup pruning is opt-in through
Settings, retains at least the configured newest snapshots, and runs after successful
backups/updates. Release directories are not automatically deleted.

## Updates and troubleshooting

```bash
newsbotctl doctor
newsbotctl status
newsbotctl logs
free -h
```

**Do not reinstall from scratch for future changes.** Upload/extract the new archive
into a new directory and use:

```bash
newsbotctl update /root/newsbot-update/signal-news-bot
```

The updater snapshots state and preserves your configuration/keys/feeds/account.
See [safe updates, backups and rollback](OPERATIONS.md) for exact steps.

## Verification scope

Automated checks use temporary databases and mock HTTP endpoints; the native Signal
binary is smoke-tested without registration. They do **not** prove real provider
eligibility, factual accuracy, successful SMS registration or end-to-end group
delivery. Verify those in preview before enabling live posting.

- [Provider setup, quota semantics and current free-offer research](PROVIDERS.md)
- [Commands, feed errors, updates, backup, rollback and HTTPS](OPERATIONS.md)
- [Code layout, tests and migration rules](DEVELOPMENT.md)
