# Testing Contract

## Purpose

Test-suite contract for Archivey: uniform `ArchiveMember` behavior across formats,
safe rejection of adversarial inputs, round trips for writable formats, native-reader
oracle validation, non-seekable stream coverage, corpus conformance, and concurrent
reader stress.

## Related specs

| Spec | Relationship |
| --- | --- |
| `archive-reading` | Public read API, member identity, streaming pass rules |
| `archive-writing` | Writable-format round trips and conversion |
| `safe-extraction` | Extraction safety limits and path/link rejection |
| `access-mode-and-cost` | Streaming legality, seekability, fail-fast source requirements |
| `reader-concurrency` | Capability gates, lifecycle leases, free-threaded contract |
| `format-7z` | Native 7z corpus and oracle validation |
| `format-rar` | Native RAR corpus, `unrar`, encrypted headers, Blake2sp validation |
| `packaging-and-extras` | Optional dependency and oracle availability rules |

## Requirements

### Requirement: Equivalence matrix across formats

The system SHALL produce equivalent `ArchiveMember` objects from ZIP, TAR, 7z, RAR,
and ISO sources when reading the canonical directory structure: files, symlinks,
nested directories, empty directories, and filenames with Unicode and spaces.
Equivalence SHALL mean field-by-field equality excluding identity fields
(`member_id` / `archive_id`), `raw_name`, `compressed_size`, `hashes`, and `extra`.
Per-format expected deviations MUST be represented as `ArchiveFormatFeatures` flags
and consumed by the assertion helper, not silently excluded.

#### Scenario: canonical-format equivalence

| Case | Expected |
| --- | --- |
| Same canonical structure archived as ZIP/TAR/7z/RAR/ISO | Members compare equal on every representable field except the documented exclusions |
| Format cannot represent a field faithfully | `ArchiveFormatFeatures` records the limitation and the helper scopes the comparison |

### Requirement: Adversarial corpus coverage

The system SHALL include an adversarial test corpus that exercises every documented
attack category and verifies that the correct exception is raised or limit is
enforced in each case. The required adversarial cases are:

| Case | Expected outcome |
| --- | --- |
| Zip bomb: quine-style and nested / 42.zip variant | `max_ratio` and `max_extracted_bytes` limits enforced before resource exhaustion |
| Ratio-floor false positive: tiny highly-compressible file (10 B -> 15 KiB, 1500:1) | Extracts without error while under `ratio_activation_threshold` |
| Path traversal: `../evil`, `../../etc/passwd`, `./../../outside` | `PathTraversalError`; no outside write |
| Absolute paths: `/etc/passwd`, `C:\Windows\System32\evil.dll` | `PathTraversalError` |
| Symlink escape: target `../../outside`, chained symlinks | `SymlinkEscapeError` |
| Symlink loop: cyclic `a -> b`, `b -> a` | `SymlinkEscapeError`; no uncaught `OSError` or crash |
| Corrupt archive: missing EOCD, truncated TAR, bad CRC | `CorruptionError` or `TruncatedError` with original cause attached |
| Unicode bombs: null bytes, RTL override characters | Null bytes rejected as traversal; RTL warns or rejects |
| Giant claimed size: member claims 1 TiB while archive is 1 KiB | Extraction aborts cleanly before exhausting resources |

Regenerable adversarial archives SHALL be generated deterministically in memory or on
demand by `tests/create_adversarial.py` and SHALL NOT be committed. A hostile archive that
cannot be generated in the test environment MAY be committed under
`tests/fixtures/adversarial/` only with the fixture-policy JSON sidecar and an explicit
rationale.

The RTL warning/rejection outcome applies to every `ArchiveMember` presented by any
backend, including directory and single-file pseudo-archives. A backend SHALL NOT emit
duplicate warnings for one presentation of the same member.

#### Scenario: adversarial-behavior matrix

| Case | Expected |
| --- | --- |
| Zip bomb extracted with default limits | `ExtractionError` before configured byte or ratio limit is exceeded |
| Archive member named `../evil` is extracted | `PathTraversalError`; destination outside tree remains untouched |
| Truncated or CRC-invalid archive is read | `CorruptionError` or `TruncatedError`; original exception is `__cause__` |

#### Scenario: RTL warning is backend-independent

- **WHEN** any backend presents a member whose name contains U+202E RIGHT-TO-LEFT OVERRIDE
- **THEN** the member is rejected or exactly one warning is emitted for that presentation

### Requirement: Round-trip test for every writable format

The system SHALL include a `create -> extract -> compare` round-trip test for every
writable format. The extracted files and metadata MUST match the originals within
the format's documented timestamp and permission limitations.

#### Scenario: writable-format round trips

| Case | Expected |
| --- | --- |
| Canonical file set written to ZIP then extracted | Content and every ZIP-representable metadata field match |
| Canonical file set written to TAR then extracted | Content and every TAR-representable metadata field match |
| Future writable format is added | A matching round-trip row/test is added before the format is considered supported |

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

### Requirement: Cover solid RAR link demux in the corpus

The system SHALL include RAR corpus entries (or dedicated tests) that combine solid
compression with symlinks and hardlinks/`FILE_COPY`, and SHALL assert that native
`stream_members()` pipe demux stays aligned: stdout length equals the sum of payload
FILE sizes only, link members carry resolved `link_target` when possible, and file
bytes match the `rarfile`/`unrar` oracle.

#### Scenario: solid-link demux coverage

| Case | Expected |
| --- | --- |
| Solid RAR5 with symlinks | Native listing + stream bytes match oracle; pipe ignores symlink sizes |
| Solid RAR4 with packed symlink targets | Same; stored targets still resolve on list when readable |
| Solid RAR5 with hardlinks | Hardlinks are `HARDLINK`; payload files demux correctly |

### Requirement: Non-seekable stream coverage for streaming backends

The system SHALL test every backend that supports streaming with a `FakeNonSeekable`
wrapper that raises `io.UnsupportedOperation` on every `seek` and `tell` call. A
streaming-capable backend MUST read and iterate correctly without repositioning the
source.

A backend that requires a seekable source, including ZIP and ISO per
`access-mode-and-cost` and the format specs, SHALL fail fast at open time with
`StreamNotSeekableError`. It MUST never implicitly buffer the non-seekable source to
make it seekable. The recovery path is to provide a seekable source for
seek-required formats, or to use a streaming format/path where applicable.

#### Scenario: non-seekable-source matrix

| Case | Expected |
| --- | --- |
| ZIP opened through `FakeNonSeekable` | `open_archive` raises `StreamNotSeekableError` at open; no member data read; never implicitly buffers |
| ISO opened through `FakeNonSeekable` | `StreamNotSeekableError` at open under the same seek-required rule |
| `.tar.gz` opened through `FakeNonSeekable` | Members are iterable and data is readable without seek/tell |
| Caller needs ZIP from non-seekable input | Use a seekable source; do not rely on Archivey buffering |

### Requirement: Corpus conformance sweep

The test suite SHALL include one parametrized conformance sweep driven by the
declarative archive corpus. Every corpus entry for an implemented format MUST open
with `open_archive()`, list members matching declared expectations, and extract
cleanly to a temporary directory under the default safety policy with contents
verified. An entry that declares an expected failure MUST raise exactly the
documented `ArchiveyError` subclass.

Corpus entries for formats not yet implemented SHALL remain in the corpus and skip
through a registry-driven guard, so registering a reader activates its entries
without re-porting. Entries that need an absent optional dependency SHALL skip, not
fail. The corpus SHALL cover at least the DEV declarative corpus shapes for
implemented formats and record the DEV commit hash the shapes were ported from.

#### Scenario: corpus-sweep matrix

| Case | Expected |
| --- | --- |
| Implemented-format corpus entry runs | Archive opens, listing matches names/types/sizes/link targets, extraction verifies contents |
| Entry declares a documented failure, such as encrypted without password | Exact documented `ArchiveyError` subclass is raised and the sweep passes |
| 7z or RAR entry appears before native reader registration | Skipped by registry guard; runs once the format registers |
| Entry needs absent optional dependency | Skipped, not failed |

### Requirement: Frozen DEV oracle retired

The system SHALL NOT contain the frozen DEV oracle tree `tests/_dev_oracle/`.
Durable assets from DEV MUST live in their new homes: declarative corpus shapes in
the v2 corpus and oracle libraries / CLIs in dev-group cross-validation tests.
Dead v1-API test drivers SHALL be deleted, and tooling configuration MUST contain
no special-case exclusions for the retired tree.

#### Scenario: retired-oracle cleanup

| Case | Expected |
| --- | --- |
| Repository is searched for `_dev_oracle` | No test tree and no pytest/ruff/type-checker exclusion references remain |

### Requirement: Capability-gate behavior is tested on every format

The test suite SHALL cover the declared-capability gate uniformly for every
implemented format, including directory. A reader opened without
`MemberStreams.CONCURRENT` MUST raise `ConcurrentAccessError` on a second
overlapping `open()` while the first stream stays readable; sequential
`open -> read -> close -> open next` MUST succeed without any declaration. The
error message MUST include the recorded `open_archive()` call site.

Without `MemberStreams.SEEKABLE`, member streams from random `open()` and
`stream_members()` MUST report `seekable() is False` and raise
`io.UnsupportedOperation` from `seek()` on every format, including real directory
files. With `MemberStreams.SEEKABLE`, positioning MUST work where the backend
provides it. `extract_all()`, including hardlink recovery and symlink-target reads,
MUST succeed on readers with no declared capabilities. `ArchiveyUsageError` and
`ConcurrentAccessError` MUST NOT be `ArchiveyError` subclasses. Accelerator/index
activation MUST be demand-driven and match `seekable-decompressor-streams`.

#### Scenario: capability-gate matrix

| Case | Expected |
| --- | --- |
| Second overlapping `open()` on each implemented format without `CONCURRENT` | `ConcurrentAccessError` names the open site; first stream remains readable |
| Sequential open/read/close loop without declarations | Succeeds on every implemented format |
| `ConcurrentAccessError` inside `except ArchiveyError` | Propagates out of that handler |
| Undeclared accelerator-eligible source | No seek index instantiated |
| Declared `SEEKABLE` accelerator-eligible source | `AUTO` accelerator resolves as specified |

### Requirement: Non-file open and ANTI classification tests

Tests SHALL assert `ArchiveyUsageError` from `open`/`read` on directory members
for ZIP, TAR, ISO, and the directory backend (not empty bytes / raw OS /
ISO `CorruptionError`), and `stream_members` stream `None`. 7z anti fixtures
SHALL assert `type == MemberType.ANTI`, `None` stream, and usage-error open/read.

#### Scenario: coverage matrix

| Case | Expected |
| --- | --- |
| ZIP/TAR/ISO/directory dir member `read` | `ArchiveyUsageError` |
| Directory backend dir `open` | `ArchiveyUsageError` (not `IsADirectoryError`) |
| 7z anti list + stream + open | `ANTI`; stream `None`; `ArchiveyUsageError` |

### Requirement: Property-based tests for safety logic

The test suite SHALL include bounded Hypothesis property tests over the
load-bearing safety functions: member-name normalization, the universal
extraction filter, link-target resolution, volume-name discovery, and format
detection over an arbitrary byte prefix on a peekable source. Tests SHALL assert
structural invariants (totality under typed errors, no escape introduced by
normalization, peek/replay preserved for detection) rather than golden outputs
from a second implementation. Shrunk counterexamples SHALL be pinned as explicit
regression examples. `hypothesis` is a `dev`-group dependency only; `[core-only]`
MUST still pass without it.

#### Scenario: property-test matrix

| Case | Expected |
| --- | --- |
| Generated traversal / absolute / NUL member names fed to `check_universal` | Typed `FilterRejectionError` subclass for every unsafe name |
| Arbitrary decoded names fed to `normalize_member_name` | Always returns `str`; idempotent; never introduces `..` or leading `/` absent from the input |
| Arbitrary byte prefixes on a peekable detection source | Typed result or typed error; peek source left unadvanced |
| Strategy discovers a shrunk failing input | Input is pinned as an `@example` or unit case |

### Requirement: Coverage-guided fuzz gate for parsers and entry points

The test suite SHALL provide an Atheris (libFuzzer) coverage-guided fuzz harness
over archivey-owned hostile-input entry points. The harness MUST seed from the
declarative corpus and adversarial fixtures, force accelerators off, and treat
success as: within each time budget, only typed `ArchiveyError` subclasses or
clean returns — never an uncaught non-`ArchiveyError` exception, process abort,
or hang past the slice timeout.

For CRC/checksum-gated targets (native 7z header parse at minimum; other
formats when their interesting paths sit behind a header CRC), the harness
SHALL apply a **mutate-then-fixup** step that recomputes and patches valid
CRC fields before invoking the parser, so coverage guidance reaches post-CRC
logic. It MUST NOT rely on unaided libFuzzer CMP feedback to solve CRC32. A
minority of inputs (or a small dedicated budget) SHALL retain broken CRCs so
the reject path stays exercised.

The default main-branch run SHALL partition a wall-clock budget of approximately
150 seconds across these targets (exact seconds MAY be env-overridable):

| Target | Role |
| --- | --- |
| Native 7z header parse | Deep coverage of the pure-Python header parser |
| 7z `open_archive` + member list/materialize | Reader/spine path after parse |
| `detect_format` over arbitrary/prefix seeds | Magic/peek-replay entry point |
| ZIP and TAR `open_archive` + member list | Shallow wrapper/translation coverage |
| ISO `open_archive` + member list | Shallow; MUST use a hard wall-clock kill timeout |
| Native RAR header parse | Deep coverage of the pure-Python RAR3/RAR5 metadata parser (CRC mutate-then-fixup) |
| RAR `open_archive` + member list | Reader/spine path after parse; skip cleanly if the backend is unavailable |

Full member **extract** is out of scope for this harness (covered by the
mutation harness). Stream/codec-only targets MAY be added later without removing
the above.

CI SHALL run the harness on every push to `main` and via `workflow_dispatch`
(longer budgets allowed). It MUST NOT be part of the default pull-request test
matrix. On failure the job SHALL upload reproducing inputs as artifacts and
print a one-line local re-run command. Always-on nightly schedules are not
required.

The existing corpus mutation harness and Hypothesis property tests remain
mandatory complementary layers; Atheris does not replace them. `atheris` is
installed only via the CI `fuzz` dependency group (`packaging-and-extras`).

#### Scenario: atheris gate matrix

| Case | Expected |
| --- | --- |
| Push to `main` | Fuzz workflow runs partitioned ~150s budget; green if no crash/hang/raw exception |
| `workflow_dispatch` with longer env budget | Same targets; extended exploration |
| Pull request (default matrix) | Atheris job not required |
| RAR backend absent | RAR targets skipped; other targets still run |
| Fuzzer finds a crashing input | Job fails; repro bytes uploaded; re-run command printed |
| 7z / RAR header target with fixup enabled | Most iterations present a matching header CRC and enter post-CRC parse |
| Broken-CRC sample / minority path | Typed CRC/corruption failure; reject path still hit |
| Mutation harness / `ARCHIVEY_FUZZ` | Still available and unchanged in role |

### Requirement: Concurrent member-stream correctness and free-threaded stress

The test suite SHALL exercise the supported post-materialization concurrency
contract from `reader-concurrency` for readers declared with
`MemberStreams.CONCURRENT`. Coverage MUST include representative backend shapes:
directory independent handles, ZIP library-coordinated handles, Archivey
`SharedSource` views for single-file and native 7z/RAR as available, and
Archivey-locked library handles for random-access TAR and ISO.

Tests SHALL cover concurrent `open()` by member and name; independent stream
`read`, `readinto`, `close`, supported positioning, and non-seekable
`io.UnsupportedOperation`; cache publication separate from lifecycle; child
operation-owner scopes; generator abandonment; lifecycle leases, failures,
finalizers, and caller-owned sources; password candidate/provider coordination; and
detected unsupported overlap. Stress tests MUST vary interleavings across threads
and assert exact bytes/state, not merely lack of exceptions.

CI SHALL define a required Linux `free-threaded-concurrency` job that installs
CPython `3.13t`, uses the zero-dependency core environment, and runs tests marked
`concurrent_reader`. The marker SHALL cover directory, ZIP, single-file stdlib
codecs, `SharedSource`, lifecycle/operation state, and TAR. The job MUST fail rather
than skip merely because the GIL is disabled. Optional backend free-threaded support
is not claimed until an equivalent dedicated job can install and run that backend.
ISO multi-thread coverage runs in the ordinary `[all]` matrix until a dedicated
extras job exists.

The TAR/ISO correctness-lock implementation SHALL record a proportionate baseline:
wall time, lock wait/hold time, and practical seek/decompression/read metrics.
There is no pass/fail performance threshold. A later optimization or speed claim
MUST include targeted before/after measurements for the mechanism it changes; peak
memory and broader DIRECT/SOLID workloads are required only when that strategy can
affect buffering, materialization, or decompression work.

#### Scenario: concurrency-test matrix

| Case | Expected |
| --- | --- |
| Available representative backend materialized and workers use distinct streams under varied interleavings | Exact bytes and independent supported positions; non-seekable streams keep standard unsupported-operation behavior |
| Required `free-threaded-concurrency` job runs under CPython `3.13t` | Passes without cache, lifecycle, password, or source-position data races |
| Multi-thread workers cover core backends (directory, ZIP, stdlib single-file, SharedSource, plain TAR) | Exact member bytes and documented misuse errors |
| TAR/ISO correctness lock implemented | Practical serialization metrics recorded without a correctness speed threshold |
| Later performance claim changes handle sharing, decoding, or locks | Focused before/after metrics for affected resources |

### Requirement: Stored-digest parity across backends

The corpus conformance sweep SHALL assert stored-digest parity: for every applicable
member, each backend SHALL surface the stored digest(s) documented for its format, and
SHALL omit digest keys where the format stores none. This turns silent parity drift ("a
backend quietly stopped populating `crc32`") into a test failure.

The asserted matrix SHALL match the documented policy:

| Format | Member kind | Expected `hashes` keys |
| --- | --- | --- |
| ZIP | FILE / SYMLINK | `crc32` present |
| 7z | FILE | `crc32` present |
| RAR5 | FILE with CRC32 | `crc32` present |
| RAR5 | FILE with Blake2sp only | `blake2sp` present, `crc32` absent |
| single-file GZIP | single member, seekable | `crc32` present |
| single-file GZIP | multi-member or non-seekable | `crc32` absent |
| single-file LZIP | via seekable lzip backend | `crc32` present |
| single-file BZ2/XZ/ZLIB/BR/`.Z`, TAR, directory | any | no stored-digest key |

#### Scenario: parity sweep

| Case | Expected |
| --- | --- |
| Backend surfaces its documented stored digest for an applicable member | Sweep passes |
| A backend stops populating a documented digest | Sweep fails |
| A backend populates a digest the format does not store | Sweep fails |


### Requirement: BLAKE2sp verification is tested with KATs and a RAR5 oracle

The suite SHALL include BLAKE2sp known-answer tests (reference BLAKE2 vectors) proving
the internal hasher independent of RAR fixtures, and SHALL assert end-to-end that a native
read of a RAR5 BLAKE2sp-only member verifies: an intact member reads clean and a
corrupted-payload member raises `CorruptionError` (not a silent `DIGEST_UNVERIFIABLE`).
The oracle cross-check (`unrar`/`rarfile`) SHALL confirm the intact bytes; oracle-backed
cases SHALL skip when the tool/library is unavailable.

#### Scenario: BLAKE2sp verification

| Case | Expected |
| --- | --- |
| BLAKE2sp known-answer vectors | Internal hasher matches reference digests |
| Intact RAR5 BLAKE2sp-only member, native read | Reads clean; digest verified (no `DIGEST_UNVERIFIABLE`) |
| Corrupted RAR5 BLAKE2sp-only member payload | `CorruptionError` at terminal read |
| `unrar`/`rarfile` unavailable | Oracle cross-check skips; KATs and native read still run |

### Requirement: WinZip AES ZIP corpus and failure cases

The corpus SHALL include WinZip AES ZIP entries covering AE-1 and AE-2, key strengths
128 and 256, over STORED and DEFLATE members, cross-validated against an oracle (`7z`/
`py7zr`) for decrypted bytes; oracle-backed cases SHALL skip when the tool/library is
unavailable. Dedicated tests SHALL assert wrong-password (`EncryptionError`, no bytes),
tampered-HMAC (`CorruptionError`), AE-2 absent-`crc32`, and missing-`[crypto]`
(`PackageNotInstalledError`, still reported encrypted).

#### Scenario: AES ZIP coverage

| Case | Expected |
| --- | --- |
| AE-1/AE-2 × 128/256 × STORED/DEFLATE, correct password | Bytes match the oracle; skip if oracle absent |
| Wrong password | `EncryptionError`, no bytes |
| Tampered ciphertext | `CorruptionError` at terminal read |
| AE-2 member | No `crc32` surfaced (parity sweep expects its absence) |
| `[crypto]` not installed | `PackageNotInstalledError`; member still identified as encrypted |

### Requirement: Performance budget is measured and gated

The system SHALL provide a benchmark harness that measures, per format and per
operation (open, list, read-all, extract), three axes: wall time, total bytes
decompressed, and source seek count. Bytes-decompressed and seek-count SHALL be read
from archivey's own stream instrumentation, not estimated from wall time. Bytes
decompressed counts decode/output volume (distinct from the existing compressed-input
`compressed_bytes_consumed` live-ratio counter; both MAY be available together). The
harness SHALL run as a CI gate over a fixed comparison corpus and fail when a tracked
metric regresses past its recorded baseline.

Wall-time SHALL be gated as a ratio against the stdlib peer for that format
(ZIP→`zipfile`, TAR→`tarfile`, single-file gzip→`gzip`), honoring the `VISION.md`
budget (≤1.3× common paths; up to ~2× where a safety/correctness feature justifies it,
annotated per case). Bytes-decompressed and seek-count SHALL be gated as deterministic
structural invariants (exact value or ≤ bound), since they are host-independent.
Structural invariants SHALL gate (block) every PR. Full wall-time ratio checks SHALL run
off the PR path (non-blocking) as a separate scheduled job on a daily cadence, guarded so
the expensive run is SKIPPED unless the default branch changed since the previous run
(commit-recency guard), and MAY be forced on demand via `workflow_dispatch`. The job records
results (JSON artifact + informational VISION print) and fails visibly (notifying) only on a
real structural regression or a gross wall regression past the sanity ceiling. Per-PR
wall-time execution SHALL NOT be required (it taxes every PR with a multi-minute run), and a
plain always-on nightly SHALL be avoided in favour of the change-guarded schedule — this
project is bursty with long dormant stretches, so the guard yields next-run signal after a
change at near-zero cost while dormant.

The harness SHALL enforce the solid-block no-re-decode invariant: reading every member
of a solid archive (7z folder / solid RAR) in listing order SHALL decompress each packed
byte at most once (total bytes-decompressed ≤ unpacked size × a small constant). Random
out-of-order `open()` MAY re-decode (the documented `AccessCost.SOLID` cost) and is
recorded but not failed. Baselines SHALL be committed, reviewable artifacts; a metric
change requires an explicit baseline diff.

#### Scenario: benchmark axes and gating

| Case | Expected |
| --- | --- |
| ZIP/TAR/gzip open·list·read·extract | Wall-time ratio vs stdlib peer within the `VISION.md` budget or the annotated exception |
| Sequential read of every member of a solid 7z folder | Bytes-decompressed ≤ folder unpacked size × constant (no per-member re-decode) |
| Out-of-order random `open()` on a solid folder | Re-decode recorded, not failed (documented SOLID cost) |
| A change that re-reads a solid block from start per member | CI gate fails on the bytes-decompressed invariant |
| Metric drifts past recorded baseline | Gate fails until the baseline diff is reviewed and updated |
| Benchmark run on a noisy CI host | Ratio tolerance band absorbs host variance; structural invariants stay exact |

