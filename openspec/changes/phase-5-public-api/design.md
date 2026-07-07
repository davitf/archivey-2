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

```python
@dataclass(frozen=True)
class PasswordRequest:
    member: ArchiveMember | None  # the member being decrypted; None for archive-level
                                  # (header) decryption, where no member exists yet
    attempt: int                  # 1 on the first ask for this unit; increments when a
                                  # previously returned password failed for it

PasswordProvider = Callable[[PasswordRequest], str | bytes | None]
```

- **Per encrypted unit** (a 7z folder, a RAR/ZIP member, an encrypted header), the
  reader tries the per-archive **known-good list** first (passwords that already
  succeeded, most-recent first), then remaining sequence candidates, then — if a
  provider was given — calls it with a `PasswordRequest` so interactive UIs can show
  what is being asked about *and* whether this is a retry. A provider may be called
  repeatedly for the same unit (with `attempt` incrementing) until it returns `None`
  (= give up → `EncryptionError`). Every success is appended to the known-good list for
  the rest of the operation, so a provider is asked once per *new* password, not once
  per member.
- **Why a context object, not a bare member:** a callable's signature cannot be widened
  compatibly after the freeze — every future need (attempt count, the prior error, the
  encryption algorithm) would be a breaking change. The frozen dataclass costs one
  import now and makes those additions non-breaking. The `attempt` field exists from
  day one because "may be called repeatedly until it returns `None`" *invites* retry
  loops, and a UI that cannot distinguish first-ask from wrong-password is broken in an
  obvious, immediately-visible way.
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
    # None disables that guard; the UNLIMITED preset is all-None.
    max_extracted_bytes: int | None = 2 * 2**30
    max_ratio: float | None = 1000.0
    ratio_activation_threshold: int = 5 * 2**20
    max_entries: int | None = 1_048_576

    UNLIMITED: ClassVar["ExtractionLimits"]  # every guard disabled (trusted archives)

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
- **Per-call limits override:** `extract()` and `extract_all()` additionally accept
  `limits: ExtractionLimits | None = None`. Precedence: per-call `limits` >
  `config.extraction_limits` > library default. Rationale: the bomb limits are the one
  config field callers legitimately vary *per call* ("tighter cap for this untrusted
  upload, defaults for our own archives"); forcing a `replace()`d config per call would
  make the security-relevant knob the least ergonomic one. This replaces the four loose
  Phase-4b kwargs (`max_extracted_bytes`/`max_ratio`/`ratio_activation_threshold`/
  `max_entries`) — one structured type, two supply points, no parallel surface.
- **Presets:** `ExtractionLimits()` (the defaults) is the untrusted posture —
  safe-by-default already serves that case — and `ExtractionLimits.UNLIMITED` disables
  all four guards for explicitly trusted archives. Presets are deliberately **not**
  named by trust (`TRUSTED`/`UNTRUSTED`) and **not** coupled to `ExtractionPolicy`:
  policy governs metadata/permission semantics, limits govern resource bounds, and an
  implicit coupling (e.g. `policy=TRUSTED` silently lifting bomb guards) would be a
  footgun in the dangerous direction. The trusted-archive recipe is two explicit
  decisions — `extract(src, dest, policy=ExtractionPolicy.TRUSTED,
  limits=ExtractionLimits.UNLIMITED)` — documented in SPEC.md rather than encoded. A
  `SecurityConfig` sub-config / umbrella `trust=` convenience was considered and
  deferred post-v1: a convenience can be added compatibly, unbundling one cannot.
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

### 6. Link resolution: positional hardlinks, last-wins symlinks, member-id cycles

The two access modes currently disagree on duplicate names. Streaming
(`_register_progressively`) resolves links against an *incremental* name map — a
hardlink finds the latest **earlier** occurrence. Random access (`_resolve_link`)
resolves against one *last-wins* map built from all members — a hardlink can resolve
**forward**, and with duplicate names resolves to the **last occurrence overall**.
Concretely, for `[A.txt(content1), hardlink L→A.txt, A.txt(content2)]`: real tar
extraction gives `L` content1 (it links against what is on disk when `L` is written);
v2 streaming agrees; v2 random access returns content2 from `read(L)` and links `L`
against the content2 inode. Same archive, different answers by mode.

Decided semantics (both modes, one story):

- **Hardlinks resolve positionally**: the latest occurrence **strictly before** the
  link (filesystem-faithful — every real tar writer stores the data-bearing entry
  before its link entries, since hardlinks are detected by inode during archiving;
  RAR5's redirect model is the same). Implementation: `_resolve_link` walks members in
  order with an incremental map, as the streaming path already does.
- **Forward fallback kept in random-access mode**: when no earlier occurrence exists
  (a crafted / reordered archive), fall back to a later member rather than failing —
  extraction's orphan second pass already recovers this case gracefully (links against
  the source once written, bytes bomb-counted once; PR #33's regression test), and
  that behavior depends on `link_target_member` being set. Strictly-backward-only
  would regress it. Streaming inherently cannot resolve forward and already fails per
  `OnError` — an acceptable, documented asymmetry.
- **Symlinks keep last-wins overall** (random access): a symlink is a *name* resolved
  at use time, and the final extracted state of a duplicated name is the last
  occurrence — so the full-map resolution is already correct. Streaming resolves to
  the latest earlier occurrence because a single pass cannot see forward; documented,
  not fought.
- **Cycle detection tracks member ids**, not names, in both `_resolve_link` and
  `_open_with_link_follow`: name-based visited-sets false-positive on chains passing
  through distinct same-named members. The raised error's message must say "cycle"
  (today's `LinkTargetNotFoundError` subclasses `ReadError`, so the spec scenario is
  technically met, but the message misleads).
- **Spec wording softened** (`archive-reading`): "hardlinks SHALL always resolve to an
  earlier member" becomes "resolve to the most recent earlier member; an archive where
  the source appears only later is malformed but tolerated — random access falls back
  to the later member, streaming fails per `OnError`."

## Resolved decisions (2026-07 maintainer review)

1. **`max_entries` counting semantics — count only members written.** Move
   `BombTracker.start_member()` to after the `members` selector and user `filter`;
   selector-skips and filter-skips do not count. Spec delta: `safe-extraction`.
2. **ZIP `format_availability` — report current read truth.** ZIP stays **PARTIAL**
   until Phase 7 wires optional member codecs into ZIP member reads; `missing` lists
   absent packages when applicable and is empty when the gap is implementation-stage.
   Spec deltas: `backend-registry`, `format-zip`.

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
