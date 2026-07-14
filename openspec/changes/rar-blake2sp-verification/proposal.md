## Why

RAR5 members can carry a **BLAKE2sp** integrity hash instead of a CRC-32, and archivey
already *surfaces* it at `member.hashes["blake2sp"]`. But it is never actually
**verified**: `VerifyingStream` builds hashers via `hashlib`, `hashlib` has no
`blake2sp` (only `blake2b`/`blake2s`), so `_make_hasher("blake2sp")` returns `None` and
the read **silently degrades to a `DIGEST_UNVERIFIABLE` diagnostic**. The two specs
disagree as a result: `format-rar` §171 asserts "Member has CRC32/Blake2sp →
verification runs as bytes are read and raises on mismatch," while
`compressed-streams` treats `blake2sp` as the canonical *cannot-be-computed* example.
So a corrupted BLAKE2sp-only RAR5 member is read back as clean today. With the flagship
being **consistency + safety**, this is a genuine integrity gap on the one native format
where the stronger hash is the *only* integrity signal — and BLAKE2sp is implementable on
the stdlib (`hashlib.blake2s` tree parameters), keeping the zero-dep core intact.

## What Changes

- Implement **BLAKE2sp** (the 8-way parallel BLAKE2s tree hash RAR5 uses) as an internal,
  zero-dependency incremental hasher built on `hashlib.blake2s` tree parameters, with an
  incremental streaming interface matching `VerifyingStream`'s `_IncrementalHasher`
  protocol.
- Register `blake2sp` in the verification layer's hasher factory so BLAKE2sp-only members
  are **actually verified** and raise `CorruptionError` on mismatch (no longer degrading
  to `DIGEST_UNVERIFIABLE`).
- **Reconcile the specs:** update the `compressed-streams` digest matrix so `blake2sp` is
  a computable algorithm by default (replace the "cannot be computed" example with a
  genuinely-unknown algorithm), making `format-rar` §171's claim true rather than
  aspirational.
- Validate against RAR5 `blake2sp`-only fixtures and an oracle (`unrar`/`rarfile`
  round-trip) plus BLAKE2sp known-answer test vectors.

## Capabilities

### New Capabilities

<!-- none -->

### Modified Capabilities

- `compressed-streams`: `blake2sp` is a supported/computable verification algorithm; the
  digest matrix's unverifiable example changes to a truly-unknown algorithm.
- `format-rar`: the RAR5 Blake2sp verification claim is now backed by an implementation
  (no `DIGEST_UNVERIFIABLE` fallback for well-formed BLAKE2sp members).
- `testing-contract`: RAR oracle/corpus coverage asserts BLAKE2sp verification (corrupt
  → `CorruptionError`) plus BLAKE2sp KATs.

## Impact

- New internal module (e.g. `internal/hashing/blake2sp.py`); wired into
  `internal/streams/verify.py` `_make_hasher`.
- No public-API change; no new runtime dependency (stdlib `hashlib`).
- Behavior change: a corrupt BLAKE2sp-only RAR5 member now raises `CorruptionError`
  instead of reading back clean with a diagnostic — a safety improvement, spec-aligning.
