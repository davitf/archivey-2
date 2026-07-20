# Release checklist

Maintainer runbook for cutting a versioned release (`vX.Y.Z`). Work top to bottom;
check boxes in the PR that prepares the release (or keep a local copy).

One-time **repo rename / PyPI Trusted Publishing / Pages** steps live in
[`release-repo-cutover.md`](release-repo-cutover.md) — do those before the first
public tag from the canonical `archivey` name. This page is the **every release**
loop.

Package version is the static `[project].version` in `pyproject.toml` (not derived
from the git tag). `publish.yml` fails if the tag and packaged version disagree.

---

## 0. Preconditions

- [ ] Default branch green (CI + recent nightly wall job not in unexplained fail).
- [ ] No open “must land before this tag” items on `review/STATUS.md` /
      debt-ledger pay-list / in-flight OpenSpec changes you intended for this
      version (for `0.2.0`, that includes D2 `SECURITY.md`, DD4 rapidgzip
      characterization, and any other recorded pay-befores).
- [ ] First public release only: cutover runbook complete (or consciously
      releasing to TestPyPI from `archivey-2` while still pre-rename).

---

## 1. Gather user-visible changes (CHANGELOG)

Previous release tag: `PREV=$(git describe --tags --abbrev=0 2>/dev/null || true)`  
(If there is no tag yet — first release — use the empty range / whole history and
write the entry-zero under `## [0.2.0]` from `[Unreleased]`.)

- [ ] List commits / merged PRs since `PREV`:

  ```bash
  git log --first-parent --oneline "${PREV:-$(git rev-list --max-parents=0 HEAD)}..HEAD"
  # Optional PR-oriented view (GitHub CLI):
  gh pr list --state merged --base main --search "merged:>$(git log -1 --format=%cI "$PREV" 2>/dev/null || echo 2020-01-01)" --limit 100
  ```

- [ ] Triage into Keep a Changelog buckets in `CHANGELOG.md`:
  **Added** / **Changed** / **Deprecated** / **Removed** / **Fixed** / **Security**.
  Prefer user-facing behavior over internal refactors. Omit chore-only / review-doc
  noise unless it changes a published claim.
- [ ] Move `[Unreleased]` items into a new `## [X.Y.Z] - YYYY-MM-DD` section; leave
  a fresh empty `[Unreleased]` above it.
- [ ] Add / update the compare links at the bottom of `CHANGELOG.md` once the tag
  name is known (repo may still be `archivey-2` pre-cutover).

---

## 2. Performance numbers for the CHANGELOG

Goal: a short, honest table (or bullet list) of peer wall ratios **for this
release**, plus a note vs the **previous release** when one exists. Absolute
VISION bands stay informational; do not claim CI hard-fails on ≤1.3×.

- [ ] On a quiet machine (or after pulling the latest successful
      `benchmark-wall-realistic` artifact), measure current `main`:

  ```bash
  uv sync --group dev --extra all
  uv run --no-sync python -m benchmarks.harness \
    --mode full --scale realistic --warmup \
    --json-out /tmp/archivey-wall-current.json \
    --text-out /tmp/archivey-wall-current.md
  ```

- [ ] **Vs previous release** (skip on first release): check out the previous
      tag in a worktree or second clone, run the same harness command to
      `/tmp/archivey-wall-prev.json`, then compare overlapping `wall_ratio`
      cases (higher = slower vs peer):

  ```bash
  # Example: previous tag v0.2.0
  git worktree add /tmp/archivey-prev v0.2.0
  ( cd /tmp/archivey-prev && uv sync --group dev --extra all && \
    uv run --no-sync python -m benchmarks.harness \
      --mode full --scale realistic --warmup \
      --json-out /tmp/archivey-wall-prev.json \
      --text-out /tmp/archivey-wall-prev.md )
  # Drift helper against the previous JSON (same relative gates as nightly)
  uv run --no-sync python -m benchmarks.harness \
    --mode full --scale realistic --warmup \
    --wall-drift-baseline /tmp/archivey-wall-prev.json \
    --json-out /tmp/archivey-wall-current.json \
    --text-out /tmp/archivey-wall-current.md
  ```

  Alternatively download the nightly artifact that corresponds to the previous
  tag’s era if you trust that host more than a local rerun — record which host /
  artifact you used.

- [ ] Paste a compact summary into the CHANGELOG entry (and refresh
      `docs/costs.md` / VISION measured tables if numbers moved materially).
      Note measurement host / core count (see `benchmarks/RESULTS.md`).
- [ ] Confirm PR structural gate still passes:

  ```bash
  uv run --no-sync python -m benchmarks.harness --mode structural --scale ci
  ```

---

## 3. Docs and claims

- [ ] End-user docs still match behavior: `docs/usage.md`, `formats.md`,
      `safe-extraction.md`, `gotchas.md`, `api.md`, `philosophy.md`, `costs.md`.
- [ ] `VISION.md` performance / safety sentences still match what you are willing
      to ship (no falsifiable over-claim).
- [ ] `docs/internal/threat-model.md` open items: either fixed, consciously
      deferred with wording, or called out in SECURITY / gotchas.
- [ ] `docs/internal/open-issues.md` not contradicting shipped decisions (stale
      rows fixed or moved to Closed).
- [ ] MkDocs builds clean:

  ```bash
  uv run --group docs mkdocs build --strict
  ```

- [ ] README install / quickstart / doc links still accurate.
- [ ] First release: migration notes if promising a path from
      `zipfile`/`tarfile`/`shutil.unpack_archive`/`patool` (PLAN release bundle).

---

## 4. Security and packaging

- [ ] `SECURITY.md` present with a disclosure path (required before any public
      “safe extraction” marketing).
- [ ] `pyproject.toml` metadata: name, description, classifiers, URLs, extras ↔
      capabilities (`packaging-and-extras` spec).
- [ ] Free-threading / platform support statement matches CI (core-only `3.13t`
      job — document honestly).
- [ ] Optional: OSS-Fuzz onboarding status noted (may trail the first tag).

---

## 5. Quality gate (same bar as “before pushing”)

From `CONTRIBUTING.md` — all three dependency configs:

```bash
# 1. Current [all]
uv sync --group dev --extra all && uv run --no-sync pytest

# 2. Lowest direct
uv sync --group dev --extra all --resolution lowest-direct && uv run --no-sync pytest

# 3. Core-only
uv sync --no-dev && uv run --no-sync python tests/check_zero_dep_core.py \
  && uv run --no-sync --with pytest --with pytest-timeout --with pytest-cov pytest tests/ -q

# Restore everyday env
uv sync --group dev --extra all
```

Also:

- [ ] `uv run --no-sync ruff check` + `ruff format --check` over
      `src/ tests/ scripts/ benchmarks/`
- [ ] `uv run --no-sync pyrefly check` and `uv run --no-sync ty check`
- [ ] `openspec validate --all`
- [ ] Spot-check: `unrar` / `p7zip-full` present if you are asserting RAR data /
      encrypted-ZIP fixture coverage in this release’s story

---

## 6. Version bump and tag

- [ ] Set `[project].version` in `pyproject.toml` to `X.Y.Z` (**drop** any
      `.devN` suffix).
- [ ] Commit on a release PR: `CHANGELOG.md`, version bump, doc/number updates;
      merge to the default branch.
- [ ] Tag and push (annotated preferred):

  ```bash
  git checkout main && git pull
  git tag -a "vX.Y.Z" -m "archivey X.Y.Z"
  git push origin "vX.Y.Z"
  ```

- [ ] Confirm `publish.yml` built distributions and published to the expected
      index (TestPyPI while repo is `archivey-2`; PyPI after cutover to
      `archivey`). See cutover runbook for Trusted Publishing setup.
- [ ] Create a GitHub Release for `vX.Y.Z` whose body **mirrors** the CHANGELOG
      section (generated notes are optional; the committed file remains
      authoritative).

---

## 7. Post-release

- [ ] Bump `[project].version` to the next `X.Y.(Z+1).dev0` (or minor/major
      `.dev0`) on `main` so installs from the default branch stay distinguishable
      from the tag.
- [ ] Leave `[Unreleased]` ready for the next cycle.
- [ ] If docs site is Pages-published, confirm the workflow ran on the tag /
      default branch as configured.
- [ ] Announce as you prefer (not required by the tooling).

---

## Quick command cheat sheet

| Step | Command |
| --- | --- |
| Commits since last tag | `git log --first-parent --oneline "$(git describe --tags --abbrev=0)"..HEAD` |
| Structural bench | `uv run --no-sync python -m benchmarks.harness --mode structural --scale ci` |
| Realistic wall + JSON | `uv run --no-sync python -m benchmarks.harness --mode full --scale realistic --warmup --json-out /tmp/wall.json --text-out /tmp/wall.md` |
| Docs | `uv run --group docs mkdocs build --strict` |
| Build only | `uv build` then inspect `dist/` |
| Version source | `pyproject.toml` → `[project].version` |
