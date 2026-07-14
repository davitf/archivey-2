# Acknowledgements

None of this would exist without the people who built the formats, libraries, and tools
Archivey leans on — whether we depend on their code directly, learned from their design,
or just used their project to check our own work. This page is where we say thanks and
give credit properly.

License texts for adapted kernels live next to the code
(`src/archivey/internal/streams/unix_compress.py`,
`src/archivey/internal/backends/rar_parser.py`). Packaging extras and codec rationale:
[Formats](formats.md), [library analysis](internal/library-analysis.md).

## Thanks

Thanks to the maintainers and contributors of every project below — especially the ones
whose work shaped our design without ever becoming a dependency. When we built our own
parser or stream layer instead of wrapping a library, it’s usually because it needed to
fit Archivey’s architecture and behave consistently across formats, not because the
original wasn’t good. If we missed a project that should be here, open an issue or a
PR — genuinely.

## Adapted source

| Project | Role in Archivey | License |
| --- | --- | --- |
| [uncompresspy](https://github.com/kYwzor/uncompresspy) (Tiago Gomes) | LZW kernel for unix-compress (`.Z`) adapted into `UnixCompressDecompressorStream` / `LzwState`. **Not** a runtime dependency; `[unix-compress]` was removed when the kernel was vendored into core. | BSD-3-Clause |
| [rarfile](https://github.com/markokr/rarfile) (Marko Kreen) | RAR3 SHA-1 / string-to-key and Unicode filename decompression ported into the native RAR metadata parser. Also a **dev-only** oracle and fixture source (below). | ISC |

## Format references, oracles, and corpora

These are not on the primary read path (except where noted).

| Project | Role |
| --- | --- |
| [py7zr](https://github.com/miurahr/py7zr) (Hiroshi Miura et al.) | 7z **format reference**, write backend under `[7z-write]`, and **dev oracle** / optional external corpus (`ARCHIVEY_PY7ZR_TEST_FILES`). Reading is native — see [ADR 0001](decisions/0001-native-7z-not-py7zr.md). |
| [rarfile](https://github.com/markokr/rarfile) | RAR **format reference**, **dev oracle**, optional corpus (`ARCHIVEY_RARFILE_TEST_FILES`), and two legacy fixtures under `tests/fixtures/rar/` (RAR 1.5 / 2.0). Reading metadata is native; data uses RARLAB `unrar` — see [ADR 0002](decisions/0002-native-rar-metadata-unrar-data.md). |
| [libarchive](https://github.com/libarchive/libarchive) (+ [libarchive-c](https://github.com/Changaco/python-libarchive-c)) | Optional **cross-format corpus** oracle (`ARCHIVEY_LIBARCHIVE_TEST_FILES` → libarchive’s `libarchive/test` uuencoded archives). Dev-only; not a runtime backend. |
| [7-Zip](https://www.7-zip.org/) / `7z` CLI ([p7zip](https://github.com/p7zip-project/p7zip)) | Fixture builder and anti-item / encrypted-ZIP oracle in tests (when installed). |
| [RARLAB](https://www.rarlab.com/) `unrar` / `rar` | Runtime decompressor for RAR **member data**; fixture generator for committed RAR samples. |

## Seekable-stream design references

Indexed / seekable decompressors shaped the stream layer even when Archivey does **not**
depend on them. Full scoring lives in [library analysis](internal/library-analysis.md);
the single-accelerator constraint is in [ADR 0008](decisions/0008-single-accelerator-rapidgzip.md)
and [known issues](internal/known-issues.md).

| Project | Role |
| --- | --- |
| [python-xz](https://github.com/Rogdham/python-xz) (Rogdham) | Design reference for native XZ block-index seeking / synthetic single-block streams (`xz.py` over stdlib `lzma`). Evaluated and **not** used as a dependency (was briefly pinned dead in `[all]`, then removed). |
| [rapidgzip](https://github.com/mxmlnkn/rapidgzip) (mxmlnkn) | Runtime `[seekable]` accelerator for gzip **and** bzip2 (`IndexedBzip2File` bundled inside rapidgzip). |
| [indexed_bzip2](https://github.com/mxmlnkn/indexed_bzip2) / [indexed_gzip](https://github.com/pauldmccarthy/indexed_gzip) | Evaluated for random access; standalone `indexed_bzip2` is **deliberately not** imported (macOS dual-load heap corruption with rapidgzip). Same author lineage as rapidgzip; also relevant via [ratarmount](https://github.com/mxmlnkn/ratarmount). |
| [indexed_zstd](https://github.com/martinellimarco/indexed_zstd) (martinellimarco) | Evaluated for efficient seeking over arbitrary `.zst`; **deferred** (frame-granularity only; C++ coexistence risk). Tracked in `IDEAS.md`. |
| [pyzstd](https://github.com/animalize/pyzstd) | Evaluated for zstd decode / `SeekableZstdFile` (Seekable Zstd container only). Decode instead targets stdlib `compression.zstd` / [backports.zstd](https://github.com/Rogdham/backports.zstd) — see [ADR 0009](decisions/0009-zstd-stdlib-backports.md). |
| [zstandard](https://github.com/indygreg/python-zstandard) | Former zstd backend; replaced after the compression-library evaluation. |

## Runtime dependencies (optional extras)

Bare `pip install archivey` has **no** third-party runtime deps. Named extras pull:

| Extra | Packages |
| --- | --- |
| `[7z]` | [pyppmd](https://github.com/miurahr/pyppmd), [inflate64](https://github.com/miurahr/inflate64), [brotli](https://github.com/google/brotli), [lz4](https://github.com/python-lz4/python-lz4), [cryptography](https://github.com/pyca/cryptography), [pybcj](https://github.com/miurahr/pybcj), [backports.zstd](https://github.com/Rogdham/backports.zstd) (Python before 3.14) |
| `[rar]` / `[crypto]` | [cryptography](https://github.com/pyca/cryptography) (Blake2sp backend still TBD) |
| `[7z-write]` | [py7zr](https://github.com/miurahr/py7zr) |
| `[iso]` | [pycdlib](https://github.com/clalancette/pycdlib) |
| `[zstd]` | [backports.zstd](https://github.com/Rogdham/backports.zstd) on Python before 3.14; 3.14+ uses stdlib `compression.zstd` |
| `[lz4]` | [lz4](https://github.com/python-lz4/python-lz4) |
| `[cli]` | [tqdm](https://github.com/tqdm/tqdm) |
| `[seekable]` | [rapidgzip](https://github.com/mxmlnkn/rapidgzip) |

`[recommended]` / `[recommended-lite]` / `[all]` are convenience aliases over the table
above (`[all]` currently equals `[recommended]`).

**Stdlib** (always): [`zipfile`](https://docs.python.org/3/library/zipfile.html),
[`tarfile`](https://docs.python.org/3/library/tarfile.html),
[`gzip`](https://docs.python.org/3/library/gzip.html),
[`bz2`](https://docs.python.org/3/library/bz2.html),
[`lzma`](https://docs.python.org/3/library/lzma.html),
[`zlib`](https://docs.python.org/3/library/zlib.html), and on 3.14+
[`compression.zstd`](https://docs.python.org/3/library/compression.zstd.html).

## Development and test dependencies

Declared in the PEP 735 `dev` / `docs` / `fuzz` groups (not user-facing extras):

| Package | Use |
| --- | --- |
| [py7zr](https://github.com/miurahr/py7zr), [rarfile](https://github.com/markokr/rarfile), [libarchive-c](https://github.com/Changaco/python-libarchive-c) | Format oracles / optional external corpora |
| [ncompress](https://github.com/valgur/ncompress) | LZW **compressor** for `.Z` fixtures (decode is native) |
| [pycdlib](https://github.com/clalancette/pycdlib), [cryptography](https://github.com/pyca/cryptography), [backports.zstd](https://github.com/Rogdham/backports.zstd), [lz4](https://github.com/python-lz4/python-lz4), [brotli](https://github.com/google/brotli) | Exercise optional backends in the default dev env |
| [urllib3](https://github.com/urllib3/urllib3), [fsspec](https://github.com/fsspec/filesystem_spec) | Real third-party stream objects in input tests |
| [hypothesis](https://github.com/HypothesisWorks/hypothesis) | Property tests for safety logic |
| [pytest](https://github.com/pytest-dev/pytest), [pytest-cov](https://github.com/pytest-dev/pytest-cov), [pytest-timeout](https://github.com/pytest-dev/pytest-timeout), [coverage](https://github.com/nedbat/coveragepy) | Test runner / coverage |
| [ruff](https://github.com/astral-sh/ruff), [pyrefly](https://github.com/facebook/pyrefly), [ty](https://github.com/astral-sh/ty), [pre-commit](https://github.com/pre-commit/pre-commit) | Lint and type-check |
| [mkdocs](https://github.com/mkdocs/mkdocs), [mkdocs-material](https://github.com/squidfunk/mkdocs-material), [mkdocstrings](https://github.com/mkdocstrings/mkdocstrings), … | Docs site (`docs` group) |
| [atheris](https://github.com/google/atheris) | Coverage-guided fuzz (`fuzz` group; CI-only) |
