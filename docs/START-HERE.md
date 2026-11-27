# Signal News Bot — Start here

Signal News Bot polls RSS/Atom feeds, uses hosted AI to pick and summarize
relevant developments, and posts to one configured Signal group. It runs with
Python, SQLite, and systemd; its dashboard is private.

## Install on your own VPS

1. Obtain the versioned installer and its matching `.sha256` from the trusted
   project source. Do not substitute GitHub's automatic source-code download.
   For 1.1.0 (Continuity), run `sha256sum -c signal-news-bot-1.1.0.tar.gz.sha256` in the
   download directory before extracting.
2. On a fresh Debian 12+ x86_64 VPS, follow the install and registration
   instructions in the [installation guide](INSTALL.md). Register your own dedicated
   Signal number, then configure a trusted test group. Do not reuse another
   install's account files, private config, or database.
3. Tunnel to the dashboard over SSH and leave the Signal RPC port private.
   Add your own provider keys, focus, exclusions, and feeds. See
   [provider setup](PROVIDERS.md) for free-tier limits.
4. Run `newsbotctl doctor` on the VPS and send `/version` in the test group.
   Check **Test 5 recent baseline items** in dashboard preview before enabling
   live posting. Then confirm an actual test-group post arrives.
5. Follow [Operations](OPERATIONS.md) for upgrades, backups, and rollback.

## Work on the code

- Open the repository root in OpenCode. Use `@newsbot-test` for local regressions
  and `@newsbot-verify-release` to verify an existing archive. For first-time
  dependency setup and a code map, see [Development](DEVELOPMENT.md).
- Open this `docs/` folder as an Obsidian vault if desired. Commit only shared,
  reviewed Markdown; keep personal notes and local Obsidian settings out of Git.
- See the [changelog](CHANGELOG.md) and [verification record](VERIFICATION.md).
  Local tests do not prove live Signal delivery or hosted provider access.
- `/dmtest` is an outbound test from the selected Signal group. Incoming
  DMs still don't run commands, and bot-to-bot communication isn't implemented.
