#!/usr/bin/env bash
# Cursor Cloud update/install script (see .cursor/environment.json).
# Must stay idempotent — runs on every agent boot after the workspace is checked out.
set -euo pipefail

# Official uv installer lands here; keep it first for non-login shells.
export PATH="${HOME}/.local/bin:${PATH}"

sudo apt-get update
sudo apt-get install -y unrar

npm install -g --prefix "${HOME}/.local" @fission-ai/openspec

# JIT / snapshot-less Cloud images may not ship uv; bootstrap if missing.
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi

uv sync --group dev --extra all

# Auto ruff fix+format on commit (Cursor remaps core.hooksPath; this install is aware).
./scripts/install-git-hooks.sh
