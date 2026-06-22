# Tasks — Seekable blocked-gzip reading + block-split writing

> Specs-only proposal. These tasks describe the implementation when the change is
> accepted and scheduled; nothing is implemented here. Run tools through `uv`
> (`uv run pytest`, `uv run pyrefly check`, `uv run ty check`, `uv run ruff`).
> Depends on the Phase-2 stream layer (`DecompressorStream`/`SeekPoint`, codec registry).

## 1. Native blocked-gzip random access (BGZF / mgzip) — zero-dep

- [ ] 1.1 Add a `BgzfDecompressorStream` (a `DecompressorStream` subclass) that, on a
      seekable source, recognizes blocked gzip from the first member's extra field:
      the BGZF `BC` subfield (`BSIZE`, ≤64 KiB) or the mgzip `MZ` subfield (4-byte
      compressed-member-size body).
- [ ] 1.2 Build the `SeekPoint` index by walking members without decompressing: per-member
      compressed size from the extra subfield; per-member uncompressed size from the gzip
      `ISIZE` trailer. Decode a seek by resetting to the member containing the target.
- [ ] 1.3 Wire it into the gzip codec path: when the source is seekable and blocked-gzip is
      recognized, use this backend; otherwise fall back to the existing sequential/
      accelerator path (no rewind warning needed when the index is present).
- [ ] 1.4 Report random-access capability in the cost model (non-SOLID `access_cost`,
      `seekable` flag) when the index is available.
- [ ] 1.5 Leave non-blocked gzip untouched (still sequential, still warns on rewind).

## 2. `indexed_gzip` alternative accelerator (optional)

- [ ] 2.1 Add an `indexed_gzip` backend for arbitrary gzip random access, resolved by the
      same access-mode-aware config as `rapidgzip`, gated by `[seekable]`.
- [ ] 2.2 Translate `indexed_gzip` exceptions into the v2 hierarchy; clean absence/disabled
      behavior (fall back to another accelerator, else sequential).
- [ ] 2.3 (Optional) expose import/export of its persistent index (`.gzidx`).
- [ ] 2.4 Add `indexed_gzip` to the `[seekable]` extra in `pyproject.toml`.

## 3. Block-split writing (`block_size`)

- [ ] 3.1 Add a `block_size` write option to single-file compressor writing.
- [ ] 3.2 gzip → emit BGZF (independent ≤64 KiB members + `BC` subfield + 28-byte EOF
      marker); verify standard `gzip` decompresses it and the native reader (task 1) seeks it.
- [ ] 3.3 xz → set the `lzma` stream `block_size`; verify the XZ index reader seeks it.
- [ ] 3.4 zstd → write the zstd seekable format (skippable-frame seek table).
- [ ] 3.5 A codec with no block mechanism ignores/rejects `block_size` rather than writing a
      non-seekable stream that claims to be blocked. Default off (one solid stream).

## 4. Tests

- [ ] 4.1 BGZF read: index built without full decode; backward/forward seeks decode only the
      needed block(s); cross-validate against a `bgzip`/pysam-produced fixture when available
      (skip cleanly otherwise).
- [ ] 4.2 mgzip read: `MZ`-subfield index; seek correctness; cross-validate against an
      `mgzip`-produced fixture when available.
- [ ] 4.3 Plain gzip is not misdetected as blocked (no `BC`/`MZ` → sequential path).
- [ ] 4.4 `indexed_gzip` present/absent (skip when not installed); generic-gzip seek works.
- [ ] 4.5 Round-trip: write gzip with `block_size` → read back via the native BGZF reader and
      via stdlib `gzip`; write xz with `block_size` → seek via the XZ index.

## 5. Verify — acceptance

- [ ] 5.1 All added `seekable-decompressor-streams` scenarios covered (BGZF, mgzip, plain-gzip
      fallback, indexed_gzip present/absent).
- [ ] 5.2 All added `format-single-file-compressors` write scenarios covered.
- [ ] 5.3 Zero-dependency core unchanged: the native blocked-gzip reader uses only stdlib
      `zlib` (assert no new core dependency). `indexed_gzip` stays optional under `[seekable]`.
- [ ] 5.4 `uv run pyrefly check`, `uv run ty check`, `uv run ruff check` clean; new tests green.

## 6. Sync specs

- [ ] 6.1 On acceptance, sync the `## Specs` deltas into `openspec/specs/` (the
      `seekable-decompressor-streams`, `packaging-and-extras`, and
      `format-single-file-compressors` requirements) and register nothing new in the
      capability map (no new capability — these extend existing ones).
