#!/usr/bin/env bash
# Shared dev-environment bootstrap: system binaries + Python toolchain + git hooks.
#
# Called by every environment that provisions a workspace, so they cannot drift:
#   - Cursor Cloud     → .cursor/install.sh (via .cursor/environment.json)
#   - Claude Code web  → .claude/hooks/session-start.sh (SessionStart hook)
#   - a human, locally → ./scripts/setup-dev-env.sh
#
# Must stay idempotent: it runs on every agent boot, not just first provision.
#
# Why this matters beyond convenience: the RAR *data* tests and the benchmark
# gate's rar_* cases skip cleanly when `unrar` is absent. A skip is quiet, so an
# environment missing it silently tests less than it appears to — and
# `--update-baselines` there would rewrite structural.json without those cases.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

# Official uv installer lands here; keep it first for non-login shells.
export PATH="${HOME}/.local/bin:${PATH}"

log() { printf '\n=== %s\n' "$1"; }

# Root in most containers, an ordinary user on a laptop.
if [ "$(id -u)" -eq 0 ]; then
  SUDO=""
elif command -v sudo >/dev/null 2>&1; then
  SUDO="sudo"
else
  SUDO=""
fi

install_linux_packages() {
  # Cloud VMs sometimes boot with a skewed clock; apt then rejects Release files
  # as "not valid yet". Prefer correcting the clock from an HTTP Date header; if
  # that fails, still allow a generous future skew so installs can proceed.
  if date_hdr="$(
    curl -fsSI --max-time 10 https://archive.ubuntu.com/ubuntu/ \
      | awk -F': ' 'tolower($1) == "date" { print $2; exit }'
  )"; then
    ${SUDO} date -s "${date_hdr}" >/dev/null 2>&1 || true
  fi

  # Third-party PPAs on a shared image can 403 through a proxy; a stale index is
  # still usable, so do not let one broken source block the install below.
  ${SUDO} apt-get \
    -o Acquire::Max-FutureTime=604800 \
    -o Acquire::Check-Valid-Until=false \
    update || echo "! apt-get update reported errors; continuing with cached indexes" >&2
  # unrar: RAR member-data tests (multiverse component).
  # rar: the WRITER, which the declarative corpus needs to build its 41 RAR cases.
  #   Without it they skip quietly and the RAR column of the conformance sweep is
  #   unexercised (F16 / O6; see dev-docs/investigations/rar-corpus-sweep-diagnosis.md).
  # p7zip-full: encrypted ZIP fixtures built by shelling out to `7z`.
  ${SUDO} apt-get install -y unrar rar p7zip-full
}

install_macos_packages() {
  if ! command -v brew >/dev/null 2>&1; then
    echo "! Homebrew not found; install unrar and p7zip manually" >&2
    return 0
  fi
  # The rar formula ships both `rar` and `unrar`. archivey's finder requires the
  # RARLAB build specifically — unar / 7z do not satisfy it.
  command -v unrar >/dev/null 2>&1 || brew install rar
  command -v 7z >/dev/null 2>&1 || brew install p7zip
}

install_windows_packages() {
  if ! command -v choco >/dev/null 2>&1; then
    echo "! Chocolatey not found; install unrar manually" >&2
    return 0
  fi
  command -v unrar >/dev/null 2>&1 || choco install unrar -y
}

log "system packages"
# Non-fatal on purpose: a transient apt/brew outage should not stop a session from
# starting at all. The verification block below reports anything still missing, so
# the failure stays visible instead of turning into quietly-skipped tests.
system_packages_ok=1
case "$(uname -s)" in
  Linux*) install_linux_packages || system_packages_ok=0 ;;
  Darwin*) install_macos_packages || system_packages_ok=0 ;;
  MINGW* | MSYS* | CYGWIN*) install_windows_packages || system_packages_ok=0 ;;
  *) echo "! unsupported OS $(uname -s); skipping system packages" >&2 ;;
esac
if [ "${system_packages_ok}" -eq 0 ]; then
  echo "! system package install failed; see the verification block below" >&2
fi

# JIT / snapshot-less images may not ship uv; bootstrap if missing.
if ! command -v uv >/dev/null 2>&1; then
  log "uv"
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi

# The global npm prefix is not user-writable on some images (EACCES); install
# into a writable prefix that is already on PATH.
if ! command -v openspec >/dev/null 2>&1 && command -v npm >/dev/null 2>&1; then
  log "openspec CLI"
  # The bare `openspec` package on npm is an unrelated empty stub.
  npm install -g --prefix "${HOME}/.local" @fission-ai/openspec
fi

log "python dependencies"
uv sync --group dev --extra all

log "git hooks"
./scripts/install-git-hooks.sh

# Report rather than assume: a missing binary here is the difference between
# "tests passed" and "tests silently skipped", and it is worth seeing at boot.
log "verification"
uv run --no-sync python - <<'PY'
import shutil

from benchmarks.harness import missing_baseline_requirements

# `rar` is the writer: without it the corpus's 41 declarative RAR cases skip
# quietly and the sweep's RAR column is unexercised, which is exactly the kind of
# silent gap this block exists to make visible.
for tool in ("unrar", "rar", "7z"):
    found = shutil.which(tool)
    print(f"{'ok  ' if found else 'MISSING'} {tool}{f': {found}' if found else ''}")

missing = missing_baseline_requirements()
if missing:
    print("\nIncomplete benchmark toolchain — these cases cannot be measured:")
    for item in missing:
        print(f"  - {item}")
else:
    print("ok   benchmark toolchain complete")
PY
