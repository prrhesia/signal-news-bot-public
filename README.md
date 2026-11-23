# Signal News Bot

A self-hosted RSS and Atom news editor for **one trusted Signal group**. It
uses hosted AI to select on-topic developments, skips repeat coverage, and
sends short summaries with optional photos or video.

Runs on Debian 12+ x86_64 with Python 3.11+, SQLite, systemd, and native
`signal-cli`. No Docker, local model, or frontend build is required.

## Get started

1. Download the versioned **installer** and its `.sha256` file from
   the repository's **Releases** tab. Don't use
   GitHub's automatically generated source archive as the installer.
2. Follow the [installation guide](docs/INSTALL.md) to verify the checksum,
   register a dedicated Signal account, and open the dashboard over SSH.
3. Add [eligible AI providers](docs/PROVIDERS.md), a focus, and public feeds
   or an OPML import. Start in **preview** with a test group.

Already running the bot? Follow the [update and rollback guide](docs/OPERATIONS.md#update-procedure).
Do **not** register the Signal account again. Updates preserve private settings,
subscriptions, history, keys, and account state.

## What it does

- **Selection:** Configurable focus and exclusions, a relevance threshold,
  and repeat checks before analysis and delivery. Keeps material follow-ups.
- **Media:** Up to four distinct photos from the same feed entry and one
  eligible MP4, including supported public Nitter feeds. Use `/images on`
  or `/videos on`, and `/videos min 75` to set the original AI score cutoff.
- **Direct feeds:** `/feed instant ID on` skips AI for that feed, always
  includes its safe item link, and retains the hourly cap.
- **Context:** Reply `/why` for the original decision, or use `/expand` or
  🔎 for more saved feed details. Optional web fallback adds cited context
  only when there are no unused feed details.
- **Group controls:** `/status`, `/usage`, `/mode preview`, `/pause`, and
  `/help`. `/dmtest` sends a fixed, private test reply only to its requester.

See the [1.0.7 Relay notes](docs/CHANGELOG.md#107--relay) for what changed.

## Privacy and limits

- The first feed fetch establishes a baseline. Preview doesn't automatically
  send news, and a send that Signal couldn't confirm is never retried blindly.
- All members of the configured group can use group commands. Other groups
  and incoming DMs cannot control the bot. Choose the group accordingly.
- Keep the dashboard and unauthenticated Signal RPC off the public internet.
  Secrets stay in the private dashboard/config; don't put account data,
  real feeds, or private logs in issue reports.
- AI can be wrong. Feed summaries aren't independently verified. Free-tier
  availability varies; see quota, data handling, and account rules in the
  [provider guide](docs/PROVIDERS.md).

## Development and support

- [Development and tests](docs/DEVELOPMENT.md) · [Verification record](docs/VERIFICATION.md)
- [Operations and commands](docs/OPERATIONS.md) · [Changelog](docs/CHANGELOG.md)
- [Contribution guide](CONTRIBUTING.md) · [Security reporting](SECURITY.md)
