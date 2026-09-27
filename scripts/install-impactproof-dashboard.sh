#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
install_dir="${HOME}/.local/bin"
mkdir -p "$install_dir"
mkdir -p "${HOME}/.config/impactproof"
chmod 0700 "${HOME}/.config/impactproof"
install -m 0755 "$script_dir/impactproof-dashboard" "$install_dir/impactproof-dashboard"

printf '%s\n' \
  "Installed ~/.local/bin/impactproof-dashboard" \
  "Ensure ~/.local/bin is on PATH." \
  "Create ~/.config/impactproof/bob.env manually and add:" \
  "BOB_API_KEY=<your-key>" \
  "Restrict the file to user-only access (for example: chmod 600 ~/.config/impactproof/bob.env)."
