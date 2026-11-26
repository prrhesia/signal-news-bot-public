# Codebase and verification

Runtime code deliberately uses a small Python stack: aiohttp, feedparser,
BeautifulSoup, defusedxml, Jinja2 and Pillow. No frontend build, Node process, database
server, local LLM, Docker or coding-agent framework is required.

```text
install.sh                  Small installation entry point
VERSION                     Application release version
requirements.txt            Pinned runtime dependencies
deploy/                     Hardened systemd units
scripts/
  install-release.sh        Stage, test, snapshot, activate and verify an update
  admin-lib.sh              Shared snapshot/activation/recovery helpers
  newsbotctl                Root-only administration commands
  signal-cli-run            Release-specific Signal runtime launcher
newsbot/
  __main__.py               CLI and async task supervision
  config.py                 Private atomic configuration and defaults
  store.py                  SQLite queries and additive schema migration
  feeds.py                  Public-only RSS/OPML, article media and direct-routing intake
  media.py                  Image deduplication and bounded MP4 preparation
  expansion.py              Opt-in search and cited reply validation
  ai.py                     Provider adapters, validation, routing and quotas
  http.py                   Bounded, complete JSON response reads
  signal.py                 Signal JSON-RPC and SSE command reception
  engine.py                 Relevance decisions, outbox and independent delivery
  controls.py               Group command guide, operational controls and usage visuals
  editorial.py              Rolling and related SQLite story context, close-match checks
  interactions.py           Recaps, quoted feed/web details, votes and score-footer edits
  moderation.py             Group-scoped bot reply cleanup and confirmed Signal purge
  publishing.py             News footers, saved edit layout and caption edits
  storymute.py              Group-bound expiring exclusions for individual developments
  quotas.py                 Provider reset information and snapshot freshness
  formatting.py             Native Signal text spans using UTF-16 offsets
  version.py                Installed VERSION and Signal release highlights
  web.py, templates/        Authenticated server-rendered dashboard
  doctor.py                 Credential-safe readiness diagnostics
tests/                      Unit, HTTP integration and deployment-helper tests
docs/                       Provider, operational and development references
```

## Run checks without touching production

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
for script in install.sh scripts/*.sh scripts/newsbotctl scripts/signal-cli-run; do bash -n "$script" || break; done
.venv/bin/python -m newsbot --data ./dev-data init
.venv/bin/python -m newsbot --data ./dev-data serve --port 8787
```

Use Python 3.11+; Debian 12's Python 3.11 is a test target. Optional developer tools
`ruff` and `pip-audit` are not installed on the VPS by the runtime installer.

Tests use temporary SQLite databases and mock RSS/AI/Signal HTTP services. Include
regressions for split/chunked JSON, malformed API output, replay/scope checks, failed
feeds, preview/dismissal races, model provenance, update snapshots and rollback.
Never put real provider keys, verification tokens or Signal account data in tests.

### Replay repeat-coverage cases

```bash
PYTHONPATH=. python -B tests/replay_editorial.py
```

The replay seeds labelled chronological cases, puts older dispatches behind
similar-looking distractors, and inspects the exact request passed to a fake
editor. It records whether the earlier ID was in that input and whether the
item was ultimately sent by the mock Signal transport. Use the same script
with `PYTHONPATH` pointing at a checksum-verified older checkout for a paired
comparison. The fake editor deliberately skips rewrites only when the labelled
prior is supplied; this measures the pipeline, not any hosted model's judgment.

Add sanitized feed title/snippet pairs to `tests/test_release_109.py` and pair
each rewrite with an actual correction, recovery, or separate decision that
must survive. Keep names or incident exceptions out of production rules. Use
`tests/benchmark_editorial.py` separately for elapsed time, memory, queue
counts, and prompt bytes.

## Future changes and migrations

- Keep persistent state outside the release directory. Existing config values win
  over new defaults; do not silently rewrite a user's model list or focus.
- Keep `VERSION`, the project metadata and release highlights in sync. Relay
  adds media and expansion state; the compatible base schema version remains 1. Bump schema version for
  incompatible changes and refuse unsupported newer schemas.
- Snapshot before migration/activation. Keep old code, its virtual environment and
  runtime reference until their backups are no longer needed.
- Test both the old-database upgrade and full snapshot restore. Never claim that
  restoring a local DB can undo an already sent Signal message.
- Run all tests and audit dependencies before packaging. Pack reviewed
  source/docs/tests, not development environments, credentials, databases, or
  account keys. Normalize archive owner/group, member times and gzip metadata.
- Preserve public-only feed resolution, no paid OpenRouter fallback, exact model
  output validation and ambiguous-send handling when adding provider features.

## Explicit limits of verification

Local/mock tests do not prove Signal registration acceptance, provider account
eligibility, free quota availability, summary factual accuracy or delivery to every
group member. Use preview and a real end-to-end smoke test before enabling live.
