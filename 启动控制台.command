#!/bin/zsh
console_dir="${0:A:h}"
cd -- "$console_dir" || exit 1
exec python3 "$console_dir/update_bootstrap.py" --app-root "$console_dir" "$@"
