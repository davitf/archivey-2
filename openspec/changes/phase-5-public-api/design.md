# Phase 5 design — public API finalization

## Context

The public surface has three "deferred until Phase 5" clusters: (a) the config surface
(`strict_eof` shipped bare on `open_archive`; bomb limits exist only in the
safe-extraction spec), (b) input shapes Phase 7 requires (multi-volume sources,
multi-password archives), and (c) selector semantics left open (`MemberSelector`'s
collection form). DEV precedent exists for a config object (contextvars-based) and is
deliberately *not* copied. Maintainer decisions for each cluster were taken in the
2026-07 review; this document records the rationale so implementation doesn't relitigate
them.

## Goals / Non-Goals

**Goals:**

- Lock `open_archive()` / `extract()` signatures so Phase 6–7 add no breaking changes.
- Password model that keeps single-pass streaming viable on multi-password archives.
- Multi-source entry paths ready for Phase 7's volume-joining readers.
- One explicit, immutable config object; no ambient state.

**Non-Goals:**

- Implementing the volume-joining readers themselves (Phase 7).
- A `collect_results=False` extraction mode (deferred: readers cache the member list
  internally anyway, so skipping result accumulation alone doesn't bound memory — doing
  it properly is a larger no-member-cache change; revisit post-v1 if needed).
- Ambient configuration (contextvars / global mutable defaults).
- fsspec URL opening (IDEAS; supplies remote filesystem context for volume discovery).

## Decisions

### 1. Password: single value | sequence | provider callable

`password: str | bytes | Sequence[str | bytes] | PasswordProvider | None` where
`PasswordProvider = Callable[[ArchiveMember | None], str | bytes | None]`.

- **Per encrypted unit** (a 7z folder, a RAR/ZIP member, an encrypted header), the
  reader tries the per-archive **known-good list** first (passwords that already
  succeeded, most-recent first), then remaining sequence candidates, then — if a
  provider was given — calls it, passing the `ArchiveMember` being decrypted (`None`
  for archive-level/header decryption, where no member exists yet) so interactive UIs
  can show what is being asked about. A provider may be called repeatedly for the same
  unit until it returns `None` (= give up → `EncryptionError`). Every success is
  appended to the known-good list for the rest of the operation, so a provider is asked
  once per *new* password, not once per member.
- **Why not per-call `open(member, password=...)`:** it forces random access — a
  streaming pass over a multi-password 7z could not supply the second password without
  aborting the pass. The candidate list + provider covers both random and streaming
  access with one mechanism, so the per-call parameter is dropped from `format-7z`'s
  phrasing (alternative considered and rejected for v1: both mechanisms — needless
  surface).
- **Cost note (drives the "known-good first" rule):** ZIP (1-byte ZipCrypto check /
  AES verifier) and RAR5 (explicit password-check value) validate candidates cheaply;
  **7z has no check value** — a candidate costs a deliberately expensive key derivation
  (2^19 SHA-256 rounds) plus decode-until-CRC-failure. Docs say "most likely password
  first"; the derived-key cache is keyed by (password, salt, cycles).

### 2. Multi-source: explicit list now, path discovery, stream discovery never

`source: str | Path | BinaryIO | Sequence[str | Path | BinaryIO]`.

- An explicit `Sequence` is the ordered volume list, exactly as given (works for
  streams; each seekable stream gets the standard origin normalization). Length-1
  sequence ≡ single source.
- A **single path** whose name matches a volume pattern (`.7z.001`/`.7z.NNN`,
  `.partN.rar`, `.rNN`) triggers sibling discovery in its directory, natural-ordered;
  opening any part of the set works (per `format-7z`/`format-rar`). Discovery is
  **path-only by design** — a bare stream has no filesystem to enumerate; remote sets
  get discovery via the future fsspec URL layer, which has an `fs.ls()`.
- Detection runs on the first volume only. In Phase 5 (before the joining readers
  exist) a multi-source open of any format resolves the backend and raises
  `UnsupportedFeatureError` ("multi-volume X lands in Phase 7" for 7z/RAR; "not a
  multi-volume format" otherwise) — the signature and plumbing are real, the joining is
  not. Alternative considered: defer the signature to Phase 7 with the readers —
  rejected because Phase 5 is the API freeze.

### 3. Config: explicit frozen `ArchiveyConfig`, no ambient state

```python
@dataclass(frozen=True)
class ExtractionLimits:
    max_extracted_bytes: int = 2 * 2**30
    max_ratio: float = 1000.0
    ratio_activation_threshold: int = 5 * 2**20
    max_entries: int = 1_048_576

@dataclass(frozen=True)
class ArchiveyConfig:
    use_rapidgzip: AcceleratorMode = AcceleratorMode.AUTO
    use_indexed_bzip2: AcceleratorMode = AcceleratorMode.AUTO
    strict_archive_eof: bool = False
    extraction_limits: ExtractionLimits = ExtractionLimits()
```

- Passed explicitly: `open_archive(..., config=None)` (None → module default constant),
  `extract(..., config=None)`. The reader carries its config; `extract_all()` inherits
  the reader's unless overridden.
- **Why not contextvars (DEV's approach):** ambient config makes behavior depend on
  call-site-invisible state — hard to trace in applications embedding archivey, and
  interacts confusingly with threads/executors (a worker thread silently misses the
  caller's context). Explicit-with-`replace()` is boring and predictable. A read-only
  process default (`archivey.set_default_config`) can be added later without breaking
  anything, so deciding "no" now is cheap. Alternative considered: kwargs-only (no
  object) — rejected by maintainer decision; the knob count (accelerators + limits +
  strictness, growing in Phase 7 with e.g. RAR temp-dir policy) justifies the object.
- Config **excludes** per-call operationals: `format`, `streaming`, `password`,
  `encoding`, extraction's `members`/`filter`/`policy`/`overwrite`/`on_error`/
  `on_progress`. Rule of thumb: *what* to do per call = kwarg; *how* the library
  behaves = config.
- Internal `StreamConfig` remains as the codec-layer view, constructed from
  `ArchiveyConfig` + the access mode (its `streaming` field is derived, not public).

### 4. `strict_archive_eof` defaults to False

Truncated-but-readable tars are common in the wild (trailer-less writers, `cat`-joined
archives); GNU tar itself warns rather than fails on a missing trailer. The failure
mode of default-True is also awkward: the error necessarily surfaces at the *end* of an
otherwise successful iteration (the trailer can only be checked after the last member),
so `extract()` would fail an entire run after extracting everything — and under
`OnError.CONTINUE` semantics there is no "member" to attribute the failure to. The
warning already surfaces the condition for the default case; callers validating
untrusted feeds opt in via config. (Renamed from `strict_eof`: it is an
*archive-level* end-of-data check, extensible to ZIP trailing junk / gzip trailing
garbage later; TAR is just the first implementation.)

### 5. MemberSelector collection form: names match all duplicates; members match identity

`Collection[str | ArchiveMember]` normalizes to a predicate at the API boundary:

- a `str` entry matches **every** member with that (normalized) name — duplicate names
  in a tar all match, and sequential extraction preserves last-wins-on-disk;
- an `ArchiveMember` entry matches by identity (`_archive_id` + `member_id`;
  members are unhashable so the normalizer builds an id-set, not a member-set);
- strings and members may be mixed. A `Callable` selector is unchanged. (Selecting
  "just the last duplicate by name" — `get()`'s rule — was considered and rejected:
  a selector answers "should this member be included", and silently dropping earlier
  duplicates would surprise both streaming consumers and extraction, where writing all
  duplicates is what sequential extraction does anyway.)

## Risks / Trade-offs

- [7z candidate passwords are expensive to try] → known-good-first ordering, derived-key
  cache, and documentation ("likely password first"); a wrong candidate list on a large
  solid archive costs one key derivation per candidate per folder, bounded and loud.
- [Provider called during a streaming pass can block (interactive prompt) mid-iteration]
  → documented; the provider contract is synchronous by design in the sync-only v1.
- [Multi-source signature lands before any joining reader] → the
  `UnsupportedFeatureError` path is tested now so the signature is honest; Phase 7
  replaces the rejection with real joining without a signature change.
- [Config object grows into a god-object] → additions require a spec delta; per-call
  operationals are explicitly barred from it (rule recorded in the spec).
- [`strict_archive_eof=False` hides truncation from inattentive callers] → the warning
  remains; `archivey test` (CLI, Phase 9) and extraction integrity checks are the
  loud paths.

## Migration Plan

Pre-1.0, no deprecation cycle: the `strict_eof=` keyword is removed in the same change
that adds `config=`; `openspec` specs, `SPEC.md` signature blocks, and tests move
together. Phase 6/7 proposals build on the frozen signatures.
