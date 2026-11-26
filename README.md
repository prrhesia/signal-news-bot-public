# Signal News Bot

**A self-hosted news desk for one trusted Signal group.**

Follow public RSS and Atom feeds, use hosted AI to select stories that fit your
focus, and send short Signal updates when there’s something new to report.

`RSS feeds` → `Preview and review` → `Signal group`

**[Download 1.0.9 Lens](https://github.com/prrhesia/signal-news-bot-public/releases/tag/v1.0.9)**
&nbsp; / &nbsp; [Fresh setup](docs/INSTALL.md)
&nbsp; / &nbsp; [Update an existing bot](docs/OPERATIONS.md#update-procedure)

---

## Get running

| If you’re… | Start here |
| --- | --- |
| **Setting up a new VPS** | Download the versioned installer **and** its `.sha256` file. Follow the [install guide](docs/INSTALL.md), register a dedicated Signal account, and try it in preview with a test group. |
| **Already running the bot** | Use the [update guide](docs/OPERATIONS.md#update-procedure). Pause, enter preview, verify the archive, and run `newsbotctl update`. **Don’t register again.** |
| **Opening the dashboard** | [Use an SSH tunnel](docs/INSTALL.md#4-open-the-dashboard-securely) and open `http://127.0.0.1:8787` on your computer. Keep the dashboard and Signal daemon off the public internet. |

Use the release assets, not GitHub’s automatic **Source code** archives.
The supported install is Debian 12+ x86_64 with Python 3.11+, SQLite, systemd,
and native `signal-cli`. No Docker, local model, or frontend build.

## What reaches the group

> 📰 🚆 — The North Ferry terminal reopened after repairs. Evening sailings
> will use the regular berth.
>
> *Example dispatch, not a live post.*

- **News that fits your focus.** Set topics, exclusions, and an hourly limit.
  The editor compares prior coverage; a correction or recovery can still post.
- **Media from the right entry.** Attach up to four distinct photos and one
  eligible MP4. Turn them on separately with `/images on` and `/videos on`.
- **A faster route for a trusted feed.** `/feed instant ID on` skips AI,
  includes the source link, and still counts toward the hourly limit.
- **Context on demand.** Reply with `/why` for the decision or `/expand` for
  more feed detail. Optional web search can add citations if the feed runs out
  of useful detail.

## What’s new in Lens

Lens looks for distinctive evidence in older coverage, so an earlier dispatch
is less likely to be crowded out by similar headlines. In the private
**News & history** dashboard, see which prior item IDs reached the editor,
when an item was sent, and whether media was selected or included in a
confirmed send. [See the Lens changelog](docs/CHANGELOG.md#109--lens).

## Start in preview

Set your focus, add [eligible providers](docs/PROVIDERS.md), then add public
feeds or import an OPML file. The first fetch establishes a baseline. In preview,
review picks, skipped repeats, and genuine follow-ups before enabling live
delivery. Use `/status`, `/usage`, and `/help` from the configured group.

> [!IMPORTANT]
> Only use a trusted group: its members can run bot commands. If Signal can’t
> confirm a send, check the group before requeueing it. AI summaries are not
> independent fact checks, and free-tier capacity varies by provider.

## Project references

[Operations](docs/OPERATIONS.md) · [Provider setup](docs/PROVIDERS.md) ·
[Verification](docs/VERIFICATION.md) · [Development](docs/DEVELOPMENT.md) ·
[Contributing](CONTRIBUTING.md) · [Security](SECURITY.md)
