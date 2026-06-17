# Archivey — Future Ideas / Backlog

> **Status: speculative.** Nothing here is committed or scheduled. These are
> "might do later, worth remembering" notes — *not* part of the `PLAN.md` phase
> roadmap. Firm, decided v1 deferrals (async, in-place modify, sparse-file
> extraction, multi-volume joining, etc.) live in `openspec/project.md`
> ("Deferred / out of scope (v1)") and `SPEC.md` Appendix A — this file is the
> looser idea pile. Promote an item by writing a real spec/`openspec` change for it.

## Backends & format coverage

- **Native streaming ZIP reader** — a native parser that does what stdlib `zipfile`
  can't: read from **non-seekable** streams (pipes/sockets) and **truncated / no-EOCD**
  archives by walking local file headers forward, plus better coverage of data
  descriptors, ZIP64 edge cases, extra fields, and **AES/WinZip encryption** (zipfile
  only does legacy ZipCrypto). Fits the native-first direction (cf. 7z/RAR). Streaming
  mode is forward-only and sizes/CRC arrive in trailing data descriptors — i.e. the
  **late-bound `ArchiveMember` fields** + `FORWARD_ONLY`/`is_solid=False` cost model we
  already designed for. Lands as a native variant of `formats/zip_reader.py`.

- **libarchive backend** — `python-libarchive-c` as an **alternative / additional**
  backend for several formats (zip/tar/7z/iso/cpio/…), in the `[all]`/alternative tier
  behind a `[libarchive]` extra. Caveats: native C dependency (the packaging-finicky
  axis `[recommended-lite]` exists to avoid), stream-oriented (weak random access,
  historically no solid-RAR support).

- **Synthetic single-stream RAR → libarchive** — generalize rarfile's "hack": build a
  minimal artificial RAR stream containing a single file (or one solid block) and feed
  it to libarchive's RAR decompressor, as an alternative to shelling out to the external
  `unrar` binary. Could remove the `unrar` runtime requirement for common cases.
  **Higher-risk / research spike** — RAR decode correctness is hard and libarchive's
  RAR5 coverage is partial; `unrar` remains the reference.

- **Subprocess decompressor streams** — a single reusable `SubprocessDecompressorStream`
  that pipes compressed/uncompressed data through a system binary (`zstd`, `xz`,
  `brotli`, `lz4`, …) as an alternative to installing the Python codec libs. Same pattern
  we already use for `unrar`; valuable in locked-down environments where C-extension
  wheels won't install but CLI tools are on PATH. Forward-only; needs availability
  detection and careful subprocess/error handling. Low-priority backend tier.

## API & ergonomics

- **Pathlib-like navigation** — an `ArchivePath` supporting `/` joining, `iterdir()`,
  `glob()`, `read_bytes()`, `is_dir()`, … over the member tree (precedent: `zipfile.Path`).
  Read-only wrapper; needs random access, so a `DIRECT`/indexed-archive convenience.

- **fsspec filesystem** — expose an opened archive as an `fsspec` filesystem so
  pandas/dask/pyarrow/etc. can read members by path. Pairs naturally with the pathlib
  navigation layer.

## Performance & robustness

- **Parallel extraction** — extract independent members concurrently for
  `AccessCost.DIRECT` archives (bounded by I/O). No benefit for `SOLID` (members share a
  decompression stream).

- **Seek-index persistence** — save/load the gzip/bz2/xz seek points (the index built by
  `seekable-decompressor-streams`) to disk so repeated random access into the same file
  is cheap across runs.

- **Archive repair / recovery mode** — best-effort extraction of corrupt or truncated
  archives, returning what is readable rather than failing the whole operation.
  Synergizes with the native streaming ZIP reader.

- **Integrity-verify mode** — verify every member against its stored checksum without
  writing anything to disk (a read-only "scrub" pass).
