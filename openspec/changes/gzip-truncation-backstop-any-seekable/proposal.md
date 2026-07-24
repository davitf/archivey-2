# Extend the gzip ISIZE truncation backstop to any seekable source (not only paths)

## Why

`GzipCodec.open` gates the rapidgzip truncation backstop to **path** sources:

```python
# codecs.py
if isinstance(source, (str, os.PathLike)):
    return _GzipTruncationCheckStream(stream, os.fspath(source))
return stream  # non-path seekable source: rapidgzip, but NO backstop
```

So a declared-seekable **non-path** source (a caller-owned `BinaryIO` / `BytesIO`) with
`use_rapidgzip=ON` and no container-declared size gets the accelerator **without** any
truncation backstop — a silent-truncation hole that a path source of the same bytes does not
have.

**This is narrower than the spec already asks for.** `seekable-decompressor-streams` →
"Accelerator errors translate uniformly" states the backstop applies to *"seekable-source
gzip through rapidgzip"* — it does not say "path-only". The code narrowed it; the intent was
any seekable source. This change closes that gap.

The path restriction is incidental, not fundamental. Walking the two things
`_GzipTruncationCheckStream` uses the path for:

- **The ISIZE trailer read is already done up front for any seekable source** —
  `_gzip_isize_from_source` seeks to end and restores position on a bare `BinaryIO`
  (codecs.py). But `_config_with_gzip_isize` keeps only a **boolean**
  `gzip_isize_backstop=True` and **discards the value**; `_verify_not_truncated` then re-opens
  the *path* at EOF and reads the same four bytes again. Capturing the value up front removes
  the only EOF-time need for a path.
- **The empty→stdlib fallback only fires after rapidgzip is closed.**
  `_begin_stdlib_fallback` runs solely on the `total == 0` empty-EOF branch and its first act
  is `old.close()`. rapidgzip is done — there is no concurrent-cursor conflict — so the
  seekable source can be rewound (`seek(0)`) and handed to `GzipDecompressorStream`, exactly as
  a fresh path handle is today.

The non-empty soft-short compare that runs while rapidgzip is still live then needs **no** EOF
seek at all, because ISIZE was captured before the accelerator started.

Two real obstacles remain — both already-solved patterns in this repo, captured in `design.md`:

1. **rapidgzip may close the caller-owned source when the accelerator closes.** Reuse the
   existing non-owning wrapper (`ensure_bufferedio` / `_NonClosingBufferedReader` in
   `streamtools/binaryio.py`) so `old.close()` does not take the caller's stream down and the
   fallback can rewind and reuse it.
2. **rapidgzip Bug 3 — `terminate()` on a Python source that raises mid-decode.** This exposure
   is **pre-existing and independent of this change**: we already run rapidgzip on non-path
   seekable sources today (the `return stream` line above), just with less safety. Mitigation
   to investigate: have the inner source wrapper **catch every exception internally** (never let
   one propagate into rapidgzip's C++), record it, and surface it to the `_AcceleratorStream`
   outer wrapper so archivey re-raises it as a translated `compressed-streams` error. See
   `design.md` — this is the gating investigation and carries the one open decision.

## What Changes

- **`seekable-decompressor-streams`** — MODIFY "Accelerator errors translate uniformly": state
  that the gzip ISIZE backstop applies to **any declared-seekable source** (path or
  caller-owned `BinaryIO`), realized by capturing ISIZE up front and rewinding the source for
  the empty→stdlib fallback, and that a caller-owned source is never closed by the accelerator
  (non-owning wrapper). No detection/format change; no new public surface.
- **No behavior change for path sources** — they already have the backstop; this generalizes the
  mechanism they use.

This change is **investigation + specs + implementation**: the source-lifetime and Bug-3
hardening (`design.md`) are prerequisites; the backstop generalization lands once they hold.

## Specs

Proposed delta in `specs/seekable-decompressor-streams/spec.md` (kept here until accepted).
Sibling change `gzip-multimember-detect-via-index` modifies the same requirement — sequence /
rebase the deltas when the second lands.
