## MODIFIED Requirements

### Requirement: Enforce Cumulative Max-Extracted-Bytes Limit

The system SHALL track total bytes written across a single `extract()` or
`extract_all()` call and raise `ResourceLimitError` at the chunk boundary where
the total exceeds `max_extracted_bytes`. The default is 2 GiB
(2,147,483,648 bytes). Callers override it through `ExtractionLimits`; `None` via
`ExtractionLimits.UNLIMITED` disables this guard.

The limit SHALL be tracked by one `BombTracker` per extraction call. It is a
global resource guard: when it trips, extraction halts and no later members are
processed regardless of `OnError`.

#### Scenario: cumulative byte limit matrix

| Case | Expected |
| --- | --- |
| Running written-byte total crosses `max_extracted_bytes` | Immediate `ResourceLimitError`; extraction halts |
| `ExtractionLimits(max_extracted_bytes=10 * 2**30)` | Enforced cumulative limit is 10 GiB |
| `ExtractionLimits.UNLIMITED` | Cumulative byte guard is disabled |

### Requirement: Enforce Per-ArchiveMember Max Decompression Ratio

The system SHALL raise `ResourceLimitError` when a single member's output exceeds
`max_ratio * member.compressed_size` after that member's output crosses
`ratio_activation_threshold`. Defaults are `max_ratio=1000.0` and threshold
5 MiB. The ratio is per-member output, not cumulative output, and is checked only
when `member.compressed_size` is known and greater than zero. The original member
is used so late-bound compressed-size metadata remains accurate.

This guard SHALL be independent of cumulative bytes and archive-wide ratio
guards. A ratio violation for one member is member-scoped and may be continued
under `OnError.CONTINUE`; global guards remain always-stop.

#### Scenario: per-member ratio matrix

| Case | Expected |
| --- | --- |
| Member output exceeds ratio after threshold | `ResourceLimitError` while processing that member |
| Tiny highly-compressible member stays below threshold | No ratio error; threshold prevents false positive |
| `compressed_size is None` or `0` | Per-member ratio skipped; cumulative/global guards still apply |
| `ExtractionLimits(max_ratio=100)` | Members over 100:1 trip this guard |

### Requirement: Bomb Protection Scope Limited to Extraction Paths

The system SHALL apply `ExtractionLimits` bomb guards only during
`archivey.extract()` and `ArchiveReader.extract_all()`. `ArchiveReader.read()`
and `ArchiveReader.open()` return decompressed data/streams without byte, ratio,
or entry-count enforcement; callers are responsible for guarding direct reads.
Listing materialization caps are separate (`ListingLimits` in `archive-reading`)
and do not apply to `read()` / `open()` either.

#### Scenario: bomb-scope matrix

| Case | Expected |
| --- | --- |
| `reader.read(member)` on extreme-ratio data | Raw decompressed bytes returned or normal read error; no extraction bomb guard |
| `reader.open(member)` | Stream delivers decompressed data without extraction limits |
| `reader.members()` on a metadata bomb | `ListingLimits` / `ResourceLimitError` per `archive-reading`, not `ExtractionLimits` |

### Requirement: Error Policy (OnError) for extraction failures

`OnError.STOP` and `OnError.CONTINUE` SHALL govern per-member failures only.
Under `CONTINUE`, a member-scoped `FilterRejectionError`, other member-scoped
`ArchiveyError`, permitted read/write `OSError`, or per-member ratio violation
records `REJECTED`/`FAILED`, removes partial output, emits the matching diagnostic
under the active diagnostic policy, and proceeds.

Diagnostic disposition SHALL still be authoritative: `RAISE` emits
`DiagnosticRaisedError` and halts immediately even under `OnError.CONTINUE`;
logging-handler and diagnostic-callback exceptions propagate unchanged. Under
`STOP`, the genuine rejection/failure raises immediately and is not converted to
an extraction advisory. Global resource guards (`ResourceLimitError` for
cumulative bytes, archive-wide/live ratio, and max entries), `KeyboardInterrupt`,
`MemoryError`, and unexpected programming exceptions are always-stop and are not
swallowed.

#### Scenario: OnError matrix

| Case | Expected |
| --- | --- |
| Corrupt member under `CONTINUE` and default diagnostics | Partial output removed; `FAILED` result; `EXTRACTION_MEMBER_FAILED`; later members continue |
| Extraction diagnostic resolves to `RAISE` under `CONTINUE` | `DiagnosticRaisedError` halts; no report returned |
| `CorruptionError` under `STOP` | Original `CorruptionError` propagates; no continued-failure diagnostic |
| Filesystem `OSError` while writing under `CONTINUE` | Partial output removed; `FAILED` result/diagnostic; extraction proceeds |
| Cumulative bytes/live ratio/max entries exceed limit under `CONTINUE` | `ResourceLimitError` propagates and halts; no later member processed |
| Default `STOP` member failure | Exception raises immediately; failing partial file removed; earlier outputs remain |
| Mixed good/corrupt archive under `CONTINUE` | Extractable members are written; report includes `EXTRACTED` plus `FAILED`/`REJECTED`; no per-member exception escapes |

### Requirement: Archive-wide decompression ratio for solid containers

The system SHALL evaluate a static archive-wide ratio during extraction when a
member's `compressed_size` is unknown/zero and the reader exposes a cheap
`compressed_source_size`. The denominator is the archive source byte size: path
`stat`, trusted integer `size`, `try_get_size()` from Archivey streams, or an
O(1)-safe `SEEK_END`/restore probe for real files, `BytesIO`, and `mmap`.
Anything that would decompress or scan payload to answer (for example foreign
decompressor streams) yields `None`. For compressed containers this is compressed
size; for uncompressed containers the resulting ratio is about 1:1 and harmless.

The ratio SHALL be `cumulative_bytes_written / compressed_source_size`, checked
in `BombTracker.count()` using the same `max_ratio` and cumulative
`ratio_activation_threshold` as other ratio guards. If `compressed_source_size`
is absent, the static archive-wide check is skipped. Per-member and archive-wide
ratios are independent; either may trip first. A tripped archive-wide ratio
SHALL raise `ResourceLimitError`.

#### Scenario: static archive-wide ratio matrix

| Case | Expected |
| --- | --- |
| Small `.tar.gz` file with known source size expands past `max_ratio` after threshold | `ResourceLimitError` during extraction |
| Compressed tar from non-seekable pipe with unknown size | Static archive-wide ratio skipped; cumulative byte limit still applies |
| Plain `.tar` | No meaningful compressed denominator; archive-wide ratio does not trip |
| ZIP member has known `compressed_size` | Per-member ratio applies; archive-wide ratio does not replace it |
| Nested archive opened from an Archivey member/codec stream with cheap size | Cheap source size may serve as archive-wide denominator |

### Requirement: Enforce Maximum Entry Count

The system SHALL count members actually written to disk during one extraction call
and raise `ResourceLimitError` once the count exceeds `max_entries`. The default is
`1_048_576`; callers override through `ExtractionLimits`, and `None` disables the
guard. The counter protects against inode/per-directory/syscall bombs made of many
tiny entries and is independent of byte and ratio limits.

Only members that will create disk entries SHALL count: selector exclusions, user
filter skips, and members dropped before writing do not increment the counter.
Every written FILE, DIR, SYMLINK, and HARDLINK counts. This is a global resource
guard and halts even under `OnError.CONTINUE`.

#### Scenario: entry-count matrix

| Case | Expected |
| --- | --- |
| More than `max_entries` members are written | `ResourceLimitError` once the count crosses the limit; extraction halts under any `OnError` |
| `ExtractionLimits(max_entries=100)` | Error after the 100th written member when the 101st would be written |
| Selector chooses one member from millions | Extraction can complete with `max_entries=1` because only selected written entries count |
| Many tiny files stay below byte/ratio limits but exceed entry count | Entry-count guard still raises |

### Requirement: Live archive-wide decompression ratio for unknown-size streams

The system SHALL evaluate a live archive-wide ratio during extraction when no
per-member `compressed_size` and no cheap static `compressed_source_size` is
available, but the compressed backend can expose `compressed_bytes_consumed`.
This covers compressed archives from non-seekable pipes and seekable opaque
streams whose size is not cheaply knowable. Backends wrap the stream source in
the counting reader exactly when the static denominator is absent.

The ratio SHALL be `cumulative_bytes_written / compressed_bytes_consumed`, checked
after cumulative output crosses `ratio_activation_threshold` using the same
`max_ratio`. It is a cumulative global guard: if it trips, extraction halts even
under `OnError.CONTINUE` with `ResourceLimitError`. The live path complements
static checks and is not used when member compressed sizes or a cheap outer
source size provide a denominator; whichever available guard trips first wins.
Codec-layer seeks may re-read counted bytes, inflating the denominator and
weakening the guard, but never causing a false positive.

#### Scenario: live archive-wide ratio matrix

| Case | Expected |
| --- | --- |
| Highly compressible `.tar.gz` from non-seekable pipe has no static denominator | Live ratio raises `ResourceLimitError` after threshold before absolute byte cap |
| Live ratio exceeded under `OnError.CONTINUE` | `ResourceLimitError` propagates and extraction halts |
| Plain uncompressed `.tar` from a pipe | Consumed and written bytes stay about 1:1; live ratio does not trip; byte limit still applies |
| `.tar.gz` has cheap `compressed_source_size` | Static archive-wide ratio is used; live path is not engaged/double-counted |
| Seekable opaque compressed stream has no cheap size/`size`/`try_get_size()`/O(1) end seek | Source is counted live; archive is not left with only the byte cap |
