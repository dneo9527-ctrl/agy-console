#!/bin/sh
# Foreground launcher for Linux; closing this terminal remains authoritative.
set -eu
AGY_PANEL_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
command -v python3 >/dev/null 2>&1 || { echo 'Python 3.9+ is required.'; exit 1; }
exec python3 "$AGY_PANEL_DIR/update_bootstrap.py" --app-root "$AGY_PANEL_DIR" "$@"
