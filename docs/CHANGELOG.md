# Changelog

## 1.1.0 — Continuity

- Keep strongly identified rewrites from resurfacing days later. Favor matching
  event evidence when searching older coverage, check active mutes before
  analysis, and keep distinct developments eligible. The editor must still
  judge focus fit from the primary action and feed evidence.
- Use the same rolling-hour post count for delivery, the footer and `/queue`.
  A partial receipt with a known successful recipient consumes a slot while
  remaining unconfirmed. Posts in another group/account do not consume slots.
- Recognize extensionless MP4 sources advertised with MIME parameters in
  inline feed markup and supported Nitter status pages. Submissions still use
  the same ownership, validation and size checks.
- Let 🔎 and quoted `/expand` reach opt-in search when the feed only rephrases
  the headline. Keep usable result links available when a model cannot
  produce a cited summary. See private `expansion` events for skipped or failed
  requests without exposing keys, headlines or raw responses.

## 1.0.9 — Lens

- Give the editor up to four older matches that favor uncommon evidence it can
  actually see. Keep the rolling 15 confirmed posts and existing scope limits.
  **News & history** shows which prior item IDs were supplied for a decision.
- Recognize closely spaced rewrites of a counted deployment approval or an
  outage alert when the feed supports the same actors, scope and quantities.
  A different authority, changed amount, correction or actual recovery stays
  eligible. Mutes use the same evidence rather than suppressing a whole subject.
- Show publication and recorded send times separately, and distinguish media
  selected for a send from media included in a confirmed send. Partial delivery
  remains uncertain and is never auto-retried.
- Keep saved focus, exclusions, provider choices, delivery modes, and the
  database schema unchanged. The editorial replay runs entirely offline.

## 1.0.8 — Cadence

- Keep the Signal timestamp and editable caption when the daemon reports a
  successful recipient alongside a failure. Reactions and story mutes can target
  those posts. Delivery remains uncertain and is never automatically retried.
  Schedule routine reply cleanup in the same partial-success case.
- Recognize closely spaced rewrites of the same US verdict or unclassified
  intelligence breach, including before instant forwarding and during mute checks.
- Read videos and gallery images from rich feed content alongside short teasers,
  and recognize linked Nitter full-size photos and same-status video thumbnails.
- Reuse a bounded cache of immutable editorial tokens and skip the HTML parser
  for plain-text feed titles. No changes to the database schema or provider defaults.

## 1.0.7 — Relay

- Add opt-in MP4 attachments from public feed entries and supported Nitter
  status pages. Configure with `/videos on|off` and `/videos min 0..100`.
  Recognize more same-entry galleries and show private media counts.
- Keep `/expand` and 🔎 feed-first. If enabled, run a small public search and
  at most one AI attempt for a cited quote when feed detail is missing.
- Compare richer event evidence across publishers and recheck high-confidence
  repeats before instant delivery. Distinct material developments can still post.
- Always include a safe source link on per-feed instant posts, independent
  of the editorial source setting. Global `/emergency` forwarding keeps its
  previous behavior; the former command remains an alias.
- Add `/dmtest`, an outbound-only delivery test for the requesting group member.
- Update free-provider research and add public documentation, contribution
  templates, a security policy, and credential-free GitHub CI.

## 1.0.6 — Clarity

- `/usage` restores local quota bars in one inline Signal reply. Use
  `/usage details ALIAS` for one route or the dashboard for the full view.
- Votes keep the signed relevance adjustment only, not the vote count. Later
  caption edits target the last confirmed revision; unchanged adjustments don't
  trigger an edit. The original 45-minute voting window remains.
- 🔎 or a quoted `/expand` requests a single quoted follow-up from the saved feed
  description, without editing the original or using another AI request.
- Exact syndicated repeats and high-confidence incident rewrites can be skipped
  before inference. The editor still checks whether there is a material new fact
  and whether the primary development is on focus. Decorative emoji suffixes and
  repeated variants of the same gallery photo are removed.
- Source Off applies to instant and emergency headlines as well as AI posts.
  These no-AI modes still have separate forwarding and hourly-cap rules.
- Added opt-in Requesty and Z.ai free routes. Retired routes are held without
  retrying. Upgrades preserve the preferred model, keys, focus, exclusions and votes.

## 1.0.5 — Sift

- Give the editor a rolling 15-post window plus related SQLite history. Focus fit
  must pass on its own; a high score or positive votes cannot rescue an off-focus
  item. Recognize same-office appointment rewrites and display `Already covered`
  with the earlier post ID for local matches. Distinct decisions and corrections
  can still post. Saved focus, exclusions, providers and voting data are preserved.
- `/rapemode on|off` forwards newly fetched headlines without AI or the hourly
  limit. `/feed instant ID on|off` does the same for one feed **with** the limit.
  Both default off, respect pause/preview and avoid flooding baseline/old items.
- 👍/👎 voting closes at 45 minutes. Changed tallies can edit the original caption
  again and show vote counts even when the score adjustment is capped. `/storymute`
  enables reversible, expiring ❌ mutes for rewrites of a single development.
- Attach up to four distinct photos from the same entry; skip shared or ambiguous
  media instead of borrowing from another story. Direct feed posts use text and a link.
- Use emoji bullets in `/summary`, styled labels in `/why`, a compact `/usage`
  with paged details, and a `/version` nickname plus five highlights. The posts/hour
  footer counts up, the score is bold, and age goes last. Opt-in replies that
  will be removed show `⏳ 3m`.
- Fresh configurations suggest the Workers AI Gemma alias instead of the
  retiring Qwen alias. Upgrades do not touch saved provider definitions or keys.

## 1.0.4

- Add opt-in `/replycleanup on|off`: delete the bot’s own routine replies after five
  minutes. Keep `/summary` and `/why`; never target members’ messages.
- Add manual `/purge 30m`, `/purge confirm`, and `/purge status` for every member of the
  configured group. Purge sends a Signal deletion request only for previewed bot news,
  retains SQLite history, and does not refund hourly posting slots. Local `/cleanup`
  and retention still never send a Signal deletion.
- Let eligible new image posts receive caption-only score edits. `/feedback weight 3`
  controls visible and future topic adjustments (±10 max); the original AI score
  never changes. Add `/images min 75`, using the original score.
- Catch more duplicate rewrites by normalizing action and quantity wording, including
  the 900,000-chip approval example, without changing saved focus/exclusions.
- Make `/summary` cleaner, omit purged material, and keep results inline in Signal.
- Shorten Cloudflare model labels in footers, show `187/900 calls` and `📰 8/30`,
  remove small lettering, and show duration equivalents such as `180s (3m)`.

## 1.0.3

- Give exclusions priority, separate relevance from certainty, and preserve materially
  new developments such as denials and corrections. A consequential attributed claim
  can be news without being treated as established fact.
- Retrieve related sent and pending stories from bounded seven-day SQLite history;
  guard against close repeats during analysis and again before sending. No new database
  service or additional AI call per article.
- Make `short` target 12–22 words (28 maximum); request standalone American English,
  suppress publisher boilerplate with source off, and use up to four relevant emoji
  including flags for directly involved nations.
- Add `/summary [hours]`, `/images`, confirmed `/feed add` and `/feed remove` controls,
  and byte-budgeted `/feeds` pages. Recaps cover only confirmed sends for this group.
- Show the generating provider/model and compact `⚡ 187/900`, `8/30` and `🎯 86`
  footers. Add independent hourly, score and small-lettering switches. The revised
  1.0.3 archive uses ⚡ instead of the `local` label without changing quota accounting.
- Add `/why` and opt-in 👍/👎 topic feedback. Original scores remain intact; recent,
  short text-only posts can be edited in place to acknowledge live votes.
- Shorten main help; add practical argument examples and `/usage` reset, cooldown and
  error information. `/usage details` separates AI attempt types. Dashboard sections
  fetch only the data they use, with no additional runtime dependencies.

## 1.0.2

- Add version to `/status`, `/version` and authenticated health/dashboard output.
- Organize `/help` by task, with `/help feeds`, `/help retention` and other topics.
- Add independent `/footer usage on|off`; compact italic local calls/model line and
  bold age replace verbose post quota snapshots. Detailed `/usage` remains available
  with local-cap emoji bars, reported quotas and stale-snapshot labels.
- Redesign dashboard into Overview, News, Feeds, AI providers and Settings with
  responsive local CSS, feed search and pagination, no scripts/dependencies/CDNs.
- Add `/length short|normal|detailed|auto`, `/limit`, `/feeds`, `/queue`, `/last`,
  `/errors`, refresh controls and persistent per-feed/global intervals.
- Add hourly local article retention, sender-bound cleanup preview/confirmation,
  protected pending/uncertain deliveries and 90-day URL/GUID fingerprints.
  Cleanup never deletes Signal messages, keys, settings, feeds or today's counters.
- Add root-only offline compaction and opt-in backup pruning after successful
  backups/updates, with a protected minimum snapshot count; no release deletion.
- Request lower OpenRouter reasoning effort and a 4096-token output budget while
  preserving free-only routing, cooldowns and honest 429 diagnostics. This is not a
  guarantee against congestion, quota exhaustion or reasoning-output failures.
- Make footer timing tests use explicit fixed clocks and silence synthetic diagnostic
  mock output during installer tests so it cannot be mistaken for a real provider pass.

## 1.0.1

- Native Signal command formatting: bold headings, monospace commands/model aliases,
  italic notes, bullets and status icons, with UTF-16-safe style ranges.
- Add `/footer model|age|source on|off` and `/footer` for future news sends.
  Quota reporting remains visible; unknown article times are not invented.

- Replace generic AI failure messages with safe timeout/network/output/HTTP diagnostics.
- Add `newsbotctl test-ai ALIAS`: one synthetic inference, no Signal send or persisted
  preference change; cooldowns and local caps remain enforced.
- Accept a completed leading reasoning block followed by strictly validated final
  JSON, and empty/null optional text/emoji fields on explicitly skipped decisions.
  Incomplete reasoning, missing IDs and malformed selected summaries remain rejected.
- Ask Cloudflare Qwen3 not to emit reasoning using its prompt-level `/no_think` switch.
- Add verified Cloudflare Granite Micro and Gemma 4 IDs to the free-allocation
  allowlist; document Qwen3's planned retirement. Existing models/keys are preserved.

These changes improve compatibility and diagnosis; they do not establish the actual
cause of a remote provider failure until a real credentialed test is run.

## 1.0.0

Initial release: independent RSS, AI relevance filtering, Signal group controls,
authenticated dashboard, quota snapshots and versioned deployment/backup/rollback.
