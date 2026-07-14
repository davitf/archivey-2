## Context

`internal/streams/verify.py::_make_hasher` returns a factory for `"crc32"` (a
`zlib.crc32` wrapper) and for any name in `hashlib.algorithms_available`, else `None`.
`hashlib` ships `blake2b`/`blake2s` but **not** `blake2sp` — the SMP/parallel variant RAR5
uses — so BLAKE2sp members fall to the `None` path and emit `DIGEST_UNVERIFIABLE`.
`rar_reader.py` already stores `blake2sp_hash` (32 bytes) on `member.hashes["blake2sp"]`
and wraps payload streams in `VerifyingStream` with those hashes; only the hasher is
missing.

## Goals / Non-Goals

**Goals:**
- A correct, incremental, zero-dependency BLAKE2sp hasher.
- Real verification of BLAKE2sp-only RAR5 members (mismatch → `CorruptionError`).
- Spec reconciliation between `format-rar` and `compressed-streams`.

**Non-Goals:**
- Verifying BLAKE2sp for any other format (RAR5 is the only producer today).
- A public hashing API — the hasher is internal.
- Optimizing throughput beyond "correct and streaming" (RAR data throughput is dominated
  by the `unrar` pipe, not the hash).

## Key decisions

- **Build on stdlib `hashlib.blake2s` tree parameters.** BLAKE2sp with parallelism degree
  8 is a two-level tree: 8 leaf `blake2s` instances (`fanout=8`, `depth=2`,
  `inner_size=32`, `node_depth=0`, `node_offset=i` for leaf `i`, `last_node=True` on leaf
  7) feeding one root `blake2s` (`node_depth=1`, `node_offset=0`, `last_node=True`), digest
  size 32. Input bytes are distributed to leaves in **round-robin 32-byte blocks** (the
  BLAKE2sp block size). This keeps the core zero-dependency — no C extension, no new wheel.
- **Incremental interface.** Implement the `_IncrementalHasher` protocol (`update(data)`,
  `digest()`, `digest_size`). `update` maintains a small carry buffer so arbitrary chunk
  boundaries route to the correct leaf; `digest()` finalizes each leaf, feeds the 8 leaf
  digests into the root, and returns the 32-byte root digest. Finalization is idempotent
  for a single terminal read (matches how `VerifyingStream._verify` calls `digest()`).
- **Validate with known-answer tests.** Include BLAKE2sp KAT vectors (from the reference
  BLAKE2 test suite) so the implementation is proven independent of RAR fixtures, then
  cross-check end-to-end that a native read of a BLAKE2sp-only member matches `unrar`.
- **Spec reconciliation.** `compressed-streams`'s digest matrix currently uses `blake2sp`
  as the "cannot be computed" exemplar; after this change `blake2sp` is computable, so the
  unverifiable row switches to a genuinely-unknown algorithm name. `format-rar`'s existing
  verification claim needs no wording change — it becomes true.

## Open questions (resolve during apply)

- Confirm RAR5 uses BLAKE2sp **degree 8, unkeyed, 32-byte output** (the WinRAR/`unrar`
  convention) against a generated fixture + `unrar` before finalizing tree parameters.
- Whether to also expose BLAKE2sp for `member.hashes` *computed* provenance elsewhere — no
  (out of scope; RAR5 is the only stored producer).
