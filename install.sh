#!/usr/bin/env bash
set -euo pipefail
SOURCE=$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")
exec bash "$SOURCE/scripts/install-release.sh" "$@"
