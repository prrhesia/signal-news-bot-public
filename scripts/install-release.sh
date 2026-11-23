#!/usr/bin/env bash
# Invoked by the top-level install.sh; all changes are scoped to this application.
set -euo pipefail
umask 077
SOURCE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
source "$SOURCE/scripts/admin-lib.sh"
SIGNAL_RUNTIME=${SIGNAL_RUNTIME:-native}
if [[ $EUID -ne 0 ]]; then echo 'Run this installer as root (sudo bash install.sh).' >&2; exit 1; fi
if [[ $SIGNAL_RUNTIME != native && $SIGNAL_RUNTIME != jvm ]]; then echo 'SIGNAL_RUNTIME must be native or jvm.' >&2; exit 1; fi
if [[ $(uname -m) != x86_64 || ! -f /etc/debian_version ]] || ! command -v systemctl >/dev/null; then
  echo 'This installer requires Debian 12+ x86_64 with systemd.' >&2; exit 1
fi
if [[ -e $ROOT && ! -f $ROOT/.newsbot-install ]]; then echo "Unfamiliar $ROOT; refusing to overwrite it." >&2; exit 1; fi
if [[ ! -f $ROOT/.newsbot-install ]]; then
  if [[ -e $DATA || -e /usr/local/bin/newsbotctl ]] || getent passwd newsbot >/dev/null; then
    echo 'Existing unfamiliar service account/data/helper; refusing to take ownership.' >&2; exit 1
  fi
elif [[ -d $ROOT/newsbot && ! -L $ROOT/current ]]; then
  echo 'An unreleased legacy layout was found. Preserve its data; ask for a migration before installing.' >&2; exit 1
fi
exec 9>/run/lock/signal-news-bot.lock
flock -n 9 || { echo 'Another newsbot administration operation is running.' >&2; exit 1; }
VERSION=$(cat "$SOURCE/VERSION")
[[ $VERSION =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo 'Invalid release VERSION.' >&2; exit 1; }
echo "Installing Signal News Bot $VERSION ($SIGNAL_RUNTIME). Existing configuration/data will be preserved."
apt-get update
apt-get install -y python3 python3-venv curl ca-certificates tar gzip jq coreutils libatomic1 libstdc++6 zlib1g util-linux
if ! getent passwd newsbot >/dev/null; then
  useradd --system --user-group --home-dir "$DATA" --shell /usr/sbin/nologin newsbot
fi
install -d -m 755 "$ROOT" "$ROOT/releases" "$ROOT/vendor"
install -d -m 700 -o newsbot -g newsbot "$DATA" "$DATA/signal"
printf 'signal-news-bot-v1\n' > "$ROOT/.newsbot-install"
if [[ ! -L $ROOT/current ]]; then
  python3 - <<'PY'
import socket
for port in (7583, 8787):
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            raise SystemExit(f"Port {port} is already occupied; do not overwrite another service. Free the port and rerun.")
PY
fi
WORK=$(mktemp -d)
SWITCHED=false
STOPPED=false
SUCCESS=false
SNAPSHOT=''
PREVIOUS=''
if [[ -L $ROOT/current ]]; then PREVIOUS=$(readlink -f "$ROOT/current"); fi
cleanup() {
  local code=$?
  trap - EXIT
  if [[ $SUCCESS != true && $SWITCHED == true ]]; then
    stop_services || true
    if [[ -n $SNAPSHOT && -n $PREVIOUS ]]; then
      echo "Update failed; restoring pre-update backup: $SNAPSHOT" >&2
      restore_snapshot "$SNAPSHOT" && start_services || echo 'Automatic restore needs attention; backups and failed data are preserved.' >&2
    else
      echo 'First installation did not become ready. No account/configuration was deleted. Run newsbotctl logs.' >&2
    fi
  elif [[ $SUCCESS != true && $STOPPED == true ]]; then
    start_services || true
  fi
  rm -f "$WORK/java.json" "$WORK/java.tar.gz" "$WORK/signal.json" "$WORK/signal.tar.gz"
  rmdir "$WORK" 2>/dev/null || true
  exit "$code"
}
trap cleanup EXIT

# Fetch/check artifacts before stopping an existing service.
if [[ $SIGNAL_RUNTIME == jvm && ! -x $ROOT/vendor/java/bin/java ]]; then
  curl --fail --location --silent --show-error --retry 3 --max-time 60 \
    'https://api.adoptium.net/v3/assets/latest/25/hotspot?architecture=x64&image_type=jre&os=linux&vendor=eclipse' > "$WORK/java.json"
  JAVA_URL=$(jq -er '.[0].binary.package.link' "$WORK/java.json")
  JAVA_SHA=$(jq -er '.[0].binary.package.checksum' "$WORK/java.json")
  curl --fail --location --silent --show-error --retry 3 --max-time 600 "$JAVA_URL" -o "$WORK/java.tar.gz"
  echo "$JAVA_SHA  $WORK/java.tar.gz" | sha256sum -c -
  install -d -m 755 "$ROOT/vendor/java"
  tar -xzf "$WORK/java.tar.gz" -C "$ROOT/vendor/java" --strip-components=1 --no-same-owner
fi
curl --fail --location --silent --show-error --retry 3 --max-time 60 https://api.github.com/repos/AsamK/signal-cli/releases/latest > "$WORK/signal.json"
TAG=$(jq -er '.tag_name' "$WORK/signal.json")
SV=${TAG#v}
[[ $SV =~ ^[0-9]+\.[0-9]+\.[0-9]+([.][0-9]+)?$ ]] || { echo 'Unexpected upstream version.' >&2; exit 1; }
ASSET="signal-cli-$SV.tar.gz"
if [[ $SIGNAL_RUNTIME == native ]]; then ASSET="signal-cli-$SV-Linux-native.tar.gz"; fi
URL=$(jq -er --arg name "$ASSET" '.assets[] | select(.name==$name) | .browser_download_url' "$WORK/signal.json")
DIGEST=$(jq -er --arg name "$ASSET" '.assets[] | select(.name==$name) | .digest' "$WORK/signal.json")
[[ $DIGEST == sha256:* ]] || { echo 'Upstream asset SHA256 missing; refusing unverified binary.' >&2; exit 1; }
RUNTIME="$ROOT/vendor/$SIGNAL_RUNTIME-$SV"
if [[ ! -f $RUNTIME/.verified-digest || $(cat "$RUNTIME/.verified-digest") != "$DIGEST" ]]; then
  if [[ -e $RUNTIME ]]; then RUNTIME="$RUNTIME-$(date +%s)-$$"; fi
  curl --fail --location --silent --show-error --retry 3 --max-time 600 "$URL" -o "$WORK/signal.tar.gz"
  echo "${DIGEST#sha256:}  $WORK/signal.tar.gz" | sha256sum -c -
  install -d -m 755 "$RUNTIME"
  tar -xzf "$WORK/signal.tar.gz" -C "$RUNTIME" --no-same-owner
  printf '%s\n' "$DIGEST" > "$RUNTIME/.verified-digest"
fi
if [[ $SIGNAL_RUNTIME == native ]]; then
  SIGNAL_BIN="$RUNTIME/signal-cli"
  "$SIGNAL_BIN" -Xmx192m --version
else
  SIGNAL_BIN="$RUNTIME/signal-cli-$SV/bin/signal-cli"
  JAVA_HOME="$ROOT/vendor/java" JAVA_OPTS='-Xms32m -Xmx256m' "$SIGNAL_BIN" --version
fi

RELEASE="$ROOT/releases/$VERSION-$(date -u +%Y%m%dT%H%M%SZ)-$$"
install -d -m 755 "$RELEASE"
cp -R "$SOURCE/newsbot" "$SOURCE/scripts" "$SOURCE/deploy" "$SOURCE/docs" "$SOURCE/tests" "$RELEASE/"
install -m 644 "$SOURCE/requirements.txt" "$SOURCE/README.md" "$SOURCE/VERSION" "$RELEASE/"
install -m 755 "$SOURCE/install.sh" "$RELEASE/install.sh"
printf 'SIGNAL_RUNTIME=%s\nSIGNAL_BIN=%q\n' "$SIGNAL_RUNTIME" "$SIGNAL_BIN" > "$RELEASE/runtime.env"
python3 -m venv "$RELEASE/.venv"
"$RELEASE/.venv/bin/pip" install --disable-pip-version-check --no-cache-dir --only-binary=:all: -r "$RELEASE/requirements.txt"
chmod -R a+rX "$RELEASE" "$RUNTIME"
chmod 755 "$RELEASE/install.sh" "$RELEASE/scripts/"*.sh "$RELEASE/scripts/newsbotctl" "$RELEASE/scripts/signal-cli-run"
if [[ $SIGNAL_RUNTIME == jvm ]]; then chmod -R a+rX "$ROOT/vendor/java"; fi
# Verify this exact VPS Python/dependency combination before switching anything.
runuser -u newsbot -- env PYTHONPATH="$RELEASE" PYTHONDONTWRITEBYTECODE=1 \
  "$RELEASE/.venv/bin/python" -m unittest discover -s "$RELEASE/tests" -q

if [[ -n $PREVIOUS && -f $DATA/config.json ]]; then
  stop_services
  STOPPED=true
  snapshot_data before-update
  echo "Pre-update backup: $SNAPSHOT"
fi
SWITCHED=true
touch "$DATA/maintenance"
chmod 644 "$DATA/maintenance"
activate_release "$RELEASE"
ln -sfn "$ROOT/current/install.sh" "$ROOT/install.sh"
cat > /usr/local/bin/newsbotctl <<'SH'
#!/usr/bin/env bash
exec /opt/signal-news-bot/current/scripts/newsbotctl "$@"
SH
chmod 755 /usr/local/bin/newsbotctl
runuser -u newsbot -- env PYTHONPATH="$RELEASE" "$RELEASE/.venv/bin/python" -m newsbot --data "$DATA" init
systemctl enable "${UNITS[@]}"
start_services
ready_check
rm -f "$DATA/maintenance"
SUCCESS=true
python3 "$RELEASE/scripts/prune-backups.py" --apply || echo 'Backup pruning skipped after an error; update is otherwise ready.' >&2
echo 'Ready. Dashboard: 127.0.0.1:8787 (SSH tunnel or HTTPS only).'
echo 'Next: register a DEDICATED number; see README. Do not register your personal Signal number.'
echo 'Future changes: newsbotctl update /absolute/path/to/new/extracted/signal-news-bot'
