#!/usr/bin/env bash
# Cursor Cloud update/install script (see .cursor/environment.json).
# Must stay idempotent — runs on every agent boot after the workspace is checked out.
set -euo pipefail

sudo apt-get update
sudo apt-get install -y unrar

npm install -g --prefix "${HOME}/.local" @fission-ai/openspec

uv sync --group dev --extra all

# Auto ruff fix+format on commit (Cursor remaps core.hooksPath; this install is aware).
./scripts/install-git-hooks.sh
