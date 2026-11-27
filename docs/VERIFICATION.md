# Release verification

## 1.1.0 Continuity — 2026-11-27

- 176 source tests passed on CPython 3.11.17 and 3.14.10 with pinned wheels.
  `tests/test_release_110.py` covers three-day rewrites and lookalike coverage
  competing for older history slots. Different authorities, counts, scopes,
  corrections, recovery and later monitoring results remain separate.
- `tests/test_hourly_counter.py` checks consecutive full and partial receipts,
  an indeterminate timeout, a different group/account, the exact rolling-hour
  edge and restart recovery. The same count drives the footer, `/queue` and cap.
- `tests/test_expansion_reliability.py` exercises the reaction and quoted
  command, one shared reservation, substantive feed details, cited results,
  link-only results when inference is unavailable, and private diagnostics.
  `tests/test_video_delivery.py` checks typed extensionless MP4 from feed
  and same-status markup and rejects unrelated media.
- The checksum-verified Lens installer seeded a populated fixture. Continuity
  preserved the configuration, synthetic account marker, checked state across
  12 tables, vote weights and edit targets. In-flight recovery produced
  `uncertain`; schema version remained 1 and SQLite integrity passed.
- Ruff, individual Bash syntax checks, pinned environment dependency checks
  and the Python 3.11 runtime dependency audit passed. All app checks were
  synthetic; no live provider, Tavily, Signal playback or VPS action.

## 1.0.9 Lens — 2026-11-25

- 156 source tests passed on CPython 3.11.17 and 3.14.10 with pinned wheels.
  The new field-derived fixtures use fictional actors and feed text. They
  catch rewrites in both orders and in one batch even if the mock editor
  selects all items. Distinct regulators, applicants, counts and outcomes,
  feed-only corrections, and actual recoveries stay eligible. Active story
  mutes suppress a later matching rewrite without blocking the follow-up.
- `tests/replay_editorial.py` against the exact Cadence package and Lens
  source: the prior dispatch was supplied for 0/3 → 3/3 competing-history
  rewrites. Mock sends of those rewrites fell from 3/3 to 0/3. Both versions
  sent both follow-ups and the unrelated in-focus development; neither sent
  the off-focus or insufficient-evidence case. Eight mock requests on both
  (37,673 → 40,739 total prompt bytes). This is a test of evidence delivery
  and guardrails, not a live comparison of providers.
- With 2,500 retained stories and the same 60-item editorial workload, both
  screened all 60 in two mock requests. Three-run median local time was
  1.02 → 1.00 seconds with peak RSS about 56–57 MiB; this is not evidence of
  faster VPS processing. Mean input grew from 11,208 to 11,809 bytes.
- The checksum-verified 1.0.8 package seeded a populated synthetic store.
  Lens preserved configuration bytes, account marker, feed routing, history,
  votes and weighting, edits, story mutes, usage, search reservation and
  media metadata. Interrupted send recovery and SQLite integrity passed.
- A rich entry with a plain teaser supplied two photos and an MP4 to mock
  Signal RPC. Mixed recipient results kept submitted counts without promoting
  them to confirmed attachments or retrying. The cited expansion fixtures
  validate supported duration forms and reject unsupported numbers.
- Ruff, per-file Bash syntax, ShellCheck, and the pinned Python 3.11 runtime
  dependency audit passed. No account, real Signal send, live MP4 playback,
  hosted inference or public Tavily query was tested for this candidate.

## 1.0.8 Cadence

- 147 source tests passed on Python 3.11.17 and 3.14.10. The Signal fixture
  covers mixed recipient outcomes, a later reaction, caption edit, mute, and
  scheduled removal of a routine reply. A rich-content fixture checks that
  both photos and a video survive an ordinary text teaser.
- Duplicate regressions compare alternate accounts in both orders, retain
  a substantive follow-up, and exercise a different editorial focus. The new
  matcher uses event vocabulary and article evidence, not named-story rules.
- A populated copy of the checksum-verified Relay fixture preserved settings,
  account marker, feeds, votes and preference, usage, edits, mute and history.
  An interrupted send became uncertain as expected. SQLite integrity passed.
- Same Python 3.11 mock workloads, Relay → Cadence: 4,200 intake rows in
  6.30 → 2.61 seconds; 60 editorial rows against 2,500 prior posts in
  0.762 → 0.635 seconds. Both needed two mock requests; mean prompt input
  fell from 11,332 to 11,208 bytes. Peak Python RSS was about 48 MiB for
  intake and 56 MiB for editorial in both versions. Timings vary; these
  are not VPS, provider, or Signal-client measurements.

## 1.0.7 Relay — 2026-11-23

- The 143-test source suite passed on CPython 3.11.17 and 3.14.10. Relay
  regressions cover media parsing, distinct galleries, video MIME/size limits,
  redirect and pause guards, mock Signal attachments, mandatory instant source
  links, repeat suppression, cited expansion with a single inference attempt,
  dashboard key redaction, and sender-only DM tests.
- The checksum-verified 1.0.6 installer generated a populated synthetic fixture.
  Upgrading it preserved configuration, feeds, history, media hints, model
  usage, votes/weights, edit IDs, story mutes, and pre-existing uncertain rows.
  Interrupted `sending` became `uncertain`, new options defaulted off, and
  SQLite `integrity_check` returned `ok`.
- In `tests/benchmark_editorial.py`, 60 synthetic items (48 rewrites and 12
  follow-ups) against 2,500 retained posts required 2 mock AI requests in
  Relay versus 10 in Clarity. This checks the local repeat filter and prompt
  plumbing, not independent editorial quality.
- Ruff, per-file Bash syntax, and the resolved Python 3.11 dependency audit
  passed. A generated H.264 MP4 passed local container validation. This does
  not verify playback in Signal, your Nitter instance, a Tavily account, or a
  live VPS upgrade.

## 1.0.6 Clarity — 2026-11-20

- The 131-test suite passes on CPython 3.11.17 and 3.14.10. It covers quick
  votes and spaced edits against the latest accepted timestamp, count-only
  changes, unconfirmed edits, the vote cutoff, quoted feed details, source
  visibility across all routes, and repeated image variants. The incident
  fixture skips a rewritten headline without a model call while retaining a
  genuine recovery. Focus flags and provider failures are tested with mocks.
- The upgrade fixture was seeded by the exact checksum-verified 1.0.5
  installer. Loading a copy with recovery enabled preserved saved config,
  feeds, queue/history, votes and learned weighting, edit IDs, story mutes,
  provider state and usage. It recovered the latest accepted edit ID, moved
  an interrupted send to `uncertain`, and passed SQLite `integrity_check`.
- Ruff, Bash syntax checks, ShellCheck, and the resolved Python 3.11 dependency
  audit passed. Pillow is installed from a pinned wheel for bounded gallery
  comparison; neither new VPS resource usage nor peak memory was measured.
- These tests do not prove that every Signal client retains reactions across
  two actual edits, or that a specific account has free model access. Use an
  approved test group for a text and photo post with two votes spaced apart,
  and keep production in preview while comparing editor decisions.

## 1.0.5 Sift — 2026-11-20

- 124 automated tests on CPython 3.11.17 and 3.14.10. Mock tests reduce the four
  appointment rewrites to one send, keep independent injunctions and a genuine
  follow-up, and reject an off-focus item despite its high simulated score.
  This exercises the decisions and guardrails, not a live model comparison.
- Fixtures cover emergency at the hourly cap, single-feed instant below the cap,
  no backlog/duplicate/scope leakage, previews and pause, vote edits 20 minutes
  apart with both full and partial RPC results, 45-minute cutoff, reversible
  story mutes, entry-specific media, caption-only edits and Signal text spans.
- A synthetic fixture generated by the checksum-verified 1.0.4 release migrated
  additively. All stored votes, tally and learned weight, edit targets, saved alias
  and credentials, account fixture, feed, queued/sent articles and usage rows were
  preserved. SQLite integrity passed. Normal retention still applies.
- Ruff, Bash syntax, ShellCheck and `pip-audit --disable-pip --no-deps -r requirements.txt`
  passed. No known vulnerabilities found in the pinned runtime set at check time.
  No new runtime packages or services were added.

| Local Python 3.11 synthetic workload | Elapsed | Peak RSS |
|---|---:|---:|
| 200 mock feeds; 4,200 items, 200 new, no feeds failed (`tests/benchmark_feeds.py`) | 6.29 s | 48 MiB |
| 2,500 retained stories; 60 analyzed, 10 mocked batches (`tests/benchmark_editorial.py`) | 0.73 s | 51 MiB |
| Four 1 MB media payloads serialized and read by a loopback mock Signal receiver in the same Python process | — | 78 MiB |

The editorial sample used ~17.6 kB of AI input per batch vs ~14.0 kB for 1.0.4;
these are not token counts. The local checksum-verified native `signal-cli` 0.14.10
was ~389 MiB installed (~113 MiB download) and ~255 MiB RSS at HTTP readiness with
no account. The local fresh Python 3.11 environment with pinned wheels and pip was
~37 MiB on disk. These are not full Debian installation or production measurements.
No live group traffic, credentialed model calls, visual Signal check, or root
VPS upgrade was performed.

## 1.0.4 update checks

- 111 automated tests on Python 3.11 and 3.14. The mock Signal tests cover
  five-minute reply cleanup and exemptions, sender-bound purge/expiry, no requests
  to a different account or group, partial deletion and restart handling, local
  history and rate-slot preservation, and filtering withdrawn news from recaps.
- The two differently worded chip-approval examples match during analysis and at
  delivery. A reversal, separate approval, different quantity or different country
  is not blocked by that rule. These are regression fixtures, not a hosted model audit.
- Tested thresholds on both sides of 75, caption-only edits without another image
  upload, weighted feedback in both directions, unchanged original score, and
  bounded recap rendering. Reviewed relevant client edit handling and signal-cli
  RPC definitions; no live edit or deletion was performed.
- A fixture made by the published 1.0.3 compact-footer release migrates additively:
  saved focus, exclusions, feed, usage, votes, account fixture and history are
  preserved. SQLite integrity, Ruff, Bash syntax, ShellCheck and dependency audit passed.
  The audit reported no known vulnerabilities in the checked runtime dependencies.

| Same local synthetic workload | 1.0.3 | 1.0.4 |
|---|---:|---:|
| 200 feed ingestion, elapsed / Python peak RSS | 5.88 s / 45.8 MiB | 5.87 s / 45.8 MiB |
| 2,500 retained stories, 60 analyzed, elapsed / Python peak RSS | 0.329 s / 49.1 MiB | 0.414 s / 49.0 MiB |
| Mean mock AI input bytes per batch | 13,924 | 14,026 |

No added runtime dependencies or services. These are local workload snapshots,
not production measurements. No live provider request, browser visual check,
or root VPS upgrade was performed.

### Compact-footer revision (still 1.0.3)

The revised archive replaces `local` in the post footer with ⚡. The counters and
provider reset logic are unchanged. The expanded 98-test suite checks native footer
rendering with and without model and age fields, while `/usage` and `/help footer`
still distinguish the bot’s allowance from provider quota.

## 1.0.3 update checks — 2026-11-19

- 97 automated tests on Python 3.11 and 3.14, with mock AI/Signal APIs and temporary
  database and backup/restore paths. The full simulated selection/delivery path
  verifies fallback from a rate-limited preferred model, the generating model in
  the footer, exclusion precedence, the score threshold, and no incorrect sends.
- A seven-variant repeat fixture yields one send across feeds and cycles, even when
  older than the old one-day context and buried under newer posts. A denial still
  passes. These fixtures verify the program’s decisions given simulated model output;
  they do not measure any hosted model’s independent editorial quality.
- Tests cover group-bound recap/quote/reactions, reaction replacement/removal and
  edit failures, image and feed validations, UTF-8 paging and UTF-16 styles,
  local quota rollover vs. provider reset reporting, dashboard access and CSRF.
- The published 1.0.2 code generated a synthetic upgrade fixture. Verified 1.0.3
  migration preserves it (settings, keys, account fixture, feeds, usage, history);
  SQLite integrity passed. Tested the previous reader on the additive schema.
  Use the normal backup-and-rollback path for an actual downgrade.
- Ruff, Bash syntax, ShellCheck and pinned dependency audit passed. Verified the
  official native signal-cli 0.14.10 checksum and localhost daemon readiness with
  an empty account directory; no live group sends, edits or inference calls.

### Local footprint

| Same synthetic workload | 1.0.2 | 1.0.3 |
|---|---:|---:|
| 200 mock feeds, total items / new queued / feed errors | 4,200 / 200 / 0 | 4,200 / 200 / 0 |
| 200 mock feeds, elapsed / Python peak RSS | 6.02 s / 45.5 MiB | 5.92 s / 45.4 MiB |
| 2,500 retained stories, 60 analyzed, elapsed / Python peak RSS | 0.014 s / 43.6 MiB | 0.371 s / 49.0 MiB |
| Synthetic AI input per batch, mean (text only) | 10,821 bytes | 13,924 bytes |
| Synthetic story/work queue DB, SQLite page bytes | 10,940,416 | 10,981,376 |

These are development workload snapshots, not production latency, token counts,
or a guarantee of VPS usage. Idle native Signal in a clean local smoke check was
~256 MiB RSS. A clean Python 3.11 environment with runtime wheels was ~42 MiB on
disk. The official native binary was ~390 MiB extracted (~114 MiB download); the
updater reuses the existing vendor binary if version and verified digest match.
Neither previous release environments nor pre-update data snapshots are deleted.

## 1.0.2 update checks

- 77 automated tests on Python 3.11 and 3.14, including temporary-path backup/restore.
- Coverage for retention-protected statuses, same-sender/expiring cleanup confirmation,
  unpreviewed-record protection, 90-day duplicate fingerprints, monotonic article IDs,
  offline compaction/integrity, current quota preservation and additive old-config defaults.
- Native compact footers/all-fields-off, version/status/topic help, local-only usage bars,
  stale quota labels, writing limits, refresh backoff/cooldown, dashboard sections,
  masked credentials, CSRF, validated preferences and backup minimum-count policy.
- Mock OpenRouter request checks lower reasoning effort, 4096-token budget, zero-price
  routing, safe 429 categorization and provider retry-after handling.
- Ruff, Bash syntax, ShellCheck and pinned dependency audit rerun for this release.
- Synthetic 200-feed ingestion rerun: 4,200 items (4,000 baseline, 200 newly queued),
  zero feed failures, 5.67 seconds locally. This is mock-network throughput, not VPS latency.
- No new runtime dependencies. GUI uses no JavaScript, remote fonts, CDNs or theme packages.
- No production Signal account/provider requests, root VPS upgrade, or browser visual
  smoke test was performed here. HTML route/security rendering tests are not a visual
  browser test. Verify native post formatting on your group after upgrading.

## 1.0.1 update checks

- 57 automated tests passed on both Python 3.11 and development Python 3.14.
- Ruff, Bash syntax, ShellCheck and pinned dependency audit passed (no known vulnerabilities).
- Native formatting/RPC payload, UTF-16 ranges, footer switches, reasoning parsing,
  safe output errors and diagnostic persisted-preference preservation covered.
- No real credentialed provider request or live native-styled Signal send was run here.
  Verify those on the VPS before resuming live news.

## Previous 1.0.0 baseline checks

- **49 automated tests** on CPython 3.11.17 (Debian 12 Python family); the suite
  also runs on the development Python 3.14 runtime.
- Python lint checks and Bash syntax checks.
- ShellCheck on all installation/administration/runtime scripts.
- systemd unit syntax verification, using temporary executable/working-directory
  substitutions because production paths do not exist on the development machine.
- `pip-audit` of pinned runtime requirements: **no known vulnerabilities reported**.
- Checksum verification and startup of the upstream prebuilt native signal-cli.
- Temporary-path backup/restore round trips, corrupt-backup rejection, preserved
  credentials/account-state fixtures/history, and paused-preview state after restore.
- Regression coverage for chunked JSON, malformed model output, free-only model-ID
  restrictions, provider-key isolation, original model provenance, preview and
  dismissal races, failed feeds, independent dispatch/delivery, singleton locking,
  schema migration, CSRF/authentication, replay controls and ambiguous sends.

## Resource measurements

| Local smoke/fixture | Result |
|---|---|
| Native Signal daemon, no registered account | ~253 MiB peak RSS |
| Empty Python bot/dashboard | ~48 MiB peak RSS |
| Synthetic 200-feed run, two ingestion rounds | 4,200 items: 4,000 baseline, 200 newly queued; zero feed errors |
| Synthetic ingestion elapsed | ~6.1 seconds |
| Synthetic ingestion Python peak RSS | ~47 MiB |

The feed benchmark uses mock network responses and small synthetic articles. It
tests the parser, bounded scheduler, duplicate handling and SQLite ingestion; it
does **not** measure real 200-feed network latency, a linked Signal account or AI
traffic. Expect roughly 350–600 MiB combined as a planning estimate, not a guarantee.

## Not verified here

- The complete root installer running against a clean Debian VPS/systemd manager.
- Your SMS/CAPTCHA/voice registration or a live Signal group send.
- Your API key entitlement, actual free quota, provider performance or model quality.
- Factual accuracy/relevance across your real news sources and focus rules.
- Future compatibility with third-party server, API, pricing or terms changes.

The installer therefore runs the regression suite on your VPS before activation,
then checks local readiness. Start in preview and perform a real end-to-end check
before enabling live. No credentials, account state, databases or caches belong in
the distribution archive.
