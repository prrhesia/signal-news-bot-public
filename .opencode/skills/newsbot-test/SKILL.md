---
name: newsbot-test
description: Run Signal News Bot's focused or full local regression suite after bot changes, without touching the VPS, real Signal, or hosted AI.
---

# Test Signal News Bot locally

Run from the project root (`VERSION`, `requirements.txt`, `tests/`). See `docs/DEVELOPMENT.md` for the test design and `docs/VERIFICATION.md` for past results. Use temporary SQLite state and mock feeds, Signal RPC, and provider responses. Never use production config, keys, or account data.

1. While iterating, run the relevant test file by replacing `discover -s tests -q` below with `discover -s tests -p 'test_release_104.py' -v` (adjust the pattern as needed). Use `test_admin.py` for snapshot/restore, `test_formatting.py` for Signal text spans, and `test_regressions.py` for transport and races. Run the full suite before handing off a behavior change.
2. Use an isolated Python with `requirements.txt` installed. Use 3.11 for Debian 12 compatibility and also 3.14 for a release check. With `uv`, an installed interpreter, and cached wheels, run this from the source or extracted release root:

   ```bash
   (
     version=3.11  # Repeat with 3.14 for release checks.
     scratch=$(mktemp -d /tmp/opencode/newsbot-test.XXXXXXXX) || exit
     uv venv --offline --no-python-downloads --python "$version" "$scratch/venv" &&
       uv pip install --offline --only-binary=:all: \
         --python "$scratch/venv/bin/python" -r requirements.txt &&
       env -u PYTHONPATH -u PYTHONHOME "$scratch/venv/bin/python" \
         -B -W error::RuntimeWarning -m unittest discover -s tests -q
   )
   ```

   If an interpreter or wheel is missing, report that or use an existing isolated environment with the pinned dependencies. Do not switch to production credentials or services.
3. If shell scripts changed, parse each separately so `bash -n` actually reads every script:

   ```bash
   (
     for script in install.sh scripts/*.sh scripts/newsbotctl scripts/signal-cli-run; do
       bash -n "$script" || exit 1
     done
   )
   ```

   Treat any syntax error as a failed check. Use the optional checks documented in `docs/DEVELOPMENT.md` if relevant and available.
4. Report the Python version(s), test counts, failures, and skipped checks. A green suite is local evidence; it does not verify live Signal or hosted AI. For an existing release archive, use `@newsbot-verify-release`.
