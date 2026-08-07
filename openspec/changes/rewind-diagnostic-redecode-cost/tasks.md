# Tasks — cost-based rewind diagnostic

## 1. Measure first (this changed the spec)

- [x] 1.1 Check whether `rapidgzip` exposes its index spacing. It does:
      `available_block_offsets()` → `{compressed_bits: decompressed_offset}`, ~0.01 ms.
- [x] 1.2 Record the density measurement — 3 points over a 5 MB `gzip.compress` output —
      because it shows the accelerator path has the *same* blind spot, which is what
      makes the predicate uniform rather than "uniform except accelerators".

## 2. The predicate

- [x] 2.1 `REWIND_REDECODE_WARN_BYTES` in `config.py`, absolute, with the 1 GB
      single-block counterexample written next to it so "why not relative" survives.
- [x] 2.2 `redecode_distance(target) -> int | None` on `DecompressorStream` (existing
      table, no index build), `_AcceleratorStream` (`available_block_offsets`, not
      `block_offsets`, which would force the full index), and `ArchiveStream` (delegate,
      since streams nest).
- [x] 2.3 `ArchiveStream._maybe_warn_rewind` keys off the distance, not `RewindWarning`.
- [x] 2.4 Keep `RewindWarning` for its *message* content (accelerator name, install
      hint); it no longer decides *whether* to report.
- [x] 2.5 Codecs with a native index stop returning `None` from `rewind_warning` — they
      need the message shape too, now that they can qualify.

## 3. Record once, escalate always

- [x] 3.1 `DiagnosticCollector.escalate_only(code, ...)`: resolve the policy, raise on
      `RAISE`, record nothing.
- [x] 3.2 Rewind path: `emit()` the first qualifying seek, `escalate_only()` thereafter.
- [x] 3.3 Write the rule into `diagnostics` as the general one for once-per-stream codes.

## 4. Docs

- [x] 4.1 `docs/access-and-cost.md` — the seeking section now describes cost, not codecs.
- [x] 4.2 `docs/errors-and-diagnostics.md` if it names the code.
- [x] 4.3 `CHANGELOG.md`.

## 5. Tests

- [x] 5.1 Delete the marker on `test_full_rewind_emits_regardless_of_codec`.
- [x] 5.2 `test_single_block_xz_rewind_is_silent` pins today's blind spot and must flip —
      rewrite it to assert the emission.
- [x] 5.3 A short rewind emits nothing on a codec that used to warn unconditionally.
- [x] 5.4 A rewind inside one block of a multi-block xz stays quiet.
- [x] 5.5 Under a `RAISE` policy, the **second** qualifying seek raises too, and the
      report still holds exactly one record.

## 6. Verify

- [x] 6.1 `openspec validate --strict rewind-diagnostic-redecode-cost`
- [x] 6.2 Three dependency configs, `ruff`, `pyrefly`, `ty`.

## Archive (after this merges)

Left unchecked on purpose: `scripts/check_openspec_archived.py` reads an all-complete
tasks list as "finished but unarchived" and fails on `main`, and archiving is a separate
step from merging (`CONTRIBUTING.md`).

- [ ] A.1 `openspec archive rewind-diagnostic-redecode-cost --yes`, then commit the resulting
      `openspec/specs/` diff.

**Archive order matters.** Several of these changes modify the same requirement, so a
later one pastes an earlier one's version and only matches once that has been applied:

```
format-availability-required-source -> decouple-member-metadata-from-declared-seekability -> review-diagnostics-batch -> reject-bidi-overrides-in-safe-extraction -> strict-archive-eof-trailing-bytes -> rewind-diagnostic-redecode-cost
```

Verified by dry-run archive on a scratch tree in that order: all six apply, and the only
lines removed from `openspec/specs/` are ones a delta deliberately rewrites. (`openspec
validate --strict` does **not** catch a mis-targeted `MODIFIED` header — it passed while
two deltas would have silently dropped a scenario on archive.)
