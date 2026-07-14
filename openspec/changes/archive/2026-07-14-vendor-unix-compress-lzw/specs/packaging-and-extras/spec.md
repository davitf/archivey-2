## MODIFIED Requirements

### Requirement: Zero-dependency core

The system SHALL install with no third-party runtime dependencies when no extras are
requested. Bare `pip install archivey` MUST fully support every native or
stdlib-backed reader: ZIP, TAR including `tar.gz` / `tar.bz2` / `tar.xz` / `tar.Z`,
single-file GZ / BZ2 / XZ / Z (unix-compress), directories, and 7z reading for common
codecs (LZMA/LZMA2/BCJ/Delta/Deflate/BZip2/STORED) with CRC32 verification.

The system SHALL parse RAR metadata/listing natively in core with CRC32
verification. Reading RAR member data additionally requires the external `unrar`
system binary at runtime; no pip extra supplies that binary. RAR members that carry
only Blake2sp hashes still read without `[rar]`, but the Blake2sp integrity check is
skipped with a diagnostic/warning.

The build SHALL use `hatchling` and the distribution name `archivey`.

#### Scenario: core install matrix

| Case | Expected |
| --- | --- |
| `pip install archivey` with no extras | No third-party runtime packages installed |
| Core read of ZIP/TAR/GZ/BZ2/XZ/Z/directory/common-codec 7z | Fully functional |
| Core read of `.tar.Z` / bare `.Z` | Native LZW decode; no `uncompresspy` |
| Core RAR listing | Native metadata/listing works |
| Core RAR data read with no `unrar` on `PATH` | Clear error says the external `unrar` tool is required |
| Core-only 7z write | Unavailable until `[7z-write]`; 7z reading still works |

### Requirement: Optional extras enable specific capabilities

The system SHALL gate each optional capability behind a named extra that pulls the
third-party dependency required for that capability. 7z and RAR reading are native
for the common case, so extras cover less-common 7z codecs, encryption, 7z writing,
ISO, extra compression formats, seeking accelerators, and the CLI.

| Extra | Pulls in | Enables |
| --- | --- | --- |
| *(none)* | stdlib only + native parsers | ZIP, TAR + stdlib compressed TAR variants including `.tar.Z`, GZ, BZ2, XZ, Z (unix-compress), directory, 7z read for common codecs (including LZMA2+BCJ), RAR metadata/listing; RAR data still needs RARLAB `unrar` |
| `[7z]` | `pyppmd`, `inflate64`, `backports.zstd` on Python <3.14, `brotli`, `lz4`, `cryptography`, `pybcj` | All 7z reading features: PPMd, Deflate64, Zstd, Brotli, LZ4, AES, LZMA1+BCJ |
| `[rar]` | `cryptography`, Blake2sp backend | Header-encrypted RAR5 and Blake2sp checksum verification; RAR data still needs RARLAB `unrar` |
| `[crypto]` | `cryptography` | AES/crypto backend subset used by `[7z]` / `[rar]` |
| `[7z-write]` | `py7zr` | 7z writing only; reading remains native |
| `[iso]` | `pycdlib` | ISO 9660 (`.iso`) |
| `[zstd]` | `backports.zstd` on Python <3.14 | Standalone Zstandard (`.zst`, `.tar.zst`); Python 3.14+ uses stdlib `compression.zstd` |
| `[lz4]` | `lz4` | Standalone LZ4 (`.lz4`, `.tar.lz4`) and 7z LZ4 folders |
| `[cli]` | `tqdm` | `archivey` command-line interface progress output |
| `[seekable]` | `rapidgzip` | Faster gzip/bzip2 decompression and random access into gz/bz2 streams via rapidgzip / bundled `IndexedBzip2File` |
| `[recommended-lite]` | `[7z]` + `[rar]` + `[7z-write]` + `[iso]` + `[zstd]` + `[lz4]` + `[cli]` | Every broadly wheeled format/codec dependency; excludes build-finicky C++ seek libs |
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
The system SHALL NOT list `uncompresspy` in any user-facing extra or the `dev` group.

#### Scenario: extras matrix

| Case | Expected |
| --- | --- |
| Install `[7z]` | PPMd / Deflate64 / Zstd / Brotli / LZ4 / AES / LZMA1+BCJ 7z reading work |
| Install `[rar]` without `unrar` on `PATH` | Header-encrypted listing works; data read still errors on missing `unrar` |
| Install `[recommended-lite]` | All format/codec deps except rapidgzip; unix-compress needs no extra |
| Install `[recommended]` | Same as lite plus gz/bz2 seeking via rapidgzip |
| Install `[all]` | Resolves to `[recommended]` while no alternate backends exist |
| Bare install | `.Z` / `.tar.Z` readable; no `[unix-compress]` extra exists |
