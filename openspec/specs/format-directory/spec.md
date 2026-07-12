# Directory Pseudo-Archive Format Behavior

## Purpose

A filesystem directory is exposed as a pseudo-archive through the unified
`ArchiveReader` API so conversion pipelines and callers can treat a live
directory like any other readable archive.

## Related specs

| Spec | Relationship |
| --- | --- |
| `archive-reading` | Reader API and uniform member-stream constraints |
| `access-mode-and-cost` | Cost receipt and method legality |
| `diagnostics` | Directory scan-race diagnostic values / policy |
| `safe-extraction` | Directory reader as extraction/conversion source |

## Requirements

### Requirement: Present a filesystem directory as an ArchiveReader

The directory backend SHALL open a plain filesystem directory as an
`ArchiveReader` with `ArchiveFormat.DIRECTORY`. It SHALL enumerate files and
subdirectories under the root as `ArchiveMember` objects and populate metadata
from filesystem attributes (`mode`, timestamps, `uid`, `gid`, `uname`, `gname`).

The directory reader SHALL expose these properties:

| Property | Value |
| --- | --- |
| Listing cost | `ListingCost.REQUIRES_SCANNING` — enumeration walks the tree (`os.scandir` recursion); there is no O(1) index |
| Access cost | `AccessCost.DIRECT` — each file is independently addressable |
| Stream capability | `StreamCapability.SEEKABLE` |
| Member list upfront | No — `get_members_if_available()` returns `None` (the walk is a scan, run once under materialization, not on every peek) |
| Write support | No |
| Seek requirement | No archive source seek needed; files open directly |

#### Scenario: directory reader matrix

| Case | Expected |
| --- | --- |
| `archivey.open_archive(some_directory_path)` | Reader format is `ArchiveFormat.DIRECTORY` |
| Iterate reader | One `ArchiveMember` per file/subdirectory found under the root |
| Inspect member metadata | Mode, timestamps, uid/gid, uname/gname reflect filesystem state |
| Inspect `cost` | `REQUIRES_SCANNING`, `DIRECT`, `SEEKABLE` |
| `get_members_if_available()` before any pass | `None` (no upfront index; the walk is a scan) |

### Requirement: Treat scan races as diagnostics and genuine errors as errors

The directory backend SHALL propagate genuine directory-walk `OSError`s
unchanged. If a listed entry or subdirectory vanishes before inspection, the
reader SHALL skip it, continue scanning, and emit `SCAN_ENTRY_VANISHED` or
`SCAN_DIRECTORY_VANISHED` with a JSON-safe relative path and entry kind. These
events are reader-operation aggregate data and SHALL NOT attach to a member that
does not exist.

Under `RAISE`, `DiagnosticRaisedError` SHALL halt the scan. Diagnostic context
MUST NOT retain `DirEntry`, `Path`, exception, or filesystem handle objects.

#### Scenario: directory scan matrix

| Case | Expected |
| --- | --- |
| Entry disappears between listing and `stat` under default policy | Entry skipped; `SCAN_ENTRY_VANISHED` counted/retained/logged; walk continues |
| Subdirectory vanishes and code resolves to `RAISE` | `DiagnosticRaisedError` halts scan |
| Walking subdirectory raises `PermissionError` | Original error propagates unchanged; no vanished-path diagnostic substitutes |

### Requirement: Support conversion pipelines without archive-wide buffering

The directory backend SHALL allow a directory reader to act as the source for
conversion via `writer.add_members(reader)`. Members SHALL stream into the target
archive in a single forward pass without buffering the full directory content to
intermediate storage.

#### Scenario: directory conversion matrix

| Case | Expected |
| --- | --- |
| Directory reader passed to `writer.add_members(reader)` | All members stream into the target archive in one forward pass |
| Large directory conversion | No intermediate on-disk buffering of the full directory content |

### Requirement: Keep directory reader constraints as strict as archive readers

The directory reader SHALL enforce the same API-level constraints as real archive
readers even where the filesystem could permit more. Without
`MemberStreams.CONCURRENT`, a second overlapping member stream SHALL raise
`ConcurrentAccessError`. Without `MemberStreams.SEEKABLE`, member streams SHALL
report `seekable() is False`, `seek()` SHALL raise `io.UnsupportedOperation`,
and `tell()` remains available per `archive-reading`.

Code developed against a directory reader MUST behave the same when pointed at a
real archive; the directory backend therefore refuses everything a real archive
reader might refuse.

#### Scenario: directory uniformity matrix

| Case | Expected |
| --- | --- |
| One member stream is live, then another opens without `CONCURRENT` | `ConcurrentAccessError`, matching ZIP/TAR behavior |
| Member stream obtained without `SEEKABLE` | `seekable() is False`; `seek()` raises `io.UnsupportedOperation` despite real file backing |
| Same code later uses an archive reader | No dependency on directory-only leniency |
