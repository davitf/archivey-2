# seek-index.md — seek-point correctness across the discovery paths

## F1 (High) — XZ zero-`uncompressed_size` blocks crash the seek-index build

**Claim:** a crafted `.xz` whose index declares two or more blocks with
`uncompressed_size == 0` (after at least one non-zero block) makes the seek-index build
emit two `SeekPoint`s at the *same* `decompressed_offset` carrying *different* `state`
objects, which trips `assert False` in `_resolve_same_offset_collision` →
**`AssertionError`** (an uncaught crash, outside the `ArchiveyError` tree, and silently
stripped under `python -O`).

### The mechanism

`SeekPoint` orders by `decompressed_offset` only (`decompressor_stream.py:33`). When two
points share an offset, `add_seek_points` routes to `_resolve_same_offset_collision`
(`decompressor_stream.py:237`):

```python
def _resolve_same_offset_collision(self, index, point):
    existing = self._seek_points[index]
    if existing.compressed_offset == point.compressed_offset and existing.state is point.state:
        return                                   # exact dup
    if point.compressed_offset >= existing.compressed_offset and existing.state is point.state:
        self._seek_points[index] = point         # forward refinement, last-wins
        return
    assert False, (...)                          # <-- decompressor_stream.py:251
```

Both "safe" branches require `existing.state is point.state`. XZ is the only decoder that
puts a **non-`None`** `state` on its points — the `_XzBlockBounds` object for that block
(`xz.py:583`, `xz.py:615`). Two different blocks are two different objects, so `is` is
`False` and control reaches `assert False`.

Two blocks share a `decompressed_offset` **iff** the block(s) between them have
`uncompressed_size == 0`, because `decompressed_start` accumulates `uncompressed_size`
(`xz.py:269-273`). `_parse_xz_index` rejects `unpadded_size == 0` but **not**
`uncompressed_size == 0` (`xz.py:158`), so zero-decompressed blocks pass index validation.

### Two reachable triggers, neither requires decoding the block bodies

- **One-shot backward scan** (`build_index`, `xz.py:608`): fired by `seek(0, SEEK_END)`,
  a forward seek past the known frontier, or `try_get_size()`. `build_index_backwards`
  (`decompressor_stream.py:139`) maps every block with `decompressed_start > last_known`
  to a point and calls `add_seek_points`. The two zero-size blocks both map to the same
  offset → assert. **The block bodies are never decompressed on this path**, so the garbage
  in the block payload is irrelevant — only the (attacker-controlled, CRC-valid) index and
  footer are read.
- **Progressive enrichment** (`_progressive_stream_points`, `xz.py:582`): same emission,
  but gated behind the stream actually decoding to `eof`, so it additionally needs the
  bodies to be valid LZMA2. The one-shot path is the clean trigger.

### Reproducer

`repro/xz_craft.py` builds the file from the module's own `_encode_mbi`/`_round_up_4`
helpers (records `[(10,100),(10,0),(10,0)]`, correct CRCs); `repro/xz_crash.py` and
`repro/xz_size.py` trigger it:

```
$ python repro/xz_crash.py
ASSERTIONERROR (crash on hostile input): AssertionError('seek-point collision at the
same decompressed_offset with differing resume data: existing=SeekPoint(
decompressed_offset=100, compressed_offset=24, state=_XzBlockBounds(...
$ python repro/xz_size.py
ASSERTIONERROR via try_get_size: AssertionError('seek-point collision ...
```

The crafted file is only 72 bytes.

### Why it matters (VISION #2)

"Parse untrusted archives without native-code parser attack surface / memory-safe
hostile-input parsing" — a hostile `.xz` should surface a `CorruptionError`, not an
`AssertionError`. Under `-O` the assert vanishes and the divergent-state point silently
**last-wins**, so the same input then produces a *wrong* resume state for that offset
(a silently-wrong seek) instead of a crash. Both outcomes are wrong.

### Fix sketch (for the maintainer to rule on — see QUESTIONS.md)

The genuinely-divergent case (`point.state is not existing.state`,
`point.compressed_offset >= existing.compressed_offset`) on a **zero-length decompressed
span** is not corruption in the "reject the file" sense — it is a legal-but-degenerate
index. Options: (a) translate the divergent collision to `CorruptionError` instead of
asserting; (b) drop `uncompressed_size == 0` blocks (or coalesce equal-offset points)
before they reach `add_seek_points`, since a zero-length block is never a useful seek
target; (c) reject `uncompressed_size == 0` in `_parse_xz_index` (strictest; check whether
any real encoder emits empty blocks first — standard `xz` does not).

## What is fine on the other three discovery paths

- **Progressive boundary (lzip, xz stream-start):** before-placement points at member/stream
  start; lzip's `state` is always `None` so same-offset collisions (empty members)
  resolve last-wins correctly — verified with a 3-member lzip containing two empty members
  (`repro/` lzip check: seeks to size 4, no assert).
- **Progressive enrichment `inner` save/restore:** airtight (`xz.py:575`/`:603` save +
  `finally`-restore; base re-restores at `decompressor_stream.py:335`). Not a concurrency
  hazard under the sync-only v1 contract.
- **`stream_cell` late-bound closures** (`xz.py:638-667`): a faithful port of the old
  subclass coupling. `get_seek_points`/`index_built` only *read* `stream._seek_points`/
  `_index_built`; `build_index` and `feed` are never interleaved under the single-threaded
  contract, so there is no read-vs-mutate race. Correct as-is (the design already flags it
  as the thing to replace with an explicit handle when BGZF needs it).

## F5 (Low, test-coverage) — no seek-math property test

The brief's old finding #6 is still open. `tests/test_seekable_streams.py` exercises seek
with fixed offsets (`10000`, `12345`, a couple of block-boundary crossings) and no
`hypothesis`/randomized generator appears anywhere in the stream tests. The seek arithmetic
in `DecompressorStream.seek` (`decompressor_stream.py:347-417`) — buffer-relative vs
seek-point-relative branches, `SEEK_END` size discovery, the `new_pos >= size` short-circuit
— is the highest-risk arithmetic in the layer and has no property test asserting
`seek(k); read(n) == plaintext[k:k+n]` for random `k, n` across single-stream, multi-stream,
multi-block, and empty-member fixtures. Worth adding; it would also have caught F1 with a
zero-block fixture in the generator.
