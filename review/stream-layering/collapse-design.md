# Collapse design — verdict, fused stream, measured win

## Headline answer

**Partial yes.** Fuse **verification** into the outer `ArchiveStream`. Keep
**slicing** (`SlicingStream` / `SharedSource` / `LockedStream`) and the
**decode engine** as separate layers. Do not re-propose the nested-outer
collapse — #136 already did that.

Ranked sequence:

1. **Fix F1** (`read(0)` must not verify) — correctness; also a fusion prerequisite.
2. **Fuse `VerifyingStream` into `ArchiveStream`** (optional hasher +
   `expected_size` on the outer). Backends that today wrap
   `VerifyingStream(...)` pass verify knobs into `_wrap_member_stream` /
   `ArchiveStream` instead. When there is nothing to verify, the fused path is
   a no-op (same as skipping the wrap today).
3. **Let `_collapse_nested` finish** — once verify is no longer an opaque layer
   between two `ArchiveStream`s, the codec `ArchiveStream` collapses into the
   public handle (translators compose). STORED ZIP becomes
   `ArchiveStream → SlicingStream`.
4. **Optional follow-up:** real `SlicingStream.readinto` (seek+`readinto` under
   lock) so a fused hasher can hash a `memoryview` without a bytes copy. Small
   on this host; larger if callers move to `readinto`-heavy extract paths.
5. **Do not fuse** member-boundary / shared slicing, TAR/ISO `LockedStream`, or
   the decode engine.

---

## Per-layer verdict table

| Layer | Verdict | Why |
|-------|---------|-----|
| Public `ArchiveStream` (translate, stamp, size, lease, rewind, lazy open) | **keep** — identity | The brief's outer carrier; leases/finalizers/diagnostics watermark live here. |
| Nested outer from `_lazy_member_stream` | **already collapsed** (#136) | Confirm only; do not re-propose. |
| `VerifyingStream` | **fuse into outer** | Strongest candidate: outer already reads through an inner and owns `size`. Conditional (no hashes/size → no-op). Unblocks codec-AS collapse. |
| Codec `ArchiveStream` under verify | **collapse after fuse** | Structural leftover: `_collapse_nested` cannot see through `VerifyingStream`. After fuse it becomes a direct nested AS and flattens. |
| Member-boundary `SlicingStream` (ZIP raw, 7z member, RAR direct) | **keep separate, structural** | 1:N views over a shared handle; locked re-seek; CONCURRENT. Not the same object as the public lease. |
| `SharedSource.view` / `fix_stream_start_position` / 7z LZMA cap slices | **keep separate, structural** | Internal/shared uses, not member-boundary identity. |
| `LockedStream` / `CloseLockedStream` | **keep separate, structural** | Library-owned seek-then-read serialization (TAR/ISO/ZIP close). |
| `_MemberSlice` (solid) | **keep separate** | Forward-only block cursor; #136 already avoided an extra lazy wrapper type. |
| Decode engine (`DecompressorStream` / accelerator / crypto) | **keep separate** | Settled #96; Topic 6 for speed. #134 `readall` join already landed. |
| Measurement wrappers | **already conditional (skip)** | Identity when measurement off. |

---

## What fuses cleanly

### Design sketch — verify knobs on `ArchiveStream`

```text
ArchiveStream(
    open_fn,
    translate=..., stamp=..., size=member.size,
    expected_hashes=member.hashes or None,   # new, optional
    expected_size=member.size,               # already have size; reuse for cap
    digest_transforms=..., collector=...,
)
```

`read` / `close` gain the current `VerifyingStream` state machine (with F1/F2
fixed). When `expected_hashes` is empty/None **and** no length check is
requested, skip hasher setup entirely — nested `open_stream` / non-FILE /
directory / TAR / ISO paths stay as cheap as today.

`readinto`: either (a) route through fused `read` (safe, keeps today's copy), or
(b) call `inner.readinto` into the caller's buffer and `hasher.update(mv[:n])`
(fast path). (b) only wins if the inner also has a real `readinto` — today
`SlicingStream` does not (it inherits `ReadOnlyIOStream.readinto` → `read` →
bytes). So ship (a) with the fuse; treat (b)+`SlicingStream.readinto` as step 4.

### Invariants preserved (map from `correctness.md`)

| Invariant | How the fused stream keeps it |
|-----------|-------------------------------|
| 1 `read(0)` no-op | Explicit guard before EOF logic (fixes F1) |
| 2 readinto hashes | Implement fused `readinto` or force through `read` |
| 3 sequential-EOF only; seek disables | Same frontier/`_verify_enabled` logic on the outer |
| 4 size cap + trailing probe + deferred short | Same `_finish` / close ordering |
| 5 typed-error precedence + always close | `try`/`finally` around probe (fixes F2) |
| 9 one public AS; compose translators | Collapse now sees codec AS directly |
| 10 lease once | Verify no longer a separate close layer; outer `on_close` unchanged |
| 11 cheap size | Unchanged |

Nothing in the fuse touches invariants 6–8 (those are why slicing stays).

### When there is nothing to verify

TAR / ISO / directory / ZipCrypto / single-file reader / empty 7z records never
install verify today. Fused outer with knobs unset = current `ArchiveStream`
with zero hasher fields — no extra per-read work. Conditional verify stays
conditional.

---

## What resists fusion

**Member-boundary slicing + `SharedSource`.** Multiple concurrent views over one
handle need a per-view position and locked re-seek. That is a different object
model from the public member lease. Even for ZIP's single-consumer sequential
pass (where re-seek is redundant with only one live stream), the same
`SlicingStream` type is what CONCURRENT fan-out and 7z/RAR views use — splitting
"member slice inside AS" vs "shared view" would duplicate the bound/seek logic
the module docstring already unified (`slice.py:1-15`).

Quantitatively, after verify-fuse+codec-collapse the STORED stack is already
`ArchiveStream → SlicingStream` — one hop above the irreducible floor (a single
Python `read` that hashes+bounds over a file handle). Further fusing the slice
into AS buys almost nothing on the read path (see numbers) and costs the
shared-view story.

---

## Irreducible floor

On a STORED member the library **must** still:

1. Present a bounded view of the member's bytes (slice or equivalent),
2. Update at least one hasher and enforce `expected_size` on sequential EOF,
3. Translate/stamp errors,
4. Hold a lease/finalizer on the public handle.

Floor ≈ **one** `ArchiveStream` (hash+bound+translate+lease) over a
`SlicingStream` (or, for backends without archivey slicing, over the library
handle). Today's post-#136 STORED stack is **two** extra Python objects above
that floor (`VerifyingStream` + codec `ArchiveStream`). Fusion reaches the floor
for ZIP STORED; it cannot go below it without dropping verify or CONCURRENT
safety (VISION #2/#3 — not acceptable).

---

## Cost, measured

Host: shared x86_64, CPython 3.11, `[all]`. Script: `measurements.py`.
Corpus: 64 × 256 KiB **STORED** ZIP (no zlib). Medians, warmup discarded.

### End-to-end (wrapper isolation)

| Path | median | vs zipfile |
|------|-------:|-----------|
| archivey `stream_members` read-all (live, #136) | 7.37 ms | **1.75×** |
| zipfile read-all | 4.18 ms | 1.00× |
| DEFLATE same corpus (context only) | 31.1 / 15.4 ms | **2.00×** |

Open+list on the same STORED corpus remains ~5× zipfile — detection + member
model (H3 from #134), **not** the wrapper stack (streams open lazily). Out of
scope here.

### Synthetic stacks over the same `ZipFile.fp` (read-all)

| Stack | median | Δ vs current-like |
|-------|-------:|------------------:|
| current-like `AS → VS → AS → Slice` | 4.59 ms | — |
| after verify-fuse (codec AS gone) `AS → VS → Slice` | 4.42 ms | −3.7% |
| true fused `AS → Slice` (hash in AS) | 4.21 ms | **−8.3%** |
| raw seek+read+crc32 (no wrappers) | ~4.0 ms | floor reference |

Live archivey (7.37 ms) is ~2.8 ms above the current-like synthetic stack: that
delta is per-member open machinery (`_local_data_region`, `open_codec_stream` /
`resolve_codec`, lease, `ArchiveStream.__init__` ×2, watermark) — **not** fixed
by layer fusion.

### Per-`read(64K)` dispatch (4 MiB buffer, 64 reads)

| Stack | median |
|-------|-------:|
| bare `BytesIO` | 0.140 ms |
| `SlicingStream` | 0.195 ms |
| `VerifyingStream` (CRC) | 0.861 ms |
| floor `AS → VS → BytesIO` | 0.879 ms |
| fused-verify `AS → VS → Slice` | 0.933 ms |
| current `AS → VS → AS → Slice` | 0.943 ms |

CRC + Python verify loop dominates; the extra codec `ArchiveStream` hop is
~1% of this microbench. Matches the "near floor on the read path" claim.

### Construction ×1000 (build + close, no read)

| Stack | ms / 1000 |
|-------|----------:|
| `SlicingStream` | 1.6 |
| `VerifyingStream` | 1.8 |
| floor `AS → VS` | 4.5 |
| fused-verify `AS → VS → Slice` | 7.5 |
| current `AS → VS → AS → Slice` | 9.9 |

Removing the nested codec AS saves ~2.4 ms / 1000 opens (~25% of wrapper
construction). Visible on many-small-member workloads; modest on 64×256 KiB.

### `readinto`

On this host, chunked `readinto` through today's `AS → VS` is only ~6% slower
than chunked `read` — VS already goes `readinto`→`read`→bytes, and
`SlicingStream` does the same, so the fused "hash the memoryview" path cannot
avoid the slice-layer copy until `SlicingStream.readinto` is real. Peak traced
allocs were within noise (~138 KiB). **Do not sell readinto as the headline
win** of verify-fusion alone; sell codec-AS collapse + one fewer object +
correctness consolidation.

### Tie-back to #134's 2.2× / 2.4–3.7× gaps

| Gap | Wrapper share addressable by this review |
|-----|------------------------------------------|
| ZIP STORED read-all ~1.75× | Fusion recovers ~5% end-to-end (~0.4 ms of 7.4); residual is open machinery + CRC parity |
| ZIP DEFLATE read-all ~2.0–2.3× | Wrapper slice of the non-zlib bucket shrinks slightly; **decode engine still dominates** (Topic 6) |
| ZIP extract-all 2.4–3.7× | Same stream win as read-all on the stream half; FS safety half is #134 H4 |
| ZIP open+list 5–8× | **Out of scope** (not stream wrappers) |

Fusion is the right structural cleanup and a real but **small** performance
lever. It is not the budget closer.

---

## Concrete fused-stream shape (for an implementer)

```text
# ZIP STORED after fuse + collapse
ArchiveStream(                    # public: translate∘codec_translate, stamp,
                                  #          size, lease, hasher, expected_size
  inner = SlicingStream(fp, start, length, lock=ZipFile._lock)
)

# ZIP DEFLATE after fuse + collapse
ArchiveStream(                    # same knobs
  inner = DecompressorStream(SlicingStream(...))   # or accelerator
)

# TAR (unchanged — nothing to verify)
ArchiveStream(inner = [LockedStream(] ExFileObject [)])
```

Backend change pattern: replace

```python
decoded = VerifyingStream(decoded, hashes, expected_size=size, ...)
return self._wrap_member_stream(decoded, name, size=size)
```

with verify kwargs on `_wrap_member_stream` / `ArchiveStream`. Keep
`VerifyingStream` as a thin helper **or** delete it once all call sites move —
either is fine; the type is not part of the public API.

Solid 7z/RAR: keep verify **inside** the lazy `open_fn` (or equivalent fused
kwargs applied only when the pending slice actually opens) so close on an
unread handle cannot force solid positioning — the #136 constraint stays.
