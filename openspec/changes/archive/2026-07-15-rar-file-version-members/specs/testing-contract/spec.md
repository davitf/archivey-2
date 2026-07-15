## MODIFIED Requirements

### Requirement: Cross-validate native readers against reference oracles

The system SHALL validate native 7z and RAR readers against reference
implementations used only as test oracles: `py7zr` and the `7z` CLI for 7z,
`rarfile` and `unrar` for RAR. For representative corpora, native member metadata
and decompressed bytes MUST match the oracle. Oracle libraries are dev-group
dependencies only and SHALL NOT be required at runtime. Oracle-backed tests SHALL
skip, not fail, when the oracle library or CLI is unavailable.

The 7z corpus MUST cover core codecs supported without extras (LZMA1, LZMA2, simple
BCJ filters, Delta, BZip2, Deflate, STORED), optional PPMd / Deflate64 under `[7z]`,
and AES-encrypted archives under `[crypto]`. Unsupported codecs such as BCJ2 and
unrecognized method IDs MUST raise the documented unsupported-codec error rather
than returning bytes that diverge from the oracle.

The RAR corpus MUST cover RAR4 and RAR5, solid and nonsolid, stored M0, symlinks,
hardlinks/`FILE_COPY`, multi-volume sets, header-encrypted RAR5 (under `[rar]`/
`[crypto]`), Blake2sp-only members, and at least one RAR5 `-ver` file-version
archive. After the native RAR reader registers, RAR corpus entries MUST run (not
skip solely for “reader not implemented”).

`rarfile` omits file-version history rows. Dedicated `-ver` tests SHALL assert
native listing/read/`unrar` behavior directly and MUST NOT require rarfile list
equality for those history members. Non-versioned RAR corpus entries continue to
cross-check metadata and bytes against rarfile/`unrar`.

#### Scenario: native-reader oracle matrix

| Case | Expected |
| --- | --- |
| 7z corpus entry read by native reader and `py7zr`/`7z` | Metadata and bytes match; skipped if oracle unavailable |
| RAR corpus entry read by native reader and `rarfile`/`unrar` | Metadata and bytes match; skipped if oracle unavailable |
| 7z entry uses BCJ2 or unknown method ID | Documented unsupported-codec error; no guessed output |
| RAR solid+links / multi-volume / header-encrypted entry | Exercised once native RAR is registered; skip only if `unrar`/crypto/oracle absent |
| RAR5 `-ver` history members | Native exposes `path;n` + live path; bytes match `unrar p` exact name / `-ver`; rarfile list equality not required for history rows |
