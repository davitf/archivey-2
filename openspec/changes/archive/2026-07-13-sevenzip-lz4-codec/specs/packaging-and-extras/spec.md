## MODIFIED Requirements

### Requirement: Optional extras enable specific capabilities

The system SHALL gate each optional capability behind a named extra that pulls the
third-party dependency required for that capability. 7z and RAR reading are native
for the common case, so extras cover less-common 7z codecs, encryption, 7z writing,
ISO, extra compression formats, seeking accelerators, and the CLI.

| Extra | Pulls in | Enables |
| --- | --- | --- |
| *(none)* | stdlib only + native parsers | ZIP, TAR + stdlib compressed TAR variants, GZ, BZ2, XZ, directory, 7z read for common codecs (including LZMA2+BCJ), RAR metadata/listing; RAR data still needs RARLAB `unrar` |
| `[7z]` | `pyppmd`, `inflate64`, `backports.zstd` on Python <3.14, `brotli`, `lz4`, `cryptography`, `pybcj` | All 7z reading features: PPMd, Deflate64, Zstd, Brotli, LZ4, AES, LZMA1+BCJ |
| `[rar]` | `cryptography`, Blake2sp backend | Header-encrypted RAR5 and Blake2sp checksum verification; RAR data still needs RARLAB `unrar` |
| `[crypto]` | `cryptography` | AES/crypto backend subset used by `[7z]` / `[rar]` |
| `[7z-write]` | `py7zr` | 7z writing only; reading remains native |
| `[iso]` | `pycdlib` | ISO 9660 (`.iso`) |
| `[zstd]` | `backports.zstd` on Python <3.14 | Standalone Zstandard (`.zst`, `.tar.zst`); Python 3.14+ uses stdlib `compression.zstd` |
| `[lz4]` | `lz4` | Standalone LZ4 (`.lz4`, `.tar.lz4`) and 7z LZ4 folders |
| `[unix-compress]` | `uncompresspy` | Unix-compress (`.Z`, `.tar.Z`) LZW decompression |
| `[cli]` | `tqdm` | `archivey` command-line interface progress output |
| `[seekable]` | `rapidgzip` | Faster gzip/bzip2 decompression and random access into gz/bz2 streams via rapidgzip / bundled `IndexedBzip2File` |
| `[recommended-lite]` | `[7z]` + `[rar]` + `[7z-write]` + `[iso]` + `[zstd]` + `[lz4]` + `[unix-compress]` + `[cli]` | Every broadly wheeled format/codec dependency; excludes build-finicky C++ seek libs |
| `[recommended]` | `[recommended-lite]` + `[seekable]` | Recommended install: every primary backend plus gz/bz2 seeking and speed |
| `[all]` | `[recommended]` plus every alternative/secondary backend, currently none | Everything; currently resolves exactly to `[recommended]` |

The system SHALL make `[recommended]` the sensible all-useful install and
`[recommended-lite]` the fallback when `rapidgzip` cannot build. `[recommended-lite]`
MUST retain every format and codec except gz/bz2 seeking and the speed boost from
`[seekable]`.

The system SHALL keep `[all]` as a future-proof superset for redundant alternative
backends. At present `[all]` MUST resolve to exactly `[recommended]`; the former
`python-xz` and `pyzstd` alternatives are not pinned because the compression-library
analysis dropped them.

The system SHALL treat `[7z]` and `[rar]` as format bundles for complete read support
that requires Python packages. Missing optional libraries MUST degrade by one rule:
raise `PackageNotInstalledError` or `UnsupportedFeatureError` only when bytes cannot
be produced, and skip any integrity check that cannot be computed with an integrity
diagnostic/warning instead of failing the read.

The system SHALL keep `py7zr` and `rarfile` as dev-only test oracles except for
`py7zr` under `[7z-write]`. BCJ2-filtered 7z members MUST remain unsupported by every
extra. Installing any individual extra MUST make that capability available without
requiring unrelated extras. `[all]` MUST be equivalent to installing every runtime
extra. No user-facing extra SHALL pull an alternate RAR decompressor library or tool
wrapper.

Development tools, oracle libraries, and fixture generators such as `ncompress`
SHALL live in the PEP 735 `dev` dependency group, not in user-facing runtime extras.

#### Scenario: extras matrix

| Case | Expected |
| --- | --- |
| `pip install archivey[iso]` | Installs `pycdlib`; `.iso` works; unrelated optional deps are not pulled in |
| `pip install archivey[recommended]` | Every optional format/codec and CLI capability plus `[seekable]`; no redundant xz/zstd alternative backend |
| `pip install archivey[recommended-lite]` after `[recommended]` cannot build `rapidgzip` | Every format/codec still works; only gz/bz2 seeking and speed boost are absent |
| `pip install archivey[all]` | Installs `[recommended]` plus current alternatives; currently exactly `[recommended]` |
| `pip install archivey[7z]` | Installs `pybcj` (import name `bcj`) and `lz4` so LZMA1+BCJ and LZ4 7z members decode |
| `pip install archivey[lz4]` | Standalone `.lz4` / `.tar.lz4` and 7z LZ4 folders work without requiring unrelated extras |
| RAR5 data with only Blake2sp hashes and no `[rar]` | Bytes are returned unverified with a warning; no hard failure solely for skipped Blake2sp |
| 7z member uses BCJ2 | Unsupported-codec error; no extra enables it |
| RAR data without RARLAB `unrar` | `PackageNotInstalledError`; no alternate-tool extra exists |
