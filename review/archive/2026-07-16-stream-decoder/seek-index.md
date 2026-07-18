# Seek-index correctness (F1, F5)

## F1 (High) — same-offset seek-point collision crashes the index

### The assert

`add_seek_points` merges decoder-emitted `SeekPoint`s into the sorted table. When two
points share a `decompressed_offset` (the only field `SeekPoint` orders on,
`decompressor_stream.py:33`), `_resolve_same_offset_collision` decides skip / forward-refine
/ abort:

```
# decompressor_stream.py:237
def _resolve_same_offset_collision(self, index: int, point: SeekPoint) -> None:
    existing = self._seek_points[index]
    if (existing.compressed_offset == point.compressed_offset
            and existing.state is point.state):
        return                                    # exact duplicate
    if (point.compressed_offset >= existing.compressed_offset
            and existing.state is point.state):
        self._seek_points[index] = point          # forward refinement, last-wins
        return
    assert False, (                               # <-- decompressor_stream.py:251
        "seek-point collision at the same decompressed_offset with "
        f"differing resume data: existing={existing!r} new={point!r}"
    )
```

Both safe branches require `existing.state is point.state`. **XZ is the only decoder that
puts a non-`None` `state` on its points** — the `_XzBlockBounds` object for that block
(`xz.py:583`, `xz.py:615`). Two different blocks are two different objects, so the `is`
test is `False` and control reaches `assert False`. The `add_seek_points` docstring claims
the invariant "xz/lzip should filter those away" (`decompressor_stream.py:205`); XZ does
not.

There are **two reachable ways** to produce the collision — one from valid input, one from
a tiny crafted file. Both were reproduced this session.

### F1a — valid multi-stream `.xz` + the size-then-read pattern

The XZ decoder emits two kinds of point that can share a `decompressed_offset`:

1. **Progressive stream-start points** (`xz.py:564`), emitted when a whole `.xz` stream
   completes during forward reading, with `state=None` and `compressed_offset` at the
   stream *header* start.
2. **Block-bounds points** from the backward scan (`build_index`, `xz.py:615`), with
   `state=<_XzBlockBounds>` and `compressed_offset` at the header **+ 12** (blocks begin
   after the 12-byte stream header).

For stream *k* (k > 1) starting at decompressed offset `D`, the two paths emit
`SeekPoint(D, header, None)` and `SeekPoint(D, header+12, <block>)` — same offset,
divergent state, *backwards* compressed offset → `assert False`.

The collision needs `build_index` to run **before** the progressive boundary is crossed on
the same stream. Every seek test reads forward to EOF first (setting `_index_built = True`,
`decompressor_stream.py:282`, so `build_index` never runs afterward — the safe ordering).
The unsafe ordering is the ordinary "get the size, then read" one:

```python
import io, lzma
from archivey.internal.streams.xz import XzDecompressorStream
data = b"".join(lzma.compress(p, format=lzma.FORMAT_XZ) for p in (b"A"*5000, b"B"*5000))
s = XzDecompressorStream(io.BytesIO(data), seekable=True)
s.seek(0, io.SEEK_END); s.seek(0)     # build_index first
s.read()                              # forward pass -> collision
# AssertionError: existing=SeekPoint(5000, 108, state=_XzBlockBounds...) new=SeekPoint(5000, 96, state=None)
```

The input is a **valid** concatenation of two `.xz` streams — the shape produced by
`cat a.xz b.xz` and by some parallel compressors. This makes F1 a *reliability* bug on
legitimate files, not only a hostile-input bug. (`try_get_size()` / `ArchiveStream.size`
on the same handle before reading triggers it identically.)

### F1b — 72-byte crafted `.xz` with zero-`uncompressed_size` blocks

`build_index` alone (no forward decode, no valid bodies) crashes on a crafted index.
`_parse_xz_index` rejects `unpadded_size == 0` but **not** `uncompressed_size == 0`
(`xz.py:158`), and `decompressed_start` accumulates `uncompressed_size` (`xz.py:269-273`),
so two blocks with `uncompressed_size == 0` after a non-zero block share a
`decompressed_start` with distinct `_XzBlockBounds` objects:

```
records = [(10, 100), (10, 0), (10, 0)]     # (unpadded_size, uncompressed_size)
# -> block bounds: (dstart=0, cstart=12), (dstart=100, cstart=24), (dstart=100, cstart=36)
```

`build_index_backwards` (`decompressor_stream.py:139`) maps both zero-size blocks to points
at offset 100 with different `compressed_offset` and different state objects → `assert
False`. Triggered by `seek(0, SEEK_END)` or `try_get_size()`; **the block bodies are never
decompressed**, so only the (attacker-controlled, CRC-valid) index and footer matter. The
whole file is 72 bytes. This is the clean VISION #2 hostile-input case: a crafted `.xz`
should surface `CorruptionError`, never `AssertionError`.

### Reproduction

```
uv run python review/next/03-stream-decoder-findings/repro.py     # F1a and F1b
```

### Severity / impact

- **Default CPython:** `AssertionError` propagates as a raw exception — not in the
  `ArchiveyError` tree, so it crosses the error boundary uncaught (error-contract
  violation; a crash on valid input for F1a; a DoS on a 72-byte hostile file for F1b).
- **`python -O`:** the assert is compiled out; the function falls through. For F1a the
  surviving block-bounds point still yields correct bytes (benign by luck); for F1b the
  divergent-state point silently last-wins, giving a *wrong resume state* for that offset
  (silently-wrong seek). Both `-O` outcomes are wrong in principle.

### Fix direction (maintainer decision — QUESTIONS Q1)

- **(a)** Stop the XZ decoder generating the collision: drop the redundant stream-start
  `state=None` point when a block-bounds point covers the offset (F1a), and drop/coalesce
  zero-`uncompressed_size` blocks — a zero-length span is never a useful seek target (F1b).
- **(b)** Make the merge total and hostile-safe: resolve a divergent-state collision
  (prefer the richer block-bounds `state`; its `compressed_offset` is the safe resume
  point) or translate it to `CorruptionError` — never `assert False`. An attacker who can
  craft colliding points must not be able to raise a non-`ArchiveyError`.

Recommendation: **both** — (a) so the layer stops emitting collisions, (b) so no future
decoder or hostile stream can turn a collision into a crash. As written, the assert is a
latent DoS for any codec whose points are not perfectly filtered.

---

## F5 (Low-Med, test-coverage) — no seek-math property test; old finding #6 still open

`grep -rn "given\|hypothesis" tests/test_seekable_streams.py` is empty; the seekable-stream
suite is fixed-sequence example tests, and every one consumes the stream forward-to-EOF
before seeking — the exact ordering that cannot expose F1a. The property that matters —
*"for any interleaving of `try_get_size` / `SEEK_END` / forward-read / backward-seek, the
bytes at every offset match the plaintext and no non-`ArchiveyError` escapes"* — is
untested, and it is the highest-risk arithmetic in the layer (`DecompressorStream.seek`,
`decompressor_stream.py:347-417`: buffer-relative vs seek-point-relative branches,
`SEEK_END` size discovery, the `new_pos >= size` short-circuit).

A `hypothesis` test that builds random multi-stream `.xz` / multi-member `.lz` / `.Z`
(including empty-member and zero-block fixtures), drives a random sequence of size-probes
and seeks, and asserts each `read` matches the plaintext slice, would have caught **both**
F1 triggers and would guard the seek math going forward. `hypothesis>=6.100.0` is already a
dev dependency (`pyproject.toml:112`, used in `tests/test_property_safety.py`), so there is
no new tooling cost.
