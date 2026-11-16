#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
echo 'Installing the Agy Console companion skill and enabling default collaboration for this Codex home.'
exec python3 scripts/install_codex_skill.py --enable-default
