"""Exercise deployment data operations in temporary paths, never real services/users."""

import json
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from newsbot.config import Config


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.root = self.base / "program"
        self.data = self.base / "data"
        self.backups = self.base / "backups"
        self.release = self.root / "releases" / "old"
        self.release.mkdir(parents=True)
        self.data.mkdir()
        (self.release / "VERSION").write_text("1.0.0\n")
        (self.release / "runtime.env").write_text("SIGNAL_RUNTIME=native\n")
        (self.release / "newsbot").symlink_to(Path(__file__).resolve().parents[1] / "newsbot", target_is_directory=True)
        (self.release / ".venv").symlink_to(Path(sys.prefix), target_is_directory=True)
        (self.root / "current").symlink_to(self.release, target_is_directory=True)
        config = Config(self.data / "config.json")
        config.data["models"][0]["key"] = "test-secret-not-a-real-key"
        config.update(focus="original", mode="live", paused=False)
        self.original_password = config.data["dashboard_password"]
        (self.data / "signal").mkdir()
        (self.data / "signal" / "state").write_text("synthetic account state")
        (self.data / "history.txt").write_text("original history")
        library = Path(__file__).resolve().parents[1] / "scripts" / "admin-lib.sh"
        self.prefix = f"""
set -euo pipefail
umask 077
source {shlex.quote(str(library))}
ROOT={shlex.quote(str(self.root))}
DATA={shlex.quote(str(self.data))}
BACKUPS={shlex.quote(str(self.backups))}
# Tests replace ownership/service operations; no root access or real unit changes.
install() {{
  local args=()
  while [[ $# -gt 0 ]]; do
    case "$1" in -o|-g) shift 2;; *) args+=("$1"); shift;; esac
  done
  command install "${{args[@]}}"
}}
chown() {{ :; }}
runuser() {{ shift 3; "$@"; }}
activate_release() {{ ln -sfn "$1" "$ROOT/current"; }}
"""

    def tearDown(self):
        self.tmp.cleanup()

    def shell(self, command):
        return subprocess.run(["bash", "-c", self.prefix + command], capture_output=True, text=True)

    def test_snapshot_restore_preserves_keys_history_and_pauses_news(self):
        result = self.shell("""
snapshot_data test
echo 'new history' > "$DATA/history.txt"
restore_snapshot "$SNAPSHOT"
printf '%s\n' "$SNAPSHOT"
""")
        self.assertEqual(result.returncode, 0, result.stderr)
        restored = json.loads((self.data / "config.json").read_text())
        self.assertEqual(restored["focus"], "original")
        self.assertTrue(restored["paused"])
        self.assertEqual(restored["mode"], "preview")
        self.assertEqual(restored["dashboard_password"], self.original_password)
        self.assertEqual(restored["models"][0]["key"], "test-secret-not-a-real-key")
        self.assertEqual((self.data / "signal" / "state").read_text(), "synthetic account state")
        self.assertEqual((self.data / "history.txt").read_text(), "original history")
        moved = list(self.base.glob("data.before-restore-*"))
        self.assertEqual(len(moved), 1)
        self.assertEqual((moved[0] / "history.txt").read_text().strip(), "new history")

    def test_corrupt_snapshot_refused_before_data_is_moved(self):
        result = self.shell("""
snapshot_data test
printf 'corrupted' > "$SNAPSHOT/data.tar.gz"
restore_snapshot "$SNAPSHOT"
""")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((self.data / "history.txt").exists())
        self.assertEqual(list(self.base.glob("data.before-restore-*")), [])

    def test_backup_file_permissions_are_private(self):
        result = self.shell("snapshot_data test\nprintf '%s\\n' \"$SNAPSHOT\"\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        snapshot = Path(result.stdout.strip())
        self.assertEqual(snapshot.stat().st_mode & 0o777, 0o700)
        self.assertEqual((snapshot / "data.tar.gz").stat().st_mode & 0o777, 0o600)

    def test_unrecognized_restore_path_refused(self):
        result = self.shell(f"restore_snapshot {shlex.quote(str(self.base))}\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((self.data / "config.json").exists())
