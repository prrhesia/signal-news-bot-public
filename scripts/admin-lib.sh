#!/usr/bin/env bash
# Shared root-only deployment helpers. Source from install.sh/newsbotctl.
ROOT=/opt/signal-news-bot
DATA=/var/lib/signal-news-bot
BACKUPS=/var/backups/signal-news-bot
UNITS=(signal-news-signal.service signal-news-bot.service)

stop_services() {
  systemctl stop "${UNITS[@]}"
}

start_services() {
  systemctl reset-failed "${UNITS[@]}" 2>/dev/null || true
  systemctl start "${UNITS[@]}"
}

activate_release() {
  local release=$1
  ln -sfn "$release" "$ROOT/current.new"
  mv -Tf "$ROOT/current.new" "$ROOT/current"
  install -m 644 "$release/deploy/signal-news-bot.service" /etc/systemd/system/signal-news-bot.service
  install -m 644 "$release/deploy/signal-news-signal.service" /etc/systemd/system/signal-news-signal.service
  if grep -qx 'SIGNAL_RUNTIME=jvm' "$release/runtime.env"; then
    sed -i 's/^MemoryMax=512M$/MemoryMax=768M/' /etc/systemd/system/signal-news-signal.service
  fi
  systemctl daemon-reload
}

snapshot_data() {
  # Call only with services stopped and the administrator lock held.
  local label=${1:-manual} target
  target="$BACKUPS/$(date -u +%Y%m%dT%H%M%SZ)-$label-$$"
  install -d -m 700 "$BACKUPS" "$target"
  printf 'signal-news-bot-v1\n' > "$target/marker"
  readlink -f "$ROOT/current" > "$target/release"
  tar -czf "$target/data.tar.gz" -C "$DATA" .
  (cd "$target" && sha256sum data.tar.gz > SHA256SUMS)
  touch "$target/complete"
  chmod 600 "$target/marker" "$target/release" "$target/data.tar.gz"
  # Shared result consumed by the installer/newsbotctl after this function returns.
  # shellcheck disable=SC2034
  SNAPSHOT=$target
}

restore_snapshot() {
  local target=$1 release
  target=$(realpath -e "$target")
  if [[ $target != "$BACKUPS/"* || ! -f $target/complete || $(cat "$target/marker") != signal-news-bot-v1 ]]; then
    echo 'Not a recognized application backup.' >&2
    return 1
  fi
  (cd "$target" && sha256sum -c SHA256SUMS) || return 1
  release=$(cat "$target/release")
  if [[ $release != "$ROOT/releases/"* || ! -f $release/VERSION || ! -f $release/runtime.env ]]; then
    echo 'The backup release is missing; do not remove releases needed for rollback.' >&2
    return 1
  fi
  # Preserve the failed/current data as well. Never delete account keys/history.
  if [[ -d $DATA ]]; then
    mv "$DATA" "$DATA.before-restore-$(date -u +%Y%m%dT%H%M%SZ)-$$"
  fi
  install -d -m 700 -o newsbot -g newsbot "$DATA"
  tar -xzf "$target/data.tar.gz" -C "$DATA" --no-same-owner
  chown -R newsbot:newsbot "$DATA"
  chmod 700 "$DATA"
  activate_release "$release"
  # Group messages cannot be rolled back. Never automatically repost from old history.
  runuser -u newsbot -- env PYTHONPATH="$release" "$release/.venv/bin/python" -c \
    'import sys; from pathlib import Path; from newsbot.config import Config; Config(Path(sys.argv[1])/"config.json").update(paused=True, mode="preview")' "$DATA"
}

ready_check() {
  local attempt
  for ((attempt=0; attempt<15; attempt++)); do
    if runuser -u newsbot -- env PYTHONPATH="$ROOT/current" \
      "$ROOT/current/.venv/bin/python" -m newsbot --data "$DATA" doctor > /dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  echo 'Readiness check failed. Inspect: newsbotctl logs' >&2
  return 1
}
