"""Root-admin helper. Only recognized direct-child snapshots are eligible."""

import argparse
import json
import shutil
import time
from pathlib import Path


def candidates(root, days, keep, now=None):
    now = time.time() if now is None else now
    entries = []
    for path in root.iterdir() if root.is_dir() else []:
        if path.is_symlink() or not path.is_dir():
            continue
        required = [path / name for name in ("marker", "complete", "release", "data.tar.gz", "SHA256SUMS")]
        if not all(p.is_file() and not p.is_symlink() for p in required):
            continue
        if (path / "marker").read_text().strip() != "signal-news-bot-v1":
            continue
        entries.append((path, (path / "complete").stat().st_mtime))
    entries.sort(key=lambda pair: (pair[1], pair[0].name), reverse=True)
    return [path for path, stamp in entries[max(1, keep):] if stamp < now - days * 86400]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    root = Path("/var/backups/signal-news-bot")
    config = json.loads(Path("/var/lib/signal-news-bot/config.json").read_text())
    days, keep = config.get("backup_retention_days", 30), config.get("backup_keep", 3)
    if type(days) is not int or type(keep) is not int or not 1 <= days <= 3650 or not 1 <= keep <= 100:
        raise SystemExit("Invalid backup policy; nothing deleted.")
    if args.apply and config.get("backup_prune_enabled", False) is not True:
        print("Backup pruning disabled; nothing deleted.")
        return
    paths = candidates(root, days, keep)
    for path in paths:
        print(("Removing: " if args.apply else "Eligible: ") + path.name)
        if args.apply:
            shutil.rmtree(path)
    print(f"{len(paths)} backup(s) {'removed' if args.apply else 'eligible'}. Newest {keep} recognized backups protected; releases untouched.")


if __name__ == "__main__":
    main()
