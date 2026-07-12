# Design — Hypothesis property tests for the safety logic

Small, test-only change; the decisions that need pinning:

## Pure vs. filesystem-touching targets

The five targets are **not** uniformly I/O-free (the original "pure" framing was wrong):

| Target | Nature | How tested |
|--------|--------|------------|
| `normalize_member_name` | pure (string) | generated-string strategies; asserts under a silenced logger (it logs on change — permitted) |
| `resolve_link_target_name` | pure (string) | generated link/target string pairs × `MemberType` |
| `discover_volume_siblings` | **mixed** — name regexes pure, but `is_file()`/`iterdir()` hit the FS | pure part via strings; discovery via a `tmp_path` tree |
| `check_universal` | **filesystem** — `Path.resolve()` on dest/parents (real symlinks matter) | `tmp_path`-rooted strategies that materialize symlink layouts |
| detection over a prefix | pure over bytes, but needs a **peekable** source | random bytes wrapped in `PeekableStream`/`BytesIO` — raw non-seekable streams are consumed by design and out of scope |

## CI budget

Default profile: `max_examples=100`, `deadline=None` (disabled — pure-ish functions on shared
CI runners flake on wall-clock deadlines), `derandomize=True` (reproducible). Deep profile via
`ARCHIVEY_FUZZ_EXAMPLES` (e.g. `2000`) for local/nightly deepening — mirrors the mutation
harness's `ARCHIVEY_FUZZ_MUTATIONS` env-var pattern. No separate CI job.

## Invariants, not oracles

Each property asserts a structural safety invariant (e.g. "any `..`-bearing name is rejected"),
never a value re-derived from a second copy of the logic. Shrunk counterexamples become pinned
`@example`s / unit cases; a counterexample that reveals a real bug is fixed in this change (as
the mutation harness did).

## Dependency

`hypothesis` in the `dev` group only; runtime core stays zero-dependency; the test module
guards/skips cleanly under `[core-only]` where the group is absent.
