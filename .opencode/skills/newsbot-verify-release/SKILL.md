---
name: newsbot-verify-release
description: Verify an existing local Signal News Bot release archive and SHA-256 sidecar against its intended source and run the packaged tests before handoff.
---

# Verify a local Signal News Bot release

Use the trusted local archive and its matching sidecar only. This is a local release check, not a packaging, publishing, or VPS deployment workflow.

1. Start in the intended source root and record it (`source_root=$PWD`). Check `VERSION` and `[project].version` in `pyproject.toml`. A matching version string is not an artifact identifier: the original and compact-footer 1.0.3 archives have different checksums.
2. From the local folder containing the archive and sidecar, substitute the intended version and check the digest before listing or extracting:

   ```bash
   archive=signal-news-bot-X.Y.Z.tar.gz
   sha256sum -c "$archive.sha256" && tar -tvzf "$archive"
   ```

   Stop on a mismatch or an unexpected listing. Expect a single `signal-news-bot/` root with regular files/directories containing the app, `VERSION`, `pyproject.toml`, pinned requirements, installer, scripts, deploy units, docs, and tests. Reject unexpected paths, links, credentials, account state, config/databases, caches, virtualenvs, `HANDOFF.md`, and `.opencode/`.
3. Extract as a non-root user into a fresh scratch directory, never over the source tree or a running release:

   ```bash
   scratch=$(mktemp -d /tmp/opencode/newsbot-release.XXXXXXXX) &&
     tar --extract --gzip --file "$archive" --directory "$scratch" \
       --no-same-owner --no-same-permissions
   ```

   For a release expected to match the current source, compare the two trees by content. Exclusions below only omit known source-side development files; they are not a substitute for inspecting the archive listing.

   ```bash
   diff -qr --exclude=.git --exclude=.gitignore --exclude=.opencode --exclude=HANDOFF.md \
     --exclude=__pycache__ --exclude=.ruff_cache --exclude=.venv \
     "$source_root" "$scratch/signal-news-bot"
   ```

   For an older release, use its matching source snapshot or report that source parity was not checked. If the only differences are newer collaborator docs, report them separately rather than claiming an exact match. Never overwrite a versioned artifact to resolve a mismatch.
4. Load `newsbot-test` to run the full suite on Python 3.11 and 3.14 from the extracted root, and from the intended source root when verifying parity. Keep `PYTHONPATH` unset for extracted runs. Parse the packaged shell scripts individually. See `docs/DEVELOPMENT.md` and `docs/VERIFICATION.md` for release lint, audit, and migration expectations; don't claim those passed unless checked.
5. Report the artifact name and digest, source parity, per-interpreter results, and gaps. A verified local archive is not evidence of a VPS deployment. If the user separately requests an upgrade, the authoritative procedure is `docs/OPERATIONS.md`.
