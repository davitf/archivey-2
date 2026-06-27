# Unify the read-only stream wrappers behind a shared base

## Why

The stream layer has ~10 classes that all subclass `(io.RawIOBase, BinaryIO)` and each
re-implement the full read-only `BinaryIO` surface, even though most of it is identical
boilerplate. Inventory (all in `internal/streams/…`):

| Class | Real behavior it adds | Boilerplate it repeats |
|---|---|---|
| `ArchiveStream` | lazy open + exception translation on every call | `readable/writable/seekable/write`, a `readinto` variant |
| `_AcceleratorStream` | close-on-finalize guard | `readinto/seek/tell/readable/writable/seekable` |
| `_SlowSeekWarningStream` | warn once on a rewinding seek | `read/readinto/tell/readable/writable/seekable/close` |
| `_GzipTruncationCheckStream` | ISIZE truncation backstop | `readinto/tell/readable/writable/seekable/close` |
| `_ZstdReopenStream` | reopen-to-seek-backward | `readinto/tell/readable/writable/seekable/close` |
| `VerifyingStream` | hash + verify digest at EOF | `readinto/readall/readable/writable/seekable/close` |
| `SlicingStream` | expose a sub-range (offset remap) | `readinto/readable/writable` |
| `PeekableStream` | buffer a prefix of a non-seekable source | `readinto/readable/writable/seekable` |
| `DecompressorStream` | base for xz/zlib/brotli/lzip; seek-by-redecompress | `readinto/readall/readable/writable` |
| `BinaryIOWrapper` | adapt a *partial/duck-typed* file API to `BinaryIO` | `readable/writable` |

Two concrete problems, not just aesthetics:

1. **`readinto` is implemented at least three different ways** across these classes:
   delegate to `inner.readinto` with a `read()` fallback (e.g. `_SlowSeekWarningStream`,
   `_AcceleratorStream`, `ArchiveStream`); a plain `read()`-into-memoryview copy (e.g.
   `_GzipTruncationCheckStream`, `VerifyingStream`); and `BinaryIOWrapper`'s variant that also
   handles `None` returns and a `readinto` that raises `NotImplementedError`. Divergent copies
   of a tricky primitive are exactly where subtle bugs hide (the non-blocking `None` case is
   handled in some and not others).

2. **`io.RawIOBase`'s defaults are the wrong way round for these wrappers.** `RawIOBase`
   defaults `readable()/writable()/seekable()` to `False` and implements `read()`/`readall()`
   *in terms of* `readinto()` — so every read-only wrapper must override `readable()→True`,
   `writable()→False`, and provide a `readinto`. A shared base can invert that once (provide
   `readinto`/`readall` *in terms of* the subclass's `read`) and set the read-only answers, so
   a new wrapper is "override `read` (+ the one method you actually change)".

Adding a new wrapper today means re-typing 6-8 boilerplate methods and picking one of the
`readinto` variants — easy to get subtly wrong, and noise that obscures the ~1 method that is
the actual point of the class.

## What Changes

Introduce two small internal base classes in `internal/streams/streamtools` (no public API,
no behavior change):

- **`ReadOnlyIOStream(io.RawIOBase, BinaryIO)`** — the shared read-only surface:
  - `readable() → True`, `writable() → False`, `write()` raises `io.UnsupportedOperation`;
  - `readinto(b)` implemented once in terms of `self.read(len(b))` (the canonical copy,
    including the non-blocking `None` guard), and `readall()` as the standard read-loop;
  - `read()` left abstract (raises `NotImplementedError`) so a subclass that forgets it fails
    loudly instead of infinite-looping through `RawIOBase`.
  - Does **not** define `seek/tell/seekable/close` — those genuinely vary (sequential vs.
    seekable, owns-inner vs. not), so subclasses still declare them explicitly.

- **`DelegatingStream(ReadOnlyIOStream)`** — for the wrappers that hold one inner `BinaryIO`
  and forward to it: stores `self._inner`, and forwards `read/seek/tell/seekable/close` (and a
  zero-copy `readinto` straight to `inner.readinto` where present). A subclass overrides only
  the method whose behavior it changes (`_SlowSeekWarningStream` → just `seek`;
  `_AcceleratorStream` → just `close`; `_ZstdReopenStream` → just `seek`;
  `_GzipTruncationCheckStream` → `read` + `seek`).

Migration (behavior-preserving), per class:

- **On `DelegatingStream`:** `_SlowSeekWarningStream`, `_GzipTruncationCheckStream`,
  `_ZstdReopenStream`, `_AcceleratorStream` (keeps its `weakref.finalize` close guard),
  `VerifyingStream` (sequential: keep `seekable()→False`, override `read`).
- **On `ReadOnlyIOStream` only** (they remap offsets or wrap differently, so plain delegation
  doesn't fit): `SlicingStream`, `PeekableStream`, `ArchiveStream` (its per-call exception
  translation is the whole point and shouldn't be hidden behind silent delegation),
  `DecompressorStream`.
- **`BinaryIOWrapper` stays a distinct class but inherits `ReadOnlyIOStream`** for the trivial
  bits (`readable/writable/write`), keeping its specialized `read`/`readinto` (the `None` /
  non-blocking / `readinto`-raises handling). Rationale: its inner is a *duck-typed, possibly
  non-`BinaryIO`* object — a different contract ("adapt the unknown") from the others ("wrap a
  known `BinaryIO`"), so it should not pretend to be a `DelegatingStream`.

This is purely internal plumbing; the `compressed-streams` behavior (read-only, not-writable,
`readinto` semantics) is unchanged — it's just stated once instead of ten times.

## Options considered

1. **Do nothing.** Pro: each class is self-contained and greppable; the boilerplate is cheap
   to read. Con: the `readinto` triplication is a real correctness hazard, and every new
   wrapper re-pays the boilerplate tax (the `codec-descriptor-refactor` shows the codebase
   values "add one thing, not boilerplate in N places").
2. **`ReadOnlyIOStream` only** (the read-only surface + canonical `readinto`). Pro: kills the
   `readinto`/`readable`/`writable` duplication — the highest-value, lowest-risk slice — and
   applies to all 10. Con: the delegating wrappers still hand-write `seek/tell/seekable/close`.
3. **`ReadOnlyIOStream` + `DelegatingStream`** (recommended). Pro: the pure delegators shrink to
   their one real method; intent is obvious from the base. Con: a second base class, and care
   needed so the delegating `seekable()`/`readinto` passthrough preserves each wrapper's exact
   semantics (the sequential ones must keep `seekable()→False`).
4. **Fold `BinaryIOWrapper` into the delegating base too.** Rejected: its inner isn't a
   `BinaryIO` and its `read` has bespoke `None`/non-blocking handling; forcing it into the
   delegation contract would either weaken the base or special-case it back out.

**Recommendation: Option 3**, with `BinaryIOWrapper` on `ReadOnlyIOStream` only. It removes the
most error-prone duplication (one `readinto`), makes each wrapper's purpose legible, and keeps
the one genuinely-different adapter honest.

## Risks & sequencing

- **Behavior-preserving refactor**, gated by the existing stream tests (slice / peekable /
  verify / decompressor / codec / detection / single-file) — they must pass unchanged.
- **Sequence after PR #16** (`codec-descriptor-refactor`) lands: that PR rewrites `codecs.py`
  heavily (where four of these wrappers live), so doing this first would guarantee conflicts.
- Watch two semantic traps when migrating: (a) sequential wrappers (`VerifyingStream`,
  `PeekableStream`) must **not** inherit a default `seekable()→True`; (b) the delegating
  `readinto` passthrough must keep `ArchiveStream`'s per-call translation, so `ArchiveStream`
  deliberately does not use `DelegatingStream`.

## Can any wrappers merge? (ArchiveStream is the public surface)

**Decision:** the object handed back to callers is always an `ArchiveStream`, so cross-cutting,
*presentation*-level stream features (rewind-is-slow warnings, and later seek cost, a seek-point
list, etc.) have one home to grow in. `open_codec_stream()` already guarantees this; the internal
`backend.open()` (`resolve_codec(...).open()`) stays a deliberate escape hatch for transient
internal opens (`SingleFileReader._probe_decompressed_size` today; the TAR / 7z folder pipelines
later), which manage the raw stream themselves.

That single fact — `ArchiveStream` is *always* present on the public path but *not* on
`backend.open()` — decides each merge:

- **Fold `_SlowSeekWarningStream` into `ArchiveStream`. ✅** The warning is presentation, and the
  stakes of missing it are just a log line. `ArchiveStream` takes an optional "rewind is O(n)"
  signal (codec name + accelerator name, ideally sourced from the codec descriptor) and warns
  once when a `seek` lands before the current position. This removes a class *and* a wrapper
  layer, and it's the natural first tenant of the "seek-aware presentation" features the public
  `ArchiveStream` is meant to grow. Internal `backend.open()` callers simply don't warn — fine,
  they're transient and internal.

- **Do NOT fold the accelerator close-guard into `ArchiveStream`. ❌** It must attach where the
  rapidgzip object is *created*, because `backend.open()` can hand out an accelerator object with
  no `ArchiveStream` around it (the size probe today; TAR/7z member opens later) — moving the
  guard up would let those paths reach interpreter finalization with a live C++ worker thread and
  re-introduce the macOS SIGABRT. Safety belongs at the object's birth, not at an outer layer some
  callers skip. Keep it as the thin `_AcceleratorStream` (a `DelegatingStream` whose only job is
  the `weakref.finalize` close guard). Note this is *not* general "always close" hygiene —
  ordinary streams already close via `io.IOBase.__del__`; the finalize guard exists only because
  rapidgzip's thread must be stopped deterministically even under cyclic GC / at shutdown, where
  `__del__` ordering is unreliable, and the callback must avoid referencing the wrapper (a bound
  `self.close` would pin it and prevent GC-time finalization).

Net wrapper inventory after this change **+** the bases above: `ArchiveStream` (lifecycle +
exception translation + rewind warning + future seek metadata), the `ReadOnlyIOStream` /
`DelegatingStream` bases, `_AcceleratorStream` (close guard only), `_GzipTruncationCheckStream`,
`_ZstdReopenStream`, `SlicingStream`, `PeekableStream`, `VerifyingStream`, `DecompressorStream`,
`BinaryIOWrapper` — with **`_SlowSeekWarningStream` removed**.

## Specs

No behavioral requirement changes — the `compressed-streams` contract is unchanged. Two small
clarifying lines are warranted: (1) that read-only stream wrappers share an internal base
providing the read-only surface + a canonical `readinto`, and (2) that the public/codec-stream
surface is always an `ArchiveStream` (the documented place for stream-level metadata to grow).
Otherwise this is implementation-only.

